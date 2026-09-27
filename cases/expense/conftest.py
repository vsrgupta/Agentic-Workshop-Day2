"""Put cases/expense on sys.path so the case tests import its modules directly."""

import sys
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parent
if str(CASE_DIR) not in sys.path:
    sys.path.insert(0, str(CASE_DIR))
