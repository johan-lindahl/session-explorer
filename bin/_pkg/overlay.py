"""Overlay-and-restore helper for the 'shared installed app root' queue resource.

Wired as a root-dir resource's command hooks (acquire=command/release=command):

    command_acquire: session-explorer queue-overlay in
    command_release: session-explorer queue-overlay out

The engine runs both with cwd=root and exports SE_QUEUE_WORKTREE (overlay
source), SE_QUEUE_ROOT (overlay target) and SE_QUEUE_STATE_DIR (manifest dir).

`apply_overlay` copies the worktree's changed files into root and records a
manifest. `restore_overlay` undoes exactly those paths (git checkout for files
that existed in root, rm for ones the overlay created) and deletes the manifest.
restore runs in the engine's release `finally`, so it survives normal
completion, child failure, and handled SIGINT/SIGTERM — but NOT a SIGKILL/hard
crash, which skips the finally (the next overlay then refuses the dirty root).

Pure helpers take explicit paths so they unit-test without env. No Textual.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import List

MANIFEST_NAME = "overlay.manifest"


def _git_lines(cwd: str, *args: str) -> List[str]:
    out = subprocess.run(["git", "-C", cwd, *args],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def changed_files(worktree: str, root: str) -> List[str]:
    """Relpaths whose worktree version differs from root's checked-out commit,
    plus the worktree's untracked (non-ignored) files. Deduped, sorted."""
    paths = set()
    head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode == 0:
        base = head.stdout.strip()
        paths.update(_git_lines(worktree, "diff", "--name-only", base))
    paths.update(_git_lines(worktree, "ls-files", "--others", "--exclude-standard"))
    return sorted(paths)


def apply_overlay(worktree: str, root: str, state_dir: str) -> List[dict]:
    """Copy each changed file from worktree into root; write + return a manifest
    of {path, status} where status is 'modified' (existed in root) or 'added'."""
    manifest = []
    for rel in changed_files(worktree, root):
        src = os.path.join(worktree, rel)
        if not os.path.isfile(src):
            continue   # deleted / non-regular path: v1 copies-in only
        dst = os.path.join(root, rel)
        status = "modified" if os.path.exists(dst) else "added"
        os.makedirs(os.path.dirname(dst) or root, exist_ok=True)
        shutil.copy2(src, dst)
        manifest.append({"path": rel, "status": status})
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    return manifest


def restore_overlay(root: str, state_dir: str) -> List[str]:
    """Undo a prior apply_overlay. Best-effort: attempt every path, then return
    the list of paths that could NOT be restored (empty == full success).
    git-checkout modified paths, rm added ones. On full success the manifest is
    deleted; if anything failed the manifest is KEPT (forensics) and each failure
    is logged to stderr — the dirty root is then also caught by the next acquire's
    transition_guard. Corrupt/absolute/`..` manifest entries are ignored. A
    missing/corrupt manifest is a no-op (returns [])."""
    mpath = os.path.join(state_dir, MANIFEST_NAME)
    try:
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return []
    failed: List[str] = []
    for entry in manifest:
        rel = entry.get("path")
        if not rel or os.path.isabs(rel) or ".." in rel.split(os.sep):
            continue   # ignore corrupt / path-traversal manifest entries
        if entry.get("status") == "added":
            try:
                os.remove(os.path.join(root, rel))
                # Parent dirs apply_overlay created are not pruned (v1 limitation).
            except FileNotFoundError:
                pass   # already gone == restored
            except OSError as e:
                failed.append(rel)
                print(f"overlay: could not remove {rel}: {e}", file=sys.stderr)
        else:
            r = subprocess.run(["git", "-C", root, "checkout", "--", rel],
                               capture_output=True, text=True)
            if r.returncode != 0:
                failed.append(rel)
                print(f"overlay: checkout failed for {rel}: {r.stderr.strip()}",
                      file=sys.stderr)
    if not failed:
        try:
            os.remove(mpath)
        except OSError:
            pass
    return failed
