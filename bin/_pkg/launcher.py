"""OS-detecting terminal launcher.

macOS uses osascript → Terminal.app; Linux probes known terminal emulators.
Windows is supported via WSL: inside WSL `platform.system()` reports "Linux",
so the Linux path runs first; when no Linux GUI terminal is present (the common
WSL case), launch() opens a Windows Terminal window that re-enters the same
distro and runs the TUI. The fallback path prints the absolute command to
stdout (consumed by the slash command's markdown response) so the user can run
it by hand if no launcher is found.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess


def build_macos_command(target_command: str) -> list[str]:
    """Build an osascript invocation that opens Terminal.app running `target_command`."""
    # AppleScript needs the inner command quoted with escaped double quotes.
    # `target_command` is the full shell command line to run in the new window.
    apple = f'tell application "Terminal" to do script "{target_command}"'
    return ["osascript", "-e", apple]


_LINUX_CANDIDATES = [
    "x-terminal-emulator",
    "gnome-terminal",
    "konsole",
    "xfce4-terminal",
    "alacritty",
    "kitty",
    "wezterm",
]

# Per-emulator argv shape to pass through a single shell command.
_LINUX_ARGV = {
    "gnome-terminal": lambda binp, cmd: [binp, "--", "bash", "-lc", cmd],
    "konsole":         lambda binp, cmd: [binp, "-e", "bash", "-lc", cmd],
    "xfce4-terminal":  lambda binp, cmd: [binp, "-e", f"bash -lc {cmd!r}"],
    "alacritty":       lambda binp, cmd: [binp, "-e", "bash", "-lc", cmd],
    "kitty":           lambda binp, cmd: [binp, "bash", "-lc", cmd],
    "wezterm":         lambda binp, cmd: [binp, "start", "--", "bash", "-lc", cmd],
    "x-terminal-emulator": lambda binp, cmd: [binp, "-e", "bash", "-lc", cmd],
}


def build_linux_command(target_command: str, which=shutil.which) -> "list[str] | None":
    # 1. $TERMINAL wins if it resolves.
    env_term = os.environ.get("TERMINAL")
    if env_term:
        binp = which(env_term)
        if binp:
            argv_fn = _LINUX_ARGV.get(os.path.basename(env_term),
                                      lambda b, c: [b, "-e", "bash", "-lc", c])
            return argv_fn(binp, target_command)

    # 2. Probe known emulators in order.
    for name in _LINUX_CANDIDATES:
        binp = which(name)
        if binp:
            argv_fn = _LINUX_ARGV.get(name, lambda b, c: [b, "-e", "bash", "-lc", c])
            return argv_fn(binp, target_command)

    return None


def _is_wsl(proc_version_path: str = "/proc/version") -> bool:
    """True when running inside the Windows Subsystem for Linux.

    WSL sets WSL_DISTRO_NAME, and the kernel version string carries "microsoft"
    (both WSL1 and WSL2). Either signal is sufficient.
    """
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open(proc_version_path, "r", encoding="utf-8", errors="ignore") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def build_wsl_command(target_command: str, which=shutil.which) -> "list[str] | None":
    """Open a new Windows Terminal window that re-enters this WSL distro and
    runs `target_command` under bash. Returns None when `wt.exe` isn't on PATH
    (Windows executables are reachable from WSL via interop), so the caller can
    fall back to printing the command.

    `wt.exe` accepts a command line to run; we point it at `wsl.exe -d <distro>
    -- bash -lc <cmd>` so the new window lands back in the same distro.
    """
    if not which("wt.exe"):
        return None
    distro = os.environ.get("WSL_DISTRO_NAME", "")
    distro_flag = ["-d", distro] if distro else []
    return ["wt.exe", "wsl.exe", *distro_flag, "--", "bash", "-lc", target_command]


def launch(target_command: str) -> int:
    """Spawn a new terminal window running `target_command`. Returns 0 on success.

    On unsupported platforms, prints the command to stdout (for clipboard copy
    by the slash command) and returns a non-zero code.
    """
    if os.environ.get("SESSION_EXPLORER_DRY_RUN") == "1":
        print(f"DRY RUN: would launch: {target_command}")
        return 0

    system = platform.system()
    if system == "Darwin":
        cmd = build_macos_command(target_command)
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0

    if system == "Linux":
        cmd = build_linux_command(target_command)
        if cmd is not None:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return 0
        # WSL with no Linux GUI terminal: open a Windows Terminal window that
        # re-enters this distro. Best-effort — falls through to the printed
        # command below when wt.exe isn't available.
        if _is_wsl():
            wsl_cmd = build_wsl_command(target_command)
            if wsl_cmd is not None:
                subprocess.Popen(wsl_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return 0

    print(f"Unsupported platform '{system}'. Run this in any terminal:\n  {target_command}")
    return 2


def wrap_in_tmux(target_command: str, config_path: str,
                 socket: str = "session-explorer") -> str:
    """Wrap the explorer launch in a dedicated-server tmux session.

    `-A` attaches to an existing `explorer` session (so a re-`/open` reattaches
    to still-running sessions); `-n explorer` names window 0 so list-windows can
    distinguish it from session windows. SESSION_EXPLORER_TMUX=1 tells the TUI it
    is tmux-hosted and may use the interaction layer."""
    inner = f"SESSION_EXPLORER_TMUX=1 {target_command}"
    return (f"tmux -L {socket} -f {shlex.quote(config_path)} "
            f"new-session -A -s explorer -n explorer {shlex.quote(inner)}")
