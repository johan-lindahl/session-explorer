"""Retention GC for session-explorer.

Native Claude cleanup is neutralised (cleanupPeriodDays=36500); this does the
deletion instead. collect_garbage only touches UNNAMED sessions. Named
sessions are deleted only by an explicit folder expiry rule the user set
(collect_expired, see expiry.py / SPEC "Folder expiry rules").

Deletion criteria (SPEC §Disabling native auto-cleanup):
  name_cached IS NULL
  AND last_active_at older than <retention-days>  (default 30)
  AND no active flock on the JSONL
  AND JSONL mtime older than 60 seconds

The whole sweep runs inside one index.mutate() so a concurrent SessionStart
hook write can't be lost; the live-check + os.unlink happen inside the mutator,
right before the row is dropped, to minimise TOCTOU.
"""

from __future__ import annotations

import fcntl
import os
from datetime import datetime, timezone

from . import index as _index
from . import worktree as _worktree

_LIVE_MTIME_SECONDS = 60
_WORKTREE_IDLE_DAYS = 14   # idle threshold for --gc worktree pruning


def _parse_iso(value: "str | None") -> "datetime | None":
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_live(transcript_path: str, now: datetime) -> bool:
    """A session looks live if its JSONL was modified within the last 60s or
    something currently holds a flock on it."""
    try:
        mtime = os.path.getmtime(transcript_path)
    except OSError:
        return False
    if now.timestamp() - mtime < _LIVE_MTIME_SECONDS:
        return True
    try:
        with open(transcript_path, "r") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True
    except OSError:
        return False


def _age_dt(entry: dict, now: datetime) -> "datetime | None":
    """When the session was last active: the index field if parseable, else the
    JSONL's mtime. None when neither is available."""
    dt = _parse_iso(entry.get("last_active_at"))
    if dt is not None:
        return dt
    transcript = entry.get("transcript_path")
    if transcript:
        try:
            return datetime.fromtimestamp(os.path.getmtime(transcript), tz=timezone.utc)
        except OSError:
            return None
    return None


def collect_garbage(index_path: str, *, retention_days: "int | None" = None,
                    dry_run: bool = False,
                    now: "datetime | None" = None) -> dict:
    """Delete unnamed sessions older than `retention_days` (resolved from
    ui_state — configurable in the Settings screen — when not passed).

    Returns {"removed": [session_id, ...], "skipped_live": int,
             "removed_worktrees": [path, ...], "dry_run": bool}. Deleting a
    worktree session also purges its worktree dir + branch (safe, merged-only)
    and drops its stored summary; both run after the index mutate, outside the
    lock. `now` is injectable for deterministic tests; defaults to UTC now.
    """
    if retention_days is None:
        from . import ui_state as _ui
        retention_days = _ui.get_retention_days(_ui.default_path_for(index_path))
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - retention_days * 86400

    removed: list[str] = []
    removed_worktrees: list[str] = []
    wt_paths: dict[str, str] = {}   # sid -> worktree path (collected in the mutator)
    skipped_live = 0

    if dry_run:
        data = _index.load(index_path)
        for sid, entry in data.get("sessions", {}).items():
            if entry.get("name_cached"):
                continue
            transcript = entry.get("transcript_path")
            if transcript and os.path.exists(transcript) and _is_live(transcript, now):
                skipped_live += 1
                continue
            age = _age_dt(entry, now)
            if age is not None and age.timestamp() >= cutoff:
                continue
            removed.append(sid)
        return {"removed": removed, "skipped_live": skipped_live,
                "removed_worktrees": [], "dry_run": True}

    def mutator(data: dict) -> dict:
        nonlocal skipped_live
        keep: dict[str, dict] = {}
        for sid, entry in data.get("sessions", {}).items():
            if entry.get("name_cached"):
                keep[sid] = entry
                continue
            transcript = entry.get("transcript_path")
            exists = bool(transcript) and os.path.exists(transcript)
            if exists and _is_live(transcript, now):
                skipped_live += 1
                keep[sid] = entry
                continue
            age = _age_dt(entry, now)
            if age is not None and age.timestamp() >= cutoff:
                keep[sid] = entry
                continue
            if exists:
                try:
                    os.unlink(transcript)
                except OSError:
                    pass
            path = entry.get("project_path") or ""
            if _worktree.MARKER in path:
                wt_paths[sid] = path
            removed.append(sid)
        data["sessions"] = keep
        return data

    _index.mutate(index_path, mutator)

    from . import summary as _summary
    sp = _summary.default_path_for(index_path)
    for sid in removed:
        _summary.remove(sp, sid)
        wt = wt_paths.get(sid)
        if wt and _worktree.purge(wt) in ("removed", "removed_branch_kept"):
            removed_worktrees.append(wt)

    return {"removed": removed, "skipped_live": skipped_live,
            "removed_worktrees": removed_worktrees, "dry_run": False}


def collect_worktrees(index_path: str, *, idle_days: int = _WORKTREE_IDLE_DAYS,
                      dry_run: bool = False,
                      now: "datetime | None" = None) -> dict:
    """Reclaim idle, clean worktree directories (keeping branch + transcript;
    resume rebuilds them). "Idle" = the worktree dir's mtime is older than
    `idle_days` (an approximate signal — git activity touches it). Skips live
    sessions and fresh dirs; git refuses dirty trees.

    Returns {"removed_worktrees": [...], "skipped_dirty": int,
             "skipped_live": int, "dry_run": bool}. Does NOT mutate the index —
    the transcript and the row stay; only the working directory is freed.
    `skipped_dirty` also absorbs the rare non-dirty `remove()` refusal (e.g. a
    locked worktree) — both mean "left on disk", which is all the caller acts on."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - idle_days * 86400

    removed: list[str] = []
    skipped_dirty = 0
    skipped_live = 0

    data = _index.load(index_path)
    for entry in data.get("sessions", {}).values():
        path = entry.get("project_path") or ""
        if _worktree.MARKER not in path or not os.path.isdir(path):
            continue
        transcript = entry.get("transcript_path")
        if transcript and os.path.exists(transcript) and _is_live(transcript, now):
            skipped_live += 1
            continue
        try:
            if os.path.getmtime(path) >= cutoff:
                continue   # too fresh
        except OSError:
            continue
        if not _worktree.removable(path):
            skipped_dirty += 1
            continue
        if dry_run:
            removed.append(path)
            continue
        if _worktree.remove(path) == "removed":
            removed.append(path)
        else:
            skipped_dirty += 1
    return {"removed_worktrees": removed, "skipped_dirty": skipped_dirty,
            "skipped_live": skipped_live, "dry_run": dry_run}


def collect_expired(index_path: str, *, now: "datetime | None" = None,
                    dry_run: bool = False, live_ids: "set | None" = None,
                    folder_store_data: "dict | None" = None,
                    project: "str | None" = None) -> dict:
    """Apply folder expiry rules (SPEC: "Folder expiry rules (`t`)").

    Deletes every named session idle past its folder rule, via the same
    primitive as the TUI's `d` (delete.delete_session: transcript + index row +
    summary + worktree.purge). Never touches: live sessions (`live_ids` from the
    live registry, plus the flock/60s-mtime test) or sessions whose worktree has
    uncommitted/untracked changes. `folder_store_data` lets the TUI evaluate a
    rule before saving it (dry_run=True); `project` restricts to one root.

    Returns {"removed": [{"sid","name","source","days"}], "skipped_live": [sid],
             "skipped_dirty": [sid], "dry_run": bool}.
    """
    from . import expiry as _expiry
    from . import folder_store as _fs
    now = now or datetime.now(timezone.utc)
    live_ids = live_ids or set()
    if folder_store_data is None:
        folder_store_data = _fs.load(_fs.default_path_for(index_path))
    result = {"removed": [], "skipped_live": [], "skipped_dirty": [], "dry_run": dry_run}
    if not folder_store_data.get("rules"):
        return result
    data = _index.load(index_path)
    for sid, entry, st in _expiry.expired_sessions(data, folder_store_data, now, project):
        transcript = entry.get("transcript_path")
        if sid in live_ids or (transcript and os.path.exists(transcript)
                               and _is_live(transcript, now)):
            result["skipped_live"].append(sid)
            continue
        wt = entry.get("project_path") or ""
        if _worktree.MARKER in wt and os.path.isdir(wt) and not _worktree.removable(wt):
            result["skipped_dirty"].append(sid)
            continue
        item = {"sid": sid, "name": entry.get("name_cached"),
                "source": st["source"], "days": st["days"]}
        if not dry_run:
            from .delete import delete_session
            delete_session(index_path, sid)
        result["removed"].append(item)
    return result
