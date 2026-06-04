# Worktree Indicator Column Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a narrow column between each session's name and its age that shows a `⎇` glyph — dark-green when the session runs in a git worktree that still exists, red when the worktree directory was deleted, and blank for normal "root" checkouts.

**Architecture:** A pure classifier `_worktree_state(project_path)` returns `None` / `"live"` / `"dead"`. A pure renderer `_wt_cell(state)` turns that into a fixed-width (`WT_W`) cell, inserted into `_row_label` and `_column_header`. The `os.path.isdir` stat runs once per session at tree-build time and the result is cached in each leaf's `data` dict (key `worktree_state`), so the 0.2s spinner re-render and 2s live poll never touch the filesystem.

**Tech Stack:** Python 3.11+, Textual (vendored), pytest + pytest-asyncio (`asyncio_mode = auto`). All TUI rendering helpers in `bin/_pkg/tui.py` are plain pure functions returning Rich-markup strings.

**Spec:** `docs/superpowers/specs/2026-06-04-worktree-indicator-column-design.md`

---

## Reference: current code being changed

All line numbers are as of the start of this plan.

- `bin/_pkg/tui.py:40-49` — layout constants (`NAME_W`, `GUIDE_DEPTH`, `GLYPH_W`, glyph constants).
- `bin/_pkg/tui.py:80-83` — `_stat_suffix(age, tok, pct, msgs, msgs_unit, prompt)` renders the block after the name field. **Unchanged.**
- `bin/_pkg/tui.py:86-106` — `_row_label(sid, s, depth, glyph="  ")` builds a leaf row as `glyph + f"{display:<{name_w}}" + _stat_suffix(...)`.
- `bin/_pkg/tui.py:109-114` — `_column_header()` builds the header row as `" "*GLYPH_W + f"{'NAME':<{name_region}}" + _stat_suffix("AGE", ...)`.
- `bin/_pkg/tui.py:887-894` — the app's inner `render(parent, ...)` builds each leaf: `glyph = _glyph(...)`, `leaf = parent.add_leaf(_row_label(sid, s, child_depth, glyph), data={"sid": sid, **s})`. `s` carries `project_path`.
- `bin/_pkg/tui.py:1652-1658` — `_relabel_live_rows` relabels tracked rows from `leaf.data`.
- `bin/_pkg/tui.py:1691-1703` — `_apply_live_metadata` rebuilds `leaf.data = {"sid": sid, **data[sid]}` from the index, then relabels. **This drops any extra key not in the index dict — so the cached `worktree_state` must be re-merged here.**
- `bin/_pkg/tui.py:1705-1717` — `_tick_spinner` relabels working rows from `leaf.data or {}`.
- `bin/_pkg/tui.py:1830-1843` — `_WORKTREE_MARKER = "/.claude/worktrees/"` and `_dead_worktree_repo(project_path)`. The new `_worktree_state` goes next to this, reusing the same marker.

Existing tests that assert exact column offsets and **will break** when the WT cell is inserted (updated in Task 2):
- `test/test_tui.py:380-397` — `test_row_label_columns_align_across_depth`
- `test/test_tui.py:400-408` — `test_column_header_offset_matches_grouped_leaf`
- `test/test_tui.py:411-419` — `test_long_name_truncates_to_field_width` (the `row[GLYPH_W + NAME_W] == " "` assertion still holds because a root cell is blank — verify, don't change).

---

## Task 1: `_worktree_state` classifier

**Files:**
- Modify: `bin/_pkg/tui.py` (add function next to `_dead_worktree_repo`, ~line 1843)
- Test: `test/test_tui.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_tui.py`:

```python
def test_worktree_state_root_is_none(tmp_path):
    from _pkg.tui import _worktree_state
    # A normal checkout path (no worktree marker) -> not a worktree.
    assert _worktree_state(str(tmp_path)) is None
    assert _worktree_state(None) is None
    assert _worktree_state("") is None


def test_worktree_state_live_when_dir_exists(tmp_path):
    from _pkg.tui import _worktree_state
    wt = tmp_path / "repo" / ".claude" / "worktrees" / "feature-x"
    wt.mkdir(parents=True)
    assert _worktree_state(str(wt)) == "live"


def test_worktree_state_dead_when_dir_missing(tmp_path):
    from _pkg.tui import _worktree_state
    # Marker present in the path, but the directory was never created.
    gone = tmp_path / "repo" / ".claude" / "worktrees" / "deleted"
    assert _worktree_state(str(gone)) == "dead"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py -k worktree_state -q`
Expected: FAIL with `ImportError: cannot import name '_worktree_state'`.

- [ ] **Step 3: Implement the classifier**

In `bin/_pkg/tui.py`, immediately after `_dead_worktree_repo` (after line 1843), add:

```python
def _worktree_state(project_path: "str | None") -> "str | None":
    """Classify a session's working dir for the worktree indicator column.

    Returns None for a root checkout (no worktree marker), "live" for a git
    worktree whose directory still exists, "dead" for a worktree whose directory
    has been removed. Pure except for the single isdir stat — callers cache the
    result so the spinner/poll re-renders never hit the filesystem."""
    if not project_path or _WORKTREE_MARKER not in project_path:
        return None
    return "live" if os.path.isdir(project_path) else "dead"
```

(`_WORKTREE_MARKER` and `os` are already in scope; `os` is imported at the top, line 9.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py -k worktree_state -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): _worktree_state classifier for worktree indicator"
```

---

## Task 2: Render the WT column (glyph, cell, row, header)

**Files:**
- Modify: `bin/_pkg/tui.py:40-49` (constants), `80-106` (`_row_label`), `109-114` (`_column_header`)
- Test: `test/test_tui.py` (new render tests + update 2 existing alignment tests)

- [ ] **Step 1: Write the failing render tests**

Append to `test/test_tui.py`:

```python
def test_wt_cell_width_and_colors():
    from _pkg.tui import _wt_cell, WT_W
    from rich.text import Text
    # Each cell renders to exactly WT_W display cells regardless of state.
    for state in (None, "live", "dead"):
        assert Text.from_markup(_wt_cell(state)).cell_len == WT_W
    # Root is blank; live is dark green; dead is red.
    assert _wt_cell(None).strip() == ""
    assert "dark_green" in _wt_cell("live") and "⎇" in _wt_cell("live")
    assert "red" in _wt_cell("dead") and "⎇" in _wt_cell("dead")


def test_row_label_includes_wt_glyph():
    from _pkg.tui import _row_label
    s = {"name_cached": "x", "last_active_at": None, "tokens_estimate": 0,
         "tokens_window_pct": 0, "message_count": 0, "first_prompt": ""}
    assert "⎇" in _row_label("sid", s, depth=2, wt_state="live")
    assert "⎇" in _row_label("sid", s, depth=2, wt_state="dead")
    assert "⎇" not in _row_label("sid", s, depth=2, wt_state=None)
    assert "⎇" not in _row_label("sid", s, depth=2)  # default = root
```

Also **update** the two existing alignment tests to account for the inserted `WT_W`-wide cell:

In `test_row_label_columns_align_across_depth` (line 380), change the imports and the final assertion:

```python
    from _pkg.tui import _row_label, _stat_suffix, _wt_cell, NAME_W, GUIDE_DEPTH, GLYPH_W
```
```python
    assert grouped[name_w_grouped:] == ungrouped[name_w_ungrouped:]
    assert grouped[name_w_grouped:] == _wt_cell(None) + _stat_suffix("—", "~0", "(0%)", "7", "msgs", "hello")
```

In `test_column_header_offset_matches_grouped_leaf` (line 400), change the imports and the final assertion:

```python
    from _pkg.tui import _column_header, _stat_suffix, _wt_cell, NAME_W, GUIDE_DEPTH, GLYPH_W
```
```python
    assert header[:name_region].strip() == "NAME"
    assert header[name_region:] == _wt_cell(None) + _stat_suffix("AGE", "~TOK", "CTX", "MSGS", "    ", "FIRST PROMPT")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py -k "wt_cell or wt_glyph or columns_align or column_header_offset" -q`
Expected: FAIL — `_wt_cell` import error for the new tests; the two updated tests fail on the changed assertion.

- [ ] **Step 3: Add constants and the WT renderers**

In `bin/_pkg/tui.py`, add to the constants block (after line 49, near the other glyph constants):

```python
WT_W = 4  # display width of the worktree-indicator column (after the name field)
WT_GLYPH = "⎇"  # marks a git-worktree session (blank = normal "root" checkout)
```

Add the renderers right after `_glyph` (after line 77), before `_stat_suffix`:

```python
def _wt_glyph(state: "str | None") -> str:
    """Inner markup for the worktree column: dark-green glyph for a live
    worktree, red for a deleted one, a single space for a root checkout. Always
    one display cell wide after markup is stripped. Pure for unit testing."""
    if state == "live":
        return f"[dark_green]{WT_GLYPH}[/]"
    if state == "dead":
        return f"[red]{WT_GLYPH}[/]"
    return " "


def _wt_cell(state: "str | None") -> str:
    """A WT_W-wide column cell: one space of gap, the worktree glyph, then
    padding out to WT_W. Inserted between the name field and the stat block."""
    return " " + _wt_glyph(state) + " " * (WT_W - 2)
```

- [ ] **Step 4: Insert the cell into `_row_label`**

Change the signature (line 86) to add a `wt_state` parameter:

```python
def _row_label(sid: str, s: dict, depth: int, glyph: str = "  ", wt_state: "str | None" = None) -> str:
```

And change the return (line 106) to insert the cell between the name field and the stat suffix:

```python
    return glyph + f"{display:<{name_w}}" + _wt_cell(wt_state) + _stat_suffix(age, tokens, pct, msgs, "msgs", prompt)
```

- [ ] **Step 5: Insert the cell into `_column_header`**

Change the return (line 114) to add `WT_W` blank cells in the same position (blank header):

```python
    return " " * GLYPH_W + f"{'NAME':<{name_region}}" + " " * WT_W + _stat_suffix("AGE", "~TOK", "CTX", "MSGS", "    ", "FIRST PROMPT")
```

- [ ] **Step 6: Run the targeted tests**

Run: `python3 -m pytest test/test_tui.py -k "wt_cell or wt_glyph or columns_align or column_header_offset or truncates" -q`
Expected: all pass (including the unchanged `test_long_name_truncates_to_field_width` — a root cell is blank so `row[GLYPH_W + NAME_W] == " "` still holds).

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): render worktree indicator column in rows and header"
```

---

## Task 3: Wire the column into the live tree (compute once, cache, preserve)

**Files:**
- Modify: `bin/_pkg/tui.py:887-894` (build), `1652-1658` (`_relabel_live_rows`), `1691-1703` (`_apply_live_metadata`), `1705-1717` (`_tick_spinner`)
- Test: `test/test_tui.py` (new `run_test` integration test)

- [ ] **Step 1: Write the failing integration test**

Append to `test/test_tui.py`:

```python
async def test_worktree_rows_show_glyph_in_tree(tmp_path):
    import json
    from _pkg.tui import SessionExplorerApp

    repo = tmp_path / "repo"
    live_wt = repo / ".claude" / "worktrees" / "alive"
    live_wt.mkdir(parents=True)
    dead_wt = repo / ".claude" / "worktrees" / "gone"  # never created -> dead

    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"version": 2, "sessions": {
        "root1": {"name_cached": "root-sesh", "project_path": str(repo),
                  "project_label": "repo", "last_active_at": None,
                  "tokens_estimate": 0, "tokens_window_pct": 0,
                  "message_count": 0, "first_prompt": ""},
        "live1": {"name_cached": "live-wt", "project_path": str(live_wt),
                  "project_label": "repo", "last_active_at": None,
                  "tokens_estimate": 0, "tokens_window_pct": 0,
                  "message_count": 0, "first_prompt": ""},
        "dead1": {"name_cached": "dead-wt", "project_path": str(dead_wt),
                  "project_label": "repo", "last_active_at": None,
                  "tokens_estimate": 0, "tokens_window_pct": 0,
                  "message_count": 0, "first_prompt": ""},
    }}))

    app = SessionExplorerApp(index_path=str(idx))
    async with app.run_test() as pilot:
        await pilot.pause()
        labels = {sid: str(leaf.label) for sid, (leaf, _d) in app._row_nodes.items()}
        # Worktree rows carry the glyph; the root row does not.
        assert "⎇" in labels["live1"]
        assert "⎇" in labels["dead1"]
        assert "⎇" not in labels["root1"]
        # The cached classification is stored on each leaf's data dict.
        assert app._row_nodes["live1"][0].data["worktree_state"] == "live"
        assert app._row_nodes["dead1"][0].data["worktree_state"] == "dead"
        assert app._row_nodes["root1"][0].data["worktree_state"] is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest test/test_tui.py -k worktree_rows_show_glyph -q`
Expected: FAIL — `KeyError: 'worktree_state'` (not yet stored / not yet rendered).

- [ ] **Step 3: Compute and store at build time**

In `bin/_pkg/tui.py`, in the inner `render` function (lines 888-894), compute the state once, store it in the leaf's `data`, and pass it to `_row_label`:

```python
            for sid, s in node["_sessions"]:
                if self._matches(sid, s):
                    glyph = _glyph(self._live_states.get(sid), self._spinner_frame,
                                   self._ours_flag(sid))
                    wt = _worktree_state(s.get("project_path"))
                    leaf = parent.add_leaf(_row_label(sid, s, child_depth, glyph, wt),
                                           data={"sid": sid, **s, "worktree_state": wt})
                    self._row_nodes[sid] = (leaf, child_depth)
```

- [ ] **Step 4: Pass the cached state on relabel and spinner ticks**

In `_relabel_live_rows` (line 1658), pass the stored state:

```python
            leaf.set_label(_row_label(sid, data, depth, glyph, data.get("worktree_state")))
```

In `_tick_spinner` (lines 1715-1717), pass it too:

```python
            leaf.set_label(_row_label(sid, leaf.data or {}, depth,
                                      _glyph(state, self._spinner_frame,
                                             self._ours_flag(sid)),
                                      (leaf.data or {}).get("worktree_state")))
```

- [ ] **Step 5: Preserve the cached state across live-metadata refresh**

In `_apply_live_metadata` (line 1701), the `leaf.data` dict is rebuilt from the index, which has no `worktree_state` key — re-merge the cached value (a live session can't change between worktree and root, so the build-time value is still correct):

```python
        for sid, (leaf, _depth) in self._row_nodes.items():
            if sid in self._live_states and sid in data:
                leaf.data = {"sid": sid, **data[sid],
                             "worktree_state": (leaf.data or {}).get("worktree_state")}
```

- [ ] **Step 6: Run the integration test**

Run: `python3 -m pytest test/test_tui.py -k worktree_rows_show_glyph -q`
Expected: PASS.

- [ ] **Step 7: Run the whole TUI suite to catch regressions**

Run: `python3 -m pytest test/test_tui.py test/test_tui_live.py -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): wire worktree indicator into the live tree with cached state"
```

---

## Task 4: Docs — help legend, SPEC, version bump, CHANGELOG

**Files:**
- Modify: `bin/_pkg/tui.py:223-230` (help-screen legend)
- Modify: `SPEC.md:143-153` (Stats columns section)
- Modify: `bin/_pkg/__init__.py`, `plugin.json` (version), `README.md` / `SPEC.md` status lines, `CHANGELOG.md` — via the `cutting-a-release` skill.

- [ ] **Step 1: Add a worktree note to the help screen**

In `bin/_pkg/tui.py`, in the `HelpScreen` body after the "Live sessions" block (after line 230), insert a new block:

```python
        "",
        "[b]Worktrees[/]",
        "A [dark_green]⎇[/] after the name marks a session running in a git",
        "worktree; it turns [red]⎇[/] if that worktree directory was deleted.",
        "Plain (no glyph) means a normal checkout. Updated on rescan ([b]F5[/]).",
```

(Confirm the exact rescan key in the existing help text; the legend lists `F5` at line 228. If the codebase uses a different rescan binding, match it.)

- [ ] **Step 2: Document the column in SPEC.md**

In `SPEC.md`, in the `### Stats columns` list (after the **Age** bullet, line 147), add:

```markdown
- **Worktree indicator.** A narrow column between the name and the age. `_worktree_state(project_path)` classifies each session: blank for a normal checkout, a dark-green `⎇` for a git worktree (`<repo>/.claude/worktrees/<name>`) whose directory still exists, and a red `⎇` for one whose directory was deleted. Deliberately separate from the left-column live glyph so the two are never confused. The `os.path.isdir` check runs once at tree-build time and is cached in the row's `worktree_state`, so a worktree deleted while the TUI is open turns red on the next rescan, not instantly.
```

- [ ] **Step 3: Verify the full suite still passes**

Run: `python3 -m pytest test/ -q`
Expected: all pass.

- [ ] **Step 4: Cut the release**

Invoke the `cutting-a-release` skill and follow its checklist (it is authoritative): bump `bin/_pkg/__init__.py` + `plugin.json` (a **minor** bump — this is a new feature), update the README/SPEC status lines and the help-screen keybindings note if they changed, add a `CHANGELOG.md` section describing the worktree indicator column, then `gh release create vX.Y.Z`.

- [ ] **Step 5: Final commit (non-release files, if any remain)**

```bash
git add bin/_pkg/tui.py SPEC.md
git commit -m "docs: document worktree indicator column"
```

(Release-file commits are handled by the `cutting-a-release` skill in Step 4.)

---

## Self-review notes

- **Spec coverage:** classifier (Task 1) ↔ spec §1; glyph/cell/column (Task 2) ↔ spec §2-3; cache + preserve across poll (Task 3) ↔ spec §4; tests (Tasks 1-3) ↔ spec §5; docs/release (Task 4) ↔ spec §6. All sections covered.
- **Type consistency:** `_worktree_state` returns `None | "live" | "dead"` everywhere; `_wt_glyph`/`_wt_cell`/`_row_label(wt_state=...)` all consume that same three-value domain; the cache key is `worktree_state` in every call site (build, relabel, tick, apply-metadata).
- **Colors:** `dark_green` and `red` are standard Rich color names — recolorable because `⎇` is a single-cell monochrome glyph (emoji were rejected in the spec for being fixed-color/double-width).
- **Alignment invariant:** `WT_W` is a constant inserted in both `_row_label` and `_column_header`, so the stat columns shift uniformly and stay aligned across depths; the two existing alignment tests are updated to include `_wt_cell(None)` rather than deleted.
