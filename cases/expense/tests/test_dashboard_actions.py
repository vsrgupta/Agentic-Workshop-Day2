"""Tests for the reviewer's actions (story 3.2): approval queue, Release, Re-review, Unstick.

Every test uses a temporary database and a scripted fake model. Nothing calls a real model or
touches the repo's app.db or mlflow.db. `load_dotenv` is stubbed so the repo's .env never
leaks an APPROVER_NAME into a test.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import dashboard_data as dd
import load_seed
import review
from test_dashboard import CASE_DIR, _gated_review, clean_workers, join  # noqa: F401 (autouse fixture)
from test_review import engine_lines, fake, script, set_state, state, unused

PENDING_CLAIM = "CL-2001"  # its flight L-3001 is an approve over $500
PENDING_LINE = "L-3001"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    load_seed.build(path)
    return path


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    monkeypatch.setattr(dd, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("APPROVER_NAME", raising=False)


@pytest.fixture
def ana(monkeypatch):
    monkeypatch.setenv("APPROVER_NAME", "Ana")


def rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT * FROM decisions ORDER BY line_id").fetchall()
    finally:
        conn.close()


def row(db_path, line_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute("SELECT * FROM decisions WHERE line_id = ?", (line_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def reviewed(db_path, claim_id=PENDING_CLAIM):
    join(dd.start_review(claim_id, db_path, model=fake(script(db_path, claim_id))))
    assert state(db_path, claim_id) == "complete"


def claim_line_ids(db_path, claim_id):
    return list(engine_lines(db_path, claim_id))


# --- Approval queue -----------------------------------------------------------------------


def test_queue_lists_the_approve_over_500(db):
    assert dd.approval_queue(db) == []
    reviewed(db)
    queue = dd.approval_queue(db)
    pending = [r for r in rows(db) if r[5] == "pending_approval"]
    assert [q["line_id"] for q in queue] == [r[0] for r in pending]
    item = {q["line_id"]: q for q in queue}[PENDING_LINE]
    assert item["claim_id"] == PENDING_CLAIM
    assert item["amount_cents"] > 50000 and item["amount"].startswith("$")
    assert item["merchant"] and item["clause"] and item["explanation"]
    assert set(item) >= {"claim_id", "line_id", "merchant", "amount", "clause", "explanation"}


# --- Release --------------------------------------------------------------------------------


def test_release_stamps_approver_and_utc_time(db, ana):
    reviewed(db)
    before = datetime.now(timezone.utc) - timedelta(seconds=2)
    result = dd.release(PENDING_LINE, db)
    r = row(db, PENDING_LINE)
    assert result == {"status": "released", "released_by": "Ana", "released_at": r["released_at"]}
    assert (r["payout_status"], r["released_by"]) == ("released", "Ana")
    stamped = datetime.fromisoformat(r["released_at"])
    assert stamped.utcoffset() == timedelta(0)
    assert before <= stamped <= datetime.now(timezone.utc) + timedelta(seconds=2)
    assert PENDING_LINE not in [q["line_id"] for q in dd.approval_queue(db)]
    line = {l["line_id"]: l for l in dd.claim_lines(PENDING_CLAIM, db)}[PENDING_LINE]
    assert (line["payout_status"], line["released_by"], line["released_at"]) == ("released", "Ana", r["released_at"])


@pytest.mark.parametrize("status", ["payable", "not_payable", "released"])
def test_release_non_pending_changes_nothing(db, ana, status):
    reviewed(db)
    reviewed(db, "CL-2016")  # has a flag and a reject (not_payable)
    line = next(r[0] for r in rows(db) if r[5] == status) if status != "released" else PENDING_LINE
    if status == "released":
        assert dd.release(line, db)["status"] == "released"
    before = rows(db)
    assert dd.release(line, db) == {"status": "not_pending"}
    assert rows(db) == before


def test_release_unknown_or_undecided_line(db, ana):
    assert dd.release("L-NOPE", db) == {"status": "not_pending"}
    assert dd.release(PENDING_LINE, db) == {"status": "not_pending"}  # not decided yet
    assert rows(db) == []


@pytest.mark.parametrize("value", [None, "", "   "])
def test_release_without_approver_writes_nothing(db, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("APPROVER_NAME", value)
    reviewed(db)
    before = rows(db)
    assert dd.approver_name() is None
    assert dd.release(PENDING_LINE, db) == {"status": "no_approver"}
    assert rows(db) == before


def test_approver_name_is_stripped(monkeypatch):
    monkeypatch.setenv("APPROVER_NAME", "  Ana  ")
    assert dd.approver_name() == "Ana"


# --- Re-review ------------------------------------------------------------------------------


def test_re_review_keeps_released_rows_byte_identical(db, ana):
    reviewed(db)
    assert dd.release(PENDING_LINE, db)["status"] == "released"
    released = row(db, PENDING_LINE)
    others = claim_line_ids(db, PENDING_CLAIM)
    others.remove(PENDING_LINE)
    assert others, "CL-2001 needs other lines for this test"

    # Mark the others so we can see they are re-recorded (not left as they were).
    conn = sqlite3.connect(db)
    with conn:
        conn.executemany("UPDATE decisions SET explanation = 'OLD' WHERE line_id = ?", [(l,) for l in others])
    conn.close()

    result = dd.re_review(PENDING_CLAIM, db, model=fake(script(db, PENDING_CLAIM, only=set(others))))
    assert result == {"status": "started"}
    thread = next(t for (p, c), t in dd._threads.items() if c == PENDING_CLAIM and p == str(db.resolve()))
    join(thread)

    assert dd.last_error(PENDING_CLAIM, db) is None
    assert state(db, PENDING_CLAIM) == "complete"
    assert row(db, PENDING_LINE) == released
    for line_id in others:
        assert row(db, line_id)["explanation"] != "OLD"


def test_re_review_clears_unreleased_and_resets_to_waiting(db, monkeypatch):
    reviewed(db)
    started = []
    monkeypatch.setattr(dd, "_start_locked", lambda c, d, m: started.append((c, d, m)))
    model = object()
    assert dd.re_review(PENDING_CLAIM, db, model=model) == {"status": "started"}
    assert started == [(PENDING_CLAIM, db.resolve(), model)]
    assert state(db, PENDING_CLAIM) == "waiting"
    assert rows(db) == []


def test_re_review_from_incomplete(db, monkeypatch):
    reviewed(db)
    set_state(db, PENDING_CLAIM, "incomplete")
    monkeypatch.setattr(dd, "_start_locked", lambda *a, **k: None)
    assert dd.re_review(PENDING_CLAIM, db) == {"status": "started"}
    assert state(db, PENDING_CLAIM) == "waiting"


def test_re_review_only_touches_its_own_claim(db, monkeypatch):
    reviewed(db)
    reviewed(db, "CL-2016")
    other = [r for r in rows(db) if r[0] not in claim_line_ids(db, PENDING_CLAIM)]
    monkeypatch.setattr(dd, "_start_locked", lambda *a, **k: None)
    dd.re_review(PENDING_CLAIM, db)
    assert rows(db) == other
    assert state(db, "CL-2016") == "complete"


@pytest.mark.parametrize("claim_state", ["waiting", "reviewing"])
def test_re_review_refused_by_state(db, claim_state):
    reviewed(db)
    set_state(db, PENDING_CLAIM, claim_state)
    before = rows(db)
    assert dd.re_review(PENDING_CLAIM, db, model=unused()) == {"status": "not_allowed"}
    assert rows(db) == before and state(db, PENDING_CLAIM) == claim_state
    assert PENDING_CLAIM not in dd.running(db)


def test_re_review_without_a_model_writes_nothing(db, monkeypatch):
    reviewed(db)
    before = rows(db)

    def no_key():
        raise RuntimeError("GEMINI_API_KEY is not set")

    monkeypatch.setattr(review, "make_model", no_key)
    result = dd.re_review(PENDING_CLAIM, db)
    assert result["status"] == "no_model" and "GEMINI_API_KEY is not set" in result["error"]
    assert rows(db) == before and state(db, PENDING_CLAIM) == "complete"
    assert PENDING_CLAIM not in dd.running(db)


def test_re_review_registers_its_worker_before_releasing_the_lock(db, monkeypatch):
    reviewed(db)
    seen = []
    real = dd._start_locked

    def spy(claim_id, db_path, model):
        seen.append(dd._runs_lock.locked())
        return real(claim_id, db_path, model)

    monkeypatch.setattr(dd, "_start_locked", spy)
    assert dd.re_review(PENDING_CLAIM, db, model=fake(script(db, PENDING_CLAIM))) == {"status": "started"}
    assert seen == [True]
    join(dd._threads[dd._key(PENDING_CLAIM, db)])
    assert state(db, PENDING_CLAIM) == "complete"


def test_start_review_never_overwrites_a_live_thread(db):
    thread, gate = _gated_review(db, PENDING_CLAIM)
    try:
        again = dd.start_review(PENDING_CLAIM, db, model=unused())
        assert again is thread
        assert dd._threads[dd._key(PENDING_CLAIM, db)] is thread
    finally:
        gate.set()
        join(thread)
    assert dd.last_error(PENDING_CLAIM, db) is None


def test_re_review_unknown_claim(db):
    assert dd.re_review("CL-9999", db, model=unused()) == {"status": "not_allowed"}


def test_re_review_refused_while_a_thread_is_live(db):
    reviewed(db)
    set_state(db, PENDING_CLAIM, "incomplete")
    before = rows(db)
    # a live thread on the claim (it will find nothing to record and stay incomplete -> held)
    thread, gate = _gated_review(db, PENDING_CLAIM)
    try:
        assert dd.re_review(PENDING_CLAIM, db, model=unused()) == {"status": "not_allowed"}
        assert rows(db) == before
    finally:
        gate.set()
        join(thread)


# --- Unstick --------------------------------------------------------------------------------


def test_unstick_moves_reviewing_to_incomplete(db):
    reviewed(db)
    set_state(db, PENDING_CLAIM, "reviewing")
    before = rows(db)
    assert dd.recovery_actions("reviewing") == ["Unstick"]
    assert dd.unstick(PENDING_CLAIM, db) == {"status": "unstuck"}
    assert state(db, PENDING_CLAIM) == "incomplete"
    assert rows(db) == before
    assert dd.action_for("incomplete") == "Retry"


@pytest.mark.parametrize("claim_state", ["waiting", "complete", "incomplete"])
def test_unstick_refused_when_not_reviewing(db, claim_state):
    set_state(db, PENDING_CLAIM, claim_state)
    assert dd.unstick(PENDING_CLAIM, db) == {"status": "not_allowed"}
    assert state(db, PENDING_CLAIM) == claim_state


def test_unstick_refused_while_a_thread_is_live(db):
    thread, gate = _gated_review(db, PENDING_CLAIM)
    try:
        # wait until the worker has moved the claim to reviewing
        for _ in range(600):
            if state(db, PENDING_CLAIM) == "reviewing":
                break
            thread.join(timeout=0.05)
        assert state(db, PENDING_CLAIM) == "reviewing"
        assert dd.recovery_actions("reviewing", live=True) == []
        assert dd.unstick(PENDING_CLAIM, db) == {"status": "not_allowed"}
        assert state(db, PENDING_CLAIM) == "reviewing"
    finally:
        gate.set()
        join(thread)
    assert state(db, PENDING_CLAIM) == "complete"


def test_recovery_actions_by_state():
    assert dd.recovery_actions("waiting") == []
    assert dd.recovery_actions("complete") == ["Re-review"]
    assert dd.recovery_actions("incomplete") == ["Re-review"]
    assert dd.recovery_actions("reviewing") == ["Unstick"]
    assert dd.recovery_actions("complete", live=True) == []


def test_actions_are_not_agent_or_mcp_tools():
    for name in ("mcp_server.py", "review.py"):
        source = (CASE_DIR / name).read_text(encoding="utf-8")
        for word in ("release", "re_review", "unstick", "approval_queue"):
            assert f"def {word}" not in source


# --- The page -------------------------------------------------------------------------------


@pytest.fixture
def app_env(db, tmp_path, monkeypatch):
    import mlflow

    monkeypatch.setattr(review, "DB_PATH", db)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dd, "TRACKING_URI", f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}")
    monkeypatch.setattr(dd, "_mlflow_ready", False)
    yield tmp_path
    mlflow.langchain.autolog(disable=True)
    mlflow.set_tracking_uri(None)


def _app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(CASE_DIR / "dashboard.py"), default_timeout=60)
    at.run()
    assert not at.exception
    return at


def test_page_release_click(app_env, db, ana):
    reviewed(db)
    at = _app()
    assert any(t.value == "Approver: Ana" for t in at.text)
    approvals = at.tabs[3]
    assert approvals.label == "Approvals"
    assert f"release-{PENDING_LINE}" in [b.key for b in approvals.button]
    assert any(t.value == PENDING_LINE for t in approvals.text)

    at.button(key=f"release-{PENDING_LINE}").click().run()
    assert not at.exception
    r = row(db, PENDING_LINE)
    assert (r["payout_status"], r["released_by"]) == ("released", "Ana") and r["released_at"]
    assert f"release-{PENDING_LINE}" not in [b.key for b in at.button]
    assert any(f"Released {PENDING_LINE} as Ana at {r['released_at']}" in t.value for t in at.text)

    at.selectbox(key="claim-view").set_value(PENDING_CLAIM).run()
    view = at.tabs[1].dataframe[0].value
    released = view[view["Line"] == PENDING_LINE].iloc[0]
    assert (released["Payout"], released["Released by"], released["Released at"]) == ("released", "Ana", r["released_at"])


def test_page_without_approver_warns_and_disables_release(app_env, db):
    reviewed(db)
    at = _app()
    assert any("APPROVER_NAME" in w.value and "restart" in w.value for w in at.warning)
    assert at.button(key=f"release-{PENDING_LINE}").disabled
    assert row(db, PENDING_LINE)["payout_status"] == "pending_approval"


def test_page_shows_recovery_buttons_by_state(app_env, db):
    set_state(db, "CL-2001", "complete")
    set_state(db, "CL-2002", "incomplete")
    set_state(db, "CL-2003", "reviewing")
    keys = [b.key for b in _app().button]
    assert "rereview-CL-2001" in keys and "rereview-CL-2002" in keys
    assert "unstick-CL-2003" in keys
    assert "rereview-CL-2003" not in keys and "unstick-CL-2001" not in keys
    assert "rereview-CL-2004" not in keys and "unstick-CL-2004" not in keys  # waiting


def test_page_unstick_needs_confirmation(app_env, db):
    set_state(db, "CL-2003", "reviewing")
    at = _app()
    at.button(key="unstick-CL-2003").click().run()
    assert not at.exception
    assert state(db, "CL-2003") == "reviewing"  # nothing yet: a warning and a confirm button
    assert any("live run may still be going" in w.value for w in at.warning)

    at.button(key="unstick-cancel-CL-2003").click().run()
    assert state(db, "CL-2003") == "reviewing"
    assert "unstick-confirm-CL-2003" not in [b.key for b in at.button]

    at.button(key="unstick-CL-2003").click().run()
    at.button(key="unstick-confirm-CL-2003").click().run()
    assert not at.exception
    assert state(db, "CL-2003") == "incomplete"
    assert any("CL-2003 is now incomplete" in t.value for t in at.text)
    assert "start-CL-2003" in [b.key for b in at.button]  # Retry is offered


def test_page_stale_unstick_confirmation_is_cleared(app_env, db):
    set_state(db, "CL-2003", "reviewing")
    at = _app()
    at.button(key="unstick-CL-2003").click().run()
    assert "unstick-confirm-CL-2003" in [b.key for b in at.button]
    set_state(db, "CL-2003", "complete")  # Unstick no longer applies
    at.run()
    assert "confirm-unstick" not in at.session_state
    set_state(db, "CL-2003", "reviewing")  # back to reviewing: the confirm must not be pre-armed
    at.run()
    assert "unstick-confirm-CL-2003" not in [b.key for b in at.button]
    assert state(db, "CL-2003") == "reviewing"


def test_page_hides_recovery_buttons_while_a_review_is_live(app_env, db):
    thread, gate = _gated_review(db, PENDING_CLAIM)
    try:
        for _ in range(600):
            if state(db, PENDING_CLAIM) == "reviewing":
                break
            thread.join(timeout=0.05)
        assert state(db, PENDING_CLAIM) == "reviewing"
        keys = [b.key for b in _app().button]
        assert f"unstick-{PENDING_CLAIM}" not in keys and f"rereview-{PENDING_CLAIM}" not in keys
    finally:
        gate.set()
        join(thread)


def test_page_re_review_click(app_env, db, monkeypatch):
    set_state(db, "CL-2001", "complete")
    calls = []
    monkeypatch.setattr(review, "make_model", lambda: "a-model")  # never a real model
    monkeypatch.setattr(dd, "_start_locked", lambda c, d, m: calls.append((c, d, m)))
    at = _app()
    at.button(key="rereview-CL-2001").click().run()
    assert not at.exception
    assert calls == [("CL-2001", db.resolve(), "a-model")]
    assert state(db, "CL-2001") == "waiting"
    assert any("Re-review of CL-2001 started" in t.value for t in at.text)


def test_page_re_review_without_a_model_says_so(app_env, db, monkeypatch):
    set_state(db, "CL-2001", "complete")

    def no_key():
        raise RuntimeError("GEMINI_API_KEY is not set")

    monkeypatch.setattr(review, "make_model", no_key)
    at = _app()
    at.button(key="rereview-CL-2001").click().run()
    assert not at.exception
    assert state(db, "CL-2001") == "complete"
    assert any("not started" in t.value and "GEMINI_API_KEY is not set" in t.value for t in at.text)
