# session-explorer

A Claude Code plugin that turns the JSONL transcripts under `~/.claude/projects/` into a file-explorer-style tree: browse, organize, rename, move, delete, and resume sessions from a single TUI launched by one slash command.

## Goals

1. **Claude's name is the only metadata that matters.** `/rename <name>` (or `claude -n <name>` at startup) is the single source of truth for both the session's identity and its folder. The plugin never maintains a parallel "tag" field.
2. **A name means "keep".** Any named session is preserved past Claude Code's native 30-day cleanup. Unnamed sessions remain subject to expiry. One concept, not two.
3. **One slash command — `/session-explorer`** — opens a TUI in a new terminal window. Browsing, organizing, renaming, deleting, resuming: all happen there.
4. **Folders come for free, from the name.** `planning-sprint14` lives in folder `planning` as session `sprint14`. The first dash separates folder from name; the rest stays in the name. Single-level by design.
5. **Install once via a Claude Code marketplace.** Active across every project the user opens Claude Code in. Optional `install.sh` for users not on the marketplace.

## Non-goals

- Not a sync / cloud backup tool. Local-machine only.
- Not a session editor — the only writes to a JSONL are rename events in the same shape Claude's own `/rename` writes.
- Not a replacement for Claude Code's native `/resume`. It augments it; the native picker keeps working.
- No web UI. Terminal only.
- No multi-level folder hierarchies. One level is enough; deeper structure goes back to "use longer names".

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
│     { folders: [...], sessions: { <uuid>: { ... } } }             │
│                                                                   │
│  .session-explorer.backup    ← prior cleanupPeriodDays            │
│  .session-explorer.current   ← active session_id pointer          │
│                                                                   │
│  plugins/.../session-explorer/                                    │
│     ├── .claude-plugin/plugin.json                                │
│     ├── marketplace.json                                          │
│     ├── bin/session-explorer       ← single CLI entry             │
│     ├── bin/_pkg/                  ← Python package + vendored    │
│     │                                Textual                      │
│     ├── hooks/session-start.sh                                    │
│     └── commands/session-explorer.md                              │
└───────────────────────────────────────────────────────────────────┘
```

**Key idea — derive, don't store.** The plugin caches metadata for browse-speed but treats the JSONL as authoritative. Folder/name parsing happens at render time from the session's Claude-assigned name. "Kept" is `name != null`.

## Naming and folders

The session's Claude-assigned name encodes both folder and name:

```
<folder>-<rest-of-name>   → goes into folder; displayed as <rest-of-name>
<just-a-name>             → ungrouped within its project
(no name)                 → ungrouped within an "(unnamed)" sub-group
```

Only the **first** dash is the separator. Dashes after the first stay in the name.

| Session name | Folder | Display name |
|---|---|---|
| `planning-sprint14` | `planning` | `sprint14` |
| `audits-q1-review` | `audits` | `q1-review` |
| `release-2026-05` | `release` | `2026-05` |
| `sprint14` | *(none)* | `sprint14` |

**Empty folders** created in the TUI before any session is moved in are stored in the index's `folders` array. They render alongside populated folders, persisting between sessions.

## The TUI

Built on **Textual** (Python). Launched in a new terminal window by the `/session-explorer` slash command. Single tree view:

```
session-explorer · 47 sessions across 6 projects             / filter

▼ acme-web (3)
    planning/
      sprint14            main          · 2h ago  · audit AC mods…
    audits/
      q1-review    feature/...   · 5d ago  · grant audit
▼ acme-api (8)
    refactors/
      checkout-cleanup    feat/x        · 1d ago  · helper extraction
▼ (unnamed) (15)
      cd0fc4              main          · 3d ago  · brainstorm naming
▶ session-explorer (4)
```

Outer level: project (`project_label`, auto-grouped from cwd). Inner level: folders parsed from session names. Unnamed sessions land in an `(unnamed)` sub-group within their project so they remain discoverable for renaming.

### Keybindings

| Key | Action |
|---|---|
| `↑` `↓` | Move between rows |
| `←` `→` | Collapse / expand the current folder or project |
| `Enter` | Resume the selected session — see *Resume flow* |
| `Space` | Preview pane (show notes, first prompt, summary, full path) |
| `r` | Rename (= retag = move to a different folder). Prompts for the new name. |
| `n` | New folder (prompts for folder path). Created empty; persisted via `folders[]`. |
| `m` | Move the selected session to a folder (lists existing folders; can type new). |
| `d` | Delete the selected session (confirms). Removes the JSONL **and** the index entry. |
| `e` | Edit notes for the selected session (opens `$EDITOR` or an inline multi-line input). |
| `/` | Live filter across name, notes, first prompt, summary. |
| `q` `Esc` | Quit. |

**Folder deletion** is intentionally not bound. Empty folders disappear when removed from `folders[]` (achievable by moving a session out and back); populated folders cease to exist when their last session is moved or deleted. v1 does not support "delete folder and everything in it" — too easy to lose work.

### Rename and move

Both write a rename event to the session's JSONL in the same shape Claude's own `/rename` writes. **M1 task:** reverse-engineer Claude's rename serialization from a real JSONL.

**Fallback** if Claude's format proves volatile or undocumented: store an authoritative `display_name` in the index that overrides whatever the JSONL says. v1 prefers writing to the JSONL so Claude's native picker reflects the new name.

### Resume flow

`Enter` on a session causes the TUI to `exec claude --resume <id>` in the same (spawned) terminal window. The TUI process exits and Claude takes over the window. The original Claude session you typed `/session-explorer` from keeps running in its other window — two parallel Claude sessions is the expected outcome.

## The slash command

Just one — `/session-explorer`. The markdown command shells out to a small launcher script that:

1. Detects the OS (`uname -s`).
2. Picks a terminal launcher (see next section).
3. Spawns `$CLAUDE_PLUGIN_DIR/bin/session-explorer` in a new window.
4. Returns to Claude immediately (fire-and-forget; no output to wait on).

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
  "version": 1,
  "folders": [
    "audits/empty-shelf",
    "personal/learning"
  ],
  "sessions": {
    "01HXYZ…uuid": {
      "name_cached": "planning-sprint14",      // last-seen Claude name; JSONL is authoritative
      "notes": "production audit of AC modules\nfollow-up Q1",
      "project_path": "/Volumes/Projects/AcmeCorp/acme-api",
      "project_label": "acme-api",
      "branch": "feature/43070-…",
      "first_prompt": "audit which AC modules have zero production data",
      "summary": "…",                          // from /summary if available
      "created_at": "2026-05-26T14:12:00Z",
      "last_active_at": "2026-05-26T15:48:00Z",
      "message_count": 47
    }
  }
}
```

`name_cached`, `last_active_at`, `message_count` are pure perf caches — refreshed by the hook on session start and by `session-explorer index --refresh` on demand. **No `tag` field. No `kept` field.** "Kept" is `name_cached != null`.

## Hooks

One `SessionStart` hook, declared in `plugin.json`:

```jsonc
{
  "name": "session-explorer",
  "version": "0.1.0",
  "description": "File-explorer-style session management for Claude Code",
  "commands": "commands/",
  "hooks": {
    "SessionStart": [
      { "matchers": [], "command": "$CLAUDE_PLUGIN_DIR/hooks/session-start.sh" }
    ]
  }
}
```

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

`bin/session-explorer` is on the Bash-tool PATH automatically. Slash commands and hooks reference `$CLAUDE_PLUGIN_DIR`. **No manual config edits.**

**Distribution channels:**

- **Self-hosted marketplace** — the repo's own `marketplace.json` at root. Users register it with `/plugin marketplace add <owner>/<repo>`. No review. Recommended for v1 while iterating.
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
│   └── session-explorer.md               ← the one slash command
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
6. **Folder collisions.** Renaming `foo-bar` to `baz-bar` moves the session between folders. Renaming to `bar` (no dash) drops it to ungrouped. Deleting a session leaves its folder behind only if the folder also appears in `folders[]`; otherwise the folder evaporates with the last session.
7. **Empty-folder accumulation.** `--gc` also prunes entries from `folders[]` that have remained empty for >90 days.
8. **Launcher fallback.** No terminal detected → CLI prints the absolute command + copies to clipboard; the slash command's response shows "Run: …".
9. **Plugin upgrade between session starts.** Hook may be a newer version than the index format. Index reader tolerates unknown fields; writer always writes `version: 1`.

## Milestones

| M | Scope |
|---|---|
| M0 | Spec lands. (This file.) |
| M1 | Plugin manifest + `marketplace.json` + `SessionStart` hook with first-run setup + index core (`record`, `refresh`, `list`). Installable from a self-hosted marketplace. macOS terminal launcher. Reverse-engineer `/rename` JSONL format. |
| M2 | Textual TUI: tree view, all keybindings, rename/move/delete/notes, preview pane. Linux launchers. |
| M3 | `--gc` (sessions + empty folders); `session-explorer uninstall`; search across notes/prompts/summaries. |
| M4 | bats + pytest suites; CI; README quickstart with both install paths. |
| M5 | Submit to `anthropics/claude-plugins-community`. Windows / WSL launcher. |

## Open questions

- **Claude's `/rename` JSONL format.** Decided during M1 by inspecting a real renamed transcript. Fallback: `display_name` override field in the index.
- **Preview-pane content.** Currently spec'd as notes + first prompt + summary + full path. May want to add "last assistant message" once we see how cramped the layout gets. Defer to M2 dogfooding.
- **`session-explorer browse` as a standalone shell command.** Removed from this spec — the TUI is only reachable via the slash command's launcher. Easy to re-add as a thin CLI wrapper in M3 if users ask. Decision: ship without it; let usage tell us if it's needed.
