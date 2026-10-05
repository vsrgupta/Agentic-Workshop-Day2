"""Tests for the reviewer dashboard (dashboard_data.py, dashboard.py).

Every test uses a temporary database and a scripted fake model (the FakeToolModel pattern
from test_review.py). Nothing calls a real model or touches the repo's app.db or mlflow.db.
"""

import sqlite3
import threading
from pathlib import Path

import pytest

import dashboard_data as dd
import load_seed
import review
from test_review import FakeToolModel, engine_lines, fake, script, set_state, state, unused

CASE_DIR = Path(__file__).resolve().parents[1]
CLAIM = "CL-2016"  # four lines: approve, approve, flag 2.1 (L-3062), reject 5.1


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    load_seed.build(path)
    return path


_open_gates: list[threading.Event] = []


@pytest.fixture(autouse=True)
def clean_workers():
    """After each test: open every gate, join every worker thread, clear the run registry."""
    yield
    for gate in _open_gates:
        gate.set()
    _open_gates.clear()
    with dd._runs_lock:
        threads = list(dd._threads.values())
    for thread in threads:
        thread.join(timeout=60)
    with dd._runs_lock:
        dd._threads.clear()
        dd._results.clear()
        dd._errors.clear()


def decision_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT * FROM decisions ORDER BY line_id").fetchall()
    finally:
        conn.close()


def join(thread):
    thread.join(timeout=120)
    assert not thread.is_alive()


# --- Matrix rows ---------------------------------------------------------------------------


def test_claims_list_on_fresh_seed(db):
    claims = dd.list_claims(db)
    assert len(claims) == 40
    assert {c["state"] for c in claims} == {"waiting"}
    assert sum(c["line_count"] for c in claims) == 159
    for c in claims:
        assert set(c) >= {"claim_id", "employee_id", "submitted_at", "line_count", "state"}
        assert dd.action_for(c["state"]) == "Review"


def test_start_review_returns_at_once_and_completes(db):
    gate = threading.Event()
    _open_gates.append(gate)
    messages = script(db, CLAIM)

    def gated():
        assert gate.wait(timeout=60)
        yield from messages

    thread = dd.start_review(CLAIM, db, model=FakeToolModel(messages=gated()))
    assert thread.daemon and thread.is_alive()  # returned while the run is held at the gate
    gate.set()
    join(thread)

    assert state(db, CLAIM) == "complete"
    assert dd.last_error(CLAIM, db) is None
    assert dd.last_result(CLAIM, db)["status"] == "complete"
    lines = dd.claim_lines(CLAIM, db)
    engine = engine_lines(db, CLAIM)
    assert all(l["decided"] for l in lines)
    assert [(l["decision"], l["clause"]) for l in lines] == [(r.decision, r.clause) for r in engine.values()]


def test_retry_fills_missing_lines(db):
    first = next(iter(engine_lines(db, CLAIM)))
    join(dd.start_review(CLAIM, db, model=fake(script(db, CLAIM, only={first}))))
    assert state(db, CLAIM) == "incomplete"
    assert dd.action_for("incomplete") == "Retry"
    assert [l["decided"] for l in dd.claim_lines(CLAIM, db)].count(True) == 1

    join(dd.start_review(CLAIM, db, model=fake(script(db, CLAIM))))
    assert state(db, CLAIM) == "complete"
    assert all(l["decided"] for l in dd.claim_lines(CLAIM, db))


def test_no_double_start_on_a_reviewing_claim(db):
    set_state(db, CLAIM, "reviewing")
    assert dd.action_for("reviewing") is None
    join(dd.start_review(CLAIM, db, model=unused()))  # unused() fails if the agent ever runs
    assert dd.last_result(CLAIM, db) == {"claim_id": CLAIM, "status": "already_reviewing"}
    assert dd.last_error(CLAIM, db) is None
    assert state(db, CLAIM) == "reviewing"
    assert decision_rows(db) == []


def test_worker_failure_is_caught_and_recorded(db):
    thread = dd.start_review(CLAIM, db, model=fake([RuntimeError("model exploded")]))  # does not raise
    join(thread)
    assert state(db, CLAIM) == "incomplete"
    assert "model exploded" in dd.last_error(CLAIM, db)
    assert "model exploded" in dd.review_errors(db)[CLAIM]
    assert dd.last_result(CLAIM, db) is None


def test_unknown_claim_in_worker_is_recorded_not_raised(db):
    join(dd.start_review("CL-9999", db, model=unused()))
    assert "CL-9999" in dd.last_error("CL-9999", db)


def test_a_new_start_clears_the_old_error(db):
    join(dd.start_review(CLAIM, db, model=fake([RuntimeError("boom")])))
    assert dd.last_error(CLAIM, db)
    join(dd.start_review(CLAIM, db, model=fake(script(db, CLAIM))))
    assert dd.last_error(CLAIM, db) is None
    assert state(db, CLAIM) == "complete"


def test_claim_view_of_a_decided_claim(db):
    join(dd.start_review(CLAIM, db, model=fake(script(db, CLAIM))))
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("UPDATE decisions SET agent_disagrees = 1 WHERE line_id = 'L-3062'")
    conn.close()

    lines = {l["line_id"]: l for l in dd.claim_lines(CLAIM, db)}
    for line in lines.values():
        assert set(line) >= {"date", "category", "merchant", "amount_cents", "decision", "clause",
                             "explanation", "payout_status", "agent_disagrees"}
        assert line["decision"] in ("approve", "flag", "reject")
        assert line["explanation"] and line["payout_status"] != dd.UNDECIDED
    flagged = lines["L-3062"]
    assert (flagged["decision"], flagged["clause"], flagged["payout_status"]) == ("flag", "2.1", "not_payable")
    assert flagged["agent_disagrees"] is True and flagged["amount"] == "$69.62"
    assert [lid for lid, l in lines.items() if l["agent_disagrees"]] == ["L-3062"]


def test_undecided_lines_show_as_undecided(db):
    lines = dd.claim_lines(CLAIM, db)
    assert len(lines) == 4
    for line in lines:
        assert not line["decided"]
        assert line["decision"] == dd.UNDECIDED and line["payout_status"] == dd.UNDECIDED
        assert line["clause"] is None and line["explanation"] is None
        assert line["agent_disagrees"] is False


def test_claim_lines_unknown_claim(db):
    with pytest.raises(KeyError):
        dd.claim_lines("CL-9999", db)


def _add_bad_input_flag(db_path):
    """A line with an unknown category (bad input, so a flag with no clause), recorded as the
    agent would record it (the engine's decision, clause None, not_payable)."""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO line_items VALUES ('L-BAD', 'CL-2001', 99999, '2026-08-01', 'Toronto', "
            "'teleport', 'Beam Co', 4200, 'yes', 'Unknown category')"
        )
        conn.execute(
            "INSERT INTO decisions (line_id, decision, clause, explanation, payout_status) "
            "VALUES ('L-BAD', 'flag', NULL, 'The $42.00 item is flagged.', 'not_payable')"
        )
    conn.close()


def test_flags_view(db):
    assert dd.list_flags(db) == []
    join(dd.start_review(CLAIM, db, model=fake(script(db, CLAIM))))
    _add_bad_input_flag(db)

    flags = {f["line_id"]: f for f in dd.list_flags(db)}
    assert set(flags) == {"L-3062", "L-BAD"}

    meal = flags["L-3062"]
    assert meal["clause"] == "2.1" and meal["reason"] is None
    assert meal["facts"]["amount_cents"] == 6962 and meal["facts"]["day_total_cents"] is not None
    assert meal["facts"] == engine_lines(db, CLAIM)["L-3062"].facts

    bad = flags["L-BAD"]
    assert bad["clause"] is None and bad["reason"]
    assert bad["facts"]["amount_cents"] == 4200


@pytest.mark.parametrize("cents, text", [(6962, "$69.62"), (0, "$0.00"), (5, "$0.05"), (50000, "$500.00"),
                                         (123456789, "$1,234,567.89"), (-150, "-$1.50")])
def test_money_display(cents, text):
    assert dd.fmt_cents(cents) == text


def test_dashboard_data_never_writes_decisions():
    """No SQL write in the page, no INSERT anywhere, and UPDATE/DELETE only inside the three
    human actions (story 3.2: release, re_review, unstick)."""
    import ast

    page = (CASE_DIR / "dashboard.py").read_text(encoding="utf-8").upper()
    for verb in ("INSERT", "UPDATE", "DELETE"):
        assert f"{verb} " not in page

    source = (CASE_DIR / "dashboard_data.py").read_text(encoding="utf-8")
    assert "INSERT " not in source.upper()
    allowed = {"release", "re_review", "unstick"}
    rest = source
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in allowed:
            rest = rest.replace(ast.get_source_segment(source, node), "")
    assert rest != source
    rest = rest.upper()  # module-level code, every other function, nested defs and classes
    assert "UPDATE " not in rest and "DELETE " not in rest


# --- Streamlit smoke test -------------------------------------------------------------------------


@pytest.fixture
def app_env(db, tmp_path, monkeypatch):
    """Point the dashboard at the temporary database and keep mlflow.db inside tmp_path."""
    import mlflow

    monkeypatch.setattr(review, "DB_PATH", db)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dd, "TRACKING_URI", f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}")
    monkeypatch.setattr(dd, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(dd, "_mlflow_ready", False)
    yield tmp_path
    mlflow.langchain.autolog(disable=True)
    mlflow.set_tracking_uri(None)


def test_dashboard_renders(app_env, db):
    from streamlit.testing.v1 import AppTest

    set_state(db, "CL-2001", "incomplete")
    set_state(db, "CL-2002", "reviewing")

    at = AppTest.from_file(str(CASE_DIR / "dashboard.py"), default_timeout=60)
    at.run()

    assert not at.exception
    labels = [b.label for b in at.button]
    assert labels.count("Review") == 38
    assert labels.count("Retry") == 1
    assert "start-CL-2002" not in [b.key for b in at.button]
    assert dd._mlflow_ready and (app_env / "mlflow.db").exists()
    assert len(at.tabs) == 4  # Claims, Claim view, Flags, Approvals (story 3.2)


def test_dashboard_reviews_are_traced(app_env, db):
    import mlflow

    dd.setup_mlflow()
    join(dd.start_review(CLAIM, db, model=fake(script(db, CLAIM))))
    assert state(db, CLAIM) == "complete"
    mlflow.flush_trace_async_logging()
    experiment = mlflow.get_experiment_by_name("expense-reviewer")
    traces = mlflow.search_traces(locations=[experiment.experiment_id], return_type="list")
    assert len(traces) == 1
    roots = [s for s in traces[0].data.spans if s.parent_id is None]
    assert [(s.name, s.span_type) for s in roots] == [("review_claim", "AGENT")]


# --- Live threads, refresh and the rendered page ----------------------------------------------


def _gated_review(db_path, claim_id=CLAIM):
    """Start a review held at a gate; returns (thread, gate). Teardown opens the gate."""
    gate = threading.Event()
    _open_gates.append(gate)
    messages = script(db_path, claim_id)

    def gated():
        assert gate.wait(timeout=60)
        yield from messages

    return dd.start_review(claim_id, db_path, model=FakeToolModel(messages=gated())), gate


def test_running_tracks_live_threads_by_resolved_path(db, monkeypatch):
    assert dd.running(db) == set()
    thread, gate = _gated_review(db)
    try:
        assert dd.running(db) == {CLAIM}
        monkeypatch.chdir(db.parent)
        assert dd.running(Path("app.db")) == {CLAIM}  # a relative path finds the same run
        assert dd.running(db.parent / "other.db") == set()
    finally:
        gate.set()
        join(thread)
    assert dd.running(db) == set()
    assert state(db, CLAIM) == "complete"


def test_errors_are_keyed_by_resolved_path(db, monkeypatch):
    join(dd.start_review(CLAIM, db, model=fake([RuntimeError("boom")])))
    monkeypatch.chdir(db.parent)
    assert "boom" in dd.last_error(CLAIM, "app.db")
    assert CLAIM in dd.review_errors(Path("app.db"))


def test_progress_changes_with_state_and_decisions(db):
    before = dd.progress(db)
    set_state(db, CLAIM, "reviewing")
    after_state = dd.progress(db)
    assert after_state != before
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("INSERT INTO decisions (line_id, decision, clause, explanation, payout_status) "
                     "VALUES ('L-3062', 'flag', '2.1', 'x.', 'not_payable')")
    conn.close()
    assert dd.progress(db) != after_state


def _app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(CASE_DIR / "dashboard.py"), default_timeout=60)
    at.run()
    assert not at.exception
    return at


def _refresh_caption(at):
    return any("refreshes every" in c.value for c in at.caption)


def test_refresh_caption_only_while_reviewing(app_env, db):
    assert not _refresh_caption(_app())
    set_state(db, CLAIM, "reviewing")
    assert _refresh_caption(_app())


def test_refresh_and_no_button_while_a_thread_is_live(app_env, db):
    thread, gate = _gated_review(db)
    try:
        at = _app()
        assert _refresh_caption(at)
        assert f"start-{CLAIM}" not in [b.key for b in at.button]
    finally:
        gate.set()
        join(thread)


def test_review_click_starts_that_claim_on_the_tmp_db(app_env, db, monkeypatch):
    calls = []
    monkeypatch.setattr(dd, "start_review", lambda claim_id, db_path=None, model=None: calls.append((claim_id, db_path)))
    at = _app()
    at.button(key="start-CL-2001").click().run()
    assert not at.exception
    assert calls == [("CL-2001", db.resolve())]


def test_flags_and_claim_view_render_decided_lines(app_env, db):
    join(dd.start_review(CLAIM, db, model=fake(script(db, CLAIM))))
    at = _app()

    flags_tab = at.tabs[2]
    assert len(flags_tab.dataframe) == 1
    flags = flags_tab.dataframe[0].value
    assert list(flags["Line"]) == ["L-3062"]
    assert list(flags["Clause or reason"]) == ["2.1"]
    assert "amount_cents=6962" in flags["Engine facts"].iloc[0]

    at.selectbox(key="claim-view").set_value(CLAIM).run()
    assert not at.exception
    view = at.tabs[1].dataframe[0].value
    engine = engine_lines(db, CLAIM)
    assert list(view["Line"]) == list(engine)
    assert list(view["Decision"]) == [r.decision for r in engine.values()]
    assert "$69.62" in list(view["Amount"])


def test_failed_review_shows_an_error_naming_the_claim(app_env, db):
    join(dd.start_review(CLAIM, db, model=fake([RuntimeError("model exploded")])))
    at = _app()
    assert any(CLAIM in e.value for e in at.error)
    assert any("model exploded" in t.value for t in at.text)


def test_failure_banner_hidden_once_the_claim_completes(app_env, db):
    join(dd.start_review(CLAIM, db, model=fake([RuntimeError("model exploded")])))
    set_state(db, CLAIM, "complete")
    assert not any(CLAIM in e.value for e in _app().error)
