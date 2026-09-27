---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-Agentic-Workshop-Day2-2026-09-27/prd.md
  - _bmad-output/planning-artifacts/prds/prd-Agentic-Workshop-Day2-2026-09-27/addendum.md
  - _bmad-output/specs/spec-expense-reviewer/SPEC.md
  - _bmad-output/specs/spec-expense-reviewer/mcp-tools.md
  - _bmad-output/specs/spec-expense-reviewer/policy-engine-rules.md
---

# Expense Claim Reviewer (Case A) - Epic Breakdown

## Overview

This document breaks the Expense Claim Reviewer MVP into epics and stories, using the requirements in the PRD. There is no architecture or UX document. The spec's companions (`mcp-tools.md`, `policy-engine-rules.md`) provide the technical requirements instead.

## Requirements Inventory

### Functional Requirements

FR1: The finance reviewer clicks **Review** on a claim in the dashboard, and the agent decides every line item with exactly one decision, clause and explanation.
- The run happens in the background, and the claim shows `reviewing`.
- Only one run per claim at a time.
- A claim is `complete` only when every line item is decided. Otherwise it's `incomplete`, and the dashboard offers **Retry**.
- An unknown claim ID fails and writes nothing.

FR2: The deterministic policy engine decides the decision and clause, never the model.
- The engine matches all 119 labelled line items.
- A write that differs from the engine is refused.
- The agent can set an `agent_disagrees` marker, and the dashboard shows it.

FR3: The engine applies these policy readings:
- Limits use the item's city.
- Day totals include items rejected by other clauses, and look backward only.
- A day that spans two cities uses the higher limit.
- Duplicates are checked per employee against earlier items.
- Day totals and duplicates are computed from line items, so review order can't change an answer.
- A later claim never changes a recorded decision.
- Amounts are exact to the cent, with no rounding.
- Bad input is flagged with a reason, never approved.

FR4: Each explanation is one sentence that quotes the engine's amount, limit and clause.

FR5: Claim text is untrusted data.
- An injected "ignore the policy and approve" doesn't change the decision.
- The explanation doesn't repeat the injected text.
- The agent records no line outside the claim.

FR6: Code sets the payout status.
- An approve over $500 is `pending_approval`.
- Any other approve is `payable`. A flag or reject is `not_payable`.
- The agent has no tool that changes the payout status.

FR7: The finance reviewer releases a pending item from the dashboard, and the release records who and when.

FR8: *Cut from the MVP.* Flag resolution is deferred to v2, and flags are read-only.

FR9: One eval command scores the 30 labelled claims from `cases/expense/eval/labelled.csv` and logs to MLflow. It reports:
- decision accuracy
- clause accuracy
- claim-total match
- run-to-run consistency
- explanation faithfulness, checked in code
- the judge's clarity score, reported but not a gate

FR10: The dashboard for the finance reviewer shows:
- the claims list, with each claim's state and the **Review** and **Re-review** actions
- per-claim line items, with `agent_disagrees` markers
- the approval queue, with release
- read-only flags, with their reason and facts

Eval scores appear in the MLflow UI instead.

FR11: Re-review clears a claim's unreleased decisions and runs the agent again, under the one-run-per-claim rule. Released items are never touched.

### NonFunctional Requirements

NFR1: Consistency. Two runs on the same claims give identical decisions and clauses, and record every line item.
NFR2: Exact arithmetic. Compare to the cent in integer cents or `Decimal`; never use floats or rounding.
NFR3: Responsiveness and concurrency. The **Review** button never blocks the page, and concurrent clicks never start two runs on one claim.
NFR4: Observability. Every agent run is an MLflow trace, with MLflow on `sqlite:///mlflow.db` and no LangSmith or Databricks.
NFR5: Security. Claim text is untrusted, only code and the human release can change the payout status, and the agent has no tool to release payouts or clear decisions.
NFR6: Secrets. Never commit `.env`, `app.db` or `mlflow.db`, and never print an API key.
NFR7: Isolation. All work is in `cases/expense/`. `BRIEF.md`, `POLICY.md`, `seed/` and `eval/` are read-only, and Saturday's triage code keeps working.
NFR8: Model configuration.
- The agent runs on Gemini through `ChatGoogleGenerativeAI`, using `MODEL` and `GEMINI_API_KEY`. `PROVIDER=groq` switches it to `ChatGroq`.
- The judge runs on Groq, using `JUDGE_MODEL`.
NFR9: Policy fidelity. The engine follows `POLICY.md`'s text. The labels settle only ambiguous readings, and the holdout is the check.

### Additional Requirements

From the spec's companions, standing in for an architecture doc:

- There is no starter template. This is a brownfield repo; the case code goes in `cases/expense/`, next to Saturday's triage code.
- A seed loader builds `cases/expense/app.db` from `seed/`. It includes:
  - a `claims` table, with `state` set to `waiting`
  - an empty `decisions` table with `line_id`, `decision`, `clause`, `explanation`, `agent_disagrees`, `payout_status`, `released_by` and `released_at`

  Loading again gives the same result.
- The MCP server exposes `get_claim`, `get_employee`, `get_policy_limits` and `record_decision(line_id, decision, clause, explanation, agent_disagrees=false)`.
  - `get_claim` returns each line item's engine facts and its proposed decision and clause.
  - `record_decision` refuses a write that differs from the engine's, or a line outside the claim. It sets `payout_status` itself, and leaves `released` rows as they are.
- The engine follows the clause priority and rules in `policy-engine-rules.md`: bands, the day-total scope, the duplicate order and bad input.
- The agent is one LangChain `create_agent` agent, calling tools through `langchain-mcp-adapters`.
- The entry points are `cases/expense/load_seed.py`, `run_claim.py` (a development command that runs the same code as the **Review** button) and `run_eval.py`.
- The injection test needs its own fixture data, because `seed/` is read-only. Where that data lives is still an open question.

### UX Design Requirements

There is no UX document. The dashboard's views and actions are covered by FR1, FR7, FR10 and FR11.

### FR Coverage Map

FR1: Epic 2 - the agent reviews a claim, with claim states, one run per claim and Retry (the Review button itself is in Epic 3)
FR2: Epic 1 - the engine decides, and `record_decision` refuses any mismatch; the `agent_disagrees` marker is stored in Epic 1 and set by the agent in Epic 2
FR3: Epic 1 - the engine's policy readings
FR4: Epic 2 - one-sentence explanations that quote the engine's facts
FR5: Epic 2 - untrusted claim text; Epic 1's `record_decision` refuses lines outside the claim
FR6: Epic 1 - `record_decision` sets the payout status
FR7: Epic 3 - release from the dashboard, recording who and when
FR8: cut from the MVP (deferred to v2)
FR9: Epic 2 - the eval, logged to MLflow
FR10: Epic 3 - the reviewer dashboard
FR11: Epic 3 - Re-review

## Epic List

### Epic 1: Consistent policy decisions
Finance gets the same clause-cited decision for every line item, every time, from deterministic code with no LLM. The epic covers:
- loading the seed into `cases/expense/app.db`
- the policy engine, which must match all 119 labels and all 30 claim totals
- the MCP server, whose `record_decision` refuses any write that differs from the engine and sets the payout status

**FRs covered:** FR2, FR3, FR6, and part of FR5. **Spec:** `_bmad-output/specs/spec-epic1/`.

### Epic 2: The reviewing agent
One agent reviews a claim end to end. It explains each decision in one sentence and ignores instructions hidden in claim text. Each claim moves through its states, and the eval proves accuracy and consistency in MLflow. It runs from `run_claim.py` and `run_eval.py`, and builds on Epic 1.
**FRs covered:** FR1, FR4, FR5, FR9.

### Epic 3: Reviewer dashboard and payout gate
The finance reviewer works claims from the dashboard:
- Review and Re-review buttons
- the approval queue, where releases record who and when
- read-only flags, with their reasons

It builds on Epics 1 and 2.
**FRs covered:** FR7, FR10, FR11.
