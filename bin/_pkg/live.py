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
    # No pid -> TTL only.
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
