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

_WORKTREE_MARKER = "/.claude/worktrees/"


def _project_label(cwd: str) -> str:
    """Group label for a session's cwd.

    Git worktrees created by Claude Code live at
    `<project_root>/.claude/worktrees/<name>`. Without special handling each
    worktree's leaf name (e.g. `ai-weight-adjust`) becomes its own top-level
    "project", fragmenting the tree. Collapse those back under the parent
    project root so all of a repo's worktrees group together.
    """
    if _WORKTREE_MARKER in cwd:
        cwd = cwd.split(_WORKTREE_MARKER, 1)[0]
    return os.path.basename(cwd.rstrip("/")) or cwd


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
            **existing,  # preserve notes and other user-edited fields
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


def backfill(index_path: str, projects_root: "str | None" = None) -> int:
    """Index every JSONL under ~/.claude/projects/ that isn't already tracked.

    For each new session, recovers `cwd` from the JSONL's envelope lines via
    `jsonl.session_cwd()` (the hook payload's cwd isn't available for
    pre-install sessions). Skips sessions already in the index — existing
    entries are refreshed via `--refresh`, not here.

    Returns the count of newly-added sessions.
    """
    projects_root = projects_root or os.path.expanduser("~/.claude/projects")
    if not os.path.isdir(projects_root):
        return 0
    existing = set(load(index_path).get("sessions", {}).keys())
    added = 0
    for project_dir in sorted(os.listdir(projects_root)):
        full = os.path.join(projects_root, project_dir)
        if not os.path.isdir(full):
            continue
        for fname in sorted(os.listdir(full)):
            if not fname.endswith(".jsonl"):
                continue
            sid = fname[:-len(".jsonl")]
            if sid in existing:
                continue
            transcript_path = os.path.join(full, fname)
            cwd = _jsonl.session_cwd(transcript_path) or ""
            try:
                record_session(index_path, sid, transcript_path, cwd)
                added += 1
            except Exception:
                # Pre-install JSONLs can be malformed in edge ways; skip
                # silently rather than abort the whole scan.
                continue
    return added


def refresh_all(index_path: str) -> dict:
    """Recompute every session's cached fields; prune entries whose JSONL is gone.

    The prune phase runs inside mutate() so a concurrent hook can't lose a write
    via a load/save race. record_session uses its own mutate() per call, which
    correctly merges with any session added between iterations.
    """
    def prune(data: dict) -> dict:
        keep: "dict[str, dict]" = {}
        for sid, entry in data.get("sessions", {}).items():
            transcript = entry.get("transcript_path")
            if transcript and os.path.exists(transcript):
                keep[sid] = entry
        data["sessions"] = keep
        return data

    pruned = mutate(index_path, prune)
    for sid, entry in pruned["sessions"].items():
        record_session(
            index_path,
            session_id=sid,
            transcript_path=entry["transcript_path"],
            cwd=entry.get("project_path", ""),
        )
    return load(index_path)


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
