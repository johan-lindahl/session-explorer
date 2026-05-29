"""Atomic, flock'd JSON index for session-explorer.

Schema (v2): {"version": 2, "sessions": {uuid: {...}}}

Folder structure lives in a sibling file (session-explorer-folders.json,
managed by `folder_store`), not in this index. A one-shot, idempotent
`migrate_to_v2(...)` upgrades any pre-existing v1 file (which still has a
flat `folders[]` field) on first CLI invocation.

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

_DEFAULT: Dict[str, Any] = {"version": 2, "sessions": {}}


def load(path: str) -> dict:
    if not os.path.exists(path):
        return _DEFAULT.copy() | {"sessions": {}}
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


_STANDARD_WINDOW = 200_000   # default context window for current Claude 4.x models
_LARGE_WINDOW = 1_000_000    # the 1M-context tier (beta opt-in)

# Standard context window per model id. All current Claude 4.x models default to
# 200K, so this is intentionally minimal — extend it when a model ships a
# different *standard* window. The 1M-context tier is NOT encoded in the model
# id, so _context_window infers it from observed usage instead.
MODEL_WINDOWS: Dict[str, int] = {}


def _context_window(model: "str | None", tokens: int) -> int:
    """Denominator for the context-usage %.

    Start from the model's standard window (default 200K). If the session has
    already used more tokens than that window, it must be running on the
    1M-context tier — which the model id doesn't reveal — so promote to 1M.
    This keeps ordinary sessions at 200K while stopping large (e.g. 600K+)
    sessions from pegging uselessly at 100%.
    """
    base = MODEL_WINDOWS.get(model or "", _STANDARD_WINDOW)
    if tokens > base:
        return _LARGE_WINDOW
    return base

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
                   cwd: str, folder_store_path: "str | None" = None,
                   skip_git: bool = False) -> dict:
    """Idempotent upsert. Preserves 'notes' and any other user-edited fields.

    If the session's cached name contains `/`, the leading folder path is added
    (idempotently) to the per-project folder store. `folder_store_path` defaults
    to a sibling of `index_path`.

    `skip_git=True` avoids forking `git` to recompute the branch and instead
    reuses the branch already stored on the existing entry (None if absent). The
    branch is static for a session, so the live-metadata refresh sets this to
    avoid forking git every poll.
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
        model = _jsonl.latest_model(transcript_path)
        window = _context_window(model, tokens)
        new_entry = {
            **existing,  # preserve notes and other user-edited fields
            "name_cached": _jsonl.session_name(transcript_path),
            "first_prompt": _jsonl.first_user_prompt(transcript_path),
            "message_count": _jsonl.message_count(transcript_path),
            "bytes": file_bytes,
            "tokens_estimate": tokens,
            "model": model,
            "tokens_window_pct": min(100, int(tokens * 100 / window)),
            "project_path": cwd,
            "project_label": _project_label(cwd),
            "branch": existing.get("branch") if skip_git else _git_branch(cwd),
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


def backfill(index_path: str, projects_root: "str | None" = None,
             on_session: "Callable[[], None] | None" = None) -> int:
    """Index every JSONL under ~/.claude/projects/ that isn't already tracked.

    For each new session, recovers `cwd` from the JSONL's envelope lines via
    `jsonl.session_cwd()` (the hook payload's cwd isn't available for
    pre-install sessions). Skips sessions already in the index — existing
    entries are refreshed via `--refresh`, not here.

    `on_session`, if given, is called once per newly-added session (for progress
    reporting). Returns the count of newly-added sessions.
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
                if on_session:
                    on_session()
            except Exception:
                # Pre-install JSONLs can be malformed in edge ways; skip
                # silently rather than abort the whole scan.
                continue
    return added


def refresh_all(index_path: str,
                on_session: "Callable[[], None] | None" = None) -> dict:
    """Recompute every session's cached fields; prune entries whose JSONL is gone.

    The prune phase runs inside mutate() so a concurrent hook can't lose a write
    via a load/save race. record_session uses its own mutate() per call, which
    correctly merges with any session added between iterations. `on_session`, if
    given, is called once per recomputed (surviving) session.
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
        if on_session:
            on_session()
    return load(index_path)


def _reindex_units(index_path: str, projects_root: "str | None") -> int:
    """How many sessions a reindex will touch: surviving tracked sessions
    (refresh re-records these) plus untracked JSONLs on disk (backfill adds
    these). Used to pre-count the progress denominator."""
    data = load(index_path)
    tracked = data.get("sessions", {})
    refresh_n = sum(
        1 for e in tracked.values()
        if e.get("transcript_path") and os.path.exists(e["transcript_path"])
    )
    root = projects_root or os.path.expanduser("~/.claude/projects")
    backfill_n = 0
    if os.path.isdir(root):
        tracked_ids = set(tracked.keys())
        for project_dir in os.listdir(root):
            full = os.path.join(root, project_dir)
            if not os.path.isdir(full):
                continue
            for fname in os.listdir(full):
                if fname.endswith(".jsonl") and fname[:-len(".jsonl")] not in tracked_ids:
                    backfill_n += 1
    return refresh_n + backfill_n


def reindex(index_path: str, projects_root: "str | None" = None,
            progress: "Callable[[int, int], None] | None" = None) -> dict:
    """Recompute tracked sessions (pruning dead JSONLs), then import any
    untracked sessions under ~/.claude/projects/.

    Refresh runs first so each already-tracked session is touched once; backfill
    then adds the rest. Non-destructive: notes and custom-title names survive
    (see record_session). Returns {"added": int, "total": int}.

    `progress`, if given, is called as progress(done, total): once with
    (0, total) up front, then after each session processed. This is the
    user-facing "rescan" the TUI binds to F5; nothing imports pre-install
    sessions automatically.
    """
    units = _reindex_units(index_path, projects_root)
    done = [0]
    if progress:
        progress(0, units)

    def tick() -> None:
        done[0] += 1
        if progress:
            progress(done[0], units)

    refresh_all(index_path, on_session=tick)
    added = backfill(index_path, projects_root=projects_root, on_session=tick)
    total = len(load(index_path).get("sessions", {}))
    return {"added": added, "total": total}


def migrate_to_v2(index_path: str, folder_store_path: str) -> None:
    """One-shot migration of the index from v1 (with flat `folders[]`) to v2
    (folders moved out to a separate file under a synthetic (unfiled) project).

    Idempotent. Order: write the folder store first, then the v2 index. A crash
    between leaves the index at v1; on retry, folder_store.add is idempotent.

    Returns early when the index file doesn't exist — fresh installs shouldn't
    have an empty index/lock pair materialised by no-op CLI invocations like
    `session-explorer` with no subcommand.
    """
    if not os.path.exists(index_path):
        return
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
