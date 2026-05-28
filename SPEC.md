# session-explorer

A Claude Code plugin that turns the JSONL transcripts under `~/.claude/projects/` into a file-explorer-style tree: browse, organize, rename, move, delete, and resume sessions from a single TUI launched by one slash command.

## Goals

1. **Claude's name is the only metadata that matters.** `/rename <name>` (or `claude -n <name>` at startup) is the single source of truth for both the session's identity and its folder. The plugin never maintains a parallel "tag" field.
2. **A name means "keep".** Any named session is preserved past Claude Code's native 30-day cleanup. Unnamed sessions remain subject to expiry. One concept, not two.
3. **One slash command — `/session-explorer:open`** — opens a TUI in a new terminal window. Browsing, organizing, renaming, deleting, resuming: all happen there. (Claude Code namespaces plugin commands as `<plugin>:<command>`; the prefix is unavoidable.)
4. **Folders come for free, from the name.** `planning/sprint14` lives in folder `planning` as session `sprint14`. Slash-separated paths create nested folders of any depth. Dashes are literal characters with no special meaning.
5. **Install once via a Claude Code marketplace.** Active across every project the user opens Claude Code in. Optional `install.sh` for users not on the marketplace.
6. **Surface context size at a glance.** Every row in the explorer shows an approximate token count and message count, so bloated sessions are obvious before you resume.

## Non-goals

- Not a sync / cloud backup tool. Local-machine only.
- Not a session editor — the only writes to a JSONL are rename events in the same shape Claude's own `/rename` writes.
- Not a replacement for Claude Code's native `/resume`. It augments it; the native picker keeps working.
- No web UI. Terminal only.
- No in-place `/compact` in v1. The explorer surfaces context size but doesn't drive compaction; running `/compact` stays a manual step inside a resumed session. Revisit once Claude Code exposes a non-interactive compaction CLI or stable SDK affordance.

## Background: Claude Code's session surface

| Aspect | Reality |
|---|---|
| Storage | Plain JSONL: `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`, one message per line. |
| Auto-cleanup | Global `cleanupPeriodDays` in `~/.claude/settings.json`. Default 30 days. Not per-session. |
| Naming | `claude -n <name>` at startup; `/rename <name>` in-session. Persisted inside the JSONL stream. |
| Resume | `claude --resume <uuid>` continues a session in the current terminal. |
| Plugin surface | `.claude-plugin/plugin.json` declares slash commands and hooks; `bin/` is auto-added to the Bash-tool PATH while the plugin is enabled. |
| Slash command TUIs | Not possible — Claude Code holds the terminal in raw mode. Interactive TUIs must run in a separate terminal window. |

## Architecture

```
┌─ ~/.claude/ ──────────────────────────────────────────────────────┐
│  settings.json                                                    │
│     cleanupPeriodDays = 36500   # neutralise native cleanup       │
│                                                                   │
│  projects/<encoded-cwd>/<uuid>.jsonl   ← unchanged (native)       │
│                                                                   │
│  session-explorer-index.json   ← OWNED BY THIS PLUGIN             │
│     { version: 2, sessions: { <uuid>: { ... } } }                 │
│                                                                   │
│  session-explorer-folders.json ← folder store                     │
│     { version: 1, projects: { <label>: [...paths] } }             │
│                                                                   │
│  .session-explorer.backup    ← prior cleanupPeriodDays            │
│  .session-explorer.current   ← active session_id pointer          │
│  .session-explorer.help-seen ← set after first-launch help shown  │
│                                                                   │
│  plugins/.../session-explorer/                                    │
│     ├── .claude-plugin/plugin.json                                │
│     ├── marketplace.json                                          │
│     ├── bin/session-explorer       ← single CLI entry             │
│     ├── bin/_pkg/                  ← Python package + vendored    │
│     │                                Textual                      │
│     ├── hooks/session-start.sh                                    │
│     └── commands/open.md                                          │
└───────────────────────────────────────────────────────────────────┘
```

**Key idea — derive, don't store.** The plugin caches metadata for browse-speed but treats the JSONL as authoritative. Folder/name parsing happens at render time from the session's user-assigned name (slash-separated). "Kept" is `name != null`.

**What counts as a name.** Only `/rename` and `claude -n <name>` count — both write a `custom-title` event to the JSONL. The `ai-title` events that Claude emits automatically as a session evolves (refining its own descriptive summary) **do NOT** make a session "named" in this plugin's sense. The session-explorer treats those auto-generated titles as if they didn't exist, so the index's `name_cached` is populated only by explicit user intent.

## Naming and folders

The session's Claude-assigned name encodes folder path + display name via `/`:

```
<segment>/<segment>/…/<display>   → all but the last segment → folder path; last → display name
<just-a-name>  (no /)             → at project root; display = name
(no name)                         → hidden by default; toggle with [u] to surface for renaming or deletion
```

Empty segments (from `foo//bar`, leading/trailing `/`, or whitespace-only
segments) are dropped during parsing. Dashes have no special meaning —
`bugfix-watch-lockup` displays as one name at the project root.

| Session name | Folder path | Display name |
|---|---|---|
| `planning/sprint14` | `planning` | `sprint14` |
| `audits/q1-review` | `audits` | `q1-review` |
| `team/planning/q1` | `team/planning` | `q1` |
| `sprint14` | *(none)* | `sprint14` |

**Empty folders** created in the TUI before any session is moved in are stored in the folder store (see below). They render alongside populated folders, persisting between sessions.

## The TUI

Built on **Textual** (Python). Launched in a new terminal window by the `/session-explorer:open` slash command. Single tree view:

```
session-explorer · 32 sessions across 6 projects · 15 unnamed hidden (u)               / filter

▼ acme-web (3)
    planning/
      sprint14            main         2h    ~38K  (19%)    47 msgs   audit modules…
    audits/
      q1-review           feature/…    5d    ~127K (64%)   152 msgs   grant audit
▼ acme-api (8)
    team/
      planning/
        q1                feat/x       1d    ~12K   (6%)    18 msgs   helper extraction
▶ session-explorer (4)
```

Outer level: project (`project_label`, auto-grouped from cwd; git worktrees under `<repo>/.claude/worktrees/<name>` collapse into the parent repo so a project's worktrees don't each become a top-level entry). Inner level: `/`-separated folder paths parsed from session names, rendered as a nested tree of any depth. Pre-created empty folders live in the folder store file (see *Folder store* below). **Unnamed sessions are hidden by default** — only "kept" sessions (those with a Claude-assigned name) appear in the default view, mirroring the spec's "kept ⇔ named" rule and cutting the visual noise from stub records (sessions started but never used). Press `u` to surface unnamed sessions when you need to rename or delete them; they then appear under an `(unnamed)` sub-group per project. The header advertises the hidden count.

### Keybindings

| Key | Action |
|---|---|
| `↑` `↓` | Move between rows |
| `←` `→` | Collapse / expand the current folder or project |
| `Enter` | Resume the selected session — see *Resume flow* |
| `Space` | Toggle the preview pane. Headline is the session's full (un-truncated) name; body shows project, folder, branch, age, created date, message count, context size, session id, notes, first prompt, and transcript path. `Esc` also closes it. |
| `r` | Rename (= retag = move to a different folder). Prompts for the new name. |
| `n` | New folder (prompts for path under the current project; cursor on a folder pre-fills the prefix). Created empty; persisted in the folder store. |
| `m` | Move the selected session within its project (lists existing paths in the project; type a new path to create it). |
| `d` | Delete the selected session (confirms). Removes the JSONL **and** the index entry. |
| `e` | Edit notes for the selected session (opens `$EDITOR` or an inline multi-line input). |
| `u` | Toggle visibility of unnamed sessions (hidden by default). |
| `/` | Live filter across name, notes, first prompt, summary. |
| `h` | Show the help overlay (slash-folder naming, the named-only default + `u`, full key list, author credit). Auto-opens once on first launch, then only on demand. |
| `Esc` | Close the preview pane, the help overlay, or clear an active filter. Does **not** quit. |
| `q` | Quit. |

**Folder deletion** is intentionally not bound. Empty folders disappear when removed from the folder store (achievable by moving a session out and back); populated folders cease to exist when their last session is moved or deleted. v1 does not support "delete folder and everything in it" — too easy to lose work.

### Stats columns

Each session row shows:

- **Age** since `last_active_at` (relative).
- **Approx. tokens.** Derived from `cache_read_input_tokens` of the latest assistant message in the JSONL — accurate when caching is active. Falls back to `bytes / 4` when the session has no cached turns (early sessions, cache disabled). Always prefixed with `~` in the UI to signal it's an estimate.
- **Context-window %.** v1 hardcodes a 200K denominator (Sonnet 4.6 default). Per-message model id isn't recorded in the JSONL, so model-aware sizing is an M2/M3 follow-up.
- **Message count** (`wc -l` on the JSONL; always exact).
- **First-prompt tail** (truncated; full text in the preview pane).

These are pure caches in the index. The `SessionStart` hook refreshes them on every fire; `session-explorer index --refresh` recomputes them on demand.

> **Why not sum `input_tokens` / `output_tokens`?** Claude Code's per-message token counts are streaming-time estimates and have been observed to be off by an order of magnitude in community reports. `cache_read_input_tokens` is logged after the API response and is reliable for sessions that use caching (the vast majority).

### Rename and move

Both write a rename event to the session's JSONL in the same shape Claude's own `/rename` writes. **M1 task:** reverse-engineer Claude's rename serialization from a real JSONL.

**Fallback** if Claude's format proves volatile or undocumented: store an authoritative `display_name` in the index that overrides whatever the JSONL says. v1 prefers writing to the JSONL so Claude's native picker reflects the new name.

### Resume flow

`Enter` on a session causes the TUI to `chdir(session.project_path)` and then `exec claude --resume <id>` in the same (spawned) terminal window. The chdir is essential — Claude Code keys projects on cwd, so resuming without it lands the user in the spawning terminal's cwd (usually `$HOME`) and triggers a fresh "trust this folder" prompt instead of restoring the session. If `project_path` no longer exists on disk, the chdir is skipped and Claude opens in whatever cwd it inherits. The TUI process exits and Claude takes over the window. The original Claude session you typed `/session-explorer:open` from keeps running in its other window — two parallel Claude sessions is the expected outcome.

## The slash command

Just one — `/session-explorer:open`. The markdown command (`commands/open.md`) shells out to a small launcher script that:

1. Detects the OS (`uname -s`).
2. Picks a terminal launcher (see next section).
3. Spawns the `session-explorer` CLI in a new window.
4. Returns to Claude immediately (fire-and-forget; no output to wait on).

**Note on `CLAUDE_PLUGIN_ROOT`:** the env var is set for `plugin.json` hook commands, MCP/LSP `command` strings, and monitor scripts — but **not** for slash-command shell blocks (the `` !`...` `` syntax in markdown). The slash command therefore can't reference `$CLAUDE_PLUGIN_ROOT/bin/session-explorer` directly; it locates the binary by searching `~/.local/bin/session-explorer` (plain installer) and `~/.claude/plugins/cache/*/session-explorer/*/bin/session-explorer` (marketplace install). The hook script and bundled CLI scripts still use `$CLAUDE_PLUGIN_ROOT` where appropriate.

If no terminal launcher succeeds, the slash command prints the absolute command and copies it to the clipboard (`pbcopy` on macOS, `xclip -selection clipboard` on Linux). The user can then paste into any terminal.

## Terminal launcher

Auto-detection logic, first match wins:

- **macOS** — `osascript -e 'tell application "Terminal" to do script "<absolute path>"'`. Uses the user's default Terminal.app profile; window title set to "session-explorer".
- **Linux** — probe in this order:
  1. `$TERMINAL` env var
  2. `x-terminal-emulator` (Debian/Ubuntu meta)
  3. `gnome-terminal`, `konsole`, `xfce4-terminal`, `alacritty`, `kitty`, `wezterm`
- **Windows / WSL** — out of scope for v1; falls through to "print + clipboard".

In v1, **macOS is the first-class target**. Linux launchers ship in M2 once dogfooding has shaken out the Textual UX. Windows / WSL lands in M5.

## Data model — `~/.claude/session-explorer-index.json`

```jsonc
{
  "version": 2,
  "sessions": {
    "01HXYZ…uuid": {
      "name_cached": "planning/sprint14",       // last-seen Claude name; JSONL is authoritative
      "notes": "production audit of billing modules\nfollow-up Q1",
      "project_path": "/Users/you/code/acme-api",  // cwd; resume chdir's here
      "project_label": "acme-api",                  // grouping key; worktrees collapse to the parent repo
      "branch": "feature/checkout-revamp",
      "first_prompt": "audit which billing modules have zero production data",
      "summary": "…",                          // from /summary if available
      "created_at": "2026-05-26T14:12:00Z",
      "last_active_at": "2026-05-26T15:48:00Z",
      "message_count": 47,
      "bytes": 481203,                          // JSONL file size
      "tokens_estimate": 38234,                 // from cache_read_input_tokens, fallback bytes/4
      "tokens_window_pct": 19                   // assumes 200K denominator (v1)
    }
  }
}
```

`name_cached`, `last_active_at`, `message_count` are pure perf caches — refreshed by the hook on session start and by `session-explorer index --refresh` on demand. **No `tag` field. No `kept` field. No `folders` field.** "Kept" is `name_cached != null`. Folder data lives in the separate folder store below.

### Folder store — `~/.claude/session-explorer-folders.json`

Per-project flat list of folder paths. Path strings use `/` as separator.
Intermediate folders are implicit (storing `planning/sprint14` implies
`planning` exists in the rendered tree).

```jsonc
{
  "version": 1,
  "projects": {
    "acme-api": ["planning", "planning/sprint14", "bugfix"],
    "acme-app": ["watch", "watch/v2"],
    "(unfiled)": ["legacy-shelf"]                 // populated by v1→v2 migration only
  }
}
```

Atomic writes via the same flock + temp-file-rename pattern as the index.
Migration from v1 (with `index.folders[]`) is one-shot, idempotent, and runs
at every CLI entry point.

**`session-explorer index --backfill`** populates the index from every JSONL under `~/.claude/projects/` that isn't already tracked. Pre-install sessions don't fire the `SessionStart` hook, so without backfill they'd be invisible. Backfill recovers `cwd` per session from the JSONL's envelope lines (via `jsonl.session_cwd()`) since the hook payload isn't available retrospectively. Existing entries are left untouched — backfill is additive; use `--refresh` to recompute caches for already-tracked sessions. Safe to re-run.

## Hooks

One `SessionStart` hook, declared in `plugin.json`:

```jsonc
{
  "name": "session-explorer",
  "version": "0.1.0",
  "description": "File-explorer-style session management for Claude Code",
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/session-start.sh" }
        ]
      }
    ]
  }
}
```

(`commands/` is auto-discovered; no need to declare it. The env var Claude Code
sets is `CLAUDE_PLUGIN_ROOT`, not `CLAUDE_PLUGIN_DIR`.)

The hook (bash) reads stdin (`session_id`, `transcript_path`, `cwd`, `source`) and:

1. **First-run setup (idempotent).** If `~/.claude/.session-explorer.backup` is absent: back up the current `cleanupPeriodDays`, then set it to `36500`. Every subsequent fire short-circuits.
2. Adds a row to `session-explorer-index.json` if missing (with `project_path`, `project_label`, branch, `first_prompt` from the JSONL's first user message).
3. Re-reads the JSONL for `name_cached`, `last_active_at`, `message_count`.
4. Writes the active `session_id` to `~/.claude/.session-explorer.current` for any future feature that wants to address "the current session" from outside Claude.

The hook never blocks startup; failures log to `~/.claude/session-explorer.log` and exit 0.

## Disabling native auto-cleanup

`cleanupPeriodDays` in `~/.claude/settings.json` is set to `36500` (100 years) so Claude's expiry never touches user sessions. The plugin's `session-explorer index --gc` does deletion instead:

```
deletion criteria:
  name_cached IS NULL
  AND last_active_at older than <retention-days>  (default 30)
  AND no active flock on the JSONL
  AND JSONL mtime older than 60 seconds
```

Who writes the `36500`:

- **Marketplace install** — `SessionStart` hook's first-run step.
- **Plain `install.sh`** — eagerly, so the first hook fire is a no-op.

`~/.claude/.session-explorer.backup` holds the prior value so uninstall can restore it. `--gc` runs manually or via a user-configured cron / launchd job.

## Installation

**Primary path: Claude Code plugin via marketplace.**

```bash
/plugin marketplace add <owner>/session-explorer
/plugin install session-explorer
```

`bin/session-explorer` is on the Bash-tool PATH automatically. Slash commands and hooks reference `$CLAUDE_PLUGIN_ROOT`. **No manual config edits.**

**Distribution channels:**

- **Self-hosted marketplace** — the repo's own `.claude-plugin/marketplace.json`. Users register it with `/plugin marketplace add <owner>/<repo>`. No review. Recommended for v1 while iterating.
- **Community marketplace** (`anthropics/claude-plugins-community`) — submit via [claude.ai/settings/plugins/submit](https://claude.ai/settings/plugins/submit) once stable. Automated safety screening + manual review. Pinned to a commit SHA.
- **Official marketplace** — out of scope for v1.

**Secondary path: plain `install.sh`** for users not on the marketplace flow. `git clone` + `./install.sh` writes the hook into `~/.claude/settings.json`, symlinks `bin/session-explorer` to `~/.local/bin/`, and eagerly performs the first-run setup.

**Marketplace-specific constraints:**

- No installer runs on `/plugin install`. All state changes happen on the first `SessionStart` hook fire, guarded by `~/.claude/.session-explorer.backup`.
- `/plugin uninstall` has no teardown hook. Restoring `cleanupPeriodDays` is a documented one-liner and a `session-explorer uninstall` CLI subcommand.
- Plugin updates land via `/plugin update`. The plugin tolerates being upgraded between two session starts.

## File-level layout (repository)

```
session-explorer/
├── README.md
├── SPEC.md                               ← this file
├── .claude-plugin/
│   └── plugin.json                       ← marketplace manifest
├── marketplace.json                      ← repo IS its own marketplace
├── bin/
│   ├── session-explorer                  ← entry point; on PATH automatically
│   └── _pkg/                             ← Python package internals
│       ├── __init__.py
│       ├── cli.py                        ← argparse + subcommands
│       ├── index.py                      ← read/write + flock
│       ├── jsonl.py                      ← read names, prompts, message counts
│       ├── tui.py                        ← Textual app
│       ├── launcher.py                   ← OS-specific terminal spawning
│       └── _vendor/                      ← bundled Textual + transitive deps
├── hooks/
│   └── session-start.sh
├── commands/
│   └── open.md                           ← the one slash command (/session-explorer:open)
├── install.sh                            ← secondary install path
├── uninstall.sh
└── test/
    ├── fixtures/                         ← sample JSONLs + indexes
    ├── *.bats                            ← shell-level CLI tests
    └── *_test.py                         ← pytest for index/jsonl/tui logic
```

## Implementation language and dependencies

- **Bash** for the hook (tiny, stdlib-only).
- **Python 3.11+** for the CLI and TUI.
- **One Python dependency: `Textual`.** Bundled vendored under `bin/_pkg/_vendor/` so the user's Python environment is never modified. No `pip install` step on either install path.

The earlier spec's "stdlib only" promise is **dropped**: replacing fzf with a real custom TUI requires a real TUI framework, and curses is too low-level for the file-explorer UX (tree widget, prompts, dialogs, focus model). Textual is the smallest dep that delivers the UX without bringing the rest of an ecosystem.

## Edge cases

1. **Session running while `--gc` runs.** Skip any JSONL with an active flock or `mtime` within the last 60 seconds.
2. **JSONL deleted out-of-band** (user `rm`s it). `--gc` and the TUI both prune index rows whose JSONL no longer exists; log a warning.
3. **Two machines, one `~/.claude/`** (via dotfile sync). The index is keyed by session UUID; naïve last-writer-wins on the JSON file loses at most a notes edit. Not solved in v1.
4. **`/rename` inside Claude Code.** The next `SessionStart` hook fire refreshes `name_cached`. Mid-session renames are visible after closing/reopening the TUI or running `session-explorer index --refresh`.
5. **Concurrent index writes** (two `claude` sessions starting at once). Every index write uses `flock` + temp-file-rename.
6. **Folder collisions.** Renaming `plans/sprint14` to `plans/sprint15` moves the session within the folder. Renaming to `sprint14` (no slash) drops it to ungrouped. Deleting a session leaves its folder behind only if the folder also appears in the folder store; otherwise the folder evaporates with the last session.
7. **Empty-folder accumulation.** `--gc` also prunes entries from the folder store that have remained empty for >90 days.
8. **Launcher fallback.** No terminal detected → CLI prints the absolute command + copies to clipboard; the slash command's response shows "Run: …".
9. **Plugin upgrade between session starts.** Hook may be a newer version than the index format. A fresh install creates the index at `version: 1` (no `folders[]` since the field is never written to a new file); the one-shot v1→v2 migration runs at every CLI entry point and bumps `version` to `2` (moving any legacy `folders[]` to the folder store under `(unfiled)`). The migration is idempotent — once `version >= 2`, it short-circuits. Readers tolerate either version.
10. **Token estimate accuracy.** Per-message `input_tokens` / `output_tokens` in the JSONL are streaming-time estimates and can be order-of-magnitude wrong. Use `cache_read_input_tokens` from the latest assistant message; fall back to `bytes / 4` when caching wasn't active. UI labels the value with `~` so users know it's approximate.

## Milestones

| M | Scope |
|---|---|
| M0 | Spec lands. (This file.) |
| M1 | Plugin manifest + `marketplace.json` + `SessionStart` hook with first-run setup + index core (`record`, `refresh`, `list`). Installable from a self-hosted marketplace. macOS terminal launcher. Reverse-engineer `/rename` JSONL format. |
| M2 | Textual TUI: tree view, all keybindings, rename/move/delete/notes, preview pane, **context-size stats columns**. Linux launchers. |
| M3 | `--gc` (sessions + empty folders); `session-explorer uninstall`; search across notes/prompts/summaries. |
| M4 | bats + pytest suites; CI; README quickstart with both install paths. |
| M5 | Submit to `anthropics/claude-plugins-community`. Windows / WSL launcher. |

## Open questions

- **Claude's `/rename` JSONL format.** Decided during M1 by inspecting a real renamed transcript. Fallback: `display_name` override field in the index.
- **Exact `message.usage` field path.** v1 reads `cache_read_input_tokens` from the latest assistant message; the precise JSON path is confirmed during M1 by inspecting a real transcript.
- **Model-aware context window.** v1 hardcodes a 200K denominator. Per-message model id isn't in the JSONL; we'd need to read the session's startup config or Claude Code settings to pick the right window (200K Sonnet vs 1M Opus 1M-context vs 200K Haiku). Revisit in M2/M3 if the hardcode misleads Opus/Haiku users.
- **In-place compaction.** Deferred past v1 (see Non-goals). Reconsider once Claude Code ships a `claude --compact <id>` flag or a stable Agent SDK pattern for one-shot non-interactive compaction.
- **Preview-pane content.** Resolved in M2 dogfooding: headline is the full display name (the grid truncates it), followed by project, folder, branch, age, created date, message count, context size, session id, notes, first prompt, and transcript path. The `summary` block was dropped — the field is never populated today. May still add "last assistant message" later if the layout has room.
- **`session-explorer browse` as a standalone shell command.** Removed from this spec — the TUI is only reachable via the slash command's launcher. Easy to re-add as a thin CLI wrapper in M3 if users ask. Decision: ship without it; let usage tell us if it's needed.
