"""Tests for install.sh. Run with HOME redirected to a tmp dir so the user's
real $HOME is never touched."""

import json
import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INSTALLER = _REPO_ROOT / "install.sh"


def _run_install(home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        ["bash", str(_INSTALLER)],
        capture_output=True, text=True, env=env,
    )


def test_install_creates_symlink_and_settings(tmp_path):
    proc = _run_install(tmp_path)
    assert proc.returncode == 0, proc.stderr

    # Symlink exists and points at the repo binary
    link = tmp_path / ".local" / "bin" / "session-explorer"
    assert link.is_symlink()
    assert link.resolve() == (_REPO_ROOT / "bin" / "session-explorer").resolve()

    # Install registers the hook but does NOT touch cleanupPeriodDays (retention
    # is opt-in via the TUI's first-launch prompt) and writes no backup.
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "cleanupPeriodDays" not in settings
    assert not (tmp_path / ".claude" / ".session-explorer.backup").exists()

    # SessionStart hook entry registered with the absolute repo path
    hooks = settings["hooks"]["SessionStart"]
    assert len(hooks) == 1
    assert hooks[0]["matchers"] == []
    assert hooks[0]["command"].endswith("hooks/session-start.sh")


def test_install_idempotent(tmp_path):
    """Running install.sh twice should leave a single hook entry."""
    _run_install(tmp_path)
    proc2 = _run_install(tmp_path)
    assert proc2.returncode == 0

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert len(settings["hooks"]["SessionStart"]) == 1
    assert "cleanupPeriodDays" not in settings


def test_install_preserves_existing_unrelated_settings(tmp_path):
    """An existing settings.json with unrelated keys should keep them."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({
        "cleanupPeriodDays": 60,
        "theme": "dark",
        "hooks": {"SessionEnd": [{"matchers": [], "command": "/some/other"}]},
    }))

    proc = _run_install(tmp_path)
    assert proc.returncode == 0, proc.stderr

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["theme"] == "dark"
    assert settings["cleanupPeriodDays"] == 60  # untouched — retention is opt-in
    # Other hooks are preserved
    assert "SessionEnd" in settings["hooks"]
    # session-explorer hook is added to SessionStart
    assert len(settings["hooks"]["SessionStart"]) == 1
    # No backup written by install (only the opt-in prompt writes one)
    assert not (tmp_path / ".claude" / ".session-explorer.backup").exists()


def test_install_removes_old_session_explorer_hook_on_rerun(tmp_path):
    """If install.sh re-runs from a different repo path, the old hook entry is replaced."""
    # First install
    _run_install(tmp_path)
    settings_path = tmp_path / ".claude" / "settings.json"

    # Manually inject a "stale" old session-explorer hook from a previous install location
    data = json.loads(settings_path.read_text())
    data["hooks"]["SessionStart"].insert(0, {
        "matchers": [], "command": "/old/path/session-explorer/hooks/session-start.sh"
    })
    settings_path.write_text(json.dumps(data))
    assert len(json.loads(settings_path.read_text())["hooks"]["SessionStart"]) == 2

    # Re-install: should dedupe to a single entry pointing at the current repo
    proc = _run_install(tmp_path)
    assert proc.returncode == 0

    final = json.loads(settings_path.read_text())
    assert len(final["hooks"]["SessionStart"]) == 1
    assert "/old/path" not in final["hooks"]["SessionStart"][0]["command"]
