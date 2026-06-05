# session-explorer

A Claude Code plugin that turns the JSONL transcripts under `~/.claude/projects/` into a file-explorer-style tree: browse, organize, rename, move, delete, and resume sessions from a single TUI launched by one slash command.

**Status:** Shipped — **v1.11.4**, installable from the Claude Code marketplace. All milestones below (M1–M8) are complete; this document is the maintained design reference, with the milestone table and design-decision log kept as a delivery record. v1.8.0 added a subscription-usage progress bar in the tmux status line; v1.9.1 fixes explorer renames reverting when a live session re-emits its old `custom-title` (see *Design decisions (resolved)*); v1.10.0 adds the F2 rename alias, blank-name temporary sessions, a `Tab`-cycled three-mode view filter (replacing the `u` toggle), collapse-to-roots (`z`), and select-on-create; v1.11.0 adds the worktree indicator column; v1.11.1 darkens the deleted-worktree glyph (`dark_red`) to match the live `dark_green`; v1.11.2 makes resuming a deleted-worktree session recreate a real `git worktree` (on the `worktree-<leaf>` branch) instead of an empty directory; v1.11.3 repaints that session's indicator green immediately on recreate (no rescan) and treats an empty worktree dir as dead; v1.11.4 makes the context-window % model-aware (Opus 4.6+/Sonnet 4.6 measured against 1M from the first turn) so it no longer jumps when a 1M session crosses 200K.

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

**Key idea — derive, don't store.** The plugin caches metadata for browse-speed but treats the JSONL as authoritative for names *with one exception*: a live Claude session re-writes its in-memory `custom-title` every turn, so after the explorer renames a running session, Claude's next re-emit puts the **old** title back as the JSONL's last line. Naïve "last-`custom-title`-wins" would then revert the rename. The explorer therefore records the superseded title(s) as **shadows** (`name_shadows[]`) on the index entry, and a shadowed last-title is ignored in favour of the user's chosen `name_cached`. Folder/name parsing happens at render time from the session's user-assigned name (slash-separated). "Kept" is `name != null`.

**What counts as a name.** Only `/rename` and `claude -n <name>` count — both write a `custom-title` event to the JSONL. The `ai-title` events that Claude emits automatically as a session evolves (refining its own descriptive summary) **do NOT** make a session "named" in this plugin's sense. The session-explorer treats those auto-generated titles as if they didn't exist, so the index's `name_cached` is populated only by explicit user intent. A *new* (unshadowed) `custom-title` is still adopted — e.g. a `/rename` run inside the resumed session — so only re-emits of previously-seen titles are filtered, not genuine renames.

## Naming and folders

The session's Claude-assigned name encodes folder path + display name via `/`:

```
<segment>/<segment>/…/<display>   → all but the last segment → folder path; last → display name
<just-a-name>  (no /)             → at project root; display = name
(no name)                         → hidden by default; cycle view with Tab to surface for renaming or deletion
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
session-explorer · 32 sessions across 6 projects · 15 unnamed hidden (Tab)             / filter

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

Outer level: project (`project_label`, auto-grouped from cwd; git worktrees under `<repo>/.claude/worktrees/<name>` collapse into the parent repo so a project's worktrees don't each become a top-level entry). Inner level: `/`-separated folder paths parsed from session names, rendered as a nested tree of any depth. Pre-created empty folders live in the folder store file (see *Folder store* below). **Three view modes, cycled with `Tab`:**
- **Mode 0 (default):** named sessions only, plus any currently-live/active session regardless of name — cutting the visual noise from stub records. The header advertises the hidden unnamed count.
- **Mode 1 (active only):** sessions that carry the live `●` glyph (working or idle), named or unnamed. Useful for a quick "what's running" overview.
- **Mode 2 (all):** every session including unnamed ones; they appear under an `(unnamed)` sub-group per project, available for renaming or deletion.

When the visible tree is empty, the tree pane shows an actionable empty-state instead of blank space — prompting `F5` to scan when nothing is indexed yet, or `Tab` to cycle to a broader view when sessions exist but are all unnamed/hidden.

### Keybindings

| Key | Action |
|---|---|
| `↑` `↓` | Move between rows |
| `←` `→` | Collapse / expand the current folder or project |
| `Enter` | Resume the selected session — see *Resume flow* |
| `Space` | Toggle the preview pane. Headline is the session's full (un-truncated) name; body shows project, folder, branch, age, created date, message count, context size, session id, notes, first prompt, and transcript path. `Esc` also closes it. |
| `r` `F2` | Rename. On a **session**: rename (= retag = move to a different folder), prompts for the new name. On a **folder**: rename its last segment in place, prompts prefilled with that segment — cascades to every session and subfolder under it (see *Folder rename and move*). `F2` is an alias for `r`. |
| `n` | New folder (prompts for path under the current project; cursor on a folder pre-fills the prefix). Created empty; persisted in the folder store. |
| `c` | New session. On a **project** or **folder** node (or a session leaf, treated as its container): opens a dialog to name a new Claude session, pick its working directory, and optionally create a git worktree. Launches `claude --session-id <uuid> -n <name> [-w [<wt>]]` as a sibling tmux window (or via `execvp` without tmux). **Leaving the name blank** starts a temporary unnamed session — it writes no `custom-title`, stays hidden by default (visible only in mode 2 or while live), and is reaped by `--gc` on the normal retention schedule. After creation (tmux path) the explorer moves the tree cursor to the new session's row once it appears — immediately for a named session (seeded into the tree), or when the live indicator first detects it for an unnamed one. |
| `m` | Move. On a **session**: move within its project (lists existing paths; type a new path to create it). On a **folder**: re-parent the whole subtree under a chosen path (or `(ungroup)` → top level), keeping its leaf name. Candidate parents exclude the folder and its own descendants. |
| `d` | Delete the selected session (confirms). Removes the JSONL **and** the index entry. |
| `e` | Edit notes for the selected session (opens `$EDITOR` or an inline multi-line input). |
| `Tab` | Cycle the view mode: **mode 0** (named + active, default) → **mode 1** (active/live only) → **mode 2** (all, including unnamed) → back to mode 0. The header advertises the hidden unnamed count in mode 0. |
| `z` | Toggle collapse-to-roots: collapses the tree so only project root nodes are visible; pressing again expands everything back to full. Drill-down into a project is remembered across tree rebuilds within a session (not persisted across restarts). |
| `g` | Toggle the subscription-usage bar in the tmux status line (off by default; tmux-hosted only). Enable fires an immediate probe and starts a 5-min refresh interval; disable clears the bar. Off-then-on is the manual force-refresh. Inert without tmux. |
| `F5` | Rescan: import any sessions under `~/.claude/projects/` not yet tracked and refresh cached fields (runs `index.reindex` in a background worker, with a determinate progress bar shown in a modal panel — the same centered `_PanelScreen` styling as the other dialogs, overlaid on the dimmed tree). Use after a fresh install to pull in pre-existing sessions. |
| `/` | Live filter across name, notes, first prompt, summary. |
| `h` | Show the help overlay (slash-folder naming, view-mode cycle with `Tab`, full key list, author credit). Auto-opens once on first launch, then only on demand. |
| `Esc` | Close the preview pane, the help overlay, or clear an active filter. Does **not** quit. |
| `q` | Quit. |

**Folder deletion** is intentionally not bound. Empty folders disappear when removed from the folder store (achievable by moving a session out and back); populated folders cease to exist when their last session is moved or deleted. v1 does not support "delete folder and everything in it" — too easy to lose work.

### Stats columns

Each session row shows:

- **Age** since `last_active_at` (relative).
- **Worktree indicator.** A narrow column between the name and the age. `_worktree_state(project_path)` classifies each session: blank for a normal checkout, a dark-green `⎇` for a populated git worktree (`<repo>/.claude/worktrees/<name>`), and a dark-red `⎇` for one whose directory was deleted **or left empty** by a prior failed resume (both shades muted to match) — the empty-is-dead verdict matches `_dead_worktree_repo` so the indicator and the resume prompt agree. Deliberately separate from the left-column live glyph so the two are never confused. The isdir+listdir check runs once at tree-build time and is cached in the row's `worktree_state`, so a worktree deleted while the TUI is open turns red on the next rescan, not instantly. The one exception is **recreate-on-resume**: confirming the dead-worktree prompt rebuilds the worktree and immediately repaints that row green via `_set_worktree_state` (no rescan needed).
- **Approx. tokens.** Derived from `cache_read_input_tokens` of the latest assistant message in the JSONL — accurate when caching is active. Falls back to `bytes / 4` when the session has no cached turns (early sessions, cache disabled). Always prefixed with `~` in the UI to signal it's an estimate.
- **Context-window %.** The denominator is model-aware: `index._context_window(model, tokens)` looks the window up from the model id via `MODEL_WINDOWS` (prefix match; default 200K). Opus 4.6+ and Sonnet 4.6 map to 1M because the 1M window is GA in Claude Code (no beta header since 2026-03-13) and is applied automatically on Max/Team/Enterprise + API plans — so the model id, not observed usage, fixes the denominator from the first turn and the % no longer jumps as usage crosses 200K. An overflow backstop still promotes to 1M if a session's tokens exceed its mapped window (covers unknown/older models). The session's model id (`jsonl.latest_model`, from `message.model` on the latest non-synthetic assistant line) is cached in the index and shown in the preview pane.
- **Message count** (`wc -l` on the JSONL; always exact).
- **First-prompt tail** (truncated; full text in the preview pane).

These are pure caches in the index. The `SessionStart` hook refreshes them on every fire; `session-explorer index --refresh` recomputes them on demand.

> **Why not sum `input_tokens` / `output_tokens`?** Claude Code's per-message token counts are streaming-time estimates and have been observed to be off by an order of magnitude in community reports. `cache_read_input_tokens` is logged after the API response and is reliable for sessions that use caching (the vast majority).

### Rename and move

Both write a rename event to the session's JSONL in the same shape Claude's own `/rename` writes (a `custom-title` event), reverse-engineered from a real renamed transcript, **then** record the rename in the index via `index.set_name`, which sets `name_cached` and adds every other `custom-title` currently in the transcript to `name_shadows[]`. Writing to the JSONL keeps Claude's native picker in sync; the shadow makes the index authoritative against the re-emit problem (a live session re-writes its in-memory title each turn, which would otherwise revert the rename — see *Key idea*). `name_shadows` excludes the new name, so re-renaming back to a shadowed value still works (the explorer-set `name_cached` is the fallback whenever the JSONL's last title is shadowed).

> **Historical note.** Earlier specs proposed an *optional* `display_name` override "if Claude's format proves volatile." It did prove volatile — not in shape, but in ordering: Claude's per-turn re-emit means the last `custom-title` is not reliably the user's latest rename for live sessions. `name_shadows` is the concrete realization of that override, scoped to exactly the stale values rather than overriding the JSONL wholesale.

#### Folder rename and move

A folder has no record of its own — its identity is the segment-prefix shared by the session names under it plus any folder-store entry. So renaming or moving a folder is a **cascade**, not a single write:

- The new folder segments are computed from the action — `r` swaps the folder's last segment in place (`team/planning` → `team/strategy`); `m` keeps the leaf and swaps the parent (`team/planning` → `archive/planning`, or top-level via `(ungroup)`).
- Every session in the project whose folder path has the old segments as a **segment-wise prefix** is rewritten: the old prefix is replaced with the new one and the display name plus any deeper sub-segments are preserved. Each rewrite appends a `custom-title` event to that session's JSONL, then records the new name via `index.set_name` (one call per affected session, so each shadows its own prior title against re-emit reversion). Segment-wise matching means `planning` never captures a sibling named `planning-extra`.
- Folder-store entries equal to or under the old path are re-prefixed in one `folder_store.rename_subtree` call, so empty (store-only) subfolders move too.
- The whole cascade is gated behind one confirmation that names the affected session count. Re-parenting a folder into itself or a descendant is rejected; renaming/moving onto an existing target path merges into it (duplicate store entries collapse).

`tree_model.replace_folder_prefix` (pure name rewrite) and `folder_store.rename_subtree` (store re-prefix) carry the logic; `tui._relabel_folder` orchestrates the I/O.

### New session flow

`c` creates a new Claude session in the current project/folder context. A modal
collects the **name** (prefilled with the folder prefix so a slash-path nests it
exactly like rename/move), the **working directory** (derived from the project's
most-recently-active session, with any worktree suffix stripped to the repo root;
editable), and an optional **git worktree** (a checkbox plus an optional worktree
name).

The explorer generates the session UUID up front and launches
`claude --session-id <uuid> -n <name>` (plus `-w` / `-w <name>` when requested).
Claude itself writes the `custom-title` (via `-n`) and owns all worktree/branch
creation (via `-w`) — the plugin writes neither. The UUID is the tmux window name,
so the new window reconciles through the same live-registry / `list-windows`
machinery as resume. Without tmux, the explorer `execvp`s into the new session
(same exit-and-replace pattern as resume). Claude's own `--tmux` flag is
deliberately not used — sessions are hosted in the dedicated `-L session-explorer`
server.

**Name seeding.** Claude writes no transcript (and therefore no `custom-title`)
until the session's first turn, so a freshly-created, never-messaged session would
otherwise appear under `(unnamed)`. To avoid that, creation seeds `name_cached`
into the index immediately (`index.seed_new_session`) and repopulates the tree.
This does not violate "JSONL is authoritative": `claude -n` persists the identical
`custom-title` on the first turn, and `record_session` falls back to the existing
`name_cached` whenever the transcript yields no name **or** yields only a shadowed
(re-emitted) one — since the transcript is append-only, an absent title always means
"not written yet", never "name removed", so a known name is never blanked by the
2 s live-refresh. Precisely: `record_session` adopts the transcript's last
`custom-title` only when it is non-empty **and not in `name_shadows`**; otherwise it
keeps `name_cached`.

**Blank name → temporary unnamed session.** If the user leaves the name field empty
and confirms, the session is launched with no `-n` flag, so Claude writes no
`custom-title`. The session starts with `name_cached = null` (unnamed), is hidden
in mode 0 unless live, and is subject to `--gc` deletion on the normal retention
schedule. No special deletion mechanism is added — the existing GC criteria
(`name_cached IS NULL` + age) cover it.

**Select-on-create (tmux path).** After launching the new session window, the
explorer moves the tree cursor to the new session's row: immediately (on the next
repopulate) for a named session (whose row is seeded into the tree before Claude's
first turn), or as soon as the live-registry poll first detects the session alive
for an unnamed one.

If the chosen directory is not a git repository and a worktree was requested,
`claude -w` reports the error inside the session window; v1 does not pre-validate.

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
- **WSL** — inside WSL `platform.system()` reports `Linux`, so the Linux probe runs first; when it finds no Linux GUI terminal (the common WSL case) and `_is_wsl()` is true, open a Windows Terminal window via `wt.exe wsl.exe -d <distro> -- bash -lc <cmd>` so the new window re-enters the same distro. Falls through to "print the command" when `wt.exe` is absent. Detection: `WSL_DISTRO_NAME` env or `microsoft` in `/proc/version`.
- **Native Windows** (PowerShell/cmd, no WSL) — out of scope: the core relies on `fcntl` locking and a bash hook, neither of which exists there. Falls through to "print + clipboard".

**macOS is the first-class target.** Linux launchers (`$TERMINAL` / known emulators) and the WSL launcher (`wt.exe` re-entry + print/clipboard fallback) are shipped; native Windows stays out of scope.

## Data model — `~/.claude/session-explorer-index.json`

```jsonc
{
  "version": 2,
  "sessions": {
    "01HXYZ…uuid": {
      "name_cached": "planning/sprint14",       // current name; JSONL last-title wins unless shadowed
      "name_shadows": ["sprint14"],             // prior titles to ignore as stale live re-emits (optional)
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
      "model": "claude-opus-4-8",               // latest assistant message.model (or null)
      "tokens_window_pct": 19                   // model-aware denominator (1M for Opus 4.6+/Sonnet 4.6, else 200K)
    }
  }
}
```

`name_cached`, `last_active_at`, `message_count` are pure perf caches — refreshed by the hook on session start and by `session-explorer index --refresh` on demand. `name_shadows` is the one naming field that is **not** a regenerable cache: it records which `custom-title` values to treat as stale live re-emits, written only by `index.set_name` on an explorer rename (absent until then; an empty result is omitted). It survives refresh via the `**existing` carry-over in `record_session`. **No `tag` field. No `kept` field. No `folders` field.** "Kept" is `name_cached != null`. Folder data lives in the separate folder store below.

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

`index.reindex()` combines the two (refresh then backfill, so each session is touched once; accepts a `progress(done, total)` callback for the TUI's progress bar) and is what the TUI's `F5` key calls. This is the user-facing way to populate a fresh install — nothing imports pre-install sessions automatically (the `SessionStart` hook deliberately stays out of the scan path so it never blocks startup). A freshly-installed explorer shows an empty-state prompting `F5`; after a rescan the imported sessions are unnamed, so the empty-state then prompts `Tab` to cycle to a broader view to surface them.

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

## Live-session indicator

The TUI shows which Claude Code sessions are **currently live** on the machine, so a user running 2–3 agents at once can tell at a glance which are **actively working** versus **open but idle**. The signal comes from Claude Code lifecycle hooks maintaining a volatile registry; the TUI polls it. Full design rationale (why `flock`/`mtime`/`.current` were rejected) lives in `docs/superpowers/specs/2026-05-29-active-session-indicator-design.md`.

### Live registry — `~/.claude/session-explorer-live.json`

A new sidecar, **separate from the index and folder store**. Volatile runtime state only: never merged into the index, never read by retention / `--gc`. Written with the same flock + temp-file-rename atomic pattern as the index (`bin/_pkg/live.py`).

```jsonc
{
  "version": 1,
  "sessions": {
    "<session_id>": {
      "state": "working",            // "working" | "idle"
      "pid": 12345,                   // Claude process pid, recorded at SessionStart only
      "last_seen": "2026-05-29T07:08:00Z",
      "transcript_path": "/Users/.../<uuid>.jsonl",
      "cwd": "/Volumes/Projects/ClaudeSessionExplorer"
    }
  }
}
```

### Hooks → registry

A dispatcher script `hooks/session-live.sh` reads the hook payload on stdin and calls `session-explorer live --event <name> --sid <id> [...]`, which does the flock'd registry mutate. It runs detached / non-blocking and never adds turn latency; failures log and exit 0. The events are registered at **install time** — in `.claude-plugin/plugin.json` (marketplace) and in `install.sh` (plain path) — which is independent of retention: hook registration has always been the installer's job and never touches `cleanupPeriodDays`, the backup file, or the opt-in flow.

| Hook event | Matcher | Registry action |
|---|---|---|
| `SessionStart` | — | upsert entry; `state=idle`; record `pid` (from `$PPID`); `last_seen=now` |
| `UserPromptSubmit` | — | `state=working`; `last_seen=now` |
| `Stop` | — | `state=idle`; `last_seen=now` |
| `Notification` | `idle_prompt` | `state=idle`; `last_seen=now` |
| `SessionEnd` | — | remove entry (best-effort) |

State stays `working` for the whole turn (between `UserPromptSubmit` and `Stop`), so a long-running tool call with no JSONL writes is still correctly "working". `--pid` is recorded only on SessionStart.

### Death detection

`SessionEnd` is unreliable — SIGKILL, terminal-close, and crash all bypass it — so entry removal is best-effort and **PID liveness is the ground truth**. On each registry poll (`live.poll`):

- An entry is **alive iff `os.kill(pid, 0)` succeeds**; this catches the deaths `SessionEnd` misses and keeps an idle session shown for as long as its process lives.
- **TTL backstop (default 24h):** prune even a `kill -0`-alive entry whose `last_seen` is older than the TTL, guarding against PID-reuse zombies. With no recorded pid, detection is TTL-only.
- Dead entries are pruned during the poll, under flock. A stale registry left by a reboot self-heals on the first poll.

> **PID capture — validated (spike, 2026-05-29, macOS).** Recording `$PPID` assumes the hook's parent process *is* the Claude process. A throwaway probe hook on SessionStart + Stop confirmed this across many real sessions: `$PPID` is always the `claude` process directly (no wrapper shell), and `kill -0` correctly reported open sessions ALIVE / closed sessions DEAD. The TTL-only fallback (omit `--pid` → `live._alive` handles `pid is None`) is retained for any future platform where this doesn't hold, but is not needed on macOS.

### TUI rendering

Two `set_interval` timers, neither of which re-reads JSONLs or reindexes (that stays on F5):

- **Spinner tick (~200ms):** advances the animated green braille spinner (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) for every `working` row, rewriting only those rows' labels in place.
- **Registry poll (~2s):** re-reads the registry, runs death detection, recomputes each session's state, and re-renders only changed rows.
- Live rows also refresh their index metadata (first prompt, message count,
  tokens, context %) from the transcript on each ~2s poll, off the UI thread
  (only the live sessions are re-read; F5 remains the full reindex).

Glyphs: **working** → animated green spinner; **open but idle** → steady dim `○`; **inactive** → nothing. The subtitle shows the active count, e.g. `· ● N active`.

**Live sessions surface even when unnamed.** Unnamed sessions are hidden by default (in view mode 0), but a currently-live one (working *or* idle) is always shown in mode 0 — `build_nested_tree()` takes a `live_ids` escape hatch. When a live unnamed session dies it reverts to hidden on the next poll; that visibility change drives a full repopulate (rather than an in-place label rewrite) which preserves the cursor. This is orthogonal to the view-mode cycle (`Tab`) and to "kept": liveness is "shown", never "named", and never affects retention.

### Tunables (defaults)

| Knob | Default | Notes |
|---|---|---|
| Spinner tick | 200 ms | animation smoothness vs. CPU |
| Registry poll | 2 s | freshness vs. flock churn |
| Death TTL backstop | 24 h | guards PID reuse; the `kill -0` check does the real work |
| Spinner frames | `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` | braille |

**Uninstall** (`uninstall.sh` / `session-explorer uninstall`) removes all the new hook events; the registry file is volatile and can be left or cleaned.

## tmux interaction layer

The tmux interaction layer makes resume **non-destructive**: instead of `exec`-replacing the explorer process, the explorer stays alive as the **left pane** of the `explorer` window and the active session **docks as the right pane** beside it. Inactive sessions remain as background windows (so multi-session monitoring, live previews, and liveness still work), and the docked claude can be zoomed fullscreen to hide the tree. The user sees tree + active session side by side, navigating in and out without an embedded terminal widget.

Full design rationale, spike results, and build order live in `docs/superpowers/specs/2026-06-02-split-pane-explorer-claude-design.md`.

### Process model and launch

When tmux is available and not declined, `/open` launches the explorer inside a **dedicated tmux server**:

```
tmux -L session-explorer -f <generated.conf> new-session -A -s explorer 'exec session-explorer tui'
```

- **`-L session-explorer`** — fully isolated from the user's personal tmux server, config, and keybindings. The plugin never reads or writes `~/.tmux.conf`.
- **`-A` (attach-or-create)** — relaunching `/open` reattaches to an existing server. Sessions started in a previous explorer window **survive closing and reopening** the explorer; `new-session -A` is also the reconciliation mechanism: on mount, the explorer calls `tmux list-windows` to rediscover any still-running session windows.
- The explorer is the **left pane** of the `explorer` window (window name `explorer`, constant `EXPLORER_WINDOW`). The active session is **joined as the right pane** (`join-pane -h -l 65% -s <sid> -t explorer`, `DOCK_PCT=65`; size is `-l <n>%`, since `join-pane` has no `-p` flag). Inactive sessions are **background windows** named by their session id (`tmux new-window -d -n <sid>`). For a background session the `session_id → window` mapping is the window name — no separate registry; the docked claude pane is identified relative to the explorer's own pane (`$TMUX_PANE` → `_self_pane`; `docked_pane(self_pane)` returns the other pane in the explorer window, or `None` when `$TMUX_PANE` is unknown — never the explorer's own pane). The currently-docked sid is tracked in `_docked_sid`, recorded by `_join_docked` **only on a successful `join-pane`**: a failed dock leaves no phantom state (a warning is surfaced; the session keeps running as a background window, re-dockable on the next Enter).
- `launcher.py` wraps the existing `target_command` in the tmux invocation when `tmux.available()` is true; otherwise passes the command through unchanged.

**No-tmux fallback:** when tmux is absent or declined, `tui.py:run` does `os.execvp("claude", …)` (today's behaviour). The feature is purely additive.

### Interaction model

| Key | Stopped session | Running session |
|---|---|---|
| **Enter** | start it as a background window (`tmux new-window -d -n <sid> …`) then **dock it** as the right pane (`join-pane`) — one keypress | already docked → refocus the claude pane (`select-pane`); running background window → undock the current dock then dock this one |
| **space** | static metadata preview (unchanged) | live snapshot in preview pane; stay in tree |

Enter always lands you focused *in* the docked claude pane. Entering a different session **swaps the dock** — the previous claude breaks back out to a background window (`break-pane -d -s <pane_id> -n <sid>`) and keeps running, while the new one joins in. `space` peeks at any session without changing the dock. **Double-clicking** a session row is equivalent to Enter (mouse is on via the tmux config). New-session creation (`c`) docks the same way (undock-current → start → dock).

- **Cursor-follow sync (`_sync_dock_to_cursor`).** While you navigate the tree (focus in the explorer pane), the docked pane **follows the cursor**: landing on a running, dockable session of ours docks it; landing on a stopped session, a peek-only session live in another terminal, or a folder/project node **closes the pane** (undock-current; the explorer reclaims the full width). It is driven off `Tree.NodeHighlighted` (so keyboard *and* mouse moves trigger it) and **debounced** (`DOCK_SYNC_DEBOUNCE`, ~0.2 s) so holding an arrow to scroll past several running sessions coalesces to where the cursor settles rather than re-parenting the live claude pane on every keypress. Two properties keep it from getting in the way: it **never starts a stopped session** (that stays an explicit Enter), and it **never steals focus** — the join uses `join-pane -d` (`dock(..., focus=False)`) so focus stays in the tree. Because moving the cursor requires focus in the explorer, the pane never changes under you while you're typing in claude (the tree cursor isn't moving then).

- **Already live elsewhere** — if the selected session is live in the registry but is not one of our tmux windows (running in another terminal), Enter refuses with a warning and offers peek-only via transcript tail. Two `claude --resume` processes on one JSONL corrupts it.
- **Switching focus and zoom:** there are no window tabs — the explorer tree is the only session switcher. **F9** toggles focus between the two panes (`bind -n F9 select-pane -t :.+`; configurable via `switch_key`; a mouse-click on either pane also focuses it). **F12** zooms the focused pane fullscreen and back (`bind -n F12 resize-pane -Z`; configurable via `zoom_key`) — this is how you get a fullscreen claude (tree hidden) and restore the split. The status bar's right side shows a persistent `F9 ⇄ switch · F12 ⤢ full` hint, kept in the tmux status line so it survives the zoomed-fullscreen case where the Textual footer is hidden.
- cwd/worktree handling carries over from `action_resume`: a background window is created via `tmux new-window -c <resolved cwd>` (using `_resolve_resume_cwd`) before the `join-pane`, and the dead-worktree warning fires before spawning. When the session's cwd is a **deleted git worktree** (or an empty dir left by a prior failed resume), `_resolve_resume_cwd` recreates a real worktree via `_recreate_worktree`: `git -C <repo> worktree prune` then `git worktree add` on the `worktree-<leaf>` branch — reattaching if that branch survived, else creating it from HEAD (matching the branch name `claude -w` uses). Only if git fails does it fall back to a bare `makedirs` so `claude --resume` can still locate the worktree-keyed transcript.

### Snapshot rendering

For a live session the preview shows the **full metadata block** (identical to a stopped session) followed by a `── live ──` divider and the snapshot (capped to the last `LIVE_PREVIEW_LINES` rows so the metadata stays visible). A `set_interval` timer (~1 s, tunable) refreshes it via `snapshot.py`:

- **Explorer-launched (tmux) window** → `tmux capture-pane -ep -t <sid>`. tmux maintains every background window's/pane's screen buffer; the current claude frame is captured without docking it. `-e` preserves colour; rendered via `rich.text.Text.from_ansi`.
- **Live-elsewhere session** (in `live.py` but not a tmux window) → **transcript tail**: parse the last few JSONL events via `jsonl.py` into latest prompt / latest assistant text / last tool call / working-vs-idle.
- **Stopped session** → today's static metadata preview, unchanged.

Only the selected session is polled for a full snapshot. Tree-wide liveness uses the existing `live.py` poll unchanged.

### Live tree dots

No new liveness mechanism. A session started via `tmux new-window 'claude --resume …'` is an ordinary Claude session; the existing `session-live.sh` hook registers it in `session-explorer-live.json` and the TUI's existing poll renders the working/idle glyph.

When tmux-hosted, the glyph also encodes **accessibility** — whether the live session is one of *ours* (you can dock/focus into it) or running in a separate terminal (peek-only). `_poll_live` caches the set of our sessions (`_running_sids()` — background windows plus the docked pane, so the docked row shows accessible `●` rather than peek-only `○`); `_glyph(state, frame, ours)` keeps all live glyphs **green** (visible) and uses the **shape** to distinguish: **accessible** → solid green `●` (idle) / green spinner (working); **elsewhere** → hollow green `○` (idle) / green spinner (working). Without tmux (`ours=None`) the legacy look (green spinner / dim `○`) is preserved exactly.

### Lifecycle and quit-guard

**Quit (`q`) with live sessions** opens a guarded prompt listing the running set and offering (the running set is `_running_sids()` = background windows **plus** the docked session, since the docked claude is a pane, not a window, and `session_windows()` alone would miss it — otherwise a lone docked session would let `q` exit silently and kill it):
- **[s] shut down all and quit** — `tmux kill-server` → terminal closes cleanly.
- **[b] leave running in the background** — sets the persist-flag (marker file) then detaches. The server and sessions stay alive headless.
- **[c] cancel** — no action.

No silent default. With zero live sessions, `q` quits cleanly with no prompt.

**Abrupt window-close (red button / `Cmd+W`) — Option C sentinel.** An OS window close SIGHUPs the tmux *client*, which detaches without killing the server, by default leaving every claude session running headless. Resolution: a `client-detached` hook in the generated config runs server-side on any detach — `if persist-flag absent → tmux kill-server; else → persist`. Only the deliberate **[b] leave running** path sets the persist-flag before detaching; it is cleared on the next attach. Net: **red-button close shuts all sessions down**; **[b] persists** them. Killing the server SIGHUPs the claude processes, but transcripts are JSONL-streamed continuously so nothing is lost.

Fallback if the `client-detached` self-kill proves flaky: `destroy-unattached on` (drops [b] persistence but never lingers).

**Finished session** — `remain-on-exit` is deliberately NOT set, so when a session's `claude` exits its pane (or window) closes automatically. When the **docked** claude exits, its pane closes and the explorer **reclaims the full width**; a background session that exits just closes its window. The tree row reverts to a normal stopped session (no live dot); pressing Enter starts a fresh background window and docks it. No dead `[exited]` panes linger, and the transcript stays on disk (resumable, shown via the transcript-tail snapshot), so nothing is lost.

### Generated tmux config

A config file generated at launch (`~/.claude/.session-explorer.tmux.conf`), passed via `-f`, so the dedicated server is self-contained. `build_config(*, persist_flag_path, switch_key="F9", zoom_key="F12", socket=SOCKET)`. Contents: status bar on but with **no window tabs** (`window-status-format ""` / `window-status-current-format ""` — the explorer tree is the only switcher), mouse on (click-to-focus a pane), `bind -n F9 select-pane -t :.+` (pane-switch), `bind -n F12 resize-pane -Z` (fullscreen-zoom), `set-hook -g client-detached` (kill-server unless persist-flag), and a `status-right` hint `F9 ⇄ switch · F12 ⤢ full`. `remain-on-exit` is intentionally left off so exited panes auto-close. No rebinding of any user key outside this server.

### Subscription-usage bar

An opt-in progress bar showing the current 5-hour session usage (0–100%) appears in `status-left` of the tmux status line.  The existing `F9 ⇄ switch · F12 ⤢ full` hint stays on `status-right` unchanged.  Example rendering:

```
 [██░░░░░░░░░░] 18% ↺1:29am
```

**Enablement.** The bar is **off by default**.  A marker file `~/.claude/.session-explorer.usage-bar` signals "enabled" (persists across launches).  Press `g` in the TUI to toggle it on or off.  Enable fires one probe immediately so the bar appears within a few seconds, then starts a 5-minute refresh interval.  Disable removes the marker, cancels the interval, and clears `status-left`.  There is no separate refresh key — toggling `g` off then on is the manual force-refresh.  The feature is entirely **inert when not tmux-hosted**.

**Data source and probe mechanism.** There is no sanctioned local or API path to the subscription usage percentage: no local file caches it, there is no `claude usage` CLI subcommand, and the OAuth token Claude Code uses is rejected by the public Messages API (community tools that replicate the call are flagged as risking account bans).  Instead, the bar is produced by **scraping the official client**: a hidden throwaway `claude` is spawned on the dedicated `-L session-explorer` tmux server, driven to run `/usage`, and the rendered panel is captured with `capture-pane` and parsed by `usage.parse_usage`.  This uses the official client directly (lowest ban risk, no token juggling) and reuses the project's existing tmux capture machinery.

Probe details:

- The probe session runs in a **fixed cwd** `~/.claude/.session-explorer-probe/` so all probe transcripts land in one predictable project folder under `~/.claude/projects/`.
- Env `SESSION_EXPLORER_PROBE=1` is set on the spawned process.  Both the `session-start.sh` and `session-live.sh` hooks check for this variable and **bail out immediately**, so probe sessions are never recorded in the index, live registry, or tree.
- After each capture the probe transcripts in that folder are **deleted** by `usage.cleanup_probe_transcripts`, keeping the litter fully contained.
- The probe window is torn down with `send-keys 'Escape'` (to dismiss the modal Settings screen that `/usage` opens) + a `kill-window` backstop that terminates the throwaway claude either way.
- The orchestration runs in a **Textual thread worker** (decorated `@work(thread=True, exclusive=True, group="usage")` on `_refresh_usage`) so bounded waits never block the TUI event loop.

**Failure handling.** Any error — `claude` missing, trust prompt unresolved, parse miss, any timeout — degrades silently: the prior bar is left in place (or cleared on explicit toggle-off) and the UI is never blocked.  The worker swallows all exceptions and writes no log.  Failures never block the TUI or surface an error dialog.

**Scope.** Session (5-hour) bucket only.  No weekly or model-breakdown display in v1.  No configurable cadence or format.

### tmux dependency — optional and consented

- **Detect** at launch: `tmux -V`, require 3.1+ (for `join-pane -l <n>%` percentage dock sizing, plus `capture-pane -e`, root bindings, status styling). `tmux.py` owns detection and version parsing.
- **Missing** → a one-time yes/no consent prompt mirroring the retention pattern. **Yes** shows the install command for the detected package manager (`brew install tmux` on macOS; `sudo apt-get install -y tmux` / `dnf` / `pacman` / `zypper` / `apk` on Linux) for the user to run, then re-open. **No** writes a declined-marker (`~/.claude/.session-explorer.tmux-declined`) so the user is not re-nagged. The plugin only *shows* the command — it never runs the install itself (no silent sudo).
- **No bundled binary.** Auto-install is package-manager-based only, never silent, never sudo-without-asking. Vendoring a static tmux binary is rejected — against the "one vendored dep" ethos.
- **Declined or unavailable** → `execvp` fallback (§ *Process model*). The explorer remains fully functional; only background monitoring and interaction are disabled.

### New files

- **`bin/_pkg/tmux.py`** — thin CLI wrapper: `available()`/`detected_version()`, pure `build_*` argv builders + `build_config(*, persist_flag_path, switch_key, zoom_key, socket)`, persist-flag helpers (`set`/`clear`/`persist_flag_set`), and thin executing wrappers. Split-pane wrappers: `dock`/`undock` (`build_dock` = `join-pane -h -l 65%`, `build_undock` = `break-pane -d`), `list_panes`, `docked_pane` (the pane in the explorer window that is not `$TMUX_PANE`), `select_pane`. Plus the background-window wrappers (`start_window`, `start_new_session_window`, `capture_pane`, `list_windows`, `session_windows`, `kill_window`, `kill_server`, `detach_client`). `build_select_window`/`select_window` and `build_set_label`/`set_label` still exist but are no longer on the resume/new-session paths (the label is metadata-only — there are no window tabs). The dedicated server is created with `new-session -A` (attach-or-create), so there is no explicit ensure-server step. Pure logic is unit-tested; wrappers are covered by mocked TUI tests + the spikes.
- **`bin/_pkg/snapshot.py`** — `snapshot(sid) -> renderable`: capture-pane path for tmux windows, transcript-tail path otherwise. Pure; testable with fixtures.

### Tunables (defaults)

| Knob | Default | Notes |
|---|---|---|
| Snapshot poll | 1 s | freshness vs. capture-pane churn |
| tmux server socket | `session-explorer` | dedicated, isolated |
| Pane-switch key | F9 | configurable (`switch_key`); mouse-click also focuses |
| Fullscreen-zoom key | F12 | configurable (`zoom_key`) |
| Dock width | 65% | claude pane width when docked (`DOCK_PCT`) |
| tmux version floor | 3.1 | `join-pane -l <n>%` dock sizing, `capture-pane -e`, root bindings, status styling |

## Disabling native auto-cleanup

**Opt-in.** Modifying the user's `settings.json` without consent is a marketplace-review concern, so the plugin does NOT neutralise native cleanup automatically. The TUI asks on first launch (`tui.on_mount` → `retention.enable`/`retention.decline`); neither the `SessionStart` hook nor `install.sh` ever touches `settings.json`. Only when the user agrees is `cleanupPeriodDays` in `~/.claude/settings.json` set to `36500` (100 years) — with the prior value backed up — so Claude's expiry never touches user sessions and the plugin's `session-explorer index --gc` does deletion instead:

```
deletion criteria:
  name_cached IS NULL
  AND last_active_at older than <retention-days>  (default 30)
  AND no active flock on the JSONL
  AND JSONL mtime older than 60 seconds
```

`last_active_at` is read from the index; when it's missing or unparseable, the JSONL's mtime is used to judge age. Deletion removes both the JSONL and the index row, atomically under the index lock (the live-check and `unlink` run inside the same `index.mutate()` to minimise TOCTOU against a concurrent hook write).

Flags:

- `--retention-days N` — override the 30-day default.
- `--dry-run` — report what would be removed (and how many live sessions were skipped) without touching anything.

When it runs:

- **Automatically** — once retention is enabled, the `SessionStart` hook fires `session-explorer index --gc` at most once per 24 hours, fully detached so startup never blocks. A stamp file `~/.claude/.session-explorer.gc` throttles it; the stamp is written *before* gc launches, so a slow or failed run can't re-fire on the next session start. The auto-trigger is gated on the backup file existing (i.e. retention opted-in), so a user who declined never has sessions deleted.
- **Manually** — run `session-explorer index --gc` any time, or wire it into a cron / launchd job for a fixed schedule.

Opt-in state (all under `~/.claude/`):

- `.session-explorer.backup` exists → retention **enabled**; holds the prior `cleanupPeriodDays` so uninstall can restore it, and gates the auto-trigger.
- `.session-explorer.retention-declined` → user **declined**; the prompt isn't shown again.
- neither → **undecided**; the TUI prompts on next launch.

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

### macOS Dock launcher (`install-app`)

`session-explorer install-app` builds a hand-rolled `.app` under
`~/Applications` (no Automator/Xcode) and best-effort pins it to the Dock.

- **Bundle:** `Contents/Info.plist` (authored by us), `Contents/MacOS/
  session-explorer-launch` (generated zsh script, 0755), `Contents/Resources/
  icon.icns` (copied from `assets/app-icon.icns`).
- **Why a custom launcher and not Automator.** Two traps. (1) **PATH** — a
  GUI-launched Automator *Run Shell Script* inherits a stripped PATH without
  `/opt/homebrew/bin`, so `tmux.available()` returns False and `launch` silently
  drops its tmux behaviour. The generated launcher prepends the Homebrew paths.
  (2) **Icon override** — Automator applets carry a compiled `Assets.car` and a
  `CFBundleIconName` key, which modern macOS prefers over a replaced loose
  `.icns`. Our `Info.plist` sets `CFBundleIconFile` and **never**
  `CFBundleIconName`.
- **Binary resolution** is done at run time inside the launcher (read
  `installed_plugins.json` → versioned `installPath`, else `command -v`, else
  the `~/.local/bin` symlink) so it survives plugin version bumps.
- **Idempotent / best-effort.** Re-running rebuilds the bundle and reconciles a
  single Dock entry; every Dock/icon-cache call degrades to a printed
  drag-to-Dock instruction on failure. `uninstall` removes the app and unpins it.

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
│       ├── usage.py                      ← usage-bar scrape + parse + render
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
6. **Folder collisions.** Renaming `plans/sprint14` to `plans/sprint15` moves the session within the folder. Renaming to `sprint14` (no slash) drops it to ungrouped. Deleting a session leaves its folder behind only if the folder also appears in the folder store; otherwise the folder evaporates with the last session. **Folder-level** rename/move (`r`/`m` on a folder node) cascades the same prefix swap across every contained session and store entry at once (see *Folder rename and move*); renaming/moving onto an existing folder path merges the two, and re-parenting a folder into its own subtree is rejected.
7. **Empty-folder accumulation.** *Deferred (still open).* The intent is for `--gc` to also prune folder-store entries that have remained empty for >90 days, but the folder store records no per-folder timestamps today, so "empty for 90 days" isn't computable without a schema change. v1 ships session GC only; empty-folder pruning needs an `empty_since` field (folders.json schema bump) before it can be implemented. Empty folders persist until then.
8. **Launcher fallback.** No terminal detected → CLI prints the absolute command + copies to clipboard; the slash command's response shows "Run: …".
9. **Plugin upgrade between session starts.** Hook may be a newer version than the index format. A fresh install creates the index at `version: 1` (no `folders[]` since the field is never written to a new file); the one-shot v1→v2 migration runs at every CLI entry point and bumps `version` to `2` (moving any legacy `folders[]` to the folder store under `(unfiled)`). The migration is idempotent — once `version >= 2`, it short-circuits. Readers tolerate either version.
10. **Token estimate accuracy.** Per-message `input_tokens` / `output_tokens` in the JSONL are streaming-time estimates and can be order-of-magnitude wrong. Use `cache_read_input_tokens` from the latest assistant message; fall back to `bytes / 4` when caching wasn't active. UI labels the value with `~` so users know it's approximate.

## Milestones

All milestones below are **shipped** (current release: v1.11.4). The table is kept as a delivery record of what each one added.

| M | Scope |
|---|---|
| M0 | Spec lands. (This file.) |
| M1 | Plugin manifest + `marketplace.json` + `SessionStart` hook with first-run setup + index core (`record`, `refresh`, `list`). Installable from a self-hosted marketplace. macOS terminal launcher. Reverse-engineer `/rename` JSONL format. |
| M2 | Textual TUI: tree view, all keybindings, rename/move/delete/notes, preview pane, **context-size stats columns**. Linux launchers. |
| M3 | `--gc` (old unnamed sessions; auto-fired once/day by the hook + manual; empty-folder pruning deferred — see edge case #7); `session-explorer uninstall`; search across notes/prompts/summaries. |
| M4 | ✅ pytest suite + focused bats suite (install/uninstall/hook); GitHub Actions CI (ubuntu + macos × Python 3.11–3.13); README quickstart with both install paths. CLI subcommands are covered by pytest via subprocess, so bats doesn't duplicate them. |
| M5 | Community-marketplace distribution. WSL launcher (`wt.exe` re-entry + fallback); native Windows out of scope. |
| M6 | **Live-session indicator** — live registry sidecar + `session-live.sh` hooks + `live.py` (poll/death-detection) + TUI spinner/poll timers + `live_ids` unnamed-surfacing. PID-capture spike validated (2026-05-29, macOS); end-to-end TUI smoke test optional (timers/animation covered by `run_test` tests). |
| M7 | **tmux interaction layer** — `tmux.py` + `snapshot.py`; context-aware Enter (stop→start+switch-in, running→flip-in, live-elsewhere→refuse); accessible-vs-elsewhere live glyphs; generated tmux config (tabs/F12/`client-detached` sentinel); quit-guard; snapshot preview for selected live session; optional consented tmux install with declined-marker; `execvp` fallback without tmux. |
| M8 | **Subscription-usage bar** — `usage.py` (parse + render); `tui.py` scheduler (`g` toggle, immediate-probe-on-enable, 5-min interval, clear-on-disable/quit); `SESSION_EXPLORER_PROBE=1` hook bail-out; transcript cleanup after each scrape; probe cwd `~/.claude/.session-explorer-probe/`; marker file `~/.claude/.session-explorer.usage-bar`; inert without tmux; silent-failure. |

## Design decisions (resolved)

A log of decisions that were open during design and have since been settled. Two items remain deliberately deferred (in-place compaction, empty-folder pruning — see Non-goals and edge case #7).

- **Claude's `/rename` JSONL format.** Reverse-engineered from a real renamed transcript: a `custom-title` event. The index can fall back to a `display_name` override only if the format ever proves volatile.
- **Live re-emit of `custom-title` → rename reverts (FIXED).** A live Claude session re-writes its in-memory `custom-title` roughly every turn. After an *external* (explorer) rename, Claude's next re-emit appends the **old** title as the JSONL's last line, so naïve last-wins reverted the name "after a while". The early assumption that `custom-title` never drifts within a file (sampled from ~50 transcripts) was wrong — observed drift in 19/many transcripts, 3 with the exact rename-then-revert signature. Fix: `index.set_name` records superseded titles in `name_shadows[]`; `record_session` ignores a shadowed last-title and keeps the user's `name_cached`. A genuinely new (unshadowed) title is still adopted, so an in-session `/rename` still flows through. **Existing poisoned sessions self-heal on the next explorer rename** — there's no automatic backfill because a past revert can't be told apart from a deliberate rename-back.
- **Exact `message.usage` field path.** Confirmed against real transcripts: read `cache_read_input_tokens` from the latest assistant message; fall back to `bytes / 4` when caching wasn't active.
- **Model-aware context window.** `message.model` *is* present on every assistant line. The denominator reads the latest model id and maps it through `MODEL_WINDOWS` by prefix (default 200K), with Opus 4.6+ and Sonnet 4.6 mapping to 1M. This replaced an earlier usage-threshold heuristic (guess 200K, promote once tokens exceed it), which made the % collapse from ~99% to ~20% the instant a 1M session crossed 200K. The 1M window is GA in Claude Code (no beta header since 2026-03-13) and auto-applied on Max/Team/Enterprise + API plans, so the model id is a reliable denominator and the `[1m]` alias suffix never reaches the JSONL anyway (Claude Code strips it before the request). An overflow backstop still promotes to 1M for unmapped models that exceed their assumed window. Known nuance, deliberately accepted: a session actually capped at 200K (Pro-plan-without-credits, or `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`) on a 1M-capable model is measured against 1M, so it under-reports fullness — the inverse of the old jump, and only affects non-1M users.
- **In-place compaction.** *Still deferred* (see Non-goals). Reconsider once Claude Code ships a `claude --compact <id>` flag or a stable Agent SDK pattern for one-shot non-interactive compaction.
- **Preview-pane content.** Settled: headline is the full display name (the grid truncates it), followed by project, folder, branch, age, created date, message count, context size, session id, notes, first prompt, and transcript path. The `summary` block was dropped — the field is never populated today.
- **`session-explorer browse` as a standalone shell command.** Not shipped — the TUI is only reachable via the slash command's launcher (and `session-explorer tui`/`launch`). Easy to add later as a thin CLI wrapper if users ask.
- **Usage bar data source and bucket.** API-replication (using the Claude Code OAuth token against the Messages API) was rejected: the token is refused by the public API and community tools that spoof the Claude Code client are flagged as risking account bans. Scraping the official `claude` client via tmux `capture-pane` was chosen instead — it uses the official client (lowest ban risk, no token-refresh complexity) and reuses the project's existing capture machinery. Of the available `/usage` buckets (session/weekly/model breakdown), **session (5-hour) only** is displayed in v1 — it is the most actionable indicator of "can I keep working now" and fits the status-line width cleanly.
