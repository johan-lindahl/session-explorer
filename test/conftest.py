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


@pytest.fixture(autouse=True)
def _protect_live_tmux(monkeypatch):
    """Hard isolation from the maintainer's LIVE `session-explorer` tmux server
    (their own explorer + running claude sessions). `uninstall`/quit issue a
    real `tmux kill-server`, so a test that reaches one MUST NOT hit the real
    socket. Two defenses, covering both process boundaries:

    (1) SUBPROCESS: `test_cli`/`test_uninstall` run the real `session-explorer`
        CLI in a child process where in-process monkeypatching does NOT apply.
        Point the socket at a throwaway via env, inherited by every child — so
        `session-explorer uninstall`'s kill-server targets a dead throwaway
        socket, never the live one.
    (2) IN-PROCESS: block `_call`/`_capture` from executing ANY command on the
        live socket (the in-process SOCKET stays "session-explorer" so the
        build_* argv assertions still pass). Tests that need tmux behaviour stub
        the higher-level wrapper, so blocking the raw exec is invisible to them.

    CI has no live server, so both are harmless there."""
    monkeypatch.setenv("SESSION_EXPLORER_TMUX_SOCKET", "se-pytest-throwaway")
    try:
        from _pkg import tmux as _t
    except Exception:
        return
    _real_call, _real_capture = _t._call, _t._capture

    def _guard_call(argv):
        return 0 if "session-explorer" in argv else _real_call(argv)

    def _guard_capture(argv):
        return "" if "session-explorer" in argv else _real_capture(argv)

    monkeypatch.setattr(_t, "_call", _guard_call)
    monkeypatch.setattr(_t, "_capture", _guard_capture)


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
