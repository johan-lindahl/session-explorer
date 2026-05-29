# Changelog

All notable changes to session-explorer are documented here. This project
follows [semantic versioning](https://semver.org/).

## 1.2.0

### Added
- Live sessions' rows now refresh from their transcript about every 2s, so a
  freshly-started session's first prompt, message count, tokens, and context %
  fill in and tick up live (no manual F5). Persisted to the index, off the UI
  thread; only live sessions are re-read.
- README + in-app help now document the live-session glyphs, with a new
  screenshot; a dev-only `scripts/gen_screenshots.py` regenerates the images.

### Changed
- The rename / move / new-folder / delete / notes dialogs are now centered
  overlays on a dimmed backdrop (matching the help screen) instead of
  full-screen black panels.

## 1.1.0

### Added
- **Live-session indicator.** The TUI now shows which sessions are running:
  an animated green spinner for sessions actively working, a dim `○` for
  sessions open but idle, and nothing for inactive ones; the subtitle shows
  `● N active`. Scales to multiple concurrent sessions.
- New lifecycle hooks (`UserPromptSubmit`, `Stop`, `Notification`/`idle_prompt`,
  `SessionEnd`, plus a second `SessionStart` command) feed a volatile registry
  at `~/.claude/session-explorer-live.json` via `session-explorer live`.
- Liveness is crash-safe: a session is live iff its recorded Claude PID is
  alive (`kill -0`), with a 24h TTL backstop; dead sessions are pruned on poll.
  `SessionEnd` removal is best-effort only.
- Live sessions are surfaced in the tree even when unnamed (an exception to the
  hide-unnamed default), so a running agent you haven't named is still visible.

### Changed
- `install.sh`/`uninstall.sh` now register/remove the new lifecycle hooks. This
  is install-time only and independent of retention — `cleanupPeriodDays` is
  untouched.

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
