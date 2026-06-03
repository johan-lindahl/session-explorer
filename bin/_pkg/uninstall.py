"""Reverse the install-time side effects.

`teardown` is idempotent: every step is a no-op when its target is absent, so the
same routine safely covers both install paths (plain `install.sh` and marketplace)
and is safe to re-run. It is shared by the `session-explorer uninstall` subcommand
and `uninstall.sh`.
"""

from __future__ import annotations

import json
import os
import shutil

_HOOK_MARKERS = ("session-explorer", "session-start.sh", "session-live.sh")
# Lifecycle events the plugin registers the live dispatcher on (plus SessionStart).
# Mirrored in install.sh and .claude-plugin/plugin.json; keep all three in sync.
_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "Notification",
                "SessionEnd")
_OPERATIONAL_SIDECARS = (
    ".session-explorer.current",
    ".session-explorer.help-seen",
    ".session-explorer.retention-declined",
    ".session-explorer.gc",
    "session-explorer.log",
    "session-explorer-live.json",
    "session-explorer-live.json.lock",
    # tmux interaction-layer artifacts.
    ".session-explorer.tmux.conf",
    ".session-explorer.tmux-persist",
    ".session-explorer.tmux-declined",
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
             purge_data: bool = False, mac_apps_dir: "str | None" = None) -> list[str]:
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

    # 2. Strip our hook entries across every lifecycle event (no-op on marketplace
    #    installs, where the hooks live in plugin.json and /plugin uninstall removes
    #    them). Unrelated user hooks are preserved; events left empty are dropped.
    if settings is not None:
        hooks = settings.get("hooks")
        if isinstance(hooks, dict):
            for evt in _HOOK_EVENTS:
                entries = hooks.get(evt)
                if not isinstance(entries, list):
                    continue
                kept = [h for h in entries if not _is_our_hook(h)]
                if len(kept) != len(entries):
                    actions.append(f"removed {evt} hook entry")
                if kept:
                    hooks[evt] = kept
                elif evt in hooks:
                    del hooks[evt]

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

    # 4. Operational sidecars (includes tmux interaction-layer artifacts).
    for name in _OPERATIONAL_SIDECARS:
        path = os.path.join(claude_dir, name)
        if os.path.exists(path):
            os.unlink(path)
            actions.append(f"removed {name}")

    # 4b. Kill our dedicated tmux server if it is still running (best-effort).
    try:
        from . import tmux as _tmux
        if _tmux.available():
            _tmux.kill_server()
    except Exception:
        pass

    # 4c. Remove the macOS Dock launcher app (best-effort; macOS only in practice
    #     but path-driven so it is testable cross-platform).
    home = os.path.dirname(os.path.abspath(claude_dir))
    apps_dir = mac_apps_dir or os.path.join(home, "Applications")
    app = os.path.join(apps_dir, "Session Explorer.app")
    if os.path.isdir(app):
        shutil.rmtree(app, ignore_errors=True)
        actions.append("removed Session Explorer.app")
        # Unpin from the Dock (best-effort; only meaningful on macOS).
        try:
            from . import macapp
            macapp._unpin_from_dock(app)  # added below
        except Exception:
            pass

    # 5. User data (opt-in).
    if purge_data:
        for name in _DATA_FILES:
            path = os.path.join(claude_dir, name)
            if os.path.exists(path):
                os.unlink(path)
                actions.append(f"removed {name}")

    return actions
