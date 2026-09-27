# MCP tools and data

The contract for the case's MCP server (CAP-3) and database (CAP-1).

## Tools

| Tool | Returns / does |
|---|---|
| `get_claim(claim_id)` | Claim `employee_id`, `submitted_at`, `purpose`, and its line items. Each line item carries the engine's facts (amount, the limit that applies, day total where relevant, % over the limit, age in days, duplicate-of line ID, receipt, ITA code found) and the **proposed decision and clause**. |
| `get_employee(employee_id)` | The employee's level and home city. |
| `get_policy_limits(level, city)` | The limit for each category at that level and city. |
| `record_decision(line_id, decision, clause, explanation, agent_disagrees=false)` | Writes one row to `decisions`. It refuses the write if the decision or clause differs from the engine's, or if the line isn't in the claim under review. It sets `payout_status` itself. |

- `explanation` and `agent_disagrees` are deliberate additions to the `BRIEF.md` signature.
- The agent quotes limits only from `get_claim`'s facts. The home city from `get_employee` is never a limit key.
- No tool changes `payout_status` after the write, releases a payout or clears decisions. Release and Re-review are human actions outside the agent.

## Tables

**`claims` (review state)**

| Column | Notes |
|---|---|
| `claim_id` | from the seed |
| `state` | `waiting`, `reviewing`, `complete` or `incomplete` |

Only one run per claim at a time: a claim in `reviewing` can't start another run.

**`decisions`**

| Column | Notes |
|---|---|
| `line_id` | the line item decided; one row per line |
| `decision` | `approve`, `flag` or `reject` |
| `clause` | a `POLICY.md` clause, for example `2.1` |
| `explanation` | one sentence, written by the LLM, stating the engine's amount, limit and clause |
| `agent_disagrees` | true when the LLM disputes the engine; shown on the dashboard |
| `payout_status` | see below |
| `released_by`, `released_at` | set by the human release |

**`payout_status` values:**
- `pending_approval`: an approve over $500.
- `payable`: any other approve.
- `not_payable`: a flag or reject.
- `released`: set only by a human release from `pending_approval`.

## Re-review

Re-review deletes the claim's decisions whose `payout_status` is not `released`, then runs the agent again. Released rows are never touched. `record_decision` leaves an existing `released` row as it is and reports it as already decided.

## Reimbursable total

A claim's reimbursable total is the sum of its approved items, whether pending, payable or released. It's computed in exact cents.
