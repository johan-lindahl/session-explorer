# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Pre-implementation. The repo currently contains only `SPEC.md` (no commits, no code). **`SPEC.md` is the authoritative source for architecture, data model, TUI behavior, install layout, and edge-case decisions** — read it before proposing or writing any code. If a change would contradict the spec, update the spec in the same change rather than silently diverging.

## What this project is

`session-explorer` — a Claude Code plugin that turns the JSONL transcripts under `~/.claude/projects/` into a file-explorer-style tree. Single entry point:

- **`/session-explorer:open`** spawns a new terminal window running a Textual TUI. (Plugin commands are namespaced as `<plugin>:<command>`; the prefix is unavoidable.)
- **The TUI is the entire UX**: browse, rename, move between folders, delete, edit notes, resume. No other slash commands.
- **Index sidecar** at `~/.claude/session-explorer-index.json` caches per-session metadata + tracks user-created empty folders.
- **`SessionStart` hook** records new sessions, refreshes the cache, and does idempotent first-run setup (neutralising `cleanupPeriodDays`).

## Load-bearing design decisions

These are the constraints to preserve — violating any breaks the spec's contract:

- **The user's session name is the only "tag".** The plugin does not maintain a parallel tag field. `/rename` (or `claude -n`) is the single source of truth — both write a `custom-title` event to the JSONL.
- **`ai-title` is NOT a name.** Claude emits `ai-title` events automatically as it summarises an active session; those auto-generated titles are intentionally ignored by `jsonl.session_name()`. Only `custom-title` counts. Don't reintroduce the ai-title fallback.
- **"Kept" is implicit — a session is kept iff it has a (custom-title) name.** Don't reintroduce a separate `kept` flag.
- **Unnamed sessions are hidden from the TUI by default.** Press `u` to surface them for renaming or deletion. The index still tracks them (so `--gc` can expire stubs on schedule); only the default view filters them out. Don't conflate "hidden" with "deleted" — these are orthogonal.
- **Worktree sessions group under the parent repo.** A cwd of `<repo>/.claude/worktrees/<name>` derives `project_label` from `<repo>`, not the worktree leaf — otherwise every worktree becomes its own top-level "project". `project_path` keeps the worktree path so resume chdir's into the correct working tree. See `index._project_label`.
- **First-dash splits folder from name.** `planning-sprint14` → folder `planning`, display name `sprint14`. Dashes after the first stay in the name. Single-level folders only.
- **Don't move or rewrite native JSONLs.** Sessions stay where Claude Code wrote them so `/resume` keeps working. The only legitimate write to a JSONL is appending a rename event in the same shape Claude's own `/rename` writes.
- **Native cleanup is neutralised by setting `cleanupPeriodDays: 36500`.** The plugin's `--gc` does retention work, gated on `name_cached IS NULL`. Back up the prior value to `~/.claude/.session-explorer.backup` so uninstall can restore it.
- **First-run setup lives in the `SessionStart` hook**, guarded by the backup file's existence, so marketplace installs work without an installer step. The plain `install.sh` does the same step eagerly.
- **Concurrent index writes** (two Claude sessions starting at once). Use `flock` + temp-file-rename for every write.
- **`--gc` skips live sessions.** Skip any JSONL with an active flock or `mtime` within 60s.
- **Hooks never block startup.** `SessionStart` logs failures to `~/.claude/session-explorer.log` and exits 0.
- **TUI runs in a separate terminal**, not inside Claude Code. Claude holds the terminal in raw mode; an interactive TUI must have its own TTY. Slash command spawns it via OS-detected launcher (`osascript` on macOS; `$TERMINAL` / `x-terminal-emulator` / known emulators on Linux).
- **One Python dep: vendored Textual.** Bundled under `bin/_pkg/_vendor/`. No `pip install` runs on either install path. Don't add other deps casually.
- **Don't sum `input_tokens` / `output_tokens` from the JSONL for context-size stats.** Those are streaming-time estimates and have been observed to be order-of-magnitude wrong. Use `cache_read_input_tokens` from the latest assistant message; fall back to `bytes / 4` when caching wasn't active. UI labels the value with `~` to set expectations.
- **No in-place `/compact`.** v1 surfaces context size only; compaction stays a manual `/compact` inside a resumed Claude session. Don't reintroduce SDK-driven compaction without revisiting the spec.

## Commands

No build/test tooling exists yet. Planned (per spec):

- **Tests:** `bats test/` for the shell-facing CLI, `pytest test/` for index/jsonl/TUI logic.
- **Install (dev, plain path):** `./install.sh` writes the hook to `~/.claude/settings.json`, symlinks `bin/session-explorer` to `~/.local/bin/`, performs first-run setup eagerly.
- **Install (marketplace path):** `/plugin marketplace add <this-repo>` then `/plugin install session-explorer`. The plugin's `bin/` is on the Bash-tool PATH automatically.
- **Uninstall:** `./uninstall.sh` (plain) or `session-explorer uninstall` (marketplace, since `/plugin uninstall` has no teardown hook). Both restore `cleanupPeriodDays` from the backup.

Fill in concrete single-test invocations here once the test suites land.

## Implementation order

Follow the milestones in `SPEC.md` (M1 → M5). M1 ships the hook + manifest + index core + macOS launcher; M2 lands the Textual TUI; M3 adds `--gc` + uninstall + search; M4 tests + CI; M5 community-marketplace submission + Linux/Windows launchers.

## Open questions that affect implementation

These are unresolved in the spec. Don't silently pick a side — verify or confirm with the user when a task forces the choice:

- **Claude's `/rename` JSONL format.** Decided during M1 by inspecting a real renamed transcript. If the format proves volatile, fall back to a `display_name` override field in the index.
- **Preview pane content.** Notes + first prompt + summary + full path. Revisit in M2 once the layout is real.
