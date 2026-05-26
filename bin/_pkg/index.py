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
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Callable, Dict, Any

from . import jsonl as _jsonl

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


def _git_branch(cwd: str) -> "str | None":
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


_TOKEN_WINDOW = 200_000  # v1 hardcode (Sonnet 4.6 default); see SPEC open question


def record_session(index_path: str, session_id: str, transcript_path: str, cwd: str) -> dict:
    """Idempotent upsert. Preserves 'notes' and any other user-edited fields."""
    def mutator(data: dict) -> dict:
        existing = data["sessions"].get(session_id, {})
        try:
            file_bytes = os.path.getsize(transcript_path)
        except FileNotFoundError:
            file_bytes = 0
        tokens = _jsonl.tokens_estimate(transcript_path)
        new_entry = {
            **existing,  # preserve notes, etc.
            "name_cached": _jsonl.session_name(transcript_path),
            "first_prompt": _jsonl.first_user_prompt(transcript_path),
            "message_count": _jsonl.message_count(transcript_path),
            "bytes": file_bytes,
            "tokens_estimate": tokens,
            "tokens_window_pct": min(100, int(tokens * 100 / _TOKEN_WINDOW)),
            "project_path": cwd,
            "project_label": os.path.basename(cwd.rstrip("/")) or cwd,
            "branch": _git_branch(cwd),
            "last_active_at": _jsonl.last_active_at(transcript_path) or datetime.now(timezone.utc).isoformat(),
            "transcript_path": transcript_path,
        }
        if "created_at" not in new_entry:
            new_entry["created_at"] = datetime.now(timezone.utc).isoformat()
        data["sessions"][session_id] = new_entry
        return data
    return mutate(index_path, mutator)


def refresh_all(index_path: str) -> dict:
    """Recompute every session's cached fields; prune entries whose JSONL is gone."""
    data = load(index_path)
    keep: "dict[str, dict]" = {}
    for sid, entry in data.get("sessions", {}).items():
        transcript = entry.get("transcript_path")
        if transcript and os.path.exists(transcript):
            keep[sid] = entry
    data["sessions"] = keep
    save(index_path, data)
    # Now re-record each (preserves notes).
    for sid, entry in keep.items():
        record_session(
            index_path,
            session_id=sid,
            transcript_path=entry["transcript_path"],
            cwd=entry.get("project_path", ""),
        )
    return load(index_path)
