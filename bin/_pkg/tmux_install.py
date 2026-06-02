"""Detect a package manager and build the tmux install command for the consent
prompt (spec §7). We never run sudo ourselves — Linux commands are shown for the
user to run; brew (macOS) needs no sudo."""

from __future__ import annotations

import shutil
from typing import Optional

_which = shutil.which

_LINUX_MANAGERS = [
    ("apt-get", "sudo apt-get install -y tmux"),
    ("dnf", "sudo dnf install -y tmux"),
    ("pacman", "sudo pacman -S --noconfirm tmux"),
    ("zypper", "sudo zypper install -y tmux"),
    ("apk", "sudo apk add tmux"),
]


def install_command(system: str) -> Optional[str]:
    if system == "Darwin":
        if _which("brew"):
            return "brew install tmux"
        if _which("port"):
            return "sudo port install tmux"
        return None
    if system == "Linux":
        for binname, cmd in _LINUX_MANAGERS:
            if _which(binname):
                return cmd
    return None
