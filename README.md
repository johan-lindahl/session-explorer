# session-explorer

A Claude Code plugin that turns `~/.claude/projects/*.jsonl` transcripts into a
file-explorer-style listing: browse, organize, and resume sessions from a single
slash command.

M2 ships a Textual TUI with arrow navigation, expand/collapse, rename, move,
delete, notes, preview pane, and live filter. The text-mode `list` subcommand
remains for scripting.

See [`SPEC.md`](./SPEC.md) for the full design.

## Install

### Option A — Claude Code marketplace (primary)

```bash
/plugin marketplace add <owner>/session-explorer
/plugin install session-explorer
```

### Option B — plain shell installer

```bash
git clone https://github.com/<owner>/session-explorer.git
cd session-explorer
./install.sh
```

Both paths perform the same first-run setup: back up your existing
`cleanupPeriodDays`, set it to 36500, and register the `SessionStart` hook.

## Usage

After install, start any new Claude Code session in any project. The hook
records the session into `~/.claude/session-explorer-index.json` automatically.

From inside Claude:

```
/session-explorer:open
```

(Plugin commands are always namespaced as `<plugin-name>:<command>` — there's
no way to drop the prefix.)

This opens a new Terminal.app window showing your sessions grouped by project
and (first-dash) folder. Quit the TUI with `q` to close the window.

From a regular shell:

```bash
session-explorer list      # text listing
session-explorer launch    # open in a new Terminal window
session-explorer tui       # run the TUI in the current terminal
```

## TUI keybindings

| Key | Action |
|---|---|
| `↑` `↓` | Move between rows |
| `←` `→` | Collapse / expand the current folder or project |
| `Enter` | Resume the selected session (`exec claude --resume <id>`) |
| `Space` | Toggle the preview pane (notes, first prompt, summary, path) |
| `r` | Rename — also moves the session between folders |
| `n` | Create an empty folder |
| `m` | Move the selected session to a folder |
| `d` | Delete the selected session (confirms; removes the JSONL too) |
| `e` | Edit notes (Ctrl+S to save) |
| `/` | Live filter across name, notes, first prompt, summary |
| `q` `Esc` | Quit |

## How sessions are organized

Session names map to folders via the **first dash**:

| Session name | Folder | Display name |
|---|---|---|
| `planning-sprint14` | `planning` | `sprint14` |
| `audits-q1-review` | `audits` | `q1-review` |
| `sprint14` | *(none)* | `sprint14` |

Rename a session with Claude's built-in `/rename` command; the next session
start (or `session-explorer index --refresh`) reflects the change.

## Uninstall

Restore your original `cleanupPeriodDays`:

```bash
echo "Restoring cleanupPeriodDays from $(cat ~/.claude/.session-explorer.backup)"
python3 -c "
import json, os
p = os.path.expanduser('~/.claude/settings.json')
d = json.load(open(p))
d['cleanupPeriodDays'] = int(open(os.path.expanduser('~/.claude/.session-explorer.backup')).read().strip())
json.dump(d, open(p, 'w'), indent=2)
"
rm ~/.claude/.session-explorer.backup
```

Then `/plugin uninstall session-explorer` (marketplace) or remove the symlink
and hook entry manually (plain install).

## Status

M2 — Textual TUI complete. Active development; `--gc`, uninstall command, and
Windows launcher land in M3+.
