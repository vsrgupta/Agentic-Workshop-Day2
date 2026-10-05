# Epic 3 Context: Reviewer dashboard and payout gate

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

The engine and agent from Epics 1 and 2 can decide and explain every line item, but right now a finance reviewer has to use the terminal to run them. This epic gives the reviewer one Streamlit dashboard for the expense-claim case (`cases/expense/`). From it the reviewer starts reviews in the background, reads each claim's decisions (including `agent_disagrees` markers), releases approved items over $500 from an approval queue, and sees read-only flags with their reasons. It also adds Re-review, which replaces unreleased decisions, and Unstick, which recovers a claim left in `reviewing` after a hard kill. The $500 human approval gate is not optional. With this epic it becomes usable at the demo.

## Stories

- Story 3.1: The dashboard (claims list, Review/Retry, claim view, flags view)
- Story 3.2: Payout and recovery actions (approval queue + Release, Re-review, Unstick)

## Requirements & Constraints

- **Claims list:** all 40 claims, each with its state (`waiting`, `reviewing`, `complete`, `incomplete`). **Review** on `waiting` and **Retry** on `incomplete` start a background review. The page never blocks and the claim shows `reviewing`. A second click on a `reviewing` claim starts nothing, and concurrent clicks never start two runs. When the run ends the claim shows `complete` or `incomplete`.
- **Claim view:** for each line item, show the decision, clause, explanation and payout status, plus a visible marker when `agent_disagrees` is set.
- **Approval queue and Release:** list every `pending_approval` row. Release sets `payout_status='released'`, `released_by=APPROVER_NAME` and `released_at` = the current UTC time as ISO-8601. It works only on `pending_approval` rows. It is refused with a message when `APPROVER_NAME` is unset. The dashboard displays the approver name. Release changes a status and moves no money.
- **Flags view (read-only):** every `flag` decision with its clause, or the bad-input reason when the clause is null, plus the engine's facts. If time runs short this is cut first.
- **Re-review:** allowed on `complete` or `incomplete` claims. It deletes that claim's decisions whose `payout_status != 'released'`, sets the claim back to `waiting`, then runs the review under the one-run-per-claim rule. Released rows stay byte-for-byte unchanged.
- **Unstick:** shown only on `reviewing` claims. It first warns that a live run may still be going, then moves the claim to `incomplete` so Retry works. It changes no decisions.
- **Out of scope:** paying anyone, resolving flags (v2), eval scores in the dashboard (they stay in the MLflow UI), logins, multiple approvers, hosting, and emailing.
- **Demo success:** the 10 holdout claims are reviewed with the Review button and end `complete`. The 5 approved items over $500 wait in the queue. Releasing one stamps the approver and the time. Re-review leaves a released item released. Unstick recovers a claim forced into `reviewing`.
- **Observability:** every agent run is an MLflow trace on `sqlite:///mlflow.db`, with no LangSmith or Databricks.
- **Security and secrets:** claim text is untrusted, so render it as data and never act on it. Only code and the human release change `payout_status`. Never commit `.env`, `app.db` or `mlflow.db`, and never print an API key.

## Technical Decisions

- **Stack:** Streamlit, added with `uv add streamlit` (it isn't a dependency yet; `python-dotenv` already is). The entry point is `uv run streamlit run cases/expense/dashboard.py`. `APPROVER_NAME` is loaded from `.env`.
- **Logic outside Streamlit:** Release, Re-review, Unstick and the list/view/queue/flags queries are plain functions that are testable without Streamlit. Tests use a temporary database (built with `load_seed.build(db_path, seed_dir)`) and never a real model.
- **Human actions only:** Release, Re-review and Unstick live in dashboard code, never as agent tools. The four MCP tools (`get_claim`, `get_employee`, `get_policy_limits`, `record_decision`) and the agent stay unchanged. The dashboard never decides and never writes a decision itself.
- **Reviews reuse Epic 2:** use `review.review_claim(claim_id, db_path=None, model=None)`, which is async. Run it in a worker thread with `asyncio.run(review_claim(...))` and let the page refresh to show the state.
  - It returns `{"claim_id", "status": "complete"|"incomplete", "lines": [...]}`, or `{"claim_id", "status": "already_reviewing"|"already_complete"}` without running.
  - It raises `KeyError` for an unknown claim and `FileNotFoundError` if `app.db` is missing.
  - Any run exception leaves the claim `incomplete` and is re-raised, so the worker must catch it.
  - It builds the model before the state change, so a missing key changes no state.
  - It does not configure MLflow. Callers set the tracking URI and experiment `expense-reviewer`, and enable langchain autolog, as `run_claim.py` does.
- **State transitions:** `review._start` atomically moves `waiting|incomplete -> reviewing` in one conditional UPDATE. `_finish` only updates a claim that is still `reviewing`. Because a `complete` claim returns `already_complete`, Re-review must reset the state to `waiting` before calling `review_claim`. Unstick must likewise be a conditional `reviewing -> incomplete` UPDATE.
- **Database** (`cases/expense/app.db`, `review.DB_PATH`; `EXPENSE_DB` overrides it for the MCP server):
  - `claims(claim_id, employee_id, submitted_at, purpose, state CHECK in waiting/reviewing/complete/incomplete)`
  - `line_items(line_id, claim_id, seq, date, city, category, merchant, amount_cents, has_receipt, description)`
  - `decisions(line_id PK, decision CHECK approve/flag/reject, clause NULL, explanation, agent_disagrees INTEGER 0/1, payout_status CHECK pending_approval/payable/not_payable/released, released_by, released_at)`
  - Money is integer cents, so display it as exact dollars and cents with no floats.
- **Payout status** is set by `record_decision`: `pending_approval` for an approve over $500, `payable` for other approves, `not_payable` for flags and rejects, and `released` only via the human Release from `pending_approval`. `record_decision` leaves existing rows alone (`already_decided`), so a Retry skips decided lines and released rows survive Re-review.
- **Flag facts and reason:** these are not stored in `decisions`. Get them from `policy_engine.decide_claim(policy_engine.connect(db_path), claim_id)`, which returns `{line_id: Result}`. `Result` has `decision`, `clause` (None only for bad input), `reason` (set only for bad input) and `facts` (`amount_cents`, `limit_cents`, `day_total_cents`, `pct_over`, `age_days`, `duplicate_of`, `has_receipt`, `ita_code`).
- **Isolation:** all work stays in `cases/expense/`. `BRIEF.md`, `POLICY.md`, `seed/` and `eval/` are read-only, and Saturday's triage code is untouched.

## UX & Interaction Patterns

- Single-user local dashboard with four areas: claims list (state + Review/Retry/Re-review/Unstick per state), per-claim line-item view, approval queue with Release, read-only flags view.
- Action visibility follows state: Review on `waiting`, Retry on `incomplete`, Re-review on `complete`/`incomplete`, Unstick only on `reviewing` (with a warning/confirmation first).
- Review never blocks the page; state updates appear on refresh.
- Show the configured approver name; a refused release shows a clear message.

## Cross-Story Dependencies

- Story 3.2 builds on 3.1's app shell, DB-query helpers, and background-review runner (Re-review reuses the same runner and one-run-per-claim path).
- Depends on Epic 1 (`load_seed`, `policy_engine`, `mcp_server.record_decision` payout rules) and Epic 2 (`review.review_claim` and its claim-state handling); neither is to be changed by this epic.
