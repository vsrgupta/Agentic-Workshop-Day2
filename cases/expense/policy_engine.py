"""Deterministic policy engine for expense line items.

Implements `_bmad-output/specs/spec-expense-reviewer/policy-engine-rules.md`.
The core, `decide_all(rows)`, is pure: it works on in-memory rows and never
reads recorded decisions, so results don't depend on review order. Money is
integer cents throughout; there are no floats and no rounding in any compare.

Each row is a mapping with these keys:
    line_id, claim_id, seq (row order in line_items.csv), employee_id, level,
    submitted_at, date, city, category, merchant, amount_cents, has_receipt
    ('yes'/'no' or bool), description, limit_cents (int, or None when no limit
    row exists for (level, item city, category)).
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Iterable, Mapping

DB_PATH = Path(__file__).resolve().parent / "app.db"

REJECT_CATEGORIES = {"alcohol": "3.1", "personal": "3.2", "fine": "3.3"}
IT_CATEGORIES = {"software", "equipment"}
LIMIT_CLAUSES = {"meals": "2.1", "hotel": "2.2", "flight": "2.3", "ground": "6.1"}
DAY_TOTAL_CATEGORIES = {"meals", "ground"}
KNOWN_CATEGORIES = set(REJECT_CATEGORIES) | IT_CATEGORIES | set(LIMIT_CLAUSES)

STALE_DAYS = 60
RECEIPT_THRESHOLD_CENTS = 2500
ITA_PATTERN = re.compile(r"ITA-\d+")


@dataclass(frozen=True)
class Result:
    decision: str  # 'approve', 'flag' or 'reject'
    clause: str | None  # a POLICY.md clause; None only for bad input
    facts: dict
    reason: str | None = None  # set only for bad input


def _receipt(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "yes"


def _claim_key(row: Mapping) -> tuple:
    return (row["submitted_at"], row["claim_id"])


def _row_key(row: Mapping) -> tuple:
    return (row["submitted_at"], row["claim_id"], row["seq"])


def _dup_key(row: Mapping) -> tuple:
    return (row["employee_id"], row["date"], row["merchant"].strip().casefold(), row["amount_cents"])


def _band(total_cents: int, limit_cents: int) -> str:
    """2.4: at or under the limit approves, up to 1.2x flags, more rejects. Exact integers."""
    if total_cents <= limit_cents:
        return "approve"
    if total_cents * 5 <= limit_cents * 6:
        return "flag"
    return "reject"


def _pct_over(total_cents: int, limit_cents: int) -> str:
    """Display-only percentage over the limit, as a string with two decimals."""
    if limit_cents <= 0 or total_cents <= limit_cents:
        return "0.00"
    pct = Decimal(total_cents - limit_cents) * 100 / Decimal(limit_cents)
    return str(pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _visible(row: Mapping, rows: list[Mapping]) -> list[Mapping]:
    """Rows of the same employee in this claim or in claims submitted earlier."""
    key = _claim_key(row)
    return [r for r in rows if r["employee_id"] == row["employee_id"] and _claim_key(r) <= key]


def _decide_one(row: Mapping, rows: list[Mapping]) -> Result:
    category = row["category"]
    amount = row["amount_cents"]
    has_receipt = _receipt(row["has_receipt"])
    ita = ITA_PATTERN.search(row.get("description") or "")
    age_days = (date.fromisoformat(row["submitted_at"]) - date.fromisoformat(row["date"])).days

    visible = [r for r in _visible(row, rows) if r["amount_cents"] > 0]

    duplicate_of = None
    if amount > 0:
        earlier = sorted(
            (r for r in visible if _dup_key(r) == _dup_key(row) and _row_key(r) < _row_key(row)),
            key=_row_key,
        )
        duplicate_of = earlier[0]["line_id"] if earlier else None

    limit_cents = row.get("limit_cents")
    day_total = None
    if category in DAY_TOTAL_CATEGORIES:
        same_day = [r for r in visible if r["category"] == category and r["date"] == row["date"]]
        day_total = sum(r["amount_cents"] for r in same_day)
        day_limits = [r["limit_cents"] for r in same_day if r.get("limit_cents") is not None]
        if limit_cents is not None and day_limits:
            limit_cents = max(day_limits)  # two-city day: the higher limit
    compared = day_total if day_total is not None else amount

    facts = {
        "amount_cents": amount,
        "limit_cents": limit_cents if category in LIMIT_CLAUSES else None,
        "day_total_cents": day_total,
        "pct_over": _pct_over(compared, limit_cents)
        if category in LIMIT_CLAUSES and limit_cents is not None
        else None,
        "age_days": age_days,
        "duplicate_of": duplicate_of,
        "has_receipt": has_receipt,
        "ita_code": ita.group(0) if ita else None,
    }

    def bad(reason: str) -> Result:
        return Result("flag", None, facts, reason)

    if category not in KNOWN_CATEGORIES:
        return bad(f"unknown category {category!r}")
    if amount <= 0:
        return bad(f"amount must be positive, got {amount} cents")

    # 1. Section 3: never reimbursable.
    if category in REJECT_CATEGORIES:
        return Result("reject", REJECT_CATEGORIES[category], facts)
    # 2. 5.1: duplicate of an earlier item.
    if duplicate_of is not None:
        return Result("reject", "5.1", facts)
    # 3. 1.2: more than 60 days before submission.
    if age_days > STALE_DAYS:
        return Result("reject", "1.2", facts)
    # 4. 4.1: software and equipment need an ITA- code.
    if category in IT_CATEGORIES:
        return Result("approve" if ita else "reject", "4.1", facts)
    # 5. Limits (sections 2 and 6).
    clause = LIMIT_CLAUSES[category]
    if limit_cents is None:
        return bad(f"no limit for level {row['level']!r}, city {row['city']!r}, category {category!r}")
    band = _band(compared, limit_cents)
    if band != "approve":
        return Result(band, clause, facts)
    # 6. 1.3: over $25 with no receipt, only when the limit step approved.
    if amount > RECEIPT_THRESHOLD_CENTS and not has_receipt:
        return Result("flag", "1.3", facts)
    # 7. Category default.
    return Result("approve", clause, facts)


def decide_all(rows: Iterable[Mapping]) -> dict[str, Result]:
    """Decide every line item. Returns line_id -> Result, independent of input order."""
    rows = list(rows)
    return {row["line_id"]: _decide_one(row, rows) for row in sorted(rows, key=_row_key)}


def reimbursable_total_cents(results: Iterable[Result]) -> int:
    """Sum of approved amounts, in exact cents."""
    return sum(r.facts["amount_cents"] for r in results if r.decision == "approve")


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise FileNotFoundError(
            f"{db_path} not found. Load the data first: uv run python cases/expense/load_seed.py"
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def load_rows(conn: sqlite3.Connection) -> list[dict]:
    """Every line item joined with its claim, employee level and item-city limit."""
    cur = conn.execute(
        """
        SELECT li.line_id, li.claim_id, li.seq, c.employee_id, e.level, c.submitted_at,
               li.date, li.city, li.category, li.merchant, li.amount_cents, li.has_receipt,
               li.description, lim.limit_cents
        FROM line_items li
        JOIN claims c ON c.claim_id = li.claim_id
        JOIN employees e ON e.employee_id = c.employee_id
        LEFT JOIN limits lim
               ON lim.level = e.level AND lim.city = li.city AND lim.category = li.category
        ORDER BY li.seq
        """
    )
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def decide_claim(conn: sqlite3.Connection, claim_id: str) -> dict[str, Result]:
    """Decide one claim's line items, in seed order. Raises KeyError for an unknown claim."""
    if conn.execute("SELECT 1 FROM claims WHERE claim_id = ?", (claim_id,)).fetchone() is None:
        raise KeyError(f"No claim with ID {claim_id}")
    rows = load_rows(conn)
    results = decide_all(rows)
    return {r["line_id"]: results[r["line_id"]] for r in rows if r["claim_id"] == claim_id}
