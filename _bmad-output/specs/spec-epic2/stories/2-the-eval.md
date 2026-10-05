---
title: 'The eval'
type: 'feature'
created: '2026-09-27'
status: 'done'
baseline_commit: 'd976ac4b9e2334a50c2d38f17a8efddc807b3e6c'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/cases/expense/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing yet proves the agent is accurate and consistent, which is the whole point of the case.

**Approach:**
- `cases/expense/run_eval.py` runs `review_claim` over the 30 labelled claims. It does this twice, each pass on its own fresh temporary database.
- It scores those passes against `labelled.csv` and the engine's facts.
- A Groq judge rates each explanation's clarity.
- The results go into one MLflow run.

## Boundaries & Constraints

**Always:**
- **Labels:** read directly from `cases/expense/eval/labelled.csv`. The claims under test are that file's distinct `claim_id`s. The 10 holdout claims are never run.
- **Temporary databases:** each pass gets a new one from `load_seed.build`, and `app.db` is never touched.
- **Scoring code:** a pure function, `score(labels, pass1, pass2, facts) -> dict`, computes these, each a fraction from 0 to 1:
  - **`decision_accuracy`:** pass 1 decisions against the labels.
  - **`clause_accuracy`:** pass 1 clauses against the labels.
  - **`claim_total_match`:** the share of claims whose reimbursable total (the sum of approved amounts, in integer cents) matches the total from the labels.
  - **`consistency`:** 1.0 only if both passes recorded every labelled line with identical decisions and clauses. Otherwise it's the share of lines where they agree.
  - **`faithfulness`:** the share of pass 1 explanations that pass `review.check_explanation`, using `get_claim`-shaped facts from `policy_engine`.
  - **`lines_recorded`:** how many lines pass 1 recorded.
- **Judge:**
  - It uses `ChatGroq` with `JUDGE_MODEL` (default `openai/gpt-oss-120b`) and temperature 0, with structured output `{clear: bool}` for each pass 1 explanation.
  - `judge_clarity` is the fraction rated clear. It's reported, but it isn't a gate.
- **MLflow:** `sqlite:///mlflow.db`, experiment `expense-reviewer`, and one run holding the metrics, the parameters (provider, model, judge model) and a per-line table from `log_table`. Agent traces use `autolog`.
- **Exit code:** non-zero unless decision accuracy, clause accuracy, claim-total match, consistency and faithfulness are all 1.0.
- **Output:** a short console summary of the metrics.

**Decisions (human, at planning):**
- **Rate limits:**
  - A rate-limit error (HTTP 429, or the provider's rate-limit exception) is retried with exponential backoff, at most 4 attempts per claim, with a short pause between claims. This covers both a claim's agent run and each judge call.
  - Before a claim is retried, it's reset to `incomplete` so it can start again. A retry skips lines already recorded.
- **`--claims CL-2001,CL-2002`:** limits the eval to that subset of labelled claims, and the metrics cover only the subset. An ID that isn't labelled is an error.

**Never:**
- Moving the labels into an MLflow dataset.
- Writing to `eval/`.
- Touching `app.db`.
- Changing `review.py`, `mcp_server.py` or `policy_engine.py`.
- A real model in unit tests. The tests use a fake agent model and a fake judge.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| All correct | both passes match labels, faithful, identical | every gated metric 1.0; exit 0 | N/A |
| Wrong decision | one line differs from label | decision_accuracy < 1; exit non-zero | N/A |
| Wrong total | an approve line missing in pass 1 | claim_total_match < 1 | N/A |
| Passes differ | pass 2 clause differs on one line | consistency < 1 | N/A |
| Unfaithful explanation | explanation missing the limit | faithfulness < 1 | N/A |
| Incomplete claim | review raises / ends incomplete for a claim | unrecorded lines count as misses; run continues | error noted per claim |
| No judge key | `GROQ_API_KEY` unset | judge_clarity not logged; note printed | gated metrics still computed |
| Rate limited | agent or judge raises a 429 twice, then succeeds | claim completes; metrics as normal | backoff retries; after 4 attempts the claim counts as incomplete |
| Subset | `--claims CL-2016` | only that claim run and scored | unknown or unlabelled ID → exit non-zero before any run |
| Isolation | any run | `app.db` bytes unchanged; no files in `eval/` | N/A |

</frozen-after-approval>

## Code Map

- `cases/expense/review.py`:
  - reuse `review_claim(claim_id, db_path, model)`, which returns a status and lines holding `decision`, `clause` and `explanation`
  - reuse `make_model()` for the agent
  - reuse `check_explanation(explanation, line)`, which expects `get_claim`'s line shape: `amount_cents`, `limit_cents` and `clause`
  - read-only for this story
- `cases/expense/policy_engine.py` -- `connect`, `decide_claim` and `Result.facts` give the `get_claim`-shaped facts for the faithfulness check, and `reimbursable_total_cents`. Read-only.
- `cases/expense/load_seed.py` -- `build(path)` makes each pass's temporary database.
- `cases/expense/run_claim.py` -- the MLflow setup to copy: `TRACKING_URI`, `EXPERIMENT`, `load_dotenv` and `autolog`.
- `cases/expense/tests/test_review.py` -- reuse its `FakeToolModel` scripting pattern for the fake agent runs.
- `cases/expense/conftest.py` -- the `live` marker is opt-in; any real-model eval test must be marked `live`.

## Tasks & Acceptance

**Execution:**
- [x] `cases/expense/run_eval.py` -- `score()`, the judge, the two-pass runner with 429 backoff, MLflow logging, and the CLI `main()` with `--claims` -- CAP-4
- [x] `cases/expense/tests/test_eval.py` -- every matrix row: `score()` unit tests, plus one end-to-end run on 2 claims with the fake agent model and fake judge against a temporary MLflow URI -- CAP-4

**Acceptance Criteria:**
- Given the labelled set, when `uv run python cases/expense/run_eval.py` runs with working keys, then one MLflow run appears in the experiment `expense-reviewer` with all the metrics and a per-line table.
- Given no keys, when `uv run pytest` runs, then everything passes, the only skips are `live` tests, and the 113 existing tests still pass.

## Implementation Notes

- **Files:**
  - `cases/expense/run_eval.py`: `score`, `is_rate_limit`, the judge, the two-pass runner, MLflow logging, and `main` with `--claims`.
  - `cases/expense/tests/test_eval.py`: 31 tests. The whole suite now shows 144 passed and 1 skipped (the live test).
- **Additions beyond the spec:**
  - the metrics `claims_incomplete_pass1` and `claims_incomplete_pass2`
  - an `errors.txt` artifact, logged only when a claim ends incomplete
- **Rules the code follows:**
  - Rate-limit detection also matches "429" or "rate limit" in the exception text.
  - An unlabelled ID in `--claims` exits with code 2 before any run.
- **Side effect:** an early test run, before the tests were isolated, created the repo's `mlflow.db`. The file is gitignored. The 16 fake traces it received were deleted, so it is empty (the Default experiment, 0 runs, 0 traces).
- **Not verified:** a real eval run. The Gemini quota is used up and `GROQ_API_KEY` isn't set.

- **Review patches (pass 1):**
  - A claim-total match now requires every line of the claim to be recorded.
  - One `line_results` function feeds both `score()` and the per-line table.
  - The logged provider and model come from the model that actually ran, and the `claims` param is logged.
  - The judge prompt's fence is neutralised against explanation text.
  - Six verification tests added.
  - The suite now shows 151 passed and 1 skipped (the live test).

- **Follow-up fix (found in the first real Groq run):**
  - **Problem:** on 2 of 4 explanations, the judge on `openai/gpt-oss-120b` answered in plain text ("clear=true") instead of calling the tool. Groq rejected those calls (400 `tool_use_failed`), so they were never rated.
  - **Fix:** the judge now uses `with_structured_output(Clarity, method="json_schema")`, and the prompt no longer suggests literal `clear=true` text. A regression test was added.
  - **Result:** the smoke run on CL-2016 with Groq rated 4 of 4, every gated metric was 1.0, and exit was 0. The suite shows 152 passed and 1 skipped.

## Spec Change Log

## Review Triage Log

Review pass 1. Reviewer codes: BH = Blind Hunter, EC = Edge Case Hunter, VG = Verification Gap.

| # | Finding | Verdict | Evidence / route |
|---|---|---|---|
| BH1 | `claim_total_match` counts an unreviewed claim with no approved lines as a match | medium | CL-2017 and CL-2027 have no approve labels, so both totals are $0 even when nothing was recorded. A gated metric is inflated → **patch** (require every line of the claim to be recorded) |
| BH2 | The claim total doesn't reuse `reimbursable_total_cents` | low | Same arithmetic, and it will be folded into the BH3 refactor → part of **patch** |
| BH3 | `per_line_table` recomputes the per-line scoring, with different clause normalisation | medium | The table can disagree with the logged metrics → **patch** (one per-line function feeding both) |
| BH9 | The logged provider and model are copied defaults, and the `--claims` subset isn't logged | low | The fix is direct: take them from the built model, and log the subset → **patch** |
| BH10 / EC8 | The judge prompt's `>>>` delimiter can be spoofed by explanation text | low | The text derives from untrusted claim data (AGENTS.md rule). A one-line escape → **patch** |
| VG1 | Pass 2 reusing pass 1's database isn't detected | medium | With a shared database, consistency comes out 1.0 and all 31 tests pass → **patch** |
| VG2 | The pause between claims isn't verified | medium | Removing the sleep still passes → **patch** |
| VG3 | Nothing tests that the judge doesn't retry other errors | medium | Dropping `is_rate_limit` from the judge's condition still passes → **patch** |
| VG4 | The `judge_clarity` value and which pass it rates aren't verified | medium | A constant 1.0, or rating pass 2 instead, still passes → **patch** |
| VG5 | A claim ending incomplete without raising, `claims_incomplete_pass2` and `errors.txt` are untested | medium | Three mutations each still pass → **patch** |
| VG6 | The per-line table and params are checked for shape only | medium | Forcing `decision_ok`/`consistent` to True, or setting a wrong model, still passes → **patch** |
| BH4 | An incomplete result that doesn't raise isn't retried | false | By design: only rate limits are retried (a human decision), and the spec expects the run to note the claim and continue |
| BH5 | `_reset_incomplete` moves a `waiting` claim to `incomplete` | low | Harmless, since `incomplete` is a valid start state → rejected |
| BH6 | Rate-limit text matching can misfire | low | The worst case is 4 wasted attempts → rejected |
| BH7 / EC1 / EC2 | A label/seed mismatch raises `KeyError` late | low | The labels and seed are read-only and consistent → rejected |
| BH8 | Some failures give a raw traceback, not a defined exit code | low | Unlikely → rejected |
| BH11 | Test gaps: the full default run, the whole holdout range, the judge's 429 end to end, autolog | low | Partly covered by VG5 and VG6; the rest is minor → rejected |
| BH12 | A mutable-default cache in the test helpers | low | Test-only → rejected |
| EC3 / EC4 | A CSV with a BOM, or duplicate label rows | low | The label file is read-only and clean → rejected |
| EC5 / EC6 | A Ctrl-C, or `_reset_incomplete` raising, aborts the eval | low | That's the expected response to an interrupt, and the reset failing is unlikely → rejected |
| EC7 / EC9 | A parse failure in the judge, and the clarity denominator | low | The judge isn't a gate. Failures are printed, and the rate is taken over the explanations actually rated → rejected |
| EC10 | A retry re-runs the agent on lines already recorded | false | By design: `already_decided` makes it idempotent, as the planning decision says |
| VG-o | `autolog` untested; the exception-group walk and `_reset_incomplete` are unused defensive code | low | → rejected |

## Verification

**Commands:**
- `uv run pytest` -- expected: all tests pass, and the only skips are `live` tests.
- `git status --short` -- expected: changes only in `cases/expense/`, with no `app.db` or `mlflow.db`.
