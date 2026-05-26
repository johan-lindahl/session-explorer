"""Atomic, flock'd JSON index for session-explorer.

Schema: {"version": 1, "folders": [str, ...], "sessions": {uuid: {...}}}

Concurrency: every mutate uses flock(LOCK_EX) on the target path AND writes
to a sibling *.tmp file then atomic-renames over the original. This protects
both against torn writes (rename is atomic on POSIX) and against two
session-start hooks firing simultaneously.
"""

import fcntl
import json
import os
import tempfile
from typing import Callable, Dict, Any

_DEFAULT: Dict[str, Any] = {"version": 1, "folders": [], "sessions": {}}


def load(path: str) -> dict:
    if not os.path.exists(path):
        return _DEFAULT.copy() | {"folders": [], "sessions": {}}
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
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-", suffix=".tmp", dir=parent)
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
    """Read-modify-write the index under an exclusive flock on a sidecar lock file.

    A separate lock file (path + '.lock') is used because the index file itself
    is replaced atomically — flock on a file that gets renamed-over is fragile.
    """
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
