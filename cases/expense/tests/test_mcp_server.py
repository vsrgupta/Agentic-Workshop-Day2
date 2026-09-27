import asyncio
import json
import sqlite3

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import load_seed
import mcp_server as ms


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    load_seed.build(path)
    monkeypatch.setenv("EXPENSE_DB", str(path))
    return path


def decisions(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT * FROM decisions ORDER BY line_id").fetchall()
    finally:
        conn.close()


def add_line(path, line_id, category, amount_cents, merchant=None):
    """Add a synthetic line to a new claim for an L1 Toronto employee (flight limit $600)."""
    conn = sqlite3.connect(path)
    try:
        with conn:
            employee = conn.execute("SELECT employee_id FROM employees WHERE level = 'L1' LIMIT 1").fetchone()[0]
            claim_id = f"CL-T{line_id}"
            conn.execute(
                "INSERT INTO claims (claim_id, employee_id, submitted_at, purpose) VALUES (?, ?, '2026-09-20', 'test')",
                (claim_id, employee),
            )
            conn.execute(
                "INSERT INTO line_items VALUES (?, ?, 100000, '2026-09-18', 'Toronto', ?, ?, ?, 'yes', 'test')",
                (line_id, claim_id, category, merchant or f"Merchant {line_id}", amount_cents),
            )
        return claim_id
    finally:
        conn.close()


# --- Reads -----------------------------------------------------------------


def test_get_claim_returns_facts_and_proposed_decisions(db):
    claim = ms.get_claim("CL-2016")
    assert set(claim) >= {"claim_id", "employee_id", "submitted_at", "purpose", "line_items"}
    lines = {line["line_id"]: line for line in claim["line_items"]}
    assert list(lines) == ["L-3060", "L-3061", "L-3062", "L-3063"]
    l3062 = lines["L-3062"]
    assert (l3062["decision"], l3062["clause"]) == ("flag", "2.1")
    assert l3062["amount_cents"] == 6962
    assert l3062["limit_cents"] == 12000
    assert l3062["day_total_cents"] == 13924
    for key in ("amount_cents", "limit_cents", "day_total_cents"):
        assert type(l3062[key]) is int
    assert l3062["pct_over"] == "16.03"
    assert l3062["age_days"] == 3 and l3062["duplicate_of"] is None
    assert l3062["has_receipt"] is True and l3062["ita_code"] is None
    assert lines["L-3063"]["duplicate_of"] == "L-3062"
    assert (lines["L-3063"]["decision"], lines["L-3063"]["clause"]) == ("reject", "5.1")


def test_get_employee_and_policy_limits(db):
    employee = ms.get_employee("E-101")
    assert employee["level"] == "L2" and employee["home_city"] == "Toronto"
    limits = ms.get_policy_limits("L1", "Toronto")["limits_cents"]
    assert limits["meals"] == 6000 and limits["flight"] == 60000


@pytest.mark.parametrize(
    "call, name",
    [
        (lambda: ms.get_claim("CL-9999"), "CL-9999"),
        (lambda: ms.get_employee("E-999"), "E-999"),
        (lambda: ms.get_policy_limits("L9", "Toronto"), "L9"),
        (lambda: ms.get_policy_limits("L1", "Atlantis"), "Atlantis"),
        (lambda: ms.record_decision("CL-9999", "L-3062", "flag", "2.1", "x"), "CL-9999"),
    ],
)
def test_unknown_ids_raise_naming_the_id(db, call, name):
    with pytest.raises(ValueError, match=name):
        call()
    assert decisions(db) == []


# --- record_decision -------------------------------------------------------


def test_valid_write_stores_one_row(db):
    result = ms.record_decision("CL-2016", "L-3062", "flag", "2.1", "Day total $139.24 over $120.00 limit, 2.1.", True)
    assert result == {"status": "recorded", "payout_status": "not_payable"}
    assert decisions(db) == [
        ("L-3062", "flag", "2.1", "Day total $139.24 over $120.00 limit, 2.1.", 1, "not_payable", None, None)
    ]


@pytest.mark.parametrize("decision, clause", [("approve", "2.1"), ("flag", "1.3"), ("flag", None), ("reject", "2.1")])
def test_engine_mismatch_is_refused_and_writes_nothing(db, decision, clause):
    with pytest.raises(ValueError, match=r"engine's decision for L-3062 is 'flag' under clause '2.1'"):
        ms.record_decision("CL-2016", "L-3062", decision, clause, "x")
    assert decisions(db) == []


@pytest.mark.parametrize("line_id", ["L-3001", "L-0000"])
def test_line_not_in_claim_is_refused(db, line_id):
    with pytest.raises(ValueError, match=f"{line_id} is not in claim CL-2016"):
        ms.record_decision("CL-2016", line_id, "approve", "2.1", "x")
    assert decisions(db) == []


@pytest.mark.parametrize(
    "category, amount_cents, expected",
    [
        ("flight", 50000, ("approve", "2.3", "payable")),
        ("flight", 50001, ("approve", "2.3", "pending_approval")),
        ("flight", 12000, ("approve", "2.3", "payable")),
        ("flight", 66000, ("flag", "2.3", "not_payable")),
        ("alcohol", 5000, ("reject", "3.1", "not_payable")),
    ],
)
def test_payout_gate(db, category, amount_cents, expected):
    claim_id = add_line(db, "L-9001", category, amount_cents)
    decision, clause, payout = expected
    assert ms.record_decision(claim_id, "L-9001", decision, clause, "x") == {"status": "recorded", "payout_status": payout}
    assert decisions(db)[0][5] == payout


def test_already_decided_leaves_row_unchanged(db):
    ms.record_decision("CL-2016", "L-3062", "flag", "2.1", "first")
    before = decisions(db)
    again = ms.record_decision("CL-2016", "L-3062", "flag", "2.1", "second", True)
    assert again == {"status": "already_decided", "payout_status": "not_payable"}
    assert decisions(db) == before


def test_already_decided_released_row_is_untouched(db):
    claim_id = add_line(db, "L-9001", "flight", 55000)
    ms.record_decision(claim_id, "L-9001", "approve", "2.3", "first")
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            "UPDATE decisions SET payout_status = 'released', released_by = 'finance', released_at = '2026-09-27' "
            "WHERE line_id = 'L-9001'"
        )
    conn.close()
    before = decisions(db)
    again = ms.record_decision(claim_id, "L-9001", "approve", "2.3", "second")
    assert again == {"status": "already_decided", "payout_status": "released"}
    assert decisions(db) == before


def test_bad_input_line_recorded_with_null_clause(db):
    claim_id = add_line(db, "L-9001", "gifts", 80000)
    line = ms.get_claim(claim_id)["line_items"][0]
    assert (line["decision"], line["clause"]) == ("flag", None)
    assert "unknown category" in line["reason"]
    result = ms.record_decision(claim_id, "L-9001", "flag", None, "Unknown category, flagged.")
    assert result == {"status": "recorded", "payout_status": "not_payable"}
    assert decisions(db)[0][:3] == ("L-9001", "flag", None)


def test_tools_never_touch_claim_state(db):
    ms.get_claim("CL-2016")
    ms.record_decision("CL-2016", "L-3062", "flag", "2.1", "x")
    conn = sqlite3.connect(db)
    states = {s for (s,) in conn.execute("SELECT state FROM claims")}
    conn.close()
    assert states == {"waiting"}


# --- MCP layer -------------------------------------------------------------


def test_call_tool_round_trip(db):
    async def run():
        tools = {t.name for t in await ms.server.list_tools()}
        claim = await ms.server.call_tool("get_claim", {"claim_id": "CL-2016"})
        recorded = await ms.server.call_tool(
            "record_decision",
            {"claim_id": "CL-2016", "line_id": "L-3062", "decision": "flag", "clause": "2.1", "explanation": "x"},
        )
        with pytest.raises(ToolError, match="CL-9999"):
            await ms.server.call_tool("get_claim", {"claim_id": "CL-9999"})
        return tools, claim, recorded

    tools, claim, recorded = asyncio.run(run())
    assert tools == {"get_claim", "get_employee", "get_policy_limits", "record_decision"}
    assert json.loads(claim[0].text)["line_items"][2]["line_id"] == "L-3062"
    assert json.loads(recorded[0].text) == {"status": "recorded", "payout_status": "not_payable"}
    assert len(decisions(db)) == 1


def test_mismatch_on_already_decided_line_is_refused(db):
    ms.record_decision("CL-2016", "L-3062", "flag", "2.1", "first")
    before = decisions(db)
    with pytest.raises(ValueError, match=r"engine's decision for L-3062 is 'flag' under clause '2.1'"):
        ms.record_decision("CL-2016", "L-3062", "approve", "2.1", "x")
    assert decisions(db) == before


def test_call_tool_null_clause_for_bad_input_line(db):
    claim_id = add_line(db, "L-9001", "gifts", 80000)

    async def run():
        return await ms.server.call_tool(
            "record_decision",
            {"claim_id": claim_id, "line_id": "L-9001", "decision": "flag", "clause": None, "explanation": "x"},
        )

    result = asyncio.run(run())
    assert json.loads(result[0].text) == {"status": "recorded", "payout_status": "not_payable"}
    assert decisions(db)[0][:3] == ("L-9001", "flag", None)
