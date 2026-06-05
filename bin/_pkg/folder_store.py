"""Atomic, flock'd per-project folder path store for session-explorer.

Schema: {"version": 2, "projects": {project_root: [folder_path, ...]}}

`project_root` is the repo's root path (cwd with any `/.claude/worktrees/<name>`
suffix stripped) — the stable identity that keeps two same-named repos apart.
v1 stores keyed by repo basename are re-keyed once by
`index.migrate_folder_store_keys`.

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
    """Read the store. Returns a fresh default dict if the file is missing,
    so callers can always mutate the result without aliasing _DEFAULT."""
    if not os.path.exists(path):
        return _DEFAULT.copy() | {"projects": {}}
    with open(path, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            return json.load(f)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def save(path: str, data: dict) -> None:
    """Atomic write: temp file in the same directory + rename."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-folders-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)  # atomic on POSIX
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


def remove(path: str, project: str, folder: str) -> None:
    """Remove `folder` from `project`'s path list. No-op if missing."""
    def mutator(data: dict) -> dict:
        projects = data.get("projects", {})
        if project in projects:
            projects[project] = [f for f in projects[project] if f != folder]
        return data
    mutate(path, mutator)


def remove_subtree(path: str, project: str, folder: str) -> None:
    """Remove `folder` and every descendant path (anything under `folder/`)
    from `project`'s list. No-op for missing entries. Used to delete an empty
    folder along with its empty subfolders."""
    prefix = folder + "/"

    def mutator(data: dict) -> dict:
        projects = data.get("projects", {})
        if project in projects:
            projects[project] = [
                f for f in projects[project]
                if f != folder and not f.startswith(prefix)
            ]
        return data
    mutate(path, mutator)


def rename_subtree(path: str, project: str, old_path: str, new_path: str) -> None:
    """Re-prefix `old_path` and every descendant (`old_path/...`) to `new_path`
    in `project`'s list. Used when a folder is renamed or re-parented; the whole
    subtree moves together. Entries that merely share a string prefix but are a
    different segment (e.g. `old_path` + "-extra") are left untouched. Resulting
    duplicates (renaming into an existing target) collapse to one entry. No-op
    for a missing project or when `old_path == new_path`."""
    if old_path == new_path:
        return
    prefix = old_path + "/"

    def mutator(data: dict) -> dict:
        projects = data.get("projects", {})
        if project not in projects:
            return data
        out: List[str] = []
        seen = set()
        for f in projects[project]:
            if f == old_path:
                f = new_path
            elif f.startswith(prefix):
                f = new_path + f[len(old_path):]
            if f not in seen:
                seen.add(f)
                out.append(f)
        projects[project] = out
        return data
    mutate(path, mutator)


def list_paths(path: str, project: str) -> List[str]:
    """Return a sorted copy of `project`'s stored folder paths (may be empty)."""
    data = load(path)
    paths = data.get("projects", {}).get(project, [])
    return sorted(paths)
