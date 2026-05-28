"""Reverse the install-time side effects.

`teardown` is idempotent: every step is a no-op when its target is absent, so the
same routine safely covers both install paths (plain `install.sh` and marketplace)
and is safe to re-run. It is shared by the `session-explorer uninstall` subcommand
and `uninstall.sh`.
"""

from __future__ import annotations

import json
import os

_HOOK_MARKERS = ("session-explorer", "session-start.sh")
_OPERATIONAL_SIDECARS = (
    ".session-explorer.current",
    ".session-explorer.help-seen",
    "session-explorer.log",
)
_DATA_FILES = (
    "session-explorer-index.json",
    "session-explorer-index.json.lock",
    "session-explorer-folders.json",
)


def _is_our_hook(entry: object) -> bool:
    return isinstance(entry, dict) and any(
        m in str(entry.get("command", "")) for m in _HOOK_MARKERS
    )


def teardown(*, claude_dir: str, settings_path: "str | None" = None,
             purge_data: bool = False) -> list[str]:
    """Restore `cleanupPeriodDays`, strip the SessionStart hook, remove the
    `~/.local/bin` symlink, and delete sidecars. With `purge_data`, also delete
    the index and folder store. Returns a list of human-readable actions taken."""
    settings_path = settings_path or os.path.join(claude_dir, "settings.json")
    backup_path = os.path.join(claude_dir, ".session-explorer.backup")
    actions: list[str] = []

    settings: "dict | None" = None
    if os.path.exists(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            settings = None

    # 1. Restore cleanupPeriodDays from the backup, then drop the backup.
    if settings is not None and os.path.exists(backup_path):
        try:
            prior = int(open(backup_path, encoding="utf-8").read().strip())
        except (ValueError, OSError):
            prior = None
        if prior is not None:
            settings["cleanupPeriodDays"] = prior
            actions.append(f"restored cleanupPeriodDays={prior}")
    if os.path.exists(backup_path):
        os.unlink(backup_path)
        actions.append("removed .session-explorer.backup")

    # 2. Strip our SessionStart hook entry (no-op on marketplace installs, where
    #    the hook lives in plugin.json and /plugin uninstall removes it).
    if settings is not None:
        starts = (settings.get("hooks") or {}).get("SessionStart")
        if isinstance(starts, list):
            kept = [h for h in starts if not _is_our_hook(h)]
            if len(kept) != len(starts):
                settings["hooks"]["SessionStart"] = kept
                actions.append("removed SessionStart hook entry")

    if settings is not None and actions:
        # Only rewrite if we touched it.
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)

    # 3. Remove the plain-install symlink (~/.local/bin/session-explorer).
    home = os.path.dirname(os.path.abspath(claude_dir))
    symlink = os.path.join(home, ".local", "bin", "session-explorer")
    if os.path.islink(symlink):
        os.unlink(symlink)
        actions.append(f"removed symlink {symlink}")

    # 4. Operational sidecars.
    for name in _OPERATIONAL_SIDECARS:
        path = os.path.join(claude_dir, name)
        if os.path.exists(path):
            os.unlink(path)
            actions.append(f"removed {name}")

    # 5. User data (opt-in).
    if purge_data:
        for name in _DATA_FILES:
            path = os.path.join(claude_dir, name)
            if os.path.exists(path):
                os.unlink(path)
                actions.append(f"removed {name}")

    return actions
