import sqlite3
import subprocess
import sys

import pytest

import load_seed

TABLES = ("claims", "line_items", "employees", "limits", "decisions")
DECISION_COLUMNS = [
    "line_id", "decision", "clause", "explanation", "agent_disagrees",
    "payout_status", "released_by", "released_at",
]


def dump(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in TABLES}
    finally:
        conn.close()


def add_decision(db_path):
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "INSERT INTO decisions (line_id, decision, clause, explanation, agent_disagrees, payout_status) "
            "VALUES ('L-3001', 'approve', '2.3', 'x', 0, 'pending_approval')"
        )
    conn.close()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    assert load_seed.main(["--db", str(path)]) == 0
    return path


def test_counts_states_and_empty_decisions(db):
    conn = sqlite3.connect(db)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    assert counts == {"claims": 40, "line_items": 159, "employees": 12, "limits": 80, "decisions": 0}
    assert conn.execute("SELECT DISTINCT state FROM claims").fetchall() == [("waiting",)]
    columns = [r[1] for r in conn.execute("PRAGMA table_info(decisions)")]
    assert columns == DECISION_COLUMNS
    conn.close()


def test_amounts_are_integer_cents(db):
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT amount_cents FROM line_items WHERE line_id = 'L-3001'").fetchone() == (54657,)
    assert conn.execute("SELECT amount_cents FROM line_items WHERE line_id = 'L-3051'").fetchone() == (14400,)
    types = {r[0] for r in conn.execute("SELECT typeof(amount_cents) FROM line_items")}
    types |= {r[0] for r in conn.execute("SELECT typeof(limit_cents) FROM limits")}
    assert types == {"integer"}
    conn.close()


def test_to_cents_is_exact():
    assert load_seed.to_cents("546.57") == 54657
    assert load_seed.to_cents("0.1") == 10
    assert load_seed.to_cents("60") == 6000
    with pytest.raises(ValueError):
        load_seed.to_cents("1.005")


def test_loading_twice_is_identical(db):
    first = dump(db)
    assert load_seed.main(["--db", str(db)]) == 0
    assert dump(db) == first


def test_reload_guard_refuses_and_leaves_db_untouched(db, capsys):
    add_decision(db)
    before = db.read_bytes()
    assert load_seed.main(["--db", str(db)]) != 0
    assert "Refusing" in capsys.readouterr().err
    assert db.read_bytes() == before


def test_reload_guard_force_rebuilds(db):
    fresh = dump(db)
    add_decision(db)
    assert load_seed.main(["--db", str(db), "--force"]) == 0
    assert dump(db) == fresh


def test_cli_exit_code_when_refusing(db):
    add_decision(db)
    proc = subprocess.run(
        [sys.executable, load_seed.__file__, "--db", str(db)], capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert "--force" in proc.stderr
