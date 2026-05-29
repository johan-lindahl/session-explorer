# Active-Session Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show in the explorer TUI which Claude Code sessions are currently live — an animated spinner for "actively working", a dim `○` for "open but idle", nothing otherwise — scaling to multiple concurrent sessions.

**Architecture:** Claude Code lifecycle hooks (`SessionStart`/`UserPromptSubmit`/`Stop`/`Notification`/`SessionEnd`) feed a small dispatcher script that calls a new `session-explorer live` CLI subcommand, which maintains a volatile registry file `~/.claude/session-explorer-live.json` (flock + temp-rename, reusing the index's atomic pattern). The TUI gains two timers: a ~200ms spinner tick and a ~2s registry poll that reads the registry, prunes dead sessions (`kill -0 pid` + a 24h TTL backstop), and re-renders affected rows. Live sessions are shown even when unnamed (exception to the hide-unnamed default).

**Tech Stack:** Python 3.11+ (stdlib `os`/`fcntl`/`json`/`datetime`), vendored Textual (`Tree`, `set_interval`), bash hooks, pytest + bats.

**Design doc:** `docs/superpowers/specs/2026-05-29-active-session-indicator-design.md`

---

## File Structure

**Create:**
- `bin/_pkg/live.py` — registry: paths, load/mutate, `record_event`, death-detection (`poll`). One responsibility: the live-session registry.
- `hooks/session-live.sh` — lifecycle-event dispatcher → CLI. Mirrors `hooks/session-start.sh` style.
- `test/test_live.py` — pytest for `live.py`.
- `test/test_tui_live.py` — pytest for the pure TUI glyph helper.

**Modify:**
- `bin/_pkg/cli.py` — add `live` subparser, `_cmd_live`, `_live_path`.
- `bin/_pkg/tree_model.py` — `build_nested_tree` gains a `live_ids` escape hatch.
- `bin/_pkg/tui.py` — glyph column, spinner/poll timers, `sid → node` map, subtitle active count, visibility-change repopulate.
- `plugin.json` — register the four new hook events.
- `install.sh` / `uninstall.sh` — register/unregister the four new events for the plain-install path.
- `test/test_tree_model.py`, `test/hook.bats`, `test/install.bats` — extend.
- `SPEC.md` — document the feature (repo's authoritative spec).

**Conventions to follow (from the existing code):**
- Atomic writes via `index.save` + an flock'd `*.lock` sidecar (see `index.mutate`).
- Env override for paths (`SESSION_EXPLORER_INDEX` → add `SESSION_EXPLORER_LIVE`).
- Hooks never block and always `exit 0`; failures append to `~/.claude/session-explorer.log`.
- Pure helpers are unit-tested without spinning up the app.

---

## Task 1: Spike — validate PID capture from a hook (decision gate)

**Why:** The entire "open but idle" detection assumes `hooks/session-live.sh`'s parent process (`$PPID`) is the long-lived Claude process. If it's a transient wrapper shell, the recorded pid dies immediately and `kill -0` would wrongly report the session dead. This spike decides PID-path vs TTL-only **before** writing the real code. No production code is committed in this task.

**Files:**
- Create (throwaway): `/tmp/se-ppid-probe.sh`

- [ ] **Step 1: Write a probe hook**

```bash
cat > /tmp/se-ppid-probe.sh <<'EOF'
#!/usr/bin/env bash
LOG="$HOME/.claude/se-ppid-probe.log"
PAYLOAD="$(cat 2>/dev/null || true)"
EVENT="$(printf '%s' "$PAYLOAD" | python3 -c "import json,sys;print(json.load(sys.stdin).get('hook_event_name',''))" 2>/dev/null)"
{
  echo "[$(date -u +%FT%TZ)] event=$EVENT mypid=$$ ppid=$PPID"
  ps -o pid,ppid,command -p "$PPID" 2>/dev/null
} >> "$LOG" 2>&1
exit 0
EOF
chmod +x /tmp/se-ppid-probe.sh
```

- [ ] **Step 2: Temporarily register it on SessionStart + Stop**

Add to `~/.claude/settings.json` under `hooks` (remove after the spike):

```json
{
  "hooks": {
    "SessionStart": [ { "hooks": [ { "type": "command", "command": "/tmp/se-ppid-probe.sh" } ] } ],
    "Stop":         [ { "hooks": [ { "type": "command", "command": "/tmp/se-ppid-probe.sh" } ] } ]
  }
}
```

- [ ] **Step 3: Start a fresh Claude session in another terminal, send one prompt, wait for the reply, then leave it open**

- [ ] **Step 4: Inspect the probe log and the recorded pid's liveness**

Run:
```bash
cat ~/.claude/se-ppid-probe.log
# Take the ppid printed for the SessionStart line, then:
ps -o pid,command -p <that_ppid>
```
Expected (PID-path viable): the `ppid` printed at SessionStart belongs to a `claude` process and that same pid is **still alive** while the session sits idle.
If instead the `ppid` is a short-lived `sh`/`bash -c` that no longer exists, the PID-path is **not** viable.

- [ ] **Step 5: Record the decision and clean up**

Write the outcome into the design doc's §4 "Validation risk" paragraph (`docs/superpowers/specs/2026-05-29-active-session-indicator-design.md`):
- If viable → "Confirmed: hook `$PPID` is the live Claude process; PID-path adopted."
- If not → "Not viable on this platform; falling back to TTL-only death detection (do not record/trust `pid`)."

Then remove the probe hook from `settings.json` and delete `/tmp/se-ppid-probe.sh` and `~/.claude/se-ppid-probe.log`.

```bash
rm -f /tmp/se-ppid-probe.sh ~/.claude/se-ppid-probe.log
```

- [ ] **Step 6: Commit the decision**

```bash
git add docs/superpowers/specs/2026-05-29-active-session-indicator-design.md
git commit -m "docs: record PID-capture spike outcome for active-session indicator"
```

> **Downstream note:** The code in Tasks 2–9 records and uses `pid` unconditionally; `live._alive` already degrades to TTL-only when `pid` is `None`. If the spike says "not viable", the only change is in Task 5: have the hook **not** pass `--pid` (so entries store `pid: None`). All other tasks are unchanged. The rest of this plan is written for the viable case.

---

## Task 2: `live.py` — registry core (paths, load, mutate, record_event)

**Files:**
- Create: `bin/_pkg/live.py`
- Test: `test/test_live.py`

- [ ] **Step 1: Write failing tests for paths and event transitions**

```python
# test/test_live.py
import os
from datetime import datetime, timezone

from _pkg import live


T0 = datetime(2026, 5, 29, 7, 0, 0, tzinfo=timezone.utc)


def test_default_path_for_is_sibling_of_index():
    p = live.default_path_for("/x/y/session-explorer-index.json")
    assert p == "/x/y/session-explorer-live.json"


def test_load_missing_returns_v1_empty(tmp_path):
    data = live.load(str(tmp_path / "live.json"))
    assert data == {"version": 1, "sessions": {}}


def test_session_start_records_idle_with_pid(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1",
                      transcript_path="/t/s1.jsonl", cwd="/repo", pid=4242, now=T0)
    e = live.load(lp)["sessions"]["s1"]
    assert e["state"] == "idle"
    assert e["pid"] == 4242
    assert e["transcript_path"] == "/t/s1.jsonl"
    assert e["cwd"] == "/repo"
    assert e["last_seen"] == T0.isoformat()


def test_user_prompt_submit_sets_working(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1", pid=1, now=T0)
    live.record_event(lp, event="UserPromptSubmit", session_id="s1", now=T0)
    assert live.load(lp)["sessions"]["s1"]["state"] == "working"


def test_stop_sets_idle(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1", pid=1, now=T0)
    live.record_event(lp, event="UserPromptSubmit", session_id="s1", now=T0)
    live.record_event(lp, event="Stop", session_id="s1", now=T0)
    assert live.load(lp)["sessions"]["s1"]["state"] == "idle"


def test_notification_sets_idle(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1", pid=1, now=T0)
    live.record_event(lp, event="UserPromptSubmit", session_id="s1", now=T0)
    live.record_event(lp, event="Notification", session_id="s1", now=T0)
    assert live.load(lp)["sessions"]["s1"]["state"] == "idle"


def test_session_end_removes_entry(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1", pid=1, now=T0)
    live.record_event(lp, event="SessionEnd", session_id="s1", now=T0)
    assert "s1" not in live.load(lp)["sessions"]


def test_event_for_unknown_session_creates_entry(tmp_path):
    # UserPromptSubmit may arrive without a prior SessionStart in this process.
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="UserPromptSubmit", session_id="ghost", now=T0)
    assert live.load(lp)["sessions"]["ghost"]["state"] == "working"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_live.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.live'`

- [ ] **Step 3: Implement the registry core**

```python
# bin/_pkg/live.py
"""Volatile live-session registry for session-explorer.

Schema (v1): {"version": 1, "sessions": {session_id: entry}}
  entry = {"state": "working"|"idle", "pid": int|None,
           "last_seen": iso8601, "transcript_path": str, "cwd": str}

This file is runtime-only: it is never merged into the index and never read by
retention/--gc. It reuses the index's atomic write (index.save) but keeps its
own load/mutate so a fresh file defaults to version 1 (index.load defaults to
v2). Concurrency: flock(LOCK_EX) on a sibling '.lock' file, write-temp-rename.
"""

from __future__ import annotations

import fcntl
import os
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from .index import save as _save  # generic atomic temp-rename writer

WORKING = "working"
IDLE = "idle"
DEFAULT_TTL_SECONDS = 86400  # 24h backstop against PID reuse
_DEFAULT = {"version": 1, "sessions": {}}


def default_path_for(index_path: str) -> str:
    """Sibling of the index file (mirrors folder_store.default_path_for)."""
    return os.path.join(os.path.dirname(index_path), "session-explorer-live.json")


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"version": 1, "sessions": {}}
    with open(path, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            return json.load(f)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def mutate(path: str, fn: Callable[[dict], dict]) -> dict:
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)) or ".", exist_ok=True)
    with open(lock_path, "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            data = fn(load(path))
            _save(path, data)
            return data
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def record_event(path: str, *, event: str, session_id: str,
                 transcript_path: Optional[str] = None,
                 cwd: Optional[str] = None, pid: Optional[int] = None,
                 now: Optional[datetime] = None) -> None:
    ts = (now or datetime.now(timezone.utc)).isoformat()

    def m(data: dict) -> dict:
        sessions = data.setdefault("sessions", {})
        data.setdefault("version", 1)
        if event == "SessionEnd":
            sessions.pop(session_id, None)
            return data
        entry = sessions.get(session_id, {})
        if event == "SessionStart":
            entry["state"] = IDLE
            if transcript_path:
                entry["transcript_path"] = transcript_path
            if cwd:
                entry["cwd"] = cwd
            if pid is not None:
                entry["pid"] = pid
        elif event == "UserPromptSubmit":
            entry["state"] = WORKING
        elif event in ("Stop", "Notification"):
            entry["state"] = IDLE
        entry.setdefault("pid", entry.get("pid"))
        entry["last_seen"] = ts
        sessions[session_id] = entry
        return data

    mutate(path, m)
```

Also add `import json` at the top (next to `import fcntl`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_live.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/live.py test/test_live.py
git commit -m "feat: live-session registry core (record_event + atomic store)"
```

---

## Task 3: `live.py` — death detection (`poll`)

**Files:**
- Modify: `bin/_pkg/live.py`
- Test: `test/test_live.py`

- [ ] **Step 1: Write failing tests for liveness/pruning**

Append to `test/test_live.py`:

```python
from datetime import timedelta


def _seed(tmp_path, **entry):
    lp = str(tmp_path / "live.json")
    live.mutate(lp, lambda d: {**d, "sessions": {**d.get("sessions", {}), "s1": entry}})
    return lp


def test_poll_keeps_alive_pid_and_returns_state(tmp_path):
    lp = _seed(tmp_path, state="working", pid=os.getpid(),
               last_seen=T0.isoformat())
    states = live.poll(lp, now=T0)
    assert states == {"s1": "working"}


def test_poll_prunes_dead_pid(tmp_path):
    # PID 1 exists; use an almost-certainly-dead high pid instead.
    dead_pid = 2 ** 22
    lp = _seed(tmp_path, state="idle", pid=dead_pid, last_seen=T0.isoformat())
    states = live.poll(lp, now=T0)
    assert states == {}
    assert "s1" not in live.load(lp)["sessions"]  # pruned from disk


def test_poll_ttl_backstop_prunes_alive_pid_when_stale(tmp_path):
    lp = _seed(tmp_path, state="idle", pid=os.getpid(), last_seen=T0.isoformat())
    later = T0 + timedelta(seconds=live.DEFAULT_TTL_SECONDS + 1)
    assert live.poll(lp, now=later) == {}


def test_poll_no_pid_uses_ttl_only(tmp_path):
    lp = _seed(tmp_path, state="idle", pid=None, last_seen=T0.isoformat())
    assert live.poll(lp, now=T0) == {"s1": "idle"}
    later = T0 + timedelta(seconds=live.DEFAULT_TTL_SECONDS + 1)
    assert live.poll(lp, now=later) == {}


def test_poll_does_not_write_when_nothing_dead(tmp_path):
    lp = _seed(tmp_path, state="idle", pid=os.getpid(), last_seen=T0.isoformat())
    before = os.path.getmtime(lp)
    live.poll(lp, now=T0)
    assert os.path.getmtime(lp) == before  # no needless rewrite
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_live.py -q`
Expected: FAIL — `AttributeError: module '_pkg.live' has no attribute 'poll'`

- [ ] **Step 3: Implement death detection + poll**

Append to `bin/_pkg/live.py`:

```python
def _pid_alive(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False
    return True


def _age_seconds(last_seen: Optional[str], now: datetime) -> Optional[float]:
    if not last_seen:
        return None
    try:
        return (now - datetime.fromisoformat(last_seen)).total_seconds()
    except ValueError:
        return None


def _alive(entry: dict, now: datetime, ttl_seconds: int) -> bool:
    age = _age_seconds(entry.get("last_seen"), now)
    pid = entry.get("pid")
    if pid is not None:
        if not _pid_alive(pid):
            return False
        return not (age is not None and age > ttl_seconds)  # TTL backstop
    # No pid → TTL only.
    return age is not None and age <= ttl_seconds


def poll(path: str, *, now: Optional[datetime] = None,
         ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Dict[str, str]:
    """Return {session_id: state} for live sessions, pruning dead ones from disk.

    Read-only in the common case; only rewrites the file when something died.
    """
    now = now or datetime.now(timezone.utc)
    data = load(path)
    sessions = data.get("sessions", {})
    survivors: Dict[str, str] = {}
    dead = []
    for sid, entry in sessions.items():
        if _alive(entry, now, ttl_seconds):
            survivors[sid] = entry.get("state", IDLE)
        else:
            dead.append(sid)
    if dead:
        def m(d: dict) -> dict:
            for sid in dead:
                d.get("sessions", {}).pop(sid, None)
            return d
        mutate(path, m)
    return survivors
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_live.py -q`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/live.py test/test_live.py
git commit -m "feat: live registry death detection (kill -0 + TTL backstop)"
```

---

## Task 4: CLI `live` subcommand

**Files:**
- Modify: `bin/_pkg/cli.py`
- Test: `test/test_cli.py`

- [ ] **Step 1: Write a failing test**

Append to `test/test_cli.py` (it already runs the CLI via subprocess — match the existing helper style; the snippet below uses a direct call to `_pkg.cli.main`, which also works):

```python
import json as _json
from _pkg import cli as _cli


def test_cli_live_records_event(tmp_path, monkeypatch):
    live_path = tmp_path / "session-explorer-live.json"
    monkeypatch.setenv("SESSION_EXPLORER_LIVE", str(live_path))
    rc = _cli.main(["live", "--event", "SessionStart", "--sid", "abc",
                    "--transcript", "/t/abc.jsonl", "--cwd", "/repo", "--pid", "5"])
    assert rc == 0
    data = _json.loads(live_path.read_text())
    assert data["sessions"]["abc"]["state"] == "idle"
    assert data["sessions"]["abc"]["pid"] == 5


def test_cli_live_never_errors_on_bad_input(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_EXPLORER_LIVE", str(tmp_path / "live.json"))
    # Missing --sid still must not crash the hook caller.
    rc = _cli.main(["live", "--event", "Stop", "--sid", ""])
    assert rc == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_cli.py -k live -q`
Expected: FAIL — argparse error (`invalid choice: 'live'`) → nonzero exit.

- [ ] **Step 3: Implement the subcommand**

In `bin/_pkg/cli.py`, add the path resolver next to `_index_path` (line ~17):

```python
def _live_path() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_LIVE")
    if env_override:
        return env_override
    from . import live as _live
    return _live.default_path_for(_index_path())
```

In `build_parser`, after the `index_p` block (line ~42), add:

```python
    live_p = sub.add_parser("live", help="Record a session lifecycle event (used by hooks).")
    live_p.add_argument("--event", required=True,
                        help="Hook event name (SessionStart/UserPromptSubmit/Stop/Notification/SessionEnd).")
    live_p.add_argument("--sid", required=True, help="Session id.")
    live_p.add_argument("--transcript", default=None)
    live_p.add_argument("--cwd", default=None)
    live_p.add_argument("--pid", type=int, default=None)
```

Add the command handler (next to `_cmd_index`):

```python
def _cmd_live(args) -> int:
    # Hooks call this; it must never raise (would surface as a hook failure).
    if not args.sid:
        return 0
    try:
        from . import live as _live
        _live.record_event(_live_path(), event=args.event, session_id=args.sid,
                            transcript_path=args.transcript, cwd=args.cwd, pid=args.pid)
    except Exception as e:
        try:
            log = os.path.expanduser("~/.claude/session-explorer.log")
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"warn: live --event {args.event} failed: {e}\n")
        except Exception:
            pass
    return 0
```

In `main`, dispatch it (after the `index` branch, line ~179):

```python
    if args.cmd == "live":
        return _cmd_live(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_cli.py -k live -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat: 'session-explorer live' CLI subcommand for hook events"
```

---

## Task 5: `hooks/session-live.sh` dispatcher

**Files:**
- Create: `hooks/session-live.sh`
- Test: `test/hook.bats`

- [ ] **Step 1: Write a failing bats test**

Append to `test/hook.bats` (mirror the existing helper that stubs the CLI and feeds JSON on stdin):

```bash
@test "session-live.sh forwards SessionStart with pid to the CLI" {
  # Stub CLI captures argv.
  STUB="$BATS_TEST_TMPDIR/cli-args"
  cat > "$BATS_TEST_TMPDIR/session-explorer" <<EOF
#!/usr/bin/env bash
echo "\$@" >> "$STUB"
EOF
  chmod +x "$BATS_TEST_TMPDIR/session-explorer"
  export PATH="$BATS_TEST_TMPDIR:$PATH"

  echo '{"hook_event_name":"SessionStart","session_id":"s9","transcript_path":"/t/s9.jsonl","cwd":"/repo"}' \
    | "$BATS_TEST_DIRNAME/../hooks/session-live.sh"

  run cat "$STUB"
  [[ "$output" == *"live --event SessionStart"* ]]
  [[ "$output" == *"--sid s9"* ]]
  [[ "$output" == *"--pid "* ]]
}

@test "session-live.sh exits 0 even with empty stdin" {
  run bash -c 'printf "" | "$BATS_TEST_DIRNAME/../hooks/session-live.sh"'
  [ "$status" -eq 0 ]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats test/hook.bats -f session-live`
Expected: FAIL — `hooks/session-live.sh` does not exist.

- [ ] **Step 3: Implement the dispatcher**

```bash
# hooks/session-live.sh
#!/usr/bin/env bash
# Lifecycle dispatcher for session-explorer's live-session registry.
# Reads JSON on stdin: {hook_event_name, session_id, transcript_path, cwd, ...}
# Records the event via the CLI, fully non-blocking. Never blocks; exits 0.

set -u
CLAUDE_DIR="${HOME}/.claude"
LOG="${CLAUDE_DIR}/session-explorer.log"
mkdir -p "${CLAUDE_DIR}" 2>/dev/null || true
log() { echo "[$(date -u +%FT%TZ)] $*" >> "${LOG}" 2>/dev/null || true; }

PAYLOAD="$(cat 2>/dev/null || true)"

# Resolve the CLI (same strategy as session-start.sh).
CLI=""
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -x "${CLAUDE_PLUGIN_ROOT}/bin/session-explorer" ]; then
  CLI="${CLAUDE_PLUGIN_ROOT}/bin/session-explorer"
elif [ -x "${HOME}/.local/bin/session-explorer" ]; then
  CLI="${HOME}/.local/bin/session-explorer"
else
  CLI="$(command -v session-explorer 2>/dev/null || echo "")"
fi
[ -n "${CLI}" ] && [ -x "${CLI}" ] || { log "warn: session-live: CLI not found"; exit 0; }
[ -n "${PAYLOAD}" ] || exit 0

read -r EVENT SID TPATH CWD < <(printf '%s' "${PAYLOAD}" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('hook_event_name',''), d.get('session_id',''),
          d.get('transcript_path',''), d.get('cwd',''))
except Exception:
    print('', '', '', '')
" 2>/dev/null)

[ -n "${EVENT}" ] && [ -n "${SID}" ] || exit 0

# Run detached so a turn never waits on the registry write. $PPID is the Claude
# process (validated in the PID-capture spike); recorded only on SessionStart.
ARGS=(live --event "${EVENT}" --sid "${SID}")
[ -n "${TPATH}" ] && ARGS+=(--transcript "${TPATH}")
[ -n "${CWD}" ] && ARGS+=(--cwd "${CWD}")
if [ "${EVENT}" = "SessionStart" ]; then
  ARGS+=(--pid "${PPID}")
fi
( "${CLI}" "${ARGS[@]}" >>"${LOG}" 2>&1 ) >/dev/null 2>&1 &

exit 0
```

> **If the Task 1 spike said PID-path is not viable:** delete the `if [ "${EVENT}" = "SessionStart" ]` block so `--pid` is never passed. Nothing else changes.

```bash
chmod +x hooks/session-live.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bats test/hook.bats -f session-live`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add hooks/session-live.sh test/hook.bats
git commit -m "feat: session-live.sh hook dispatcher for lifecycle events"
```

---

## Task 6: Register the four hook events (plugin.json + install.sh/uninstall.sh)

**Files:**
- Modify: `plugin.json`
- Modify: `install.sh`, `uninstall.sh`
- Test: `test/install.bats`

- [ ] **Step 1: Register events in `plugin.json`**

Replace the `"hooks"` block in `plugin.json` with (keeps the existing SessionStart command, adds the live dispatcher across five events):

```json
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/session-start.sh" },
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/session-live.sh" }
        ]
      }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/session-live.sh" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/session-live.sh" } ] }
    ],
    "Notification": [
      { "matcher": "idle_prompt", "hooks": [ { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/session-live.sh" } ] }
    ],
    "SessionEnd": [
      { "hooks": [ { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/session-live.sh" } ] }
    ]
  }
```

- [ ] **Step 2: Write a failing install.bats test**

First read `install.sh` and `test/install.bats` to match the existing settings-merge approach:

Run: `sed -n '1,200p' install.sh; echo ---; sed -n '1,80p' test/install.bats`

Then append to `test/install.bats` (adjust the assertion to however the suite inspects the merged `settings.json`):

```bash
@test "install registers the live-session hooks in settings.json" {
  run_install   # use the suite's existing install helper
  run python3 -c "import json,os;d=json.load(open(os.environ['HOME']+'/.claude/settings.json'));print('Stop' in d.get('hooks',{}), 'UserPromptSubmit' in d.get('hooks',{}), 'SessionEnd' in d.get('hooks',{}))"
  [[ "$output" == "True True True" ]]
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `bats test/install.bats -f "live-session hooks"`
Expected: FAIL — events not present.

- [ ] **Step 4: Implement registration in `install.sh` / removal in `uninstall.sh`**

`install.sh` already merges a SessionStart hook into `settings.json`. Extend that same merge step to also add `UserPromptSubmit`, `Stop`, `Notification` (matcher `idle_prompt`), and `SessionEnd`, each pointing at the resolved `session-live.sh` path (the plain install uses an absolute path to the repo's `hooks/session-live.sh`, the same way it points at `session-start.sh`). Follow the file's existing merge mechanism (jq or python) — do not introduce a new one. `uninstall.sh` must remove these four events' entries (and the live dispatcher's SessionStart entry) when it removes the existing hook, leaving any user hooks intact.

> Implementation note: match `install.sh`'s current style exactly. If it merges via `python3`, add the four events to the same dict it builds; if via `jq`, extend the same filter. Do not touch `cleanupPeriodDays` here.

- [ ] **Step 5: Run install + uninstall tests to verify they pass**

Run: `bats test/install.bats test/uninstall.bats`
Expected: PASS (including the new assertion; uninstall leaves `settings.json` clean of the four events).

- [ ] **Step 6: Commit**

```bash
git add plugin.json install.sh uninstall.sh test/install.bats test/uninstall.bats
git commit -m "feat: register live-session lifecycle hooks (plugin + install/uninstall)"
```

---

## Task 7: `build_nested_tree` live-session escape hatch

**Files:**
- Modify: `bin/_pkg/tree_model.py:51-74`
- Test: `test/test_tree_model.py`

- [ ] **Step 1: Write failing tests**

Append to `test/test_tree_model.py`:

```python
def test_build_nested_tree_live_unnamed_surfaced_even_when_hidden():
    idx = {"sessions": {
        "u1": {"project_label": "proj", "name_cached": None, "last_active_at": "2026-01-01"},
    }}
    fs = {"projects": {}}
    # Without live_ids: hidden.
    assert build_nested_tree(idx, fs, include_unnamed=False) == {}
    # With u1 live: surfaced under the synthetic (unnamed) folder.
    t = build_nested_tree(idx, fs, include_unnamed=False, live_ids={"u1"})
    assert "proj" in t
    assert any(sid == "u1" for sid, _ in t["proj"]["_folders"]["(unnamed)"]["_sessions"])


def test_build_nested_tree_live_ids_none_is_default_behaviour():
    idx = {"sessions": {"u1": {"project_label": "proj", "name_cached": None,
                               "last_active_at": "2026-01-01"}}}
    assert build_nested_tree(idx, {"projects": {}}, include_unnamed=False) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tree_model.py -k live -q`
Expected: FAIL — `build_nested_tree() got an unexpected keyword argument 'live_ids'`

- [ ] **Step 3: Implement the escape hatch**

In `bin/_pkg/tree_model.py`, change the signature (line 51) and the filter (line 65):

```python
def build_nested_tree(index_data: dict, folder_store_data: dict,
                      include_unnamed: bool = False,
                      live_ids: "set[str] | None" = None) -> Dict[str, dict]:
```

```python
    live_ids = live_ids or set()
    # 1. Place each session into its project + folder path.
    for sid, s in index_data.get("sessions", {}).items():
        name = s.get("name_cached")
        if not name and not include_unnamed and sid not in live_ids:
            continue
```

(Update the docstring to note: "Live sessions (`live_ids`) are always placed, even when unnamed and `include_unnamed` is False — a live unnamed session goes under the synthetic `(unnamed)` folder.")

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tree_model.py -q`
Expected: PASS (all existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tree_model.py test/test_tree_model.py
git commit -m "feat: surface live unnamed sessions in build_nested_tree"
```

---

## Task 8: TUI glyph column (pure helper + row label + header)

**Files:**
- Modify: `bin/_pkg/tui.py` (constants near line 36; `_row_label` 46-64; `_column_header` 67-71)
- Test: `test/test_tui_live.py`

- [ ] **Step 1: Write failing tests for the glyph helper and label prefix**

```python
# test/test_tui_live.py
from _pkg import tui


def test_glyph_inactive_is_two_blank_cells():
    # None state → a non-markup 2-cell prefix so columns stay aligned.
    assert tui._glyph(None, frame=0) == "  "


def test_glyph_idle_is_dim_circle():
    assert tui._glyph("idle", frame=0) == "[dim]○[/] "


def test_glyph_working_cycles_spinner_frames():
    g0 = tui._glyph("working", frame=0)
    g1 = tui._glyph("working", frame=1)
    assert g0.startswith("[green]") and g0.endswith("[/] ")
    assert g0 != g1  # frame advanced → different braille glyph


def test_row_label_prepends_glyph_without_disturbing_name():
    s = {"name_cached": "myname", "last_active_at": None, "tokens_estimate": 0,
         "tokens_window_pct": 0, "message_count": 0, "first_prompt": ""}
    label = tui._row_label("sid12345", s, depth=2, glyph="[green]⠋[/] ")
    assert label.startswith("[green]⠋[/] ")
    assert "myname" in label
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui_live.py -q`
Expected: FAIL — `module '_pkg.tui' has no attribute '_glyph'` / `_row_label` missing `glyph` kwarg.

- [ ] **Step 3: Implement constants, `_glyph`, and the label/header prefix**

In `bin/_pkg/tui.py`, after the `NAME_W`/`GUIDE_DEPTH` constants (line ~37) add:

```python
GLYPH_W = 2  # leading cells reserved on every row for the live-state glyph
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
IDLE_GLYPH = "○"
SPINNER_INTERVAL = 0.2   # seconds between spinner frames
LIVE_POLL_INTERVAL = 2.0  # seconds between registry polls


def _glyph(state: "str | None", frame: int) -> str:
    """A GLYPH_W-wide leading cell for a row's live state. Returns Textual
    console markup (rendered by Tree.process_label). Pure for unit testing.

    Display width is always GLYPH_W cells after markup is stripped (the markup
    glyph is 1 cell + 1 separating space), so stat columns stay aligned."""
    if state == "working":
        ch = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
        return f"[green]{ch}[/] "
    if state == "idle":
        return f"[dim]{IDLE_GLYPH}[/] "
    return " " * GLYPH_W
```

Change `_row_label` (line 46) to accept and prepend the glyph:

```python
def _row_label(sid: str, s: dict, depth: int, glyph: str = "  ") -> str:
    """Leaf row. `glyph` is a GLYPH_W-wide live-state prefix (see _glyph);
    default is blank so non-live rows and existing callers are unaffected."""
    _, display = split_path(s.get("name_cached"))
    display = display or sid[:8]
    name_w = max(8, NAME_W + 2 * GUIDE_DEPTH - depth * GUIDE_DEPTH)
    if len(display) > name_w:
        display = display[: name_w - 1] + "…"
    age = fmt_age(s.get("last_active_at"))
    tokens = fmt_tokens(s.get("tokens_estimate", 0))
    pct = fmt_pct(s.get("tokens_window_pct", 0))
    msgs = str(s.get("message_count", 0))
    prompt = (s.get("first_prompt") or "").replace("\n", " ")[:40]
    return glyph + f"{display:<{name_w}}" + _stat_suffix(age, tokens, pct, msgs, "msgs", prompt)
```

Change `_column_header` (line 67) to reserve the same leading cells:

```python
def _column_header() -> str:
    """Header line whose labels sit above the stat columns. Pads to a depth-2
    leaf's absolute stat offset (GLYPH_W glyph cells + 2 levels of guide ×
    GUIDE_DEPTH + NAME_W)."""
    name_region = NAME_W + 2 * GUIDE_DEPTH
    return " " * GLYPH_W + f"{'NAME':<{name_region}}" + _stat_suffix("AGE", "~TOK", "CTX", "MSGS", "    ", "FIRST PROMPT")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui_live.py test/test_tui.py -q`
Expected: PASS (new tests pass; existing `test_tui.py` still green — no test asserts exact `_row_label`/`_column_header` strings).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_live.py
git commit -m "feat: live-state glyph column in TUI rows (spinner/idle/blank)"
```

---

## Task 9: TUI wiring — timers, poll, node map, subtitle, visibility repopulate

**Files:**
- Modify: `bin/_pkg/tui.py` (`__init__` ~366; `on_mount` ~430; `_populate` ~493-582; new methods)

- [ ] **Step 1: Add live-state fields to `__init__`**

After `self._scanned = False` (line ~379):

```python
        # Live-session state: sid -> "working"|"idle", refreshed by _poll_live.
        self._live_states: dict[str, str] = {}
        self._spinner_frame: int = 0
        # sid -> (TreeNode, child_depth) for in-place glyph updates without a
        # full rebuild. Rebuilt by _populate.
        self._row_nodes: dict[str, tuple] = {}
```

- [ ] **Step 2: Resolve the live-registry path (helper)**

Add next to `_claude_dir` (line ~427):

```python
    def _live_path(self) -> str:
        from . import live as _live
        return os.environ.get("SESSION_EXPLORER_LIVE") or _live.default_path_for(self._index_path)
```

- [ ] **Step 3: In `_populate`, build the `sid → node` map, pass `live_ids`, render glyphs, and show the active count**

- Reset the map at the top of `_populate` (after `self._tree.clear()`, line ~495): `self._row_nodes = {}`
- Pass live ids into the tree build (line ~498):

```python
        tree = build_nested_tree(
            data, fs_data, include_unnamed=self._show_unnamed,
            live_ids=set(self._live_states),
        )
```

- In the inner `render` function (line ~560), capture each leaf node and apply the glyph:

```python
        def render(parent, project_label, segments, node, child_depth):
            for sid, s in node["_sessions"]:
                if self._matches(sid, s):
                    glyph = _glyph(self._live_states.get(sid), self._spinner_frame)
                    leaf = parent.add_leaf(_row_label(sid, s, child_depth, glyph),
                                           data={"sid": sid, **s})
                    self._row_nodes[sid] = (leaf, child_depth)
            for name in sorted(node["_folders"]):
                child = node["_folders"][name]
                child_segs = segments + [name]
                folder_node = parent.add(
                    f"{name}/", expand=True,
                    data={"project": project_label, "segments": child_segs},
                )
                render(folder_node, project_label, child_segs, child, child_depth + 1)
```

- Add the active count to the subtitle. Replace the subtitle block (lines ~511-514):

```python
        active = len(self._live_states)
        active_suffix = f" · ● {active} active" if active else ""
        if unnamed_hidden:
            self.sub_title = (f"{total} sessions across {len(tree)} projects · "
                              f"{unnamed_hidden} unnamed hidden (u){active_suffix}")
        else:
            self.sub_title = f"{total} sessions across {len(tree)} projects{active_suffix}"
```

- [ ] **Step 4: Start the timers in `on_mount`**

At the end of `on_mount` (after the retention/help block, line ~458):

```python
        # Live-session indicator: poll the registry, then animate working rows.
        self._poll_live()
        self.set_interval(LIVE_POLL_INTERVAL, self._poll_live)
        self.set_interval(SPINNER_INTERVAL, self._tick_spinner)
```

- [ ] **Step 5: Implement `_poll_live` and `_tick_spinner`**

Add these methods to the `App` subclass (near `action_rescan`):

```python
    def _poll_live(self) -> None:
        """Refresh live-session state from the registry (called on a timer).

        A change that alters row *visibility* — a live unnamed session appearing
        or a surfaced one dying — needs a full repopulate (tree membership
        changed); a pure state change only needs glyph relabeling.
        """
        from . import live as _live
        try:
            new_states = _live.poll(self._live_path())
        except Exception:
            return  # never let the indicator break the UI
        if new_states == self._live_states:
            return
        old = self._live_states
        self._live_states = new_states
        if self._visibility_changed(old, new_states):
            self._populate()
        else:
            self._relabel_live_rows()

    def _visibility_changed(self, old: dict, new: dict) -> bool:
        """True if any session whose membership depends on liveness flipped.

        Only unnamed sessions are conditionally visible, and only while not
        showing all unnamed. A named session is always present regardless of
        live state, so its appearance never forces a repopulate."""
        if self._show_unnamed:
            return False
        data = _index.load(self._index_path)
        sessions = data.get("sessions", {})
        flipped = set(old) ^ set(new)  # sids that entered or left the live set
        for sid in flipped:
            s = sessions.get(sid)
            if s is not None and not s.get("name_cached"):
                return True  # an unnamed session entered/left → membership change
        return False

    def _relabel_live_rows(self) -> None:
        """Rewrite glyphs for all rows that are (or just stopped being) live,
        without rebuilding the tree."""
        for sid, (leaf, depth) in self._row_nodes.items():
            data = leaf.data or {}
            glyph = _glyph(self._live_states.get(sid), self._spinner_frame)
            leaf.set_label(_row_label(sid, data, depth, glyph))

    def _tick_spinner(self) -> None:
        """Advance the spinner frame and relabel only the working rows."""
        if not any(st == "working" for st in self._live_states.values()):
            return  # nothing animating → cheap no-op
        self._spinner_frame += 1
        for sid, state in self._live_states.items():
            node = self._row_nodes.get(sid)
            if node is None or state != "working":
                continue
            leaf, depth = node
            leaf.set_label(_row_label(sid, leaf.data or {}, depth,
                                      _glyph(state, self._spinner_frame)))
```

> **API check before running:** confirm the vendored Textual `TreeNode` exposes `set_label`. Run: `grep -n "def set_label\|def set_label\b\|label = \|@label" bin/_pkg/_vendor/textual/widgets/_tree.py`. If the method is named differently (e.g. assigning `node.label = ...` via a setter), use that form instead — the rest of the logic is unchanged.

- [ ] **Step 6: Manual smoke test (TUI is interactive — verify by eye)**

Drive the registry directly (no real Claude session needed):

```bash
export SESSION_EXPLORER_LIVE=/tmp/se-live-test.json
export SESSION_EXPLORER_INDEX=~/.claude/session-explorer-index.json
# Mark a real, named session in your index as 'working':
SID=$(python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.claude/session-explorer-index.json')));print(next(k for k,v in d['sessions'].items() if v.get('name_cached')))")
python3 bin/session-explorer live --event SessionStart --sid "$SID" --pid $$
python3 bin/session-explorer live --event UserPromptSubmit --sid "$SID"
python3 bin/session-explorer tui   # observe: that row shows a green animated spinner; subtitle shows "● 1 active"
# In another shell, flip it to idle and watch it become a dim ○ within ~2s:
python3 bin/session-explorer live --event Stop --sid "$SID"
```

Expected: the named row animates while "working", switches to a steady dim `○` within ~2s of the `Stop`, and the subtitle shows `● 1 active`. `Ctrl-C`/`q` to exit; `rm /tmp/se-live-test.json*`.

- [ ] **Step 7: Run the full Python suite**

Run: `python3 -m pytest test/ -q`
Expected: PASS (all green).

- [ ] **Step 8: Commit**

```bash
git add bin/_pkg/tui.py
git commit -m "feat: live-session indicator wiring (timers, poll, animation, subtitle)"
```

---

## Task 10: Document in SPEC.md + finalize

**Files:**
- Modify: `SPEC.md`
- Modify: `docs/superpowers/specs/2026-05-29-active-session-indicator-design.md` (status)

- [ ] **Step 1: Add a feature section to `SPEC.md`**

Add a "Live-session indicator" subsection capturing the load-bearing decisions so the spec stays authoritative (per CLAUDE.md). Include, concisely: the registry file `~/.claude/session-explorer-live.json` (volatile, separate from index, never touched by `--gc`); the hook events and their state transitions; PID + 24h TTL death detection (and the spike outcome from Task 1); the working/idle/inactive glyph treatment; the "live sessions surface even when unnamed" rule; and the tunables table. Cross-reference the design doc.

- [ ] **Step 2: Update the design doc status line**

Change `**Status:** Approved (brainstorm); pending implementation plan` → `**Status:** Implemented (<branch> / PR #<n>)`.

- [ ] **Step 3: Run both full suites**

Run: `python3 -m pytest test/ -q && bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: PASS (both suites green).

- [ ] **Step 4: Commit**

```bash
git add SPEC.md docs/superpowers/specs/2026-05-29-active-session-indicator-design.md
git commit -m "docs: document live-session indicator in SPEC.md"
```

---

## Self-Review

**Spec coverage (design doc → tasks):**
- §1 registry file → Task 2 (schema, load/mutate, record_event).
- §2 hook flow / state machine → Task 4 (CLI), Task 5 (dispatcher), Task 6 (registration).
- §3 TUI rendering + timers + visibility-change repopulate → Task 8 (glyph), Task 9 (timers/poll/repopulate).
- §4 death detection (PID + TTL) → Task 1 (spike), Task 3 (`poll`/`_alive`).
- §5 install/settings → Task 6.
- §6 edge cases (live unnamed surfaced; stale-after-reboot self-heal; launcher session working) → Task 7 (surface unnamed), Task 3 (prune dead on poll), inherent.
- §7 testing → tests in Tasks 2,3,4,5,6,7,8 + suites in 9,10.
- Tunables table → Task 8 constants + Task 10 SPEC.

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The two intentionally instruction-only steps (Task 6 Step 4 install merge, Task 10 Step 1 SPEC prose) require matching existing file style that must be read first — each says exactly what to add and names the file's existing mechanism; no invented APIs.

**Type/name consistency:** `record_event(path, *, event, session_id, transcript_path, cwd, pid, now)`, `poll(path, *, now, ttl_seconds) -> dict[str,str]`, `default_path_for`, `WORKING`/`IDLE`, `_glyph(state, frame)`, `_row_label(sid, s, depth, glyph)`, `GLYPH_W`, `SPINNER_FRAMES`, `LIVE_POLL_INTERVAL`, `SPINNER_INTERVAL`, `_live_states`, `_row_nodes`, `_poll_live`, `_tick_spinner`, `_relabel_live_rows`, `_visibility_changed`, `build_nested_tree(..., live_ids=)` — all used consistently across tasks. `live_ids` defaults to `None`→`set()`. `_glyph` returns a constant-display-width prefix; `_row_label` default `glyph="  "` keeps existing callers/tests valid.

**Known runtime checks flagged inline:** (a) `TreeNode.set_label` name verified in Task 9 Step 5; (b) PID-capture viability verified in Task 1 with a documented TTL-only fallback that touches only Task 5.
