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

    # SessionStart hook entries registered with the absolute repo path:
    # session-start.sh (indexing) + session-live.sh (live-session dispatcher).
    hooks = settings["hooks"]
    ss_cmds = [h["command"] for h in hooks["SessionStart"]]
    assert any(c.endswith("hooks/session-start.sh") for c in ss_cmds), ss_cmds
    assert any(c.endswith("hooks/session-live.sh") for c in ss_cmds), ss_cmds
    assert all(h["matchers"] == [] for h in hooks["SessionStart"])

    # The live dispatcher is registered on the other lifecycle events too.
    for evt in ("UserPromptSubmit", "Stop", "Notification", "SessionEnd"):
        cmds = [h["command"] for h in hooks[evt]]
        assert any(c.endswith("hooks/session-live.sh") for c in cmds), (evt, cmds)
    # Notification carries the idle_prompt matcher.
    assert hooks["Notification"][0]["matchers"] == ["idle_prompt"]


def test_install_idempotent(tmp_path):
    """Running install.sh twice should leave a single hook entry."""
    _run_install(tmp_path)
    proc2 = _run_install(tmp_path)
    assert proc2.returncode == 0

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    hooks = settings["hooks"]
    # SessionStart keeps exactly the two session-explorer commands; the four
    # standalone events keep exactly one live entry each (no duplication).
    assert len(hooks["SessionStart"]) == 2
    for evt in ("UserPromptSubmit", "Stop", "Notification", "SessionEnd"):
        live = [h for h in hooks[evt] if h["command"].endswith("session-live.sh")]
        assert len(live) == 1, (evt, hooks[evt])
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
    # The user's unrelated SessionEnd hook is preserved alongside our live hook.
    assert "SessionEnd" in settings["hooks"]
    se_cmds = [h["command"] for h in settings["hooks"]["SessionEnd"]]
    assert "/some/other" in se_cmds, se_cmds
    assert any(c.endswith("session-live.sh") for c in se_cmds), se_cmds
    # session-explorer hooks are added to SessionStart (start + live)
    assert len(settings["hooks"]["SessionStart"]) == 2
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
    # start.sh + live.sh + the injected stale entry = 3
    assert len(json.loads(settings_path.read_text())["hooks"]["SessionStart"]) == 3

    # Re-install: should dedupe to the current repo's start.sh + live.sh.
    proc = _run_install(tmp_path)
    assert proc.returncode == 0

    final = json.loads(settings_path.read_text())["hooks"]["SessionStart"]
    assert len(final) == 2
    assert all("/old/path" not in h["command"] for h in final), final
