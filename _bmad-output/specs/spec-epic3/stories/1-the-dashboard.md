---
title: 'The dashboard'
type: 'feature'
created: '2026-10-05'
status: 'done'
baseline_commit: 'd301532ec69150c73a2d3370a8eb0dd90b5c0262'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/cases/expense/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The finance reviewer can only review claims from a terminal and can't see decisions in one place.

**Approach:**
- `cases/expense/dashboard.py` is a Streamlit app built over plain functions in `cases/expense/dashboard_data.py`.
- It shows:
  - the claims list with each claim's state
  - **Review** and **Retry** buttons, which start Epic 2's `review_claim` in a background worker thread
  - each claim's line items
  - a read-only flags view

## Boundaries & Constraints

**Always:**
- **Logic outside Streamlit.** These are plain functions that take `db_path`:
  - `list_claims` returns each claim's ID, employee, submission date, line count and state.
  - `claim_lines(claim_id)` returns each line's date, category, merchant, amount, decision, clause, explanation, payout status and `agent_disagrees`. An undecided line shows as undecided.
  - `list_flags` returns each `flag` decision with its line, clause or reason, and the engine's facts from `policy_engine.decide_claim`.
  - `start_review(claim_id)` starts a daemon thread that runs `asyncio.run(review_claim(...))`. It returns at once, catches and records any exception from the thread, and never raises into the page.
- **Buttons:**
  - **Review** shows on `waiting` claims and **Retry** on `incomplete` claims.
  - A `reviewing` claim shows no start button.
  - `review_claim`'s own guard still enforces one run per claim.
- **Refresh:** while any claim is `reviewing`, the page refreshes itself every few seconds, so a finished run appears without a manual reload.
- **Money:** amounts show as exact dollars and cents from integer cents, with no floats.
- **MLflow:** the dashboard sets up MLflow once, the same way `run_claim.py` does (`sqlite:///mlflow.db`, experiment `expense-reviewer`, `mlflow.langchain.autolog()`), so dashboard reviews are traced.
- **Dependency:** Streamlit is added with `uv add streamlit`.

**Never:**
- No Release, Re-review or Unstick. Those are story 2.
- The dashboard never decides and never writes a decision.
- No change to `review.py`, `mcp_server.py`, `policy_engine.py`, `load_seed.py` or the read-only files.
- Tests never call a real model, and never touch the repo's `app.db` or `mlflow.db`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Claims list | fresh seed DB | 40 claims, all `waiting`, line counts total 159 | N/A |
| Start review | `waiting` claim, fake model | `start_review` returns immediately; claim ends `complete`; lines show decisions | N/A |
| Retry | `incomplete` claim | Retry button shown; review fills missing lines | N/A |
| No double start | claim `reviewing` | no start button; a direct `start_review` call starts no second agent run | N/A |
| Worker failure | model raises in thread | claim ends `incomplete`; error recorded for display; page unaffected | caught in worker |
| Claim view | decided claim | each line: decision, clause, explanation, payout status, disagree marker when set | N/A |
| Undecided line | claim before review | line shown as undecided, no crash | N/A |
| Flags view | DB with a flag (e.g. L-3062 2.1) and a bad-input flag | each flag with clause or reason and engine facts | N/A |
| Money display | 6962 cents | `$69.62` | N/A |

</frozen-after-approval>

## Code Map

- `cases/expense/review.py` -- `review_claim(claim_id, db_path=None, model=None)` (async), `DB_PATH` and `make_model()`. It isn't modified here.
- `cases/expense/policy_engine.py` -- `connect(db_path)`, plus `decide_claim(conn, claim_id)` returning `{line_id: Result}`, where `Result` carries `reason` and `facts`. Use these for the flags view.
- `cases/expense/run_claim.py` -- the MLflow setup to copy (`TRACKING_URI`, `EXPERIMENT`, `load_dotenv`, `autolog`).
- `cases/expense/load_seed.py` -- `build(path)` makes the test databases. The columns are in `SCHEMA`.
- `cases/expense/tests/test_review.py` -- reuse the `FakeToolModel` scripting pattern so `start_review` runs in tests without a real model.
- `streamlit.testing.v1.AppTest` -- use it for one smoke test that renders `dashboard.py` against a temporary database. It's available once Streamlit is installed.

## Tasks & Acceptance

**Execution:**
- [x] `pyproject.toml` / `uv.lock` -- `uv add streamlit` -- the dashboard dependency
- [x] `cases/expense/dashboard_data.py` -- `list_claims`, `claim_lines`, `list_flags`, `fmt_cents` and `start_review`, with the worker's error capture -- CAP-1, CAP-2, CAP-4
- [x] `cases/expense/dashboard.py` -- the Streamlit layout: claims list with buttons, claim view, flags view, auto-refresh while reviewing, and MLflow setup -- CAP-1, CAP-2, CAP-4
- [x] `cases/expense/tests/test_dashboard.py` -- every matrix row against `dashboard_data`, plus one `AppTest` smoke render -- CAP-1, CAP-2, CAP-4

**Acceptance Criteria:**
- Given a loaded `app.db`, when `uv run streamlit run cases/expense/dashboard.py` starts, then the browser shows the 40 claims and a **Review** button on each `waiting` claim.
- Given the tests, when `uv run pytest` runs, then all tests pass, the only skips are `live` tests, and the 152 existing tests still pass.

## Implementation Notes

- **Files:**
  - `pyproject.toml` and `uv.lock`: Streamlit 1.65.0.
  - `cases/expense/dashboard_data.py`: the logic, with no SQL writes.
  - `cases/expense/dashboard.py`: the layout, in three tabs, with a 3-second auto-refresh fragment while any claim is reviewing.
  - `cases/expense/tests/test_dashboard.py`: 20 new tests. The whole suite now shows 172 passed and 1 skipped (the live test).
- **Helpers added beyond the spec:** `action_for`, `progress` and `setup_mlflow`. A dashboard review is wrapped in a `review_claim` AGENT span.
- **Storage:** worker errors are kept in memory, so a restart clears them. Claim text is never rendered as markdown.
- **Real data:** the real `app.db` was rendered read-only: 40 Review buttons, and still 0 decisions. The repo's `mlflow.db` was not written by this story.

- **Review patches (pass 1):**
  - Live worker threads are tracked with `running(db)`. While a thread is alive the page refreshes and hides the start button, which fixes the race between the click and the claim's state.
  - The worker's error is shown as plain text.
  - The failure banner shows only on `waiting` or `incomplete` claims.
  - Database paths are resolved before they're used as keys.
  - Nine tests added, covering refresh, the click, the flags and claim views with rows, the error banner and thread tracking.
  - The suite now shows 181 passed and 1 skipped (the live test).

## Spec Change Log

## Review Triage Log

Review pass 1. Reviewer codes: BH = Blind Hunter, EC = Edge Case Hunter, VG = Verification Gap.

| # | Finding | Verdict | Evidence / route |
|---|---|---|---|
| BH1 / BH2 / EC1 / EC2 / EC6 | After a Review click, the refresh can run before the worker sets `reviewing`, so auto-refresh never starts | medium | `review_claim` builds the model before `_start`, and `st.rerun()` comes right after the thread starts. A finished run or an early error (no key) never appears without a reload, and Review stays clickable → **patch** (track live threads; refresh and hide buttons while any is alive) |
| BH3 | `st.error` renders the worker error as Markdown | low | The error text can carry claim-derived content, which breaks the untrusted-data rule. A direct fix → **patch** |
| EC4 | A stale failure banner stays on a claim that later completes | low | A direct fix to the condition → **patch** |
| EC5 | The error store is keyed by an unresolved path | low | A direct `resolve()` → **patch** |
| VG1 | Auto-refresh isn't verified | medium | Two mutations still pass all 20 tests → **patch** |
| VG2 | A Review/Retry click isn't verified | medium | Replacing `start_review` with `pass` still passes → **patch** |
| VG3 | The flags table is never rendered with rows | medium | Renaming a key still passes, though it would crash on real data → **patch** |
| VG4 | The error banner on the page isn't verified | medium | Removing `st.error` still passes → **patch** |
| BH5 | The flags view shows the engine's current result, which may not match the recorded flag | false | `record_decision` refuses anything but the engine's decision, and the data is read-only, so they always match |
| BH4 | The Amount column sorts as text | low | Cosmetic → rejected |
| BH6 | The flags view is recomputed on every refresh | low | 40 claims; cheap → rejected |
| BH7 | No-op starts leave empty traces | low | Rare (a double click); no harm to the eval → rejected |
| BH8 | Only `FileNotFoundError` is handled at startup | low | Unlikely → rejected |
| BH9 / VG-o | The no-writes test only searches the source text | low | The behaviour is covered elsewhere → rejected |
| BH11 | Process-wide error memory is never pruned | low | Its in-flight part is folded into BH1's patch; the rest is minor → rejected |
| BH12 | The claim view leaves out claim-level details | low | Not in the spec → rejected |
| EC3 | A crash mid-run leaves the claim `reviewing`, with an endless refresh | low | Story 2's **Unstick** recovers it → rejected |

## Verification

**Commands:**
- `uv run pytest` -- expected: all tests pass, and the only skips are `live` tests.
- `git status --short` -- expected: changes only in `cases/expense/`, `pyproject.toml` and `uv.lock`, with no `app.db` or `mlflow.db`.
