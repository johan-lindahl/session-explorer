# Changelog

All notable changes to session-explorer are documented here. This project
follows [semantic versioning](https://semver.org/).

## 1.5.0

### Added
- **tmux-backed multi-session interaction.** The explorer now runs as window 0
  of a dedicated `tmux -L session-explorer` server and stays alive while you
  work. `Enter` on a stopped session starts it in a background tmux window;
  `Enter` on a running session flips in to interact. `space` shows a live
  snapshot of the selected session in the preview pane — `capture-pane` for
  our tmux windows, transcript-tail for sessions running elsewhere. Switching
  back uses clickable status-bar tabs (primary) or F12 (keyboard fallback).
- **Context-aware Enter.** Stopped → launch in background and stay in explorer;
  running → flip in; live-elsewhere (another terminal holds the transcript) →
  refuse with a warning and offer peek-only, preventing duplicate `claude
  --resume` processes on one JSONL.
- **Quit-guard.** `q` with live sessions prompts: `[s]` shut down all and quit
  (`tmux kill-server`), `[b]` leave running in the background (sets persist-flag
  then detaches), or `[c]` cancel. Zero live sessions quit cleanly with no
  prompt.
- **Abrupt-close sentinel (Option C).** A `client-detached` hook in the
  generated config kills the server on any unintentional detach (red button,
  `Cmd+W`). Only the deliberate `[b]` leave-running path sets the persist-flag
  beforehand, preventing orphaned sessions.
- **Optional consented tmux install.** First launch without tmux shows a
  one-time yes/no prompt; **yes** shows the install command for the detected
  package manager (`brew`, `apt-get`, `dnf`, `pacman`, …) to run yourself,
  **no** writes a marker so it is not re-nagged. The plugin only shows the
  command — no binary bundling, no silent sudo.
- **`execvp` fallback.** Without tmux (absent, too old, or declined), resume
  behaves exactly as before (process-replace the explorer with `claude
  --resume`). No regression for non-tmux users.

## 1.4.0

### Added
- `r` (rename) and `m` (move) now work on **folder** nodes, not just sessions.
  Renaming a folder renames its last segment in place; moving re-parents the
  whole subtree under a chosen path (or `(ungroup)` to top level). Because a
  folder is just the prefix shared by the session names under it, both cascade:
  every contained session is rewritten (a `custom-title` event appended to each
  JSONL, all `name_cached` updates in one index write) and matching folder-store
  entries are re-prefixed in one pass, so empty subfolders move too. The cascade
  is gated behind a confirmation that names the affected session count.
  Segment-wise prefix matching means renaming `team/planning` never touches a
  sibling `team/planning-extra`; re-parenting a folder into its own subtree is
  rejected; renaming/moving onto an existing path merges into it.

## 1.3.0

### Changed
- The `F5` rescan progress now shows in a centered modal panel (the same
  `_PanelScreen` styling as the rename / move / new-folder / delete / notes
  dialogs) overlaid on the dimmed tree, instead of blanking the tree pane to a
  full-screen black panel. This completes the 1.2.0 dialog restyle, which had
  missed the rescan view because it was drawn inline rather than as a modal.

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
