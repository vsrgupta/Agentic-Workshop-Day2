---
title: 'Seed data and policy engine'
type: 'feature'
created: '2026-09-27'
status: 'done'
baseline_commit: 'c3a57ee6639809d6d53cf9fa342e6d4f19b0148d'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/specs/spec-expense-reviewer/policy-engine-rules.md'
  - '{project-root}/cases/expense/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The expense case has no database and no way to decide a line item. Every later piece (MCP tools, agent, dashboard) needs a deterministic, clause-cited decision that is identical on every run.

**Approach:**
- `load_seed.py` builds `cases/expense/app.db` from `seed/`.
- A pure-Python `policy_engine.py` implements `policy-engine-rules.md`. For each line item it returns the facts plus one decision and clause.
- `pytest` proves the engine against all 119 labels and every threshold edge.

## Boundaries & Constraints

**Always:**
- Keep money as integer cents from parse to compare, with no floats and no rounding.
- Follow the clause priority, bands, day-total scope and duplicate order exactly as `policy-engine-rules.md` sets them.
- The engine's core works on in-memory rows. Reading `app.db` is a thin wrapper around it, so the edge tests can use synthetic rows.
- Loading gives an identical database each time: 40 claims, 159 line items, 12 employees, 80 limits, every claim `state='waiting'`, and an empty `decisions` table with the columns in `mcp-tools.md`.

**Decisions (human, at planning):**
- `pyproject.toml` gets `"cases/expense/tests"` added to `testpaths`, so a plain `uv run pytest` runs the case tests. This is the only file changed outside the case folder.
- `load_seed.py` refuses to rebuild when `decisions` has rows, exits non-zero with a message, and leaves `app.db` untouched. `--force` rebuilds from scratch.
- The spec is kept as one story at about 1,850 tokens, above the 1,600 guideline, by choice.

**Never:**
- No LLM, MCP server, eval, MLflow, or claim state transitions.
- Don't modify `seed/`, `eval/`, `POLICY.md`, `BRIEF.md`, Saturday's `mcp/`, `run_agent.py` or the root `seed/`.
- Never commit `app.db`, and never use pip.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Labelled set | full seed | 119/119 decision+clause match; 30/30 claim totals | N/A |
| Order independence | seed rows shuffled | identical results per line_id | N/A |
| Limit edge | hotel at limit / limit+1¢ | approve 2.2 / flag 2.2 | N/A |
| 20% edge | amount = 1.2×limit / +1¢ | flag / reject | N/A |
| Receipt edge | $25.00 / $25.01, no receipt, under limit | approve / flag 1.3 | N/A |
| Stale edge | 60 / 61 days before submitted_at | normal decision / reject 1.2 | N/A |
| Two-city day | meals same day in 2 cities | day total vs higher limit | N/A |
| Later claim | same-day meal in later-submitted claim | earlier claim's result unchanged | N/A |
| Bad input | unknown category, missing limit, amount ≤ 0 | flag, `clause=None`, `reason` set | never approve |
| Reload guard | `decisions` has rows, no `--force` | refuses, and `app.db` is unchanged | exits non-zero with a message |
| Unknown claim | `decide_claim("CL-9999")` | — | raises `KeyError`, no writes |

</frozen-after-approval>

## Code Map

- `cases/expense/seed/*.csv` -- read-only input. Amounts are strings like `546.57` (parse them to cents), and `has_receipt` is `yes`/`no`.
- `cases/expense/eval/labelled.csv` -- read-only labels `line_id,claim_id,expected_decision,expected_clause`, covering the first 30 claims.
- `_bmad-output/specs/spec-expense-reviewer/mcp-tools.md` -- the column contract for `claims.state` and `decisions`.
- `mcp/triage_server.py` -- the reference style (sqlite3, `Path(__file__)`-relative database path, clear error when the database is missing). Don't modify it.
- `pyproject.toml` -- `pytest>=8` is already in the dev group, so there's nothing to add. Add `"cases/expense/tests"` to `testpaths`.
- `.gitignore` -- the `app.db` pattern already covers `cases/expense/app.db`.
- There is no root `load_seed.py` or `tests/`; Saturday's epic 1 isn't in this repo. Don't create either.

## Tasks & Acceptance

**Execution:**
- [x] `cases/expense/load_seed.py` -- create the tables from the seed CSVs (amounts as integer cents), add `claims.state` and an empty `decisions` table, and refuse when `decisions` has rows unless `--force` is passed -- CAP-1
- [x] `cases/expense/policy_engine.py` -- pure `decide_all(rows)` returning `line_id → Result(decision, clause, facts, reason)`, plus `decide_claim(conn, claim_id)` and `load_rows(conn)` -- CAP-2
- [x] `pyproject.toml` -- add `"cases/expense/tests"` to `[tool.pytest.ini_options] testpaths` -- so `uv run pytest` finds the case tests
- [x] `cases/expense/conftest.py` -- put `cases/expense` on `sys.path` for the tests -- keeps imports inside the case folder
- [x] `cases/expense/tests/test_load_seed.py` -- check the counts, the states, the empty `decisions` table, that loading twice gives the same result, and the reload guard with and without `--force` -- CAP-1
- [x] `cases/expense/tests/test_policy_engine.py` -- check the labelled set, the claim totals, order independence, the reference cases L-3062, L-3102, L-3082, L-3055/L-3051 and L-3075, and every matrix row -- CAP-2

**Acceptance Criteria:**
- Given a fresh checkout, when `uv run python cases/expense/load_seed.py` runs twice, then both runs leave identical table contents.
- Given the seed, when the engine decides every line, then the result for each line item is the same whichever claim it is looked up through.
- Given the tests, when the Verification command runs, then every test passes and no file outside `cases/expense/` changes except `pyproject.toml`'s `testpaths`.

## Implementation Notes

- **Files:**
  - `cases/expense/load_seed.py`: `to_cents`, `build`, `decisions_count` and `main`. It has hidden `--db` and `--seed` options, used by the tests.
  - `cases/expense/policy_engine.py`: `Result`, `decide_all`, `decide_claim`, `load_rows`, `connect` and `reimbursable_total_cents`.
  - `cases/expense/conftest.py`
  - Two test modules: 33 tests in all.
  - `pyproject.toml`: `testpaths` now includes `cases/expense/tests`.
- **Schema:**
  - `line_items.seq` stores each row's order in the CSV, which the duplicate rule uses to decide which item came first.
  - `claims.state` and `decisions.payout_status` have CHECK constraints listing the allowed values.
- **Choices made during implementation:**
  - The 20% band is checked as `total*5 <= limit*6`, which is exact in whole cents.
  - An unknown category, or an amount of zero or less, is flagged before any other rule. A missing limit is flagged only at the limit step, so a duplicate or stale item with no limit is still rejected under 5.1 or 1.2.
  - Amounts of zero or less are left out of day totals and duplicate checks.
  - `pct_over` is a two-decimal string, for display only.
- **Review patches (pass 1):**
  - The loader now checks every row before deleting `app.db`.
  - Four tests added, covering the stale-item day total, duplicate order by row within a claim, a duplicate or stale item with no limit, and amounts of zero or less left out of day totals.
  - 38 tests now pass.
- **Deferred to story 2:** the tests for the $500.00 and $500.01 payout gate. The payout status is set by `record_decision`, which story 2 builds.

## Spec Change Log

## Review Triage Log

Review pass 1. Reviewer codes: BH = Blind Hunter, EC = Edge Case Hunter, VG = Verification Gap.

| # | Finding | Verdict | Evidence / route |
|---|---|---|---|
| BH1 / EC4 | `build()` deletes `app.db` before `to_cents` has checked the new rows | low | Real: `to_cents` runs inside the inserts, after the unlink. The fix is a direct reorder (parse first) → **patch** |
| BH2 | `main()` shows raw tracebacks on bad seed, missing file or corrupt database | low | Real but unlikely (`seed/` is read-only and clean), and the fix adds guards → rejected |
| BH3 / EC1 / EC2 | `to_cents` mishandles NaN and Infinity | low | Not reachable with the read-only seed, and the fix adds a guard → rejected |
| BH4 / EC17 | No foreign keys; the INNER JOIN silently drops orphan lines | low | The seed is consistent, and the test asserts 159 decided lines → rejected |
| BH5 / EC8 | `has_receipt` accepts any text | low | The seed only has yes/no → rejected |
| BH6 / EC9 | A malformed date aborts the whole batch | low | The seed dates are valid ISO → rejected |
| BH7 | "Lower claim ID" is compared as a string | low | The seed IDs are fixed-width (CL-2001 to CL-2040) → rejected |
| BH8 | Several rules have no dedicated test | medium | Same gaps as VG1–VG4, triaged there. The extra items (bool receipt, two-city day with a missing limit, `--seed`) are low → rejected |
| BH9 | `decide_claim` is O(n²); two tests are weak; `Result` holds a mutable dict | low | n=159 runs in milliseconds. Re-sorting input is the order-independence design. Nothing hashes `Result` → rejected |
| EC3 | A short CSV row gives `None.strip()` | low | Not reachable with the seed → rejected |
| EC5 | Windows `PermissionError` when `app.db` is open elsewhere | low | Nothing is lost: the unlink fails before any change. The fix adds a guard → rejected |
| EC6 | A corrupt `app.db` crashes the reload guard | low | Unlikely, and the fix adds a guard → rejected |
| EC7 | A decision can be written between the count check and the build | low | Single user, and nothing writes decisions in Epic 1 → rejected |
| EC10 | An item dated after submission gets a negative age and can be approved | low | The seed's minimum age is 2 days. The rule isn't in the intent's bad-input list → rejected |
| EC11 | `merchant` of None crashes the batch | low | The seed has a merchant on every line → rejected |
| EC12 | A later valid item is rejected under 5.1 as a duplicate of an invalid row | false | Same date, merchant and amount is a duplicate under 5.1 whatever the earlier row's category |
| EC13 | Items in different categories are treated as duplicates | false | 5.1's key is date, merchant and amount, with no category; the code matches the policy |
| EC14 | A limit of zero or less rejects instead of flagging | low | The seed limits are all positive → rejected |
| EC15 | The ITA regex is case-sensitive and matches when embedded in other text | low | This follows the policy's literal "ITA- followed by digits", and the seed is fine → rejected |
| EC16 | A duplicate `line_id` in the input overwrites a result | false | `line_items.line_id` is the primary key, so the database path can't produce one |
| EC18 | City or category case differs between tables | low | The seed is consistent → rejected |
| EC19 | A claim with no lines returns `{}` | low | No such claim in the seed. Epic 2 handles completeness → rejected |
| EC20 | A test helper leaks a connection if its insert fails | low | Test-only and unlikely → rejected |
| EC21 | `testpaths` lists a `tests` folder that doesn't exist | false | Pytest ignores it, and it keeps Saturday's path |
| VG1 | No test shows stale items count toward the day total | medium | A code change leaving stale items out still passed all 33 tests → **patch** |
| VG2 | No test shows a same-claim duplicate follows `seq` rather than `line_id` | medium | Changing the tie-break to `line_id` still passed all 33 tests → **patch** |
| VG3 | No test shows a duplicate or stale item with no limit is still rejected under 5.1 or 1.2 | medium | Moving the missing-limit flag to the top still passed all 33 tests → **patch** |
| VG4 | No test shows amounts of zero or less stay out of a sibling's day total | medium | Removing the `> 0` filter still passed all 33 tests → **patch** |
| VG5 | A future root `load_seed.py` or `tests/test_load_seed.py` would clash with the case modules | maybe-false | Neither exists yet. Would be medium if they do → **defer** |
| VG1 (patch note) | The requested scenario can't happen | — | Age is counted from the item's own claim, so on a given date a stale item always sits in a later claim, and backward-only day totals never include it. The test covers the reachable case instead: the stale item's own day total includes the earlier item |
| VG6 | No test covers `decisions_count` on a database with no `decisions` table | low | Minor and unlikely path → rejected |

## Design Notes

**Choices made at planning time:**
- **Duplicate key:** the employee, date, merchant (trimmed and case-folded) and amount in cents. The seed has no near-duplicate merchant names, so this matches the labels either way.
- **Bad input:** gets `clause=None` plus a `reason`, because no `POLICY.md` clause covers it.
- **Engine input:** reads `app.db`, not the CSVs, so the MCP server in story 2 shares one source of truth.

**Result for L-3062:** an L4 employee in Toronto, with a meals limit of $120. The day total includes its duplicate, L-3063, making it 16.03% over the limit.
```python
Result(decision="flag", clause="2.1", reason=None,
       facts={"amount_cents": 6962, "limit_cents": 12000, "day_total_cents": 13924,
              "pct_over": "16.03", "age_days": 3, "duplicate_of": None,
              "has_receipt": True, "ita_code": None})
```

## Verification

**Commands:**
- `uv run python cases/expense/load_seed.py` -- expected: prints the row counts 40/159/12/80, and exits 0.
- `uv run pytest` -- expected: all tests pass.
- `git status --short` -- expected: changes only in `cases/expense/` (no `app.db`) and `pyproject.toml`.
