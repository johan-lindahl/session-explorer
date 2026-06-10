"""SessionStart awareness hint for shared-resource projects.

One pure entry point: `session_context(config_path, cwd)` -> the SessionStart
`additionalContext` text for an opted-in project, or None. Since the
leased-ground change, this is a USAGE HINT about an enforced wall (the
PreToolUse root guard denies root-touching tool calls), not a cooperation
contract — the old `guard_reason` command matching lives on, location-based,
in `root_guard.py`.

No argparse, no Textual, no stdout. Callers (cli.py) wrap in try/except and
fail open.
"""

from __future__ import annotations

from . import project_id as _pid
from . import queue_config as _qc


def _render_context(resources: dict) -> str:
    root_id = None
    root_res = None
    for rid in sorted(resources):
        if resources[rid].get("kind") == "root-dir":
            root_id, root_res = rid, resources[rid]
            break
    lines = []
    if root_res is not None:
        lines += [
            f"This project's installed root at {root_res.get('path')} is "
            f"shared across worktrees and write-blocked outside a lease "
            f"(tool calls that touch it are denied).",
            f"Run anything that needs the installed root (tests, builds, "
            f"installs) as: `session-explorer queue-run --resource {root_id} "
            f"-- <cmd>` — it overlays your changed files into the root, runs, "
            f"and restores them.",
            "`session-explorer queue-status` shows the current holder and "
            "queue.",
        ]
    others = [rid for rid in sorted(resources) if rid != root_id]
    if others:
        lines.append(
            "Other shared resources for this project (serialize the same "
            "way, via `session-explorer queue-run --resource <id> -- <cmd>`): "
            + ", ".join(others) + ".")
    return "\n".join(lines)


def session_context(config_path: str, cwd: str) -> "str | None":
    pid = _pid.project_id(cwd)
    if not pid:
        return None
    resources = _qc.list_resources(config_path, pid)
    if not resources:
        return None
    return _render_context(resources)
