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

import os
from typing import List

# Conservative auto-protect default (spec §2): applied with no prompt.
DEFAULT_PROTECT = ["/.git", "/.env", "/.env.*"]


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
