"""OS-detecting terminal launcher.

M1 ships macOS only via osascript → Terminal.app. Linux launchers land in M2,
Windows in M5. The fallback path prints the absolute command to stdout
(consumed by the slash command's markdown response).
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys


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

    print(f"Unsupported platform '{system}'. Run this in any terminal:\n  {target_command}")
    return 2
