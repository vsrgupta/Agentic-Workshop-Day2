---
id: SPEC-epic3
companions: [../spec-expense-reviewer/mcp-tools.md]
sources: [../../planning-artifacts/epics.md]
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Epic 3: reviewer dashboard and payout gate

## Why

Epics 1 and 2 decide and explain every line item, but a finance reviewer still needs a terminal to use them. This epic gives Ana one dashboard. From it she starts reviews, reads the decisions, and releases the approved items over $500 that wait for a person's yes. It also closes the one recovery gap Epic 2 left open: a claim stuck in `reviewing`. This is Epic 3 of 3 in the expense case; the parent spec is `spec-expense-reviewer`.

## Capabilities

- **CAP-1**
  - **intent:** The finance reviewer sees every claim with its state, and starts a review with one click.
  - **success:**
    - The claims list shows all 40 claims and their state.
    - **Review** on a `waiting` claim, shown as **Retry** on an `incomplete` one, starts Epic 2's review in the background. The claim shows `reviewing`, and the page stays usable.
    - A second click on a `reviewing` claim starts nothing.
    - When the run ends, the claim shows `complete` or `incomplete`.

- **CAP-2**
  - **intent:** The finance reviewer reads every decision on a claim.
  - **success:** For each line item, the claim view shows:
    - the decision, clause, explanation and payout status
    - a visible marker when `agent_disagrees` is set

- **CAP-3**
  - **intent:** The finance reviewer releases approved items over $500. Nothing else can.
  - **success:**
    - The approval queue lists every `pending_approval` item.
    - **Release** sets that row to `released`, with `released_by` set to `APPROVER_NAME` and `released_at` set to the current UTC time.
    - Release only works on `pending_approval` rows.
    - Release is refused, with a message, when `APPROVER_NAME` isn't set.

- **CAP-4**
  - **intent:** The finance reviewer sees every flag and why it was raised, so they know what to follow up outside the tool.
  - **success:** The flags view lists every `flag` decision, with its clause, or reason for bad input, and the engine's facts. The view is read-only.

- **CAP-5**
  - **intent:** The finance reviewer can re-review a claim to replace its unreleased decisions.
  - **success:**
    - **Re-review** on a `complete` or `incomplete` claim deletes that claim's decisions that aren't `released`, then runs the review again under the one-run-per-claim rule.
    - Released rows are byte-for-byte unchanged afterwards.

- **CAP-6**
  - **intent:** The finance reviewer can recover a claim stuck in `reviewing` after a hard kill.
  - **success:**
    - **Unstick** appears only on `reviewing` claims.
    - Before it acts, it warns that a live run may still be going.
    - It moves the claim to `incomplete`, so **Retry** works.
    - It changes no decisions.

## Constraints

- **Stack:**
  - The dashboard is Streamlit, added with `uv add streamlit`.
  - Release, Re-review and Unstick are plain functions that can be tested without Streamlit.
- **Human actions only:** Release, Re-review and Unstick are human actions in dashboard code, never agent tools. The agent's four MCP tools stay unchanged.
- **Reuse:** reviews go through Epic 2's `review_claim`. The dashboard never decides, and never writes a decision itself.
- **Approver:** `APPROVER_NAME` comes from `.env`. It is the only approver identity, and the dashboard displays it.
- **Read-only and secrets:** read-only case files and Saturday's code stay untouched. Never commit `.env`, `app.db` or `mlflow.db`.
- **Cut order if time runs short:** the flags view (CAP-4) is cut first.

## Non-goals

- Paying anyone. Release changes a status and moves no money.
- Resolving flags, which is deferred to v2.
- Eval scores in the dashboard. They stay in the MLflow UI.
- Logins, multiple approvers, hosting or deployment.
- Emailing employees.

## Success signal

At the demo:
- The 10 holdout claims are reviewed with the **Review** button and end `complete`.
- The 5 approved items over $500 wait in the approval queue.
- Releasing one stamps `APPROVER_NAME` and the time.
- **Re-review** on a claim with a released item leaves that item released.
- **Unstick** recovers a claim forced into `reviewing`.

## Assumptions

- The entry point is `uv run streamlit run cases/expense/dashboard.py`.
- A background review runs in a worker thread with `asyncio.run(review_claim(...))`, and the page refreshes to show the state.
- `released_at` is a UTC ISO-8601 timestamp.
- Re-review sets the claim back to `waiting` after clearing its decisions, then starts the review.
