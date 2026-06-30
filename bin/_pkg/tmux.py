"""Thin wrapper around the tmux CLI for the session-explorer's interaction layer.

A dedicated tmux server (socket `session-explorer`, via `-L`) isolates everything
from the user's personal tmux. Pure `build_*` argv builders and `parse_version`/
`build_config` carry the logic and are unit-tested; the executing wrappers at the
bottom are thin subprocess calls. Mirrors launcher.py's builder/launch split.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from typing import Callable, List, Optional

# The dedicated-server socket name. Overridable via SESSION_EXPLORER_TMUX_SOCKET
# so the test suite (and any second instance) can target a throwaway server and
# never touch a live `session-explorer` server — uninstall/quit issue a real
# `kill-server`, so a CLI subprocess in a test must NOT run against the real
# socket. Defaults to the real name; production never sets the env var.
SOCKET = os.environ.get("SESSION_EXPLORER_TMUX_SOCKET") or "session-explorer"
VERSION_FLOOR = (3, 1)  # 3.1 adds `-l <n>%` sizing for join-pane (split dock)
EXPLORER_WINDOW = "explorer"
DOCK_PCT = 65  # claude pane width when docked beside the explorer tree
PROBE_WINDOW = "se-usage-probe"  # hidden window for the usage-bar scrape


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


def build_new_session_window(sid: str, cwd: str, name: str,
                             worktree: "str | None" = None,
                             err_path: "str | None" = None) -> List[str]:
    """new-window argv for starting a *fresh* claude session (not a resume).

    The window command is one shell string tmux runs via /bin/sh -c, so the
    name (which carries spaces and '/') is composed with shlex so it can never
    be re-split or injected. `--session-id <sid>` forces a known UUID up front,
    so the window name (`-n <sid>`) matches the real session id and all the
    existing resume/live machinery applies unchanged. `claude -n` writes the
    custom-title; `claude -w` owns worktree creation. `worktree` is None for no
    worktree, "" for a bare `-w` (claude auto-names), or a name for `-w <name>`.
    An empty `name` omits `-n`, starting an unnamed (temporary) session that
    stays hidden by default and is reaped by `--gc`.

    When `err_path` is given, claude's stderr is redirected to that file so a
    startup failure (e.g. `git worktree add` collision under `-w`) is captured
    even though the window closes when claude exits. The redirect is appended
    after shlex.join so the `2>` operator is not quoted; the path is quoted.
    """
    inner = ["exec", "claude", "--session-id", sid]
    if name:
        inner += ["-n", name]
    if worktree is not None:
        inner.append("-w")
        if worktree:
            inner.append(worktree)
    cmd = shlex.join(inner)
    if err_path:
        cmd += f" 2>{shlex.quote(err_path)}"
    return build_base() + [
        "new-window", "-d", "-n", sid, "-c", cwd, cmd]


def build_set_label(sid: str, label: str) -> List[str]:
    """Store a human-readable label on the window as a custom option
    (`@se_label`); the window name stays the sid for unique targeting. Kept as
    session metadata — the split-pane layout no longer renders window tabs, so
    it is not shown in the status bar."""
    return build_base() + ["set-option", "-w", "-t", sid, "@se_label", label]


def build_set_remain_on_exit(pane_id: str) -> List[str]:
    """set-option argv marking `pane_id` remain-on-exit=failed: a pane whose
    process exits non-zero (a TUI crash) is kept on screen — traceback visible,
    window not ceded to the docked claude pane — while a clean exit still
    closes it. 'failed' needs tmux >= 3.2; on older tmux the call fails and the
    caller ignores it (pre-3.2 behaviour is unchanged)."""
    return build_base() + ["set-option", "-p", "-t", pane_id,
                           "remain-on-exit", "failed"]


def build_select_window(target: str) -> List[str]:
    return build_base() + ["select-window", "-t", target]


def build_dock(sid: str, pct: int = DOCK_PCT, focus: bool = True) -> List[str]:
    """Join the background window `sid` into the explorer window as a right-hand
    pane. `-h` makes the split horizontal (side by side); the joined (claude)
    pane lands on the right at ~`pct`% width. Size is `-l <n>%`: `join-pane` has
    no `-p` flag (that belongs to `split-window` and is gone from modern tmux —
    `-p` yields "size missing"). The `%` suffix on `-l` needs tmux ≥ 3.1, which
    is why VERSION_FLOOR is 3.1. `focus=False` adds `-d` so the joined pane is
    not selected — the explorer keeps focus (cursor-follow sync)."""
    argv = ["join-pane", "-h", "-l", f"{pct}%", "-s", sid, "-t", EXPLORER_WINDOW]
    if not focus:
        argv.insert(1, "-d")              # join-pane -d: don't select the pane
    return build_base() + argv


def build_undock(pane_id: str, sid: str) -> List[str]:
    """Break the docked claude pane back out into its own background window
    (named `sid` so reconciliation finds it). `-d` keeps it off-screen."""
    return build_base() + ["break-pane", "-d", "-s", pane_id, "-n", sid]


def build_list_panes() -> List[str]:
    """Pane ids in the explorer window — its own pane plus the docked claude
    pane (target is hard-coded; the explorer is the only multi-pane window)."""
    return build_base() + [
        "list-panes", "-t", EXPLORER_WINDOW, "-F", "#{pane_id}"]


def build_select_pane(pane_id: str) -> List[str]:
    return build_base() + ["select-pane", "-t", pane_id]


def build_capture(target: str) -> List[str]:
    # -e keeps colour as escape sequences (rendered via Text.from_ansi);
    # -p prints the visible pane (alt-screen content for a full-screen app).
    return build_base() + ["capture-pane", "-ep", "-t", target]


def build_probe_window(cwd: str, window: str = PROBE_WINDOW) -> List[str]:
    """Detached hidden window running a throwaway claude for the /usage scrape.
    SESSION_EXPLORER_PROBE=1 is set on the claude process so the SessionStart /
    lifecycle hooks bail out and leave no index/registry trace."""
    return build_base() + [
        "new-window", "-d", "-n", window, "-c", cwd,
        "SESSION_EXPLORER_PROBE=1 exec claude"]


def build_send_keys(target: str, *keys: str) -> List[str]:
    return build_base() + ["send-keys", "-t", target, *keys]


def build_capture_plain(target: str) -> List[str]:
    """Plain (no -e) capture: easier to regex than colour-escaped output."""
    return build_base() + ["capture-pane", "-p", "-t", target]


def build_set_status_left(text: str) -> List[str]:
    # tmux runs status-left through strftime: a literal '%' (and the next char)
    # is consumed as a date format, so "31% used" renders as "31". Double it.
    return build_base() + [
        "set-option", "-g", "status-left", text.replace("%", "%%")]


def build_list_windows() -> List[str]:
    return build_base() + ["list-windows", "-F", "#{window_name}"]


def build_kill_window(target: str) -> List[str]:
    return build_base() + ["kill-window", "-t", target]


def build_rename_window(target: str, new_name: str) -> List[str]:
    return build_base() + ["rename-window", "-t", target, new_name]


# --- explorer-window self-heal (recover a claude-swallowed explorer) ----------

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def explorer_window_has_tui(pane_cmds) -> bool:
    """True if any pane command is our Textual TUI (python). A window named
    EXPLORER_WINDOW with no such pane has been swallowed by a docked claude —
    its own TUI pane closed and the claude pane is all that's left."""
    for c in pane_cmds:
        cl = (c or "").lower()
        if cl.startswith("python") or "session-explorer" in cl:
            return True
    return False


def sid_from_claude_cmd(cmd) -> "str | None":
    """Extract a claude session id (UUID) from its process command line —
    `--session-id <uuid>` or `--resume=<uuid>`. None when absent."""
    m = _UUID_RE.search(cmd or "")
    return m.group(0) if m else None


def heal_explorer_impostors(*, list_windows=None, panes_of=None,
                            cmd_of_pid=None, rename=None):
    """Recover a claude-swallowed explorer window. When a window named
    EXPLORER_WINDOW has no live TUI pane (only claude pane(s)), the explorer's
    own pane closed and a docked claude took the window over — yet because the
    window is still *named* 'explorer', the launcher's recreate step is fooled
    into thinking a live explorer exists, so a re-/open never rebuilds the TUI.

    Rename such a window to its claude session id (so it rejoins the
    background-session windows and the tree can map it) — or to a unique
    non-'explorer' fallback when no id is derivable. The launcher's recreate
    step then builds a fresh explorer window. Best-effort; all tmux/ps access
    is injected so the decision logic is unit-tested. Returns the (old, new)
    renames performed."""
    list_windows = _list_windows_fn() if list_windows is None else list_windows
    panes_of = _panes_of_window if panes_of is None else panes_of
    cmd_of_pid = _cmd_of_pid if cmd_of_pid is None else cmd_of_pid
    rename = rename_window if rename is None else rename
    renames = []
    for w in list_windows():
        if w != EXPLORER_WINDOW:
            continue
        panes = panes_of(w)
        if not panes or explorer_window_has_tui([c for c, _ in panes]):
            continue                      # empty/odd, or a healthy explorer
        first_pid = panes[0][1]
        sid = sid_from_claude_cmd(cmd_of_pid(first_pid))
        new_name = sid or f"orphan-{first_pid}"
        rename(w, new_name)
        renames.append((w, new_name))
    return renames


def reclaim_explorer_panes(self_pane, *, panes=None, cmd_of_pid=None,
                           break_pane=None):
    """Break every non-self pane out of the explorer window to its own
    background window (named by its claude session id; fallback `orphan-<pid>`),
    so a fresh or respawned TUI starts single-paned.

    A previous explorer process can leave a docked claude pane behind — a
    crash-respawn (the v1.17.4 self-heal) or a manual restart while a session
    was docked. The new process has no `_docked_sid` for that pane, so without
    this a later dock *stacks* a second pane instead of swapping it. Breaking
    leftovers out (they keep running, re-dockable from the tree) restores the
    one-pane-at-startup invariant.

    Safety floor: no-op unless `self_pane` is genuinely one of the window's
    panes — so a unit test with a fake `$TMUX_PANE` (or any process that isn't
    actually this server's explorer) can never break out a real server's panes.
    All tmux/ps access is injected so the decision logic is unit-tested.
    Returns the (pane_id, window_name) breakouts performed."""
    if not self_pane:
        return []
    panes = _panes_of_explorer if panes is None else panes
    cmd_of_pid = _cmd_of_pid if cmd_of_pid is None else cmd_of_pid
    break_pane = undock if break_pane is None else break_pane
    plist = panes()
    if self_pane not in [pid_pane for pid_pane, _ in plist]:
        return []
    done = []
    for pane_id, pid in plist:
        if pane_id == self_pane:
            continue
        sid = sid_from_claude_cmd(cmd_of_pid(pid)) or f"orphan-{pid}"
        break_pane(pane_id, sid)
        done.append((pane_id, sid))
    return done


def build_kill_server() -> List[str]:
    return build_base() + ["kill-server"]


def build_detach() -> List[str]:
    return build_base() + ["detach-client"]


def build_config(*, switch_key: str = "F9",
                 zoom_key: str = "F12", socket: str = SOCKET) -> str:
    """tmux config for the dedicated server. Self-contained; never touches the
    user's ~/.tmux.conf. The split-pane layout: the explorer is the left pane
    and the active claude session docks as a right pane. `switch_key` flips
    focus; `zoom_key` toggles fullscreen.

    Persist-by-default: there is NO client-detached hook. Detaching the client
    by any means (red-button/Cmd-W, crash, or the deliberate `x → b`) leaves the
    server — background sessions and the detached explorer — running. Only an
    explicit `x → s` ("shut down all") calls kill-server. The next `/open`
    reattaches via `new-session -A`.
    """
    hint = (f"#[fg=black,bg=green] {switch_key} ⇄ switch "
            f"· {zoom_key} ⤢ full #[default]")
    return "\n".join([
        "set -g mouse on",
        "set -g status on",
        'set -g status-left ""',
        "set -g status-left-length 40",
        'set -g window-status-format ""',
        'set -g window-status-current-format ""',
        f'set -g status-right "{hint}"',
        "set -g status-right-length 40",
        f"bind -n {switch_key} select-pane -t :.+",
        f"bind -n {zoom_key} resize-pane -Z",
        "",
    ])


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


def start_new_session_window(sid: str, cwd: str, name: str,
                             worktree: "str | None" = None,
                             label: "str | None" = None,
                             err_path: "str | None" = None) -> int:
    """Start a fresh session window; see build_new_session_window for the
    worktree tri-state and the err_path stderr-capture semantics."""
    rc = _call(build_new_session_window(sid, cwd, name, worktree, err_path))
    if label:
        _call(build_set_label(sid, label))
    return rc


def select_window(target: str) -> int:
    return _call(build_select_window(target))


def dock(sid: str, pct: int = DOCK_PCT, focus: bool = True) -> int:
    """Join the background window `sid` into the explorer window as the right
    pane. join-pane consumes the source window. With `focus=True` (Enter) it
    selects the joined pane so the user lands in claude; with `focus=False`
    (cursor-follow sync) it adds `-d` so focus stays in the explorer tree."""
    return _call(build_dock(sid, pct, focus))


def undock(pane_id: str, sid: str) -> int:
    return _call(build_undock(pane_id, sid))


def list_panes() -> List[str]:
    out = _capture(build_list_panes())
    return [ln for ln in out.splitlines() if ln]


def docked_pane(self_pane: "str | None",
                _list: Callable[[], List[str]] = list_panes) -> "str | None":
    """The id of the docked claude pane: the one pane in the explorer window
    that is NOT the explorer's own pane (`self_pane`, from $TMUX_PANE).
    Returns None when nothing is docked, or when `self_pane` is unknown — we
    can't tell our pane from claude's, so reporting None is safer than
    returning the first pane (which could be the explorer's own, and a caller
    would then break-pane/refocus the wrong one)."""
    if self_pane is None:
        return None
    for p in _list():
        if p != self_pane:
            return p
    return None


def select_pane(pane_id: str) -> int:
    return _call(build_select_pane(pane_id))


def capture_pane(target: str) -> str:
    return _capture(build_capture(target))


def start_probe_window(cwd: str, window: str = PROBE_WINDOW) -> int:
    return _call(build_probe_window(cwd, window))


def send_keys(target: str, *keys: str) -> int:
    return _call(build_send_keys(target, *keys))


def capture_plain(target: str) -> str:
    return _capture(build_capture_plain(target))


def set_status_left(text: str) -> int:
    return _call(build_set_status_left(text))


def set_remain_on_exit(pane_id: str) -> int:
    return _call(build_set_remain_on_exit(pane_id))


def list_windows() -> List[str]:
    out = _capture(build_list_windows())
    return [ln for ln in out.splitlines() if ln]


def session_windows(_list: Callable[[], List[str]] = list_windows) -> List[str]:
    return [w for w in _list() if w != EXPLORER_WINDOW]


def kill_window(target: str) -> int:
    return _call(build_kill_window(target))


def rename_window(target: str, new_name: str) -> int:
    return _call(build_rename_window(target, new_name))


def _list_windows_fn():
    """Indirection so heal_explorer_impostors can default to the module-level
    list_windows (defined above) without capturing it at def-time."""
    return list_windows


def _panes_of_window(window: str):
    """[(pane_current_command, pane_pid:int), ...] for `window`; [] on error.
    pane_current_command is a single token (e.g. 'Python', 'claude', a claude
    version like '2.1.196'), so a right-split cleanly separates it from pid."""
    out = _capture(build_base() + [
        "list-panes", "-t", window, "-F", "#{pane_current_command} #{pane_pid}"])
    panes = []
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.rsplit(" ", 1)
        cmd = parts[0]
        try:
            pid = int(parts[1]) if len(parts) == 2 else 0
        except ValueError:
            pid = 0
        panes.append((cmd, pid))
    return panes


def _panes_of_explorer():
    """[(pane_id, pane_pid:int), ...] for the explorer window; [] on error."""
    out = _capture(build_base() + [
        "list-panes", "-t", EXPLORER_WINDOW, "-F", "#{pane_id} #{pane_pid}"])
    res = []
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 2:
            try:
                pid = int(parts[1])
            except ValueError:
                pid = 0
            res.append((parts[0], pid))
    return res


def _cmd_of_pid(pid) -> str:
    """Full command line of `pid` via ps (to read claude's --session-id); ''
    on any error."""
    try:
        return subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def kill_server() -> int:
    return _call(build_kill_server())


def detach_client() -> int:
    return _call(build_detach())
