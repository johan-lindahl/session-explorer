# Create a new session from a folder or project node

**Status:** Design approved 2026-06-02. Implementation pending.

## Problem

The TUI can browse, rename, move, delete, and resume sessions — but every
session must already exist (started by Claude Code elsewhere). There is no way
to *start* a new Claude session from the explorer. A user standing on a project
root or a folder should be able to create a new session that lands in that
context, name it directly, and optionally spin it up in a git worktree.

## Key enabler — Claude Code already owns the hard parts

Inspecting `claude --help` settled the architecture:

- **`-n, --name <name>`** — sets the session display name. This writes the
  `custom-title` event (the plugin's single source of truth for name *and*
  folder placement). Equivalent to the existing rename/move path.
- **`-w, --worktree [name]`** — Claude creates the git worktree for the
  session, optionally named. **We write no `git worktree add` and manage no
  branches** — Claude owns worktree creation, branch naming, and cwd.
- **`--session-id <uuid>`** — forces a specific session UUID. We generate the
  UUID up front so the tmux window name (`window-name == sid`, the existing
  convention) matches the real session id from the first frame, with zero new
  mapping logic.

We deliberately do **not** use Claude's own `--tmux` flag: session-explorer
hosts sessions in its dedicated `-L session-explorer` tmux server, and `--tmux`
would spawn a competing tmux/iTerm layer.

## Trigger and node handling

- New keybinding **`c`** → `action_new_session` (`c` is currently free).
- Active on **project nodes** and **folder nodes**. On a **session leaf** it
  targets that leaf's parent folder/project, reusing the same "treat a leaf as
  its container" rule as `_project_and_prefix_for_cursor`.
- No-op when there is no project context (empty tree); guarded in
  `check_action`.

## The dialog — `NewSessionScreen(_PanelScreen)`

A modal in the same `_PanelScreen` style as the rename/move/new-folder dialogs.
Fields top to bottom:

1. **Name** (`Input`): prefilled with the folder prefix when the cursor is on a
   folder (`planning/`), empty at a project root. A slash-path is folder
   placement, identical to rename/move semantics. Empty/whitespace → cancel.
2. **Directory** (`Input`, editable): prefilled with a **derived** cwd — the
   most-recently-active session's `project_path` under the target
   `project_label`; if that path sits inside `/.claude/worktrees/`, strip back
   to the repo root so `-w` branches from the real repository. Falls back to
   `$HOME` when nothing is resolvable.
3. **Worktree**: a `Checkbox` plus an **optional worktree-name** `Input`
   (enabled only when the box is checked; blank = bare `-w`).

Returns `{name, cwd, worktree: bool, worktree_name: str}`, or `None` on cancel.

## Launch flow — `action_new_session`

1. Generate a UUID → used as both the tmux window name and
   `claude --session-id <uuid>`.
2. Build argv: `claude -n <name> --session-id <uuid>` plus `-w` / `-w <wtname>`
   when requested. Names carry spaces and slashes, so the exec string is
   composed with `shlex.quote`.
3. **With tmux:** new pure builder `tmux.build_new_session_window(...)` →
   `new-window -d -n <uuid> -c <cwd> 'exec claude …'`; set `@se_label` to the
   display name; then `select_window(uuid)` to land in the session — the same
   one-keypress-in behavior as resume.
4. **Without tmux:** mirror `_exit_to_resume` — stash a `_new_session_argv` +
   cwd, `app.exit()`, and have `run()` chdir + `execvp` into it (consistent with
   today's resume fallback that replaces the explorer process).
5. The new session surfaces in the tree on its own: the `SessionStart` hook
   records it and the ~2s live poll picks up the live window. Because it is
   named via `-n`, it appears immediately under the correct folder.

## Edge cases

- **cwd is not a git repo but a worktree was requested.** `claude -w` errors
  inside the session window. v1 does **not** pre-validate — the error is visible
  to the user and keeping the launch path simple is preferred. (A lightweight
  `git rev-parse` pre-check can be added later if this proves annoying.)
- **Derived cwd no longer exists on disk.** The directory field stays editable;
  the user corrects it before submitting.
- **Name collides with an existing folder path.** Fine — the session simply
  nests there, consistent with rename/move semantics.

## Spec and tests

- **`SPEC.md`** updated in the same change: add `c` to the keybindings table,
  add a "New session" subsection under *The TUI*, and document the reliance on
  `claude -n` / `-w` / `--session-id` plus the worktree behavior.
- **Tests:**
  - Pure `tmux.build_new_session_window` argv shape + `shlex` quoting,
    unit-tested (matches the existing `build_*` test pattern).
  - `NewSessionScreen` return value and `action_new_session` orchestration via
    Textual `run_test` with mocked tmux, matching existing TUI test patterns.
  - cwd-derivation helper (most-recent session path, worktree-root stripping,
    `$HOME` fallback) unit-tested.

## Load-bearing decisions preserved

- The session name remains the only "tag"; `-n` writes `custom-title` exactly
  as `/rename` does.
- No JSONL is moved or rewritten beyond the name event Claude itself writes.
- Resume/new-session remain non-destructive under tmux (sibling window), with
  the `execvp` fallback unchanged when tmux is absent.
- One vendored dep (Textual); no new dependencies.
