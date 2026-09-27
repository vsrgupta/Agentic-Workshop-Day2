# MCP tools and data

The contract for the case's MCP server (CAP-3) and database (CAP-1).

## Tools

| Tool | Returns / does |
|---|---|
| `get_claim(claim_id)` | Claim `employee_id`, `submitted_at`, `purpose`, and its line items. Each line item carries the engine's facts (amount, the limit that applies, day total where relevant, % over the limit, age in days, duplicate-of line ID, receipt, ITA code found) and the **proposed decision and clause**. |
| `get_employee(employee_id)` | The employee's level and home city. |
| `get_policy_limits(level, city)` | The limit for each category at that level and city. |
| `record_decision(line_id, decision, clause, explanation)` | Writes one row to `decisions`. It refuses the write if the decision or clause differs from the engine's. It sets `payout_status` itself. |

`explanation` is a deliberate addition to the `BRIEF.md` signature, so the judge has text to score.

No tool changes `payout_status` after the write, and no tool releases a payout.

## `decisions` table

| Column | Notes |
|---|---|
| `line_id` | the line item decided |
| `decision` | `approve`, `flag` or `reject` |
| `clause` | a `POLICY.md` clause, for example `2.1` |
| `explanation` | one sentence, written by the LLM from the engine's facts |
| `payout_status` | `pending_approval` for an approve over $500; `payable` for any other approve; `not_payable` for flag or reject. A human release changes `pending_approval` to released. |

## Reimbursable total

A claim's reimbursable total is the sum of its approved items, including those still waiting for release.
