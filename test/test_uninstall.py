"""Tests for _pkg.uninstall.teardown — reverse install-time side effects."""

import json
import os
import subprocess
from pathlib import Path

from _pkg import uninstall

_BIN = Path(__file__).resolve().parent.parent / "bin" / "session-explorer"


def _claude(tmp_path):
    """A populated ~/.claude-like dir: settings with our hook + cleanup override,
    a backup, operational sidecars, and the index/folder data files."""
    home = tmp_path
    claude = home / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({
        "cleanupPeriodDays": 36500,
        "theme": "dark",
        "hooks": {
            "SessionStart": [
                {"matchers": [], "command": "/x/session-explorer/hooks/session-start.sh"},
                {"matchers": [], "command": "/some/other/hook.sh"},
            ],
            "SessionEnd": [{"matchers": [], "command": "/keep/me.sh"}],
        },
    }))
    (claude / ".session-explorer.backup").write_text("30\n")
    (claude / ".session-explorer.current").write_text("sid-123")
    (claude / ".session-explorer.help-seen").write_text("")
    (claude / "session-explorer.log").write_text("log line\n")
    (claude / "session-explorer-index.json").write_text('{"version": 2, "sessions": {}}')
    (claude / "session-explorer-index.json.lock").write_text("")
    (claude / "session-explorer-folders.json").write_text('{"version": 1, "projects": {}}')
    # Symlink at $HOME/.local/bin/session-explorer (plain-install artifact).
    binroot = home / ".local" / "bin"
    binroot.mkdir(parents=True)
    target = home / "repo" / "bin" / "session-explorer"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n")
    os.symlink(str(target), str(binroot / "session-explorer"))
    return claude


def test_teardown_restores_cleanup_period_and_deletes_backup(tmp_path):
    claude = _claude(tmp_path)
    uninstall.teardown(claude_dir=str(claude))
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["cleanupPeriodDays"] == 30
    assert not (claude / ".session-explorer.backup").exists()


def test_teardown_removes_only_session_explorer_hook(tmp_path):
    claude = _claude(tmp_path)
    uninstall.teardown(claude_dir=str(claude))
    settings = json.loads((claude / "settings.json").read_text())
    starts = settings["hooks"]["SessionStart"]
    assert len(starts) == 1
    assert starts[0]["command"] == "/some/other/hook.sh"
    # Unrelated hook event and settings keys are untouched.
    assert settings["hooks"]["SessionEnd"] == [{"matchers": [], "command": "/keep/me.sh"}]
    assert settings["theme"] == "dark"


def test_teardown_removes_symlink(tmp_path):
    claude = _claude(tmp_path)
    link = tmp_path / ".local" / "bin" / "session-explorer"
    assert link.is_symlink()
    uninstall.teardown(claude_dir=str(claude))
    assert not link.exists() and not link.is_symlink()


def test_teardown_deletes_operational_sidecars(tmp_path):
    claude = _claude(tmp_path)
    uninstall.teardown(claude_dir=str(claude))
    for name in (".session-explorer.current", ".session-explorer.help-seen",
                 "session-explorer.log"):
        assert not (claude / name).exists(), name


def test_teardown_preserves_data_by_default(tmp_path):
    claude = _claude(tmp_path)
    uninstall.teardown(claude_dir=str(claude))
    assert (claude / "session-explorer-index.json").exists()
    assert (claude / "session-explorer-folders.json").exists()


def test_teardown_purge_deletes_index_and_folders(tmp_path):
    claude = _claude(tmp_path)
    uninstall.teardown(claude_dir=str(claude), purge_data=True)
    assert not (claude / "session-explorer-index.json").exists()
    assert not (claude / "session-explorer-index.json.lock").exists()
    assert not (claude / "session-explorer-folders.json").exists()


def test_teardown_idempotent_when_nothing_installed(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    # No settings, no backup, no sidecars — must not raise and must report nothing destructive.
    actions = uninstall.teardown(claude_dir=str(claude))
    assert isinstance(actions, list)
    # A second run is equally safe.
    uninstall.teardown(claude_dir=str(claude))


def test_uninstall_subcommand_restores_setting(tmp_path):
    """`session-explorer uninstall` runs teardown against $HOME/.claude and
    reminds the user to remove the plugin itself."""
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 36500}))
    (claude / ".session-explorer.backup").write_text("45\n")

    env = {**os.environ, "HOME": str(tmp_path)}
    proc = subprocess.run([str(_BIN), "uninstall"], capture_output=True, text=True, env=env)

    assert proc.returncode == 0, proc.stderr
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["cleanupPeriodDays"] == 45
    assert "plugin uninstall" in proc.stdout.lower()


def test_uninstall_subcommand_purge_flag(tmp_path):
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "session-explorer-index.json").write_text('{"version": 2, "sessions": {}}')
    env = {**os.environ, "HOME": str(tmp_path)}
    proc = subprocess.run([str(_BIN), "uninstall", "--purge"],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    assert not (claude / "session-explorer-index.json").exists()


def test_uninstall_sh_reverses_install_sh(tmp_path):
    """End-to-end: uninstall.sh undoes install.sh's symlink, hook, and setting."""
    repo = Path(__file__).resolve().parent.parent
    env = {**os.environ, "HOME": str(tmp_path)}
    subprocess.run(["bash", str(repo / "install.sh")],
                   capture_output=True, text=True, env=env, check=True)

    link = tmp_path / ".local" / "bin" / "session-explorer"
    settings_path = tmp_path / ".claude" / "settings.json"
    assert link.is_symlink()
    assert json.loads(settings_path.read_text())["cleanupPeriodDays"] == 36500

    proc = subprocess.run(["bash", str(repo / "uninstall.sh")],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr

    assert not link.is_symlink()
    settings = json.loads(settings_path.read_text())
    assert settings["cleanupPeriodDays"] == 30                     # restored from backup
    assert settings["hooks"]["SessionStart"] == []                 # our hook removed
    assert not (tmp_path / ".claude" / ".session-explorer.backup").exists()
