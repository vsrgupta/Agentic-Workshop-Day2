import csv
import random
from collections import defaultdict
from pathlib import Path

import pytest

import load_seed
import policy_engine as pe
from policy_engine import Result

CASE_DIR = Path(__file__).resolve().parent.parent
LABELS = CASE_DIR / "eval" / "labelled.csv"


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    path = tmp_path_factory.mktemp("expense") / "app.db"
    load_seed.build(path)
    c = pe.connect(path)
    yield c
    c.close()


@pytest.fixture(scope="module")
def rows(conn):
    return pe.load_rows(conn)


@pytest.fixture(scope="module")
def results(rows):
    return pe.decide_all(rows)


def labels():
    with open(LABELS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- Seed and labels -------------------------------------------------------


def test_labelled_set_matches(results):
    rows = labels()
    assert len(rows) == 119
    mismatches = [
        (r["line_id"], r["expected_decision"], r["expected_clause"], results[r["line_id"]].decision, results[r["line_id"]].clause)
        for r in rows
        if (results[r["line_id"]].decision, results[r["line_id"]].clause) != (r["expected_decision"], r["expected_clause"])
    ]
    assert mismatches == []


def test_claim_totals_match(rows, results):
    by_claim = defaultdict(list)
    for label in labels():
        by_claim[label["claim_id"]].append(label)
    assert len(by_claim) == 30
    amounts = {r["line_id"]: r["amount_cents"] for r in rows}
    for claim_id, claim_labels in by_claim.items():
        claim_lines = [r["line_id"] for r in rows if r["claim_id"] == claim_id]
        assert sorted(claim_lines) == sorted(l["line_id"] for l in claim_labels)
        expected = sum(amounts[l["line_id"]] for l in claim_labels if l["expected_decision"] == "approve")
        assert pe.reimbursable_total_cents(results[i] for i in claim_lines) == expected, claim_id


def test_every_line_gets_exactly_one_decision(results):
    assert len(results) == 159
    assert {r.decision for r in results.values()} <= {"approve", "flag", "reject"}
    assert all(r.clause is not None for r in results.values())


def test_order_independence(rows, results):
    rng = random.Random(1234)
    for _ in range(5):
        shuffled = list(rows)
        rng.shuffle(shuffled)
        assert pe.decide_all(shuffled) == results


def test_decide_claim_matches_decide_all(conn, results):
    for (claim_id,) in conn.execute("SELECT claim_id FROM claims").fetchall():
        for line_id, result in pe.decide_claim(conn, claim_id).items():
            assert result == results[line_id]


def test_unknown_claim_raises_and_writes_nothing(conn):
    before = conn.total_changes
    with pytest.raises(KeyError):
        pe.decide_claim(conn, "CL-9999")
    assert conn.total_changes == before
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


# --- Reference cases -------------------------------------------------------


def test_l3062_duplicate_counts_toward_day_total(results):
    assert results["L-3062"] == Result(
        decision="flag", clause="2.1", reason=None,
        facts={"amount_cents": 6962, "limit_cents": 12000, "day_total_cents": 13924,
               "pct_over": "16.03", "age_days": 3, "duplicate_of": None,
               "has_receipt": True, "ita_code": None},
    )
    assert (results["L-3063"].decision, results["L-3063"].clause) == ("reject", "5.1")
    assert results["L-3063"].facts["duplicate_of"] == "L-3062"


def test_l3102_day_total_rejects(results):
    assert (results["L-3102"].decision, results["L-3102"].clause) == ("reject", "2.1")
    assert (results["L-3103"].decision, results["L-3103"].clause) == ("reject", "5.1")


def test_l3082_l3083_both_rejected_on_day_total(results):
    for line in ("L-3082", "L-3083"):
        assert (results[line].decision, results[line].clause) == ("reject", "2.1")
    assert results["L-3082"].facts["day_total_cents"] == results["L-3083"].facts["day_total_cents"]


def test_l3055_l3051_ita_code(results):
    assert (results["L-3055"].decision, results["L-3055"].clause) == ("approve", "4.1")
    assert results["L-3055"].facts["ita_code"] == "ITA-4471"
    assert (results["L-3051"].decision, results["L-3051"].clause) == ("reject", "4.1")
    assert results["L-3051"].facts["ita_code"] is None


def test_l3075_no_receipt_under_limit(results):
    assert (results["L-3075"].decision, results["L-3075"].clause) == ("flag", "1.3")
    assert results["L-3075"].facts["amount_cents"] == 3366


# --- Synthetic threshold edges ---------------------------------------------

_counter = iter(range(1, 10_000))


def item(**overrides):
    n = next(_counter)
    row = {
        "line_id": f"L-{n}", "claim_id": "CL-1", "seq": n, "employee_id": "E-1", "level": "L2",
        "submitted_at": "2026-08-20", "date": "2026-08-15", "city": "Toronto",
        "category": "hotel", "merchant": f"Merchant {n}", "amount_cents": 10000,
        "has_receipt": "yes", "description": "", "limit_cents": 20000,
    }
    row.update(overrides)
    return row


def decide(*rows):
    return pe.decide_all(rows)


def one(row):
    return decide(row)[row["line_id"]]


def outcome(result):
    return (result.decision, result.clause)


def test_hotel_at_limit_and_one_cent_over():
    assert outcome(one(item(amount_cents=20000))) == ("approve", "2.2")
    assert outcome(one(item(amount_cents=20001))) == ("flag", "2.2")


def test_twenty_percent_edge():
    assert outcome(one(item(amount_cents=24000))) == ("flag", "2.2")
    assert outcome(one(item(amount_cents=24001))) == ("reject", "2.2")
    # A limit where 1.2x is not a whole cent: 1.2 x 10001 = 12001.2
    assert outcome(one(item(amount_cents=12001, limit_cents=10001))) == ("flag", "2.2")
    assert outcome(one(item(amount_cents=12002, limit_cents=10001))) == ("reject", "2.2")


def test_twenty_percent_edge_on_day_total():
    a = item(category="meals", amount_cents=6000, limit_cents=10000)
    b = item(category="meals", amount_cents=6000, limit_cents=10000)
    assert {outcome(r) for r in decide(a, b).values()} == {("flag", "2.1")}
    c = item(category="meals", amount_cents=6000, limit_cents=10000)
    d = item(category="meals", amount_cents=6001, limit_cents=10000)
    assert {outcome(r) for r in decide(c, d).values()} == {("reject", "2.1")}


def test_receipt_edge():
    assert outcome(one(item(amount_cents=2500, has_receipt="no"))) == ("approve", "2.2")
    assert outcome(one(item(amount_cents=2501, has_receipt="no"))) == ("flag", "1.3")
    assert outcome(one(item(category="ground", amount_cents=2501, has_receipt="no"))) == ("flag", "1.3")


def test_over_limit_keeps_limit_clause_without_receipt():
    assert outcome(one(item(amount_cents=20001, has_receipt="no"))) == ("flag", "2.2")


def test_stale_edge():
    assert outcome(one(item(date="2026-06-21"))) == ("approve", "2.2")  # 60 days
    r = one(item(date="2026-06-20"))  # 61 days
    assert outcome(r) == ("reject", "1.2")
    assert r.facts["age_days"] == 61


def test_two_city_day_uses_higher_limit():
    toronto = item(category="meals", city="Toronto", amount_cents=6000, limit_cents=8000)
    montreal = item(category="meals", city="Montreal", amount_cents=5000, limit_cents=12000)
    res = decide(toronto, montreal)
    for r in res.values():
        assert outcome(r) == ("approve", "2.1")
        assert r.facts["limit_cents"] == 12000
        assert r.facts["day_total_cents"] == 11000


def test_later_claim_does_not_change_earlier_result():
    early = item(claim_id="CL-A", submitted_at="2026-08-20", category="meals", amount_cents=9000, limit_cents=10000)
    later = item(claim_id="CL-B", submitted_at="2026-08-25", category="meals", amount_cents=9000, limit_cents=10000)
    alone = one(early)
    both = decide(early, later)
    assert both[early["line_id"]] == alone
    assert outcome(alone) == ("approve", "2.1")
    # The later claim sees the earlier one: 180.00 against 100.00 is more than 20% over.
    assert outcome(both[later["line_id"]]) == ("reject", "2.1")


def test_duplicate_order_tie_breaks():
    common = dict(merchant="Cafe", amount_cents=3000, category="meals", limit_cents=10000)
    first = item(claim_id="CL-2", submitted_at="2026-08-20", **common)
    second = item(claim_id="CL-3", submitted_at="2026-08-20", **common)
    res = decide(second, first)
    assert outcome(res[first["line_id"]]) == ("approve", "2.1")
    assert outcome(res[second["line_id"]]) == ("reject", "5.1")
    assert res[second["line_id"]].facts["duplicate_of"] == first["line_id"]


def test_duplicate_merchant_is_trimmed_and_case_folded():
    a = item(merchant="The Keg", category="meals", amount_cents=3000, limit_cents=10000)
    b = item(merchant="  the keg ", category="meals", amount_cents=3000, limit_cents=10000)
    assert outcome(decide(a, b)[b["line_id"]]) == ("reject", "5.1")


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "snacks"},
        {"limit_cents": None},
        {"amount_cents": 0},
        {"amount_cents": -500},
    ],
    ids=["unknown-category", "missing-limit", "zero-amount", "negative-amount"],
)
def test_bad_input_is_flagged_with_reason(overrides):
    r = one(item(**overrides))
    assert r.decision == "flag"
    assert r.clause is None
    assert r.reason


def test_engine_uses_integer_cents_only(results):
    for r in results.values():
        for key in ("amount_cents", "limit_cents", "day_total_cents"):
            value = r.facts[key]
            assert value is None or type(value) is int


def test_stale_item_counts_toward_day_total():
    # For one date, a later submission is always older, so a stale meal can only sit in a
    # later claim than a current meal on that date. The current (earlier) claim never sees
    # the later one, and the stale meal's day total still includes the current meal.
    current = item(claim_id="CL-EARLY", submitted_at="2026-08-01", category="meals",
                   date="2026-06-10", amount_cents=5000, limit_cents=8000)
    stale = item(claim_id="CL-LATE", submitted_at="2026-08-15", category="meals",
                 date="2026-06-10", amount_cents=5000, limit_cents=8000)
    res = decide(stale, current)
    assert res[current["line_id"]].facts["age_days"] == 52
    assert res[stale["line_id"]].facts["age_days"] == 66
    assert outcome(res[stale["line_id"]]) == ("reject", "1.2")
    assert res[stale["line_id"]].facts["day_total_cents"] == 10000
    assert res[stale["line_id"]].facts["pct_over"] == "25.00"
    assert res[current["line_id"]].facts["day_total_cents"] == 5000
    assert outcome(res[current["line_id"]]) == ("approve", "2.1")


def test_within_claim_duplicate_order_follows_seq_not_line_id():
    common = dict(merchant="Cafe", amount_cents=3000, category="meals", limit_cents=10000)
    later = item(seq=2, line_id="L-1", **common)
    earlier = item(seq=1, line_id="L-2", **common)
    res = decide(later, earlier)
    assert outcome(res["L-2"]) == ("approve", "2.1")
    assert outcome(res["L-1"]) == ("reject", "5.1")
    assert res["L-1"].facts["duplicate_of"] == "L-2"


def test_stale_item_with_missing_limit_is_rejected_under_1_2():
    assert outcome(one(item(date="2026-06-01", limit_cents=None))) == ("reject", "1.2")


def test_duplicate_with_missing_limit_is_rejected_under_5_1():
    first = item(merchant="Inn", amount_cents=15000, limit_cents=None)
    second = item(merchant="Inn", amount_cents=15000, limit_cents=None)
    assert outcome(decide(first, second)[second["line_id"]]) == ("reject", "5.1")


def test_non_positive_amount_stays_out_of_day_total():
    valid = item(category="meals", amount_cents=4000, limit_cents=10000)
    negative = item(category="meals", amount_cents=-5000, limit_cents=10000)
    res = decide(valid, negative)
    assert res[valid["line_id"]].facts["day_total_cents"] == 4000
    assert outcome(res[valid["line_id"]]) == ("approve", "2.1")
