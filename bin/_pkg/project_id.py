"""Canonical project identity for the shared-resource queue.

The existing `index.project_root()` merely string-strips `/.claude/worktrees/`,
so it (a) treats a plain `git worktree add ../repo-feat` as its own project,
(b) cannot resolve a subdirectory cwd to the repo top-level, and (c) does not
canonicalize symlinks. The queue keys must be identical across *every* worktree
of a repo, so we key by the repo's git common dir instead.

No Textual import: imported by CLI, queue store, and the exclusive-or policy.
"""

from __future__ import annotations

import hashlib
import os
import subprocess


def _git(cwd: str, *args: str, timeout: float = 2.0) -> "str | None":
    """Run `git -C cwd ...`; return stripped stdout on success, else None."""
    try:
        out = subprocess.run(["git", "-C", cwd, *args],
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def common_dir(cwd: str) -> "str | None":
    """The repo's canonical git common dir, shared by all its worktrees.

    `git rev-parse --git-common-dir` yields the *main* repo's `.git`, identical
    for the main tree and every worktree. It may be relative to `cwd`; we make
    it absolute and `realpath` it so symlinked checkouts collapse together.
    """
    raw = _git(cwd, "rev-parse", "--git-common-dir")
    if not raw:
        return None
    if not os.path.isabs(raw):
        raw = os.path.join(cwd, raw)
    return os.path.realpath(raw)


def project_id(cwd: str) -> "str | None":
    """Stable 16-hex identity for the repo containing `cwd` (None if not a repo)."""
    cd = common_dir(cwd)
    if cd is None:
        return None
    return hashlib.sha256(cd.encode("utf-8")).hexdigest()[:16]


def main_root(cwd: str) -> "str | None":
    """The repo's MAIN working tree path — where a bind-mounted stack lives.

    The first `worktree <path>` line of `git worktree list --porcelain` is
    always the main working tree, regardless of which worktree `cwd` is in.
    """
    out = _git(cwd, "worktree", "list", "--porcelain")
    if not out:
        return None
    for line in out.splitlines():
        if line.startswith("worktree "):
            return os.path.realpath(line[len("worktree "):])
    return None


def toplevel(cwd: str) -> "str | None":
    """The working tree root for `cwd` (subdir-resolving), realpath'd."""
    out = _git(cwd, "rev-parse", "--show-toplevel")
    return os.path.realpath(out) if out else None


def is_root_cwd(cwd: str, root: "str | None") -> bool:
    """True iff `cwd` is inside the repo's MAIN working tree (root or a subdir
    of it), and not inside a worktree. A worktree's toplevel is the worktree
    path, which differs from `root`, so the equality test already excludes it."""
    if not root:
        return False
    return toplevel(cwd) == os.path.realpath(root)
