# Policy engine rules

How the deterministic engine (CAP-2) reads `POLICY.md`. `POLICY.md` is the authority; these rules settle readings it leaves open. A reference implementation of these rules matches all 119 labelled line items and all 30 claim totals.

## Clause priority

For each line item, the first rule that applies decides:

| Order | Clause | Rule | Decision |
|---|---|---|---|
| 1 | 3.1 / 3.2 / 3.3 | category `alcohol` / `personal` / `fine` | reject |
| 2 | 5.1 | duplicate of an earlier item (see below) | reject |
| 3 | 1.2 | item date more than 60 days before the claim's `submitted_at` | reject |
| 4 | 4.1 | category `software` or `equipment`: description contains `ITA-` followed by digits | approve, else reject |
| 5 | 2.1 / 2.2 / 2.3 / 6.1 | amount against the limit (see bands) | approve / flag / reject |
| 6 | 1.3 | amount over $25 and `has_receipt` ≠ `yes` | flag |
| 7 | category default | none of the above | approve under 2.1 meals, 2.2 hotel, 2.3 flight, 6.1 ground |

Step 6 applies only when step 5 approved: an over-limit item keeps its limit clause, not 1.3.

## Amounts

- **No rounding.** Compare to the cent, using integer cents or `Decimal`, never floats. The thresholds are exactly `POLICY.md`'s numbers.
- **Bands (2.4):**
  - total ≤ limit → approve
  - limit < total ≤ 1.2 × limit → flag
  - total > 1.2 × limit → reject
- **Other thresholds:**
  - 1.3 applies to an amount **over** $25.
  - 1.2 applies to an item **more than** 60 days old.
  - The payout gate applies to an approve **over** $500.

## Limits

- **Lookup key:** `(employee level, line item city, category)` from `seed/limits.csv`. Use the **item's** city, never the employee's home city.
- **Compared amount:**
  - hotel (2.2): the line amount; one line is one night.
  - flight (2.3): the line amount; one line is one trip.
  - meals (2.1) and ground (6.1): the **day total**.
- **Day total:**
  - **What it sums:** the employee's items in that category on that date. It includes items rejected by an earlier rule, such as duplicates and stale items.
  - **Which claims it covers:** this claim and claims the same employee submitted **earlier**, never later ones.
  - **Every item that day** gets the day's band.
  - **Two-city day:** if the day's items span two cities, compare against the higher of those cities' limits.

## Duplicates (5.1)

- **Key:** same employee, date, merchant and amount.
- **Scope:** the item is checked against the employee's other items in the same claim and in claims they submitted earlier.
- **Earlier:** the claim with the earlier `submitted_at`. On a tie, the lower claim ID. Within one claim, the earlier row in `seed/line_items.csv`.
- **Outcome:** the earliest item stands and is decided normally; each later match is rejected under 5.1.

## Order independence

Day totals and duplicates are computed from line items, never from recorded decisions. Reviewing claims in any order gives the same answers, and a later claim never changes a recorded decision.

## Bad input

Flag the item with a stated reason, and never approve it, when any of these hold:
- the category is unknown
- no limit row exists for it
- the amount is zero or negative

An unknown claim ID is an error and writes nothing.

## Reference cases from the labels

| Line | Shows |
|---|---|
| L-3062 / L-3063 | Duplicate meal: L-3063 is rejected under 5.1, but still counts toward the day total, so L-3062 is flagged under 2.1. |
| L-3102 / L-3103 | Same pattern: the day total pushes L-3102 to reject under 2.1. |
| L-3082 / L-3083 | Two meals on one day, both rejected under 2.1 on the day total. |
| L-3055 / L-3051 | Figma licence with an ITA- code is approved under 4.1; the one without is rejected under 4.1. |
| L-3075 | Ground item of $33.66 with no receipt, under the limit: flagged under 1.3. |
