---
title: 'MCP server with guarded record_decision'
type: 'feature'
created: '2026-09-27'
status: 'done'
baseline_commit: 'd737d0028a71d29c4d2f3156c40d09889782785f'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/specs/spec-expense-reviewer/mcp-tools.md'
  - '{project-root}/cases/expense/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 2's agent needs a way to read claims and record decisions. Nothing may let it write a decision the engine didn't make, or set the payout status.

**Approach:**
- A stdio FastMCP server, `cases/expense/mcp_server.py`, exposes three read tools: `get_claim` (with the engine's facts and proposed decision for each line), `get_employee` and `get_policy_limits`.
- It also exposes one guarded write, `record_decision(claim_id, line_id, decision, clause, explanation, agent_disagrees=False)`. It validates every write against `policy_engine.decide_claim` and sets `payout_status` in code.

## Boundaries & Constraints

**Always:**
- Every query is parameterized.
- An unknown ID raises a tool error, never an empty result.
- A refusal writes nothing.
- `payout_status` is set only inside `record_decision`:
  - `pending_approval`: approve and amount > 50000 cents
  - `payable`: any other approve
  - `not_payable`: flag or reject
- `record_decision` compares the decision and clause, including `clause=None` for bad input, to the engine's for that line.
- The tool docstrings tell the agent how to chain the calls: `get_claim` first, then one `record_decision` per line.

**Decisions (human, at planning):**
- A second `record_decision` for a line that already has any row, released or not, leaves the row unchanged and returns `already_decided`, so the first decision stands. A retry therefore skips lines that are already done. Only Re-review (Epic 3) clears rows.

**Never:**
- No tool sets `released`, clears decisions, changes `claims.state` or edits seed tables.
- No change to `mcp/triage_server.py`, `policy_engine.py`'s behavior or `load_seed.py`.
- No LLM.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Read claim | `get_claim("CL-2016")` | claim fields + per-line facts, proposed decision/clause; L-3062 flag 2.1 | N/A |
| Unknown IDs | `get_claim`/`get_employee` unknown, `get_policy_limits` unknown level/city | — | tool error naming the ID |
| Valid write | engine's decision/clause for a line in the claim | one row; explanation and agent_disagrees stored | N/A |
| Engine mismatch | wrong decision or wrong clause | nothing written | tool error stating engine's decision/clause |
| Line not in claim | `line_id` belongs to another claim, or doesn't exist | nothing written | tool error |
| Payout gate | approve at 50000 / 50001 cents; approve 12000; flag; reject | payable / pending_approval / payable / not_payable / not_payable | N/A |
| Already decided | line already has a row (released or not) | row unchanged; returns `already_decided` | not an error |
| Bad input line | engine gives flag, `clause=None` | recorded with clause NULL, `not_payable` | N/A |

</frozen-after-approval>

## Code Map

- `cases/expense/policy_engine.py`:
  - reuse `connect(db_path)`, `decide_claim(conn, claim_id)` (it raises `KeyError` for an unknown claim) and `Result(decision, clause, facts, reason)`
  - `facts` holds `amount_cents`, `limit_cents`, `day_total_cents`, `pct_over`, `age_days`, `duplicate_of`, `has_receipt` and `ita_code`
  - read-only for this story
- `cases/expense/load_seed.py` -- `build(db_path)` gives the tests a temporary database, and the `decisions` columns are in its `SCHEMA`. Read-only here.
- `mcp/triage_server.py` -- the style to copy: `FastMCP(name, log_level="WARNING")`, a `_query` helper, `ValueError` for unknown IDs, and `server.run()` under `__main__`. Don't modify it.
- `cases/expense/tests/test_policy_engine.py` -- its `item(...)` helper shows how to build synthetic rows. The gate tests need to insert their own line into a temporary database, for example a flight with a high limit.
- `mcp` 1.30: `@server.tool()` returns the plain function, so tests can call the tools directly. `await server.call_tool(name, args)` exercises the MCP layer end to end.

## Tasks & Acceptance

**Execution:**
- [x] `cases/expense/mcp_server.py`:
  - add the four tools
  - add a database-path override (the `EXPENSE_DB` env var), used only by the tests
  - return `amount`, `limit` and the day total as integer cents in `get_claim`
  - `record_decision` returns `{"status": "recorded" | "already_decided", "payout_status": ...}`
  - covers CAP-3 and CAP-4
- [x] `cases/expense/tests/test_mcp_server.py` -- one test per matrix row, plus one `call_tool` round trip through the MCP layer -- CAP-3 and CAP-4

**Acceptance Criteria:**
- Given a loaded `app.db`, when `uv run python cases/expense/mcp_server.py` starts, then it serves the four tools over stdio.
- Given any refused `record_decision`, when the `decisions` table is read afterwards, then it is unchanged.
- Given the tests, when `uv run pytest` runs, then all tests pass, story 1's 38 included.

## Implementation Notes

- **Files:**
  - `cases/expense/mcp_server.py`: the four tools, plus the `EXPENSE_DB` override.
  - `cases/expense/tests/test_mcp_server.py`: 24 tests. The whole suite now has 62.
- **`record_decision` check order:** the claim exists, then the line is in the claim, then the decision and clause match the engine, then the insert with `ON CONFLICT DO NOTHING`. So a mismatch is refused even on a line that already has a row. Nothing is written either way.
- **Field names:** `get_employee` returns `home_city`, and `get_policy_limits` returns `limits_cents`.
- **Review patches (pass 1):**
  - A `call_tool` test now sends `clause=None`.
  - A test checks that a mismatch on a line that already has a row is refused.
  - The `get_claim` docstring now covers a null limit and a two-city day.
  - 64 tests now pass.
- **Imports:** `mcp_server.py` imports `policy_engine` as a plain module, so Epic 2 must start it by file path, for example `uv run python cases/expense/mcp_server.py`.

## Spec Change Log

## Review Triage Log

Review pass 1. Reviewer codes: BH = Blind Hunter, EC = Edge Case Hunter, VG = Verification Gap.

| # | Finding | Verdict | Evidence / route |
|---|---|---|---|
| VG1 / BH6 | `clause=None` is never sent through the MCP layer | medium | Narrowing the annotation to `str` would break bad-input lines for the real agent, and every test would still pass → **patch** |
| VG2 / BH5 | No test sends a mismatched decision to a line that already has a row | medium | Moving the existing-row check first would turn the refusal into `already_decided`, and every test would still pass → **patch** |
| VG3 / BH3 | No test starts the server over stdio | maybe-false | It was checked by hand with a scratch stdio client. A subprocess test would settle it; Epic 2's agent is the real consumer. Would be medium → **defer** |
| BH1 | `already_decided` can hide a stale stored decision | false | The engine only changes if the data does, and the seed is read-only. A reload needs an empty `decisions` table or `--force`, and later claims never change earlier results |
| BH2 / EC4 | A blank or very long explanation is accepted | low | An LLM agent is unlikely to send a blank one, Epic 2's faithfulness check catches it, and the fix adds a guard → rejected |
| BH4 | Nothing shows the seed tables are never edited | false | The only write path in `mcp_server.py` is the `INSERT` into `decisions` |
| BH7 | The "level and city both exist, not together" branch is untested | low | Only the error message differs → rejected |
| BH8 | The test helpers insert and read by column position | low | A schema change would fail these tests loudly, not silently → rejected |
| BH9 | The gate is tested only on flights | low | An approve needs the day total ≤ the limit, which is under $500. The gate reads the line's own `amount_cents` whatever the category → rejected |
| BH10 | `get_claim` docstring: `limit_cents` can be null, and a two-city day uses the higher limit | low | Agent-facing text that's missing a case. The fix is a direct correction to the docstring → **patch** |
| BH11 | Each call re-decides the whole database | low | 159 rows take milliseconds → rejected |
| BH12 | `mcp-tools.md` was edited outside this diff | false | It was changed as a spec decision, logged in the parent spec's memlog, and isn't part of the story's code |
| BH13 | `ON CONFLICT` swallows unrelated rows | false | `line_id` is the global primary key, and the line-in-claim check runs first |
| EC1 / EC2 | An orphan employee drops a claim's lines silently | low | The seed is consistent (story 1 asserts 159 lines are decided) → rejected |
| EC3 | A row removed between the INSERT and the SELECT raises `TypeError` | low | Single user; Re-review arrives in Epic 3 → rejected |
| EC5 | Several approved lines under $500 can add up to more than $500 in one claim | false | The PRD and spec gate each item: "an approve over $500" is per line |

## Verification

**Commands:**
- `uv run pytest` -- expected: all pass.
- `git status --short` -- expected: changes only in `cases/expense/` (no `app.db`).
