# session-explorer

[![CI](https://github.com/johan-lindahl/session-explorer/actions/workflows/ci.yml/badge.svg)](https://github.com/johan-lindahl/session-explorer/actions/workflows/ci.yml)

A Claude Code plugin that turns `~/.claude/projects/*.jsonl` transcripts into a
file-explorer-style listing: browse, organize, and resume sessions from a single
slash command.

The Textual TUI gives you arrow navigation, expand/collapse, rename, move,
delete, notes, a preview pane, and live filter. The text-mode `list` subcommand
remains for scripting.

See [`SPEC.md`](./SPEC.md) for the full design.

## What it looks like

Sessions are grouped by project, then by the `/`-separated folders encoded in
their names. Stat columns show age, approximate context size, the share of the
context window used, and message count.

![The session-explorer tree view](docs/images/tree.png)

Press `Space` for a preview pane with the full name, project, branch, notes,
first prompt, and transcript path:

![The preview pane](docs/images/preview.png)

Press `h` for the built-in help (it also auto-opens on first launch):

![The help overlay](docs/images/help.png)

> Screenshots use sample data.

## Install

This GitHub repo **is** the plugin marketplace — there's no separate packaging
or publishing step. Installing and distributing are the same two commands.

### Option A — Claude Code marketplace (recommended; also how you share it)

Run these inside Claude Code:

```
/plugin marketplace add johan-lindahl/session-explorer
/plugin install session-explorer@session-explorer
```

`session-explorer@session-explorer` is `<plugin-name>@<marketplace-name>`; both
happen to be `session-explorer` here.

**Distributing to colleagues:** the repo is public, so just send them those two
lines — no access grants needed. When you push a new version, they update with:

```
/plugin marketplace update session-explorer
/plugin install session-explorer@session-explorer
```

### Option B — plain shell installer (local development)

```bash
git clone https://github.com/johan-lindahl/session-explorer.git
cd session-explorer
./install.sh
```

Both paths perform the same first-run setup: back up your existing
`cleanupPeriodDays`, set it to 36500, and register the `SessionStart` hook.

> **Platform note:** `/session-explorer:open` opens the TUI in a new terminal
> window — **macOS** (Terminal.app) and **Linux** (your `$TERMINAL` or a known
> emulator) are supported directly. On **Windows, use WSL**: the plugin runs as
> a Linux app there, and the launcher opens a Windows Terminal window back into
> your distro when `wt.exe` is available. If no launcher is found on any
> platform, the command to run is printed so you can paste it into a terminal
> yourself. Native (non-WSL) Windows is not supported.

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
and folder. Quit the TUI with `q` to close the window.

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
| `Space` | Toggle the preview pane (full name, project, folder, branch, age, created, messages, context, session id, notes, first prompt, path) |
| `r` | Rename — also moves the session between folders |
| `n` | Create an empty folder |
| `m` | Move the selected session to a folder |
| `d` | Delete the selected session (confirms; removes the JSONL too), or an empty folder (refuses if it still contains sessions) |
| `e` | Edit notes (Ctrl+S to save) |
| `u` | Toggle visibility of unnamed sessions (hidden by default) |
| `F5` | Rescan `~/.claude/projects/` — import sessions not yet tracked (shows a progress bar) |
| `/` | Live filter across name, notes, first prompt, summary |
| `h` | Show help (auto-opens once on first launch) |
| `Esc` | Close the preview pane / help (or clear an active filter) |
| `q` | Quit |

## How sessions are organized

Session names map to folders via `/`-separated paths:

| Session name | Folder path | Display name |
|---|---|---|
| `planning/sprint14` | `planning` | `sprint14` |
| `audits/q1-review` | `audits` | `q1-review` |
| `team/planning/q1` | `team/planning` | `q1` |
| `sprint14` | *(none)* | `sprint14` |

Dashes are plain characters with no special meaning. Multiple `/` segments
create nested folders of any depth. Rename a session with Claude's built-in
`/rename` command; the next session start (or `session-explorer index
--refresh`) reflects the change.

## Cleanup & retention

Installing sets `cleanupPeriodDays` to 36500, which disables Claude Code's own
session expiry — so the plugin handles cleanup instead. `session-explorer index
--gc` deletes **unnamed** sessions idle longer than the retention window
(default 30 days). Named (renamed) sessions are never touched, and sessions that
look live — a transcript modified in the last 60 seconds, or one holding an
active lock — are skipped.

You don't have to run it manually: the `SessionStart` hook fires `--gc` at most
once every 24 hours, in the background, so old unnamed stubs expire on their own.

```bash
session-explorer index --gc                   # delete now (defaults)
session-explorer index --gc --dry-run         # show what would be deleted
session-explorer index --gc --retention-days 7
```

`--dry-run` reports the count (and how many live sessions it skipped) without
deleting anything.

## Uninstall

Uninstalling restores your original `cleanupPeriodDays` (saved at install time)
and removes session-explorer's files. **Run the teardown first, then remove the
plugin** — `/plugin uninstall` deletes the binary, so the order matters.

Your session index and folder data (names, notes, folders) are **kept by
default** so a reinstall restores them. Add `--purge` to delete those too.

### Marketplace install

Run the teardown (this resolver locates the installed binary, which isn't on your
shell `PATH`), then remove the plugin inside Claude Code:

```bash
bash -c 'F="$HOME/.claude/plugins/installed_plugins.json"; CLI=$(python3 -c "import json,sys,os; d=json.load(open(sys.argv[1])) if os.path.exists(sys.argv[1]) else {}; e=d.get(\"plugins\",{}).get(\"session-explorer@session-explorer\",[]); print((e[0].get(\"installPath\",\"\")+\"/bin/session-explorer\") if e else \"\")" "$F"); [ -x "$CLI" ] || CLI=$(command -v session-explorer); "$CLI" uninstall'
```

```
/plugin uninstall session-explorer
```

### Plain install

```bash
./uninstall.sh            # add --purge to also delete the index + folders
```

## Status

M3 + M4 complete: the Textual TUI (M2), `--gc` retention with a once-daily
auto-trigger, rescan (`F5`), `session-explorer uninstall`, live filtering,
model-aware context sizing, and macOS/Linux/WSL launchers (native Windows is out
of scope). Tested by pytest + bats, run in CI on ubuntu + macos across Python
3.11–3.13. Next up — M5: submission to the community marketplace.

### Running the tests

```bash
python3 -m pytest test/ -q                                   # Python logic + CLI/TUI
bats test/install.bats test/uninstall.bats test/hook.bats    # shell scripts + hook
```

pytest needs `pytest` + `pytest-asyncio` (`pip install -r test/requirements-dev.txt`);
Textual is vendored, so nothing else is required. `bats` is [bats-core](https://github.com/bats-core/bats-core).
