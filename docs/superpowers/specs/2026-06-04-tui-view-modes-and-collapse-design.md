# TUI quality-of-life tweaks: F2 rename, temp sessions, view-mode cycle, select-on-create, collapse-to-roots

Date: 2026-06-04
Status: Approved — ready for implementation plan

## Summary

Five small, independent UX tweaks to the Textual TUI (`bin/_pkg/tui.py`, with
small touches to `bin/_pkg/tree_model.py` and `bin/_pkg/tmux.py`):

1. **F2 → rename** — bind the conventional system rename key as an alias for `r`.
2. **Blank-name `c` → temporary unnamed session** — pressing `c` and leaving the
   name empty starts a normal *unnamed* Claude session instead of bailing.
3. **`Tab` cycles three display modes** — replacing the `u` show/hide toggle.
4. **Select the newly created session in the tree** after `c` finishes.
5. **`z` collapses the tree to project roots** with sticky drill-down.

Each item is self-contained; none changes the index/folder data model or the
load-bearing rename/liveness contracts. `SPEC.md` is updated in the same change.

## Item 1 — F2 → rename

Add an alias binding next to the existing `r` (`tui.py:553`):

```python
Binding("f2", "rename", "Rename", key_display="F2", show=False),
```

- `show=False` keeps the footer uncluttered (`r Rename` already shows).
- Add `"f2"` is not needed in `check_action` — it routes to the existing
  `"rename"` action, which is already in the modal-gating allow-list
  (`tui.py:618`). No new action method.

**Test:** pressing `f2` invokes the same code path as `r` (rename screen pushed).

## Item 2 — Blank-name `c` → temporary unnamed session

Today `action_new_session`'s `after` callback returns early when the name is
empty (`tui.py:1244-1245`). Change it so an empty name is a valid choice that
starts an unnamed session.

An unnamed session is *already* the spec's notion of "not kept / ephemeral":
it is hidden by default and reaped by `--gc` on the retention schedule. So there
is **no new deletion mechanism** — "auto-deleted" is satisfied by the existing
gc-of-unnamed behavior. (If retention is not enabled, the session simply stays
unnamed and hidden, exactly like any other unnamed session.)

When the chosen name is empty:

- **Skip `seed_new_session`** — there is no name to seed, and seeding an empty
  string would write a misleading `name_cached`.
- **Omit `-n <name>`** from the launch command so Claude never writes a
  `custom-title`:
  - non-tmux path: `_new_session_argv` (`tui.py:1807`) builds
    `["claude", "--session-id", sid]` (no `-n`) when `name` is empty.
  - tmux path: `build_new_session_window` (`tmux.py:54`) builds inner
    `["exec", "claude", "--session-id", sid]` (no `-n`) when `name` is empty.
- **Dock label** falls back to `sid[:8]` (already the case via
  `label = display or sid[:8]`).

The worktree tri-state and cwd handling are unchanged.

**Tests:** empty name produces argv without `-n`; empty name does not call
`seed_new_session`; a non-empty name is unchanged (regression guard).

## Item 3 — `Tab` cycles three display modes (removes `u`)

Replace `self._show_unnamed: bool` (`tui.py:586`) with `self._view_mode: int`
taking values 0/1/2:

| Mode | Name | Shows | Maps to `build_nested_tree` |
|------|------|-------|------------------------------|
| 0 | Named + active (default) | named sessions + any live session | `include_unnamed=False, live_only=False` |
| 1 | Active only | only sessions with the live ● glyph (named *or* unnamed) | `live_only=True` |
| 2 | All | everything, including all unnamed | `include_unnamed=True, live_only=False` |

Changes:

- **Binding:** remove `Binding("u", "toggle_unnamed", ...)`; add
  `Binding("tab", "cycle_view", "Cycle view", key_display="Tab")`.
- **Action:** rename `action_toggle_unnamed` → `action_cycle_view`, which does
  `self._view_mode = (self._view_mode + 1) % 3` then `_populate()`.
- **`check_action`** allow-list (`tui.py:618`): replace `"toggle_unnamed"` with
  `"cycle_view"`.
- **`build_nested_tree`** (`tree_model.py:74`) grows an optional
  `live_only: bool = False` parameter (backward-compatible). When `live_only` is
  true, only sessions whose `sid in live_ids` are placed (named or unnamed); the
  synthetic `(unnamed)` folder is still used for the unnamed live ones.
- **`_populate`** (`tui.py:787`) selects the parameters from `_view_mode`:
  - mode 0 → `include_unnamed=False`
  - mode 1 → `live_only=True`
  - mode 2 → `include_unnamed=True`
- **Subtitle** (`tui.py:803-807`) reflects the active mode:
  - mode 0: `… · {K} unnamed hidden (Tab)` when any are hidden, else plain count.
  - mode 1: `Active only — {N} sessions (Tab)`.
  - mode 2: `All sessions incl. unnamed (Tab)`.
- **`_visibility_changed`** (`tui.py:1549`): in mode 1, *any* change to the live
  set is a membership change (so return `True`); mode 0 keeps the existing
  "only unnamed flips matter" logic; mode 2 returns `False` (all sessions always
  present regardless of liveness).
- **Help screen** and **footer label** text updated from "Toggle unnamed (u)" to
  the Tab cycle.
- **`_empty_state_text`** (`tui.py:175`) keeps working off `unnamed_hidden`; in
  mode 1 with zero live sessions it should read as "no active sessions" rather
  than "unnamed hidden" — handled by passing the mode (or a small flag) through.

**Tests:** `live_only` includes a live unnamed session and excludes a non-live
named one; `Tab` advances 0→1→2→0 and each mode renders the expected row set;
subtitle text per mode.

## Item 4 — Select the new session after creating it (tmux path)

After `_do_new_session` (`tui.py:1270`) starts and docks the session, move the
tree cursor to it, reusing the existing `_restore_cursor_to_sid` helper
(`tui.py:1527`).

Timing matters: a **named** new session appears on the immediate `_populate()`,
but an **unnamed** one in mode 0 is only visible because it is live, and
`_do_new_session` runs `_populate()` *before* `_poll_live()` has detected the
brand-new process (its JSONL may not exist yet). So a one-shot select would miss
the unnamed case.

Solution — a **pending select**:

- Set `self._pending_select_sid = sid` in `action_new_session`'s `after` (and in
  `_do_new_session`).
- At the end of `_populate()`, if `_pending_select_sid` is set and its row now
  exists in `_row_nodes`, move the cursor there and clear the flag (so it cannot
  yank the cursor later). Named → fires on the first populate; unnamed → fires on
  the next ~2s live-poll repopulate.
- Reset `_pending_select_sid` defensively after the first live-poll cycle even if
  the row never appears, to avoid a late surprise jump.

The **non-tmux path** exits and `execvp`s Claude, so there is no tree to select —
this item is tmux-path only.

**Tests:** creating a named session moves the cursor to its row; pending-select
fires on a later populate for a session that appears after the first populate.

## Item 5 — `z` toggles collapse-to-roots, with sticky drill-down

Today every project/folder node renders with `expand=True` (`tui.py:861,869`),
so the whole tree is always open.

Add a persistent `self._collapse_mode: bool` (default `False`) toggled by a new
binding `Binding("z", "toggle_collapse", "Collapse tree")` (label flips to
"Expand tree" when collapsed; key is easy to rebind). Add `"toggle_collapse"`
to the `check_action` allow-list.

- **Expanded** (default): unchanged — everything open.
- **Collapsed**: projects render collapsed, so only the project root rows show;
  the user drills into the project they are working on.

Because `_populate()` rebuilds the tree on every live-membership change, a naive
"default expand = not collapse_mode" would re-collapse a project the moment a
background session dies. So drill-down is made **sticky**:

- Track `self._expanded: set[str]` of opened node keys. Key form:
  `project_label` for a project node, and `project_label + "\x00" +
  "/".join(segments)` for a folder node (`\x00` cannot appear in a label/segment).
- A node renders expanded iff `not self._collapse_mode or key in self._expanded`.
- Handle Textual's `Tree.NodeExpanded` / `Tree.NodeCollapsed` messages to add /
  remove the node's key from `_expanded` (only meaningful while collapsed; harmless
  otherwise). Project/folder nodes already carry `data={"project", "segments"}`,
  so the key is derivable in the handler.
- `action_toggle_collapse`: flip `_collapse_mode`; when turning **on**, clear
  `_expanded` (everything closes to roots); when turning **off**, return to
  expand-all. Then `_populate()`.

Interaction with item 4: when the pending-select target sits under a collapsed
branch, add its ancestor keys (project, then each folder prefix) to `_expanded`
before moving the cursor, so the branch opens and the row is reachable.

**Tests:** in collapse mode, project nodes render collapsed and folders are
hidden; an entry in `_expanded` renders that node open across a repopulate;
toggling collapse on clears `_expanded`; selecting a new session under a
collapsed project opens its ancestors.

## Out of scope / non-goals

- No new deletion mechanism for temp sessions (existing `--gc` covers it).
- No change to the index/folder data model, the rename/`name_shadows` contract,
  or the live-detection mechanism.
- No persistence of `_view_mode`, `_collapse_mode`, or `_expanded` across TUI
  restarts — they are in-session UI state only.
- Non-tmux session creation does not get tree-selection (the app exits).

## SPEC.md updates (same change)

- TUI keybinding table: add `F2` (rename alias), `Tab` (cycle view), `z`
  (collapse tree); remove `u`.
- Replace the "unnamed sessions hidden; press `u`" wording with the three-mode
  description.
- Note that a blank-name `c` creates an unnamed (ephemeral, gc-reaped) session.
- Note select-on-create and the collapse-to-roots behavior.
