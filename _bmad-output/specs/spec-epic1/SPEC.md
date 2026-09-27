---
id: SPEC-epic1
companions: [../../../cases/expense/POLICY.md, ../spec-expense-reviewer/policy-engine-rules.md, ../spec-expense-reviewer/mcp-tools.md]
sources: [../../planning-artifacts/epics.md]
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Epic 1: consistent policy decisions

## Why

Consistency is the whole product: the same claim must get the same clause-cited decision every time. This epic builds the part that guarantees it, before any LLM exists: the seed data, a deterministic policy engine, and an MCP server whose `record_decision` can't write anything the engine didn't decide. Epic 2's agent and Epic 3's dashboard build on it. It is Epic 1 of 3 in the expense case (parent spec: `spec-expense-reviewer`).

## Capabilities

- **CAP-1**
  - **intent:** A person can load the case's seed data into the case database, with the review-state and decisions tables empty and ready.
  - **success:** After a load, `cases/expense/app.db` holds:
    - 40 claims, 159 line items, 12 employees and 80 limits
    - a `claims` state of `waiting` for every claim
    - an empty `decisions` table with the columns in `mcp-tools.md`

    Loading a second time gives the same result.

- **CAP-2**
  - **intent:** A deterministic policy engine gives every line item its facts and its decision and clause, following `policy-engine-rules.md`.
  - **success:**
    - The engine matches all 119 rows of `cases/expense/eval/labelled.csv` and all 30 labelled claim totals.
    - Its output is the same for any order the claims are processed in.
    - An unknown category, a missing limit or a non-positive amount is flagged with a reason, never approved.
    - Unit tests sit on every threshold edge: exactly the limit, the limit plus one cent, exactly 1.2 × the limit, 1.2 × the limit plus one cent, $25.00 and $25.01, 60 and 61 days, and $500.00 and $500.01 for the gate.

- **CAP-3**
  - **intent:** An MCP client can read claims, employees and limits, with each line item carrying the engine's facts and proposed decision.
  - **success:**
    - `get_claim`, `get_employee` and `get_policy_limits` return the fields in `mcp-tools.md`.
    - `get_claim` includes, for each line item, the engine's facts and its proposed decision and clause.
    - An unknown ID returns an error, not an empty result.

- **CAP-4**
  - **intent:** `record_decision(claim_id, line_id, …)` writes only what the engine decided for a line in that claim, and sets the payout status itself.
  - **success:**
    - It refuses, and writes nothing, when the decision or clause differs from the engine's, or when `line_id` isn't in `claim_id`.
    - A valid write stores the explanation and the `agent_disagrees` marker.
    - A valid write sets `payout_status`: `pending_approval` for an approve over $500, `payable` for any other approve, and `not_payable` for a flag or reject.
    - An existing `released` row is left as it is and reported as already decided.

## Constraints

- **No LLM** in this epic. Everything is verified with `uv run pytest`.
- **Exact amounts:** compare in integer cents or `Decimal`, with no rounding and no floats.
- **Read-only:** `BRIEF.md`, `POLICY.md`, `seed/` and `eval/` in the case, and Saturday's triage code.
- **Tool surface:** no tool changes `payout_status` after the write, sets `released`, or clears decisions.
- **Engine rules follow `POLICY.md`'s text.** The labels settle only the readings recorded in `policy-engine-rules.md`.
- **Secrets and data files:** never commit `app.db`, `.env` or `mlflow.db`.

## Non-goals

- The agent, explanations written by an LLM, and the injection test (Epic 2).
- The eval and MLflow tracing (Epic 2).
- Claim state transitions such as `reviewing`, `complete`, `incomplete` and Retry (Epic 2). This epic only creates the `state` column.
- The dashboard, the human release, and Re-review (Epic 3).

## Success signal

`uv run pytest` passes. That run includes:
- the engine matching 119/119 labels and 30/30 claim totals
- the threshold-edge tests
- the `record_decision` refusal and payout-status tests

Running `uv run python cases/expense/load_seed.py` twice leaves the same database.

## Assumptions

- The engine lives in `cases/expense/policy_engine.py`.
- The MCP server is a stdio FastMCP server at `cases/expense/mcp_server.py`, like Saturday's `mcp/triage_server.py`.
- The loader is `cases/expense/load_seed.py`.
