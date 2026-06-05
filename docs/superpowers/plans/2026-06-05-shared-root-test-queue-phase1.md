# Shared-root Test Queue — Phase 1 (Queue Core + CLI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a daemon-less FIFO queue plus a `queue-run`/`queue-status`/`queue-cancel` CLI that serialises access to the shared repo root for stack-dependent tests, syncing a worktree over root on acquire and enforcing the "root is exclusive-or" safety policy.

**Architecture:** A JSON ticket store (`queue_store.py`) keyed by named resource, using the same `flock` + temp-file-rename concurrency as `folder_store.py`, with pid-based reaping (like `live.py`) so a crashed holder never deadlocks the queue. A lease module (`queue.py`) wraps the lifecycle: take ticket → wait for turn → confirm no live root session → rsync worktree over root → run the command from root → release. The CLI exposes this. Phase 1 derives the shared root from the worktree path and uses sensible defaults; the per-project config file and TUI checkbox are Phase 2.

**Tech Stack:** Python 3.11+ (stdlib only: `fcntl`, `json`, `tempfile`, `subprocess`, `shlex`), `rsync`/`git` as system tools, pytest (+ pytest-asyncio already configured). No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-06-05-shared-root-test-queue-design.md` (components §1, §3, §4, §5; build order step 1).

**Out of scope (later phases):** per-project config file & opt-in flag (§2), TUI dialog checkbox & queue pane (§6), PreToolUse hook & skill (§7). Phase 1's `queue-run` therefore takes its resource/root as flags/derivation, not from a config file.

---

## File Structure

- **Create `bin/_pkg/queue_store.py`** — FIFO ticket store. One responsibility: persist and order tickets per resource, atomically and crash-safely. Mirrors `folder_store.py`.
- **Create `bin/_pkg/queue.py`** — lease lifecycle + sync + the exclusive-or policy. One responsibility: orchestrate one `queue-run` invocation end to end. Depends on `queue_store`, `live`, `index`.
- **Modify `bin/_pkg/cli.py`** — add `queue-run`, `queue-status`, `queue-cancel` subparsers + handlers. Wiring only.
- **Create `test/test_queue_store.py`** — store unit tests.
- **Create `test/test_queue.py`** — lease/sync/policy unit tests.
- **Modify `test/test_cli.py`** — CLI smoke tests for the three subcommands.

### Data model (`queue_store.py`)

```json
{
  "version": 1,
  "resources": {
    "shared-docker-stack": {
      "seq": 7,
      "tickets": [
        {"ticket": 6, "sid": "01ABC", "cwd": "/repo/.claude/worktrees/feat",
         "command": "npm run test:e2e", "pid": 4321,
         "created_at": "2026-06-05T10:00:00+00:00"}
      ]
    }
  }
}
```

- `seq` is a per-resource monotonic counter; it only ever increases, so ticket numbers are never reused even after reaping.
- **Holder** = the live ticket (pid alive) with the lowest `ticket` value.
- Dead-pid tickets are reaped inside every `mutate`.

---

## Task 1: Queue store — load/save/mutate skeleton

**Files:**
- Create: `bin/_pkg/queue_store.py`
- Test: `test/test_queue_store.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for _pkg.queue_store — atomic FIFO ticket queue."""

import threading

from _pkg import queue_store


def test_load_missing_returns_default(tmp_path):
    assert queue_store.load(str(tmp_path / "absent.json")) == {
        "version": 1, "resources": {}
    }


def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "q.json")
    payload = {"version": 1, "resources": {"r": {"seq": 0, "tickets": []}}}
    queue_store.save(path, payload)
    assert queue_store.load(path) == payload


def test_save_writes_via_temp_rename(tmp_path):
    path = str(tmp_path / "q.json")
    queue_store.save(path, {"version": 1, "resources": {}})
    assert list(tmp_path.glob("*.tmp")) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.queue_store'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Daemon-less FIFO ticket queue for serialising access to a shared resource.

Schema: {"version": 1, "resources": {resource: {"seq": int, "tickets": [t,...]}}}
ticket = {"ticket": int, "sid": str, "cwd": str, "command": str,
          "pid": int, "created_at": iso8601}

Holder = the live ticket (pid alive) with the lowest "ticket" number. A crashed
holder's ticket is reaped on the next mutate (pid no longer running), so the
queue never deadlocks. Concurrency mirrors folder_store: read LOCK_SH; mutate
LOCK_EX on a sidecar .lock + temp-file + atomic rename.
"""

import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

_DEFAULT: Dict[str, Any] = {"version": 1, "resources": {}}


def default_path_for(index_path: str) -> str:
    """Return the queue-store path that sits alongside `index_path`."""
    d = os.path.dirname(os.path.abspath(index_path)) or "."
    return os.path.join(d, "session-explorer-queue.json")


def load(path: str) -> dict:
    if not os.path.exists(path):
        return _DEFAULT.copy() | {"resources": {}}
    with open(path, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            return json.load(f)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def save(path: str, data: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-queue-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def mutate(path: str, fn: Callable[[dict], dict]) -> dict:
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)) or ".", exist_ok=True)
    with open(lock_path, "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            data = load(path)
            data = fn(data)
            save(path, data)
            return data
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_store.py test/test_queue_store.py
git commit -m "feat(queue): atomic flock'd ticket store skeleton"
```

---

## Task 2: Queue store — take_ticket / holder / waiters

**Files:**
- Modify: `bin/_pkg/queue_store.py`
- Test: `test/test_queue_store.py`

- [ ] **Step 1: Write the failing test**

```python
def _alive_all(pid):
    return True


def test_take_ticket_assigns_monotonic_numbers(tmp_path):
    path = str(tmp_path / "q.json")
    t1 = queue_store.take_ticket(path, "r", sid="a", cwd="/wt/a",
                                 command="x", pid=1, is_alive=_alive_all)
    t2 = queue_store.take_ticket(path, "r", sid="b", cwd="/wt/b",
                                 command="y", pid=2, is_alive=_alive_all)
    assert (t1, t2) == (1, 2)


def test_holder_is_lowest_live_ticket(tmp_path):
    path = str(tmp_path / "q.json")
    queue_store.take_ticket(path, "r", sid="a", cwd="/wt/a", command="x",
                            pid=1, is_alive=_alive_all)
    queue_store.take_ticket(path, "r", sid="b", cwd="/wt/b", command="y",
                            pid=2, is_alive=_alive_all)
    h = queue_store.holder(path, "r", is_alive=_alive_all)
    assert h["ticket"] == 1 and h["sid"] == "a"


def test_waiters_returns_live_tickets_in_order(tmp_path):
    path = str(tmp_path / "q.json")
    queue_store.take_ticket(path, "r", sid="a", cwd="/wt/a", command="x",
                            pid=1, is_alive=_alive_all)
    queue_store.take_ticket(path, "r", sid="b", cwd="/wt/b", command="y",
                            pid=2, is_alive=_alive_all)
    order = [t["sid"] for t in queue_store.waiters(path, "r", is_alive=_alive_all)]
    assert order == ["a", "b"]


def test_holder_none_for_empty_resource(tmp_path):
    path = str(tmp_path / "q.json")
    assert queue_store.holder(path, "r", is_alive=_alive_all) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: FAIL with `AttributeError: module '_pkg.queue_store' has no attribute 'take_ticket'`

- [ ] **Step 3: Write minimal implementation**

Append to `bin/_pkg/queue_store.py`:

```python
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_is_alive(pid: Optional[int]) -> bool:
    from .live import _pid_alive
    return _pid_alive(pid)


def _resource(data: dict, resource: str) -> dict:
    return data.setdefault("resources", {}).setdefault(
        resource, {"seq": 0, "tickets": []})


def _live_tickets(data: dict, resource: str, is_alive: Callable) -> List[dict]:
    res = data.get("resources", {}).get(resource, {"seq": 0, "tickets": []})
    live = [t for t in res.get("tickets", []) if is_alive(t.get("pid"))]
    return sorted(live, key=lambda t: t["ticket"])


def take_ticket(path: str, resource: str, *, sid: str, cwd: str, command: str,
                pid: int, is_alive: Callable = _default_is_alive) -> int:
    assigned = {"n": 0}

    def mutator(data: dict) -> dict:
        res = _resource(data, resource)
        # Reap dead tickets so a stale queue never blocks live ones.
        res["tickets"] = [t for t in res["tickets"] if is_alive(t.get("pid"))]
        res["seq"] = int(res.get("seq", 0)) + 1
        assigned["n"] = res["seq"]
        res["tickets"].append({
            "ticket": res["seq"], "sid": sid, "cwd": cwd,
            "command": command, "pid": pid, "created_at": _now_iso(),
        })
        return data

    mutate(path, mutator)
    return assigned["n"]


def holder(path: str, resource: str,
           is_alive: Callable = _default_is_alive) -> Optional[dict]:
    live = _live_tickets(load(path), resource, is_alive)
    return live[0] if live else None


def waiters(path: str, resource: str,
            is_alive: Callable = _default_is_alive) -> List[dict]:
    return _live_tickets(load(path), resource, is_alive)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_store.py test/test_queue_store.py
git commit -m "feat(queue): ticket allocation + holder/waiters ordering"
```

---

## Task 3: Queue store — release, pid reaping, snapshot, concurrency

**Files:**
- Modify: `bin/_pkg/queue_store.py`
- Test: `test/test_queue_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_release_removes_ticket(tmp_path):
    path = str(tmp_path / "q.json")
    t = queue_store.take_ticket(path, "r", sid="a", cwd="/wt/a", command="x",
                                pid=1, is_alive=_alive_all)
    queue_store.release(path, "r", t)
    assert queue_store.holder(path, "r", is_alive=_alive_all) is None


def test_dead_pid_ticket_is_reaped_on_next_take(tmp_path):
    path = str(tmp_path / "q.json")
    dead = {7}

    def is_alive(pid):
        return pid not in dead

    queue_store.take_ticket(path, "r", sid="dead", cwd="/wt/d", command="x",
                            pid=7, is_alive=is_alive)
    # The crashed holder (pid 7) is reaped when the next ticket is taken.
    queue_store.take_ticket(path, "r", sid="live", cwd="/wt/l", command="y",
                            pid=8, is_alive=is_alive)
    h = queue_store.holder(path, "r", is_alive=is_alive)
    assert h["sid"] == "live"


def test_holder_skips_dead_pid_even_with_lower_ticket(tmp_path):
    path = str(tmp_path / "q.json")

    def is_alive(pid):
        return pid != 1

    queue_store.take_ticket(path, "r", sid="dead", cwd="/wt/d", command="x",
                            pid=1, is_alive=is_alive)
    queue_store.take_ticket(path, "r", sid="live", cwd="/wt/l", command="y",
                            pid=2, is_alive=is_alive)
    assert queue_store.holder(path, "r", is_alive=is_alive)["sid"] == "live"


def test_snapshot_lists_all_resources(tmp_path):
    path = str(tmp_path / "q.json")
    queue_store.take_ticket(path, "r1", sid="a", cwd="/wt/a", command="x",
                            pid=1, is_alive=_alive_all)
    queue_store.take_ticket(path, "r2", sid="b", cwd="/wt/b", command="y",
                            pid=2, is_alive=_alive_all)
    snap = queue_store.snapshot(path, is_alive=_alive_all)
    assert set(snap) == {"r1", "r2"}
    assert snap["r1"][0]["sid"] == "a"


def test_concurrent_take_ticket_no_duplicate_numbers(tmp_path):
    path = str(tmp_path / "q.json")
    seen = []
    lock = threading.Lock()

    def worker(n):
        for _ in range(25):
            t = queue_store.take_ticket(path, "r", sid=f"s{n}", cwd="/wt",
                                        command="x", pid=1000 + n,
                                        is_alive=_alive_all)
            with lock:
                seen.append(t)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == len(set(seen)) == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: FAIL with `AttributeError: module '_pkg.queue_store' has no attribute 'release'`

- [ ] **Step 3: Write minimal implementation**

Append to `bin/_pkg/queue_store.py`:

```python
def release(path: str, resource: str, ticket: int,
            is_alive: Callable = _default_is_alive) -> None:
    def mutator(data: dict) -> dict:
        res = data.get("resources", {}).get(resource)
        if res is not None:
            res["tickets"] = [
                t for t in res["tickets"]
                if t.get("ticket") != ticket and is_alive(t.get("pid"))
            ]
        return data

    mutate(path, mutator)


def snapshot(path: str,
             is_alive: Callable = _default_is_alive) -> Dict[str, List[dict]]:
    """Return {resource: [live tickets, lowest-first]} for every resource that
    currently has at least one live ticket."""
    data = load(path)
    out: Dict[str, List[dict]] = {}
    for resource in data.get("resources", {}):
        live = _live_tickets(data, resource, is_alive)
        if live:
            out[resource] = live
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_store.py test/test_queue_store.py
git commit -m "feat(queue): release, pid reaping, snapshot"
```

---

## Task 4: Lease helpers — shared_root_for, root_is_dirty, sync

**Files:**
- Create: `bin/_pkg/queue.py`
- Test: `test/test_queue.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for _pkg.queue — lease lifecycle, sync, exclusive-or policy."""

import os
import subprocess

from _pkg import queue


def test_shared_root_for_strips_worktree_marker():
    cwd = "/Users/jl/proj/myrepo/.claude/worktrees/feat-x"
    assert queue.shared_root_for(cwd) == "/Users/jl/proj/myrepo"


def test_shared_root_for_returns_cwd_when_not_worktree():
    cwd = "/Users/jl/proj/myrepo"
    assert queue.shared_root_for(cwd) == "/Users/jl/proj/myrepo"


def test_root_is_dirty_true_for_uncommitted_changes(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "f.txt").write_text("x")
    assert queue.root_is_dirty(str(root)) is True


def test_root_is_dirty_false_for_clean_tree(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "f.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    assert queue.root_is_dirty(str(root)) is False


def test_sync_worktree_to_root_overlays_files(tmp_path):
    wt = tmp_path / "wt"
    root = tmp_path / "root"
    wt.mkdir()
    root.mkdir()
    (wt / "src.py").write_text("new")
    (root / "stale.py").write_text("old")  # not in worktree
    queue.sync_worktree_to_root(str(wt), str(root))
    assert (root / "src.py").read_text() == "new"
    # Overlay (no --delete): pre-existing root files remain in Phase 1.
    assert (root / "stale.py").exists()


def test_sync_excludes_git_dir(tmp_path):
    wt = tmp_path / "wt"
    root = tmp_path / "root"
    (wt / ".git").mkdir(parents=True)
    (wt / ".git" / "HEAD").write_text("ref")
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ROOTHEAD")
    queue.sync_worktree_to_root(str(wt), str(root))
    # Root's own .git must be untouched by the sync.
    assert (root / ".git" / "HEAD").read_text() == "ROOTHEAD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_queue.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.queue'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Lease lifecycle for the shared-root test sandbox.

A `queue-run` invocation: take a ticket, wait for our turn in the FIFO, confirm
no live session is working directly in root (the "exclusive-or" policy), rsync
our worktree over root, run the command from root, then release the ticket.

Phase 1 derives the shared root from the worktree path and uses overlay rsync
(no --delete) excluding only .git; the per-project config file that tunes
resource name, excludes and the opt-in flag arrives in Phase 2.
"""

import os
import shlex
import subprocess
import sys
import time
from typing import Callable, List, Optional

from . import queue_store

_WORKTREE_MARKER = "/.claude/worktrees/"
_SANDBOX_SENTINEL = ".session-explorer-sandbox"


def shared_root_for(cwd: str) -> str:
    """The repo root that backs `cwd`. A Claude worktree lives at
    `<root>/.claude/worktrees/<name>`; everything else is its own root."""
    if _WORKTREE_MARKER in cwd:
        return cwd.split(_WORKTREE_MARKER, 1)[0]
    return cwd.rstrip("/") or cwd


def root_is_dirty(root: str) -> bool:
    """True if `root` has uncommitted git changes. Treats a non-git or
    git-less environment as 'not dirty' (nothing to protect)."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root,
            capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return False
    if out.returncode != 0:
        return False
    return bool(out.stdout.strip())


def sync_worktree_to_root(worktree: str, root: str) -> None:
    """Overlay the worktree onto root with rsync, excluding .git. Trailing
    slashes make rsync copy contents (not the dir itself)."""
    src = worktree.rstrip("/") + "/"
    dst = root.rstrip("/") + "/"
    subprocess.run(
        ["rsync", "-a", "--exclude", ".git", src, dst], check=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_queue.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue.py test/test_queue.py
git commit -m "feat(queue): shared-root derivation, dirty check, rsync sync"
```

---

## Task 5: Exclusive-or policy — root_is_busy

**Files:**
- Modify: `bin/_pkg/queue.py`
- Test: `test/test_queue.py`

The check cross-references the live store (which sessions are alive) with the
index (each session's cwd via `project_path`): root is busy if some *other*
live session's cwd equals the shared root.

- [ ] **Step 1: Write the failing test**

```python
def _write_index(tmp_path, sessions):
    import json
    p = tmp_path / "index.json"
    p.write_text(json.dumps({"version": 2, "sessions": sessions}))
    return str(p)


def test_root_is_busy_when_live_session_cwd_is_root(tmp_path, monkeypatch):
    root = "/Users/jl/proj/myrepo"
    idx = _write_index(tmp_path, {
        "ROOTSID": {"project_path": root},
    })
    monkeypatch.setattr(queue.live, "poll", lambda path: {"ROOTSID": "idle"})
    blocker = queue.root_is_busy(idx, "live.json", root, my_cwd=root + "/.claude/worktrees/x")
    assert blocker == "ROOTSID"


def test_root_is_not_busy_when_no_live_root_session(tmp_path, monkeypatch):
    root = "/Users/jl/proj/myrepo"
    idx = _write_index(tmp_path, {
        "WTSID": {"project_path": root + "/.claude/worktrees/x"},
    })
    monkeypatch.setattr(queue.live, "poll", lambda path: {"WTSID": "working"})
    assert queue.root_is_busy(idx, "live.json", root,
                              my_cwd=root + "/.claude/worktrees/x") is None


def test_root_is_busy_ignores_self(tmp_path, monkeypatch):
    # A session running queue-run FROM root must not see itself as a blocker.
    root = "/Users/jl/proj/myrepo"
    idx = _write_index(tmp_path, {
        "ME": {"project_path": root},
    })
    monkeypatch.setattr(queue.live, "poll", lambda path: {"ME": "working"})
    assert queue.root_is_busy(idx, "live.json", root, my_cwd=root) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_queue.py -q`
Expected: FAIL with `AttributeError: module '_pkg.queue' has no attribute 'live'` (the import is added next)

- [ ] **Step 3: Write minimal implementation**

Add the imports near the top of `bin/_pkg/queue.py` (under the existing `from . import queue_store`):

```python
from . import index as _index
from . import live
```

Append the function:

```python
def root_is_busy(index_path: str, live_path: str, shared_root: str,
                 my_cwd: str) -> Optional[str]:
    """Return the session id of a live session working directly in `shared_root`
    (other than the caller), or None if root is free for the sandbox.

    Cross-references live.poll() (which sids are alive) with the index
    (each sid's cwd via project_path). Self is identified by matching cwd so a
    root session running queue-run doesn't block itself.
    """
    root = os.path.abspath(shared_root)
    mine = os.path.abspath(my_cwd)
    try:
        live_sids = live.poll(live_path)
    except Exception:
        return None  # fail open: never wedge a test run on a polling error
    sessions = _index.load(index_path).get("sessions", {})
    for sid in live_sids:
        cwd = sessions.get(sid, {}).get("project_path", "")
        if not cwd:
            continue
        if os.path.abspath(cwd) == root and root != mine:
            return sid
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_queue.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue.py test/test_queue.py
git commit -m "feat(queue): exclusive-or root_is_busy policy check"
```

---

## Task 6: Lease orchestration — run_queued

**Files:**
- Modify: `bin/_pkg/queue.py`
- Test: `test/test_queue.py`

`run_queued` ties it together. Clock and sleeper are injected for deterministic
tests. The dirty-root transition guard is skipped once a `.session-explorer-sandbox`
sentinel exists in root (root is already in sandbox mode).

- [ ] **Step 1: Write the failing test**

```python
def test_run_queued_happy_path_runs_command_from_root(tmp_path):
    root = tmp_path / "root"
    wt = tmp_path / "wt"
    root.mkdir()
    wt.mkdir()
    (wt / "marker.txt").write_text("hi")
    qpath = str(tmp_path / "q.json")
    idx = _write_index(tmp_path, {})  # no live sessions

    rc = queue.run_queued(
        command=["sh", "-c", "test -f marker.txt && echo ok > ran.txt"],
        resource="r", worktree=str(wt), shared_root=str(root),
        index_path=idx, live_path="live.json", queue_path=qpath,
        timeout=5.0, poll_interval=0.01, do_sync=True,
        is_alive=lambda pid: True,
    )
    assert rc == 0
    assert (root / "marker.txt").exists()      # synced
    assert (root / "ran.txt").read_text().strip() == "ok"  # ran from root
    assert queue.queue_store.holder(qpath, "r", is_alive=lambda p: True) is None


def test_run_queued_refuses_dirty_root_without_sentinel(tmp_path, monkeypatch):
    root = tmp_path / "root"
    wt = tmp_path / "wt"
    root.mkdir(); wt.mkdir()
    monkeypatch.setattr(queue, "root_is_dirty", lambda r: True)
    rc = queue.run_queued(
        command=["true"], resource="r", worktree=str(wt), shared_root=str(root),
        index_path=_write_index(tmp_path, {}), live_path="live.json",
        queue_path=str(tmp_path / "q.json"), timeout=5.0, poll_interval=0.01,
        do_sync=True, is_alive=lambda pid: True,
    )
    assert rc == 3  # refusal exit code


def test_run_queued_skips_dirty_guard_when_sentinel_present(tmp_path, monkeypatch):
    root = tmp_path / "root"; wt = tmp_path / "wt"
    root.mkdir(); wt.mkdir()
    (root / ".session-explorer-sandbox").write_text("")  # already sandbox
    monkeypatch.setattr(queue, "root_is_dirty", lambda r: True)
    rc = queue.run_queued(
        command=["true"], resource="r", worktree=str(wt), shared_root=str(root),
        index_path=_write_index(tmp_path, {}), live_path="live.json",
        queue_path=str(tmp_path / "q.json"), timeout=5.0, poll_interval=0.01,
        do_sync=True, is_alive=lambda pid: True,
    )
    assert rc == 0


def test_run_queued_times_out_when_root_stays_busy(tmp_path, monkeypatch):
    root = tmp_path / "root"; wt = tmp_path / "wt"
    root.mkdir(); wt.mkdir()
    monkeypatch.setattr(queue, "root_is_busy", lambda *a, **k: "OTHER")
    fake = {"t": 0.0}

    def clock():
        return fake["t"]

    def sleeper(dt):
        fake["t"] += dt

    rc = queue.run_queued(
        command=["true"], resource="r", worktree=str(wt), shared_root=str(root),
        index_path=_write_index(tmp_path, {}), live_path="live.json",
        queue_path=str(tmp_path / "q.json"), timeout=0.05, poll_interval=0.01,
        do_sync=True, is_alive=lambda pid: True, clock=clock, sleeper=sleeper,
    )
    assert rc == 4  # timeout exit code
    # Ticket released even on timeout.
    assert queue.queue_store.holder(str(tmp_path / "q.json"), "r",
                                    is_alive=lambda p: True) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_queue.py -q`
Expected: FAIL with `AttributeError: module '_pkg.queue' has no attribute 'run_queued'`

- [ ] **Step 3: Write minimal implementation**

Append to `bin/_pkg/queue.py`:

```python
# Exit codes (distinct so callers/hooks can react):
EXIT_REFUSE_DIRTY = 3
EXIT_TIMEOUT = 4


def _ensure_sentinel(root: str) -> None:
    try:
        open(os.path.join(root, _SANDBOX_SENTINEL), "a").close()
    except OSError:
        pass


def run_queued(*, command: List[str], resource: str, worktree: str,
               shared_root: str, index_path: str, live_path: str,
               queue_path: str, timeout: float = 1800.0,
               poll_interval: float = 1.0, do_sync: bool = True,
               is_alive: Callable = queue_store._default_is_alive,
               clock: Callable[[], float] = time.monotonic,
               sleeper: Callable[[float], None] = time.sleep) -> int:
    """Acquire the lease, (optionally) sync, run `command` from root, release."""
    pid = os.getpid()
    sid = os.environ.get("CLAUDE_SESSION_ID", "")
    ticket = queue_store.take_ticket(
        queue_path, resource, sid=sid, cwd=worktree,
        command=shlex.join(command), pid=pid, is_alive=is_alive)
    start = clock()
    try:
        while True:
            h = queue_store.holder(queue_path, resource, is_alive=is_alive)
            our_turn = h is not None and h.get("ticket") == ticket
            if our_turn:
                blocker = root_is_busy(index_path, live_path, shared_root, worktree)
                if blocker is None:
                    break
                print(f"queue: waiting — root in use by live session {blocker}",
                      file=sys.stderr)
            if clock() - start > timeout:
                print("queue: timed out waiting for the shared root",
                      file=sys.stderr)
                return EXIT_TIMEOUT
            sleeper(poll_interval)

        if do_sync:
            sentinel = os.path.join(shared_root, _SANDBOX_SENTINEL)
            if not os.path.exists(sentinel) and root_is_dirty(shared_root):
                print("queue: refusing — root has uncommitted changes the "
                      "sandbox would overwrite; stash or commit them first.",
                      file=sys.stderr)
                return EXIT_REFUSE_DIRTY
            sync_worktree_to_root(worktree, shared_root)
            _ensure_sentinel(shared_root)

        return subprocess.run(command, cwd=shared_root).returncode
    finally:
        queue_store.release(queue_path, resource, ticket, is_alive=is_alive)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_queue.py -q`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue.py test/test_queue.py
git commit -m "feat(queue): run_queued lease orchestration"
```

---

## Task 7: CLI — queue-status

**Files:**
- Modify: `bin/_pkg/cli.py` (parser in `build_parser` ~line 36-76; dispatch in `main` ~line 241-256)
- Test: `test/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_cli.py`:

```python
def test_queue_status_empty_json(tmp_path):
    qpath = tmp_path / "q.json"
    result = subprocess.run(
        [_BIN, "queue-status", "--json"],
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE": str(qpath)},
    )
    assert result.returncode == 0, result.stderr
    import json
    assert json.loads(result.stdout) == {}


def test_queue_status_human_when_empty(tmp_path):
    qpath = tmp_path / "q.json"
    result = subprocess.run(
        [_BIN, "queue-status"],
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE": str(qpath)},
    )
    assert result.returncode == 0, result.stderr
    assert "no active queues" in result.stdout.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_cli.py -k queue_status -q`
Expected: FAIL (non-zero exit / `invalid choice: 'queue-status'`)

- [ ] **Step 3: Write minimal implementation**

In `bin/_pkg/cli.py`, add a `_queue_path()` helper next to `_index_path()` (after line 29):

```python
def _queue_path() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_QUEUE")
    if env_override:
        return env_override
    from . import queue_store as _qs
    return _qs.default_path_for(_index_path())
```

In `build_parser`, after the `live` subparser block (after line 58), add:

```python
    qstatus_p = sub.add_parser("queue-status", help="Show shared-resource queues.")
    qstatus_p.add_argument("--json", action="store_true",
                           help="Emit machine-readable JSON (used by the TUI pane).")
```

Add the handler function (near `_cmd_list`):

```python
def _cmd_queue_status(args) -> int:
    import json
    from . import queue_store as _qs
    snap = _qs.snapshot(_queue_path())
    if args.json:
        print(json.dumps(snap))
        return 0
    if not snap:
        print("No active queues.")
        return 0
    for resource, tickets in sorted(snap.items()):
        h = tickets[0]
        hname = h.get("sid") or "?"
        print(f"{resource}: held by {hname} ({len(tickets)} in queue)")
        for i, t in enumerate(tickets[1:], start=1):
            print(f"  {i}. waiting: {t.get('sid') or '?'}")
    return 0
```

In `main`, add dispatch (after the `live` branch, ~line 244):

```python
    if args.cmd == "queue-status":
        return _cmd_queue_status(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_cli.py -k queue_status -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(cli): queue-status subcommand"
```

---

## Task 8: CLI — queue-run

**Files:**
- Modify: `bin/_pkg/cli.py`
- Test: `test/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_cli.py`:

```python
def test_queue_run_executes_command_from_root(tmp_path):
    root = tmp_path / "root"
    wt = tmp_path / "wt"
    root.mkdir(); wt.mkdir()
    (wt / "marker.txt").write_text("hi")
    qpath = tmp_path / "q.json"
    idx = tmp_path / "index.json"
    idx.write_text('{"version": 2, "sessions": {}}')
    env = {
        **os.environ,
        "SESSION_EXPLORER_QUEUE": str(qpath),
        "SESSION_EXPLORER_INDEX": str(idx),
        "SESSION_EXPLORER_LIVE": str(tmp_path / "live.json"),
    }
    result = subprocess.run(
        [_BIN, "queue-run", "--resource", "r",
         "--root", str(root), "--worktree", str(wt),
         "--", "sh", "-c", "cat marker.txt"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "hi" in result.stdout
    assert (root / "marker.txt").exists()


def test_queue_run_propagates_command_exit_code(tmp_path):
    root = tmp_path / "root"; wt = tmp_path / "wt"
    root.mkdir(); wt.mkdir()
    qpath = tmp_path / "q.json"
    idx = tmp_path / "index.json"
    idx.write_text('{"version": 2, "sessions": {}}')
    env = {
        **os.environ,
        "SESSION_EXPLORER_QUEUE": str(qpath),
        "SESSION_EXPLORER_INDEX": str(idx),
        "SESSION_EXPLORER_LIVE": str(tmp_path / "live.json"),
    }
    result = subprocess.run(
        [_BIN, "queue-run", "--root", str(root), "--worktree", str(wt),
         "--no-sync", "--", "sh", "-c", "exit 7"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_cli.py -k queue_run -q`
Expected: FAIL (`invalid choice: 'queue-run'`)

- [ ] **Step 3: Write minimal implementation**

In `build_parser`, after the `queue-status` block, add:

```python
    qrun_p = sub.add_parser(
        "queue-run",
        help="Run a command against the shared root under the FIFO queue.")
    qrun_p.add_argument("--resource", default="shared-docker-stack",
                        help="Named resource to serialise on (default shared-docker-stack).")
    qrun_p.add_argument("--root", default=None,
                        help="Shared root path (default: derived from --worktree).")
    qrun_p.add_argument("--worktree", default=None,
                        help="Source worktree to sync (default: current directory).")
    qrun_p.add_argument("--timeout", type=float, default=1800.0,
                        help="Seconds to wait for the lease before giving up (default 1800).")
    qrun_p.add_argument("--poll-interval", type=float, default=1.0,
                        help="Seconds between queue/root polls (default 1).")
    qrun_p.add_argument("--no-sync", action="store_true",
                        help="Serialise only; do not rsync the worktree over root.")
    qrun_p.add_argument("command", nargs=argparse.REMAINDER,
                        help="Command to run (prefix with --).")
```

Add the handler:

```python
def _cmd_queue_run(args) -> int:
    from . import queue as _queue
    cmd = list(args.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("queue-run: no command given (use: queue-run ... -- CMD)", file=sys.stderr)
        return 2
    worktree = os.path.abspath(args.worktree or os.getcwd())
    root = os.path.abspath(args.root) if args.root else _queue.shared_root_for(worktree)
    return _queue.run_queued(
        command=cmd, resource=args.resource, worktree=worktree,
        shared_root=root, index_path=_index_path(), live_path=_live_path(),
        queue_path=_queue_path(), timeout=args.timeout,
        poll_interval=args.poll_interval, do_sync=not args.no_sync)
```

In `main`, add dispatch after `queue-status`:

```python
    if args.cmd == "queue-run":
        return _cmd_queue_run(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_cli.py -k queue_run -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(cli): queue-run subcommand"
```

---

## Task 9: CLI — queue-cancel

**Files:**
- Modify: `bin/_pkg/cli.py`
- Test: `test/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_cli.py`:

```python
def test_queue_cancel_removes_a_ticket(tmp_path):
    from _pkg import queue_store
    qpath = tmp_path / "q.json"
    t = queue_store.take_ticket(str(qpath), "r", sid="a", cwd="/wt",
                                command="x", pid=os.getpid())
    result = subprocess.run(
        [_BIN, "queue-cancel", "--resource", "r", "--ticket", str(t)],
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE": str(qpath)},
    )
    assert result.returncode == 0, result.stderr
    assert queue_store.holder(str(qpath), "r") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_cli.py -k queue_cancel -q`
Expected: FAIL (`invalid choice: 'queue-cancel'`)

- [ ] **Step 3: Write minimal implementation**

In `build_parser`, after the `queue-run` block:

```python
    qcancel_p = sub.add_parser("queue-cancel", help="Drop a queue ticket.")
    qcancel_p.add_argument("--resource", required=True)
    qcancel_p.add_argument("--ticket", type=int, required=True)
```

Add the handler:

```python
def _cmd_queue_cancel(args) -> int:
    from . import queue_store as _qs
    _qs.release(_queue_path(), args.resource, args.ticket)
    print(f"Cancelled ticket {args.ticket} on {args.resource}.")
    return 0
```

In `main`, after `queue-run`:

```python
    if args.cmd == "queue-cancel":
        return _cmd_queue_cancel(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_cli.py -k queue_cancel -q`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(cli): queue-cancel subcommand"
```

---

## Task 10: Full suite + spec sync

**Files:**
- Modify: `SPEC.md`
- Modify: `CLAUDE.md` (load-bearing design decisions list)

- [ ] **Step 1: Run the whole Python suite**

Run: `python3 -m pytest test/ -q`
Expected: PASS (all existing tests + the new queue tests, no regressions)

- [ ] **Step 2: Manual smoke of the CLI**

```bash
SESSION_EXPLORER_QUEUE=/tmp/se-q.json bin/session-explorer queue-status
```
Expected: prints `No active queues.`

- [ ] **Step 3: Update SPEC.md**

Add a section documenting the shared-root test queue (resource-keyed FIFO ticket
store at `~/.claude/session-explorer-queue.json`, pid-based reaping, the
`queue-run`/`queue-status`/`queue-cancel` CLI, the sync-on-acquire-no-cleanup
model, and the exclusive-or root policy with the `.session-explorer-sandbox`
sentinel). Note Phase 1 status (core + CLI shipped; config file, TUI pane, and
hook deferred to Phases 2–3). Cross-reference the design spec at
`docs/superpowers/specs/2026-06-05-shared-root-test-queue-design.md`.

- [ ] **Step 4: Update CLAUDE.md**

Add a bullet to "Load-bearing design decisions":

> - **Shared-root test queue serialises the singleton root.** Stack-dependent
>   tests run via `queue-run`, which takes a FIFO ticket, waits its turn,
>   refuses to clobber a live root session (exclusive-or policy) or uncommitted
>   root changes, then rsyncs the worktree over root and runs from there. There
>   is no cleanup step — the next holder's sync is the reset. Don't reintroduce
>   copy-in/delete-out cleanup; it reintroduces the cross-agent wipe race.

- [ ] **Step 5: Commit**

```bash
git add SPEC.md CLAUDE.md
git commit -m "docs: document Phase 1 shared-root test queue in SPEC + CLAUDE"
```

---

## Notes & known limitations (Phase 1)

- **Overlay rsync, not mirror.** Phase 1 uses `rsync -a --exclude .git` (no
  `--delete`), so files that existed in root but not the worktree linger. This
  still fixes the overwrite/cleanup races. `--delete` plus configurable excludes
  (so `node_modules`/build dirs survive) is a Phase 2 concern alongside the
  config file.
- **Sentinel reset.** The `.session-explorer-sandbox` sentinel makes the
  dirty-root guard fire only on first entry into sandbox mode. Resetting it when
  a real root session resumes (so the guard protects fresh uncommitted work
  again) is deferred to Phase 2/3, where the config/dialog manages lifecycle.
- **Self-identification by cwd.** `root_is_busy` excludes the caller by matching
  cwd, not session id (Phase 1 doesn't reliably know its own sid). Two distinct
  live sessions both rooted at root is a rare, separately-dangerous case; Phase 2
  can tighten this once the dialog steers root work into worktrees.
- **No config/opt-in yet.** Phase 1 is driven entirely by explicit flags /
  derivation, usable via a `CLAUDE.md` instruction telling agents to wrap
  stack tests in `queue-run`. The opt-in flag, guarded-command patterns, and the
  hook that enforces them arrive in Phases 2–3.
```
