"""OS-detecting terminal launcher.

M1 ships macOS only via osascript → Terminal.app. Linux launchers land in M2,
Windows in M5. The fallback path prints the absolute command to stdout
(consumed by the slash command's markdown response).
"""

from __future__ import annotations

import platform
import shlex
import subprocess
import sys


def build_macos_command(target_command: str) -> list[str]:
    """Build an osascript invocation that opens Terminal.app running `target_command`."""
    # AppleScript needs the inner command quoted with escaped double quotes.
    # `target_command` is the full shell command line to run in the new window.
    apple = f'tell application "Terminal" to do script "{target_command}"'
    return ["osascript", "-e", apple]


def launch(target_command: str) -> int:
    """Spawn a new terminal window running `target_command`. Returns 0 on success.

    On unsupported platforms, prints the command to stdout (for clipboard copy
    by the slash command) and returns a non-zero code.
    """
    system = platform.system()
    if system == "Darwin":
        cmd = build_macos_command(target_command)
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0

    # M2/M5: detect $TERMINAL, x-terminal-emulator, etc.
    print(f"Unsupported platform '{system}'. Run this in any terminal:\n  {target_command}")
    return 2
