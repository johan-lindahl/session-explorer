"""Volatile live-session registry for session-explorer.

Schema (v1): {"version": 1, "sessions": {session_id: entry}}
  entry = {"state": "working"|"idle", "last_seen": iso8601,
           "transcript_path": str, "cwd": str, "pid": int (optional)}
  "pid" is present only when known (recorded at SessionStart).

This file is runtime-only: it is never merged into the index and never read by
retention/--gc. It reuses the index's atomic write (index.save) but keeps its
own load/mutate so a fresh file defaults to version 1 (index.load defaults to
v2). Concurrency: flock(LOCK_EX) on a sibling '.lock' file, write-temp-rename.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from typing import Callable, Optional

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
        return {**_DEFAULT, "sessions": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError):
        return {**_DEFAULT, "sessions": {}}


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
        entry["last_seen"] = ts
        sessions[session_id] = entry
        return data

    mutate(path, m)
