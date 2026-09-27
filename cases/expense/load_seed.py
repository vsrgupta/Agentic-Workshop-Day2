"""Build cases/expense/app.db from cases/expense/seed/.

Money is stored as integer cents. Every claim starts in state 'waiting' and the
decisions table starts empty. Loading twice gives identical table contents.

Refuses to rebuild when `decisions` already has rows, unless --force is passed.
"""

import argparse
import csv
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parent
DB_PATH = CASE_DIR / "app.db"
SEED_DIR = CASE_DIR / "seed"

SCHEMA = """
CREATE TABLE employees (
    employee_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    level       TEXT NOT NULL,
    city        TEXT NOT NULL,
    department  TEXT NOT NULL
);
CREATE TABLE limits (
    level       TEXT NOT NULL,
    city        TEXT NOT NULL,
    category    TEXT NOT NULL,
    limit_cents INTEGER NOT NULL,
    PRIMARY KEY (level, city, category)
);
CREATE TABLE claims (
    claim_id     TEXT PRIMARY KEY,
    employee_id  TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    purpose      TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'waiting'
                 CHECK (state IN ('waiting', 'reviewing', 'complete', 'incomplete'))
);
CREATE TABLE line_items (
    line_id      TEXT PRIMARY KEY,
    claim_id     TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    date         TEXT NOT NULL,
    city         TEXT NOT NULL,
    category     TEXT NOT NULL,
    merchant     TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    has_receipt  TEXT NOT NULL,
    description  TEXT NOT NULL
);
CREATE TABLE decisions (
    line_id         TEXT PRIMARY KEY,
    decision        TEXT NOT NULL CHECK (decision IN ('approve', 'flag', 'reject')),
    clause          TEXT,
    explanation     TEXT,
    agent_disagrees INTEGER NOT NULL DEFAULT 0,
    payout_status   TEXT NOT NULL
                    CHECK (payout_status IN ('pending_approval', 'payable', 'not_payable', 'released')),
    released_by     TEXT,
    released_at     TEXT
);
"""


def to_cents(text: str) -> int:
    """Parse a dollar string such as '546.57' or '144.0' into exact integer cents."""
    try:
        cents = Decimal(text.strip()) * 100
    except InvalidOperation as exc:
        raise ValueError(f"Not an amount: {text!r}") from exc
    if cents != cents.to_integral_value():
        raise ValueError(f"Amount has fractions of a cent: {text!r}")
    return int(cents)


def _read(seed_dir: Path, name: str) -> list[dict]:
    with open(seed_dir / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def decisions_count(db_path: Path) -> int:
    """Rows in `decisions`, or 0 when the database or table doesn't exist."""
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'decisions'"
        ).fetchone()
        return conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] if exists else 0
    finally:
        conn.close()


def build(db_path: Path = DB_PATH, seed_dir: Path = SEED_DIR) -> dict[str, int]:
    """Rebuild the database from scratch and return the row counts."""
    employees = _read(seed_dir, "employees.csv")
    limits = _read(seed_dir, "limits.csv")
    claims = _read(seed_dir, "claims.csv")
    items = _read(seed_dir, "line_items.csv")

    # Parse everything (including every to_cents) before touching the old database.
    employee_rows = [(e["employee_id"], e["name"], e["level"], e["city"], e["department"]) for e in employees]
    limit_rows = [(r["level"], r["city"], r["category"], to_cents(r["limit_cad"])) for r in limits]
    claim_rows = [(c["claim_id"], c["employee_id"], c["submitted_at"], c["purpose"]) for c in claims]
    item_rows = [
        (
            i["line_id"], i["claim_id"], seq, i["date"], i["city"], i["category"],
            i["merchant"], to_cents(i["amount"]), i["has_receipt"], i["description"],
        )
        for seq, i in enumerate(items, start=1)
    ]

    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA)
            conn.executemany("INSERT INTO employees VALUES (?, ?, ?, ?, ?)", employee_rows)
            conn.executemany("INSERT INTO limits VALUES (?, ?, ?, ?)", limit_rows)
            conn.executemany(
                "INSERT INTO claims (claim_id, employee_id, submitted_at, purpose, state) VALUES (?, ?, ?, ?, 'waiting')",
                claim_rows,
            )
            conn.executemany("INSERT INTO line_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", item_rows)
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("claims", "line_items", "employees", "limits", "decisions")
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the expense seed into app.db.")
    parser.add_argument("--force", action="store_true", help="rebuild even when decisions exist")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--seed", type=Path, default=SEED_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    existing = decisions_count(args.db)
    if existing and not args.force:
        print(
            f"Refusing to rebuild {args.db}: the decisions table has {existing} row(s). "
            "Pass --force to rebuild from scratch.",
            file=sys.stderr,
        )
        return 1

    counts = build(args.db, args.seed)
    print(
        f"Loaded {args.db}: {counts['claims']} claims, {counts['line_items']} line items, "
        f"{counts['employees']} employees, {counts['limits']} limits, {counts['decisions']} decisions."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
