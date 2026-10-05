"""Plain, Streamlit-free functions behind the reviewer dashboard (`dashboard.py`).

Every query takes `db_path` (None means `review.DB_PATH`). Nothing here decides a line or
writes a decision: reviews run Epic 2's `review.review_claim` in a background thread, and
that path (the agent through `record_decision`) is the only writer of decisions.
"""

import asyncio
import sqlite3
import threading
import traceback
from pathlib import Path

import policy_engine
import review

TRACKING_URI = "sqlite:///mlflow.db"
EXPERIMENT = "expense-reviewer"

UNDECIDED = "undecided"


# --- Helpers ------------------------------------------------------------------------


def resolve_db(db_path=None) -> Path:
    return (Path(db_path) if db_path else Path(review.DB_PATH)).resolve()


def _connect(db_path) -> sqlite3.Connection:
    db = resolve_db(db_path)
    if not db.exists():
        raise FileNotFoundError(f"No database at {db}; run load_seed.py first.")
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def fmt_cents(cents: int) -> str:
    """Exact dollars and cents from integer cents, with no floats: 6962 -> '$69.62'."""
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rest:02d}"


def action_for(state: str) -> str | None:
    """The start button a claim in `state` shows: Review on waiting, Retry on incomplete, else none."""
    return {"waiting": "Review", "incomplete": "Retry"}.get(state)


# --- Queries ---------------------------------------------------------------------------


def list_claims(db_path=None) -> list[dict]:
    """Every claim with its ID, employee, submission date, line count and state."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT c.claim_id, c.employee_id, e.name AS employee_name, c.submitted_at, c.state, "
            "       (SELECT COUNT(*) FROM line_items li WHERE li.claim_id = c.claim_id) AS line_count "
            "FROM claims c LEFT JOIN employees e ON e.employee_id = c.employee_id "
            "ORDER BY c.claim_id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def progress(db_path=None) -> tuple:
    """A cheap fingerprint of review progress: every claim's state and the decision count."""
    conn = _connect(db_path)
    try:
        states = tuple(conn.execute("SELECT claim_id, state FROM claims ORDER BY claim_id").fetchall())
        decided = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        return tuple(tuple(r) for r in states), decided
    finally:
        conn.close()


def claim_lines(claim_id: str, db_path=None) -> list[dict]:
    """Each line item of one claim with its decision (or 'undecided') and payout status.

    Raises KeyError for an unknown claim.
    """
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM claims WHERE claim_id = ?", (claim_id,)).fetchone() is None:
            raise KeyError(f"No claim with ID {claim_id}")
        rows = conn.execute(
            "SELECT li.line_id, li.date, li.category, li.merchant, li.amount_cents, "
            "       d.decision, d.clause, d.explanation, d.payout_status, d.agent_disagrees "
            "FROM line_items li LEFT JOIN decisions d ON d.line_id = li.line_id "
            "WHERE li.claim_id = ? ORDER BY li.seq",
            (claim_id,),
        ).fetchall()
    finally:
        conn.close()
    lines = []
    for row in rows:
        line = dict(row)
        decided = line["decision"] is not None
        line["decided"] = decided
        line["amount"] = fmt_cents(line["amount_cents"])
        if not decided:
            line["decision"] = UNDECIDED
            line["payout_status"] = UNDECIDED
        line["agent_disagrees"] = bool(line["agent_disagrees"]) if decided else False
        lines.append(line)
    return lines


def list_flags(db_path=None) -> list[dict]:
    """Every recorded `flag` decision with its line, its clause or the engine's bad-input
    reason, and the engine's facts (from `policy_engine.decide_claim`)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT li.claim_id, li.line_id, li.date, li.category, li.merchant, li.amount_cents, "
            "       d.clause, d.explanation "
            "FROM decisions d JOIN line_items li ON li.line_id = d.line_id "
            "WHERE d.decision = 'flag' ORDER BY li.claim_id, li.seq"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return []

    engine: dict[str, dict] = {}
    conn = policy_engine.connect(resolve_db(db_path))
    try:
        for claim_id in sorted({r["claim_id"] for r in rows}):
            engine[claim_id] = policy_engine.decide_claim(conn, claim_id)
    finally:
        conn.close()

    flags = []
    for row in rows:
        flag = dict(row)
        result = engine[row["claim_id"]].get(row["line_id"])
        flag["amount"] = fmt_cents(row["amount_cents"])
        flag["reason"] = result.reason if result else None
        flag["facts"] = dict(result.facts) if result else {}
        flags.append(flag)
    return flags


# --- MLflow ------------------------------------------------------------------------------

_mlflow_lock = threading.Lock()
_mlflow_ready = False


def load_dotenv():
    from dotenv import load_dotenv as _load

    _load()


def setup_mlflow() -> None:
    """Once per process, as run_claim.py does: .env, tracking URI, experiment, langchain autolog."""
    global _mlflow_ready
    with _mlflow_lock:
        if _mlflow_ready:
            return
        import mlflow

        load_dotenv()
        mlflow.set_tracking_uri(TRACKING_URI)
        mlflow.set_experiment(EXPERIMENT)
        mlflow.langchain.autolog()
        _mlflow_ready = True


# --- Background reviews ------------------------------------------------------------------

_runs_lock = threading.Lock()
_results: dict[tuple[str, str], dict] = {}
_errors: dict[tuple[str, str], str] = {}
_threads: dict[tuple[str, str], threading.Thread] = {}


def _key(claim_id: str, db_path) -> tuple[str, str]:
    return (str(resolve_db(db_path)), claim_id)


def _run(claim_id: str, db_path, model) -> dict:
    coro = review.review_claim(claim_id, db_path=db_path, model=model)
    if not _mlflow_ready:
        return asyncio.run(coro)
    import mlflow

    with mlflow.start_span(name="review_claim", span_type="AGENT") as span:
        span.set_inputs({"claim_id": claim_id})
        result = asyncio.run(coro)
        span.set_outputs(result)
        return result


def _worker(claim_id: str, db_path, model) -> None:
    key = _key(claim_id, db_path)
    try:
        result = _run(claim_id, db_path, model)
    except BaseException as exc:  # never let anything escape the thread
        message = f"{type(exc).__name__}: {exc}"
        with _runs_lock:
            _errors[key] = message
            _results.pop(key, None)
        traceback.print_exc()
        return
    with _runs_lock:
        _results[key] = result


def start_review(claim_id: str, db_path=None, model=None) -> threading.Thread:
    """Start `review_claim` for one claim in a daemon thread and return at once.

    Never raises into the page. Any exception from the run is caught in the thread and
    recorded (see `last_error`). `review_claim`'s own guard enforces one run per claim,
    so a call on a `reviewing` claim starts no second agent run.
    """
    db = resolve_db(db_path)
    key = _key(claim_id, db)
    with _runs_lock:
        _errors.pop(key, None)
        _results.pop(key, None)
        thread = threading.Thread(
            target=_worker, args=(claim_id, db, model), name=f"review-{claim_id}", daemon=True
        )
        _threads[key] = thread
        thread.start()
    return thread


def running(db_path=None) -> set[str]:
    """Claim IDs with a live background review thread on this database. A just-started
    claim can still read `waiting` until its thread reaches `review_claim`'s start."""
    db = str(resolve_db(db_path))
    with _runs_lock:
        return {cid for (path, cid), t in _threads.items() if path == db and t.is_alive()}


def last_error(claim_id: str, db_path=None) -> str | None:
    """The error recorded by the last background review of this claim, if it failed."""
    with _runs_lock:
        return _errors.get(_key(claim_id, db_path))


def last_result(claim_id: str, db_path=None) -> dict | None:
    """What the last background review of this claim returned, if it finished without error."""
    with _runs_lock:
        return _results.get(_key(claim_id, db_path))


def review_errors(db_path=None) -> dict[str, str]:
    """{claim_id: error} for every failed background review on this database."""
    db = str(resolve_db(db_path))
    with _runs_lock:
        return {cid: msg for (path, cid), msg in _errors.items() if path == db}
