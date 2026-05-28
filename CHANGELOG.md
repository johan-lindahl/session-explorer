# Changelog

All notable changes to session-explorer are documented here. This project
follows [semantic versioning](https://semver.org/).

## 1.0.0

First release prepared for the community marketplace.

### Added
- Textual TUI: tree view grouped by project and `/`-folders, arrow-key
  navigation, expand/collapse (`←`/`→`), rename, move, delete, notes, preview
  pane, live filter (`/`), rescan (`F5`), and in-TUI help (`h`) showing the
  version and repo link.
- Resume (`Enter`) via `claude --resume=<id>`; falls back to recreating a
  deleted git worktree's directory (with confirmation) so its session still
  resumes.
- Delete empty folders with `d` (refuses if the folder still contains sessions).
- Model-aware context-window percentage (reads `message.model`; promotes to a
  1M window when usage exceeds 200K) and a Model + full project Path field in
  the preview pane.
- `session-explorer index --gc [--dry-run] [--retention-days N]` — retention GC
  of old **unnamed** sessions, with a live-session guard.
- `session-explorer uninstall` and `uninstall.sh` teardown.
- macOS, Linux, and WSL terminal launchers (native Windows is out of scope).

### Changed
- **Retention is now opt-in.** The plugin no longer modifies your Claude Code
  settings automatically. The first time you open the explorer it asks whether
  to manage retention; only then is `cleanupPeriodDays` backed up and set to
  `36500`, and the once-daily background GC enabled. Declining leaves Claude's
  native cleanup in charge.

### Tested
- pytest + a focused bats suite (install/uninstall/hook), run in CI on
  ubuntu + macos across Python 3.11–3.13.

### License
- MIT.
