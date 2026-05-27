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
