# Shared-Resource Queue Core + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the daemon-less, `flock`-reaped FIFO lease engine and its CLI (`queue-run` / `queue-status` / `queue-cancel`) so a class of commands can serialize access to a shared singleton resource (a bind-mounted repo root, a DB, a simulator, a license seat), with `sync` / `none` / `command` acquire strategies, the root-dir exclusive-or-with-live-session safety net, and `health` / `wait_for` probes — usable from the shell with only `CLAUDE.md` guidance (no TUI, no hooks).

**Architecture:** Mirrors the existing per-user JSON stores (`index.py` / `folder_store.py` / `live.py`): every store takes an explicit path, reads under `LOCK_SH`, mutates under `LOCK_EX` on a sidecar `.lock` + temp-file-rename. The queue is *the set of ticket files* on disk — no daemon remembers order; the holder is the lowest-numbered ticket whose owner process is still alive, proven by an `flock` liveness probe that survives `SIGKILL` and PID reuse. One `queue-run` process owns the whole lease lifecycle so release is guaranteed in a `finally`. A new shared canonical-project helper (`project_id.py`) keys queues/config/live-root matching by the repo's git *common dir* so all worktrees of a repo collapse to one identity.

**Tech Stack:** Python 3.11+ stdlib only (`fcntl.flock`, `subprocess`, `tempfile`, `hashlib`, `socket`, `signal`), external `rsync` + `git` (both already required by the project), vendored Textual untouched. Tests: `pytest` (sync; the suite runs `asyncio_mode = auto` but this phase has no async). No new runtime dependency.

This plan covers **only Build-order Phase 1** of `docs/superpowers/specs/2026-06-05-shared-root-test-queue-design.md` (Queue core + CLI). The TUI (Phase 2) and the awareness/enforcement hooks + cooperative skill (Phase 3) are separate plans. Where the spec describes TUI behavior (e.g. the §2 classification *dialog*, the §5.4 prevention layer, the §6 detection toast), Phase 1 supplies the underlying CLI mechanism and refuses with a printed instruction to edit config manually — the dialog wraps it later.

---

## Module map (what each new file owns)

| File | Responsibility |
|---|---|
| `bin/_pkg/project_id.py` | The shared canonical-project helper: git-common-dir identity hash, main-working-tree root path, subdir-resolving toplevel, root-cwd test. Used by queue identity, config keying, CLI resolution, live-root matching. |
| `bin/_pkg/queue_config.py` | Per-project resource declarations (`~/.claude/session-explorer-queue-config.json`), keyed by project-id. `kind` model, `resource_id` slug validation, CRUD. Mirrors `folder_store.py`. |
| `bin/_pkg/queue_store.py` | Queue core: ticket allocation under the queue `.lock`, publication-after-lock ordering, `flock` liveness probing + crash-reaping, holder selection, FIFO position, cancellation tombstones. |
| `bin/_pkg/qsync.py` | The `sync` strategy: exact rsync `--filter` construction, `--delete` dry-run deletion analysis, the protected-baseline classification gate, the per-resource sandbox marker. (Named `qsync` to avoid shadowing any stdlib/`sync` notion.) |
| `bin/_pkg/exclusive.py` | Root-dir exclusive-or policy: live-root-session detection via the live registry, the uncommitted-changes transition guard. |
| `bin/_pkg/probes.py` | `health` check + `wait_for` readiness probe (port / url / command). |
| `bin/_pkg/queue_run.py` | The single-process lease lifecycle orchestrator: take ticket → wait → [root-dir exclusive-or] → health → acquire → wait_for → run → release → finally-release. Signal handling, exit codes. |
| `bin/_pkg/cli.py` (modify) | Wire `queue-run` / `queue-status` / `queue-cancel` subcommands + path helpers + env overrides. |
| `test/test_project_id.py`, `test/test_queue_config.py`, `test/test_queue_store.py`, `test/test_qsync.py`, `test/test_exclusive.py`, `test/test_probes.py`, `test/test_queue_run.py`, `test/test_cli.py` (extend) | One test file per module. |
| `SPEC.md` (modify), `CLAUDE.md` (modify) | Keep the authoritative spec + guidance in sync in the same change. |

**On-disk layout (all under `~/.claude/`, per-user, never committed):**

```
session-explorer-queue-config.json                # resource declarations, keyed by project-id
session-explorer-queues/<project-id>/<resource-id>/
    .lock                                          # queue metadata lock (allocate/publish/cancel/reap)
    t0000000001-<sid>.json                         # one ticket file per participant (lifetime-flock'd)
    sandbox.marker                                 # present once the root-dir baseline is settled
    history/cancel-0000000001-<sid>.json           # cancellation tombstones + release-failure records
```

`<project-id>` = `project_id.project_id(cwd)`; `<resource-id>` = validated slug. The filesystem `path` of a resource is **stored as data inside config/tickets, never used as a path component** (no traversal, no nesting).

---

## Conventions for every task

- Run the full suite with `python3 -m pytest test/ -q`; a single file with `python3 -m pytest test/test_queue_store.py -q`.
- All stores take an **explicit path argument** (like `folder_store`/`live`) so tests use `tmp_path` — never the real `~/.claude`.
- Time is injected as `now: datetime` (UTC) where ordering/TTL matters, mirroring `live.py`.
- Commit after each task with the message shown in its final step.

---

### Task 1: Canonical project-id helper (`project_id.py`)

The spec (§1) says the existing `index.project_root()` (a `/.claude/worktrees/` string-strip) is insufficient three ways: it treats a plain `git worktree add ../repo-feat` as its own project, it doesn't resolve a subdirectory cwd to the repo top-level, and it doesn't canonicalize symlinks. This module is the single correct replacement used by queue identity, config keying, CLI resolution, and live-root matching.

**Files:**
- Create: `bin/_pkg/project_id.py`
- Test: `test/test_project_id.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_project_id.py
import os
import subprocess

import pytest

from _pkg import project_id


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A real git repo with one commit at <tmp>/main."""
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("x")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-qm", "init")
    return root


def test_project_id_is_stable_and_hex16(repo):
    pid = project_id.project_id(str(repo))
    assert pid is not None
    assert len(pid) == 16 and all(c in "0123456789abcdef" for c in pid)
    assert project_id.project_id(str(repo)) == pid  # stable


def test_subdir_resolves_to_same_id(repo):
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    assert project_id.project_id(str(sub)) == project_id.project_id(str(repo))


def test_worktree_shares_parent_repo_id(repo):
    wt = repo.parent / "feat"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert project_id.project_id(str(wt)) == project_id.project_id(str(repo))


def test_non_repo_returns_none(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert project_id.project_id(str(plain)) is None


def test_main_root_is_main_working_tree_not_worktree(repo):
    wt = repo.parent / "feat"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert project_id.main_root(str(wt)) == os.path.realpath(str(repo))
    assert project_id.main_root(str(repo)) == os.path.realpath(str(repo))


def test_is_root_cwd_true_for_root_and_subdir_false_for_worktree(repo):
    main = project_id.main_root(str(repo))
    sub = repo / "src"
    sub.mkdir()
    wt = repo.parent / "feat"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert project_id.is_root_cwd(str(repo), main) is True
    assert project_id.is_root_cwd(str(sub), main) is True       # subdir of root
    assert project_id.is_root_cwd(str(wt), main) is False       # worktree session
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_project_id.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.project_id'`.

- [ ] **Step 3: Implement the module**

```python
# bin/_pkg/project_id.py
"""Canonical project identity for the shared-resource queue.

The existing `index.project_root()` merely string-strips `/.claude/worktrees/`,
so it (a) treats a plain `git worktree add ../repo-feat` as its own project,
(b) cannot resolve a subdirectory cwd to the repo top-level, and (c) does not
canonicalize symlinks. The queue keys must be identical across *every* worktree
of a repo, so we key by the repo's git common dir instead.

No Textual import: imported by CLI, queue store, and the exclusive-or policy.
"""

from __future__ import annotations

import hashlib
import os
import subprocess


def _git(cwd: str, *args: str, timeout: float = 2.0) -> "str | None":
    """Run `git -C cwd ...`; return stripped stdout on success, else None."""
    try:
        out = subprocess.run(["git", "-C", cwd, *args],
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def common_dir(cwd: str) -> "str | None":
    """The repo's canonical git common dir, shared by all its worktrees.

    `git rev-parse --git-common-dir` yields the *main* repo's `.git`, identical
    for the main tree and every worktree. It may be relative to `cwd`; we make
    it absolute and `realpath` it so symlinked checkouts collapse together.
    """
    raw = _git(cwd, "rev-parse", "--git-common-dir")
    if not raw:
        return None
    if not os.path.isabs(raw):
        raw = os.path.join(cwd, raw)
    return os.path.realpath(raw)


def project_id(cwd: str) -> "str | None":
    """Stable 16-hex identity for the repo containing `cwd` (None if not a repo)."""
    cd = common_dir(cwd)
    if cd is None:
        return None
    return hashlib.sha256(cd.encode("utf-8")).hexdigest()[:16]


def main_root(cwd: str) -> "str | None":
    """The repo's MAIN working tree path — where a bind-mounted stack lives.

    The first `worktree <path>` line of `git worktree list --porcelain` is
    always the main working tree, regardless of which worktree `cwd` is in.
    """
    out = _git(cwd, "worktree", "list", "--porcelain")
    if not out:
        return None
    for line in out.splitlines():
        if line.startswith("worktree "):
            return os.path.realpath(line[len("worktree "):])
    return None


def toplevel(cwd: str) -> "str | None":
    """The working tree root for `cwd` (subdir-resolving), realpath'd."""
    out = _git(cwd, "rev-parse", "--show-toplevel")
    return os.path.realpath(out) if out else None


def is_root_cwd(cwd: str, root: "str | None") -> bool:
    """True iff `cwd` is inside the repo's MAIN working tree (root or a subdir
    of it), and not inside a worktree. A worktree's toplevel is the worktree
    path, which differs from `root`, so the equality test already excludes it."""
    if not root:
        return False
    return toplevel(cwd) == os.path.realpath(root)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_project_id.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/project_id.py test/test_project_id.py
git commit -m "feat(queue): canonical git-common-dir project identity helper"
```

---

### Task 2: Per-project resource config store (`queue_config.py`)

Spec §2: declarative, per-user, never committed, keyed by the canonical project-id. A project is "opted in" iff it has ≥1 resource. This task builds the store + the `kind` model + `resource_id` slug validation. (The §2 *classification dialog* is Phase 2; the data it writes — `protect` / `allow_delete` lists — lives in this schema now.)

**Files:**
- Create: `bin/_pkg/queue_config.py`
- Test: `test/test_queue_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_queue_config.py
import pytest

from _pkg import queue_config as qc


def test_default_path_for_is_sibling_of_index():
    p = qc.default_path_for("/x/y/session-explorer-index.json")
    assert p == "/x/y/session-explorer-queue-config.json"


def test_load_missing_returns_empty(tmp_path):
    assert qc.load(str(tmp_path / "c.json")) == {"version": 1, "projects": {}}


@pytest.mark.parametrize("rid,ok", [
    ("root", True), ("ios-sim", True), ("db2", True),
    ("Root", False), ("a/b", False), ("a.b", False),
    ("..", False), ("-x", False), ("", False),
])
def test_resource_id_validation(rid, ok):
    assert qc.valid_resource_id(rid) is ok


def test_add_and_get_resource(tmp_path):
    p = str(tmp_path / "c.json")
    qc.add_resource(p, project_id="pid1", display_path="/repo/Gym",
                    resource_id="root",
                    resource={"kind": "root-dir", "path": "/repo/Gym",
                              "run_in": "root", "acquire": "sync",
                              "release": "none"})
    r = qc.get_resource(p, "pid1", "root")
    assert r["kind"] == "root-dir"
    assert r["path"] == "/repo/Gym"
    # display_path is stored as project metadata, not on a key
    data = qc.load(p)
    assert data["projects"]["pid1"]["display_path"] == "/repo/Gym"


def test_add_rejects_bad_kind(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="x",
                        resource={"kind": "bogus", "run_in": "worktree",
                                  "acquire": "none", "release": "none"})


def test_add_rejects_bad_resource_id(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="A/B",
                        resource={"kind": "name", "run_in": "worktree",
                                  "acquire": "none", "release": "none"})


def test_add_rejects_bad_release(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="x",
                        resource={"kind": "name", "run_in": "worktree",
                                  "acquire": "none", "release": "bogus"})


def test_add_command_acquire_requires_command(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="db",
                        resource={"kind": "port", "run_in": "worktree",
                                  "acquire": "command", "release": "none"})


def test_add_command_release_requires_command(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="db",
                        resource={"kind": "port", "run_in": "worktree",
                                  "acquire": "none", "release": "command"})


def test_add_root_dir_requires_path(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="root",
                        resource={"kind": "root-dir", "run_in": "root",
                                  "acquire": "sync", "release": "none",
                                  "sync": {"delete": True, "exclude": ["/.git"],
                                           "protect": ["/.git"]}})


def test_remove_resource_and_opt_out_when_empty(tmp_path):
    p = str(tmp_path / "c.json")
    qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="db",
                    resource={"kind": "port", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    assert qc.is_opted_in(p, "pid1") is True
    qc.remove_resource(p, "pid1", "db")
    assert qc.get_resource(p, "pid1", "db") is None
    assert qc.is_opted_in(p, "pid1") is False  # no resources -> opted out


def test_list_resources_and_all_projects(tmp_path):
    p = str(tmp_path / "c.json")
    qc.add_resource(p, project_id="pid1", display_path="/a", resource_id="root",
                    resource={"kind": "root-dir", "path": "/a", "run_in": "root",
                              "acquire": "sync", "release": "none"})
    qc.add_resource(p, project_id="pid2", display_path="/b", resource_id="sim",
                    resource={"kind": "device", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    assert set(qc.list_resources(p, "pid1")) == {"root"}
    assert set(qc.all_projects(p)) == {"pid1", "pid2"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_queue_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.queue_config'`.

- [ ] **Step 3: Implement the module**

```python
# bin/_pkg/queue_config.py
"""Per-project shared-resource declarations for the queue engine.

Schema (v1):
  {"version": 1,
   "projects": {
     "<project-id>": {
       "display_path": "/abs/repo/path",          # human-readable, never a key
       "resources": {
         "<resource-id>": {
           "kind": "root-dir"|"path"|"port"|"service"|"device"|"name",
           "path": "/abs/path",                    # data only; for root-dir/path
           "guard": [{"exe": "docker", "sub": ["compose", "up"]}],
           "run_in": "root"|"worktree",
           "acquire": "sync"|"none"|"command",
           "release": "none"|"command",
           "command_acquire": "<shell>",           # when acquire == command
           "command_release": "<shell>",           # when release == command
           "release_required": false,
           "sync": {"delete": true, "exclude": ["/.git"],
                    "protect": ["/.git", "/.env", "/.env.*"]},
           "allow_delete": [],                      # classified regenerable paths
           "health": "<shell>",                     # optional
           "wait_for": {"type": "url"|"port"|"command",
                        "target": "...", "timeout": 60}   # optional
         }
       }
     }
   }}

Keyed by `project_id.project_id(cwd)` — NOT a raw cwd path. Concurrency mirrors
`folder_store`: read under LOCK_SH (via `load`), mutate under LOCK_EX on a
sidecar `.lock` + temp-file-rename.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from typing import Any, Callable, Dict, List

VALID_KINDS = {"root-dir", "path", "port", "service", "device", "name"}
_RESOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DEFAULT: Dict[str, Any] = {"version": 1, "projects": {}}


def default_path_for(index_path: str) -> str:
    d = os.path.dirname(os.path.abspath(index_path)) or "."
    return os.path.join(d, "session-explorer-queue-config.json")


def valid_resource_id(rid: str) -> bool:
    """Slug usable as an on-disk queue key: lowercase, no slash/dot/`..`."""
    return bool(rid) and bool(_RESOURCE_ID_RE.match(rid))


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"version": 1, "projects": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "projects": {}}


def save(path: str, data: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-qc-", suffix=".tmp", dir=parent)
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
            data = fn(load(path))
            save(path, data)
            return data
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def _validate(resource_id: str, resource: dict) -> None:
    if not valid_resource_id(resource_id):
        raise ValueError(
            f"invalid resource id {resource_id!r}: must match [a-z0-9][a-z0-9-]*")
    kind = resource.get("kind")
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind {kind!r}: one of {sorted(VALID_KINDS)}")
    if resource.get("run_in") not in ("root", "worktree"):
        raise ValueError("run_in must be 'root' or 'worktree'")
    acquire = resource.get("acquire")
    if acquire not in ("sync", "none", "command"):
        raise ValueError("acquire must be 'sync', 'none' or 'command'")
    release = resource.get("release")
    if release not in ("none", "command"):
        raise ValueError("release must be 'none' or 'command'")
    # v1: sync only applies to root-dir (spec §2).
    if acquire == "sync" and kind != "root-dir":
        raise ValueError("acquire 'sync' is only valid for kind 'root-dir' in v1")
    # Strategy invariants: a command strategy needs its command; a root-dir
    # resource needs the path it bind-mounts / syncs to.
    if acquire == "command" and not resource.get("command_acquire"):
        raise ValueError("acquire 'command' requires a 'command_acquire' shell string")
    if release == "command" and not resource.get("command_release"):
        raise ValueError("release 'command' requires a 'command_release' shell string")
    if kind == "root-dir" and not resource.get("path"):
        raise ValueError("kind 'root-dir' requires a 'path'")


def add_resource(path: str, *, project_id: str, display_path: str,
                 resource_id: str, resource: dict) -> None:
    """Add/replace one resource under a project. Validates before writing."""
    _validate(resource_id, resource)

    def m(data: dict) -> dict:
        projects = data.setdefault("projects", {})
        proj = projects.setdefault(project_id, {"display_path": display_path,
                                                "resources": {}})
        proj["display_path"] = display_path
        proj.setdefault("resources", {})[resource_id] = resource
        data.setdefault("version", 1)
        return data

    mutate(path, m)


def remove_resource(path: str, project_id: str, resource_id: str) -> None:
    def m(data: dict) -> dict:
        proj = data.get("projects", {}).get(project_id)
        if proj:
            proj.get("resources", {}).pop(resource_id, None)
            if not proj.get("resources"):
                data["projects"].pop(project_id, None)  # opt out when empty
        return data

    mutate(path, m)


def get_resource(path: str, project_id: str, resource_id: str) -> "dict | None":
    return load(path).get("projects", {}).get(project_id, {}) \
        .get("resources", {}).get(resource_id)


def list_resources(path: str, project_id: str) -> Dict[str, dict]:
    return dict(load(path).get("projects", {}).get(project_id, {})
                .get("resources", {}))


def is_opted_in(path: str, project_id: str) -> bool:
    return bool(list_resources(path, project_id))


def all_projects(path: str) -> Dict[str, dict]:
    return dict(load(path).get("projects", {}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_queue_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_config.py test/test_queue_config.py
git commit -m "feat(queue): per-project resource config store with kind model"
```

---

### Task 3: Queue core — ticket allocation, publication ordering, holder selection, reaping (`queue_store.py`)

Spec §1: the queue *is* the set of ticket files. Holder = lowest-numbered ticket whose owner is alive. Liveness is proven by `flock`: the owner holds `LOCK_EX` on its own ticket for its whole life; a prober that *can* grab `LOCK_EX|LOCK_NB` concludes the owner is dead (kernel released the lock on exit, even `SIGKILL`). The publication ordering (allocate → write temp → lock → rename, all under the queue `.lock`) guarantees a ticket is visible only after it already holds its lifetime lock, so a prober can never catch a just-created ticket in an unlocked gap.

> **flock note for the implementer:** `flock` locks attach to the *open file description*. Two separate `open()` calls on the same file (even within one process) are distinct descriptions and **do contend** — so the owner's own ticket correctly probes as "alive" when the owner itself scans the queue, and `holder()` returns the owner's own number when it is the lowest live ticket. Do not `dup()` the lock fd.

**Files:**
- Create: `bin/_pkg/queue_store.py`
- Test: `test/test_queue_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_queue_store.py
import os

import pytest

from _pkg import queue_store as qs


def _qdir(tmp_path):
    return str(tmp_path / "q")


def test_take_ticket_publishes_locked_file(tmp_path):
    qdir = _qdir(tmp_path)
    t = qs.take_ticket(qdir, sid="s1", cwd="/wt", command=["echo", "hi"],
                       pid=1234, label="Gym/root", now_iso="2026-06-06T00:00:00+00:00")
    assert t.number == 1
    assert os.path.exists(t.path)
    # the owner holds it -> a foreign liveness probe says alive
    assert qs._probe_alive(t.path) is True
    t.release()


def test_numbers_are_monotonic_among_live_tickets(tmp_path):
    qdir = _qdir(tmp_path)
    a = qs.take_ticket(qdir, sid="a", cwd="/", command=["x"], pid=1, label="l", now_iso="t")
    b = qs.take_ticket(qdir, sid="b", cwd="/", command=["x"], pid=2, label="l", now_iso="t")
    assert (a.number, b.number) == (1, 2)
    a.release(); b.release()


def test_holder_is_lowest_live_ticket(tmp_path):
    qdir = _qdir(tmp_path)
    a = qs.take_ticket(qdir, sid="a", cwd="/", command=["x"], pid=1, label="l", now_iso="t")
    b = qs.take_ticket(qdir, sid="b", cwd="/", command=["x"], pid=2, label="l", now_iso="t")
    assert qs.holder(qdir) == a.number
    a.release()
    assert qs.holder(qdir) == b.number   # advances after the holder leaves
    b.release()
    assert qs.holder(qdir) is None


def test_dead_owner_is_reaped_and_holder_advances(tmp_path):
    """Simulate a crashed holder: a published ticket file whose lock nobody holds."""
    qdir = _qdir(tmp_path)
    # Hand-craft a 'dead' lower ticket (no live flock on it).
    os.makedirs(qdir, exist_ok=True)
    dead = os.path.join(qdir, qs.ticket_name(1, "dead"))
    with open(dead, "w") as f:
        f.write('{"number": 1, "sid": "dead", "pid": 999999}')
    live = qs.take_ticket(qdir, sid="live", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    assert live.number == 2
    assert qs.holder(qdir) == live.number     # dead #1 reaped, #2 is holder
    assert not os.path.exists(dead)           # reaped from disk
    live.release()


def test_release_removes_ticket(tmp_path):
    qdir = _qdir(tmp_path)
    t = qs.take_ticket(qdir, sid="s", cwd="/", command=["x"], pid=1, label="l", now_iso="t")
    p = t.path
    t.release()
    assert not os.path.exists(p)


def test_position_reports_place_in_line(tmp_path):
    qdir = _qdir(tmp_path)
    a = qs.take_ticket(qdir, sid="a", cwd="/", command=["x"], pid=1, label="l", now_iso="t")
    b = qs.take_ticket(qdir, sid="b", cwd="/", command=["x"], pid=2, label="l", now_iso="t")
    c = qs.take_ticket(qdir, sid="c", cwd="/", command=["x"], pid=3, label="l", now_iso="t")
    assert qs.position(qdir, a.number) == (1, 3)
    assert qs.position(qdir, b.number) == (2, 3)
    assert qs.position(qdir, c.number) == (3, 3)
    a.release(); b.release(); c.release()


def test_list_tickets_returns_sorted_live_entries(tmp_path):
    qdir = _qdir(tmp_path)
    a = qs.take_ticket(qdir, sid="a", cwd="/", command=["x"], pid=1, label="Gym/root", now_iso="t")
    rows = qs.list_tickets(qdir)
    assert [r["sid"] for r in rows] == ["a"]
    assert rows[0]["label"] == "Gym/root"
    a.release()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.queue_store'`.

- [ ] **Step 3: Implement the module**

```python
# bin/_pkg/queue_store.py
"""Daemon-less FIFO queue core: one ticket file per participant.

The queue *is* the set of ticket files in a directory. Ordering comes from a
monotonic number baked into each filename; the holder is the lowest-numbered
ticket whose owner process is still alive. Liveness is proven by flock: the
owner holds LOCK_EX on its own ticket for its whole life, so a prober that can
grab LOCK_EX|LOCK_NB knows the owner died (the kernel drops the lock on exit,
including SIGKILL) -> immune to PID reuse, survives crashes.

Publication ordering (under the queue '.lock'): allocate number -> write temp
ticket -> acquire LOCK_EX on it -> atomic rename into the visible dir. A ticket
is therefore visible only after it already holds its lifetime lock, so a
prober's LOCK_NB can never catch it in an unlocked gap and falsely reap it.

Concurrency mirrors the other stores: flock(LOCK_EX) on '<qdir>/.lock' guards
allocate / publish / reap / cancel.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_TICKET_PREFIX = "t"
_NUM_WIDTH = 10


def ticket_name(number: int, sid: str) -> str:
    """Lexically-sortable ticket filename (zero-padded number)."""
    safe_sid = "".join(c for c in sid if c.isalnum() or c in "-_") or "anon"
    return f"{_TICKET_PREFIX}{number:0{_NUM_WIDTH}d}-{safe_sid}.json"


def _parse_number(name: str) -> Optional[int]:
    if not (name.startswith(_TICKET_PREFIX) and name.endswith(".json")):
        return None
    core = name[len(_TICKET_PREFIX):-len(".json")]
    num_str = core.split("-", 1)[0]
    try:
        return int(num_str)
    except ValueError:
        return None


def _lock_path(qdir: str) -> str:
    return os.path.join(qdir, ".lock")


def _ticket_files(qdir: str) -> List[Tuple[int, str]]:
    """(number, abspath) for every ticket file, sorted by number."""
    out: List[Tuple[int, str]] = []
    try:
        names = os.listdir(qdir)
    except FileNotFoundError:
        return out
    for name in names:
        num = _parse_number(name)
        if num is not None:
            out.append((num, os.path.join(qdir, name)))
    out.sort(key=lambda t: t[0])
    return out


def _probe_alive(ticket_path: str) -> bool:
    """True iff some process still holds the ticket's lifetime flock."""
    try:
        f = open(ticket_path, "r")
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # we grabbed it -> owner is dead
        return False
    except (BlockingIOError, OSError):
        return True
    finally:
        f.close()


def _next_number(qdir: str) -> int:
    files = _ticket_files(qdir)
    return (files[-1][0] + 1) if files else 1


@dataclass
class Ticket:
    number: int
    path: str
    sid: str
    _lock_fd: object  # kept open for the ticket's lifetime

    def release(self) -> None:
        """Drop the lifetime lock and remove the ticket file. Best-effort and
        idempotent — never raises (a racing reaper, an already-gone file, or a
        permission error must not propagate out of cleanup)."""
        try:
            os.unlink(self.path)
        except OSError:
            pass  # already gone / racing reaper / permission — nothing to do
        try:
            self._lock_fd.close()  # releases the flock
        except Exception:
            pass


def take_ticket(qdir: str, *, sid: str, cwd: str, command, pid: int,
                label: str, now_iso: str) -> Ticket:
    """Allocate, lock, and publish a ticket. The returned Ticket holds the
    lifetime lock; call .release() (always in a finally) when done."""
    os.makedirs(qdir, exist_ok=True)
    with open(_lock_path(qdir), "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            number = _next_number(qdir)
            payload = {"number": number, "sid": sid, "cwd": cwd,
                       "command": command, "pid": pid, "label": label,
                       "created": now_iso}
            fd, tmp = tempfile.mkstemp(prefix=".t-", suffix=".tmp", dir=qdir)
            with os.fdopen(fd, "w", encoding="utf-8") as wf:
                json.dump(payload, wf)
            # Re-open and grab the LIFETIME lock BEFORE publishing.
            lock_fd = open(tmp, "r+")
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            final = os.path.join(qdir, ticket_name(number, sid))
            os.replace(tmp, final)   # publish; lock_fd follows the inode
            return Ticket(number=number, path=final, sid=sid, _lock_fd=lock_fd)
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def _reap_and_scan(qdir: str) -> List[Tuple[int, str]]:
    """Under the queue lock: unlink dead tickets, return live (number, path)."""
    os.makedirs(qdir, exist_ok=True)
    live: List[Tuple[int, str]] = []
    with open(_lock_path(qdir), "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            for number, path in _ticket_files(qdir):
                if _probe_alive(path):
                    live.append((number, path))
                else:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
    return live


def holder(qdir: str) -> Optional[int]:
    """Lowest-numbered live ticket number, reaping dead ones. None if empty."""
    live = _reap_and_scan(qdir)
    return live[0][0] if live else None


def position(qdir: str, my_number: int) -> Tuple[int, int]:
    """(place, total) among live tickets; place is 1-based by number."""
    live = _reap_and_scan(qdir)
    total = len(live)
    place = sum(1 for n, _ in live if n <= my_number)
    return place, total


def _read_ticket(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def list_tickets(qdir: str) -> List[dict]:
    """Live ticket payloads, sorted by number (for queue-status / the pane)."""
    rows: List[dict] = []
    for _, path in _reap_and_scan(qdir):
        data = _read_ticket(path)
        if data is not None:
            rows.append(data)
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_store.py test/test_queue_store.py
git commit -m "feat(queue): daemon-less FIFO ticket core with flock crash-reaping"
```

---

### Task 4: Waiting loop + cancellation tombstones (`queue_store.py` extended)

Spec §4: a waiting `queue-run` holds its ticket (its FIFO place) while polling; `Ctrl-C` removes it via the `finally`. Cancellation by *another* process is atomic under the queue `.lock`: `queue-cancel` revalidates the target is still a *waiter* (never the current holder), then **unlinks the ticket itself** *and* writes an excluded tombstone in `history/`. The waiter, polling under the same lock discipline, treats *its ticket gone or a tombstone for it* as cancellation and exits non-zero with the reason. A current holder cannot be cancelled this way.

**Files:**
- Modify: `bin/_pkg/queue_store.py`
- Test: `test/test_queue_store.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
# append to test/test_queue_store.py
import time


def test_wait_returns_acquired_when_holder(tmp_path):
    qdir = _qdir(tmp_path)
    t = qs.take_ticket(qdir, sid="solo", cwd="/", command=["x"], pid=os.getpid(),
                       label="l", now_iso="t")
    outcome = qs.wait_for_turn(qdir, t, poll_interval=0.01, timeout=1.0)
    assert outcome == "acquired"
    t.release()


def test_wait_times_out_behind_a_live_holder(tmp_path):
    qdir = _qdir(tmp_path)
    head = qs.take_ticket(qdir, sid="head", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    me = qs.take_ticket(qdir, sid="me", cwd="/", command=["x"], pid=os.getpid(),
                        label="l", now_iso="t")
    outcome = qs.wait_for_turn(qdir, me, poll_interval=0.01, timeout=0.2)
    assert outcome == "timeout"
    me.release(); head.release()


def test_cancel_waiter_unlinks_and_tombstones(tmp_path):
    qdir = _qdir(tmp_path)
    head = qs.take_ticket(qdir, sid="head", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    waiter = qs.take_ticket(qdir, sid="wait", cwd="/", command=["x"], pid=os.getpid(),
                            label="l", now_iso="t")
    assert qs.cancel(qdir, sid="wait", reason="user cancelled") is True
    assert not os.path.exists(waiter.path)             # ticket unlinked
    assert qs.cancelled_reason(qdir, waiter.number, "wait") == "user cancelled"
    head.release(); waiter.release()


def test_cancel_refuses_current_holder(tmp_path):
    qdir = _qdir(tmp_path)
    head = qs.take_ticket(qdir, sid="head", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    assert qs.cancel(qdir, sid="head", reason="nope") is False  # holder protected
    assert os.path.exists(head.path)
    head.release()


def test_wait_returns_cancelled_when_ticket_removed(tmp_path):
    qdir = _qdir(tmp_path)
    head = qs.take_ticket(qdir, sid="head", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    me = qs.take_ticket(qdir, sid="me", cwd="/", command=["x"], pid=os.getpid(),
                        label="l", now_iso="t")
    qs.cancel(qdir, sid="me", reason="bye")
    outcome = qs.wait_for_turn(qdir, me, poll_interval=0.01, timeout=1.0)
    assert outcome == "cancelled:bye"
    head.release()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: FAIL with `AttributeError: module '_pkg.queue_store' has no attribute 'wait_for_turn'`.

- [ ] **Step 3: Implement the additions**

Add to the top of `queue_store.py` (imports):

```python
import time
```

Append these functions to `bin/_pkg/queue_store.py`:

```python
def _history_dir(qdir: str) -> str:
    return os.path.join(qdir, "history")


def _tombstone_name(number: int, sid: str) -> str:
    safe_sid = "".join(c for c in sid if c.isalnum() or c in "-_") or "anon"
    return f"cancel-{number:0{_NUM_WIDTH}d}-{safe_sid}.json"


def cancel(qdir: str, *, sid: str, reason: str) -> bool:
    """Cancel a WAITING ticket for `sid`. Returns False (no-op) if `sid` is the
    current holder or has no ticket. Atomic under the queue lock: unlink the
    ticket AND write a tombstone, so neither a stale ticket nor a lone tombstone
    can mis-order the queue."""
    os.makedirs(qdir, exist_ok=True)
    with open(_lock_path(qdir), "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            # Recompute live set under the lock (also reaps dead tickets).
            live: List[Tuple[int, str]] = []
            for number, path in _ticket_files(qdir):
                if _probe_alive(path):
                    live.append((number, path))
                else:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
            if not live:
                return False
            holder_number = live[0][0]
            target = next(((n, p) for n, p in live
                           if _read_ticket(p) and _read_ticket(p).get("sid") == sid),
                          None)
            if target is None:
                return False
            number, path = target
            if number == holder_number:
                return False  # cannot cancel the running holder
            os.makedirs(_history_dir(qdir), exist_ok=True)
            tomb = os.path.join(_history_dir(qdir), _tombstone_name(number, sid))
            with open(tomb, "w", encoding="utf-8") as tf:
                json.dump({"number": number, "sid": sid, "reason": reason}, tf)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            return True
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def cancelled_reason(qdir: str, number: int, sid: str) -> Optional[str]:
    """If a tombstone exists for (number, sid), return its reason, else None."""
    tomb = os.path.join(_history_dir(qdir), _tombstone_name(number, sid))
    data = _read_ticket(tomb)
    return data.get("reason") if data else None


def wait_for_turn(qdir: str, ticket: "Ticket", *, poll_interval: float = 0.5,
                  timeout: Optional[float] = None) -> str:
    """Block until `ticket` is the holder. Returns:
      "acquired"          -> ticket is now the holder
      "timeout"           -> gave up after `timeout` seconds
      "cancelled:<reason>"-> the ticket was cancelled by another process
    The caller still owns the ticket and must release() it in a finally."""
    waited = 0.0
    while True:
        # Cancellation: our ticket file vanished, or a tombstone names us.
        reason = cancelled_reason(qdir, ticket.number, ticket.sid)
        if reason is not None or not os.path.exists(ticket.path):
            return f"cancelled:{reason or 'cancelled'}"
        if holder(qdir) == ticket.number:
            return "acquired"
        if timeout is not None and waited >= timeout:
            return "timeout"
        time.sleep(poll_interval)
        waited += poll_interval
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_queue_store.py -q`
Expected: PASS (all task-3 and task-4 tests).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_store.py test/test_queue_store.py
git commit -m "feat(queue): FIFO wait loop + atomic waiter cancellation tombstones"
```

---

### Task 5: rsync `sync` filter construction (`qsync.py`)

Spec §2: `sync` is the most dangerous primitive (`--delete` against root). Its filters are specified exactly. `exclude` (worktree junk not copied in) and `protect` (root-only files preserved) share one mechanism — **anchored rsync exclude filters** (`--filter='exclude /<path>'`) — not rsync `P`/`protect` rules (which only block deletion, letting a same-named worktree path still overwrite). Never `--delete-excluded`. `/.git` (no trailing slash) matches both the worktree's `.git` *file* and root's `.git` *directory*.

**Files:**
- Create: `bin/_pkg/qsync.py`
- Test: `test/test_qsync.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_qsync.py
from _pkg import qsync


def test_filters_anchor_and_dedupe():
    f = qsync.build_filters(exclude=["/.git", "node_modules"],
                            protect=["/.git", "/.env"])
    # exclude + protect unioned, each rendered as an anchored exclude filter
    assert "--filter=exclude /.git" in f
    assert "--filter=exclude /node_modules" in f
    assert "--filter=exclude /.env" in f
    # /.git appears once despite being in both lists
    assert f.count("--filter=exclude /.git") == 1


def test_rsync_command_shape():
    cmd = qsync.rsync_command("/wt", "/root", exclude=["/.git"], protect=["/.env"],
                              dry_run=False)
    assert cmd[0] == "rsync"
    assert "-a" in cmd and "--delete" in cmd
    assert "--delete-excluded" not in cmd     # never; excluded must survive
    assert cmd[-2] == "/wt/" and cmd[-1] == "/root/"   # trailing slashes


def test_dry_run_adds_itemize_flags():
    cmd = qsync.rsync_command("/wt", "/root", exclude=[], protect=[], dry_run=True)
    assert "-n" in cmd and "-i" in cmd


def test_trailing_slashes_normalized():
    cmd = qsync.rsync_command("/wt/", "/root/", exclude=[], protect=[], dry_run=False)
    assert cmd[-2] == "/wt/" and cmd[-1] == "/root/"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_qsync.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.qsync'`.

- [ ] **Step 3: Implement the filter layer**

```python
# bin/_pkg/qsync.py
"""The `sync` acquire strategy: rsync a holder's worktree OVER the shared root.

`--delete` against root is the most dangerous primitive in the design, so the
filters are exact. `exclude` (worktree junk not copied in) and `protect`
(root-only files preserved untouched) share ONE mechanism: anchored rsync
exclude filters. An exclude both removes a path from the transfer AND (with
--delete) from deletion, so root's version wins on both axes -- which is what
`protect` needs. rsync's own `P`/`protect` rule only blocks deletion, so a
same-named worktree path would still overwrite root; we never use it.

Never pass --delete-excluded: excluded/protected paths must always survive.
`/.git` (no trailing slash) matches both the worktree's `.git` *file* (a gitdir
pointer) and root's `.git` *directory*, so the worktree pointer never corrupts
root's repo.
"""

from __future__ import annotations

import os
from typing import List

# Conservative auto-protect default (spec §2): applied with no prompt.
DEFAULT_PROTECT = ["/.git", "/.env", "/.env.*"]


def _anchor(path: str) -> str:
    """Normalize a filter path to the spec's anchored form: leading "/" (anchor
    at the transfer root), no trailing slash (so `/.git` matches both root's
    `.git` directory and a worktree's `.git` file). "node_modules" -> "/node_modules"."""
    p = "/" + path.lstrip("/")
    return p.rstrip("/") if len(p) > 1 else p


def build_filters(exclude: List[str], protect: List[str]) -> List[str]:
    """Anchored `--filter=exclude <path>` args for the union of exclude+protect,
    de-duplicated AFTER normalization (so `.git` and `/.git/` collapse to one)
    while preserving first-seen order."""
    args: List[str] = []
    seen = set()
    for path in list(exclude) + list(protect):
        if not path:
            continue
        anchored = _anchor(path)
        if anchored not in seen:
            seen.add(anchored)
            args.append(f"--filter=exclude {anchored}")
    return args


def rsync_command(src: str, dst: str, *, exclude: List[str], protect: List[str],
                  dry_run: bool) -> List[str]:
    """Build the exact rsync argv. Trailing slashes are normalized so rsync
    copies *contents* (src/ -> dst/), not a nested src directory."""
    cmd = ["rsync", "-a", "--delete"]
    if dry_run:
        cmd += ["-n", "-i"]
    cmd += build_filters(exclude, protect)
    cmd += [src.rstrip("/") + "/", dst.rstrip("/") + "/"]
    return cmd
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_qsync.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/qsync.py test/test_qsync.py
git commit -m "feat(queue): exact rsync --filter construction for the sync strategy"
```

---

### Task 6: Dry-run deletion analysis + baseline classification + sandbox marker (`qsync.py` extended)

Spec §2: at the **first sandbox transition** the protected baseline is built **by classification, not blanket capture**. Auto-protect `/.git /.env /.env.*` silently. The classification set is **only untracked + gitignored** root paths the dry-run would delete; each must be classified *protect* vs *allow-delete*. **Tracked** root files the dry-run would delete are a legitimate branch difference — never prompted, always deleted. A per-resource **sandbox marker** records that the baseline is settled; later acquires reset freely without re-prompting.

In Phase 1 (no TUI dialog yet) the *gate* is implemented but classification is **manual**: `queue-run` refuses and prints the unclassified paths plus the instruction to add each to `protect` or `allow_delete` in the config. Phase 2 wraps this in the dialog.

**Files:**
- Modify: `bin/_pkg/qsync.py`
- Test: `test/test_qsync.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
# append to test/test_qsync.py
import os
import subprocess

import pytest


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _repo(tmp_path, name):
    r = tmp_path / name
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    return r


def test_parse_deletions_from_itemized_output():
    out = (
        "*deleting   stale.txt\n"
        ">f+++++++++ new.txt\n"
        "*deleting   cache/blob\n"
        "cd+++++++++ dir/\n"
    )
    assert qsync.parse_deletions(out) == ["stale.txt", "cache/blob"]


def test_classify_separates_tracked_from_untracked(tmp_path):
    root = _repo(tmp_path, "root")
    (root / "tracked.txt").write_text("a")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "c")
    (root / "untracked.txt").write_text("b")
    (root / ".gitignore").write_text("ignored.txt\n")
    (root / "ignored.txt").write_text("c")
    would_delete = ["tracked.txt", "untracked.txt", "ignored.txt"]
    needs = qsync.classify_candidates(str(root), would_delete)
    # tracked is auto-allowed (legitimate branch diff); the rest need a decision
    assert set(needs) == {"untracked.txt", "ignored.txt"}


def test_unclassified_excludes_auto_protect_and_already_classified(tmp_path):
    root = _repo(tmp_path, "root")
    (root / ".env").write_text("SECRET=1")
    (root / "build").mkdir()
    (root / "build" / "out").write_text("x")
    (root / "certs").mkdir()
    (root / "certs" / "key.pem").write_text("k")
    would_delete = [".env", "build/out", "certs/key.pem"]
    unresolved = qsync.unclassified(
        str(root), would_delete,
        protect=["/certs"], allow_delete=["/build"])
    # .env -> auto-protected; build -> allow_delete; certs -> protect; none left
    assert unresolved == []
    # Now drop the classifications: build/out + certs/key.pem must surface.
    unresolved2 = qsync.unclassified(str(root), would_delete,
                                     protect=[], allow_delete=[])
    assert set(unresolved2) == {"build/out", "certs/key.pem"}


def test_sandbox_marker_roundtrip(tmp_path):
    qdir = str(tmp_path / "q")
    assert qsync.in_sandbox(qdir) is False
    qsync.mark_sandbox(qdir)
    assert qsync.in_sandbox(qdir) is True


def test_dry_run_fails_closed_on_rsync_error(tmp_path):
    # A non-existent source makes rsync exit non-zero; we must RAISE, not
    # return [] (which would silently bypass the delete-classification gate).
    with pytest.raises(qsync.SyncDryRunError):
        qsync.dry_run_deletions(str(tmp_path / "does-not-exist"),
                                str(tmp_path), exclude=[], protect=[])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_qsync.py -q`
Expected: FAIL with `AttributeError: module '_pkg.qsync' has no attribute 'parse_deletions'`.

- [ ] **Step 3: Implement the additions**

Add to the imports of `qsync.py`:

```python
import fnmatch
import subprocess
```

Append to `bin/_pkg/qsync.py`:

```python
def parse_deletions(itemized_output: str) -> List[str]:
    """Pull the paths from rsync's `-n -i` output that would be DELETED.
    rsync marks deletions with a leading `*deleting` token."""
    deletions: List[str] = []
    for line in itemized_output.splitlines():
        if line.startswith("*deleting"):
            # "*deleting   path/to/file"  ->  "path/to/file"
            parts = line.split(None, 1)
            if len(parts) == 2:
                deletions.append(parts[1].strip())
    return deletions


class SyncDryRunError(Exception):
    """The dry-run that gates the destructive --delete could not be completed.
    Raised so queue-run FAILS CLOSED — never assume 'no deletions' on error."""


def dry_run_deletions(src: str, dst: str, *, exclude: List[str],
                      protect: List[str]) -> List[str]:
    """Run the sync as a dry-run and return paths (relative to dst) it would
    delete. FAILS CLOSED: a non-zero rsync, a launch error, or a timeout raises
    SyncDryRunError rather than returning [], so the caller refuses instead of
    silently bypassing the destructive-delete classification gate."""
    cmd = rsync_command(src, dst, exclude=exclude, protect=protect, dry_run=True)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        raise SyncDryRunError("rsync dry-run timed out") from e
    except OSError as e:
        raise SyncDryRunError(f"rsync dry-run could not run: {e}") from e
    if out.returncode != 0:
        raise SyncDryRunError(
            out.stderr.strip() or f"rsync dry-run exited {out.returncode}")
    return parse_deletions(out.stdout)


def _is_tracked(root: str, rel_path: str) -> bool:
    """True iff `rel_path` is a tracked file on root's current branch."""
    try:
        r = subprocess.run(
            ["git", "-C", root, "ls-files", "--error-unmatch", rel_path],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def classify_candidates(root: str, would_delete: List[str]) -> List[str]:
    """Of the would-delete paths, the untracked/gitignored ones that need a
    protect-vs-allow-delete decision. Tracked files are omitted: deleting them
    is a legitimate branch difference the reset must apply (spec §2)."""
    return [p for p in would_delete if not _is_tracked(root, p)]


def _matches_anchored(rel_path: str, anchored: List[str]) -> bool:
    """Match `rel_path` (e.g. "build/out") against anchored patterns
    ("/build", "/.env.*"): the leading "/" anchors at root; a pattern matches
    the path itself or any descendant, with fnmatch globbing."""
    norm = "/" + rel_path.lstrip("/")
    for pat in anchored:
        p = pat if pat.startswith("/") else "/" + pat
        if fnmatch.fnmatch(norm, p) or fnmatch.fnmatch(norm, p.rstrip("/") + "/*"):
            return True
    return False


def unclassified(root: str, would_delete: List[str], *, protect: List[str],
                 allow_delete: List[str]) -> List[str]:
    """Untracked/gitignored would-delete paths NOT yet resolved by the
    auto-protect default, an explicit `protect`, or an explicit `allow_delete`.
    A non-empty result means `queue-run` must refuse until the user classifies."""
    resolved = list(DEFAULT_PROTECT) + list(protect) + list(allow_delete)
    out: List[str] = []
    for rel in classify_candidates(root, would_delete):
        if not _matches_anchored(rel, resolved):
            out.append(rel)
    return out


def _marker_path(qdir: str) -> str:
    return os.path.join(qdir, "sandbox.marker")


def in_sandbox(qdir: str) -> bool:
    """True once the protected baseline has been settled for this resource."""
    return os.path.exists(_marker_path(qdir))


def mark_sandbox(qdir: str) -> None:
    """Record that the baseline is settled; later acquires reset freely."""
    os.makedirs(qdir, exist_ok=True)
    with open(_marker_path(qdir), "a", encoding="utf-8"):
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_qsync.py -q`
Expected: PASS (filter + classification tests).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/qsync.py test/test_qsync.py
git commit -m "feat(queue): sync dry-run deletion analysis + protected-baseline gate"
```

---

### Task 7: Root-dir exclusive-or policy (`exclusive.py`)

Spec §5: root is **either** a live working session **or** the lease sandbox — never both. Liveness comes from the **live registry** (`live.py`, judged by `live._alive`), *not* the `--gc` heuristic. A session counts as "in root" when its `cwd` resolves to this project's canonical main root and is not a worktree (use `project_id`, not `index.project_root`). The transition guard refuses if root has uncommitted git changes; the rsync dry-run refusal (Task 6) is the second, authoritative layer. (This whole module is `root-dir`-only; `port`/`device`/`name` resources are plain leases.)

**Files:**
- Create: `bin/_pkg/exclusive.py`
- Test: `test/test_exclusive.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_exclusive.py
import os
import subprocess
from datetime import datetime, timezone

from _pkg import exclusive, live


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _repo(tmp_path):
    r = tmp_path / "main"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("x")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-qm", "init")
    return r


T0 = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)


def test_no_live_session_means_sandbox_free(tmp_path):
    root = _repo(tmp_path)
    lp = str(tmp_path / "live.json")
    assert exclusive.live_root_session(lp, str(root), now=T0) is None


def test_live_root_session_detected(tmp_path):
    root = _repo(tmp_path)
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1",
                      cwd=str(root), pid=os.getpid(), now=T0)
    hit = exclusive.live_root_session(lp, str(root), now=T0)
    assert hit is not None and hit["sid"] == "s1"


def test_subdir_session_counts_as_root(tmp_path):
    root = _repo(tmp_path)
    sub = root / "src"
    sub.mkdir()
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s2",
                      cwd=str(sub), pid=os.getpid(), now=T0)
    assert exclusive.live_root_session(lp, str(root), now=T0) is not None


def test_worktree_session_does_not_count_as_root(tmp_path):
    root = _repo(tmp_path)
    wt = tmp_path / "feat"
    _git(root, "worktree", "add", "-q", str(wt))
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="w1",
                      cwd=str(wt), pid=os.getpid(), now=T0)
    assert exclusive.live_root_session(lp, str(root), now=T0) is None


def test_transition_guard_blocks_dirty_root(tmp_path):
    root = _repo(tmp_path)
    (root / "f.txt").write_text("modified")  # uncommitted change
    assert exclusive.transition_guard(str(root)) is not None


def test_transition_guard_passes_clean_root(tmp_path):
    root = _repo(tmp_path)
    assert exclusive.transition_guard(str(root)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_exclusive.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.exclusive'`.

- [ ] **Step 3: Implement the module**

```python
# bin/_pkg/exclusive.py
"""Root-dir exclusive-or-with-live-session policy (spec §5). root-dir ONLY.

Root is either a live working session OR the lease sandbox, never both -- which
is what makes a destructive `sync` acquire safe. Liveness comes from the live
registry (live.py), judged by live._alive (PID + 24h TTL). A session is "in
root" when its cwd resolves (via the canonical project_id helper, NOT the weaker
index.project_root string-strip) to the project's main working tree and is not a
worktree. The transition guard refuses a dirty root; the rsync dry-run refusal
(qsync.unclassified) is the second, authoritative layer for ignored files.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Optional

from . import live as _live
from . import project_id as _pid


def live_root_session(live_path: str, main_root: str, *,
                      now: Optional[datetime] = None,
                      ttl_seconds: int = _live.DEFAULT_TTL_SECONDS) -> Optional[dict]:
    """Return {"sid", "cwd", "name"} of a live session working in `main_root`
    (root or a subdir, not a worktree), or None. The first such session wins."""
    now = now or datetime.now(timezone.utc)
    data = _live.load(live_path)
    for sid, entry in data.get("sessions", {}).items():
        if not _live._alive(entry, now, ttl_seconds):
            continue
        cwd = entry.get("cwd")
        if cwd and _pid.is_root_cwd(cwd, main_root):
            return {"sid": sid, "cwd": cwd, "name": entry.get("name", sid)}
    return None


def transition_guard(main_root: str) -> Optional[str]:
    """Refusal reason if `main_root` has uncommitted git changes, else None.
    This catches tracked changes; ignored root-only files are caught by the
    rsync dry-run refusal layer (qsync)."""
    try:
        r = subprocess.run(["git", "-C", main_root, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None  # fail open: the dry-run refusal still guards deletes
    if r.returncode == 0 and r.stdout.strip():
        return ("root has uncommitted changes the sandbox would overwrite — "
                "stash/commit first")
    return None
```

> Note: `live.record_event` does not store a `name`; `entry.get("name", sid)` falls back to the session id, which is fine for Phase 1 messaging. The TUI (Phase 2) resolves the display name from the index.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_exclusive.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/exclusive.py test/test_exclusive.py
git commit -m "feat(queue): root-dir exclusive-or live-session + transition guard"
```

---

### Task 8: Health + readiness probes (`probes.py`)

Spec §2/§4: `health` answers "is the resource up?" (v1 **detects + warns**, never auto-starts). `wait_for` is a readiness probe (port / url / command) with a timeout, run after acquire, before the command.

**Files:**
- Create: `bin/_pkg/probes.py`
- Test: `test/test_probes.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_probes.py
import socket
import threading

from _pkg import probes


def test_health_ok_on_zero_exit():
    ok, _ = probes.health_check("true", timeout=5)
    assert ok is True


def test_health_down_on_nonzero_exit():
    ok, _ = probes.health_check("false", timeout=5)
    assert ok is False


def test_health_none_command_is_ok():
    # No health command declared -> treated as "not checked", reported up.
    ok, detail = probes.health_check(None, timeout=5)
    assert ok is True and "no health" in detail.lower()


def test_wait_for_command_succeeds():
    spec = {"type": "command", "target": "true", "timeout": 2}
    assert probes.wait_for(spec, poll_interval=0.05) is True


def test_wait_for_command_times_out():
    spec = {"type": "command", "target": "false", "timeout": 0.2}
    assert probes.wait_for(spec, poll_interval=0.05) is False


def test_wait_for_port_open():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        spec = {"type": "port", "target": f"127.0.0.1:{port}", "timeout": 2}
        assert probes.wait_for(spec, poll_interval=0.05) is True
    finally:
        srv.close()


def test_wait_for_port_closed_times_out():
    spec = {"type": "port", "target": "127.0.0.1:1", "timeout": 0.2}
    assert probes.wait_for(spec, poll_interval=0.05) is False


def test_wait_for_none_spec_is_ready():
    assert probes.wait_for(None) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_probes.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.probes'`.

- [ ] **Step 3: Implement the module**

```python
# bin/_pkg/probes.py
"""Health + readiness probes for the queue lease lifecycle (spec §2/§4).

`health_check` answers "is the resource up?" -- v1 detects and warns, never
auto-starts (the `ensure` param is deferred). `wait_for` polls a port / url /
command until ready or the timeout elapses, run after acquire and before the
wrapped command. Stdlib only: subprocess + socket (no requests/http client dep).
"""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple


def health_check(command: Optional[str], *, timeout: float = 10) -> Tuple[bool, str]:
    """Run the health shell command; up iff it exits 0. No command -> (True,
    'no health check')."""
    if not command:
        return True, "no health check configured"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "health check timed out"
    except OSError as e:
        return False, f"health check error: {e}"
    if r.returncode == 0:
        return True, "up"
    return False, (r.stderr.strip() or r.stdout.strip() or
                   f"health check exited {r.returncode}")


def _check_port(target: str, timeout: float) -> bool:
    host, _, port = target.partition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _check_url(target: str, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(target, timeout=timeout) as resp:
            return 200 <= resp.status < 500   # any response = the server answered
    except urllib.error.HTTPError:
        return True       # an HTTP error is still a live server
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _check_command(target: str, timeout: float) -> bool:
    try:
        return subprocess.run(target, shell=True, capture_output=True,
                              timeout=timeout).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def wait_for(spec: Optional[dict], *, poll_interval: float = 0.5) -> bool:
    """Poll `spec` (a {type, target, timeout} dict) until ready or timeout.
    `type` is 'port' | 'url' | 'command'. None/empty spec -> ready immediately."""
    if not spec:
        return True
    kind = spec.get("type")
    target = spec.get("target", "")
    deadline = float(spec.get("timeout", 60))
    per_try = min(poll_interval * 4, 5.0)  # cap a single probe's own timeout
    checker = {"port": _check_port, "url": _check_url,
               "command": _check_command}.get(kind)
    if checker is None:
        return True  # unknown probe type: don't block (fail open)
    waited = 0.0
    while True:
        if checker(target, per_try):
            return True
        if waited >= deadline:
            return False
        time.sleep(poll_interval)
        waited += poll_interval
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_probes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/probes.py test/test_probes.py
git commit -m "feat(queue): health + wait_for readiness probes"
```

---

### Task 9: Lease lifecycle orchestrator (`queue_run.py`)

Spec §3/§4/§5: one process runs the whole lifecycle so release is guaranteed. Order: take ticket → wait turn → **[root-dir: exclusive-or check + sync source/baseline guards]** → health (warn) → acquire → wait_for → run command in `run_in` → release → **release ticket strictly last (finally)**. Exit code is the child's; a pre-command refusal uses a reserved code. Signals forward to the child then release.

**Files:**
- Create: `bin/_pkg/queue_run.py`
- Test: `test/test_queue_run.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_queue_run.py
import os
import subprocess

import pytest

from _pkg import queue_config as qc
from _pkg import queue_run, queue_store


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _repo(tmp_path):
    r = tmp_path / "main"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("x")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-qm", "init")
    return r


@pytest.fixture
def paths(tmp_path):
    return {
        "config": str(tmp_path / "qc.json"),
        "queues_root": str(tmp_path / "queues"),
        "live": str(tmp_path / "live.json"),
    }


def test_none_strategy_runs_command_and_returns_its_code(tmp_path, paths):
    root = _repo(tmp_path)
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "port", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    marker = tmp_path / "ran"
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["sh", "-c", f"touch {marker}"], cwd=str(root),
        sid="s1", pid=os.getpid())
    assert rc == 0
    assert marker.exists()


def test_command_failure_exit_code_is_preserved(tmp_path, paths):
    root = _repo(tmp_path)
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["sh", "-c", "exit 7"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == 7


def test_unknown_resource_is_refusal_code(tmp_path, paths):
    root = _repo(tmp_path)
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id(str(root))
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="missing",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT


def test_ticket_released_after_run(tmp_path, paths):
    root = _repo(tmp_path)
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    qdir = queue_run.queue_dir(paths["queues_root"], pid, "db")
    assert queue_store.holder(qdir) is None   # no ticket left behind


def test_root_dir_sync_from_root_cwd_refuses(tmp_path, paths):
    """A root-dir sync invoked from root itself must refuse (no worktree source)."""
    root = _repo(tmp_path)
    from _pkg import project_id as _pid
    pid = _pid.project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="root",
                    resource={"kind": "root-dir", "path": _pid.main_root(str(root)),
                              "run_in": "root", "acquire": "sync", "release": "none",
                              "sync": {"delete": True, "exclude": ["/.git"],
                                       "protect": ["/.git"]}})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="root",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT


def test_command_acquire_runs_before_command(tmp_path, paths):
    root = _repo(tmp_path)
    from _pkg import project_id as _pid
    pid = _pid.project_id(str(root))
    acq = tmp_path / "acquired"
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "port", "path": "", "run_in": "worktree",
                              "acquire": "command", "release": "none",
                              "command_acquire": f"touch {acq}"})
    main = tmp_path / "order"
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["sh", "-c", f"test -f {acq} && touch {main}"],
        cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == 0 and main.exists()   # acquire ran before the command


def test_live_root_blocks_then_proceeds_when_cleared(tmp_path, paths, monkeypatch):
    """A live root session makes a worktree queue-run WAIT (holding its ticket),
    then run once the session clears — not refuse and lose its place (spec §5)."""
    from _pkg import project_id as pid_mod
    root = _repo(tmp_path)
    wt = tmp_path / "feat"
    _git(root, "worktree", "add", "-q", str(wt))
    pid = pid_mod.project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="root",
                    resource={"kind": "root-dir", "path": pid_mod.main_root(str(root)),
                              "run_in": "worktree", "acquire": "none", "release": "none"})
    calls = {"n": 0}

    def fake_live(_live_path, root_arg, **_kw):
        calls["n"] += 1
        # "held" for the first two polls, then the session clears.
        return ({"sid": "x", "cwd": root_arg, "name": "main"}
                if calls["n"] < 3 else None)

    monkeypatch.setattr(queue_run.exclusive, "live_root_session", fake_live)
    # No-op the sleep so the wait loop spins fast (it still polls fake_live).
    monkeypatch.setattr(queue_run.time, "sleep", lambda _s: None)
    marker = tmp_path / "ran"
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="root",
        command=["sh", "-c", f"touch {marker}"], cwd=str(wt), sid="s1",
        pid=os.getpid())
    assert rc == 0 and marker.exists()
    assert calls["n"] >= 3   # it polled (waited) before proceeding


def test_interrupt_while_waiting_releases_ticket(tmp_path, paths, monkeypatch):
    """SIGINT during the wait raises _Interrupted; the finally still drops the
    ticket and leaves the line (spec §4)."""
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id
    root = _repo(tmp_path)
    project_id = pid(str(root))
    qc.add_resource(paths["config"], project_id=project_id, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})

    def boom(*_a, **_k):
        raise queue_run._Interrupted(2)

    monkeypatch.setattr(queue_store, "wait_for_turn", boom)
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=project_id, resource_id="db",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT
    qdir = queue_run.queue_dir(paths["queues_root"], project_id, "db")
    assert queue_store.holder(qdir) is None   # ticket released despite interrupt


def test_release_runs_after_acquire_even_when_readiness_fails(tmp_path, paths):
    """Acquire succeeded but wait_for never readies -> release hook must STILL
    run before the ticket is released (spec §4)."""
    from _pkg import project_id as pid_mod
    root = _repo(tmp_path)
    pid = pid_mod.project_id(str(root))
    rel = tmp_path / "released"
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "port", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "command",
                              "command_release": f"touch {rel}",
                              "wait_for": {"type": "command", "target": "false",
                                           "timeout": 0.2}})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT   # readiness timed out
    assert rel.exists()                    # release hook still ran


def test_missing_executable_is_controlled_exit_and_runs_release(tmp_path, paths):
    """A non-existent command must return a controlled code (no traceback) and
    still run the release hook (acquire succeeded)."""
    from _pkg import project_id as pid_mod
    root = _repo(tmp_path)
    pid = pid_mod.project_id(str(root))
    rel = tmp_path / "released"
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "command",
                              "command_release": f"touch {rel}"})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["this-executable-does-not-exist-xyz"], cwd=str(root),
        sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT     # controlled, not a traceback
    assert rel.exists()                      # release hook still ran
    qdir = queue_run.queue_dir(paths["queues_root"], pid, "db")
    assert queue_store.holder(qdir) is None  # ticket released


def test_release_exception_still_releases_ticket(tmp_path, paths, monkeypatch):
    """A release hook that raises an unexpected exception must NOT strand the
    ticket — the innermost finally still removes it (spec §4 liveness)."""
    from _pkg import project_id as pid_mod
    root = _repo(tmp_path)
    pid = pid_mod.project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "command",
                              "command_release": "true"})

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(queue_run, "_do_release", boom)
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == 0                            # child succeeded; release error logged
    qdir = queue_run.queue_dir(paths["queues_root"], pid, "db")
    assert queue_store.holder(qdir) is None   # ticket released despite the raise


def test_sync_marker_not_written_when_dry_run_fails(tmp_path, monkeypatch):
    """Fail-closed: a dry-run error refuses AND must not settle the baseline."""
    from _pkg import qsync
    qdir = str(tmp_path / "q")
    resource = {"kind": "root-dir", "acquire": "sync", "release": "none",
                "path": str(tmp_path / "root"),
                "sync": {"delete": True, "exclude": ["/.git"], "protect": ["/.git"]}}
    monkeypatch.setattr(queue_run.exclusive, "transition_guard", lambda _r: None)
    monkeypatch.setattr(qsync, "dry_run_deletions",
                        lambda *a, **k: (_ for _ in ()).throw(qsync.SyncDryRunError("boom")))
    msg = queue_run._do_acquire(resource, src=str(tmp_path / "wt"),
                                root=resource["path"], qdir=qdir)
    assert msg and "verify" in msg.lower()
    assert qsync.in_sandbox(qdir) is False


def test_sync_marker_not_written_when_real_rsync_fails(tmp_path, monkeypatch):
    """A failed real rsync must not settle the baseline (gate re-fires next run)."""
    from _pkg import qsync
    qdir = str(tmp_path / "q")
    resource = {"kind": "root-dir", "acquire": "sync", "release": "none",
                "path": str(tmp_path / "root"),
                "sync": {"delete": True, "exclude": ["/.git"], "protect": ["/.git"]}}
    monkeypatch.setattr(queue_run.exclusive, "transition_guard", lambda _r: None)
    monkeypatch.setattr(qsync, "dry_run_deletions", lambda *a, **k: [])

    class _Failed:
        returncode = 1

    monkeypatch.setattr(queue_run.subprocess, "run", lambda *a, **k: _Failed())
    msg = queue_run._do_acquire(resource, src=str(tmp_path / "wt"),
                                root=resource["path"], qdir=qdir)
    assert msg == "rsync acquire failed"
    assert qsync.in_sandbox(qdir) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_queue_run.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.queue_run'`.

- [ ] **Step 3: Implement the orchestrator**

```python
# bin/_pkg/queue_run.py
"""Single-process lease lifecycle (spec §3/§4/§5).

One process runs: take ticket -> wait turn -> [root-dir exclusive-or +
sync-source/baseline guards] -> health (warn) -> acquire -> wait_for -> run the
command in run_in -> release hook -> release ticket (strictly last, in finally).
Exit code is the child's; a pre-command refusal uses REFUSAL_EXIT. SIGINT/SIGTERM
forward to the child, then the finally releases the ticket. Crash/SIGKILL is
covered by queue_store's flock auto-release + reaping.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from . import exclusive, probes, qsync
from . import project_id as _pid
from . import queue_store

REFUSAL_EXIT = 70   # distinct from any wrapped-command failure
_RELEASE_TIMEOUT = 60


class _Interrupted(Exception):
    """Raised from the signal handler when SIGINT/SIGTERM arrives while we are
    WAITING (no child running yet), so the finally releases the ticket. The spec
    requires Ctrl-C to drop the ticket and leave the line even mid-wait."""


def queue_dir(queues_root: str, project_id: str, resource_id: str) -> str:
    return os.path.join(queues_root, project_id, resource_id)


def _wait_for_root_free(live_path: str, root: str, ticket, qdir: str, *,
                        poll_interval: float = 0.5,
                        timeout: Optional[float] = None) -> str:
    """Block (holding our FIFO ticket) while a live root session works in root.
    Returns 'free' once clear, 'timeout', or 'cancelled:<reason>'. Raises
    _Interrupted on signal (time.sleep is interrupted by the handler)."""
    waited = 0.0
    announced = False
    while True:
        reason = queue_store.cancelled_reason(qdir, ticket.number, ticket.sid)
        if reason is not None or not os.path.exists(ticket.path):
            return f"cancelled:{reason or 'cancelled'}"
        hit = exclusive.live_root_session(live_path, root)
        if hit is None:
            return "free"
        if not announced:
            print(f"queue-run: waiting — root held by live session "
                  f"{hit['name']!r}", file=sys.stderr)
            announced = True
        if timeout is not None and waited >= timeout:
            return "timeout"
        time.sleep(poll_interval)
        waited += poll_interval


def _refuse(msg: str) -> int:
    print(f"queue-run: {msg}", file=sys.stderr)
    return REFUSAL_EXIT


def _run_shell(command: str, cwd: Optional[str], timeout: Optional[float]) -> int:
    return subprocess.run(command, shell=True, cwd=cwd, timeout=timeout).returncode


def _do_acquire(resource: dict, *, src: str, root: str, qdir: str) -> Optional[str]:
    """Run the acquire strategy. Returns a refusal message, or None on success."""
    strategy = resource.get("acquire", "none")
    if strategy == "none":
        return None
    if strategy == "command":
        cmd = resource.get("command_acquire")
        if cmd and _run_shell(cmd, cwd=root, timeout=None) != 0:
            return "acquire command failed"
        return None
    if strategy == "sync":
        sync = resource.get("sync", {})
        exclude = sync.get("exclude", ["/.git"])
        protect = sync.get("protect", list(qsync.DEFAULT_PROTECT))
        allow_delete = resource.get("allow_delete", [])
        first_transition = not qsync.in_sandbox(qdir)
        # First sandbox transition: classification gate (spec §2/§5.3).
        if first_transition:
            guard = exclusive.transition_guard(root)
            if guard:
                return guard
            try:
                would_delete = qsync.dry_run_deletions(src, root, exclude=exclude,
                                                       protect=protect)
            except qsync.SyncDryRunError as e:
                # Fail closed: never assume "no deletions" when the dry-run that
                # gates the destructive --delete could not be completed.
                return f"could not verify the sandbox reset is safe: {e}"
            unresolved = qsync.unclassified(root, would_delete, protect=protect,
                                            allow_delete=allow_delete)
            if unresolved:
                listing = "\n  ".join(unresolved)
                return ("the sandbox reset would delete unclassified root files; "
                        "add each to this resource's `protect` (precious) or "
                        "`allow_delete` (regenerable) list in the queue config, "
                        f"then retry:\n  {listing}")
        cmd = qsync.rsync_command(src, root, exclude=exclude, protect=protect,
                                  dry_run=False)
        if subprocess.run(cmd).returncode != 0:
            return "rsync acquire failed"
        # The marker means "baseline settled" -> write it only AFTER a
        # successful real sync, so a failed rsync doesn't skip the gate next run.
        if first_transition:
            qsync.mark_sandbox(qdir)
        return None
    return f"unknown acquire strategy {strategy!r}"


def _do_release(resource: dict, *, root: str) -> bool:
    """Run the release hook (time-bounded). Returns True on success/none."""
    if resource.get("release") != "command":
        return True
    cmd = resource.get("command_release")
    if not cmd:
        return True
    try:
        return _run_shell(cmd, cwd=root, timeout=_RELEASE_TIMEOUT) == 0
    except subprocess.TimeoutExpired:
        return False


def _record_release_failure(qdir: str, sid: str, msg: str) -> None:
    hist = os.path.join(qdir, "history")
    os.makedirs(hist, exist_ok=True)
    path = os.path.join(hist, f"release-fail-{sid}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sid": sid, "error": msg}, f)
    except OSError:
        pass


def run_lease(*, config_path: str, queues_root: str, live_path: str,
              project_id: str, resource_id: str, command: List[str], cwd: str,
              sid: str, pid: int, timeout: Optional[float] = None) -> int:
    from . import queue_config as qc

    resource = qc.get_resource(config_path, project_id, resource_id)
    if resource is None:
        return _refuse(f"no resource {resource_id!r} configured for this project")

    kind = resource.get("kind")
    run_in = resource.get("run_in", "worktree")
    root = resource.get("path") or _pid.main_root(cwd) or cwd
    src = _pid.toplevel(cwd) or cwd     # the holder's worktree (sync source)
    work_dir = root if run_in == "root" else cwd

    # root-dir sync must have a worktree source, never rsync root over itself.
    if kind == "root-dir" and resource.get("acquire") == "sync":
        if _pid.is_root_cwd(cwd, root):
            return _refuse("root-dir sync must run from a worktree, not root "
                           "itself (a root cwd is the exclusive-or holder)")

    qdir = queue_dir(queues_root, project_id, resource_id)
    display = qc.load(config_path).get("projects", {}).get(project_id, {}) \
        .get("display_path", project_id)
    label = f"{os.path.basename(display.rstrip('/')) or display}/{resource_id}"
    now_iso = datetime.now(timezone.utc).isoformat()

    ticket = queue_store.take_ticket(qdir, sid=sid, cwd=cwd, command=command,
                                     pid=pid, label=label, now_iso=now_iso)

    # A mutable holder so the signal handler always sees the *current* child.
    child_holder: List[Optional[subprocess.Popen]] = [None]

    def _handler(signum, _frame):
        ch = child_holder[0]
        if ch is not None and ch.poll() is None:
            ch.send_signal(signum)          # a command is running: forward to it
        else:
            raise _Interrupted(signum)      # still waiting: abort -> finally releases

    old_int = signal.signal(signal.SIGINT, _handler)
    old_term = signal.signal(signal.SIGTERM, _handler)

    start = time.monotonic()

    def _remaining() -> Optional[float]:
        if timeout is None:
            return None
        return max(0.0, timeout - (time.monotonic() - start))

    # `result` is set on every post-acquire path and returned AFTER finally, so
    # the release hook (in finally) can still bump it for release_required. Note:
    # pre-acquire paths `return` directly (acquired is False -> finally is a
    # no-op for release), which is safe.
    result = REFUSAL_EXIT
    acquired = False
    try:
        outcome = queue_store.wait_for_turn(qdir, ticket, timeout=_remaining())
        if outcome.startswith("cancelled:"):
            return _refuse(outcome.split(":", 1)[1])
        if outcome == "timeout":
            return _refuse("timed out waiting for the resource")

        # Exclusive-or (root-dir only): WAIT holding our FIFO place while a live
        # root session works in root; proceed once it clears (spec §5/§4) —
        # never fail and lose the line.
        if kind == "root-dir":
            root_outcome = _wait_for_root_free(live_path, root, ticket, qdir,
                                               timeout=_remaining())
            if root_outcome.startswith("cancelled:"):
                return _refuse(root_outcome.split(":", 1)[1])
            if root_outcome == "timeout":
                return _refuse("timed out waiting for the live root session to end")

        # Health: detect + warn, never block.
        ok, detail = probes.health_check(resource.get("health"))
        if not ok:
            print(f"queue-run: warning: resource appears down: {detail}",
                  file=sys.stderr)

        refusal = _do_acquire(resource, src=src, root=root, qdir=qdir)
        if refusal:
            return _refuse(refusal)        # acquire failed: nothing to release
        acquired = True

        if not probes.wait_for(resource.get("wait_for")):
            print("queue-run: resource did not become ready before timeout",
                  file=sys.stderr)
            result = REFUSAL_EXIT          # set, don't return: release must run
        else:
            try:
                child = subprocess.Popen(command, cwd=work_dir)
            except OSError as e:
                # Missing executable / bad cwd: a controlled exit, not a
                # traceback. acquired stays True so the finally still releases.
                print(f"queue-run: failed to start command: {e}", file=sys.stderr)
                result = REFUSAL_EXIT
            else:
                child_holder[0] = child
                try:
                    child.wait()
                    result = child.returncode
                finally:
                    # Clear so a signal after completion (but before handler
                    # restoration) can't forward to / mis-detect an exited child.
                    child_holder[0] = None
    except _Interrupted:
        print("queue-run: interrupted — releasing ticket", file=sys.stderr)
        result = REFUSAL_EXIT
    finally:
        # The release phase must be UNINTERRUPTIBLE: a signal here must not raise
        # (_Interrupted from our handler, or KeyboardInterrupt from the default
        # one) and skip ticket release. Ignore SIGINT/SIGTERM for the duration,
        # then restore the originals. ticket.release() sits in its own innermost
        # finally so it runs even if _do_release raises unexpectedly.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            # Release hook runs whenever acquire SUCCEEDED, on any exit path
            # after acquire (normal completion, readiness refusal, Popen
            # failure, interrupt) — before the ticket is released (spec §4).
            if acquired:
                err = None
                try:
                    ok = _do_release(resource, root=root)
                except Exception as e:   # a buggy release hook must not strand the queue
                    ok = False
                    err = f"release hook raised: {e}"
                if not ok:
                    _record_release_failure(
                        qdir, sid, err or "release hook failed/timed out")
                    print("queue-run: warning: release hook failed", file=sys.stderr)
                    if resource.get("release_required") and result == 0:
                        result = 1
        finally:
            try:
                ticket.release()   # absolutely last, unavoidable
            finally:
                # Restore handlers even if release somehow raised, so the
                # process never escapes with SIGINT/SIGTERM left at SIG_IGN.
                signal.signal(signal.SIGINT, old_int)
                signal.signal(signal.SIGTERM, old_term)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_queue_run.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_run.py test/test_queue_run.py
git commit -m "feat(queue): single-process lease lifecycle orchestrator"
```

---

### Task 10: CLI wiring — `queue-run` / `queue-status` / `queue-cancel` (`cli.py`)

Spec §3: `queue-run --resource <r> -- <command>`; project resolved from cwd via the canonical helper, overridable with `--project <root>` or a fully-qualified `<project-id>/<resource-id>`. Plus `queue-status` (JSON + human, fully-qualified ids) and `queue-cancel` (drop a waiting ticket).

**Files:**
- Modify: `bin/_pkg/cli.py`
- Test: `test/test_cli.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
# append to test/test_cli.py
import json as _json
import subprocess as _sp


def _git(cwd, *args):
    _sp.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _repo(tmp_path):
    r = tmp_path / "main"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("x")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-qm", "init")
    return r


def _qenv(tmp_path):
    return {**os.environ,
            "SESSION_EXPLORER_INDEX": str(tmp_path / "index.json"),
            "SESSION_EXPLORER_QUEUE_CONFIG": str(tmp_path / "qc.json"),
            "SESSION_EXPLORER_QUEUES_ROOT": str(tmp_path / "queues"),
            "SESSION_EXPLORER_LIVE": str(tmp_path / "live.json")}


def _seed_resource(tmp_path, env):
    """Use the config store directly to declare a trivial 'none' resource."""
    from _pkg import project_id as pid_mod, queue_config as qc
    root = _repo(tmp_path)
    pid = pid_mod.project_id(str(root))
    qc.add_resource(env["SESSION_EXPLORER_QUEUE_CONFIG"], project_id=pid,
                    display_path=str(root), resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    return root, pid


def test_queue_run_executes_command_from_cwd(tmp_path):
    env = _qenv(tmp_path)
    root, _pid = _seed_resource(tmp_path, env)
    marker = tmp_path / "ran"
    r = _sp.run([_BIN, "queue-run", "--resource", "db", "--",
                 "sh", "-c", f"touch {marker}"],
                cwd=str(root), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert marker.exists()


def test_queue_run_unknown_resource_uses_refusal_code(tmp_path):
    env = _qenv(tmp_path)
    root = _repo(tmp_path)
    r = _sp.run([_BIN, "queue-run", "--resource", "nope", "--", "true"],
                cwd=str(root), env=env, capture_output=True, text=True)
    from _pkg.queue_run import REFUSAL_EXIT
    assert r.returncode == REFUSAL_EXIT


def test_queue_status_json_lists_configured_resource(tmp_path):
    env = _qenv(tmp_path)
    root, pid = _seed_resource(tmp_path, env)
    r = _sp.run([_BIN, "queue-status", "--json"], env=env,
                capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = _json.loads(r.stdout)
    ids = [row["id"] for row in data]
    assert f"{pid}/db" in ids


def test_queue_cancel_reports_no_waiter(tmp_path):
    env = _qenv(tmp_path)
    root, pid = _seed_resource(tmp_path, env)
    # Nothing waiting -> cancel is a clean no-op with a clear message.
    r = _sp.run([_BIN, "queue-cancel", "--resource", "db", "--sid", "ghost"],
                cwd=str(root), env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "no waiting ticket" in (r.stdout + r.stderr).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_cli.py -q -k queue`
Expected: FAIL — `queue-run`/`queue-status`/`queue-cancel` are not yet subcommands (argparse error / "invalid choice").

- [ ] **Step 3: Add path helpers + subparsers + dispatch**

Add the path helpers near `_live_path()` in `bin/_pkg/cli.py`:

```python
def _queue_config_path() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_QUEUE_CONFIG")
    if env_override:
        return env_override
    from . import queue_config as _qc
    return _qc.default_path_for(_index_path())


def _queues_root() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_QUEUES_ROOT")
    if env_override:
        return env_override
    return os.path.join(os.path.dirname(_index_path()), "session-explorer-queues")


def _resolve_project(args) -> "tuple[str, str] | None":
    """Resolve (project_id, resource_id) from --resource + cwd/--project.
    Accepts a fully-qualified '<project-id>/<resource-id>' --resource too."""
    from . import project_id as _pid
    res = args.resource
    if "/" in res:
        pid, rid = res.split("/", 1)
        return pid, rid
    cwd = getattr(args, "project", None) or os.getcwd()
    pid = _pid.project_id(cwd)
    if pid is None:
        return None
    return pid, res
```

Add the subparsers inside `build_parser()` (after the `live_p` block):

```python
    qr = sub.add_parser("queue-run",
                        help="Run a command under a shared-resource lease.")
    qr.add_argument("--resource", required=True,
                    help="Resource id, or fully-qualified <project-id>/<resource-id>.")
    qr.add_argument("--project", default=None,
                    help="Repo root to resolve the resource against (default: cwd).")
    qr.add_argument("--timeout", type=float, default=None,
                    help="Max seconds to wait for the lease before giving up.")
    qr.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- then the command to run.")

    qstat = sub.add_parser("queue-status",
                           help="Show active shared-resource queues.")
    qstat.add_argument("--json", action="store_true", help="Emit JSON.")

    qcancel = sub.add_parser("queue-cancel",
                             help="Cancel a waiting ticket on a resource.")
    qcancel.add_argument("--resource", required=True)
    qcancel.add_argument("--project", default=None)
    qcancel.add_argument("--sid", required=True, help="Session id of the waiter.")
    qcancel.add_argument("--reason", default="cancelled by user")
```

Add the command implementations (top-level functions in `cli.py`):

```python
def _cmd_queue_run(args) -> int:
    from . import queue_run as _qr
    resolved = _resolve_project(args)
    if resolved is None:
        print("queue-run: cwd is not inside a git repo / opted-in project",
              file=sys.stderr)
        return _qr.REFUSAL_EXIT
    project_id, resource_id = resolved
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("queue-run: no command given (use: queue-run --resource R -- CMD)",
              file=sys.stderr)
        return _qr.REFUSAL_EXIT
    import uuid
    sid = os.environ.get("CLAUDE_SESSION_ID") or f"cli-{uuid.uuid4().hex[:8]}"
    return _qr.run_lease(
        config_path=_queue_config_path(), queues_root=_queues_root(),
        live_path=_live_path(), project_id=project_id, resource_id=resource_id,
        command=command, cwd=os.getcwd(), sid=sid, pid=os.getpid(),
        timeout=args.timeout)


def _cmd_queue_status(args) -> int:
    import json as _json
    from . import queue_config as _qc
    from . import queue_run as _qr
    from . import queue_store as _qs
    cfg = _queue_config_path()
    rows = []
    for pid, proj in _qc.all_projects(cfg).items():
        for rid in proj.get("resources", {}):
            qdir = _qr.queue_dir(_queues_root(), pid, rid)
            tickets = _qs.list_tickets(qdir)
            holder = tickets[0] if tickets else None
            rows.append({
                "id": f"{pid}/{rid}",
                "project": proj.get("display_path", pid),
                "resource": rid,
                "holder": holder["sid"] if holder else None,
                "waiting": [t["sid"] for t in tickets[1:]],
            })
    if args.json:
        print(_json.dumps(rows))
        return 0
    if not rows:
        print("No shared resources configured.")
        return 0
    for row in rows:
        state = (f"holder: {row['holder']}" if row["holder"] else "free")
        wait = (f"  waiting: {', '.join(row['waiting'])}" if row["waiting"] else "")
        print(f"{row['id']:<40} {state}{wait}")
    return 0


def _cmd_queue_cancel(args) -> int:
    from . import queue_run as _qr
    from . import queue_store as _qs
    resolved = _resolve_project(args)
    if resolved is None:
        print("queue-cancel: could not resolve project", file=sys.stderr)
        return 2
    pid, rid = resolved
    qdir = _qr.queue_dir(_queues_root(), pid, rid)
    if _qs.cancel(qdir, sid=args.sid, reason=args.reason):
        print(f"Cancelled waiting ticket for {args.sid} on {pid}/{rid}.")
        return 0
    print(f"queue-cancel: no waiting ticket for {args.sid} on {pid}/{rid} "
          f"(it may be the running holder or already gone).", file=sys.stderr)
    return 1
```

Wire them into `main()` dispatch (alongside the existing `if args.cmd == ...` block):

```python
    if args.cmd == "queue-run":
        return _cmd_queue_run(args)
    if args.cmd == "queue-status":
        return _cmd_queue_status(args)
    if args.cmd == "queue-cancel":
        return _cmd_queue_cancel(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_cli.py -q -k queue`
Expected: PASS. Then run the whole suite: `python3 -m pytest test/ -q` — expected: all green.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(queue): wire queue-run/queue-status/queue-cancel CLI subcommands"
```

---

### Task 11: Documentation — `SPEC.md` + `CLAUDE.md` (no version bump here)

Phase 1 is "usable immediately with only `CLAUDE.md` guidance." Per the project rule, `SPEC.md` is authoritative and must be updated in the same change. **Do not bump the version or cut a release in this task** — per the project's phased-delivery rule, the version bump + release happens once after all three phases land (follow the `cutting-a-release` skill then).

**Files:**
- Modify: `SPEC.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a shared-resource-queue section to `SPEC.md`**

First read the current structure to place it well:

Run: `grep -n "^## \|^### " SPEC.md | head -50`

Then add a new top-level section documenting the Phase-1 surface. Paste this block after the existing architecture sections (adjust the heading level to match neighbors):

```markdown
## Shared-resource lease engine (queue core — Phase 1)

`session-explorer queue-run --resource <r> -- <cmd>` serializes a command
against a shared singleton resource declared per-project. See
`docs/superpowers/specs/2026-06-05-shared-root-test-queue-design.md` for the
full design; this records the shipped Phase-1 surface.

- **Identity:** queues + config are keyed by `project_id.project_id(cwd)` — a
  hash of the repo's `git --git-common-dir`, so every worktree of a repo shares
  one identity. This supersedes `index.project_root()` for queue purposes (which
  string-strips `/.claude/worktrees/` and is kept for tree grouping only).
- **Config:** `~/.claude/session-explorer-queue-config.json`, keyed by
  project-id; a project is opted in iff it has ≥1 resource. Resource `kind` is
  one of `root-dir`/`path`/`port`/`service`/`device`/`name`. `acquire` is
  `sync`/`none`/`command`; `sync` is v1-restricted to `root-dir`.
- **Queue:** `~/.claude/session-explorer-queues/<project-id>/<resource-id>/`.
  The queue *is* the set of ticket files; holder = lowest-numbered ticket whose
  owner still holds its lifetime `flock` (crash/SIGKILL-safe, PID-reuse immune).
  Ticket publication happens under the queue `.lock` only after the ticket holds
  its lock, so a liveness probe never falsely reaps a fresh ticket. Cancellation
  unlinks the ticket + writes a `history/` tombstone, atomic under `.lock`.
- **sync strategy:** `rsync -a --delete` with anchored `--filter=exclude`
  rules (never `--delete-excluded`); `exclude` and `protect` share that one
  mechanism. The first sandbox transition runs a `--dry-run`, auto-protects
  `/.git /.env /.env.*`, deletes tracked branch-diff files, and refuses on any
  untracked/ignored would-delete path not classified into `protect` or
  `allow_delete`. A `sandbox.marker` settles the baseline; later acquires reset
  freely. **Phase 1 classification is manual** (edit the config); the §2 dialog
  arrives in Phase 2.
- **root-dir exclusive-or (§5):** if a live registry session's cwd resolves to
  the repo's main working tree (or a subdir, not a worktree), worktree acquires
  block. A dirty root blocks the first transition. root-dir/sync invoked from
  root itself is refused.
- **Lifecycle:** one process, release in a `finally`; child exit code is passed
  through; pre-command refusals use exit code 70; `SIGINT`/`SIGTERM` forward to
  the child then release.
- **Probes:** `health` warns (never auto-starts; `ensure` deferred); `wait_for`
  polls port/url/command until ready or timeout.
- **Deferred (schema-reserved):** `ensure`, `reload`, `env`, `capacity`>1; the
  TUI (Phase 2) and the SessionStart/PreToolUse hooks + cooperative skill
  (Phase 3).
```

- [ ] **Step 2: Add a guidance snippet to `CLAUDE.md`**

Add a bullet under the "Load-bearing design decisions" list in `CLAUDE.md`:

```markdown
- **Shared-resource queue keys by git-common-dir, not `project_root`.** The
  Phase-1 lease engine (`queue-run`/`queue-status`/`queue-cancel`,
  `bin/_pkg/queue_*.py` + `project_id.py` + `qsync.py` + `exclusive.py` +
  `probes.py`) identifies a project by `project_id.project_id(cwd)` (a hash of
  `git rev-parse --git-common-dir`), so every worktree of a repo collapses to
  one queue identity — `index.project_root()` (a `/.claude/worktrees/`
  string-strip) is NOT used for queues. The queue is the set of ticket files;
  the holder is the lowest-numbered ticket whose owner still holds its lifetime
  `fcntl.flock` (no daemon, crash-safe). The `sync` acquire strategy runs
  `rsync -a --delete` with anchored `--filter=exclude` rules and refuses the
  first sandbox transition until untracked/ignored would-delete paths are
  classified into `protect`/`allow_delete`. Never reintroduce `--delete-excluded`
  or rsync `P`/`protect` rules. See `SPEC.md` → "Shared-resource lease engine".
```

- [ ] **Step 3: Verify docs reference real symbols**

Run: `grep -rn "queue_run\|project_id\|qsync\|queue_store\|queue_config\|exclusive" bin/_pkg/ | grep "def \|class " | head`
Expected: confirms every symbol named in the docs exists.

- [ ] **Step 4: Run the full suite once more**

Run: `python3 -m pytest test/ -q`
Expected: all green (no regressions in existing suites).

- [ ] **Step 5: Commit**

```bash
git add SPEC.md CLAUDE.md
git commit -m "docs(queue): record Phase-1 lease engine in SPEC.md + CLAUDE.md"
```

---

## Self-review against the spec

**Spec coverage map (Phase-1 scope):**

| Spec section | Covered by |
|---|---|
| §1 Queue identity (project-id, common-dir helper) | Task 1, Task 3 |
| §1 Daemon-less FIFO + flock crash-reaping | Task 3 |
| §1 Ticket publication ordering (no false reap) | Task 3 (`take_ticket`) |
| §1 No central holder lock / unique numbers | Task 3 (`holder`) |
| §1 Capacity deferred | Honored (single holder; not implemented) |
| §2 Config store, kind model, resource_id slug | Task 2 |
| §2 Parameter model (core params) | Task 2 schema + consumed in Task 9 |
| §2 Guard matching | **Deferred to Phase 3** (stored as data only; queue-run doesn't match guards — the hook does). Noted in plan intro. |
| §2 sync knobs + exact rsync filters | Task 5 |
| §2 Protected baseline classification + sandbox marker | Task 6 (manual classification in Phase 1) |
| §3 CLI spine + project resolution | Task 10 |
| §4 Lease lifecycle order | Task 9 |
| §4 Failure semantics (finally, exit codes, signals) | Task 9 |
| §4 Waiting & cancellation | Task 4 + Task 9 + Task 10 |
| §4 root-dir sync requires worktree source | Task 9 (`is_root_cwd` refusal) |
| §5 Exclusive-or (live-root detection, transition guard) | Task 7 + Task 9 |
| §5.4 Prevention layer (new-session dialog) | **Phase 2 (TUI)** — out of scope |
| §6 TUI / detection toast | **Phase 2** — out of scope |
| §7 Template library | **Phase 2** (editor picker pre-fills config) — Phase 1 accepts hand-written config |
| §8 Hooks + skill | **Phase 3** — out of scope |
| Constraints (stdlib flock, rsync/git only, fail-open, opt-in) | Tasks 1–10 honor; Task 11 records |

**Deliberate Phase-1 deferrals (faithful to "Build order"):** guard *matching* (Phase 3 consumer), the classification *dialog* (Phase 2 — Phase 1 refuses with manual instructions), the detection flag/toast (Phase 2), templates (Phase 2 picker), the SessionStart/PreToolUse hooks and cooperative skill (Phase 3). The version bump + release follows all three phases per the project's phased-delivery rule.

**Review fixes folded in (round 2):**

- *Live-root contention is a WAIT, not a refusal* (spec §5/§4). `run_lease` calls `_wait_for_root_free` after `wait_for_turn`, holding the ticket until the live root session clears (or `--timeout` / cancel / Ctrl-C). It no longer returns `REFUSAL_EXIT` on first detection. Tested by `test_live_root_blocks_then_proceeds_when_cleared`.
- *Sandbox marker is written only after a SUCCESSFUL real rsync* — so a failed acquire re-fires the classification gate next run. Tested by `test_sync_marker_not_written_when_real_rsync_fails`.
- *The dry-run fails closed* — `qsync.dry_run_deletions` raises `SyncDryRunError` (never returns `[]`) on rsync non-zero/timeout/launch error, and `_do_acquire` turns that into a refusal. Tested by `test_dry_run_fails_closed_on_rsync_error` + `test_sync_marker_not_written_when_dry_run_fails`.
- *Ctrl-C mid-wait drops the ticket* — the signal handler forwards to a running child but raises `_Interrupted` when none exists (during any wait/health/acquire/readiness step), so the `finally` releases the ticket. Tested by `test_interrupt_while_waiting_releases_ticket`.
- *Release hook runs on every post-acquire exit path* — moved into the `finally`, gated on `acquired`, before `ticket.release()`; `result` is set (not returned) on post-acquire paths so the `finally` can still bump it for `release_required`. Tested by `test_release_runs_after_acquire_even_when_readiness_fails`.
- *Config validation* now covers `release`, the `command_acquire`/`command_release` invariants, and `path` for `root-dir`. Tested by the four `test_add_*` cases in Task 2.

**Review fixes folded in (round 3):**

- *`build_filters` anchors every path* (`_anchor`: leading `/`, no trailing slash) and de-dupes after normalization, satisfying the spec's anchored-pattern rule and the `node_modules → /node_modules` test.
- *`Popen` failure is a controlled exit* — a missing executable / bad cwd is caught and returned as `REFUSAL_EXIT` (no traceback escapes `run_lease`), with `acquired` still True so the `finally` runs the release hook. Tested by `test_missing_executable_is_controlled_exit_and_runs_release`.
- *Test hygiene* — Task 2's opt-out test no longer declares `acquire: "command"` without `command_acquire` (would fail the new validation at setup); Task 6's appended qsync tests now `import pytest`.

**Review fixes folded in (round 4):**

- *The release phase is uninterruptible* — the `finally` sets `SIGINT`/`SIGTERM` to `SIG_IGN` for the duration of `_do_release` + `ticket.release()`, then restores the originals, so a signal mid-release can neither raise `_Interrupted` (our handler) nor `KeyboardInterrupt` (default) and skip ticket release. `_do_release` is wrapped in `try/except Exception` (a buggy hook is logged, not fatal), and `ticket.release()` lives in an innermost `finally` so it is absolutely unavoidable. Tested by `test_release_exception_still_releases_ticket`.
- *`child_holder` is cleared after `wait()`* (in a `finally` around the wait), so a signal arriving after the command exits but before handler restoration can't forward to / mis-detect the exited child and raise a spurious `_Interrupted`.

**Review fixes folded in (round 5):**

- *Signal handlers are always restored* — `ticket.release()` is wrapped in its own `try/finally` so the `signal.signal(... old)` restoration runs even if release raised, preventing the process from ever escaping with `SIGINT`/`SIGTERM` left at `SIG_IGN`.
- *`Ticket.release()` never raises* — the unlink is now a best-effort `try/except OSError` (no `os.path.exists` pre-check, so it's race-free against a concurrent reaper) and the lock-fd close stays swallowed, keeping cleanup idempotent.

**Type/name consistency checks:** `project_id.project_id/main_root/toplevel/is_root_cwd` used identically in Tasks 7 & 9 & 10. `queue_store.take_ticket → Ticket(number,path,sid,release())`, `holder`, `position`, `wait_for_turn`, `cancel`, `cancelled_reason`, `list_tickets`, `ticket_name` — all referenced consistently across Tasks 3, 4, 9, 10. `qsync.build_filters/rsync_command/parse_deletions/dry_run_deletions/classify_candidates/unclassified/in_sandbox/mark_sandbox/DEFAULT_PROTECT` — consistent in Tasks 5, 6, 9. `queue_config.add_resource/get_resource/list_resources/all_projects/is_opted_in/valid_resource_id/default_path_for` — consistent in Tasks 2, 9, 10. `queue_run.run_lease/queue_dir/REFUSAL_EXIT` — consistent in Tasks 9, 10. `exclusive.live_root_session/transition_guard` — consistent in Tasks 7, 9. `probes.health_check/wait_for` — consistent in Tasks 8, 9.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-06-shared-resource-queue-core.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
