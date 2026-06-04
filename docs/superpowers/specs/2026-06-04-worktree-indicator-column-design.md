# Worktree indicator column — design

**Date:** 2026-06-04
**Status:** Approved, pending implementation plan

## Problem

The explorer tree gives no visual cue whether a session's working directory is a
normal repository checkout ("root") or a Claude Code git **worktree**
(`<repo>/.claude/worktrees/<name>`). Worktree sessions are already grouped under
their parent project (`index._project_label`), so from the tree alone they are
indistinguishable from root sessions of the same project. Users also can't tell
at a glance when a worktree's directory has since been **deleted** (resuming such
a session would silently recreate the dir — see `_dead_worktree_repo`).

## Goal

Mark each session row with a small, unobtrusive indicator:

- **Root session** → blank (no glyph).
- **Worktree, directory still on disk** → dark-green `⎇`.
- **Worktree, directory deleted** → red `⎇`.

The indicator lives in its **own narrow column placed after the name and before
the age column**, deliberately separate from the far-left active-session
glyph (spinner / `○` / `●`) so the two are never confused.

## Non-goals

- Flagging deleted *root* repositories. Root sessions stay blank regardless of
  whether their `project_path` still exists — consistent with the root-vs-worktree
  framing.
- Live (sub-refresh) detection of deletion. See "Freshness" below.
- Any change to grouping, sorting, filtering, or resume behavior.

## Design

### 1. Worktree-state helper (pure)

Add a pure helper in `bin/_pkg/tui.py`, next to the existing
`_dead_worktree_repo()` and reusing the same `_WORKTREE_MARKER`
(`"/.claude/worktrees/"`):

```python
def _worktree_state(project_path: "str | None") -> "str | None":
    """Classify a session's working dir for the worktree column.

    Returns None for a root checkout (no worktree marker), "live" for a
    worktree whose directory still exists, "dead" for a worktree whose
    directory has been removed. Pure except for the single isdir stat."""
    if not project_path or _WORKTREE_MARKER not in project_path:
        return None
    return "live" if os.path.isdir(project_path) else "dead"
```

Three outcomes: `None` (root, blank), `"live"` (dark-green `⎇`), `"dead"`
(red `⎇`).

### 2. Glyph renderer

Add a small renderer kept **separate** from `_glyph()` (the active-session
indicator) so styling can never bleed between the two columns:

```python
WT_GLYPH = "⎇"

def _wt_glyph(state: "str | None") -> str:
    if state == "live":
        return f"[dark_green]{WT_GLYPH}[/]"
    if state == "dead":
        return f"[red]{WT_GLYPH}[/]"
    return " "   # root: blank cell
```

Exact colors: `dark_green` (live) and `red` (dead) — both Textual named colors,
recolorable because the glyph is a single-cell monochrome symbol (emoji were
rejected: fixed color + double width would break recoloring and column
alignment).

### 3. Column layout

Introduce a constant `WT_W` (worktree column total width = 1 glyph cell +
trailing padding) and insert it **between the variable-width name field and the
stat suffix** (age / `~TOK` / `CTX` / `MSGS` / first prompt).

- Current row build (`_row_label`, `tui.py:86`):
  `glyph + f"{display:<{name_w}}" + _stat_suffix(...)`
- New row build:
  `glyph + f"{display:<{name_w}}" + _wt_cell(state) + _stat_suffix(...)`
  where `_wt_cell` right-pads the glyph to `WT_W` cells.

Because `WT_W` is constant across all rows, the stat columns remain aligned at
the same absolute screen column regardless of folder nesting depth (the existing
`name_w = max(8, NAME_W + 2*GUIDE_DEPTH - depth*GUIDE_DEPTH)` invariant is
unaffected — the constant column shifts every row equally).

The header row (`_column_header`, `tui.py:109`) gains `WT_W` blank spaces in the
same position — **blank header**, no label.

### 4. Freshness / caching

The `os.path.isdir` stat runs **once per session at tree-build time** (initial
load and the manual `r` refresh). The result is stored in the row's data dict as
`worktree_state`. `_row_label` and `_tick_spinner` read that cached value, so:

- The 0.2s spinner re-render (`_tick_spinner`) never stats the filesystem.
- The 2s live poll (`_poll_live`) never stats the filesystem.

**Consequence:** a worktree deleted while the TUI is open turns red on the next
`r` refresh, not instantly. This is the accepted trade-off (chosen over
recomputing every 2s) to keep rendering cheap.

### 5. Tests

Following existing patterns in `test/`:

- **Unit** (`_worktree_state`): three cases using `tmp_path` —
  a non-worktree path → `None`; a path under `.../.claude/worktrees/<name>` that
  exists → `"live"`; the same shape with the dir removed → `"dead"`.
- **Render**: build a row for a live worktree, a dead worktree, and a root
  session; assert the label contains `[dark_green]⎇`, `[red]⎇`, and neither
  glyph respectively — mirroring the existing label-assertion tests.

### 6. Docs / release

- Update `SPEC.md` (TUI column list and glyph legend) in the same change.
- Update the help-screen glyph legend if it enumerates row glyphs.
- Version bump + `CHANGELOG.md` entry + GitHub release per the
  `cutting-a-release` skill when shipping.

## Files touched

- `bin/_pkg/tui.py` — `_worktree_state`, `_wt_glyph`, `WT_W`/`WT_GLYPH`,
  `_row_label`, `_column_header`, and storing `worktree_state` in row data at
  build time.
- `test/` — new unit + render tests.
- `SPEC.md`, help screen, `CHANGELOG.md`, version files.
