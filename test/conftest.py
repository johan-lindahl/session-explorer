"""Add bin/ to sys.path so test files can `from _pkg import ...`."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_REPO_ROOT, "bin")
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)

# Suppress the first-launch tmux-install offer across the whole suite. It fires
# whenever tmux is absent (e.g. CI runners), which would pop an unexpected modal
# over every TUI test. The dedicated offer test opts back in via monkeypatch.
os.environ.setdefault("SESSION_EXPLORER_TMUX_NO_OFFER", "1")
