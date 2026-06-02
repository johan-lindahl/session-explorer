# tmux-backed Session Interaction & Live Monitoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make resuming a session non-destructive — the explorer stays alive as tmux window 0, each resumed session runs as a background tmux window you can flip into, peek at via a live preview snapshot, and shut down deliberately on exit.

**Architecture:** A dedicated tmux server (`-L session-explorer`) hosts the explorer (window 0) and one window per resumed session. The TUI shells out to the tmux CLI to start/select/capture windows. Live status reuses the existing `live.py` registry; the preview gains a snapshot (tmux `capture-pane` for our windows, JSONL transcript-tail otherwise). tmux is an optional enhancement with a clean no-tmux fallback to today's `execvp` resume.

**Tech Stack:** Python 3.11+, vendored Textual, tmux ≥3.0 (optional, external), pytest + pytest-asyncio, bats.

**Spec:** `docs/superpowers/specs/2026-06-02-tmux-session-interaction-design.md` — read it first.

---

## File Structure

**New files**
- `bin/_pkg/tmux.py` — thin tmux CLI wrapper. Pure `build_*` argv builders + `parse_version` + `build_config`, plus thin executing wrappers. Mirrors `launcher.py`'s "pure builder + thin launch" split.
- `bin/_pkg/snapshot.py` — `transcript_tail()` (pure, JSONL→activity text) and `snapshot()` dispatch (capture vs tail, capture injected).
- `test/test_tmux.py`, `test/test_snapshot.py` — unit tests for the above.

**Modified files**
- `bin/_pkg/launcher.py` — wrap the launch command in the tmux invocation when tmux is available.
- `bin/_pkg/cli.py` — pass tmux availability into the launch path.
- `bin/_pkg/tui.py` — context-aware `action_resume`; preview snapshot + poll; quit-guard modal; reconciliation; `_tmux_enabled` flag.
- `bin/_pkg/uninstall.py` — remove generated config + persist/declined markers.
- `test/test_tui.py`, `test/test_launcher.py` — extended.
- `SPEC.md`, `CLAUDE.md` — new load-bearing decisions.

**Conventions to follow**
- tmux socket constant `SOCKET = "session-explorer"`; every command is prefixed `["tmux", "-L", SOCKET, ...]`.
- Split pure argv `build_*` functions (unit-tested) from thin executing wrappers (not unit-tested) — exactly like `launcher.build_macos_command` vs `launcher.launch`.
- Marker/sidecar paths are derived as siblings of the index dir, via `os.path.dirname(self._index_path)` (see `tui._claude_dir`).
- Tests: `async def` + `app.run_test()` pilot for the TUI; monkeypatch the `tmux` module's executing wrappers (never run real tmux in CI).

---

## SPIKES (do first — they gate the build)

These are exploratory validations from spec §9. They are **not** TDD. Each has a pass/fail gate and a documented fallback. Record outcomes in the spec's Status line (date + machine), matching how the active-session-indicator spec recorded its PID spike.

### Spike A: tmux mouse vs Textual mouse

- [ ] **A1: Launch the explorer under a throwaway tmux server with mouse on**

```bash
printf 'set -g mouse on\nset -g status on\n' > /tmp/se-spike.conf
tmux -L se-spike -f /tmp/se-spike.conf new-session 'python3 -c "from _pkg.tui import run; run()"'
# (run from the repo's bin/ dir so _pkg imports, or: PYTHONPATH=bin python3 -m _pkg.cli tui)
```

- [ ] **A2: Verify in the running explorer**
  - Clicking a tree row highlights it (mouse reaches Textual).
  - Scrolling the tree works.
  - Clicking the tmux status bar (bottom) switches windows (mouse reaches tmux).

**Gate:** tree clicks reach Textual AND status-bar clicks reach tmux → **mouse-on is viable; proceed.** If tree clicks are swallowed by tmux → **set mouse off in the generated config; rely on F12 + arrow-key navigation only** (clickable tabs become keyboard-only window switching via the back-key + a forward-key). Note the outcome in the spec.

- [ ] **A3: Tear down**: `tmux -L se-spike kill-server`

### Spike B: repaint after flip-back

- [ ] **B1:** With the Spike-A server running, open a second window and flip between them:

```bash
tmux -L se-spike new-window 'claude --resume=<some-real-session-id>'   # or: 'top'
tmux -L se-spike select-window -t 0    # back to explorer
```

- [ ] **B2:** Confirm the explorer renders correctly immediately on return (no blank/garbled frame), including after resizing the terminal while window 1 was focused.

**Gate:** explorer repaints cleanly → proceed. If it shows a stale/blank frame → add an explicit refresh: a `client-session-changed`/`window-pane-changed` tmux hook that sends `refresh-client`, or call `self.refresh(layout=True)` on a short post-flip timer. Note the chosen remedy.

### Spike C: client-detached self-kill hook + persist-flag

- [ ] **C1:** Build a minimal config exercising Option C:

```bash
FLAG=/tmp/se-persist.flag; rm -f "$FLAG"
cat > /tmp/se-spikeC.conf <<EOF
set -g mouse on
set-hook -g client-detached 'run-shell -b "if [ ! -f $FLAG ]; then tmux -L se-spikeC kill-server; fi"'
EOF
tmux -L se-spikeC -f /tmp/se-spikeC.conf new-session 'top'
```

- [ ] **C2: Abrupt-close path** — from another terminal, detach the client with the flag ABSENT and confirm the server dies:

```bash
rm -f /tmp/se-persist.flag
tmux -L se-spikeC detach-client          # simulates red-button close (SIGHUP→detach)
tmux -L se-spikeC list-sessions ; echo "exit=$?"   # expect: error + non-zero (server gone)
```

- [ ] **C3: Persist path** — relaunch, set the flag, detach, confirm the server survives:

```bash
tmux -L se-spikeC -f /tmp/se-spikeC.conf new-session -d 'top'
touch /tmp/se-persist.flag
tmux -L se-spikeC detach-client
tmux -L se-spikeC list-sessions ; echo "exit=$?"   # expect: lists session + zero (server alive)
tmux -L se-spikeC kill-server
```

**Gate:** C2 kills the server AND C3 keeps it alive → **Option C confirmed; proceed.** If the self-kill hook is unreliable (server won't die from its own hook, or timing races) → **fall back to Option B**: drop the persist-flag and the "leave running in background" quit option, and set `destroy-unattached on` in the generated config (Task 3 and Task 11 change accordingly). Record which option won in the spec.

---

## Task 1: `tmux.py` — availability & version gate

**Files:**
- Create: `bin/_pkg/tmux.py`
- Test: `test/test_tmux.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_tmux.py
from _pkg import tmux


def test_parse_version_extracts_major_minor():
    assert tmux.parse_version("tmux 3.4") == (3, 4)
    assert tmux.parse_version("tmux 3.2a") == (3, 2)
    assert tmux.parse_version("tmux next-3.5") == (3, 5)


def test_parse_version_returns_none_on_garbage():
    assert tmux.parse_version("not tmux") is None
    assert tmux.parse_version("") is None


def test_meets_floor():
    assert tmux.meets_floor((3, 0)) is True
    assert tmux.meets_floor((3, 4)) is True
    assert tmux.meets_floor((2, 9)) is False


def test_available_false_when_not_on_path():
    assert tmux.available(which=lambda _: None) is False


def test_available_true_when_on_path():
    assert tmux.available(which=lambda _: "/usr/bin/tmux") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.tmux'`

- [ ] **Step 3: Write minimal implementation**

```python
# bin/_pkg/tmux.py
"""Thin wrapper around the tmux CLI for the session-explorer's interaction layer.

A dedicated tmux server (socket `session-explorer`, via `-L`) isolates everything
from the user's personal tmux. Pure `build_*` argv builders and `parse_version`/
`build_config` carry the logic and are unit-tested; the executing wrappers at the
bottom are thin subprocess calls. Mirrors launcher.py's builder/launch split.
"""

from __future__ import annotations

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): availability + version gate"
```

---

## Task 2: `tmux.py` — command argv builders

**Files:**
- Modify: `bin/_pkg/tmux.py`
- Test: `test/test_tmux.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_tmux.py  (append)
def test_build_base_uses_dedicated_socket():
    assert tmux.build_base() == ["tmux", "-L", "session-explorer"]


def test_build_start_window():
    argv = tmux.build_start_window("sid-123", "/proj")
    assert argv == [
        "tmux", "-L", "session-explorer", "new-window", "-d",
        "-n", "sid-123", "-c", "/proj", "exec claude --resume=sid-123",
    ]


def test_build_select_window():
    assert tmux.build_select_window("sid-123") == [
        "tmux", "-L", "session-explorer", "select-window", "-t", "sid-123"]


def test_build_capture():
    assert tmux.build_capture("sid-123") == [
        "tmux", "-L", "session-explorer", "capture-pane", "-ep", "-t", "sid-123"]


def test_build_list_windows():
    assert tmux.build_list_windows() == [
        "tmux", "-L", "session-explorer", "list-windows", "-F", "#{window_name}"]


def test_build_kill_window_and_server_and_detach():
    assert tmux.build_kill_window("sid-9")[-2:] == ["-t", "sid-9"]
    assert tmux.build_kill_server()[-1] == "kill-server"
    assert tmux.build_detach()[-1] == "detach-client"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: FAIL — `AttributeError: module '_pkg.tmux' has no attribute 'build_base'`

- [ ] **Step 3: Write minimal implementation** (append to `tmux.py`)

```python
def build_base() -> List[str]:
    return ["tmux", "-L", SOCKET]


def build_start_window(sid: str, cwd: str) -> List[str]:
    # The window command is one shell string tmux runs via /bin/sh -c.
    # `exec` replaces the shell so closing the window kills claude directly.
    # `--resume=<sid>` binds the id to the option (injection-safe; see
    # tui._resume_argv for the rationale).
    return build_base() + [
        "new-window", "-d", "-n", sid, "-c", cwd, f"exec claude --resume={sid}"]


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): command argv builders"
```

---

## Task 3: `tmux.py` — generated config

**Files:**
- Modify: `bin/_pkg/tmux.py`
- Test: `test/test_tmux.py`

> If Spike C selected **Option B**, replace the `client-detached` hook line below with `set -g destroy-unattached on` and drop the `persist_flag_path` parameter.

- [ ] **Step 1: Write the failing test**

```python
# test/test_tmux.py  (append)
def test_build_config_contains_core_settings():
    conf = tmux.build_config(persist_flag_path="/tmp/se.flag", back_key="F12")
    assert "set -g mouse on" in conf
    assert "set -g status on" in conf
    assert "set -g remain-on-exit on" in conf
    # Back-to-explorer key (no-prefix root binding):
    assert "bind -n F12 select-window -t explorer" in conf
    # Option C: kill the server on detach unless the persist-flag is present.
    assert "client-detached" in conf
    assert "/tmp/se.flag" in conf
    assert "kill-server" in conf


def test_build_config_respects_custom_back_key():
    conf = tmux.build_config(persist_flag_path="/tmp/f", back_key="C-g")
    assert "bind -n C-g select-window -t explorer" in conf
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: FAIL — `AttributeError: ... 'build_config'`

- [ ] **Step 3: Write minimal implementation** (append to `tmux.py`)

```python
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
    return "\n".join([
        "set -g mouse on",
        "set -g status on",
        "set -g remain-on-exit on",
        f"bind -n {back_key} select-window -t {EXPLORER_WINDOW}",
        detach_hook,
        "",
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): generated server config with Option-C detach hook"
```

---

## Task 4: `tmux.py` — persist-flag + thin executing wrappers

**Files:**
- Modify: `bin/_pkg/tmux.py`
- Test: `test/test_tmux.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_tmux.py  (append)
def test_persist_flag_set_clear_check(tmp_path):
    flag = str(tmp_path / "persist.flag")
    assert tmux.persist_flag_set(flag) is False
    tmux.set_persist_flag(flag)
    assert tmux.persist_flag_set(flag) is True
    tmux.clear_persist_flag(flag)
    assert tmux.persist_flag_set(flag) is False
    tmux.clear_persist_flag(flag)  # idempotent, no raise


def test_session_windows_excludes_explorer():
    # list_windows is the executing wrapper; patch the captured output.
    assert tmux.session_windows(
        _list=lambda: ["explorer", "sid-1", "sid-2"]) == ["sid-1", "sid-2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: FAIL — `AttributeError: ... 'persist_flag_set'`

- [ ] **Step 3: Write minimal implementation** (append to `tmux.py`)

```python
import os


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


def start_window(sid: str, cwd: str) -> int:
    return _call(build_start_window(sid, cwd))


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): persist-flag helpers + executing wrappers"
```

---

## Task 5: `snapshot.py` — transcript tail

**Files:**
- Create: `bin/_pkg/snapshot.py`
- Test: `test/test_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_snapshot.py
import json
from _pkg import snapshot


def _write_jsonl(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_transcript_tail_renders_user_assistant_and_tools(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        {"type": "user", "message": {"content": "add retry to fetch"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Editing index.py"},
            {"type": "tool_use", "name": "Edit"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash"}]}},
    ])
    out = snapshot.transcript_tail(str(p), limit=10)
    assert "you: add retry to fetch" in out
    assert "claude: Editing index.py" in out
    assert "tool: Edit" in out
    assert "tool: Bash" in out


def test_transcript_tail_keeps_only_last_n(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [{"type": "user", "message": {"content": f"msg{i}"}}
                     for i in range(20)])
    out = snapshot.transcript_tail(str(p), limit=3)
    assert "msg19" in out and "msg0" not in out
    assert len(out.splitlines()) == 3


def test_transcript_tail_missing_file_is_empty(tmp_path):
    assert snapshot.transcript_tail(str(tmp_path / "nope.jsonl")) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_snapshot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.snapshot'`

- [ ] **Step 3: Write minimal implementation**

```python
# bin/_pkg/snapshot.py
"""Read-only progress snapshot for a session's preview pane.

Two sources (spec §3): an explorer-launched tmux window is captured live via
tmux capture-pane (handled at the call site, ANSI); any other live session is
summarised here by tailing its JSONL transcript into a compact activity view.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from . import jsonl as _jsonl


def _line_for(msg: dict) -> Optional[str]:
    t = msg.get("type")
    content = msg.get("message", {}).get("content")
    if t == "user":
        if isinstance(content, str):
            return f"you: {content.strip()[:80]}"
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    return f"you: {item.get('text', '').strip()[:80]}"
        return None
    if t == "assistant" and isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text", "").strip():
                return f"claude: {item['text'].strip()[:80]}"
            if item.get("type") == "tool_use":
                return f"tool: {item.get('name', '?')}"
    return None


def transcript_tail(path: str, limit: int = 12) -> str:
    """Last `limit` human-meaningful activity lines from the JSONL, or ''."""
    lines: List[str] = []
    for msg in _jsonl._iter_messages(path):
        ln = _line_for(msg)
        if ln:
            lines.append(ln)
    return "\n".join(lines[-limit:])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_snapshot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/snapshot.py test/test_snapshot.py
git commit -m "feat(snapshot): JSONL transcript tail"
```

---

## Task 6: `snapshot.py` — source dispatch

**Files:**
- Modify: `bin/_pkg/snapshot.py`
- Test: `test/test_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_snapshot.py  (append)
def test_snapshot_uses_capture_for_tmux_window():
    text, is_ansi = snapshot.snapshot(
        sid="sid-1", transcript_path="/x.jsonl",
        tmux_window_names=["sid-1", "sid-2"],
        capture_fn=lambda s: f"CAPTURED:{s}",
        tail_fn=lambda p, limit=12: "TAIL")
    assert text == "CAPTURED:sid-1"
    assert is_ansi is True


def test_snapshot_falls_back_to_tail_for_non_window():
    text, is_ansi = snapshot.snapshot(
        sid="sid-9", transcript_path="/x.jsonl",
        tmux_window_names=["sid-1"],
        capture_fn=lambda s: "CAPTURED",
        tail_fn=lambda p, limit=12: "TAIL")
    assert text == "TAIL"
    assert is_ansi is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_snapshot.py -q`
Expected: FAIL — `AttributeError: ... 'snapshot'`

- [ ] **Step 3: Write minimal implementation** (append to `snapshot.py`)

```python
def snapshot(*, sid: str, transcript_path: str,
             tmux_window_names: List[str],
             capture_fn: Callable[[str], str],
             tail_fn: Callable[..., str] = transcript_tail) -> Tuple[str, bool]:
    """Return (text, is_ansi). Capture-pane for our tmux windows (is_ansi=True);
    transcript tail for any other live session (is_ansi=False)."""
    if sid in tmux_window_names:
        return capture_fn(sid), True
    return tail_fn(transcript_path), False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_snapshot.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/snapshot.py test/test_snapshot.py
git commit -m "feat(snapshot): capture-vs-tail source dispatch"
```

---

## Task 7: `launcher.py` — wrap launch command in tmux

**Files:**
- Modify: `bin/_pkg/launcher.py`
- Test: `test/test_launcher.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_launcher.py  (append)
from _pkg import launcher as _launcher


def test_wrap_in_tmux_builds_dedicated_session(monkeypatch):
    cmd = _launcher.wrap_in_tmux("exec /abs/session-explorer tui",
                                 config_path="/tmp/se.conf")
    assert cmd.startswith("tmux -L session-explorer")
    assert "-f /tmp/se.conf" in cmd
    assert "new-session -A -s explorer -n explorer" in cmd
    assert "exec /abs/session-explorer tui" in cmd
    # The explorer marks itself so the TUI knows it is tmux-hosted:
    assert "SESSION_EXPLORER_TMUX=1" in cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_launcher.py -q`
Expected: FAIL — `AttributeError: ... 'wrap_in_tmux'`

- [ ] **Step 3: Write minimal implementation** (append to `launcher.py`)

```python
import shlex


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_launcher.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/launcher.py test/test_launcher.py
git commit -m "feat(launcher): tmux-wrapped launch command builder"
```

---

## Task 8: `cli.py` — write config & wrap launch when tmux is available

**Files:**
- Modify: `bin/_pkg/cli.py:181-187` (`_cmd_launch`)
- Test: `test/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_cli.py  (append)
def test_launch_wraps_in_tmux_when_available(monkeypatch):
    from _pkg import cli, tmux, launcher
    monkeypatch.setattr(tmux, "available", lambda which=None: True)
    monkeypatch.setattr(tmux, "detected_version", lambda: (3, 4))
    captured = {}
    monkeypatch.setattr(launcher, "launch", lambda cmd: captured.setdefault("cmd", cmd) or 0)
    monkeypatch.setenv("SESSION_EXPLORER_DRY_RUN", "1")
    cli._cmd_launch()
    assert "tmux -L session-explorer" in captured["cmd"]


def test_launch_plain_when_tmux_absent(monkeypatch):
    from _pkg import cli, tmux, launcher
    monkeypatch.setattr(tmux, "available", lambda which=None: False)
    captured = {}
    monkeypatch.setattr(launcher, "launch", lambda cmd: captured.setdefault("cmd", cmd) or 0)
    cli._cmd_launch()
    assert "tmux" not in captured["cmd"]
    assert captured["cmd"].startswith("exec ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_cli.py -q -k launch`
Expected: FAIL — launch is not wrapped / `tmux` attr errors.

- [ ] **Step 3: Write minimal implementation** — replace `_cmd_launch` in `cli.py`

```python
def _cmd_launch() -> int:
    import shlex as _shlex
    from . import tmux as _tmux
    bin_path = os.path.abspath(sys.argv[0])
    # `exec` so closing the TUI closes the spawned terminal window cleanly.
    target = f"exec {_shlex.quote(bin_path)} tui"
    if _tmux.available() and _tmux.meets_floor(_tmux.detected_version()):
        flag = os.path.expanduser("~/.claude/.session-explorer.tmux-persist")
        conf = os.path.expanduser("~/.claude/.session-explorer.tmux.conf")
        with open(conf, "w") as f:
            f.write(_tmux.build_config(persist_flag_path=flag))
        # Stale persist-flag from a prior run must not suppress the next
        # abrupt-close kill; clear it on every fresh launch.
        _tmux.clear_persist_flag(flag)
        target = _launcher.wrap_in_tmux(target, config_path=conf)
    return _launcher.launch(target)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_cli.py -q -k launch`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(cli): write tmux config and wrap launch when tmux present"
```

---

## Task 9: `tui.py` — context-aware, non-destructive Enter

**Files:**
- Modify: `bin/_pkg/tui.py` — add `_tmux_enabled`/helpers in `__init__`; rewrite `action_resume` (`:703-731`)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui.py  (append)
async def test_enter_starts_background_window_when_stopped(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: [])   # nothing running
    monkeypatch.setattr(tuimod._tmux, "start_window",
                        lambda sid, cwd: calls.setdefault("start", (sid, cwd)) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")            # project node
        await pilot.press("down")            # folder node
        await pilot.press("down")            # session leaf (sid-1)
        await pilot.press("enter")
        await pilot.pause()
    assert calls["start"][0] == "sid-1"      # started in the background
    assert app._resume_target is None        # did NOT exit-to-resume


async def test_enter_flips_into_running_window(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: ["sid-1"])
    monkeypatch.setattr(tuimod._tmux, "select_window",
                        lambda t: calls.setdefault("select", t) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")            # project node
        await pilot.press("down")            # folder node
        await pilot.press("down")            # session leaf (sid-1)
        await pilot.press("enter")
        await pilot.pause()
    assert calls["select"] == "sid-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k "background_window or flips_into"`
Expected: FAIL — Enter still exits (`_resume_target` set; `start`/`select` not called).

- [ ] **Step 3: Write minimal implementation**

First add the tmux import near the top of `tui.py` (with the other `from . import` lines):

```python
from . import tmux as _tmux
```

Add to `__init__` (after `self._row_nodes = {}` at `:478`):

```python
        # tmux-hosted interaction layer (spec §1). The launcher sets this env
        # var only when it wrapped the explorer in our dedicated tmux server.
        self._tmux_enabled: bool = os.environ.get("SESSION_EXPLORER_TMUX") == "1"
```

Replace `action_resume` (`:703-731`) with:

```python
    def action_resume(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        sid = node.data["sid"]
        project_path = node.data.get("project_path")

        # No tmux → today's behaviour: exit and execvp claude (handled in run()).
        if not self._tmux_enabled:
            self._exit_to_resume(sid, project_path)
            return

        running = _tmux.session_windows()
        if sid in running:
            _tmux.select_window(sid)                 # flip in to interact
            return
        if sid in self._live_states:
            # Live in another terminal, not one of our windows: never start a
            # second claude on the same transcript (spec §5).
            self.push_screen(ConfirmScreen(
                "This session is already running in another terminal.\n"
                "Showing its progress here; press space to peek. (y/esc)"))
            return
        # Stopped → start in a background tmux window, stay in the explorer.
        cwd = _resolve_resume_cwd(project_path) or os.path.expanduser("~")
        if _dead_worktree_repo(project_path):
            def after(ok: bool) -> None:
                if ok:
                    _tmux.start_window(sid, _resolve_resume_cwd(project_path) or cwd)
                    self._poll_live()
            self.push_screen(ConfirmScreen(
                "This session is from a deleted git worktree.\n"
                "Resume anyway? This re-creates an empty directory:\n"
                f"{project_path}"), after)
        else:
            _tmux.start_window(sid, cwd)
            self._poll_live()

    def _exit_to_resume(self, sid: str, project_path: "str | None") -> None:
        def proceed() -> None:
            self._resume_target = sid
            self._resume_cwd = project_path
            self.exit()
        if _dead_worktree_repo(project_path):
            self.push_screen(ConfirmScreen(
                "This session is from a deleted git worktree.\n"
                "Resume anyway? This re-creates an empty directory:\n"
                f"{project_path}"), lambda ok: proceed() if ok else None)
        else:
            proceed()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -q -k "background_window or flips_into or resume"`
Expected: PASS (and the existing `test_enter_sets_resume_target` still passes — it runs without the env var, so `_tmux_enabled` is False).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): context-aware non-destructive Enter via tmux"
```

---

## Task 10: `tui.py` — live snapshot in the preview pane

**Files:**
- Modify: `bin/_pkg/tui.py` — `_refresh_preview` (`:1241-1250`); add snapshot poll in `on_mount` (`:561-563`)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui.py  (append)
async def test_preview_shows_snapshot_for_live_session(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: ["sid-1"])
    monkeypatch.setattr(tuimod._tmux, "capture_pane", lambda s: "LIVE FRAME for " + s)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._live_states = {"sid-1": "working"}
        group = app._render_live_preview(
            {"sid": "sid-1", "transcript_path": "/x", "name_cached": "planning/sprint14"},
            "sid-1")
    # Group.renderables is a list of rich Text objects; check the captured frame
    # made it into the body.
    bodies = " ".join(r.plain for r in group.renderables if hasattr(r, "plain"))
    assert "LIVE FRAME for sid-1" in bodies
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k snapshot`
Expected: FAIL — preview still shows static metadata only.

- [ ] **Step 3: Write minimal implementation**

Add the snapshot import and a constant near the other constants (`:42`):

```python
SNAPSHOT_POLL_INTERVAL = 1.0  # seconds between preview snapshot refreshes
```

Add to imports (`from . import` block):

```python
from . import snapshot as _snapshot
```

In `on_mount`, after the existing `self.set_interval(SPINNER_INTERVAL, self._tick_spinner)` (`:563`):

```python
        self.set_interval(SNAPSHOT_POLL_INTERVAL, self._refresh_preview)
```

Replace `_refresh_preview` (`:1241-1250`) with:

```python
    def _refresh_preview(self) -> None:
        if not self._preview.display:
            return
        node = self._tree.cursor_node
        data = node.data if node and node.data else {}
        if "sid" not in data:
            self._preview.update("[dim]Select a session to preview.[/]")
            return
        sid = data["sid"]
        if self._tmux_enabled and sid in self._live_states:
            self._preview.update(self._render_live_preview(data, sid))
            return
        self._preview.update(_preview_text(data))

    def _render_live_preview(self, data: dict, sid: str):
        from rich.console import Group
        from rich.text import Text
        text, is_ansi = _snapshot.snapshot(
            sid=sid,
            transcript_path=data.get("transcript_path", ""),
            tmux_window_names=_tmux.session_windows(),
            capture_fn=_tmux.capture_pane)
        body = Text.from_ansi(text) if is_ansi else Text(text)
        header = Text.from_markup(
            f"[b]{data.get('name_cached') or sid[:8]}[/]  "
            f"[green]{self._live_states.get(sid, '')}[/]\n[dim]── live ──[/]")
        return Group(header, body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -q -k snapshot`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): live snapshot in preview (capture-pane / transcript tail)"
```

---

## Task 11: `tui.py` — quit-guard modal

**Files:**
- Modify: `bin/_pkg/tui.py` — add `QuitScreen` (near `ConfirmScreen` `:332`); add `action_quit` on the app
- Test: `test/test_tui.py`

> If Spike C selected **Option B**, drop the "background" option from `QuitScreen` and from `action_quit` (only "shutdown"/"cancel" remain).

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui.py  (append)
async def test_quit_with_live_sessions_shuts_down(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: ["sid-1"])
    monkeypatch.setattr(tuimod._tmux, "kill_server", lambda: calls.setdefault("kill", True) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_quit()                    # live sessions → guard modal
        await pilot.pause()
        await pilot.press("s")               # shut down all
        await pilot.pause()
    assert calls.get("kill") is True


async def test_quit_without_sessions_exits_directly(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: [])
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_quit()                    # no sessions → no modal, just exit
        await pilot.pause()
    # Reaching here without a hanging modal means it exited cleanly.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k "quit_with_live or quit_without"`
Expected: FAIL — no `QuitScreen` / `s` does nothing.

- [ ] **Step 3: Write minimal implementation**

Add a `QuitScreen` after `ConfirmScreen` (`:351`):

```python
class QuitScreen(_PanelScreen):
    """Exit guard when sessions are still running. Returns 'shutdown',
    'background', or None (cancel)."""

    BINDINGS = [
        Binding("s", "dismiss('shutdown')", "Shut down all", show=False),
        Binding("b", "dismiss('background')", "Leave running", show=False),
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("c", "dismiss(None)", "Cancel", show=False),
    ]

    def __init__(self, names: list) -> None:
        super().__init__()
        self._names = names

    def compose(self) -> ComposeResult:
        listing = "\n".join(f"  • {n}" for n in self._names)
        yield Vertical(
            Label(f"{len(self._names)} Claude session(s) still running:\n{listing}",
                  classes="dialog-title"),
            Label("s shut down all · b leave running · esc/c cancel",
                  classes="dialog-hint"),
            id="panel",
        )
```

Add `action_quit` on `SessionExplorerApp` (place near `action_help`):

```python
    def action_quit(self) -> None:
        if not self._tmux_enabled:
            self.exit()
            return
        running = _tmux.session_windows()
        if not running:
            self.exit()
            return

        def after(choice) -> None:
            if choice == "shutdown":
                _tmux.kill_server()
                self.exit()
            elif choice == "background":
                flag = os.path.join(self._claude_dir(),
                                    ".session-explorer.tmux-persist")
                _tmux.set_persist_flag(flag)   # Option C: this detach is deliberate
                _tmux.detach_client()
            # None → cancel: stay in the explorer.
        self.push_screen(QuitScreen(running), after)
```

Add `quit` to the modal-guard list in `check_action` (`:484`) so the `q` binding is suppressed while a modal is up (append `"quit"` to the tuple).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -q -k "quit_with_live or quit_without"`
Expected: PASS. Also run the existing `test_tui_quit` (no env var → `_tmux_enabled` False → direct exit): `python3 -m pytest test/test_tui.py -q -k tui_quit` → PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): quit-guard with shutdown/leave-running/cancel"
```

---

## Task 12: `tui.py` — reconciliation log on mount (defensive)

**Files:**
- Modify: `bin/_pkg/tui.py` — `on_mount`
- Test: `test/test_tui.py`

Reconciliation is mostly implicit (Enter reads `session_windows()` live; the live dots come from `live.py`). The one explicit need: on mount, prune any `exited` corpses left from a prior run so stale dead windows don't linger. Keep it minimal.

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui.py  (append)
async def test_mount_does_not_crash_without_tmux(index_path):
    # Sanity: mount path must be safe when not tmux-hosted (no env var).
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._tmux_enabled is False
```

- [ ] **Step 2: Run test to verify it fails (or passes trivially)**

Run: `python3 -m pytest test/test_tui.py -q -k mount_does_not_crash`
Expected: PASS already if Task 9's `_tmux_enabled` is in place. (This task is a guard against regressions; if it passes, proceed — no code change required beyond confirming `session_windows()` is never called at mount when `_tmux_enabled` is False.)

- [ ] **Step 3: Confirm no eager tmux call at mount**

Grep to ensure `_tmux.` is only called from `action_resume`, `action_quit`, and `_render_live_preview` — never unconditionally in `on_mount`:

Run: `grep -n "_tmux\." bin/_pkg/tui.py`
Expected: matches only inside those methods.

- [ ] **Step 4: Run the full TUI suite**

Run: `python3 -m pytest test/test_tui.py test/test_tui_live.py -q`
Expected: PASS

- [ ] **Step 5: Commit (if anything changed; else skip)**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "test(tui): guard mount path stays tmux-free without host"
```

---

## Task 13: tmux detection + consent-to-install prompt

**Files:**
- Create: `bin/_pkg/tmux_install.py` (detection + package-manager command building)
- Modify: `bin/_pkg/tui.py` — `on_mount` consent prompt when `_tmux_enabled` is False but tmux could help
- Test: `test/test_tmux_install.py`

> This surfaces the install offer only on the **plain** (non-tmux) launch — i.e. tmux was missing when `/open` ran, so the explorer is not tmux-hosted. The prompt mirrors the retention pattern (one-time, declined-marker).

- [ ] **Step 1: Write the failing test**

```python
# test/test_tmux_install.py
from _pkg import tmux_install


def test_install_command_macos_brew(monkeypatch):
    monkeypatch.setattr(tmux_install, "_which", lambda n: "/opt/homebrew/bin/brew" if n == "brew" else None)
    assert tmux_install.install_command("Darwin") == "brew install tmux"


def test_install_command_linux_apt(monkeypatch):
    monkeypatch.setattr(tmux_install, "_which", lambda n: "/usr/bin/apt-get" if n == "apt-get" else None)
    assert tmux_install.install_command("Linux") == "sudo apt-get install -y tmux"


def test_install_command_unknown_returns_none(monkeypatch):
    monkeypatch.setattr(tmux_install, "_which", lambda n: None)
    assert tmux_install.install_command("Linux") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tmux_install.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# bin/_pkg/tmux_install.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tmux_install.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux_install.py test/test_tmux_install.py
git commit -m "feat(tmux): package-manager install-command detection"
```

---

## Task 14: consent prompt wiring + declined marker

**Files:**
- Modify: `bin/_pkg/tui.py` — `on_mount` (after the retention block, before live polling)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui.py  (append)
async def test_tmux_offer_shown_once_then_marked(tmp_path, monkeypatch):
    import json
    from _pkg.tui import SessionExplorerApp
    # Fresh index dir WITHOUT the tmux-declined marker; retention already decided.
    path = str(tmp_path / "se-index.json")
    json.dump({"version": 1, "folders": [], "sessions": {}}, open(path, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    monkeypatch.delenv("SESSION_EXPLORER_TMUX", raising=False)  # plain launch
    app = SessionExplorerApp(index_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # The tmux offer modal is up; declining writes the marker.
        await pilot.press("n")
        await pilot.pause()
    assert (tmp_path / ".session-explorer.tmux-declined").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k tmux_offer`
Expected: FAIL — no marker written.

- [ ] **Step 3: Write minimal implementation**

Add a helper and call it from `on_mount`. After the retention `else: self._maybe_open_help()` branch resolves — i.e. fold the tmux offer into `_maybe_open_help`'s tail, or add a dedicated method invoked after help. Concretely, add:

```python
    def _tmux_decline_marker(self) -> str:
        return os.path.join(self._claude_dir(), ".session-explorer.tmux-declined")

    def _maybe_offer_tmux(self) -> None:
        # Only when NOT tmux-hosted (tmux was absent at /open) and not already
        # declined. Mirrors the retention one-time prompt.
        if self._tmux_enabled or os.path.exists(self._tmux_decline_marker()):
            return
        if _tmux.available():       # present now but launch wasn't wrapped; skip
            return

        def after(ok: bool) -> None:
            if not ok:
                open(self._tmux_decline_marker(), "a").close()
            # On "yes" we show the install command via a follow-up info modal;
            # the user installs it and re-runs /open. No marker on yes so the
            # offer can reappear until tmux is actually present.
            else:
                import platform
                from . import tmux_install
                cmd = tmux_install.install_command(platform.system()) \
                    or "see https://github.com/tmux/tmux/wiki/Installing"
                self.push_screen(ConfirmScreen(
                    f"Run this, then re-open the explorer:\n\n  {cmd}\n\n(y/esc)"))
        self.push_screen(ConfirmScreen(
            "Run multiple sessions and monitor them live inside the explorer?\n"
            "This needs tmux, which isn't installed. Set it up? (y = how, n = no)"),
            after)
```

Call `self._maybe_offer_tmux()` at the end of `_maybe_open_help` (so it runs after the help modal closes, preserving modal ordering). Find `_maybe_open_help` (`:565`) and append the call as its last statement.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -q -k tmux_offer`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): one-time tmux install offer with declined marker"
```

---

## Task 15: uninstall teardown of generated files

**Files:**
- Modify: `bin/_pkg/uninstall.py`
- Test: `test/test_uninstall.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_uninstall.py  (append — match existing teardown test style)
def test_teardown_removes_tmux_artifacts(tmp_path):
    from _pkg import uninstall
    claude = tmp_path
    for name in (".session-explorer.tmux.conf",
                 ".session-explorer.tmux-persist",
                 ".session-explorer.tmux-declined"):
        (claude / name).write_text("x")
    uninstall.teardown(claude_dir=str(claude), purge_data=False)
    for name in (".session-explorer.tmux.conf",
                 ".session-explorer.tmux-persist",
                 ".session-explorer.tmux-declined"):
        assert not (claude / name).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_uninstall.py -q -k tmux_artifacts`
Expected: FAIL — files still present.

- [ ] **Step 3: Write minimal implementation**

In `uninstall.teardown`, add removal of the three tmux artifacts (follow the existing best-effort `os.remove`/try-except pattern already used for other sidecars). If a running tmux server exists, also kill it:

```python
    # tmux interaction layer artifacts (best-effort).
    for name in (".session-explorer.tmux.conf",
                 ".session-explorer.tmux-persist",
                 ".session-explorer.tmux-declined"):
        try:
            os.remove(os.path.join(claude_dir, name))
            actions.append(f"removed {name}")
        except FileNotFoundError:
            pass
    try:
        from . import tmux as _tmux
        if _tmux.available():
            _tmux.kill_server()   # no-op if our server isn't running
    except Exception:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_uninstall.py -q -k tmux_artifacts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/uninstall.py test/test_uninstall.py
git commit -m "feat(uninstall): tear down tmux config and markers"
```

---

## Task 16: docs — SPEC.md & CLAUDE.md load-bearing decisions

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md`

- [ ] **Step 1: Update `SPEC.md`** — add a "tmux interaction layer" section describing: dedicated server, non-destructive resume, context-aware Enter, snapshot sources, quit-guard + Option-C detach sentinel, tmux-optional + consented install. Cross-reference the design spec.

- [ ] **Step 2: Update `CLAUDE.md`** — add to "Load-bearing design decisions":
  - **Resume is non-destructive when tmux-hosted.** The explorer runs as tmux window 0 and stays alive; sessions are sibling windows. Without tmux it falls back to `execvp` (today's behaviour). Don't reintroduce unconditional exit-on-resume.
  - **tmux is an optional, consented dependency.** Detect + offer install (declined-marker), never bundle a binary, never silent-sudo. The dedicated `-L session-explorer` server never touches the user's tmux.
  - **Snapshots are read-only.** capture-pane for our windows, transcript-tail otherwise. No embedded interactive terminal widget.
  - **Abrupt window-close shuts sessions down via the persist-flag sentinel (Option C).** Only the deliberate "leave running" quit path persists. Don't leave lingering claude sessions on red-button close.

- [ ] **Step 3: Bump version** — update `plugin.json` and `.claude-plugin/plugin.json` version and add a `CHANGELOG.md` entry.

- [ ] **Step 4: Commit**

```bash
git add SPEC.md CLAUDE.md CHANGELOG.md .claude-plugin/plugin.json bin/_pkg/__init__.py
git commit -m "docs: record tmux interaction layer in SPEC and CLAUDE; bump version"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `python3 -m pytest test/ -q`
Expected: all pass.

- [ ] **Run the shell suites**

Run: `bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: all pass.

- [ ] **Manual end-to-end (with tmux installed):** `session-explorer launch` → resume a stopped session (starts in background, dot goes live) → resume a second → `space` to peek each → Enter on a running one (flips in) → F12 / click tab back → `q` → choose shut down → terminal closes, no lingering `tmux -L session-explorer list-sessions`.

---

## Self-review notes (for the implementer)

- **Spec coverage:** §1 launch→Task 7/8; §2 Enter/switch→Task 9 + config Task 3; §3 snapshot→Task 5/6/10; §4 dots→reuse `live.py` (no task needed); §5 quit-guard + Option C→Task 11 + config Task 3; §6 config→Task 3; §7 dependency/install→Task 1/13/14; §8 code shape→Tasks 1–14; §9 spikes→Spikes A–C; §10 build order→task order; §11 testing→tests in each task.
- **Spike gating:** Tasks 3 and 11 carry an "if Option B" branch. Run Spike C before Task 3 and honour the outcome.
- **Naming consistency:** `session_windows()`, `start_window()`, `select_window()`, `capture_pane()`, `set_persist_flag()/clear_persist_flag()/persist_flag_set()`, `build_config(persist_flag_path=, back_key=)`, `wrap_in_tmux(target, config_path)`, `_tmux_enabled`. These names are used identically across tasks — keep them.
- **No real tmux in CI:** every TUI/CLI test monkeypatches the `tmux` executing wrappers; only the spikes touch a live server.
