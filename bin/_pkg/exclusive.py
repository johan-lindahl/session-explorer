"""Root-dir exclusive-or-with-live-session policy (spec §5). root-dir ONLY.

Root is either a live working session OR the lease sandbox, never both -- which
is what makes a destructive `sync` acquire safe. Liveness comes from the live
registry (live.py), judged by live._alive (PID + 24h TTL). A session is "in
root" when its cwd resolves (via the canonical project_id helper, NOT the weaker
index.project_root string-strip) to the project's main working tree and is not a
worktree. The transition guard refuses a dirty root; the rsync dry-run refusal
(qsync.unclassified) is the second, authoritative layer for ignored files.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Optional

from . import live as _live
from . import project_id as _pid


def live_root_session(live_path: str, main_root: str, *,
                      now: Optional[datetime] = None,
                      ttl_seconds: int = _live.DEFAULT_TTL_SECONDS) -> Optional[dict]:
    """Return {"sid", "cwd", "name"} of a live session working in `main_root`
    (root or a subdir, not a worktree), or None. The first such session wins."""
    now = now or datetime.now(timezone.utc)
    data = _live.load(live_path)
    for sid, entry in data.get("sessions", {}).items():
        if not _live._alive(entry, now, ttl_seconds):
            continue
        cwd = entry.get("cwd")
        if cwd and _pid.is_root_cwd(cwd, main_root):
            return {"sid": sid, "cwd": cwd, "name": entry.get("name", sid)}
    return None


def transition_guard(main_root: str) -> Optional[str]:
    """Refusal reason if `main_root` has uncommitted git changes, else None.
    This catches tracked changes; ignored root-only files are caught by the
    rsync dry-run refusal layer (qsync)."""
    try:
        r = subprocess.run(["git", "-C", main_root, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None  # fail open: the dry-run refusal still guards deletes
    if r.returncode == 0 and r.stdout.strip():
        return ("root has uncommitted changes the sandbox would overwrite — "
                "stash/commit first")
    return None
