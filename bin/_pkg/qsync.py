"""The `sync` acquire strategy: rsync a holder's worktree OVER the shared root.

`--delete` against root is the most dangerous primitive in the design, so the
filters are exact. `exclude` (worktree junk not copied in) and `protect`
(root-only files preserved untouched) share ONE mechanism: anchored rsync
exclude filters. An exclude both removes a path from the transfer AND (with
--delete) from deletion, so root's version wins on both axes -- which is what
`protect` needs. rsync's own `P`/`protect` rule only blocks deletion, so a
same-named worktree path would still overwrite root; we never use it.

Never pass --delete-excluded: excluded/protected paths must always survive.
`/.git` (no trailing slash) matches both the worktree's `.git` *file* (a gitdir
pointer) and root's `.git` *directory*, so the worktree pointer never corrupts
root's repo.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from typing import List

# Conservative auto-protect default (spec §2): applied with no prompt.
# `/.claude/worktrees` is explorer-owned and gitignored — it holds the repo's
# SIBLING worktrees (the sync source is itself one). Without this, the holder's
# worktree (which lacks the gitignored dir) would rsync `--delete` it out of
# root, wiping every other worktree; and because it's untracked the gate would
# instead refuse, inviting the user to `allow_delete` it — the same disaster.
# Protect it unconditionally so it is never deleted and never needs classifying.
DEFAULT_PROTECT = ["/.git", "/.env", "/.env.*", "/.claude/worktrees"]


def _anchor(path: str) -> str:
    """Normalize a filter path to the spec's anchored form: leading "/" (anchor
    at the transfer root), no trailing slash (so `/.git` matches both root's
    `.git` directory and a worktree's `.git` file). "node_modules" -> "/node_modules"."""
    p = "/" + path.lstrip("/")
    return p.rstrip("/") if len(p) > 1 else p


def build_filters(exclude: List[str], protect: List[str]) -> List[str]:
    """Anchored `--filter=exclude <path>` args for the union of exclude+protect,
    de-duplicated AFTER normalization (so `.git` and `/.git/` collapse to one)
    while preserving first-seen order."""
    args: List[str] = []
    seen = set()
    for path in list(exclude) + list(protect):
        if not path:
            continue
        anchored = _anchor(path)
        if anchored not in seen:
            seen.add(anchored)
            args.append(f"--filter=exclude {anchored}")
    return args


def rsync_command(src: str, dst: str, *, exclude: List[str], protect: List[str],
                  dry_run: bool) -> List[str]:
    """Build the exact rsync argv. Trailing slashes are normalized so rsync
    copies *contents* (src/ -> dst/), not a nested src directory."""
    cmd = ["rsync", "-a", "--delete"]
    if dry_run:
        cmd += ["-n", "-i"]
    cmd += build_filters(exclude, protect)
    cmd += [src.rstrip("/") + "/", dst.rstrip("/") + "/"]
    return cmd


def parse_deletions(itemized_output: str) -> List[str]:
    """Pull the paths from rsync's `-n -i` output that would be DELETED.
    rsync marks deletions with a leading `*deleting` token."""
    deletions: List[str] = []
    for line in itemized_output.splitlines():
        if line.startswith("*deleting"):
            # "*deleting   path/to/file"  ->  "path/to/file"
            parts = line.split(None, 1)
            if len(parts) == 2:
                deletions.append(parts[1].strip())
    return deletions


class SyncDryRunError(Exception):
    """The dry-run that gates the destructive --delete could not be completed.
    Raised so queue-run FAILS CLOSED — never assume 'no deletions' on error."""


def dry_run_deletions(src: str, dst: str, *, exclude: List[str],
                      protect: List[str]) -> List[str]:
    """Run the sync as a dry-run and return paths (relative to dst) it would
    delete. FAILS CLOSED: a non-zero rsync, a launch error, or a timeout raises
    SyncDryRunError rather than returning [], so the caller refuses instead of
    silently bypassing the destructive-delete classification gate."""
    cmd = rsync_command(src, dst, exclude=exclude, protect=protect, dry_run=True)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        raise SyncDryRunError("rsync dry-run timed out") from e
    except OSError as e:
        raise SyncDryRunError(f"rsync dry-run could not run: {e}") from e
    if out.returncode != 0:
        raise SyncDryRunError(
            out.stderr.strip() or f"rsync dry-run exited {out.returncode}")
    return parse_deletions(out.stdout)


def _is_tracked(root: str, rel_path: str) -> bool:
    """True iff `rel_path` is a tracked file on root's current branch."""
    try:
        r = subprocess.run(
            ["git", "-C", root, "ls-files", "--error-unmatch", rel_path],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def classify_candidates(root: str, would_delete: List[str]) -> List[str]:
    """Of the would-delete paths, the untracked/gitignored ones that need a
    protect-vs-allow-delete decision. Tracked files are omitted: deleting them
    is a legitimate branch difference the reset must apply (spec §2)."""
    return [p for p in would_delete if not _is_tracked(root, p)]


def _matches_anchored(rel_path: str, anchored: List[str]) -> bool:
    """Match `rel_path` (e.g. "build/out") against anchored patterns
    ("/build", "/.env.*"): the leading "/" anchors at root; a pattern matches
    the path itself or any descendant, with fnmatch globbing."""
    norm = "/" + rel_path.lstrip("/")
    for pat in anchored:
        p = pat if pat.startswith("/") else "/" + pat
        if fnmatch.fnmatch(norm, p) or fnmatch.fnmatch(norm, p.rstrip("/") + "/*"):
            return True
    return False


def unclassified(root: str, would_delete: List[str], *, protect: List[str],
                 allow_delete: List[str]) -> List[str]:
    """Untracked/gitignored would-delete paths NOT yet resolved by the
    auto-protect default, an explicit `protect`, or an explicit `allow_delete`.
    A non-empty result means `queue-run` must refuse until the user classifies."""
    resolved = list(DEFAULT_PROTECT) + list(protect) + list(allow_delete)
    out: List[str] = []
    for rel in classify_candidates(root, would_delete):
        if not _matches_anchored(rel, resolved):
            out.append(rel)
    return out


def _marker_path(qdir: str) -> str:
    return os.path.join(qdir, "sandbox.marker")


def in_sandbox(qdir: str) -> bool:
    """True once the protected baseline has been settled for this resource."""
    return os.path.exists(_marker_path(qdir))


def mark_sandbox(qdir: str) -> None:
    """Record that the baseline is settled; later acquires reset freely."""
    os.makedirs(qdir, exist_ok=True)
    with open(_marker_path(qdir), "a", encoding="utf-8"):
        pass
