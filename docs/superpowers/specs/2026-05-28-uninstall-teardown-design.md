# Uninstall teardown — design

**Date:** 2026-05-28
**Status:** Approved, pre-implementation

## Problem

Install neutralises Claude Code's native session cleanup by setting
`cleanupPeriodDays: 36500` in the user's global `~/.claude/settings.json`, backing
up the prior value to `~/.claude/.session-explorer.backup`. Nothing ever restores
it. So removing the plugin (via `/plugin uninstall` or by deleting the repo) leaves
native cleanup **permanently disabled**, plus orphaned sidecar files. The teardown
is described in SPEC.md / CLAUDE.md but was never built (M3).

## Key facts

- **Names survive a purge.** Session names live in the JSONLs as `custom-title`
  events; a reinstall + rescan re-derives them. Only **notes** and **empty
  user-created folders** live solely in the index / folder store — those are what
  `--purge` actually discards.
- **Two install paths.** Plain `install.sh` writes the SessionStart hook into
  `settings.json` and symlinks `bin/session-explorer` into `~/.local/bin/`.
  Marketplace install registers the hook via `plugin.json` (removed by
  `/plugin uninstall`) and creates no symlink.

## Design

### One testable teardown, two thin entry points

`bin/_pkg/uninstall.py`:

```python
def teardown(*, claude_dir: str, settings_path: str | None = None,
             purge_data: bool = False) -> list[str]:
    """Reverse install-time side effects. Idempotent — every step is a no-op
    when its target is absent. Returns human-readable actions performed."""
```

Steps, in order:

1. **Restore `cleanupPeriodDays`** from `<claude_dir>/.session-explorer.backup`
   into `settings.json`, then delete the backup file. If the backup or settings
   file is absent, skip.
   - *Known limitation:* the backup stores a bare integer (default `30` when the
     key was originally unset), so we write that integer back rather than
     re-removing the key. Fixing fidelity would require a backup-format change
     that existing installs can't retroactively benefit from. Out of scope.
2. **Remove the SessionStart hook entry** from `settings.json` using the same
   idempotent filter `install.sh` applies (drop entries whose `command` contains
   `session-explorer` or `session-start.sh`). No-op on marketplace installs.
3. **Remove the `~/.local/bin/session-explorer` symlink** if present. No-op on
   marketplace installs.
4. **Delete operational sidecars:** `.session-explorer.current`,
   `.session-explorer.help-seen`, `session-explorer.log`.
5. **If `purge_data`:** also delete `session-explorer-index.json` (+ its `.lock`)
   and `session-explorer-folders.json`.

Idempotency lets the single routine cover both paths and be safe to re-run.

### Entry points

- **`session-explorer uninstall [--purge]`** (CLI subcommand) — runs teardown,
  prints the actions, then reminds the user to run `/plugin uninstall
  session-explorer` in Claude Code to remove the plugin files.
- **`uninstall.sh [--purge]`** (plain path) — thin wrapper that `exec`s
  `$REPO/bin/session-explorer uninstall "$@"`, so the logic has one home.

No new slash command — uninstall stays a CLI/shell action, consistent with the
single-entry-point (`/open`) design.

## Testing (pytest `test_uninstall.py`)

- Restores `cleanupPeriodDays` from the backup and deletes the backup file.
- Removes only the session-explorer hook entry; other hooks and other settings
  keys are left intact.
- Removes the `~/.local/bin` symlink when present.
- Deletes operational sidecars; preserves index + folder store by default.
- `purge_data=True` also deletes the index (+ `.lock`) and folder store.
- Idempotent: running with no backup/settings/sidecars present does nothing and
  raises nothing.

## Docs

- README: uninstall instructions for both paths, including the resolver one-liner
  marketplace users run (mirroring `commands/open.md`).
- SPEC.md / CLAUDE.md already describe the teardown; mark it shipped.
