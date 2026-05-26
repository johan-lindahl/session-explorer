"""Add bin/ to sys.path so test files can `from _pkg import ...`."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_REPO_ROOT, "bin")
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
