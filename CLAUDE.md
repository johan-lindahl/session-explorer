# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Implemented and released, installable from the Claude Code marketplace. The hook, manifest, index core, Textual TUI, `--gc` retention, uninstall, launchers, the live-session indicator, the split-pane tmux interaction layer (explorer left / active session docked right, F9 switch, F12 fullscreen), new-session creation (`c`), and CI are all shipped. **`SPEC.md` is the authoritative source for architecture, data model, TUI behavior, install layout, and edge-case decisions** — read it before proposing or writing any code. If a change would contradict the spec, update the spec in the same change rather than silently diverging.

## What this project is

`session-explorer` — a Claude Code plugin that turns the JSONL transcripts under `~/.claude/projects/` into a file-explorer-style tree. Single entry point:

- **`/session-explorer:open`** spawns a new terminal window running a Textual TUI. (Plugin commands are namespaced as `<plugin>:<command>`; the prefix is unavoidable.)
- **The TUI is the entire UX**: browse, rename, move between folders, delete, edit notes, resume. No other slash commands.
- **Index sidecar** at `~/.claude/session-explorer-index.json` caches per-session metadata. User-created empty folders and per-project folder paths live in the separate **folder store** at `~/.claude/session-explorer-folders.json`.
- **`SessionStart` hook** records new sessions and refreshes the cache. It **never modifies settings.json**; retention is opt-in (see below).

## Load-bearing design decisions

These are the constraints to preserve — violating any breaks the spec's contract:

- **The user's session name is the only "tag".** The plugin does not maintain a parallel tag field. `/rename` (or `claude -n`) is the single source of truth — both write a `custom-title` event to the JSONL.
- **A live session re-emits its `custom-title` every turn, so "last-title-wins" alone reverts external renames.** After the explorer renames a *running* session, Claude's next re-emit appends the OLD title as the JSONL's last line. `index.set_name` therefore records superseded titles in `name_shadows[]`, and `record_session` ignores a shadowed last-title (keeping the user's `name_cached`). A new, unshadowed title is still adopted, so an in-session `/rename` still flows through. Don't restore naïve last-wins in `record_session`, and route all explorer renames through `index.set_name` (not a bare `name_cached` mutate). See `jsonl.all_custom_titles`.
- **`ai-title` is NOT a name.** Claude emits `ai-title` events automatically as it summarises an active session; those auto-generated titles are intentionally ignored by `jsonl.session_name()`. Only `custom-title` counts. Don't reintroduce the ai-title fallback.
- **"Kept" is implicit — a session is kept iff it has a (custom-title) name.** Don't reintroduce a separate `kept` flag.
- **Unnamed sessions are hidden from the TUI by default.** Press `u` to surface them for renaming or deletion. The index still tracks them (so `--gc` can expire stubs on schedule); only the default view filters them out. Don't conflate "hidden" with "deleted" — these are orthogonal.
- **Worktree sessions group under the parent repo.** A cwd of `<repo>/.claude/worktrees/<name>` derives `project_label` from `<repo>`, not the worktree leaf — otherwise every worktree becomes its own top-level "project". `project_path` keeps the worktree path so resume chdir's into the correct working tree. See `index._project_label`.
- **Worktree cleanup is non-destructive and explorer-owned.** Removal goes through `worktree.remove()` (`bin/_pkg/worktree.py`) using `git worktree remove` *without* `--force` (git refuses dirty/untracked trees — never force it) and never deletes the `worktree-<name>` branch, so a removed worktree is just a "dead" one that `_recreate_worktree` rebuilds on resume. The explorer owns this because Claude only offers native cleanup from the `-w`-creating process, and our retention `cleanupPeriodDays = 36500` disables Claude's age-based sweep. Three triggers share the primitive: `w` (`action_remove_worktree`), the docked-exit offer in `_poll_live` (`_maybe_offer_worktree_cleanup`, once-per-sid via `_offered_cleanup`), and `gc.collect_worktrees` (idle >14d, clean, not live). `collect_worktrees` does NOT mutate the index and may reclaim directories of *kept* sessions — transcript + branch survive, so it's safe. The worktree git/FS primitives live in `worktree.py` so `gc.py` needn't import Textual.
- **Slash splits folder path from display name.** `team/planning/sprint14` → folder path `team/planning`, display `sprint14`. Multiple `/` create nested folders. Dashes are literal characters with no special meaning. Empty segments are dropped.
- **Folder structure lives in `~/.claude/session-explorer-folders.json`, scoped per-project.** Sessions named with `/` auto-add their path to the store on indexing. Pre-created empty folders live there too. The session index file no longer carries a `folders[]` field; a one-shot v1→v2 migration moves any legacy entries under a synthetic `(unfiled)` project.
- **Don't move or rewrite native JSONLs.** Sessions stay where Claude Code wrote them so `/resume` keeps working. The only legitimate write to a JSONL is appending a rename event in the same shape Claude's own `/rename` writes.
- **Retention is opt-in (modifying `cleanupPeriodDays` is the review-sensitive bit).** Neither the hook nor `install.sh` touches settings.json. The TUI asks on first launch (`tui.on_mount` → `retention.enable`/`decline`); only on "yes" is the prior `cleanupPeriodDays` backed up to `~/.claude/.session-explorer.backup` and set to `36500`. The backup's existence is the "retention enabled" signal; a `.session-explorer.retention-declined` marker records a "no" so the prompt isn't re-shown.
- **`--gc` only runs once retention is enabled.** The plugin's `--gc` does retention work, gated on `name_cached IS NULL`; the hook's once-daily auto-trigger is additionally gated on the backup file existing. `uninstall` restores `cleanupPeriodDays` from the backup.
- **Concurrent index writes** (two Claude sessions starting at once). Use `flock` + temp-file-rename for every write.
- **`--gc` skips live sessions.** Skip any JSONL with an active flock or `mtime` within 60s.
- **Hooks never block startup.** `SessionStart` logs failures to `~/.claude/session-explorer.log` and exits 0.
- **TUI runs in a separate terminal**, not inside Claude Code. Claude holds the terminal in raw mode; an interactive TUI must have its own TTY. Slash command spawns it via OS-detected launcher (`osascript` on macOS; `$TERMINAL` / `x-terminal-emulator` / known emulators on Linux).
- **One Python dep: vendored Textual.** Bundled under `bin/_pkg/_vendor/`. No `pip install` runs on either install path. Don't add other deps casually.
- **Don't sum `input_tokens` / `output_tokens` from the JSONL for context-size stats.** Those are streaming-time estimates and have been observed to be order-of-magnitude wrong. Use `cache_read_input_tokens` from the latest assistant message; fall back to `bytes / 4` when caching wasn't active. UI labels the value with `~` to set expectations.
- **No in-place `/compact`.** v1 surfaces context size only; compaction stays a manual `/compact` inside a resumed Claude session. Don't reintroduce SDK-driven compaction without revisiting the spec.
- **Resume is non-destructive when tmux-hosted.** The explorer is the left pane of the `explorer` window and stays alive; the active session docks as the right pane (`join-pane`), while inactive sessions keep running as background windows. F9 switches focus between the two panes (also mouse-click); F12 zooms the focused pane fullscreen. Without tmux it falls back to `execvp`. Don't reintroduce unconditional exit-on-resume, and don't reintroduce the old window-flipping/window-tab model (the explorer tree is the only session switcher).
- **tmux is an optional, consented dependency.** Detect + offer install (declined-marker at `~/.claude/.session-explorer.tmux-declined`), never bundle a binary, never silent-sudo. The dedicated `-L session-explorer` server never touches the user's tmux.
- **Snapshots are read-only.** `capture-pane` for our tmux windows, transcript-tail otherwise. No embedded interactive terminal widget.
- **Sessions persist by default; only an explicit shut-down kills the server (v1.15.0, reverses "Option C").** The generated tmux config has no `client-detached` kill hook, so detaching the client by any means (red-button/`Cmd+W`, crash, or the deliberate `[b]` leave-running quit) leaves the server and all background sessions running; the next `/open` reattaches via `new-session -A`. Only `x → [s]` ("shut down all") runs `kill-server`. The persist-flag mechanism (marker file + `set`/`clear`/`persist_flag_set`) was removed. Don't reintroduce a detach-time kill.
- **A new session's launch failure is captured, not silent.** New-session windows run `exec claude … 2>` a per-sid errfile; ~1.5s after launch `_check_launch` (`tui.py`) verifies the session is alive and, on death (typically `claude -w` failing to create its worktree), surfaces+logs the captured stderr and stamps the index row's `last_launch_error` (shown in the preview). The docked-pane liveness arm verifies the pane actually exists — a claude that docks then dies within the delay window leaves `_docked_sid` as a phantom, which must read as dead (and be cleared), not alive. A transcript-less stub is started fresh on Enter via `--session-id` (`_start_stub_fresh`), never `--resume`. `record_session` clears `last_launch_error` once a transcript appears.
- **`transcript_path` in the index can be dangling — check the disk before writing/resuming.** The hook records the path at SessionStart, but claude creates the file (and its project dir) only on the first message. Rename/move/folder-cascade append the JSONL event only when the file exists (`tui._transcript_on_disk`); otherwise the rename is index-only, and `index.set_name` shadows the *replaced* `name_cached` directly so claude's first write (re-emitting its `-n` title) can't revert it. Enter's stub test is likewise "no messages AND no transcript file on disk" (never `--resume` a dangling path), but a *running* transcript-less session docks instead of restarting. Don't reintroduce `O_CREAT`-ing claude's transcript before claude does — the missing project dir made it crash the TUI mid-callback, and the dead pane let the docked claude swallow the explorer window.
- **Shared-resource queue keys by git-common-dir, not `project_root`.** The
  Phase-1 lease engine (`queue-run`/`queue-status`/`queue-cancel`,
  `bin/_pkg/queue_*.py` + `project_id.py` + `qsync.py` + `exclusive.py` +
  `probes.py`) identifies a project by `project_id.project_id(cwd)` (a hash of
  `git rev-parse --git-common-dir`), so every worktree of a repo collapses to
  one queue identity — `index.project_root()` (a `/.claude/worktrees/`
  string-strip) is NOT used for queues. The queue is the set of ticket files;
  the holder is the lowest-numbered ticket whose owner still holds its lifetime
  `fcntl.flock` (no daemon, crash-safe). The `sync` acquire strategy runs
  `rsync -a --delete` with anchored `--filter=exclude` rules and refuses the
  first sandbox transition until untracked/ignored would-delete paths are
  classified into `protect`/`allow_delete`. Never reintroduce `--delete-excluded`
  or rsync `P`/`protect` rules. See `SPEC.md` → "Shared-resource lease engine".
- **Phase-3 enforcement is location-based (fail-closed semantics, fail-open plumbing).** For a Claude session in a worktree, the shared installed root is unreachable through tools (`Bash|Edit|Write|NotebookEdit`) except via a single `session-explorer queue-*` command. Decision logic lives in `bin/_pkg/root_guard.py` (pure, Textual-free, unit-tested); `hooks/pre-tool-use.sh` → `queue-guard` is the hook shell, unchanged. Plumbing fails open (a broken hook emits nothing and exits 0); within working plumbing a root mention from a worktree session is denied with the exact `queue-run` rewrite. The **matcher widens** from `Bash` to `Bash|Edit|Write|NotebookEdit` in `.claude-plugin/plugin.json` **and** `install.sh`, torn down in `uninstall.py` (`_HOOK_MARKERS`/`_HOOK_EVENTS`) — keep all three in sync. SessionStart awareness (`queue_awareness._render_context`) shrinks to a ≤6-line usage hint. Deleted: `guard_match.py` (`{exe, sub}` guard vocabulary), `queue_detect.py` (mtime out-of-lease toast). Per-project setup is a parameterless `s` toggle (v1.17.1): no dialog — `action_resource_setup` pushes a `ConfirmScreen` that either stops sharing (current overlay resource) or enables/migrates one (writing the overlay shape under the existing id or `root`). The Protect box was removed (it only fed the engine's `sync` strategy, which is not a UI surface) and with it the `ctrl+d`-swallowing `TextArea`; `SharedRootScreen`/`QueueHelpScreen`/`_queue_help_text`/`parse_path_lines` are deleted. Experimental claim: enforced for Claude tool calls, advisory beyond them.

## Commands

- **Tests (Python):** `python3 -m pytest test/ -q`. Single file: `python3 -m pytest test/test_gc.py -q`. Single test: `python3 -m pytest test/test_gc.py::test_old_unnamed_session_is_deleted -q`. Config in `pytest.ini` (`asyncio_mode = auto`); dev deps in `test/requirements-dev.txt` (pytest + pytest-asyncio). Textual is vendored, so nothing else needs installing.
- **Tests (shell):** `bats test/install.bats test/uninstall.bats test/hook.bats` — shell-level coverage of `install.sh`, `uninstall.sh`, and the hook. (CLI subcommands are covered by pytest via subprocess in `test_cli.py`, so bats deliberately doesn't re-test them.)
- **CI:** `.github/workflows/ci.yml` runs both suites on ubuntu + macos across Python 3.11–3.13 for every push/PR to `main`.
- **Install (dev, plain path):** `./install.sh` registers the hook in `~/.claude/settings.json` and symlinks `bin/session-explorer` to `~/.local/bin/`. It does NOT touch `cleanupPeriodDays` (retention is opt-in via the TUI prompt).
- **Install (marketplace path):** `/plugin marketplace add <this-repo>` then `/plugin install session-explorer`. The plugin's `bin/` is on the Bash-tool PATH automatically.
- **Uninstall:** `./uninstall.sh` (plain) or `session-explorer uninstall` (marketplace, since `/plugin uninstall` has no teardown hook). Both restore `cleanupPeriodDays` from the backup.
- **Releasing:** every iteration of changes that ships needs a version bump and a GitHub release. **Follow the `cutting-a-release` skill (`.claude/skills/cutting-a-release/SKILL.md`) — it is the authoritative checklist** (bump `__init__.py` + `plugin.json`, update README/SPEC status lines and the help-screen keybindings if they changed, add a `CHANGELOG.md` section, then `gh release create vX.Y.Z`). Don't ship version-affecting work without running through it.

## Implementation order

All milestones (M1–M7) are shipped as of v1.5.0; the `SPEC.md` milestone table records what each delivered. For new work, keep `SPEC.md` authoritative — update it in the same change rather than letting code and spec diverge.

## Resolved design decisions

Previously open; settled during implementation. See `SPEC.md` → "Design decisions (resolved)" for the full log.

- **Claude's `/rename` JSONL format** was reverse-engineered from a real transcript: a `custom-title` event. The index falls back to a `display_name` override only if that format ever proves volatile.
- **Preview pane content** is settled: name, project, folder, branch, age, created date, message count, context size, session id, notes, first prompt, and transcript path (the never-populated `summary` block was dropped).

Two items remain deliberately deferred (see `SPEC.md`): in-place `/compact` and empty-folder pruning.
