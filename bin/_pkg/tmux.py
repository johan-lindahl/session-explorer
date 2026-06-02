"""Thin wrapper around the tmux CLI for the session-explorer's interaction layer.

A dedicated tmux server (socket `session-explorer`, via `-L`) isolates everything
from the user's personal tmux. Pure `build_*` argv builders and `parse_version`/
`build_config` carry the logic and are unit-tested; the executing wrappers at the
bottom are thin subprocess calls. Mirrors launcher.py's builder/launch split.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Callable, List, Optional

SOCKET = "session-explorer"
VERSION_FLOOR = (3, 0)
EXPLORER_WINDOW = "explorer"


def parse_version(text: str) -> Optional[tuple]:
    """Parse `tmux -V` output (e.g. 'tmux 3.4', 'tmux 3.2a', 'tmux next-3.5')."""
    m = re.search(r"(\d+)\.(\d+)", text or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def meets_floor(version: Optional[tuple]) -> bool:
    return bool(version) and version >= VERSION_FLOOR


def available(which: Callable[[str], Optional[str]] = shutil.which) -> bool:
    return which("tmux") is not None


def build_base() -> List[str]:
    return ["tmux", "-L", SOCKET]


def build_start_window(sid: str, cwd: str) -> List[str]:
    # The window command is one shell string tmux runs via /bin/sh -c.
    # `exec` replaces the shell so closing the window kills claude directly.
    # `--resume=<sid>` binds the id to the option (injection-safe; see
    # tui._resume_argv for the rationale).
    return build_base() + [
        "new-window", "-d", "-n", sid, "-c", cwd, f"exec claude --resume={sid}"]


def build_set_label(sid: str, label: str) -> List[str]:
    """Store a human-readable label on the window (window name stays the sid for
    unique targeting). The status bar renders `@se_label` instead of the raw
    sid — see build_config's window-status-format."""
    return build_base() + ["set-option", "-w", "-t", sid, "@se_label", label]


def build_select_window(target: str) -> List[str]:
    return build_base() + ["select-window", "-t", target]


def build_capture(target: str) -> List[str]:
    # -e keeps colour as escape sequences (rendered via Text.from_ansi);
    # -p prints the visible pane (alt-screen content for a full-screen app).
    return build_base() + ["capture-pane", "-ep", "-t", target]


def build_list_windows() -> List[str]:
    return build_base() + ["list-windows", "-F", "#{window_name}"]


def build_kill_window(target: str) -> List[str]:
    return build_base() + ["kill-window", "-t", target]


def build_kill_server() -> List[str]:
    return build_base() + ["kill-server"]


def build_detach() -> List[str]:
    return build_base() + ["detach-client"]


def build_config(*, persist_flag_path: str, back_key: str = "F12",
                 socket: str = SOCKET) -> str:
    """tmux config for the dedicated server. Self-contained; never touches the
    user's ~/.tmux.conf. The client-detached hook implements Option C: an abrupt
    window close (no persist-flag) kills the server; a deliberate detach that
    first touched the flag is left to persist (spec §5)."""
    detach_hook = (
        f"set-hook -g client-detached "
        f"'run-shell -b \"if [ ! -f {persist_flag_path} ]; then "
        f"tmux -L {socket} kill-server; fi\"'"
    )
    # Show the human label (@se_label) in the status bar, falling back to the
    # window name (#W) for the explorer window which has none. Without this the
    # bar shows raw session-id UUIDs.
    win_fmt = " #I:#{?#{@se_label},#{@se_label},#W} "
    return "\n".join([
        "set -g mouse on",
        "set -g status on",
        f'set -g window-status-format "{win_fmt}"',
        f'set -g window-status-current-format "{win_fmt}"',
        # No `remain-on-exit`: when claude exits, its window closes and tmux
        # drops the user back into the explorer (window 0). Avoids dead [exited]
        # panes lingering and being mistaken for running sessions.
        f"bind -n {back_key} select-window -t {EXPLORER_WINDOW}",
        detach_hook,
        "",
    ])


# --- persist-flag helpers ---

def set_persist_flag(path: str) -> None:
    with open(path, "a"):
        os.utime(path, None)


def clear_persist_flag(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def persist_flag_set(path: str) -> bool:
    return os.path.exists(path)


# --- thin executing wrappers (not unit-tested; covered by spikes + TUI tests) ---

def _call(argv: List[str]) -> int:
    return subprocess.run(argv, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


def _capture(argv: List[str]) -> str:
    return subprocess.run(argv, capture_output=True, text=True).stdout


def detected_version() -> Optional[tuple]:
    if not available():
        return None
    return parse_version(_capture(["tmux", "-V"]))


def start_window(sid: str, cwd: str, label: "str | None" = None) -> int:
    rc = _call(build_start_window(sid, cwd))
    if label:
        _call(build_set_label(sid, label))
    return rc


def select_window(target: str) -> int:
    return _call(build_select_window(target))


def capture_pane(target: str) -> str:
    return _capture(build_capture(target))


def list_windows() -> List[str]:
    out = _capture(build_list_windows())
    return [ln for ln in out.splitlines() if ln]


def session_windows(_list: Callable[[], List[str]] = list_windows) -> List[str]:
    return [w for w in _list() if w != EXPLORER_WINDOW]


def kill_window(target: str) -> int:
    return _call(build_kill_window(target))


def kill_server() -> int:
    return _call(build_kill_server())


def detach_client() -> int:
    return _call(build_detach())
