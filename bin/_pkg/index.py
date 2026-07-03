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


_STANDARD_WINDOW = 200_000   # default window when the model isn't known to be larger
_LARGE_WINDOW = 1_000_000    # the 1M-context window (GA since 2026-03-13)

# Context window per model id, matched by prefix so dated ids
# (claude-haiku-4-5-20251001) and future point releases are covered. Opus 4.6+
# and Sonnet 4.6 run at the 1M window in Claude Code: 1M context went GA (no beta
# header) on 2026-03-13, and on Max/Team/Enterprise + API plans these models use
# it automatically. The model id is therefore the denominator signal — the `[1m]`
# alias suffix is stripped before the request, so it never reaches the JSONL.
# Models not listed here (Haiku, pre-4.6 Opus, anything unknown) default to 200K.
MODEL_WINDOWS: Dict[str, int] = {
    "claude-opus-4-6": _LARGE_WINDOW,
    "claude-opus-4-7": _LARGE_WINDOW,
    "claude-opus-4-8": _LARGE_WINDOW,
    "claude-sonnet-4-6": _LARGE_WINDOW,
}


def _context_window(model: "str | None", tokens: int) -> int:
    """Denominator for the context-usage %.

    Look up the model's window by id prefix (default 200K). This fixes the
    denominator from the first turn for known-1M models, so the % no longer
    jumps as usage crosses 200K. If a session has somehow used more tokens than
    its mapped window, it must be on a larger one — promote to 1M. That overflow
    backstop covers unknown/older models and any window we haven't mapped.
    """
    base = _STANDARD_WINDOW
    if model:
        for prefix, window in MODEL_WINDOWS.items():
            if model.startswith(prefix):
                base = window
                break
    if tokens > base:
        return _LARGE_WINDOW
    return base

_WORKTREE_MARKER = "/.claude/worktrees/"


def project_root(cwd: str) -> str:
    """The repo root for a session's cwd — the stable grouping identity.

    Git worktrees created by Claude Code live at
    `<project_root>/.claude/worktrees/<name>`. Stripping the worktree suffix
    collapses a repo's worktrees back under the parent so they don't each
    become a top-level entry. Two different repos that happen to share a
    basename still differ here (their full paths differ), which is what lets
    the tree tell them apart — see `tree_model.disambiguate`.
    """
    if _WORKTREE_MARKER in cwd:
        cwd = cwd.split(_WORKTREE_MARKER, 1)[0]
    return cwd.rstrip("/") or cwd


def _project_label(cwd: str) -> str:
    """Default display label for a session's cwd: its repo's basename.

    This is only the *default* — the TUI disambiguates same-named repos at
    render time using the repo root (`project_root`). Worktrees collapse to the
    parent repo's basename.
    """
    root = project_root(cwd)
    return os.path.basename(root) or root


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
        # Name selection. The transcript's LAST custom-title normally wins, BUT a
        # live Claude session re-emits its in-memory title every turn, so after an
        # explorer rename Claude's next re-emit puts the OLD title back as the last
        # line. `set_name` records those superseded titles as "shadows"; we ignore
        # a last-title that's shadowed and keep the user's chosen name instead. A
        # genuinely new title (e.g. /rename inside the resumed session) isn't
        # shadowed, so it's still adopted. Fall back to the last-known name when the
        # transcript yields none (a just-created `claude -n` session has no
        # transcript until its first turn; append-only means absent != removed).
        jsonl_title = _jsonl.session_name(transcript_path)
        shadows = set(existing.get("name_shadows") or [])
        if jsonl_title and jsonl_title not in shadows:
            name_cached = jsonl_title
        else:
            name_cached = existing.get("name_cached")
        new_entry = {
            **existing,  # preserve notes, name_shadows, and other user-edited fields
            "name_cached": name_cached,
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
        # A transcript appearing means the launch succeeded; drop any error stub
        # carried forward from `existing` (set_launch_error stamps it on failure).
        new_entry.pop("last_launch_error", None)
        data["sessions"][session_id] = new_entry
        return data
    result = mutate(index_path, mutator)

    entry = result["sessions"][session_id]
    name = entry.get("name_cached") or ""
    if "/" in name:
        segments, _ = split_path(name)
        if segments:
            fs_path = folder_store_path or _fs.default_path_for(index_path)
            _fs.add(fs_path, project_root(cwd), "/".join(segments))
    return result


def seed_new_session(index_path: str, session_id: str, name: str,
                     cwd: str) -> dict:
    """Record a just-created session's user-chosen name before its transcript
    exists. `claude -n <name>` writes the matching `custom-title` to the
    transcript on its first turn, so this only bridges the gap until then; once
    the transcript appears, record_session reads the (identical) title, and
    while it's still absent record_session preserves this seeded name. Merges
    onto any existing row (e.g. one the SessionStart hook already created)."""
    def mutator(data: dict) -> dict:
        existing = data["sessions"].get(session_id, {})
        now = datetime.now(timezone.utc).isoformat()
        data["sessions"][session_id] = {
            **existing,
            "name_cached": name,
            "project_path": cwd,
            "project_label": _project_label(cwd),
            "created_at": existing.get("created_at", now),
            "last_active_at": now,
        }
        return data
    return mutate(index_path, mutator)


def set_launch_error(index_path: str, session_id: str, error: str) -> dict:
    """Record why a session's launch failed (e.g. `claude -w` could not create
    its worktree). Shown in the preview so an unopenable stub explains itself;
    cleared by record_session once a transcript appears (a successful start)."""
    def mutator(data: dict) -> dict:
        row = data["sessions"].get(session_id)
        if row is not None:
            row["last_launch_error"] = error
        return data
    return mutate(index_path, mutator)


def set_name(index_path: str, session_id: str, new_name: str,
             transcript_path: "str | None" = None) -> dict:
    """Record an explorer-driven rename as authoritative.

    Sets `name_cached` to `new_name` and adds every OTHER custom-title currently
    in the transcript to the entry's `name_shadows`. A live Claude session keeps
    re-emitting its in-memory title, so without shadows its next re-emit of the
    pre-rename title would revert the name on the next `record_session` (the
    "rename reverts after a while" bug). Call AFTER appending the new
    custom-title — `new_name` is excluded from the shadow set, so its presence in
    the transcript is harmless. Shadowing every prior title (not just the
    immediate predecessor) also covers chained renames and re-emits of any
    earlier title.
    """
    titles = set(_jsonl.all_custom_titles(transcript_path)) if transcript_path else set()

    def mutator(data: dict) -> dict:
        entry = data["sessions"].setdefault(session_id, {})
        shadows = set(entry.get("name_shadows") or []) | titles
        # Shadow the name being replaced even when the transcript yields no
        # titles — the hook records transcript_path at SessionStart, but claude
        # only writes the file on the first message, so a just-created session
        # has a dangling path and `titles` is empty. Without this, claude's
        # first write (re-emitting its `-n` title) would revert the rename.
        prev = entry.get("name_cached")
        if prev:
            shadows.add(prev)
        shadows.discard(new_name)
        if shadows:
            entry["name_shadows"] = sorted(shadows)
        entry["name_cached"] = new_name
        return data
    return mutate(index_path, mutator)


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


def reconcile_relocated(index_path: str, projects_root: "str | None" = None,
                        live_ids: "set | None" = None) -> int:
    """Heal tracked rows whose transcript went missing because Claude Code
    RELOCATED it (worktree removed → transcript moved to the parent repo's
    project dir; see jsonl.relocated_cwd). Without this, such a row's
    transcript_path/project_path dangle and the session drops out of the tree
    even though its data is intact on disk.

    For each tracked session whose `transcript_path` no longer exists, search for
    `<sid>.jsonl` elsewhere under `projects_root`; if a non-empty one is found,
    re-record the session at its true (relocated) cwd. Unlike reindex this NEVER
    prunes and merges onto the existing row via record_session, so notes /
    name_shadows / created_at survive.

    `live_ids` are skipped (a live transcript is mid-write; a transient stat miss
    must not re-record it). Rows with no transcript anywhere on disk (genuine
    just-created stubs, or truly deleted sessions) are left untouched.

    Returns the number of rows healed. Cheap in steady state: the disk walk runs
    only when at least one tracked row is actually dangling.
    """
    projects_root = projects_root or os.path.expanduser("~/.claude/projects")
    live = set(live_ids or ())
    sessions = load(index_path).get("sessions", {})
    dangling = [
        sid for sid, s in sessions.items()
        if sid not in live
        and not (s.get("transcript_path") and os.path.exists(s["transcript_path"]))
    ]
    if not dangling or not os.path.isdir(projects_root):
        return 0
    wanted = {sid + ".jsonl": sid for sid in dangling}
    # One pass over the project dirs; per dangling sid keep the largest non-empty
    # match (a relocated transcript can leave a 0-byte husk behind).
    best = {}  # sid -> (transcript_path, size)
    for project_dir in os.listdir(projects_root):
        full = os.path.join(projects_root, project_dir)
        if not os.path.isdir(full):
            continue
        try:
            names = os.listdir(full)
        except OSError:
            continue
        for fname in names:
            sid = wanted.get(fname)
            if not sid:
                continue
            cand = os.path.join(full, fname)
            try:
                size = os.path.getsize(cand)
            except OSError:
                continue
            if size > 0 and size > best.get(sid, (None, -1))[1]:
                best[sid] = (cand, size)
    healed = 0
    for sid, (transcript, _size) in best.items():
        cwd = _jsonl.effective_cwd(transcript)
        if not cwd or not os.path.isdir(cwd):
            continue  # can't resolve a real cwd — never guess a location
        record_session(index_path, sid, transcript, cwd, skip_git=True)
        healed += 1
    return healed


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
    # Heal relocated transcripts FIRST: this re-points dangling rows at their
    # moved transcript, so refresh_all's prune below keeps them (their
    # transcript_path now exists) instead of dropping the row + its notes.
    reconcile_relocated(index_path, projects_root=projects_root)
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


def migrate_folder_store_keys(index_path: str, folder_store_path: str) -> None:
    """Re-key the folder store from repo *basename* to repo *root*.

    Early folder stores keyed each project by its basename (e.g. `magento2`),
    which silently merged distinct same-named repos. Going forward the store is
    keyed by repo root path so they stay separate. This maps each legacy
    basename key to the root(s) of the sessions that carry it (copying into
    every matching root when a basename is shared); keys with no matching
    session — empty-folder-only projects, the synthetic `(unfiled)` bucket — are
    left untouched.

    NOT a one-shot: a stale pre-root-keying hook (older installed plugin copy)
    can re-add basename keys *after* the store was stamped v2, so a v2 store is
    re-checked and healed whenever a bare key matches a current session root.
    The file is rewritten only when something actually changes. Idempotent.
    """
    # Nothing to re-key (and don't materialise an empty store on no-op CLI runs).
    if not os.path.exists(folder_store_path):
        return
    from . import folder_store as _fs
    store = _fs.load(folder_store_path)
    projects = store.get("projects") or {}
    # Fast path: already v2 and every key is a path (or a synthetic bucket that
    # can't be a repo basename) — skip the index read entirely.
    if store.get("version", 1) >= 2 and all("/" in k for k in projects):
        return

    base_to_roots: dict = {}
    for s in load(index_path).get("sessions", {}).values():
        cwd = s.get("project_path")
        if not cwd:
            continue
        root = project_root(cwd)
        base_to_roots.setdefault(os.path.basename(root) or root, set()).add(root)

    def remap(data: dict) -> dict:
        old = data.get("projects") or {}
        new: dict = {}
        for key, paths in old.items():
            targets = base_to_roots.get(key) or {key}
            for t in sorted(targets):
                dest = new.setdefault(t, [])
                for p in paths:
                    if p not in dest:
                        dest.append(p)
        data["projects"] = new
        data["version"] = 2
        return data

    # v2 store with bare keys: heal only if a bare key actually resolves to a
    # session root — otherwise (e.g. only "(unfiled)") leave the file untouched.
    if store.get("version", 1) >= 2 and not any(
            "/" not in k and k in base_to_roots for k in projects):
        return
    _fs.mutate(folder_store_path, remap)
