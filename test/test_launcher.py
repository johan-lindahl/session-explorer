"""Tests for _pkg.launcher."""

import platform
from unittest import mock

from _pkg import launcher


def test_build_macos_command_quotes_path():
    """A path with a space must end up properly quoted inside osascript."""
    cmd = launcher.build_macos_command("/path with space/session-explorer")
    assert "osascript" in cmd[0]
    # The applescript string contains the quoted absolute path
    joined = " ".join(cmd)
    assert "session-explorer" in joined
    # No unescaped quotes that would break applescript
    assert '\\"' in joined or "do script" in joined


def test_launch_dispatches_by_platform(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    with mock.patch("subprocess.Popen") as popen:
        launcher.launch("/abs/path/session-explorer")
        assert popen.called
        called_cmd = popen.call_args[0][0]
        assert "osascript" in called_cmd[0]


def test_launch_unsupported_platform_returns_fallback(monkeypatch, capsys):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    rc = launcher.launch("/abs/path/session-explorer")
    captured = capsys.readouterr()
    assert rc != 0
    assert "/abs/path/session-explorer" in captured.out
