"""Git-worktree directory operations, shared by the TUI and retention GC.

Removal is deliberately non-destructive: `git worktree remove` runs WITHOUT
--force, so git refuses any tree with uncommitted or untracked changes, and the
branch `worktree-<name>` is always kept. A removed worktree is therefore just a
"dead" worktree that `tui._recreate_worktree` rebuilds on resume.

No Textual import here on purpose — `gc.py` and `cli.py` import this module.
"""

from __future__ import annotations

import os
import subprocess

MARKER = "/.claude/worktrees/"


def root_of(project_path: "str | None") -> "str | None":
    """The parent repo root for a worktree path, or None if it isn't one."""
    if not project_path or MARKER not in project_path:
        return None
    return project_path.split(MARKER, 1)[0]


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    """Run git at the repo *root* (not the worktree path)."""
    return subprocess.run(["git", "-C", root, *args],
                          capture_output=True, text=True)


def removable(project_path: "str | None") -> bool:
    """True iff the directory exists and is clean (no modified or untracked
    files) — i.e. `git worktree remove` would succeed without --force."""
    if not project_path or not os.path.isdir(project_path):
        return False
    # Bounded: this runs on the UI thread (poll tick) — a slow/network FS must
    # not freeze it. On timeout we treat the tree as not-removable (safe).
    try:
        r = subprocess.run(["git", "-C", project_path, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.strip() == ""


def remove(project_path: "str | None") -> str:
    """Remove a worktree's working directory, keeping its branch.

    Returns "removed" on success, "dirty" if git refused (uncommitted/untracked
    work — never forced), or "failed" for anything else. Callers must ensure the
    session is not live."""
    root = root_of(project_path)
    if not root or not os.path.isdir(root) or not os.path.isdir(project_path):
        return "failed"
    rc = _git(root, "worktree", "remove", project_path).returncode
    if rc == 0:
        _git(root, "worktree", "prune")
        return "removed"
    # git refused. Dirty if the dir survives and is no longer clean; otherwise
    # "failed" (e.g. a locked worktree) — callers treat both as "leave it alone".
    if os.path.isdir(project_path) and not removable(project_path):
        return "dirty"
    return "failed"


def size(project_path: "str | None") -> str:
    """Human-readable on-disk size (e.g. "12M"), or "" if unavailable."""
    if not project_path or not os.path.isdir(project_path):
        return ""
    try:
        # Bounded so a pathologically large tree can't freeze the caller (the
        # preview pane computes this on the UI thread). On timeout: no size.
        out = subprocess.run(["du", "-sh", project_path],
                             capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout:
            return out.stdout.split("\t", 1)[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""
