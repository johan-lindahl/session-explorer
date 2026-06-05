# Changelog

All notable changes to session-explorer are documented here. This project
follows [semantic versioning](https://semver.org/).

## 1.11.4

### Fixed
- **The context-window `CTX %` is now model-aware and no longer jumps.**
  Previously the denominator was guessed at 200K and only promoted to 1M once a
  session's observed tokens *exceeded* 200K — so a 1M-context session climbed to
  ~99% and then visibly collapsed to ~20% the moment it crossed 200K, then
  climbed again. `index._context_window` now reads the window from the model id
  (`MODEL_WINDOWS`, prefix match): Opus 4.6/4.7/4.8 and Sonnet 4.6 map to 1M, so
  the percentage is correct from the first turn. This reflects that the 1M
  context window is now GA in Claude Code (no beta header since 2026-03-13) and
  is applied automatically on Max/Team/Enterprise + API plans; the `[1m]` alias
  suffix is stripped before the request, so it never reaches the transcript and
  can't be used as a signal. An overflow backstop still promotes to 1M for
  unmapped models that exceed their assumed window. Known trade-off: a session
  actually capped at 200K on a 1M-capable model (Pro plan without usage credits,
  or `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`) is measured against 1M and under-reports
  fullness — the inverse of the old jump, affecting only non-1M users.

## 1.11.3

### Fixed
- **The worktree indicator now turns green the instant a deleted worktree is
  recreated on resume**, instead of staying red until a manual rescan.
  Confirming the recreate prompt repaints that row in place via
  `_set_worktree_state`.
- **An empty worktree directory now counts as "dead", not "live".**
  `_worktree_state` previously called any existing dir "live" (so a bare dir
  left by an earlier failed resume showed a misleading green `⎇`); it now agrees
  with `_dead_worktree_repo` that an empty worktree dir is dead — keeping the
  indicator and the resume prompt consistent.

## 1.11.2

### Fixed
- **Resuming a deleted-worktree session now recreates the git worktree**, not an
  empty directory. Confirming the dead-worktree prompt runs `git worktree prune`
  then `git worktree add` on the `worktree-<leaf>` branch that `claude -w` uses —
  reattaching to that branch if it survived (preserving the work), otherwise
  creating it fresh from `HEAD` — so the session resumes in a real working tree.
  An empty directory left by a prior resume is also detected as "dead" and
  recreated, so a session whose worktree dir exists but is empty no longer
  silently resumes into a broken cwd. If git can't recreate the worktree, it
  falls back to a bare directory so `claude --resume` can still locate the
  transcript. The confirm prompt now reads "Recreate the worktree and resume?".

## 1.11.1

### Changed
- **Deleted-worktree glyph is now `dark_red`** instead of bright `red`, so the
  red `⎇` matches the muted darkness of the live-worktree `dark_green` `⎇`.
  Purely cosmetic — no behavior change.

## 1.11.0

### Added
- **Worktree indicator column.** A narrow column between the session name and
  its age shows a `⎇` glyph for sessions running in a git worktree
  (`<repo>/.claude/worktrees/<name>`): **dark-green** when the worktree
  directory still exists, **red** when it was deleted. Normal "root" checkouts
  stay blank. Deliberately a separate column from the left-edge live-session
  glyph so the two are never confused. The directory check runs once at
  tree-build time and is cached, so a worktree deleted while the TUI is open
  turns red on the next rescan.

## 1.10.0

### Added
- **`F2` renames** the selected node, aliased to `r` (conventional system
  rename key).
- **Blank-name temporary sessions.** Leaving the name empty on `c` starts a
  throwaway *unnamed* Claude session (launch omits `-n`, no index seeding). It
  stays hidden by default and is reaped by the existing `--gc` — no new deletion
  mechanism.
- **Select-the-new-session on create.** After `c`, the tree cursor jumps to the
  new session's row once it appears (immediately for a named session; on the
  next live poll for an unnamed one).
- **`z` collapse-to-roots.** Collapse the tree to project roots and drill into
  the one you want; the drill-down sticks across tree rebuilds within the session.

### Changed
- **`Tab` cycles three view modes**, replacing the old `u` toggle: **named +
  active** (default) → **active only** (just the live `●` sessions) → **all
  incl. unnamed** → back. `Tab` is suppressed while the `/` filter input is
  focused.

## 1.9.1

### Fixed
- **Explorer renames no longer revert.** A live Claude session re-writes its
  in-memory `custom-title` roughly once per turn, so renaming a *running*
  session was overwritten on Claude's next turn (names are read "last
  `custom-title` wins"). The index is now authoritative for explorer renames:
  `index.set_name` records superseded titles in a new `name_shadows[]` field,
  and `record_session` adopts the JSONL's last title only when it is **not**
  shadowed. All three rename paths (rename, folder-cascade, move) route through
  `set_name`. Backward compatible — sessions never renamed via the explorer have
  no shadows, so last-wins is unchanged. Already-reverted sessions self-heal on
  the next explorer rename.

## 1.9.0

### Added
- **macOS Dock launcher (`session-explorer install-app`).** Builds a clickable
  `~/Applications/Session Explorer.app` with the explorer icon and pins it to the
  Dock. The bundled launcher repairs `PATH` so it opens **with tmux**, and
  resolves the binary at run time so it survives plugin updates. `uninstall`
  removes the app and unpins it. The build is hand-rolled (no Automator), which
  avoids the `CFBundleIconName`/`Assets.car` icon-override and stripped-`PATH`
  traps of an Automator applet.

## 1.8.0

### Added
- **Subscription usage bar in the tmux status line (`g`).** Renders the same
  5-hour "Current session" percentage as Claude Code's `/usage` —
  `[████░░░░] 31% ↺1:30am` — in the (previously empty) `status-left`. Off by
  default, toggled with `g` and persisted; enabling fires an immediate probe
  then refreshes every 5 minutes. The %/reset live only in Anthropic's response
  headers (no local cache, no `claude usage` subcommand), so a hidden throwaway
  `claude` is driven through `/usage` on the dedicated `-L session-explorer`
  server and the panel is `capture-pane`d and parsed. The probe leaves no trace
  (both hooks bail out for it, its transcript is cleaned up each run) and
  degrades silently (not logged in / no tmux / parse miss → bar unchanged).

## 1.7.0

### Changed
- **Split-pane resume replaces window-flipping.** The explorer stays in the left
  pane and the active Claude session docks in the right pane, side by side.
  `Enter` docks a session and focuses it (entering another swaps it in, the
  previous keeps running in the background); double-click == Enter. **F9**
  toggles focus between panes (mouse-click also focuses); **F12** zooms the
  focused pane fullscreen and back. Navigating the tree cursor-follows: landing
  on a running session of ours docks it without stealing focus, landing on a
  stopped/peek-only session or folder closes the pane (debounced ~0.2s). The
  tree is the only session switcher — no window-tab status bar — and a
  persistent `F9 ⇄ switch · F12 ⤢ full` hint lives in the tmux status line so it
  survives the zoomed-fullscreen case.

## 1.6.0

### Added
- **Create new sessions from the explorer (`c`).** A `c` on a project or folder
  node creates a new Claude session — naming it directly and optionally spinning
  up a git worktree — without leaving the explorer. A modal collects the name
  (prefilled with the folder prefix so a slash-path nests it), the working
  directory (derived from the project's most-recently-active session, worktree
  suffix stripped to the repo root; editable), and an optional git worktree.
  Launches `claude --session-id <uuid> -n <name> [-w [<wt>]]` — Claude writes
  the `custom-title` and owns all worktree/branch creation. Under tmux it starts
  as a sibling window; without tmux it falls back to `execvp`. Creation seeds
  `name_cached` immediately and `record_session` preserves a known name when the
  transcript yields none, so a just-created session never flickers as
  `(unnamed)`.

## 1.5.0

### Added
- **tmux-backed multi-session interaction.** The explorer now runs as window 0
  of a dedicated `tmux -L session-explorer` server and stays alive while you
  work. `Enter` on a stopped session starts it and switches you straight in;
  `Enter` on a running session flips in to interact. `space` shows a live
  snapshot of the selected session in the preview pane — `capture-pane` for
  our tmux windows, transcript-tail for sessions running elsewhere. Switching
  back uses clickable status-bar tabs (primary) or F12 (keyboard fallback).
  The status bar shows each session's human name, not its raw id, and shows a
  `F12 → explorer` hint while you're inside a session.
- **Context-aware Enter.** Stopped → start the session and switch into it
  (single keypress); running → flip in; live-elsewhere (another terminal holds
  the transcript) → refuse with a warning and offer peek-only, preventing
  duplicate `claude --resume` processes on one JSONL.
- **Accessibility-aware live glyphs.** When tmux-hosted, the tree distinguishes
  sessions running in *our* tmux (solid green `●` — press Enter to jump in) from
  sessions running in a separate terminal (hollow green `○` — peek-only). All
  live glyphs are green for visibility; the shape carries the distinction.
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
