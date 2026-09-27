# Policy engine rules

How the deterministic engine (CAP-2) reads `POLICY.md`. `POLICY.md` is the authority; these rules settle readings it leaves open. With these rules, a reference implementation matches all 119 labelled line items.

## Clause priority

For each line item, the first rule that applies decides:

| Order | Clause | Rule | Decision |
|---|---|---|---|
| 1 | 3.1 / 3.2 / 3.3 | category `alcohol` / `personal` / `fine` | reject |
| 2 | 5.1 | duplicate of an earlier item (see below) | reject |
| 3 | 1.2 | item date more than 60 days before the claim's `submitted_at` | reject |
| 4 | 4.1 | category `software` or `equipment`: description contains `ITA-` followed by digits | approve, else reject |
| 5 | 2.1 / 2.2 / 2.3 / 6.1 | amount against limit (see bands) | approve / flag / reject |
| 6 | 1.3 | amount > $25 and `has_receipt` ≠ `yes` | flag |
| 7 | category default | none of the above | approve under 2.1 meals, 2.2 hotel, 2.3 flight, 6.1 ground |

Step 6 applies only when step 5 approved: an over-limit item takes its limit clause, not 1.3.

## Limits

- **Lookup key:** `(employee level, line item city, category)` from `seed/limits.csv`. The city is the **item's** city, not the employee's home city.
- **Compared amount:**
  - hotel (2.2): the line amount; one line is one night.
  - flight (2.3): the line amount; one line is one trip.
  - meals (2.1) and ground (6.1): the **day total**.
- **Day total:** the sum of all of that **employee's** items in that category on that date, **across all their claims**, including items rejected by an earlier rule (duplicates, stale items). Every item in that day gets the day's band.
- **Bands (2.4):** total ≤ limit → approve. limit < total ≤ 1.2 × limit → flag. Total > 1.2 × limit → reject.

## Duplicates (5.1)

- **Key:** same employee, date, merchant and amount.
- **Scope:** the item is checked against the employee's other items in the same claim and in every claim they submitted earlier.
- **Earlier:** the claim with the earlier `submitted_at`. On a tie, the lower claim ID. Within one claim, the earlier row in `seed/line_items.csv`.
- **Outcome:** the earliest item stands and is decided normally; each later match is rejected under 5.1.

## Reference cases from the labels

| Line | Shows |
|---|---|
| L-3062 / L-3063 | Duplicate meal: L-3063 is rejected under 5.1, but still counts toward the day total, so L-3062 is flagged under 2.1. |
| L-3102 / L-3103 | Same pattern: the day total pushes L-3102 to reject under 2.1. |
| L-3082 / L-3083 | Two meals on one day, both rejected under 2.1 on the day total. |
| L-3055 / L-3051 | Figma licence with an ITA- code is approved under 4.1; the one without is rejected under 4.1. |
| L-3075 | Ground item of $33.66 with no receipt, under the limit: flagged under 1.3. |
