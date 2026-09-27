- source_spec: `_bmad-output/specs/spec-epic1/stories/1-seed-data-and-policy-engine.md`
  summary: The case's `load_seed` module and `test_load_seed.py` will clash with Saturday's planned root `load_seed.py` and `tests/test_load_seed.py` (clash in `sys.modules` and a pytest "import file mismatch" error).
  evidence: Unverified. Neither root file exists yet. Settle it by adding a root `load_seed.py` and `tests/test_load_seed.py`, then running `uv run pytest`. Possible fixes are renaming the case test modules or importing the case modules by path.
