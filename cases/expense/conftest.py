"""Put cases/expense on sys.path so the case tests import its modules directly."""

import sys
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parent
if str(CASE_DIR) not in sys.path:
    sys.path.insert(0, str(CASE_DIR))


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls a real model; skipped when no API key is set")


def pytest_collection_modifyitems(config, items):
    """Live tests are opt-in: they run only with `-m live` (or a markexpr naming live) or EXPENSE_LIVE=1."""
    import os

    import pytest

    markexpr = config.getoption("markexpr", "") or ""
    if "live" in markexpr or os.environ.get("EXPENSE_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: run with -m live or EXPENSE_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
