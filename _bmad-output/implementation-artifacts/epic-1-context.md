# Epic 1 Context: Consistent policy decisions

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Build the deterministic core of the expense-claim reviewer (Case A, in `cases/expense/`) before any LLM exists. That means three pieces: a seed loader for the case database, a pure-Python policy engine that gives every line item exactly one decision (approve, flag or reject) and the clause behind it, and an MCP server whose `record_decision` can only write what the engine decided and sets the payout status itself. Consistency is the whole product, meaning the same claim gets the same clause-cited decision every time. This epic is what guarantees it. Epic 2 (the agent and the eval) and Epic 3 (the dashboard, release and Re-review) build on it.

## Stories

- Story 1.1: Seed data and policy engine
- Story 1.2: MCP server with guarded record_decision

## Requirements & Constraints

- **Loader:** `cases/expense/load_seed.py` builds `cases/expense/app.db` from `cases/expense/seed/`. The result is 40 claims, 159 line items, 12 employees and 80 limits. It also adds a `claims` review-state table with every claim in `waiting`, and an empty `decisions` table. Loading twice gives an identical database.
- **Engine accuracy:** it matches all 119 rows of `cases/expense/eval/labelled.csv` and all 30 labelled claim totals. Its output doesn't depend on the order claims are processed in.
- **Threshold-edge unit tests:**
  - exactly the limit, and the limit + $0.01
  - exactly 1.2 × the limit, and 1.2 × the limit + $0.01
  - $25.00 and $25.01
  - 60 and 61 days
  - $500.00 and $500.01 for the gate
- **Bad input:** an unknown category, a missing limit row, or an amount of zero or less is flagged with a stated reason and never approved. An unknown claim ID is an error and writes nothing.
- **MCP reads:** `get_claim`, `get_employee` and `get_policy_limits` return the contracted fields. An unknown ID returns an error, not an empty result.
- **`record_decision` refuses and writes nothing** when the decision or clause differs from the engine's, or when `line_id` isn't in `claim_id`. A valid write stores the explanation and `agent_disagrees`. An existing `released` row is left untouched and reported as already decided.
- **Payout status is set by code:**
  - an approve over $500 → `pending_approval`
  - any other approve → `payable`
  - a flag or reject → `not_payable`
- **Tool surface:** no tool changes `payout_status` after the write, sets `released`, or clears decisions.
- **Scope:**
  - No LLM in this epic. Everything is verified with `uv run pytest`.
  - Out of scope: claim state transitions (only the `state` column is created), explanations, injection tests, the eval, MLflow, the dashboard, release and Re-review.
- **Files:**
  - `BRIEF.md`, `POLICY.md`, `seed/` and `eval/` in the case are read-only.
  - Saturday's triage code must keep working and must not be changed.
  - Never commit `app.db`, `.env` or `mlflow.db`.
  - Add packages with `uv add`, never pip.

## Technical Decisions

- **Layout:** the engine is `cases/expense/policy_engine.py`. The MCP server is a stdio FastMCP server at `cases/expense/mcp_server.py`. It mirrors the structure of `mcp/triage_server.py`, which must not be modified.
- **Exact money:** use integer cents or `Decimal` everywhere. No floats and no rounding. The thresholds are exactly the policy's numbers.
- **Clause priority** (the first rule that applies wins):
  1. Category `alcohol`, `personal` or `fine` → reject under 3.1, 3.2 or 3.3.
  2. A duplicate → reject under 5.1.
  3. Dated more than 60 days before the claim's `submitted_at` → reject under 1.2.
  4. `software` or `equipment` → approve under 4.1 if the description contains `ITA-` followed by digits, otherwise reject under 4.1.
  5. Limit bands → 2.1 meals, 2.2 hotel, 2.3 flight, 6.1 ground.
  6. Over $25 and `has_receipt` ≠ `yes` → flag under 1.3. This applies only when step 5 approved, so an over-limit item keeps its limit clause.
  7. Otherwise → approve under the category's clause.
- **Bands:**
  - total ≤ limit → approve
  - limit < total ≤ 1.2 × limit → flag
  - total > 1.2 × limit → reject
- **Strict thresholds:** 1.3 applies to an amount *over* $25, 1.2 to an item *more than* 60 days old, and the payout gate to an approve *over* $500.
- **Limit lookup:** the key is `(employee level, line item city, category)`. Use the item's city, never the employee's home city.
  - Hotel and flight compare the line amount.
  - Meals and ground compare the day total.
- **Day total:**
  - It sums the employee's items in that category on that date, including items rejected by earlier rules (duplicates, stale items).
  - It covers this claim and the same employee's earlier-submitted claims only, never later ones.
  - Every item that day gets the day's band.
  - A two-city day uses the higher of those cities' limits.
- **Duplicates (5.1):**
  - The key is the same employee, date, merchant and amount.
  - An item is checked within its own claim and against the employee's earlier claims.
  - "Earlier" means the earlier `submitted_at`, then the lower claim ID on a tie, then the earlier row in `line_items.csv`.
  - The earliest item is decided normally. Each later match is rejected.
- **Order independence:** day totals and duplicates are computed from line items, never from recorded decisions. A later claim never changes a recorded decision.
- **Reference cases** (use them as test anchors):
  - L-3062/L-3063: the duplicate still counts toward the day total, so L-3062 is flagged under 2.1.
  - L-3102/L-3103: the same pattern pushes L-3102 to reject under 2.1.
  - L-3082/L-3083: both are rejected under 2.1 on the day total.
  - L-3055/L-3051: approved and rejected under 4.1, on whether an ITA code is present.
  - L-3075: ground $33.66 with no receipt, under the limit → flagged under 1.3.
- **Rejected readings:** limits by home city match only 109/119 labels. Day totals that exclude rejected items match only 117/119. Day totals that count later claims are rejected because they could change recorded decisions.
- **`get_claim` output:** returns the claim's `employee_id`, `submitted_at`, `purpose` and its line items. Each line item carries the engine's facts: amount, the applicable limit, day total where relevant, % over the limit, age in days, duplicate-of line ID, receipt and ITA code found. It also carries the proposed decision and clause.
- **`get_employee` and `get_policy_limits`:**
  - `get_employee` returns the employee's level and home city. The home city is never a limit key.
  - `get_policy_limits(level, city)` returns the limit for each category.
- **`record_decision` signature:** `(claim_id, line_id, decision, clause, explanation, agent_disagrees=false)`. `claim_id`, `explanation` and `agent_disagrees` are deliberate additions to the brief's signature.
- **`decisions` table:** one row per line, with these columns:
  - `line_id`
  - `decision` (`approve`, `flag` or `reject`)
  - `clause`
  - `explanation`
  - `agent_disagrees`
  - `payout_status` (`pending_approval`, `payable`, `not_payable` or `released`)
  - `released_by`, `released_at`
- **`claims` state values:** `waiting`, `reviewing`, `complete` or `incomplete`.
- **Reimbursable total:** a claim's total is the sum of its approved items (pending, payable or released), in exact cents. The 30 claim-total checks use it.

## Cross-Story Dependencies

- **Within this epic:** Story 1.2 depends on 1.1.
  - `get_claim` exposes 1.1's engine facts and proposed decisions.
  - `record_decision` validates against the engine.
  - Both use the database and schema that 1.1 creates.
- **Epic 2:**
  - Its agent calls these MCP tools through `langchain-mcp-adapters`.
  - It sets `agent_disagrees` and writes the explanations.
  - It drives the `claims.state` transitions.
- **Epic 3:**
  - It performs the human release (`released`, `released_by`, `released_at`) and Re-review, which deletes unreleased decisions.
  - Those are human actions outside the agent's tool surface, so the Epic 1 schema must support them without exposing them as tools.
