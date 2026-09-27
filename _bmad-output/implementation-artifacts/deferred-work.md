- source_spec: `_bmad-output/specs/spec-epic1/stories/1-seed-data-and-policy-engine.md`
  summary: The case's `load_seed` module and `test_load_seed.py` will clash with Saturday's planned root `load_seed.py` and `tests/test_load_seed.py` (clash in `sys.modules` and a pytest "import file mismatch" error).
  evidence: Unverified. Neither root file exists yet. Settle it by adding a root `load_seed.py` and `tests/test_load_seed.py`, then running `uv run pytest`. Possible fixes are renaming the case test modules or importing the case modules by path.
- source_spec: `_bmad-output/specs/spec-epic1/stories/2-mcp-server-with-guarded-record-decision.md`
  summary: Add a subprocess smoke test that starts `cases/expense/mcp_server.py` over stdio by file path and lists its four tools, without the `EXPENSE_DB` override.
  evidence: Unverified by tests (it was checked by hand with a scratch stdio client). Would be medium, because Epic 2's agent launches the server this way. An integration test through the agent's MCP client would settle it.
- source_spec: `_bmad-output/specs/spec-epic2/stories/1-the-reviewing-agent.md`
  summary: Recover a claim stuck in `reviewing` after a hard kill. It needs to be handled in Epic 3, through Re-review or an unstick action.
  evidence: The one-run-per-claim guard blocks Retry while the state is `reviewing`. The human chose to leave recovery to Epic 3 (planning decision).
- source_spec: `_bmad-output/specs/spec-epic2/stories/1-the-reviewing-agent.md`
  summary: Add a real race test for `_start`: two threads or processes behind a barrier each try to start the same claim, and exactly one wins.
  evidence: Unverified. The current tests call `_start` one after another, so they can't catch a non-atomic regression. The code is correct by inspection (a single conditional UPDATE). Would be medium.
