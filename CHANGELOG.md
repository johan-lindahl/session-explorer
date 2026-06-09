# Changelog

All notable changes to session-explorer are documented here. This project
follows [semantic versioning](https://semver.org/).

## 1.16.0

### Added
- **Shared installed app root (overlay tests) — experimental.** New queue
  template `overlay-installed-root` plus a `queue-overlay in|out` helper:
  serialize "run tests in the installed root" overlays through a
  failure/signal-safe lease instead of a hand-rolled cp/git-restore. On acquire
  the lease copies the worktree's changed files into the root (refusing a dirty
  root); on release the engine restores exactly those paths. Curated PHP guard
  (phpunit/phpstan/`bin/magento setup:*`; deliberately not phpcs/php-cs-fixer,
  which are worktree-safe).

### Changed
- The shared-resource queue subsystem is now clearly labeled **experimental**
  (Queues pane header, resource setup/editor dialogs, offline help, and the
  SessionStart awareness text) — it is cooperative/advisory and cannot prevent
  an uncoordinated process from touching a resource.

## 1.15.0

### Changed
- **Sessions now persist across every explorer exit except an explicit "shut
  down all."** Closing the terminal window (red button / `Cmd+W`) or a crash no
  longer kills your running Claude sessions — the dedicated tmux server stays
  alive and the next `/session-explorer:open` reattaches you to exactly where
  you left off. Only `x` → `[s]` ("shut down all") tears the server down; `x` →
  `[b]` ("leave running") and any abrupt close are now equivalent. This reverses
  the prior "Option C" behavior (the `client-detached` kill hook and persist-flag
  marker are removed). Trade-off: background sessions can accumulate — the tree's
  live indicators and the `q` quit prompt list what is still running.

### Fixed
- **New-session launch failures are surfaced instead of vanishing.** When a new
  session can't start — most often `claude -w` unable to create its git worktree
  (a stray `.claude/worktrees/<slug>` directory or a transient `.git` lock) — the
  `claude` process exits and its tmux window closes before anything is visible.
  The explorer now captures that startup stderr, shows it as a warning toast,
  logs it to `~/.claude/session-explorer.log`, and records it on the session so
  the preview shows `Launch failed: …`.
- **A named session whose first turn never happened no longer refuses to open.**
  Such a session has no transcript, so resuming it with `claude --resume` could
  never work. Pressing Enter now starts it fresh (reusing its id and name, with
  the worktree defaulted the same way new-session creation does) instead of
  failing silently.

## 1.14.0

### Added
- **Shared-resource lease engine — agent awareness & command-guard (Phase 3).**
  Every Claude session in an opted-in project is now told the project shares
  singleton resources, and guarded commands are nudged toward `queue-run`.
  - **SessionStart awareness injection.** For an opted-in project the
    SessionStart hook injects `additionalContext` (via the new
    `session-explorer queue-context`) listing the declared shared resources,
    their guarded commands, and how to cooperate (use `queue-run`, never boot a
    second copy of a warm shared stack, don't busy-spin on a busy resource,
    expect a `sync` lease to overwrite the shared root).
  - **PreToolUse command-guard.** A new fail-open `PreToolUse` Bash hook
    (`hooks/pre-tool-use.sh` → `session-explorer queue-guard`) denies a guarded
    command and redirects it to `session-explorer queue-run --resource <name> --
    <command>`. Compound commands (shell operators, newlines) are wrapped whole
    in `bash -c` so every separator runs inside the lease. Matching reuses the
    Phase-1 parsed-argv `guard_match` (never a substring regex), so already-
    wrapped `queue-run` invocations and `echo queue-run && …` bypasses are
    handled correctly.
  - **Cooperative guidance.** `docs/queue-guide.md` gains a "Cooperating as an
    agent" section with a copy-paste `CLAUDE.md` snippet.
- Decision text and guard matching are single-sourced in the new pure
  `bin/_pkg/queue_awareness.py` module.

### Changed
- The `PreToolUse` hook is registered on all install paths — the marketplace
  manifest (`.claude-plugin/plugin.json`), the plain `install.sh`, and torn down
  by `uninstall.py` — using the documented nested matcher-group form. Install/
  uninstall now prune nested sub-hooks by concrete script name so a user hook
  sharing the `Bash` matcher group is never dropped.

### Notes
- **Fail open, always.** Both hooks emit nothing and exit 0 on any error (bad
  payload, missing config, parse ambiguity) — a false deny is worse than a
  missed guard. Accepted v1 blind spot: wrappers (`make`/`npm run`) that hide a
  guarded command are not caught; the awareness injection is the backstop.

## 1.13.0

### Added
- **Shared-resource lease engine — TUI surface (Phase 2).** The Phase-1
  `queue-run`/`queue-status`/`queue-cancel` core now has an explorer front end,
  read directly from the in-process stores (no shelling out).
  - **Queues pane (`q`)** — a global, **read-only** view of every active queue
    across all opted-in projects, each row showing the holder (+ elapsed) and
    the waiting line with positions ("1 of 2"); `⛔ held by live session` for a
    root-dir blocked by a live root session. Live on the existing ~2s refresh
    loop. **Content-gated**: it takes space only when there is an active queue
    anywhere or the selected project has configured resources, so a curious
    one-time toggle never leaves a permanent empty pane. Visibility persists
    globally in `~/.claude/session-explorer-ui.json`.
  - **Per-project setup (`s`)** — a resource list (add/edit/remove) and a
    **template-first editor** that reflows per `kind`: a `device`/`port`/`name`
    hides the path + protect inputs and runs in a worktree; a `root-dir` shows
    `protect` and a read-only canonical path (always the repo's main working
    tree). Templates cover the documented cases (bind-mounted stack, browser
    e2e, iOS simulator, shared DB, root-only `.env`, device/seat, custom).
  - **Test panel** — a guard-match tester (no side effects), an **rsync
    dry-run** that highlights deletions *and* surfaces the exclusive-or check
    (live-root block + dirty-root transition guard) and refuses when the source
    equals root, and a health probe. De-risks the destructive `sync` acquire
    before any real run.
  - **New-session dialog** — checking *Create git worktree* auto-fills the
    worktree name from a slug of the session name (manual edits stop the
    sync); the checkbox **defaults on** and a plain-root session warns when the
    project has a `root-dir` resource.
  - **Best-effort out-of-lease detection toast** — a debounced, weak signal
    that the shared root changed while no lease and no live root session held
    it. Honest about its limits (catches creates/deletes/renames, misses
    in-place writes); never enforcement.
  - **Offline `?` help** and a copyable guide link, plus the full
    `docs/queue-guide.md` user guide (when-NOT-to-use first, then the
    `--delete`/`protect` rules and template catalog).

### Changed
- **`q` now toggles the Queues pane and `x` exits** (quit moved off `q`). The
  only added footer key is `q`; `s`/`a`/`e`/`Del` are pane-local, and `x` is
  *only* Exit — never a destructive action.

## 1.12.1

### Fixed
- **Ghost project nodes and spurious parent-prefixed labels from legacy
  folder-store keys.** The registered `SessionStart` hook can be an *older
  installed plugin copy* than the TUI being run, and a pre-v1.11.5 hook re-adds
  **basename** folder-store keys (e.g. `"magento-os": ["planning"]`) after the
  store was already migrated to root keys. The tree treated such a bare key as
  its own repo root, so the basename looked contested: the real repo got a
  spurious parent prefix (`RoyalUnibrew/magento-os`) while a ghost
  `magento-os (0)` node rendered its folders — and F5 couldn't fix it (reindex
  rewrites the index, not the store; the re-key was one-shot and version-gated).
  Two defenses now make the explorer immune regardless of what's in the store:
  `build_nested_tree` **folds** any bare (no-`/`) store key into every session
  root sharing that basename at render time, and `migrate_folder_store_keys` is
  **self-healing** — a v2 store is re-checked on every CLI entry and bare keys
  that resolve to a session root are re-keyed (the file is rewritten only when
  something actually changed). Bare keys that resolve to nothing
  (empty-folder-only projects, `(unfiled)`) still render as their own nodes.

## 1.12.0

### Added
- **Reversible worktree cleanup — reclaim the disk that git worktrees eat.**
  Git worktrees (`<repo>/.claude/worktrees/<name>`) used to pile up: the
  explorer's recreate-on-resume path rebuilds them with raw `git worktree add`,
  so Claude (which only offers native cleanup from the `-w`-creating process)
  never prompts to remove them, and opt-in retention's `cleanupPeriodDays =
  36500` disables Claude's own age-based sweep too. The explorer now owns
  cleanup, through one non-destructive primitive (`bin/_pkg/worktree.py`):
  `git worktree remove` **without `--force`** (git refuses any dirty or
  untracked tree — the safety floor) and the `worktree-<name>` branch is never
  deleted. Because a deleted worktree is rebuilt on resume, removing a directory
  is reversible — committed work and the transcript always survive. Three ways
  to trigger it:
  - **`w`** removes the selected worktree session's directory after a confirm
    (showing its on-disk size); refuses while the session is live/running/docked,
    and flips the indicator to `dark_red` in place.
  - **An on-exit offer** when a docked worktree session ends clean — asked once
    per session (a cancel won't re-nag; press `w` to retry).
  - **`--gc` pruning** of idle (dir untouched > 14 days), clean, non-live
    worktrees — including those of *kept* sessions, since the transcript and
    branch survive and resume rebuilds. Runs in the same `--gc` pass, honours
    `--dry-run`, and never mutates the index.
- **The preview pane shows a worktree's on-disk size** (`du -sh`, cached per
  session so the refresh timer never re-stats).

## 1.11.5

### Fixed
- **Sessions from different repos that share a name no longer collapse into one
  tree node.** Working across several checkouts named `magento2` (one per
  organization) previously merged them all under a single `magento2` project,
  because the grouping key was the repo *basename*. Grouping — and the folder
  store — are now keyed by the repo **root path** (`project_path`, with any
  `/.claude/worktrees/<name>` suffix stripped), so distinct repos stay separate
  while a repo's worktrees still collapse under it. The displayed label remains
  the bare basename, and is prefixed with the **minimal distinguishing ancestor
  path only on collision**: `acme/magento2` when the immediate parent suffices,
  `work/…/magento2` when a higher ancestor is needed (`tree_model.disambiguate`).
  A lone repo is unaffected. A one-shot, idempotent migration
  (`index.migrate_folder_store_keys`) re-keys any existing basename-keyed folder
  store to repo roots, mapping each key via the session index and copying into
  each root when a basename was shared.

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
