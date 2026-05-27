# Multi-level folder hierarchy — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the first-dash folder convention with `/`-separated multi-level paths, move folder structure into a separate per-project store file, and rewire the TUI's new-folder/move flows to operate on multi-level paths.

**Architecture:** Pure tree-model functions (`split_path`, `build_nested_tree`) live in `bin/_pkg/tree_model.py`. A new `bin/_pkg/folder_store.py` owns the per-project flat path list at `~/.claude/session-explorer-folders.json` with the same flock + temp-file-rename atomic pattern the index uses. `record_session` auto-adds folder paths to the store when a session's name contains `/`. A one-shot migration runs at every CLI entry, moving any legacy `index.folders[]` entries into the new file under a synthetic `(unfiled)` project key and bumping the index from `version: 1` to `version: 2`. The TUI re-renders nested via the Textual `Tree` widget; `m` and `n` operate against the folder store with project scoping derived from the cursor.

**Tech Stack:** Python 3, Textual TUI, pytest (+ pytest-asyncio for TUI tests), JSON files under `~/.claude/`. No new dependencies.

**Reference:** `docs/superpowers/specs/2026-05-27-multi-level-folder-hierarchy-design.md`.

---

## File map

**New files:**
- `bin/_pkg/folder_store.py` — atomic per-project folder path store.
- `test/test_folder_store.py` — folder store tests.

**Modified:**
- `bin/_pkg/tree_model.py` — add `split_path`, `build_nested_tree`; remove `split_folder` and legacy `build_tree` at the end.
- `bin/_pkg/index.py` — `record_session` auto-adds folder paths; add `migrate_to_v2`.
- `bin/_pkg/cli.py` — run migration at top of `main`; switch `_cmd_list` to nested tree.
- `bin/_pkg/tui.py` — switch `_populate`, `MoveScreen`, `NewFolderScreen`, `action_move`, `action_new_folder` to the new model; replace `split_folder` usages with `split_path`.
- `bin/_pkg/__init__.py` — bump version if exposed (check during task).
- `SPEC.md`, `CLAUDE.md`, `README.md` — naming convention, keybindings, data model.
- `test/test_tree_model.py`, `test/test_index.py`, `test/test_tui.py`, `test/test_cli.py` — adjust assertions for new model.

**Removed:**
- `bin/_pkg/folders.py`
- `test/test_folders.py`

---

## Task 1: `split_path` in tree_model

**Files:**
- Modify: `bin/_pkg/tree_model.py`
- Test: `test/test_tree_model.py`

Add a new function alongside (not replacing) `split_folder`. Replacement happens in Task 10 after all callers move.

- [ ] **Step 1.1: Write failing tests**

Append to `test/test_tree_model.py`:

```python
from _pkg.tree_model import split_path


def test_split_path_none():
    assert split_path(None) == ([], "")


def test_split_path_empty():
    assert split_path("") == ([], "")


def test_split_path_no_slash():
    assert split_path("sprint14") == ([], "sprint14")


def test_split_path_one_slash():
    assert split_path("planning/sprint14") == (["planning"], "sprint14")


def test_split_path_many_slashes():
    assert split_path("team/planning/q1/notes") == (["team", "planning", "q1"], "notes")


def test_split_path_leading_slash_dropped():
    assert split_path("/planning/x") == (["planning"], "x")


def test_split_path_trailing_slash_dropped():
    assert split_path("planning/x/") == (["planning"], "x")


def test_split_path_double_slash_collapses():
    assert split_path("planning//x") == (["planning"], "x")


def test_split_path_whitespace_only_segments_dropped():
    assert split_path("planning/  /x") == (["planning"], "x")


def test_split_path_only_slashes_returns_empty():
    assert split_path("///") == ([], "")


def test_split_path_preserves_dashes_in_segments():
    """Dashes are no longer separators — they're literal characters in segments."""
    assert split_path("bugfix-watch/v2") == (["bugfix-watch"], "v2")
    assert split_path("bugfix-watch-lockup") == ([], "bugfix-watch-lockup")
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `PYTHONPATH=bin python3 -m pytest test/test_tree_model.py -k split_path -v`
Expected: every new test errors with `ImportError: cannot import name 'split_path'`.

- [ ] **Step 1.3: Implement `split_path`**

In `bin/_pkg/tree_model.py`, after the existing `split_folder` function, add:

```python
def split_path(name: "str | None") -> Tuple[List[str], str]:
    """Split a session name on `/` into folder segments + display name.

    The last non-empty segment is the display name; everything before it is the
    folder path. Empty segments (from `foo//bar`, leading/trailing `/`, or
    whitespace-only segments) are dropped. Returns ([], "") when there's no
    usable content.
    """
    if not name:
        return ([], "")
    segments = [seg.strip() for seg in name.split("/")]
    segments = [seg for seg in segments if seg]
    if not segments:
        return ([], "")
    return (segments[:-1], segments[-1])
```

Also update the imports at the top of the file:

```python
from typing import Dict, List, Tuple
```

(already there — verify and leave alone if so).

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `PYTHONPATH=bin python3 -m pytest test/test_tree_model.py -v`
Expected: all `split_path*` tests pass; existing `split_folder*` and `build_tree*` tests still pass.

- [ ] **Step 1.5: Commit**

```bash
git add bin/_pkg/tree_model.py test/test_tree_model.py
git commit -m "feat(tree_model): add split_path for /-separated folder paths"
```

---

## Task 2: `folder_store` module — load / save / mutate

**Files:**
- Create: `bin/_pkg/folder_store.py`
- Test: `test/test_folder_store.py`

Mirrors the I/O layer of `index.py` but for the folder store file.

- [ ] **Step 2.1: Write failing tests**

Create `test/test_folder_store.py`:

```python
"""Tests for _pkg.folder_store — atomic per-project folder path storage."""

import json
import os
import threading

from _pkg import folder_store


def test_load_missing_returns_default(tmp_path):
    assert folder_store.load(str(tmp_path / "absent.json")) == {
        "version": 1, "projects": {}
    }


def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "folders.json")
    payload = {"version": 1, "projects": {"acme-api": ["planning"]}}
    folder_store.save(path, payload)
    assert folder_store.load(path) == payload


def test_save_writes_via_temp_rename(tmp_path):
    path = str(tmp_path / "folders.json")
    folder_store.save(path, {"version": 1, "projects": {"x": []}})
    assert list(tmp_path.glob("*.tmp")) == []


def test_concurrent_writes_dont_corrupt(tmp_path):
    path = str(tmp_path / "folders.json")

    def worker(project: str):
        for i in range(50):
            folder_store.add(path, project, f"f{i}")

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    data = folder_store.load(path)
    assert len(data["projects"]["a"]) == 50
    assert len(data["projects"]["b"]) == 50


def test_default_path_for_index_sibling(tmp_path):
    idx = str(tmp_path / "session-explorer-index.json")
    expected = str(tmp_path / "session-explorer-folders.json")
    assert folder_store.default_path_for(idx) == expected
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `PYTHONPATH=bin python3 -m pytest test/test_folder_store.py -v`
Expected: `ImportError: No module named '_pkg.folder_store'`.

- [ ] **Step 2.3: Implement the I/O layer**

Create `bin/_pkg/folder_store.py`:

```python
"""Atomic, flock'd per-project folder path store for session-explorer.

Schema: {"version": 1, "projects": {project_label: [folder_path, ...]}}

Folder paths use `/` as separator and are stored as flat strings per project
(e.g. "planning", "planning/sprint14"). Intermediate folders are implicit —
storing "planning/sprint14" means "planning" is also part of the tree even
when not stored explicitly.

Concurrency mirrors index.py: read uses LOCK_SH; every mutate uses LOCK_EX on
a sibling .lock file plus a temp-file + atomic rename.
"""

import fcntl
import json
import os
import tempfile
from typing import Callable, Dict, Any, List

_DEFAULT: Dict[str, Any] = {"version": 1, "projects": {}}


def default_path_for(index_path: str) -> str:
    """Return the folder-store path that sits alongside `index_path`."""
    d = os.path.dirname(os.path.abspath(index_path)) or "."
    return os.path.join(d, "session-explorer-folders.json")


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"version": 1, "projects": {}}
    with open(path, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            return json.load(f)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def save(path: str, data: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-folders-", suffix=".tmp", dir=parent)
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
    """Read-modify-write under an exclusive flock on a sidecar lock file."""
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


def add(path: str, project: str, folder: str) -> None:
    """Idempotently add `folder` to `project`'s path list."""
    def mutator(data: dict) -> dict:
        projects = data.setdefault("projects", {})
        folders = projects.setdefault(project, [])
        if folder and folder not in folders:
            folders.append(folder)
        return data
    mutate(path, mutator)
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `PYTHONPATH=bin python3 -m pytest test/test_folder_store.py -v`
Expected: all tests pass.

- [ ] **Step 2.5: Commit**

```bash
git add bin/_pkg/folder_store.py test/test_folder_store.py
git commit -m "feat(folder_store): per-project flat path store with atomic writes"
```

---

## Task 3: `folder_store.remove` and `list_paths`

**Files:**
- Modify: `bin/_pkg/folder_store.py`
- Modify: `test/test_folder_store.py`

- [ ] **Step 3.1: Write failing tests**

Append to `test/test_folder_store.py`:

```python
def test_add_idempotent(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning")
    folder_store.add(path, "p1", "planning")
    assert folder_store.load(path)["projects"]["p1"] == ["planning"]


def test_add_multiple_projects_isolated(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning")
    folder_store.add(path, "p2", "bugfix")
    data = folder_store.load(path)
    assert data["projects"]["p1"] == ["planning"]
    assert data["projects"]["p2"] == ["bugfix"]


def test_remove_existing(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning")
    folder_store.add(path, "p1", "bugfix")
    folder_store.remove(path, "p1", "planning")
    assert folder_store.load(path)["projects"]["p1"] == ["bugfix"]


def test_remove_absent_is_noop(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning")
    folder_store.remove(path, "p1", "ghost")
    folder_store.remove(path, "no-such-project", "anything")
    assert folder_store.load(path)["projects"]["p1"] == ["planning"]


def test_list_paths_returns_sorted_copy(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning/sprint14")
    folder_store.add(path, "p1", "bugfix")
    folder_store.add(path, "p1", "planning")
    paths = folder_store.list_paths(path, "p1")
    assert paths == ["bugfix", "planning", "planning/sprint14"]
    # mutating the result must not affect storage
    paths.append("evil")
    assert folder_store.list_paths(path, "p1") == ["bugfix", "planning", "planning/sprint14"]


def test_list_paths_missing_project_returns_empty(tmp_path):
    path = str(tmp_path / "f.json")
    assert folder_store.list_paths(path, "p1") == []
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `PYTHONPATH=bin python3 -m pytest test/test_folder_store.py -v`
Expected: `remove` and `list_paths` tests fail with `AttributeError`.

- [ ] **Step 3.3: Implement `remove` and `list_paths`**

Append to `bin/_pkg/folder_store.py`:

```python
def remove(path: str, project: str, folder: str) -> None:
    """Remove `folder` from `project`'s path list. No-op if missing."""
    def mutator(data: dict) -> dict:
        projects = data.get("projects", {})
        if project in projects:
            projects[project] = [f for f in projects[project] if f != folder]
        return data
    mutate(path, mutator)


def list_paths(path: str, project: str) -> List[str]:
    """Return a sorted copy of `project`'s stored folder paths (may be empty)."""
    data = load(path)
    paths = data.get("projects", {}).get(project, [])
    return sorted(paths)
```

- [ ] **Step 3.4: Run tests to verify they pass**

Run: `PYTHONPATH=bin python3 -m pytest test/test_folder_store.py -v`
Expected: all tests pass.

- [ ] **Step 3.5: Commit**

```bash
git add bin/_pkg/folder_store.py test/test_folder_store.py
git commit -m "feat(folder_store): remove and list_paths"
```

---

## Task 4: Index schema v1 → v2 migration

**Files:**
- Modify: `bin/_pkg/index.py`
- Test: `test/test_index.py`

Adds `migrate_to_v2(index_path, folder_store_path)`. Idempotent: when `version` is already 2, returns immediately. When 1, moves any `folders[]` entries to the folder store under `(unfiled)` and rewrites the index without the `folders` key and with `version: 2`. Write order: folder store first, then index — so a crash leaves `version == 1` and the retry is safe (`folder_store.add` is idempotent).

- [ ] **Step 4.1: Write failing tests**

Append to `test/test_index.py`:

```python
def test_migrate_to_v2_moves_legacy_folders(tmp_path):
    from _pkg import folder_store
    idx_path = str(tmp_path / "index.json")
    fs_path = str(tmp_path / "folders.json")
    # Pre-existing v1 index with folders[].
    index.save(idx_path, {
        "version": 1,
        "folders": ["audits/q1", "planning"],
        "sessions": {},
    })
    index.migrate_to_v2(idx_path, fs_path)

    new_idx = index.load(idx_path)
    assert new_idx["version"] == 2
    assert "folders" not in new_idx
    assert new_idx["sessions"] == {}

    fs_data = folder_store.load(fs_path)
    assert fs_data["projects"]["(unfiled)"] == ["audits/q1", "planning"] or \
           sorted(fs_data["projects"]["(unfiled)"]) == ["audits/q1", "planning"]


def test_migrate_to_v2_is_idempotent(tmp_path):
    from _pkg import folder_store
    idx_path = str(tmp_path / "index.json")
    fs_path = str(tmp_path / "folders.json")
    index.save(idx_path, {"version": 1, "folders": ["a"], "sessions": {}})
    index.migrate_to_v2(idx_path, fs_path)
    # Second call is a no-op.
    index.migrate_to_v2(idx_path, fs_path)
    assert index.load(idx_path)["version"] == 2
    assert folder_store.load(fs_path)["projects"]["(unfiled)"] == ["a"]


def test_migrate_to_v2_v1_no_folders_field(tmp_path):
    """A v1 index with no folders[] key still bumps to v2 without touching the store."""
    from _pkg import folder_store
    idx_path = str(tmp_path / "index.json")
    fs_path = str(tmp_path / "folders.json")
    index.save(idx_path, {"version": 1, "sessions": {}})
    index.migrate_to_v2(idx_path, fs_path)
    assert index.load(idx_path)["version"] == 2
    # Folder store file not created when nothing to migrate.
    import os as _os
    assert not _os.path.exists(fs_path)
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `PYTHONPATH=bin python3 -m pytest test/test_index.py -k migrate -v`
Expected: `AttributeError: module '_pkg.index' has no attribute 'migrate_to_v2'`.

- [ ] **Step 4.3: Implement `migrate_to_v2`**

Append to `bin/_pkg/index.py` (after `refresh_all`):

```python
def migrate_to_v2(index_path: str, folder_store_path: str) -> None:
    """One-shot migration of the index from v1 (with flat `folders[]`) to v2
    (folders moved out to a separate file under a synthetic (unfiled) project).

    Idempotent. Order: write the folder store first, then the v2 index. A crash
    between leaves the index at v1; on retry, folder_store.add is idempotent.
    """
    from . import folder_store as _fs
    data = load(index_path)
    if data.get("version", 1) >= 2:
        return
    legacy = data.get("folders") or []
    for folder in legacy:
        _fs.add(folder_store_path, "(unfiled)", folder)
    def to_v2(d: dict) -> dict:
        d["version"] = 2
        d.pop("folders", None)
        return d
    mutate(index_path, to_v2)
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `PYTHONPATH=bin python3 -m pytest test/test_index.py -v`
Expected: all tests pass (new + existing).

- [ ] **Step 4.5: Commit**

```bash
git add bin/_pkg/index.py test/test_index.py
git commit -m "feat(index): migrate_to_v2 moves legacy folders[] to folder_store"
```

---

## Task 5: `record_session` auto-adds folder path on `/`-bearing names

**Files:**
- Modify: `bin/_pkg/index.py`
- Test: `test/test_index.py`

`record_session` is the single funnel for adding/refreshing session metadata. After it writes the session entry, if `name_cached` contains `/`, derive the folder path with `split_path` and add it to the folder store under that project.

The function gains a new optional `folder_store_path: str | None = None` argument. When `None`, it derives the path via `folder_store.default_path_for(index_path)`. Tests pass an explicit path.

- [ ] **Step 5.1: Write failing tests**

Append to `test/test_index.py`:

```python
def test_record_session_writes_folder_path_when_name_has_slash(tmp_path):
    """A session named foo/bar/baz should add foo/bar to the folder store under its project."""
    from _pkg import folder_store
    import json
    idx_path = str(tmp_path / "index.json")
    fs_path = str(tmp_path / "folders.json")
    # Build a JSONL with a custom-title containing /.
    jsonl = tmp_path / "S.jsonl"
    jsonl.write_text(
        '{"type":"user","sessionId":"S","cwd":"/u/x/acme-api",'
        '"timestamp":"2026-05-26T10:00:00Z",'
        '"message":{"role":"user","content":"hi"}}\n'
        '{"type":"custom-title","customTitle":"team/planning/sprint14","sessionId":"S"}\n'
    )
    index.record_session(idx_path, session_id="S",
                         transcript_path=str(jsonl), cwd="/u/x/acme-api",
                         folder_store_path=fs_path)
    paths = folder_store.list_paths(fs_path, "acme-api")
    assert paths == ["team/planning"]


def test_record_session_no_folder_write_when_name_has_no_slash(tmp_path):
    from _pkg import folder_store
    import os as _os
    idx_path = str(tmp_path / "index.json")
    fs_path = str(tmp_path / "folders.json")
    jsonl = tmp_path / "S.jsonl"
    jsonl.write_text(
        '{"type":"user","sessionId":"S","cwd":"/u/x/acme-api",'
        '"timestamp":"2026-05-26T10:00:00Z",'
        '"message":{"role":"user","content":"hi"}}\n'
        '{"type":"custom-title","customTitle":"sprint14","sessionId":"S"}\n'
    )
    index.record_session(idx_path, session_id="S",
                         transcript_path=str(jsonl), cwd="/u/x/acme-api",
                         folder_store_path=fs_path)
    # Folder store untouched (file never created — nothing to write).
    assert not _os.path.exists(fs_path)


def test_record_session_unnamed_does_not_touch_folder_store(tmp_path):
    from _pkg import folder_store
    import os as _os
    idx_path = str(tmp_path / "index.json")
    fs_path = str(tmp_path / "folders.json")
    jsonl = tmp_path / "S.jsonl"
    jsonl.write_text('{"type":"user","sessionId":"S","cwd":"/u/x/acme-api",'
                     '"timestamp":"2026-05-26T10:00:00Z",'
                     '"message":{"role":"user","content":"hi"}}\n')
    index.record_session(idx_path, session_id="S",
                         transcript_path=str(jsonl), cwd="/u/x/acme-api",
                         folder_store_path=fs_path)
    assert not _os.path.exists(fs_path)


def test_record_session_uses_default_folder_store_path(tmp_path):
    """When folder_store_path is None, derive a sibling of the index file."""
    from _pkg import folder_store
    idx_path = str(tmp_path / "session-explorer-index.json")
    jsonl = tmp_path / "S.jsonl"
    jsonl.write_text(
        '{"type":"user","sessionId":"S","cwd":"/u/x/acme-api",'
        '"timestamp":"2026-05-26T10:00:00Z",'
        '"message":{"role":"user","content":"hi"}}\n'
        '{"type":"custom-title","customTitle":"x/y","sessionId":"S"}\n'
    )
    index.record_session(idx_path, session_id="S",
                         transcript_path=str(jsonl), cwd="/u/x/acme-api")
    sibling = str(tmp_path / "session-explorer-folders.json")
    assert folder_store.list_paths(sibling, "acme-api") == ["x"]
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `PYTHONPATH=bin python3 -m pytest test/test_index.py -k "folder_path or folder_store or unnamed_does_not" -v`
Expected: tests fail because `record_session` doesn't accept the new arg / doesn't write the store.

- [ ] **Step 5.3: Modify `record_session`**

In `bin/_pkg/index.py`, replace the existing `record_session` signature and body with:

```python
def record_session(index_path: str, session_id: str, transcript_path: str,
                   cwd: str, folder_store_path: "str | None" = None) -> dict:
    """Idempotent upsert. Preserves 'notes' and any other user-edited fields.

    If the session's cached name contains `/`, the leading folder path is added
    (idempotently) to the per-project folder store. `folder_store_path` defaults
    to a sibling of `index_path`.
    """
    from . import folder_store as _fs
    from .tree_model import split_path

    def mutator(data: dict) -> dict:
        existing = data["sessions"].get(session_id, {})
        try:
            file_bytes = os.path.getsize(transcript_path)
        except FileNotFoundError:
            file_bytes = 0
        tokens = _jsonl.tokens_estimate(transcript_path)
        new_entry = {
            **existing,
            "name_cached": _jsonl.session_name(transcript_path),
            "first_prompt": _jsonl.first_user_prompt(transcript_path),
            "message_count": _jsonl.message_count(transcript_path),
            "bytes": file_bytes,
            "tokens_estimate": tokens,
            "tokens_window_pct": min(100, int(tokens * 100 / _TOKEN_WINDOW)),
            "project_path": cwd,
            "project_label": _project_label(cwd),
            "branch": _git_branch(cwd),
            "last_active_at": _jsonl.last_active_at(transcript_path) or datetime.now(timezone.utc).isoformat(),
            "transcript_path": transcript_path,
        }
        if "created_at" not in new_entry:
            new_entry["created_at"] = datetime.now(timezone.utc).isoformat()
        data["sessions"][session_id] = new_entry
        return data
    result = mutate(index_path, mutator)

    entry = result["sessions"][session_id]
    name = entry.get("name_cached") or ""
    if "/" in name:
        segments, _ = split_path(name)
        if segments:
            fs_path = folder_store_path or _fs.default_path_for(index_path)
            _fs.add(fs_path, entry["project_label"], "/".join(segments))
    return result
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `PYTHONPATH=bin python3 -m pytest test/test_index.py -v`
Expected: all tests pass, including existing record-session tests (they don't use `/` so folder store is untouched).

- [ ] **Step 5.5: Commit**

```bash
git add bin/_pkg/index.py test/test_index.py
git commit -m "feat(index): record_session auto-adds folder paths for /-bearing names"
```

---

## Task 6: `build_nested_tree` in tree_model

**Files:**
- Modify: `bin/_pkg/tree_model.py`
- Modify: `test/test_tree_model.py`

A new function alongside legacy `build_tree`. Returns the nested in-memory form described in the spec. Existing `build_tree` stays until Task 10.

Shape:
```python
{
  "project_label": {
    "_sessions": [(sid, s), ...],            # sorted last_active_at desc
    "_folders": {
      "segment": {"_sessions": [...], "_folders": {...}},
      ...
    },
  },
}
```

- [ ] **Step 6.1: Write failing tests**

Append to `test/test_tree_model.py`:

```python
from _pkg.tree_model import build_nested_tree


def _fs_data(projects):
    return {"version": 1, "projects": dict(projects)}


def test_build_nested_tree_root_session_no_slash():
    idx = _idx({
        "a": {"project_label": "acme-api", "name_cached": "sprint14",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({"acme-api": []})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    assert list(t.keys()) == ["acme-api"]
    proj = t["acme-api"]
    assert [sid for sid, _ in proj["_sessions"]] == ["a"]
    assert proj["_folders"] == {}


def test_build_nested_tree_session_with_path_creates_intermediates():
    idx = _idx({
        "a": {"project_label": "acme-api",
              "name_cached": "team/planning/sprint14",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    team = t["acme-api"]["_folders"]["team"]
    planning = team["_folders"]["planning"]
    assert team["_sessions"] == []
    assert planning["_sessions"] != []
    sid, s = planning["_sessions"][0]
    assert sid == "a"
    assert s["name_cached"] == "team/planning/sprint14"


def test_build_nested_tree_stored_path_creates_empty_folders():
    idx = _idx({})
    fs = _fs_data({"acme-api": ["planning/sprint14"]})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    planning = t["acme-api"]["_folders"]["planning"]
    sprint = planning["_folders"]["sprint14"]
    assert planning["_sessions"] == []
    assert sprint["_sessions"] == []
    assert sprint["_folders"] == {}


def test_build_nested_tree_sessions_sorted_desc_within_folder():
    idx = _idx({
        "a": {"project_label": "p", "name_cached": "x/a",
              "last_active_at": "2026-05-26T10:00:00Z"},
        "b": {"project_label": "p", "name_cached": "x/b",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    sids = [sid for sid, _ in t["p"]["_folders"]["x"]["_sessions"]]
    assert sids == ["b", "a"]


def test_build_nested_tree_unnamed_hidden_by_default():
    idx = _idx({
        "u": {"project_label": "p", "name_cached": None,
              "last_active_at": "2026-05-27T10:00:00Z"},
        "n": {"project_label": "p", "name_cached": "kept",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    sids = [sid for sid, _ in t["p"]["_sessions"]]
    assert sids == ["n"]
    assert "(unnamed)" not in t["p"]["_folders"]


def test_build_nested_tree_unnamed_surfaced_in_pseudo_folder():
    idx = _idx({
        "u": {"project_label": "p", "name_cached": None,
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({})
    t = build_nested_tree(idx, fs, include_unnamed=True)
    assert "(unnamed)" in t["p"]["_folders"]
    assert [sid for sid, _ in t["p"]["_folders"]["(unnamed)"]["_sessions"]] == ["u"]


def test_build_nested_tree_unfiled_project_appears_when_store_has_it():
    idx = _idx({})
    fs = _fs_data({"(unfiled)": ["legacy-shelf"]})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    assert "(unfiled)" in t
    assert "legacy-shelf" in t["(unfiled)"]["_folders"]
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `PYTHONPATH=bin python3 -m pytest test/test_tree_model.py -k build_nested -v`
Expected: ImportError for `build_nested_tree`.

- [ ] **Step 6.3: Implement `build_nested_tree`**

In `bin/_pkg/tree_model.py`, after `build_tree`, add:

```python
def _empty_node() -> dict:
    return {"_sessions": [], "_folders": {}}


def _walk_to(node: dict, segments: List[str]) -> dict:
    """Walk into the nested tree, creating empty folder nodes as needed."""
    for seg in segments:
        node = node["_folders"].setdefault(seg, _empty_node())
    return node


def build_nested_tree(index_data: dict, folder_store_data: dict,
                      include_unnamed: bool = False) -> Dict[str, dict]:
    """Nested project → folder → folder ... → sessions, the form the TUI renders.

    Each node is {"_sessions": [(sid, s)], "_folders": {seg: node, ...}}.

    Unnamed sessions: hidden by default. When include_unnamed=True they appear
    under a synthetic "(unnamed)" folder per project (preserves prior UX).
    """
    out: Dict[str, dict] = {}

    # 1. Place each session into its project + folder path.
    for sid, s in index_data.get("sessions", {}).items():
        name = s.get("name_cached")
        if not name and not include_unnamed:
            continue
        project = s.get("project_label") or "(unknown)"
        proj_node = out.setdefault(project, _empty_node())
        if not name:
            target = proj_node["_folders"].setdefault("(unnamed)", _empty_node())
        else:
            segments, _ = split_path(name)
            target = _walk_to(proj_node, segments)
        target["_sessions"].append((sid, s))

    # 2. Lay in stored folder paths (may create empty folder nodes).
    for project, paths in (folder_store_data.get("projects") or {}).items():
        proj_node = out.setdefault(project, _empty_node())
        for path_str in paths or []:
            segs = [seg for seg in path_str.split("/") if seg.strip()]
            _walk_to(proj_node, segs)

    # 3. Sort every _sessions list newest-first.
    def sort_node(node: dict):
        node["_sessions"].sort(key=lambda x: x[1].get("last_active_at", ""), reverse=True)
        for child in node["_folders"].values():
            sort_node(child)
    for proj in out.values():
        sort_node(proj)

    return out
```

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `PYTHONPATH=bin python3 -m pytest test/test_tree_model.py -v`
Expected: all tests pass (new + legacy).

- [ ] **Step 6.5: Commit**

```bash
git add bin/_pkg/tree_model.py test/test_tree_model.py
git commit -m "feat(tree_model): build_nested_tree returns project→folder→sessions nesting"
```

---

## Task 7: CLI `_cmd_list` switches to nested rendering, run migration in `main`

**Files:**
- Modify: `bin/_pkg/cli.py`
- Modify: `test/test_cli.py`

`_cmd_list` walks the nested tree and prints each session with its full folder path as a prefix. Stored-only empty folders print as `path/  (empty)`. The CLI's `main` calls `index.migrate_to_v2` at the top so every entry point auto-migrates.

- [ ] **Step 7.1: Read current `_cmd_list` and `main`**

Read `bin/_pkg/cli.py` start to end so the next edits are precisely targeted.

- [ ] **Step 7.2: Update tests for new output**

Modify `test/test_cli.py::test_list_groups_by_project_and_folder` to match the new format. Replace its body with:

```python
def test_list_groups_by_project_and_folder(tmp_path):
    transcript = tmp_path / "01ABC.jsonl"
    shutil.copy(os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = tmp_path / "index.json"
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(idx_path)}
    subprocess.run(
        [_BIN, "index", "--record", "01ABC", str(transcript), "/Users/jl/proj/foo"],
        check=True, env=env,
    )
    result = subprocess.run([_BIN, "list"], capture_output=True, text=True, env=env)
    assert result.returncode == 0
    out = result.stdout
    assert "foo" in out                                     # project label
    # named.jsonl's custom-title is "planning-sprint14-custom" → root row (no /).
    assert "planning-sprint14-custom" in out
```

- [ ] **Step 7.3: Add a test for `/`-path rendering**

Append to `test/test_cli.py`:

```python
def test_list_renders_slash_path_as_nested(tmp_path):
    """A session with a /-bearing name renders under its folder path in the list."""
    transcript = tmp_path / "02XYZ.jsonl"
    transcript.write_text(
        '{"type":"user","sessionId":"02XYZ","cwd":"/u/p/foo",'
        '"timestamp":"2026-05-27T10:00:00Z",'
        '"message":{"role":"user","content":"plan"}}\n'
        '{"type":"custom-title","customTitle":"planning/sprint14","sessionId":"02XYZ"}\n'
    )
    idx_path = tmp_path / "index.json"
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(idx_path)}
    subprocess.run(
        [_BIN, "index", "--record", "02XYZ", str(transcript), "/u/p/foo"],
        check=True, env=env,
    )
    result = subprocess.run([_BIN, "list"], capture_output=True, text=True, env=env)
    assert result.returncode == 0
    out = result.stdout
    assert "foo" in out
    # Folder header printed as a path, session indented under it.
    assert "planning/" in out
    assert "sprint14" in out
```

- [ ] **Step 7.4: Run tests to verify failure**

Run: `PYTHONPATH=bin python3 -m pytest test/test_cli.py -v`
Expected: list-format tests fail (legacy formatter still in place).

- [ ] **Step 7.5: Rewrite `_cmd_list`**

Replace `_cmd_list` and `main` in `bin/_pkg/cli.py`:

```python
def _cmd_list() -> int:
    from . import folder_store as _fs
    idx_path = _index_path()
    data = _index.load(idx_path)
    fs_data = _fs.load(_fs.default_path_for(idx_path))
    if not data.get("sessions") and not fs_data.get("projects"):
        print("No sessions recorded yet.")
        return 0

    tree = build_nested_tree(data, fs_data, include_unnamed=True)

    def total(node):
        return len(node["_sessions"]) + sum(total(c) for c in node["_folders"].values())

    for proj in sorted(tree):
        node = tree[proj]
        print(f"\n{proj} ({total(node)})")
        # Root-level sessions first.
        for sid, s in node["_sessions"]:
            _print_session_row(sid, s, indent="  ")
        # Then folders, recursively, with path prefix.
        _print_subtree(node["_folders"], prefix="")
    return 0


def _print_session_row(sid, s, indent: str) -> None:
    _, display = split_path(s.get("name_cached"))
    display = display or sid[:8]
    age = fmt_age(s.get("last_active_at"))
    tokens = fmt_tokens(s.get("tokens_estimate", 0))
    pct = s.get("tokens_window_pct", 0)
    msgs = s.get("message_count", 0)
    prompt = (s.get("first_prompt") or "").replace("\n", " ")[:40]
    print(f"{indent}{display:<24} {age:>4}  {tokens:>6} ({pct:>3}%)  {msgs:>4} msgs   {prompt}")


def _print_subtree(folders: dict, prefix: str) -> None:
    for name in sorted(folders):
        child = folders[name]
        path = f"{prefix}{name}"
        if not child["_sessions"] and not child["_folders"]:
            print(f"  {path}/  (empty)")
            continue
        print(f"  {path}/")
        for sid, s in child["_sessions"]:
            _print_session_row(sid, s, indent="    ")
        _print_subtree(child["_folders"], prefix=path + "/")


def main(argv: list[str] | None = None) -> int:
    from . import folder_store as _fs
    parser = build_parser()
    args = parser.parse_args(argv)
    # Run schema migration once per invocation (idempotent, very cheap).
    idx_path = _index_path()
    try:
        _index.migrate_to_v2(idx_path, _fs.default_path_for(idx_path))
    except Exception:
        pass  # never block the CLI on migration; the next invocation retries
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "index":
        return _cmd_index(args)
    if args.cmd == "list":
        return _cmd_list()
    if args.cmd == "launch":
        return _cmd_launch()
    if args.cmd == "tui":
        from .tui import run
        return run()
    print(f"(not implemented) cmd={args.cmd}", file=sys.stderr)
    return 2
```

Add the imports at the top of `cli.py`:

```python
from .tree_model import build_nested_tree, split_path
```

(Remove `build_tree, split_folder` from that import line.)

- [ ] **Step 7.6: Verify `tui.py` is untouched**

This task only modifies `cli.py`. `tui.py` still imports `from .tree_model import build_tree, split_folder` — those functions still exist in `tree_model.py` until Task 11. Confirm no accidental edits to `tui.py`:

```bash
git diff --stat bin/_pkg/tui.py
```

Expected: no output (file unchanged).

- [ ] **Step 7.7: Run the full suite**

Run: `PYTHONPATH=bin python3 -m pytest -q`
Expected: all tests pass. (CLI tests use the new format; TUI tests are unaffected because `tui.py` still uses legacy `build_tree` / `split_folder`.)

- [ ] **Step 7.8: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(cli): nested tree rendering + auto-run v1→v2 migration"
```

---

## Task 8: TUI nested rendering + multi-level move (one commit)

**Files:**
- Modify: `bin/_pkg/tui.py`
- Modify: `test/test_tui.py`

This task bundles `_populate`, `_row_label`, `MoveScreen`, and `action_move` together because the test changes for the move flow cross both `_populate` (nested rendering) and `MoveScreen` (new API). Committing them together keeps the suite green throughout.

The new `_row_label(sid, s, depth)` chooses the name-field width from the leaf's actual depth in the tree, replacing the old binary grouped/ungrouped switch.

- [ ] **Step 8.1: Update existing `_row_label` unit tests for the new depth-based signature**

In `test/test_tui.py`, find `test_row_label_columns_align_across_depth` and `test_long_name_truncates_to_field_width` and replace them with:

```python
def test_row_label_columns_align_across_depth():
    """A leaf one level shallower (depth=1, ungrouped) and a leaf one level
    deeper (depth=2, folder-grouped) must place stat columns at the same
    absolute screen column. In the bare row string this means the stat suffix
    sits at `name_w` in each, and that `name_w` differs by GUIDE_DEPTH, which
    exactly equals one tree-indent level."""
    from _pkg.tui import _row_label, _stat_suffix, NAME_W, GUIDE_DEPTH
    s = {"name_cached": "x", "last_active_at": None,
         "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 7,
         "first_prompt": "hello"}
    ungrouped = _row_label("sid", s, depth=1)
    grouped = _row_label("sid", s, depth=2)
    # At depth=2: name_w = NAME_W. At depth=1: name_w = NAME_W + GUIDE_DEPTH.
    name_w_grouped = NAME_W
    name_w_ungrouped = NAME_W + GUIDE_DEPTH
    assert grouped[name_w_grouped:] == ungrouped[name_w_ungrouped:]
    assert grouped[name_w_grouped:] == _stat_suffix("—", "~0", "(0%)", "7", "msgs", "hello")


def test_long_name_truncates_to_field_width():
    from _pkg.tui import _row_label, NAME_W
    s = {"name_cached": "a" * 100, "last_active_at": None,
         "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
         "first_prompt": ""}
    # depth=2 → name_w == NAME_W
    row = _row_label("sid", s, depth=2)
    assert row[:NAME_W].endswith("…")
    assert row[NAME_W] == " "  # stat suffix's leading space sits exactly at NAME_W
```

- [ ] **Step 8.2: Write a test for nested rendering**

Append to `test/test_tui.py`:

```python
async def test_populate_renders_nested_folders(index_path, tmp_path):
    """A session named foo/bar should render as project → foo/ → bar leaf."""
    import json
    data = json.load(open(index_path))
    data["sessions"]["sid-nested"] = {
        "project_label": "demo",
        "project_path": "/tmp/demo",
        "name_cached": "planning/sprint99",
        "last_active_at": "2026-05-27T11:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Find the planning/ folder node and check its child carries sid-nested.
        def find(node, label_contains):
            for c in node.children:
                if label_contains in str(c.label):
                    return c
                got = find(c, label_contains)
                if got:
                    return got
            return None
        planning = find(app._tree.root, "planning/")
        assert planning is not None
        leaf = next((c for c in planning.children if c.data and c.data.get("sid") == "sid-nested"), None)
        assert leaf is not None
```

- [ ] **Step 8.3: Run the test to verify it fails**

Run: `PYTHONPATH=bin python3 -m pytest test/test_tui.py::test_populate_renders_nested_folders -v`
Expected: fails (the test data has `/` but legacy `split_folder` produces `("", "planning/sprint99")` so it lands at root).

- [ ] **Step 8.4: Rewrite `_populate` and switch tree_model imports**

In `bin/_pkg/tui.py`:

1. Change the imports near the top from:
```python
from .tree_model import build_tree, split_folder
```
to:
```python
from .tree_model import build_nested_tree, split_path
```

2. Replace `_row_label` so it accepts the leaf depth:

```python
def _row_label(sid: str, s: dict, depth: int) -> str:
    """Leaf row. `depth` is the number of tree levels above the leaf
    (project = 1 level above ungrouped leaves; folder above that = 2 levels;
    etc.). Used to choose the name_field width so stat columns align."""
    _, display = split_path(s.get("name_cached"))
    display = display or sid[:8]
    # Each level of indent steals GUIDE_DEPTH cells from the name field.
    # depth=1 (root child) → widest field; depth=2 → minus 1*G; depth=3 → minus 2*G…
    name_w = max(8, NAME_W + 2 * GUIDE_DEPTH - depth * GUIDE_DEPTH)
    if len(display) > name_w:
        display = display[: name_w - 1] + "…"
    age = fmt_age(s.get("last_active_at"))
    tokens = fmt_tokens(s.get("tokens_estimate", 0))
    pct = fmt_pct(s.get("tokens_window_pct", 0))
    msgs = str(s.get("message_count", 0))
    prompt = (s.get("first_prompt") or "").replace("\n", " ")[:40]
    return f"{display:<{name_w}}" + _stat_suffix(age, tokens, pct, msgs, "msgs", prompt)
```

3. Replace `_column_header()` — the constant offset still corresponds to a depth-2 leaf (the common case):

```python
def _column_header() -> str:
    """Header line whose labels sit above the stat columns. Pads to a depth-2
    leaf's absolute stat offset (2 levels of guide × GUIDE_DEPTH + NAME_W)."""
    name_region = NAME_W + 2 * GUIDE_DEPTH
    return f"{'NAME':<{name_region}}" + _stat_suffix("AGE", "~TOK", "CTX", "MSGS", "    ", "FIRST PROMPT")
```

4. Replace `_populate`:

```python
def _populate(self) -> None:
    from . import folder_store as _fs
    self._tree.clear()
    data = _index.load(self._index_path)
    fs_data = _fs.load(_fs.default_path_for(self._index_path))
    tree = build_nested_tree(data, fs_data, include_unnamed=self._show_unnamed)
    unnamed_hidden = 0
    if not self._show_unnamed:
        unnamed_hidden = sum(
            1 for s in data.get("sessions", {}).values() if not s.get("name_cached")
        )
    root = self._tree.root
    root.expand()

    def count(node):
        return len(node["_sessions"]) + sum(count(c) for c in node["_folders"].values())

    total = sum(count(p) for p in tree.values())
    if unnamed_hidden:
        self.sub_title = f"{total} sessions across {len(tree)} projects · {unnamed_hidden} unnamed hidden (u)"
    else:
        self.sub_title = f"{total} sessions across {len(tree)} projects"

    def render(parent, node, depth):
        for sid, s in node["_sessions"]:
            if self._matches(sid, s):
                parent.add_leaf(_row_label(sid, s, depth + 1), data={"sid": sid, **s})
        for name in sorted(node["_folders"]):
            child = node["_folders"][name]
            folder_node = parent.add(f"{name}/", expand=True)
            render(folder_node, child, depth + 1)

    for project in sorted(tree):
        node = tree[project]
        proj_node = root.add(f"{project} ({count(node)})", expand=True)
        render(proj_node, node, depth=1)
```

- [ ] **Step 8.5: Update the existing move tests for the new MoveScreen API and `/` join**

In `test/test_tui.py`, replace `test_move_changes_folder` with:

```python
async def test_move_changes_folder(index_path, tmp_path):
    """Moving a session to folder 'release' rewrites its custom-title to release/<display>."""
    import json
    data = json.load(open(index_path))
    transcript = tmp_path / "t2.jsonl"
    transcript.write_text('{"type":"user","uuid":"u1"}\n')
    data["sessions"]["sid-1"]["name_cached"] = "planning/sprint14"
    data["sessions"]["sid-1"]["transcript_path"] = str(transcript)
    data["sessions"]["sid-2"] = {
        "project_label": "demo", "project_path": "/tmp/demo",
        "name_cached": "archive/old",
        "last_active_at": "2026-01-01T00:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp, MoveScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Navigate to sid-1's leaf by searching.
        def find_leaf(node, sid):
            for c in node.children:
                if c.data and c.data.get("sid") == sid:
                    return c
                got = find_leaf(c, sid)
                if got:
                    return got
            return None
        leaf = find_leaf(app._tree.root, "sid-1")
        assert leaf is not None
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        app.screen.dismiss("release")
        await pilot.pause()

    name = json.load(open(index_path))["sessions"]["sid-1"]["name_cached"]
    assert name == "release/sprint14"
    last = json.loads(transcript.read_text().splitlines()[-1])
    assert last == {"type": "custom-title", "customTitle": "release/sprint14", "sessionId": "sid-1"}


async def test_move_ungroup_unnamed_session_uses_sid_prefix(index_path, tmp_path):
    """Regression: move-to-(ungroup) of an unnamed session must write a non-empty
    customTitle (sid[:8]) and must not contain /."""
    import json
    data = json.load(open(index_path))
    transcript = tmp_path / "tu.jsonl"
    transcript.write_text('{"type":"user"}\n')
    data["sessions"]["unnamed-sid-xyz"] = {
        "project_label": "demo", "project_path": "/tmp/demo",
        "name_cached": None,
        "last_active_at": "2026-05-25T00:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
        "transcript_path": str(transcript),
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    from textual.screen import ModalScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u"); await pilot.pause()  # surface unnamed
        def find(node, sid):
            for c in node.children:
                if c.data and c.data.get("sid") == sid:
                    return c
                got = find(c, sid)
                if got: return got
            return None
        leaf = find(app._tree.root, "unnamed-sid-xyz")
        assert leaf is not None
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        app.screen.dismiss("")  # (ungroup)
        await pilot.pause()

    name = json.load(open(index_path))["sessions"]["unnamed-sid-xyz"]["name_cached"]
    assert name == "unnamed-"
    assert "/" not in name
    last = json.loads(transcript.read_text().splitlines()[-1])
    assert last["customTitle"] == "unnamed-"


async def test_move_to_new_path_adds_to_folder_store(index_path, tmp_path):
    """Typing a new path in MoveScreen auto-creates it in the folder store."""
    import json
    from _pkg import folder_store
    data = json.load(open(index_path))
    transcript = tmp_path / "tn.jsonl"
    transcript.write_text('{"type":"user"}\n')
    data["sessions"]["sid-1"]["transcript_path"] = str(transcript)
    data["sessions"]["sid-1"]["name_cached"] = "sprint14"
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp, MoveScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # cursor onto sid-1
        def find(node, sid):
            for c in node.children:
                if c.data and c.data.get("sid") == sid: return c
                got = find(c, sid)
                if got: return got
            return None
        leaf = find(app._tree.root, "sid-1")
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        app.screen.dismiss("team/new-folder")  # auto-creates this path
        await pilot.pause()

    fs_path = folder_store.default_path_for(index_path)
    paths = folder_store.list_paths(fs_path, "demo")
    assert "team/new-folder" in paths
    assert json.load(open(index_path))["sessions"]["sid-1"]["name_cached"] == "team/new-folder/sprint14"
```

- [ ] **Step 8.6: Rewrite `MoveScreen` and `action_move` in `bin/_pkg/tui.py`**

Replace `MoveScreen` with:

```python
class MoveScreen(ModalScreen[str]):
    """Pick or type a folder path (e.g. 'planning/sprint14').

    Returns "" to ungroup, the chosen/typed path string, or None on cancel.
    """

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, project: str, existing_paths: list[str], current: str) -> None:
        super().__init__()
        self._project = project
        self._existing = sorted(set(existing_paths))
        self._current = current

    def compose(self) -> ComposeResult:
        opts = [Option("(ungroup)", id="__none__")] + [
            Option(p, id=p) for p in self._existing
        ]
        yield Vertical(
            Label(
                f"Move within '{self._project}' (current: {self._current or '(none)'})."
                " Pick or type a path (use / for nesting):"
            ),
            OptionList(*opts, id="move-list"),
            Input(placeholder="…or type a new path (e.g. team/planning)", id="move-input"),
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        self.dismiss("" if opt_id == "__none__" else opt_id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())
```

Replace `action_move` with:

```python
def action_move(self) -> None:
    from . import folder_store as _fs
    node = self._tree.cursor_node
    if not node or not node.data or "sid" not in node.data:
        self.bell(); return
    sid = node.data["sid"]
    name = node.data.get("name_cached") or ""
    transcript = node.data.get("transcript_path")
    project = node.data.get("project_label")
    if not project:
        self.bell(); return
    segments, display = split_path(name)
    current_folder = "/".join(segments)

    fs_path = _fs.default_path_for(self._index_path)
    # Folder list = store ∪ folders implied by indexed session names in this project.
    paths = set(_fs.list_paths(fs_path, project))
    data = _index.load(self._index_path)
    for s in data.get("sessions", {}).values():
        if s.get("project_label") != project:
            continue
        segs, _ = split_path(s.get("name_cached"))
        for i in range(1, len(segs) + 1):
            paths.add("/".join(segs[:i]))

    def after(target: "str | None") -> None:
        if target is None or not transcript:
            return
        leaf = display or sid[:8]
        new_name = leaf if not target else f"{target}/{leaf}"
        from .rename import append_custom_title
        append_custom_title(transcript, session_id=sid, new_name=new_name)
        if target:
            _fs.add(fs_path, project, target)
        def _mut(d: dict) -> dict:
            d["sessions"].setdefault(sid, {})["name_cached"] = new_name
            return d
        _index.mutate(self._index_path, _mut)
        self._populate()

    self.push_screen(MoveScreen(project, sorted(paths), current_folder), after)
```

- [ ] **Step 8.7: Run the suite**

Run: `PYTHONPATH=bin python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 8.8: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): nested folder rendering + multi-level move via folder_store"
```

---

## Task 9: TUI `n` (new folder) — context-aware via folder store

**Files:**
- Modify: `bin/_pkg/tui.py`
- Modify: `test/test_tui.py`

`n` infers the project from the cursor node and pre-fills the path so creating a child folder is one fewer keystroke.

- [ ] **Step 9.1: Replace the existing test**

In `test/test_tui.py`, replace `test_new_folder_adds_to_index` with:

```python
async def test_new_folder_under_project_adds_to_folder_store(index_path):
    """`n` on a project node creates a top-level folder in that project."""
    from _pkg.tui import SessionExplorerApp, NewFolderScreen
    from _pkg import folder_store
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # cursor sits on the demo project (first child of the hidden root).
        proj = app._tree.root.children[0]
        app._tree.select_node(proj); app._tree.cursor_line = proj.line
        await pilot.pause()
        await pilot.press("n"); await pilot.pause()
        assert isinstance(app.screen, NewFolderScreen)
        app.screen.dismiss("audits/q1")
        await pilot.pause()

    fs_path = folder_store.default_path_for(index_path)
    assert "audits/q1" in folder_store.list_paths(fs_path, "demo")


async def test_new_folder_under_folder_creates_child(index_path):
    """`n` on a folder node creates a child path under it."""
    from _pkg.tui import SessionExplorerApp, NewFolderScreen
    from _pkg import folder_store
    import json
    data = json.load(open(index_path))
    data["sessions"]["sid-1"]["name_cached"] = "planning/sprint14"
    json.dump(data, open(index_path, "w"))

    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Find the planning/ folder node.
        def find(node, label_contains):
            for c in node.children:
                if label_contains in str(c.label): return c
                got = find(c, label_contains)
                if got: return got
            return None
        planning = find(app._tree.root, "planning/")
        assert planning is not None
        app._tree.select_node(planning); app._tree.cursor_line = planning.line
        await pilot.pause()
        await pilot.press("n"); await pilot.pause()
        assert isinstance(app.screen, NewFolderScreen)
        # The modal must prefill with "planning/" — the engineer types "retro".
        screen = app.screen
        assert screen._prefix == "planning/"
        screen.dismiss("planning/retro")
        await pilot.pause()

    fs_path = folder_store.default_path_for(index_path)
    paths = folder_store.list_paths(fs_path, "demo")
    assert "planning/retro" in paths
```

- [ ] **Step 9.2: Run the tests to verify failure**

Run: `PYTHONPATH=bin python3 -m pytest test/test_tui.py -k new_folder -v`
Expected: fails on `NewFolderScreen` API (no `_prefix`), folder store empty, etc.

- [ ] **Step 9.3: Rewrite `NewFolderScreen` and `action_new_folder`**

In `bin/_pkg/tui.py`, replace `NewFolderScreen`:

```python
class NewFolderScreen(ModalScreen[str]):
    """Prompt for a folder path. The Input is prefilled with `prefix` (which
    ends in '/' when creating a child of an existing folder)."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def __init__(self, project: str, prefix: str = "") -> None:
        super().__init__()
        self._project = project
        self._prefix = prefix

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New folder path under '{self._project}' (use / for nesting):"),
            Input(value=self._prefix, id="newfolder-input"),
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())
```

Replace `action_new_folder`:

```python
def action_new_folder(self) -> None:
    from . import folder_store as _fs
    project, prefix = self._project_and_prefix_for_cursor()
    if not project:
        self.bell(); return

    def after(path: str) -> None:
        if not path:
            return
        # Normalize: collapse repeated/leading/trailing slashes; drop empty segs.
        segs = [seg.strip() for seg in path.split("/") if seg.strip()]
        if not segs:
            return
        _fs.add(_fs.default_path_for(self._index_path), project, "/".join(segs))
        self._populate()

    self.push_screen(NewFolderScreen(project, prefix), after)


def _project_and_prefix_for_cursor(self) -> "tuple[str | None, str]":
    """Return (project_label, prefix). prefix ends in '/' when the cursor sits
    on a folder so child creation is one segment away from done."""
    node = self._tree.cursor_node
    if node is None or node is self._tree.root:
        return (None, "")
    # Walk ancestors, collecting folder segments, until we hit a project node
    # (a direct child of root) or a session leaf (which we treat as its parent).
    if node.data and "sid" in node.data:
        node = node.parent  # treat session leaf as its containing folder/project
    path: list[str] = []
    while node is not None and node.parent is not self._tree.root and node is not self._tree.root:
        # Folder labels look like "<segment>/". Strip the trailing slash.
        label = str(node.label)
        if label.endswith("/"):
            path.insert(0, label[:-1])
        node = node.parent
    if node is None or node.parent is not self._tree.root:
        return (None, "")
    # Project node label looks like "<project> (count)" — strip the count suffix.
    proj_label = str(node.label).rsplit(" (", 1)[0]
    prefix = "/".join(path) + "/" if path else ""
    return (proj_label, prefix)
```

- [ ] **Step 9.4: Run the suite**

Run: `PYTHONPATH=bin python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 9.5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): context-aware new-folder action backed by folder_store"
```

---

## Task 10: Cleanup — remove legacy `split_folder`, `build_tree`, `folders.py`

**Files:**
- Modify: `bin/_pkg/tree_model.py`
- Delete: `bin/_pkg/folders.py`
- Delete: `test/test_folders.py`
- Modify: `test/test_tree_model.py`

- [ ] **Step 10.1: Verify no remaining callers**

Run: `grep -rn "split_folder\|from .folders\|from _pkg.folders\|build_tree" bin/_pkg/ test/ | grep -v build_nested_tree`
Expected: only matches inside `bin/_pkg/tree_model.py` itself (the legacy definitions) and `test/test_tree_model.py` (legacy tests). Anything else means a caller still references the old API — fix that first.

- [ ] **Step 10.2: Delete legacy tests in `test/test_tree_model.py`**

Remove every test whose name starts with `test_split_folder_` and every test that imports / asserts against `build_tree` (the legacy flat version). Keep only `split_path` tests and `build_nested_tree` tests.

- [ ] **Step 10.3: Delete `test/test_folders.py`**

```bash
git rm test/test_folders.py
```

- [ ] **Step 10.4: Delete `bin/_pkg/folders.py`**

```bash
git rm bin/_pkg/folders.py
```

- [ ] **Step 10.5: Remove legacy definitions from `tree_model.py`**

In `bin/_pkg/tree_model.py`:
- Delete `split_folder`.
- Delete `build_tree` (the legacy flat version).
- Delete the `ProjectsTree` type alias if it's only used by the removed function.
- Update the module docstring at the top to describe the new model only.

The remaining file should contain only: imports, `split_path`, `_empty_node`, `_walk_to`, and `build_nested_tree`.

- [ ] **Step 10.6: Run the suite**

Run: `PYTHONPATH=bin python3 -m pytest -q`
Expected: all tests pass.

- [ ] **Step 10.7: Commit**

```bash
git add bin/_pkg/tree_model.py bin/_pkg/folders.py test/test_folders.py test/test_tree_model.py
git commit -m "chore: drop legacy split_folder, build_tree, folders.py"
```

---

## Task 11: Update `SPEC.md`, `CLAUDE.md`, `README.md`

**Files:**
- Modify: `SPEC.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 11.1: Update `SPEC.md` § Naming and folders**

Replace the current naming block (the one starting with "The session's Claude-assigned name encodes both folder and name") with:

```markdown
The session's Claude-assigned name encodes folder path + display name via `/`:

```
<segment>/<segment>/…/<display>   → all but the last segment → folder path; last → display name
<just-a-name>  (no /)             → at project root; display = name
(no name)                         → hidden by default; toggle with [u] to surface for renaming or deletion
```

Empty segments (from `foo//bar`, leading/trailing `/`, or whitespace-only
segments) are dropped during parsing. Dashes have no special meaning —
`bugfix-watch-lockup` displays as one name at the project root.

| Session name | Folder path | Display name |
|---|---|---|
| `planning/sprint14` | `planning` | `sprint14` |
| `audits/q1-review` | `audits` | `q1-review` |
| `team/planning/q1` | `team/planning` | `q1` |
| `sprint14` | *(none)* | `sprint14` |
```

- [ ] **Step 11.2: Update `SPEC.md` § Data model**

In the existing index example JSON, ensure `version` is `2` and there is no `folders` key (it moved out). Add a new section for the folder store:

```markdown
### Folder store — `~/.claude/session-explorer-folders.json`

Per-project flat list of folder paths. Path strings use `/` as separator.
Intermediate folders are implicit (storing `planning/sprint14` implies
`planning` exists in the rendered tree).

```jsonc
{
  "version": 1,
  "projects": {
    "acme-api": ["planning", "planning/sprint14", "bugfix"],
    "acme-app": ["watch", "watch/v2"],
    "(unfiled)": ["legacy-shelf"]                 // populated by v1→v2 migration only
  }
}
```

Atomic writes via the same flock + temp-file-rename pattern as the index.
Migration from v1 (with `index.folders[]`) is one-shot, idempotent, and runs
at every CLI entry point.
```

- [ ] **Step 11.3: Update `SPEC.md` § The TUI example tree**

Replace the example tree's flat folders with one that shows nesting:

```
session-explorer · 32 sessions across 6 projects · 15 unnamed hidden (u)               / filter

▼ acme-web (3)
    planning/
      sprint14            main         2h    ~38K  (19%)    47 msgs   audit modules…
    audits/
      q1-review           feature/…    5d    ~127K (64%)   152 msgs   grant audit
▼ acme-api (8)
    team/
      planning/
        q1                feat/x       1d    ~12K   (6%)    18 msgs   helper extraction
▶ session-explorer (4)
```

Update the surrounding prose: drop the line about "first-dash folder prefix"; replace with one about `/`-separated paths and that pre-created empty folders live in the folder-store file (referencing the new section).

- [ ] **Step 11.4: Update `SPEC.md` § Keybindings table**

The `n` and `m` rows now describe multi-level paths. Replace:

```markdown
| `n` | New folder (prompts for path under the current project; cursor on a folder pre-fills the prefix). Created empty; persisted in the folder store. |
| `m` | Move the selected session within its project (lists existing paths in the project; type a new path to create it). |
```

- [ ] **Step 11.5: Update `CLAUDE.md` load-bearing decisions**

Replace the existing decision `- **First-dash splits folder from name.**` with:

```markdown
- **Slash splits folder path from display name.** `team/planning/sprint14` → folder path `team/planning`, display `sprint14`. Multiple `/` create nested folders. Dashes are literal characters with no special meaning. Empty segments are dropped.
- **Folder structure lives in `~/.claude/session-explorer-folders.json`, scoped per-project.** Sessions named with `/` auto-add their path to the store on indexing. Pre-created empty folders live there too. The session index file no longer carries a `folders[]` field; a one-shot v1→v2 migration moves any legacy entries under a synthetic `(unfiled)` project.
```

- [ ] **Step 11.6: Update `README.md`**

Replace the "How sessions are organized" example table with the `/`-based examples used in `SPEC.md` (Task 12.1).

- [ ] **Step 11.7: Verify and commit**

Run: `PYTHONPATH=bin python3 -m pytest -q`
Expected: all tests pass (docs don't affect tests, but it's a cheap final check).

```bash
git add SPEC.md CLAUDE.md README.md
git commit -m "docs: update spec, README, CLAUDE.md for /-separated multi-level folders"
```

---

## Self-review checklist (do this before claiming done)

Verify each spec section maps to at least one task:

- §1 Name parsing → Task 1 (`split_path`).
- §2 Folder store file → Tasks 2 & 3.
- §3 Tree building → Task 6 (`build_nested_tree`), Tasks 7 & 8 (consumers).
- §4 Auto-create on indexing → Task 5 (`record_session`).
- §5 UX — `n` → Task 9.
- §6 UX — `m` → Task 8.
- §7 `--gc` / `--refresh` / `--backfill` → Task 5 covers refresh+backfill via `record_session`. `--gc` empty-folder pruning is **deliberately out of scope** for this plan; it will be implemented when `--gc` itself is added (M3 milestone in `SPEC.md`). Note that here so a reader doesn't get confused.
- §8 Schema migration → Task 4 (function), Task 7 (auto-run at entry).
- §9 Module layout → Tasks 2, 9, 10.
- §10 Testing → embedded throughout each task.
- §11 Scope guard — respected (no backward-compatible dash reading, no folder rename/delete, no drag-and-drop, no cross-project moves).
