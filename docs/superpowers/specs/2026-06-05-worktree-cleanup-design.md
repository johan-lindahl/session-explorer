# Worktree cleanup — design

**Date:** 2026-06-05
**Status:** Approved, pending implementation plan

## Problem

Git worktrees (`<repo>/.claude/worktrees/<name>`) accumulate on disk and the
explorer offers no way to reclaim them. Two distinct leaks feed the pile:

1. **The recreate path.** When the explorer resumes a session whose worktree
   directory was deleted, it rebuilds the directory with raw `git worktree add`
   (`_recreate_worktree`), then `claude --resume`. Claude only offers its native
   cleanup-on-exit prompt from the *process that created the worktree with `-w`*
   — it has no on-disk ownership marker, only in-session state. Because the
   explorer (not Claude) created the directory this session, Claude never offers
   to clean it up, so these worktrees are never reclaimed.

2. **The retention interaction.** Claude *also* auto-removes clean / background
   worktrees after `cleanupPeriodDays`. The explorer's opt-in retention feature
   sets `cleanupPeriodDays = 36500`, which disables that age-based sweep too. So
   on any machine with retention enabled, even native `-w` worktrees are stranded
   on disk indefinitely.

Delegating cleanup back to Claude is not viable: combining `--resume` with `-w`
is undocumented and structurally suspect (`-w` creates a *new* worktree and
chdir's into it; `--resume` is scoped to the cwd that recorded the session — they
pull in opposite directions). The explorer is what recreates these worktrees, so
the explorer should own removing them.

## The insight that makes removal safe

Removing a worktree's **directory** is non-destructive and reversible here:

- `git worktree remove` **without `--force`** keeps the branch `worktree-<name>`,
  so committed work survives, and git *refuses* when the tree is dirty or has
  untracked files — uncommitted work cannot be lost. Git's own refusal is our
  safety floor; we never pass `--force`.
- The explorer already rebuilds a deleted worktree on resume
  (`_recreate_worktree`). A removed worktree is just a "dead" worktree that
  resume re-materializes on demand.

So cleanup is **"free the checkout, keep the session."** Even a named / kept
session can have its worktree directory reclaimed: the transcript and the branch
survive, and resuming rebuilds a working tree on the same branch.

## Goal

Give the explorer four ways to reclaim idle worktree directories, all sharing one
guarded removal primitive and the reversibility guarantee above:

1. A manual TUI action (`w`).
2. Opt-in `--gc` pruning of the accumulated pile.
3. A one-time prompt when a docked worktree session exits clean.
4. Disk-size visibility so the user can see what's worth reclaiming.

## Non-goals

- **No `--force`, ever.** Dirty / untracked worktrees are always kept; we surface
  the refusal rather than overriding it.
- **No branch deletion.** Only the working directory is removed; `worktree-<name>`
  is preserved so committed work and resume both survive.
- **No delegation to Claude's native `-w` cleanup** (undocumented `--resume -w`
  combination — out of scope, see Problem).
- **No new glyph.** Removal reuses the existing worktree indicator: a removed
  directory flips `live` → `dead` (`⎇` green → `dark_red`).

## Design

### 1. The removal primitive

Add `_remove_worktree(project_path)` in `bin/_pkg/tui.py`, next to
`_recreate_worktree` and reusing `_WORKTREE_MARKER`. It returns a small result
enum/string — `"removed"`, `"dirty"`, or `"failed"`:

```python
def _remove_worktree(project_path: str) -> str:
    """Remove a worktree's working directory, keeping its branch.

    Runs `git -C <root> worktree remove <project_path>` WITHOUT --force, then
    `git worktree prune`. Git refuses (non-zero) when the tree is dirty or has
    untracked files — reported as "dirty", never forced. Caller guarantees the
    session is not live. Returns "removed" | "dirty" | "failed"."""
```

- Root is derived as in `_dead_worktree_repo`:
  `project_path.split(_WORKTREE_MARKER, 1)[0]`.
- Caller is responsible for the **live** guard (no flock, not in
  `self._live_states`, mtime > 60s) — same liveness model `--gc` already uses.
- `git worktree prune` afterward clears the now-stale registration so a later
  `_recreate_worktree` doesn't trip over it (symmetry with the existing
  prune-before-add in recreate).

### 2. Manual TUI action — `w`

New binding `Binding("w", "remove_worktree", "Remove worktree")` (the `w` key is
free in the main view). On a session row:

- Not a worktree (`_worktree_state` is `None`) → no-op / status hint.
- Worktree directory already gone (`"dead"`) → no-op ("already removed").
- Session is **running** (docked, in `_live_states`, or a tmux window) → refuse:
  "Stop the session before removing its worktree."
- Otherwise → `ConfirmScreen` showing the path **and its on-disk size** (§4);
  on yes, call `_remove_worktree`, then:
  - `"removed"` → flip the indicator in place via `_set_worktree_state(sid,
    "dead")` (same mechanism resume-recreate uses to repaint green).
  - `"dirty"` → status message: "Worktree has uncommitted changes — kept."
  - `"failed"` → status message with a pointer to `session-explorer.log`.

### 3. Disk-size visibility

- A cached `_worktree_size(project_path)` helper running `du -sh` (or
  equivalent) on the directory, memoized **per-sid** so the 0.2s spinner and 2s
  live poll never re-stat (same caching discipline as `worktree_state`).
- **Preview pane** gains a `Worktree size` line for worktree sessions whose
  directory exists.
- The §2 confirm dialog reuses the same cached value.
- Cache is invalidated on `r` rescan and after a successful removal.

### 4. Prompt on session end

In `_poll_live`, when a **docked** worktree session transitions live → stopped:

- Run a cheap removability check (dir exists, clean — e.g. `git worktree remove
  --dry-run` style probe, or `git status --porcelain` empty).
- If removable and the sid is not in a new `self._offered_cleanup: set[str]`,
  push a one-time `ConfirmScreen` "Remove this worktree to free space?
  `<path>` (`<size>`)". Record the sid in `_offered_cleanup` regardless of the
  answer so it never nags.
- Scoped strictly to the session that just exited — not a sweep. Dirty sessions
  are silently skipped (the user has unsaved work; surfacing nothing is correct).

### 5. `--gc` pruning

A new step in `bin/_pkg/gc.py` (`collect_garbage`, wired through
`bin/_pkg/cli.py` alongside the existing `--gc` / `--dry-run` / `--retention-days`
flags), gated **exactly like existing retention**: `--gc` runs, and the
auto-trigger additionally requires the retention backup file to exist. Worktree
pruning inherits the existing `--dry-run` mode (reports the directories it would
remove, deletes nothing).

- Enumerate index sessions whose `project_path` is under `.claude/worktrees/` and
  whose directory exists.
- Skip any that are **live** (active flock or mtime within 60s — the existing
  `--gc` liveness skip).
- Skip any whose directory mtime is **newer than the idle threshold**
  (default **14 days**; see "Idle threshold" below).
- For the rest, `_remove_worktree` (no `--force`). Clean ones are reclaimed;
  dirty ones are refused by git and **skipped + logged** to
  `~/.claude/session-explorer.log` (count of reclaimed / skipped-dirty).
- **Decoupled from transcript retention.** We prune the *directory* even for
  kept / named sessions — the transcript and branch survive and resume rebuilds.
  This is what actually drains both leaks, including the native-`-w` worktrees
  stranded by `cleanupPeriodDays = 36500`.

#### Idle threshold

A worktree directory is eligible for `--gc` removal when its directory mtime is
older than **14 days**, a fixed default independent of the (effectively infinite)
transcript retention period (the existing `--retention-days`, default 30, governs
*unnamed-session transcript* expiry — a separate concern from idle worktree
directories). Rationale: because removal is reversible via `_recreate_worktree`,
an aggressive, short threshold is low-risk and maximizes reclaimed disk. Kept as a
module constant (not a new flag) in this iteration (YAGNI); it mirrors the
`--retention-days` shape so it is trivial to surface as `--worktree-idle-days`
later if users ask.

### 6. SPEC + docs

Update in the same change:

- `SPEC.md`: the reversible-cleanup model, the "git refuses dirty = safety floor"
  guarantee, the `--gc` worktree-pruning step and its retention gate, and the
  documented `cleanupPeriodDays` / native-worktree interaction (why the explorer
  must own cleanup). Add the `w` keybinding to the TUI key list.
- Help screen (`tui.py` help text): add `w` — Remove worktree.
- `CLAUDE.md` "Load-bearing design decisions": a bullet that worktree removal is
  never `--force`d and never deletes the branch, and that `--gc` may reclaim
  directories of kept sessions because resume rebuilds them.

### 7. Tests

pytest, temp git repos (mirroring existing worktree tests in `test/`):

- `_remove_worktree`: clean tree → `"removed"` and the branch `worktree-<name>`
  still resolves; dirty tracked change → `"dirty"`, directory intact;
  untracked file → `"dirty"`, directory intact.
- **Round-trip**: `_remove_worktree` then `_recreate_worktree` restores a working
  tree on the same branch (proves reversibility).
- `--gc` pruning: an idle + clean worktree is removed; a fresh-mtime (or
  flock-held) worktree is skipped (live guard); a dirty worktree is skipped and
  the transcript + branch are untouched; the retention gate is respected (no
  pruning without the backup file).
- Render / size: `_worktree_size` cache is populated once and reused (no second
  `du`); confirm dialog and preview show the cached value.

## Files touched

- `bin/_pkg/tui.py` — `_remove_worktree`, `_worktree_size` (cached),
  `action_remove_worktree`, the `w` binding, `_offered_cleanup` + the
  `_poll_live` exit-prompt hook, preview-pane size line, help text.
- `bin/_pkg/gc.py` — the worktree-pruning step in `collect_garbage`,
  idle-threshold constant, liveness + retention gating, `--dry-run` support, log
  output. `bin/_pkg/cli.py` only if help text needs updating.
- `SPEC.md`, `CLAUDE.md`, help screen, `CHANGELOG.md`, version files
  (`__init__.py` + `plugin.json`) per the `cutting-a-release` skill when shipping.
- `test/` — new unit, round-trip, `--gc`, and render/size tests.
