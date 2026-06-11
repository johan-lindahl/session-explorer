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


def test_linux_uses_TERMINAL_env(monkeypatch):
    monkeypatch.setenv("SESSION_EXPLORER_DRY_RUN", "1")
    monkeypatch.setenv("TERMINAL", "kitty")
    monkeypatch.setattr("platform.system", lambda: "Linux")
    cmd = launcher.build_linux_command("echo hi", which=lambda x: "/usr/bin/kitty" if x == "kitty" else None)
    assert cmd[0].endswith("kitty")


def test_linux_falls_through_emulator_list(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    found = {"gnome-terminal": "/usr/bin/gnome-terminal"}
    cmd = launcher.build_linux_command("echo hi", which=found.get)
    assert cmd[0].endswith("gnome-terminal")


def test_linux_no_emulator_returns_None(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert launcher.build_linux_command("echo hi", which=lambda _: None) is None


# --- WSL support ---

def test_is_wsl_true_via_env(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert launcher._is_wsl() is True


def test_is_wsl_true_via_proc_version(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    proc = tmp_path / "version"
    proc.write_text("Linux version 5.15.0-microsoft-standard-WSL2 (...)")
    assert launcher._is_wsl(proc_version_path=str(proc)) is True


def test_is_wsl_false_on_plain_linux(monkeypatch, tmp_path):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    proc = tmp_path / "version"
    proc.write_text("Linux version 6.1.0-generic (buildd@host)")
    assert launcher._is_wsl(proc_version_path=str(proc)) is False


def test_is_wsl_false_when_proc_missing(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    assert launcher._is_wsl(proc_version_path="/no/such/file") is False


def test_build_wsl_command_uses_wt_and_distro(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    cmd = launcher.build_wsl_command(
        "exec '/abs/session-explorer' tui",
        which=lambda x: "/mnt/c/.../wt.exe" if x == "wt.exe" else None,
    )
    assert cmd is not None
    assert cmd[0] == "wt.exe"
    # Re-enters the same distro via wsl.exe and runs the command under bash -lc.
    assert "wsl.exe" in cmd
    assert "-d" in cmd and "Ubuntu" in cmd
    assert cmd[-3:] == ["bash", "-lc", "exec '/abs/session-explorer' tui"]


def test_build_wsl_command_omits_distro_flag_when_unset(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    cmd = launcher.build_wsl_command(
        "echo hi", which=lambda x: "/wt.exe" if x == "wt.exe" else None
    )
    assert cmd is not None
    assert "-d" not in cmd


def test_build_wsl_command_none_without_wt(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert launcher.build_wsl_command("echo hi", which=lambda _: None) is None


def test_launch_uses_wsl_fallback_when_no_linux_terminal(monkeypatch):
    """Inside WSL with no Linux GUI terminal, launch() spawns via wt.exe."""
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.delenv("SESSION_EXPLORER_DRY_RUN", raising=False)
    monkeypatch.setattr(launcher, "build_linux_command", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_is_wsl", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "build_wsl_command",
                        lambda *a, **k: ["wt.exe", "wsl.exe", "--", "bash", "-lc", "x"])
    with mock.patch("subprocess.Popen") as popen:
        rc = launcher.launch("/abs/path/session-explorer")
        assert rc == 0
        assert popen.called
        assert popen.call_args[0][0][0] == "wt.exe"


def test_launch_falls_back_to_print_when_wsl_has_no_wt(monkeypatch, capsys):
    """WSL but no wt.exe -> the printed-command fallback (still usable)."""
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.delenv("SESSION_EXPLORER_DRY_RUN", raising=False)
    monkeypatch.setattr(launcher, "build_linux_command", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "_is_wsl", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "build_wsl_command", lambda *a, **k: None)
    rc = launcher.launch("/abs/path/session-explorer")
    captured = capsys.readouterr()
    assert rc != 0
    assert "/abs/path/session-explorer" in captured.out


from _pkg import launcher as _launcher


def test_wrap_in_tmux_builds_dedicated_session():
    cmd = _launcher.wrap_in_tmux("exec /abs/session-explorer tui",
                                 config_path="/tmp/se.conf")
    # The attach command itself (the respawn-dead-pane prefix may precede it).
    assert "tmux -L session-explorer -f /tmp/se.conf new-session" in cmd
    assert "new-session -A -s explorer -n explorer" in cmd
    assert "exec /abs/session-explorer tui" in cmd
    # The explorer marks itself so the TUI knows it is tmux-hosted:
    assert "SESSION_EXPLORER_TMUX=1" in cmd


def test_wrap_in_tmux_respawns_dead_explorer_pane():
    """A crashed TUI leaves a dead pane (remain-on-exit=failed); re-/open must
    revive it via respawn-pane before attaching, or the user reattaches to a
    dead explorer."""
    cmd = _launcher.wrap_in_tmux("exec /abs/session-explorer tui",
                                 config_path="/tmp/se.conf")
    respawn, attach = cmd.split("new-session", 1)
    assert "respawn-pane" in respawn          # heal BEFORE attaching
    assert "pane_dead" in respawn             # only dead panes are respawned
    assert "-A -s explorer" in attach         # attach still intact
