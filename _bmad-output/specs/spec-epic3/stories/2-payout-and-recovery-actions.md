---
title: 'Payout and recovery actions'
type: 'feature'
created: '2026-10-05'
status: 'done'
baseline_commit: 'bc27ccf5e2bffc777a040743892e09a35420c128'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/cases/expense/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Approved items over $500 can't be paid out, a wrong unreleased decision can't be redone, and a claim stuck in `reviewing` can't be recovered.

**Approach:**
- Add three human actions to `cases/expense/dashboard_data.py` as plain functions:
  - `release(line_id, db_path=None)`
  - `re_review(claim_id, db_path=None, model=None)`
  - `unstick(claim_id, db_path=None)`
- Add `approval_queue(db_path=None)`, which lists the items waiting.
- Wire them into `dashboard.py`: an Approvals tab, plus Re-review and Unstick buttons on the claims list.

## Boundaries & Constraints

**Always:**
- **`release`:**
  - It is a single conditional `UPDATE` that applies only where `payout_status='pending_approval'`. It sets `payout_status='released'`, `released_by` to the `APPROVER_NAME` from the environment (loaded from `.env`) and `released_at` to the current UTC time in ISO-8601.
  - It returns `{"status": "released"|"not_pending"|"no_approver"}`.
  - When `APPROVER_NAME` is unset or blank, it writes nothing.
- **`re_review`:**
  - It works only on a `complete` or `incomplete` claim that has no live worker thread.
  - In one transaction, it deletes that claim's `decisions` rows whose `payout_status` isn't `released` and moves the claim from `complete` or `incomplete` to `waiting`. Then it calls `start_review`.
  - It returns `{"status": "started"|"not_allowed"}`.
  - Released rows are never deleted or changed.
- **`unstick`:**
  - It is a single conditional `UPDATE` from `reviewing` to `incomplete`, refused while this process has a live worker thread for the claim.
  - It changes no decisions.
  - It returns `{"status": "unstuck"|"not_allowed"}`.
- **`approval_queue`:** lists each `pending_approval` line with its claim ID, line ID, merchant, amount, clause and explanation.
- **Buttons:**
  - **Re-review** appears on `complete` and `incomplete` claims.
  - **Unstick** appears only on `reviewing` claims with no live thread. It sits behind a confirmation with a warning that a live run may still be going.
  - The dashboard shows the configured approver name, or a clear warning when it's missing.
- **Display:** claim text, and every message, is shown as plain text.

**Never:**
- None of these actions becomes an agent or MCP tool.
- No money moves.
- No change to `review.py`, `mcp_server.py`, `policy_engine.py`, `load_seed.py` or the read-only files.
- Tests never call a real model, and never touch the repo's `app.db` or `mlflow.db`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Release | pending row, `APPROVER_NAME=Ana` | `released`, `released_by=Ana`, UTC `released_at`; leaves queue | N/A |
| Release non-pending | payable / not_payable / released row | row unchanged | `not_pending` |
| No approver | `APPROVER_NAME` unset or blank | nothing written | `no_approver`; page shows warning |
| Queue | after reviewing a claim with an approve > $500 | that line listed with amount and explanation | N/A |
| Re-review | complete claim with one released and other rows | released row byte-identical; others re-recorded; claim ends `complete` | N/A |
| Re-review refused | `waiting` / `reviewing` claim, or live thread | nothing changes | `not_allowed` |
| Unstick | `reviewing` claim, no live thread | becomes `incomplete`; decisions unchanged; Retry offered | N/A |
| Unstick refused | not `reviewing`, or live thread | nothing changes | `not_allowed` |
| Page | AppTest with a pending item and `APPROVER_NAME` set | Approvals tab lists it; clicking Release releases it | N/A |

</frozen-after-approval>

## Code Map

- `cases/expense/dashboard_data.py` -- extend it, reusing:
  - `resolve_db` and `_connect`
  - `load_dotenv`
  - `start_review` and `running(db)`, the live-thread set
  - `fmt_cents`
  - `action_for(state)`, which story 2 extends for Re-review and Unstick, or adds a sibling helper
- `cases/expense/dashboard.py` -- the tabs `st.tabs(["Claims", "Claim view", "Flags"])` gain an "Approvals" tab, and the claims rows gain buttons next to the `start-{cid}` button. Keep `_snapshot` and auto-refresh as they are.
- `cases/expense/load_seed.py` -- `SCHEMA`: the `decisions` columns `payout_status`, `released_by` and `released_at`, and the `claims.state` CHECK.
- `cases/expense/tests/test_dashboard.py` -- reuse its fake model, `script` helpers, temporary-db fixtures and AppTest pattern. To make pending items, review CL-2001 with the fake model; it has an approved flight over $500 (L-3001).

## Tasks & Acceptance

**Execution:**
- [x] `cases/expense/dashboard_data.py` -- `approval_queue`, `release`, `re_review` and `unstick` -- CAP-3, CAP-5, CAP-6
- [x] `cases/expense/dashboard.py` -- the Approvals tab with Release, the approver display and warning, and the Re-review and confirmed Unstick buttons -- CAP-3, CAP-5, CAP-6
- [x] `cases/expense/tests/test_dashboard_actions.py` -- every matrix row, plus an AppTest release click -- CAP-3, CAP-5, CAP-6

**Acceptance Criteria:**
- Given a pending item and `APPROVER_NAME` in `.env`, when the reviewer clicks **Release** in the running dashboard, then the item leaves the queue and its row shows `released`, the approver and the time.
- Given the tests, when `uv run pytest` runs, then all tests pass, the only skips are `live` tests, and the 181 existing tests still pass.

## Implementation Notes

- **Files:**
  - `cases/expense/dashboard_data.py`: `approval_queue`, `approver_name`, `release`, `re_review`, `unstick` and `recovery_actions`. `claim_lines` now also returns `released_by` and `released_at`.
  - `cases/expense/dashboard.py`: the Approvals tab, the approver display, the Recovery column, and Unstick with Confirm and Cancel.
  - `cases/expense/tests/test_dashboard_actions.py`: 30 new tests. The whole suite now shows 211 passed and 1 skipped (the live test).
- **Story 1 tests changed:**
  - The no-writes test now allows UPDATE and DELETE only inside `release`, `re_review` and `unstick`, and allows no INSERT anywhere. That is stricter than before.
  - The tab count is now 4.
- **Choices made during implementation:**
  - Re-review is hidden while a live thread exists, the same as Review.
  - The live-thread check only sees this process, which is why Unstick warns and asks for confirmation.
  - `APPROVER_NAME` changes need a dashboard restart.

- **Review patches (pass 1):**
  - `re_review` builds the model before any write, and returns `no_model` without changing anything if that fails.
  - Workers are registered while the lock is still held (`_start_locked`), and a live thread is never overwritten.
  - Release buttons are disabled when no approver is set.
  - `release` returns the stamp it wrote.
  - `.env.example` documents `APPROVER_NAME`.
  - The no-writes test now scans the whole module.
  - A stale Unstick confirmation is cleared.
  - Tests added for hiding recovery buttons while a thread is live, and for the action notices.
- **Test-suite slowdown:**
  - Two full runs on the orchestrator took 9 min 55 s and 16 min 30 s. Each file group took about 1 minute on its own.
  - An autouse `clean_workers` fixture now opens every gate, joins every worker and clears the stored worker state after each test.
  - A clean full run is back to 1 min 57 s: 217 passed and 1 skipped (the live test), with no test over 5 seconds. The slowdown didn't reproduce afterwards; the likeliest cause is gated workers left stuck while the tests were half-edited.

## Spec Change Log

## Review Triage Log

Review pass 1. Reviewer codes: BH = Blind Hunter, EC = Edge Case Hunter, VG = Verification Gap.

| # | Finding | Verdict | Evidence / route |
|---|---|---|---|
| EC1 | Re-review deletes decisions, then the worker's `make_model` fails (no key or no quota) | medium | The page passes `model=None`, so the model is built in the thread after the DELETE. The claim is wiped and reset while the page says "started" → **patch** (build the model before the transaction; refuse on failure) |
| BH1 / EC2 | `re_review` releases the lock before registering the worker | medium | In the gap another session can start a run and overwrite `_threads`, leaving the real worker untracked, so Unstick would be offered → **patch** (register under the lock) |
| BH4 | Release buttons render when no approver is set | low | A direct fix (`disabled`) → **patch** |
| BH5 | The approver is read three times, and the notice may not name the one stamped | low | A direct fix: `release` returns `released_by` and `released_at` → **patch** |
| BH7 | `APPROVER_NAME` isn't in `.env.example`, and the restart note isn't visible | low | A doc addition, plus a hint in the page warning → **patch** |
| BH8 / EC6 | The no-writes test only checks top-level `def`s | low | The note calling it "stricter" overclaims. A direct fix: scan the whole module outside the allowed functions → **patch** |
| EC5 | A stale `confirm-unstick` session key | low | A direct fix: clear it when Unstick no longer applies → **patch** |
| VG1 | The page's hiding of recovery buttons while a thread is live is untested | medium | Dropping `live=` still passes → **patch** |
| VG2 | The Re-review and Unstick notice text is untested | medium | Inverting the status checks still passes → **patch** |
| BH2 | Re-review deletes decisions with one click and no confirmation | low | The engine is deterministic, so a re-review re-records the same decisions and pending items come back pending. Only explanations change → rejected |
| BH3 | Release has no confirmation and no undo | low | The click is the human yes the gate requires, and the spec has no undo → rejected |
| BH6 | A re-review's verdict on a released line is silently dropped | false | The engine is deterministic and `record_decision` refuses anything but its decision, so the verdict can't differ |
| BH9 | `_live` duplicates `running` | low | Minor → rejected |
| BH10 | Missing tests: release racing a re-review, Cancel state | low | The re-review failure path is covered by EC1's patch; the rest is minor → rejected |
| EC3 | A busy database under the lock can block for 30 seconds | low | Single user → rejected |
| EC4 | An exception in an action shows a traceback | low | Unlikely → rejected |

## Verification

**Commands:**
- `uv run pytest` -- expected: all tests pass, and the only skips are `live` tests.
- `git status --short` -- expected: changes only in `cases/expense/`, with no `app.db` or `mlflow.db`.
