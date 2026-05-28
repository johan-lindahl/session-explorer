"""Opt-in session-retention management.

Neutralising Claude Code's native cleanup (settings.json `cleanupPeriodDays`)
modifies the user's settings, so it is **opt-in**: the TUI asks on first launch
(see tui.on_mount). This module holds the enable/decline/state logic, shared by
that prompt and by uninstall.

State signals (both under ~/.claude, or the index's dir in tests):
- backup file `.session-explorer.backup` exists  → retention ENABLED (also lets
  uninstall restore the prior value).
- marker  `.session-explorer.retention-declined` → user DECLINED; don't re-ask.
- neither                                         → UNDECIDED; prompt on launch.
"""

from __future__ import annotations

import json
import os

_NEUTRALISED = 36500   # ~100 years: native cleanup never fires
_DEFAULT_PRIOR = 30    # Claude's default cleanupPeriodDays when unset


def backup_path(claude_dir: str) -> str:
    return os.path.join(claude_dir, ".session-explorer.backup")


def declined_path(claude_dir: str) -> str:
    return os.path.join(claude_dir, ".session-explorer.retention-declined")


def _settings_path(claude_dir: str) -> str:
    return os.path.join(claude_dir, "settings.json")


def is_enabled(claude_dir: str) -> bool:
    return os.path.exists(backup_path(claude_dir))


def is_declined(claude_dir: str) -> bool:
    return os.path.exists(declined_path(claude_dir))


def is_decided(claude_dir: str) -> bool:
    """True once the user has either enabled or declined retention."""
    return is_enabled(claude_dir) or is_declined(claude_dir)


def enable(claude_dir: str) -> int:
    """Back up the prior `cleanupPeriodDays` and set it to 36500 so the plugin
    owns retention. Idempotent: the backup is written once (preserving the real
    prior value across repeat calls). Returns the backed-up prior value. Clears
    any prior decline marker."""
    os.makedirs(claude_dir, exist_ok=True)
    sp = _settings_path(claude_dir)
    try:
        with open(sp, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}

    prior = data.get("cleanupPeriodDays", _DEFAULT_PRIOR)
    bp = backup_path(claude_dir)
    if not os.path.exists(bp):
        with open(bp, "w", encoding="utf-8") as f:
            f.write(str(prior))

    data["cleanupPeriodDays"] = _NEUTRALISED
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    dp = declined_path(claude_dir)
    if os.path.exists(dp):
        os.unlink(dp)
    return prior


def decline(claude_dir: str) -> None:
    """Record that the user declined retention, so the prompt isn't shown again.
    Leaves settings.json untouched (native cleanup stays in charge)."""
    os.makedirs(claude_dir, exist_ok=True)
    open(declined_path(claude_dir), "a").close()
