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

import json

import pytest


@pytest.fixture
def index_path(tmp_path):
    """Per-test index in an isolated directory (shared across TUI test modules).

    The folder store is derived as a sibling of the index, so co-locating the
    index inside the unique tmp_path keeps the folder store test-isolated too.
    """
    path = str(tmp_path / "se-index.json")
    json.dump({
        "version": 1, "folders": [],
        "sessions": {
            "sid-1": {
                "project_label": "demo",
                "project_path": "/tmp/demo-project",
                "name_cached": "planning/sprint14",
                "last_active_at": "2026-05-27T10:00:00Z",
                "tokens_estimate": 12345,
                "tokens_window_pct": 6,
                "message_count": 18,
                "first_prompt": "hello",
            }
        }
    }, open(path, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    yield path
