"""Best-effort out-of-lease change detector for root-dir resources (spec §6/§9).

Snapshots the top-level entry set + mtimes of the shared root and compares
between polls. A change with no ticket held is a *weak* signal of out-of-lease
access — debounced and surfaced as a transient toast, never as enforcement.
Catches creates/deletes/renames; MISSES in-place content writes. Excludes the
protect baseline and any caller-supplied generated paths.

`exclude` entries are **glob patterns matched against the top-level entry name**
(via `fnmatch`), so a protect pattern like `.env.*` correctly excludes
`.env.local` — matching how the sync `protect` baseline is anchored. (Callers
strip the protect entries' leading `/` before passing them in.)
"""

from __future__ import annotations

import fnmatch
import os
from typing import Dict, Set


def _excluded(name: str, exclude: Set[str]) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in exclude)


def top_level_snapshot(path: str, *, exclude: Set[str]) -> Dict[str, float]:
    """{entry_name: mtime} for the immediate children of `path`, dropping any
    whose name matches an `exclude` glob (e.g. '.git', '.env', '.env.*')."""
    out: Dict[str, float] = {}
    try:
        with os.scandir(path) as it:
            for entry in it:
                if _excluded(entry.name, exclude):
                    continue
                try:
                    out[entry.name] = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    out[entry.name] = 0.0
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return {}
    return out


def changed(before: Dict[str, float], after: Dict[str, float]) -> bool:
    """True iff the entry set or any mtime differs (creates/deletes/renames)."""
    return before != after
