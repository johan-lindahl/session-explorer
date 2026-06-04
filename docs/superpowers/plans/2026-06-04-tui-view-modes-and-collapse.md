# TUI View Modes, Temp Sessions, Collapse & Select-on-Create — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five TUI quality-of-life tweaks — F2 rename alias, blank-name temporary sessions, a Tab-driven 3-mode view cycle (replacing `u`), select-the-new-session-on-create, and a `z` collapse-to-roots toggle with sticky drill-down.

**Architecture:** All changes live in the existing Textual app `bin/_pkg/tui.py`, with a backward-compatible new parameter on `bin/_pkg/tree_model.py:build_nested_tree` and a small conditional in `bin/_pkg/tmux.py:build_new_session_window`. No data-model changes. In-session UI state only (view mode, collapse mode, expanded set) — nothing persisted across restarts.

**Tech Stack:** Python 3.11+, Textual (vendored under `bin/_pkg/_vendor/`), pytest + pytest-asyncio (`asyncio_mode = auto`). Run tests with `python3 -m pytest test/ -q`.

**Reference spec:** `docs/superpowers/specs/2026-06-04-tui-view-modes-and-collapse-design.md`

**Conventions for this plan:**
- Tests live in `test/test_tui.py` (TUI + pure helpers) and `test/test_tree_model.py` (tree builder).
- TUI tests use the `index_path` fixture (`test/test_tui.py:6`) and the `_collect_leaf_sids` helper (`test/test_tui.py:287`).
- Commit after each task with the message shown.

---

## Task 1: F2 as a rename alias

**Files:**
- Modify: `bin/_pkg/tui.py` (BINDINGS list ~line 553)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_tui.py` (near the other rename tests):

```python
async def test_f2_opens_rename_dialog(index_path):
    from _pkg.tui import SessionExplorerApp, RenameScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # project node
        await pilot.press("down")  # folder node
        await pilot.press("down")  # session leaf
        await pilot.press("f2")
        await pilot.pause()
        assert isinstance(app.screen, RenameScreen)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py::test_f2_opens_rename_dialog -q`
Expected: FAIL — pressing `f2` does nothing, `app.screen` is the main app screen, not `RenameScreen`.

- [ ] **Step 3: Add the binding**

In `bin/_pkg/tui.py`, in the app `BINDINGS` list, immediately after the existing rename line:

```python
        Binding("r", "rename", "Rename"),
```

add:

```python
        Binding("f2", "rename", "Rename", key_display="F2", show=False),
```

(No change to `check_action` — it gates on the action name `"rename"`, already present.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py::test_f2_opens_rename_dialog -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): bind F2 as a rename alias"
```

---

## Task 2: `build_nested_tree` gains a `live_only` filter

**Files:**
- Modify: `bin/_pkg/tree_model.py:74-103` (`build_nested_tree`)
- Test: `test/test_tree_model.py`

This is the foundation for view mode 1 ("Active only").

- [ ] **Step 1: Write the failing tests**

Add to `test/test_tree_model.py` (after the existing live tests, ~line 285):

```python
def test_build_nested_tree_live_only_keeps_only_live_sessions():
    idx = {"sessions": {
        "named-live": {"project_label": "p", "name_cached": "feature"},
        "named-dead": {"project_label": "p", "name_cached": "other"},
        "unnamed-live": {"project_label": "p", "name_cached": None},
        "unnamed-dead": {"project_label": "p", "name_cached": None},
    }}
    t = build_nested_tree(idx, {"projects": {}}, live_only=True,
                          live_ids={"named-live", "unnamed-live"})
    flat = {sid for proj in t.values()
            for sid, _ in _all_sessions(proj)}
    assert flat == {"named-live", "unnamed-live"}


def test_build_nested_tree_live_only_empty_when_nothing_live():
    idx = {"sessions": {
        "a": {"project_label": "p", "name_cached": "x"},
    }}
    assert build_nested_tree(idx, {"projects": {}}, live_only=True,
                             live_ids=set()) == {}
```

If `_all_sessions` is not already a helper in the file, add this near the top of `test/test_tree_model.py` (after the imports):

```python
def _all_sessions(node):
    out = list(node["_sessions"])
    for child in node["_folders"].values():
        out.extend(_all_sessions(child))
    return out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tree_model.py::test_build_nested_tree_live_only_keeps_only_live_sessions test/test_tree_model.py::test_build_nested_tree_live_only_empty_when_nothing_live -q`
Expected: FAIL with `TypeError: build_nested_tree() got an unexpected keyword argument 'live_only'`.

- [ ] **Step 3: Implement `live_only`**

In `bin/_pkg/tree_model.py`, change the signature (line 74-76) to add the parameter:

```python
def build_nested_tree(index_data: dict, folder_store_data: dict,
                      include_unnamed: bool = False,
                      live_ids: "set[str] | None" = None,
                      live_only: bool = False) -> Dict[str, dict]:
```

Update the docstring's intro (after the existing live-sessions paragraph, before `live_ids = live_ids or set()`):

```python
    When `live_only` is True, only sessions whose sid is in `live_ids` are
    placed (named or unnamed); `include_unnamed` is ignored. This backs the
    TUI's "Active only" view mode.
```

In the placement loop (currently lines 92-103), replace the skip condition:

```python
    for sid, s in index_data.get("sessions", {}).items():
        name = s.get("name_cached")
        if live_only:
            if sid not in live_ids:
                continue
        elif not name and not include_unnamed and sid not in live_ids:
            continue
        project = s.get("project_label") or "(unknown)"
        proj_node = out.setdefault(project, _empty_node())
        if not name:
            target = proj_node["_folders"].setdefault("(unnamed)", _empty_node())
        else:
            segments, _ = split_path(name)
            target = _walk_to(proj_node, segments)
        target["_sessions"].append((sid, s))
```

Note: in `live_only` mode the stored-folder-paths loop (step 2 of the function, lines 105-110) would still lay in empty folder nodes. That is acceptable — empty folders render but hold no sessions, and the TUI's empty-state logic counts visible sessions, not folders. Leave that loop unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tree_model.py -q`
Expected: PASS (the two new tests plus all existing tree-model tests).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tree_model.py test/test_tree_model.py
git commit -m "feat(tree_model): add live_only filter to build_nested_tree"
```

---

## Task 3: Tab cycles three view modes (removes `u`)

**Files:**
- Modify: `bin/_pkg/tui.py` — state init (~586), BINDINGS (~559), `check_action` (~618), `_populate` (~787-807), `action_toggle_unnamed`→`action_cycle_view` (~1422), `_visibility_changed` (~1549), `_empty_state_text` (~175) + its call site (~816), `_help_text` (~216-217, ~254)
- Test: `test/test_tui.py`

### 3a — state, binding, action, populate, visibility

- [ ] **Step 1: Write the failing test**

Replace the existing `test_unnamed_hidden_by_default_toggle_with_u` (`test/test_tui.py:419`) with a Tab-driven version, and add an active-only test. First, delete the old test body and write:

```python
async def test_tab_cycles_view_modes(index_path):
    import json
    data = json.load(open(index_path))
    data["sessions"]["unnamed-xyz"] = {
        "project_label": "demo", "name_cached": None,
        "last_active_at": "2026-05-25T00:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Mode 0: named-only. sid-1 named shows, unnamed hidden, nothing live.
        assert app._view_mode == 0
        sids = _collect_leaf_sids(app._tree.root)
        assert "sid-1" in sids and "unnamed-xyz" not in sids

        # Mode 1: active only. Nothing is live -> no rows.
        await pilot.press("tab")
        await pilot.pause()
        assert app._view_mode == 1
        assert _collect_leaf_sids(app._tree.root) == set()

        # Mode 2: all. Both show.
        await pilot.press("tab")
        await pilot.pause()
        assert app._view_mode == 2
        sids = _collect_leaf_sids(app._tree.root)
        assert "sid-1" in sids and "unnamed-xyz" in sids

        # Back to mode 0.
        await pilot.press("tab")
        await pilot.pause()
        assert app._view_mode == 0
        assert "unnamed-xyz" not in _collect_leaf_sids(app._tree.root)


async def test_active_only_mode_shows_live_named_and_unnamed(index_path):
    import json
    data = json.load(open(index_path))
    data["sessions"]["unnamed-live"] = {
        "project_label": "demo", "name_cached": None,
        "last_active_at": "2026-05-25T00:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Pretend the unnamed session is live, the named sid-1 is not.
        app._live_states = {"unnamed-live": "idle"}
        await pilot.press("tab")  # -> mode 1 (active only)
        await pilot.pause()
        sids = _collect_leaf_sids(app._tree.root)
        assert sids == {"unnamed-live"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py::test_tab_cycles_view_modes test/test_tui.py::test_active_only_mode_shows_live_named_and_unnamed -q`
Expected: FAIL — `app._view_mode` does not exist (AttributeError).

- [ ] **Step 3: Implement the view-mode state machine**

In `bin/_pkg/tui.py`:

(a) Replace the state init (line 586):

```python
        self._show_unnamed: bool = False
```

with:

```python
        # Display mode cycled by Tab: 0 = named + active (default),
        # 1 = active only, 2 = all (incl. unnamed).
        self._view_mode: int = 0
```

(b) In `BINDINGS`, replace the `u` line (line 559):

```python
        Binding("u", "toggle_unnamed", "Toggle unnamed"),
```

with:

```python
        Binding("tab", "cycle_view", "Cycle view", key_display="Tab"),
```

(c) In `check_action` (line 618), replace `"toggle_unnamed"` with `"cycle_view"` in the tuple.

(d) Replace `action_toggle_unnamed` (lines 1422-1424):

```python
    def action_toggle_unnamed(self) -> None:
        self._show_unnamed = not self._show_unnamed
        self._populate()
```

with:

```python
    def action_cycle_view(self) -> None:
        self._view_mode = (self._view_mode + 1) % 3
        self._populate()
```

(e) In `_populate` (lines 787-807), replace the tree build + subtitle block. Replace:

```python
        tree = build_nested_tree(data, fs_data, include_unnamed=self._show_unnamed,
                                 live_ids=set(self._live_states))
        unnamed_hidden = 0
        if not self._show_unnamed:
            unnamed_hidden = sum(
                1 for s in data.get("sessions", {}).values() if not s.get("name_cached")
            )
```

with:

```python
        live_ids = set(self._live_states)
        tree = build_nested_tree(
            data, fs_data,
            include_unnamed=(self._view_mode == 2),
            live_ids=live_ids,
            live_only=(self._view_mode == 1),
        )
        # Only mode 0 hides unnamed stubs; surface the count so the subtitle and
        # empty-state can advertise the Tab cycle.
        unnamed_hidden = 0
        if self._view_mode == 0:
            unnamed_hidden = sum(
                1 for s in data.get("sessions", {}).values() if not s.get("name_cached")
            )
```

Then replace the subtitle block (the `if unnamed_hidden: ... else: ...` at lines 803-807):

```python
        if self._view_mode == 1:
            self.sub_title = f"Active only — {total} session(s){active_suffix} (Tab)"
        elif self._view_mode == 2:
            self.sub_title = (f"All sessions incl. unnamed — {total} across "
                              f"{len(tree)} projects{active_suffix} (Tab)")
        elif unnamed_hidden:
            self.sub_title = (f"{total} sessions across {len(tree)} projects · "
                              f"{unnamed_hidden} unnamed hidden (Tab){active_suffix}")
        else:
            self.sub_title = f"{total} sessions across {len(tree)} projects{active_suffix}"
```

(f) Update `_visibility_changed` (lines 1549-1564). Replace the body:

```python
    def _visibility_changed(self, old: dict, new: dict) -> bool:
        """True if any session whose membership depends on liveness flipped.

        Only unnamed sessions are conditionally visible, and only while not
        showing all unnamed. A named session is always present regardless of
        live state, so its appearance never forces a repopulate."""
        if self._show_unnamed:
            return False
        data = _index.load(self._index_path)
        sessions = data.get("sessions", {})
        flipped = set(old) ^ set(new)  # sids that entered or left the live set
        for sid in flipped:
            s = sessions.get(sid)
            if s is not None and not s.get("name_cached"):
                return True  # an unnamed session entered/left -> membership change
        return False
```

with:

```python
    def _visibility_changed(self, old: dict, new: dict) -> bool:
        """True if a live-set change alters which rows are visible.

        Mode 1 (active only) is purely liveness-driven, so any flip matters.
        Mode 2 (all) always shows every session, so liveness never changes
        membership. Mode 0 only conditionally shows *unnamed* live sessions, so
        only an unnamed flip matters (a named session is always present)."""
        if self._view_mode == 2:
            return False
        if self._view_mode == 1:
            return set(old) != set(new)
        data = _index.load(self._index_path)
        sessions = data.get("sessions", {})
        flipped = set(old) ^ set(new)  # sids that entered or left the live set
        for sid in flipped:
            s = sessions.get(sid)
            if s is not None and not s.get("name_cached"):
                return True  # an unnamed session entered/left -> membership change
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py::test_tab_cycles_view_modes test/test_tui.py::test_active_only_mode_shows_live_named_and_unnamed -q`
Expected: PASS

### 3b — empty-state and help text

- [ ] **Step 5: Update the empty-state tests and helper**

In `test/test_tui.py`, replace `test_empty_state_text_prompts_u_when_all_unnamed_hidden` (line 773) with:

```python
def test_empty_state_text_prompts_tab_when_all_unnamed_hidden():
    from _pkg.tui import _empty_state_text
    msg = _empty_state_text(total_indexed=5, visible=0, unnamed_hidden=5,
                            filter_active=False, scanned=False, view_mode=0)
    assert "Tab" in msg
    assert "5" in msg


def test_empty_state_text_active_only_when_nothing_live():
    from _pkg.tui import _empty_state_text
    msg = _empty_state_text(total_indexed=5, visible=0, unnamed_hidden=0,
                            filter_active=False, scanned=False, view_mode=1)
    assert "active" in msg.lower()
    assert "Tab" in msg
```

Also update `test_empty_state_text_filter_no_match_takes_precedence` (line 781) to pass `view_mode=0` explicitly (keyword is optional but keep the call current):

```python
    msg = _empty_state_text(total_indexed=5, visible=0, unnamed_hidden=2,
                            filter_active=True, scanned=False, view_mode=0)
```

And update the async `test_empty_state_shown_when_only_unnamed` (line 790): after the assertions that drive `u`, it presses `u` to hide — replace any `pilot.press("u")` there with `pilot.press("tab")` and adjust expectations to the new mode wording. Read the test body first; if it asserts `"Press u"` in the empty message, change it to assert `"Tab"`.

- [ ] **Step 6: Run the empty-state tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py::test_empty_state_text_prompts_tab_when_all_unnamed_hidden test/test_tui.py::test_empty_state_text_active_only_when_nothing_live -q`
Expected: FAIL — `_empty_state_text()` has no `view_mode` parameter (TypeError).

- [ ] **Step 7: Update `_empty_state_text` and its call site**

In `bin/_pkg/tui.py`, change the signature (line 175) and add the mode-1 branch:

```python
def _empty_state_text(total_indexed: int, visible: int, unnamed_hidden: int,
                      filter_active: bool, scanned: bool,
                      view_mode: int = 0) -> "str | None":
    """Message for the tree pane when no rows are visible, else None.

    Pure so it can be unit-tested. Branch order is deliberate: an active filter
    explains itself first (the user is mid-search), then the active-only mode,
    then hidden-unnamed, then the empty-index prompts — split by whether a
    rescan has already run, so a fruitless scan doesn't keep telling the user
    to "press F5"."""
    if visible > 0:
        return None
    if filter_active:
        return "No sessions match the current filter.\nPress Esc to clear it."
    if view_mode == 1:
        return "No active sessions right now.\nPress Tab to show all sessions."
    if unnamed_hidden > 0:
        return (f"{unnamed_hidden} unnamed session(s) hidden.\n"
                "Press Tab to cycle views, then r to name one.")
    if total_indexed == 0:
        if scanned:
            return "No sessions found under ~/.claude/projects/."
        return ("No sessions indexed yet.\n"
                "Press F5 to scan ~/.claude/projects/ for your sessions.")
    return None
```

Then update the call site in `_populate` (line 816-822) to pass the mode:

```python
        msg = _empty_state_text(
            total_indexed=len(data.get("sessions", {})),
            visible=visible,
            unnamed_hidden=unnamed_hidden,
            filter_active=bool(self._filter_needle),
            scanned=self._scanned,
            view_mode=self._view_mode,
        )
```

- [ ] **Step 8: Update help text**

In `bin/_pkg/tui.py` `_help_text()`:

Replace the "named-only visibility" paragraph (lines 216-217):

```python
        "Only named (renamed) sessions show by default. Unnamed stubs are hidden;",
        "press [b]u[/] to toggle them on so you can rename or delete them.",
```

with:

```python
        "Only named (renamed) sessions show by default. Press [b]Tab[/] to cycle",
        "the view: named+active → active only → all (incl. unnamed) → back.",
```

Replace the keybinding line (line 254):

```python
        key("u", "Toggle visibility of unnamed sessions"),
```

with:

```python
        key("Tab", "Cycle view: named+active → active only → all"),
        key("z", "Collapse the tree to project roots (toggle)"),
```

(The `z` line is added now so help is complete; Task 6 implements the binding.)

- [ ] **Step 9: Run the full TUI + tree suites**

Run: `python3 -m pytest test/test_tui.py test/test_tree_model.py -q`
Expected: PASS. If any other test still references `"u"`, `_show_unnamed`, `action_toggle_unnamed`, or `"Press u"`, update it to the new mode wording (search: `grep -rn '_show_unnamed\|toggle_unnamed\|press("u")\|Press u' test/`).

- [ ] **Step 10: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): Tab cycles 3 view modes (named+active / active / all), replacing u"
```

---

## Task 4: Blank-name `c` creates a temporary unnamed session

**Files:**
- Modify: `bin/_pkg/tui.py` — `_new_session_argv` (~1807), `action_new_session`'s `after` (~1240-1268)
- Modify: `bin/_pkg/tmux.py` — `build_new_session_window` (~66)
- Test: `test/test_tui.py`, `test/test_tmux.py`

### 4a — argv builders omit `-n` when name is empty

- [ ] **Step 1: Write the failing tests**

Add to `test/test_tui.py` (near the other `_new_session_argv` tests, ~line 1008):

```python
def test_new_session_argv_blank_name_omits_dash_n():
    from _pkg.tui import _new_session_argv
    assert _new_session_argv("sid-9", "") == ["claude", "--session-id", "sid-9"]


def test_new_session_argv_blank_name_with_worktree():
    from _pkg.tui import _new_session_argv
    assert _new_session_argv("sid-9", "", worktree="wt1") == [
        "claude", "--session-id", "sid-9", "-w", "wt1"]
```

Add to `test/test_tmux.py` (near the existing `build_new_session_window` tests — find them with `grep -n build_new_session_window test/test_tmux.py`):

```python
def test_build_new_session_window_blank_name_omits_dash_n():
    from _pkg.tmux import build_new_session_window
    argv = build_new_session_window("sid-9", "/tmp/p", "")
    # The inner `claude` command is the last element (a shlex-joined string).
    # The tmux window is still named with the sid via the new-window flags, but
    # the inner command must carry no `-n` (so claude starts unnamed).
    inner = argv[-1]
    assert "claude --session-id sid-9" in inner
    assert " -n " not in f" {inner} "


def test_build_new_session_window_named_still_has_dash_n():
    from _pkg.tmux import build_new_session_window
    inner = build_new_session_window("sid-9", "/tmp/p", "feature")[-1]
    assert "-n feature" in inner
```

Note: the tmux window itself is always named with the sid (`-n sid` in the `new-window` flags, which are separate earlier list elements); only the *inner* `claude` command string (`argv[-1]`) must drop `-n`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py::test_new_session_argv_blank_name_omits_dash_n test/test_tui.py::test_new_session_argv_blank_name_with_worktree test/test_tmux.py::test_build_new_session_window_blank_name_omits_dash_n -q`
Expected: FAIL — current builders always append `-n name` (so the inner string contains `--session-id sid-9 -n`).

- [ ] **Step 3: Implement conditional `-n`**

In `bin/_pkg/tui.py`, `_new_session_argv` (lines 1807-1816):

```python
def _new_session_argv(sid: str, name: str, worktree: "str | None" = None) -> list[str]:
    """argv for `os.execvp` to start a fresh session without tmux. A list (no
    shell), so the name needs no quoting. An empty `name` starts an unnamed
    (temporary) session — `claude` writes no custom-title, so it stays unnamed
    and is reaped by `--gc`. `worktree`: None → no `-w`; "" → bare `-w`;
    otherwise `-w <name>`."""
    argv = ["claude", "--session-id", sid]
    if name:
        argv += ["-n", name]
    if worktree is not None:
        argv.append("-w")
        if worktree:
            argv.append(worktree)
    return argv
```

In `bin/_pkg/tmux.py`, `build_new_session_window` (line 66), replace:

```python
    inner = ["exec", "claude", "--session-id", sid, "-n", name]
```

with:

```python
    inner = ["exec", "claude", "--session-id", sid]
    if name:
        inner += ["-n", name]
```

Also update that function's docstring sentence "`claude -n` writes the custom-title" to note: "An empty `name` omits `-n`, starting an unnamed (temporary) session."

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py::test_new_session_argv_blank_name_omits_dash_n test/test_tui.py::test_new_session_argv_blank_name_with_worktree test/test_tmux.py::test_build_new_session_window_blank_name_omits_dash_n -q`
Expected: PASS. Also re-run the existing argv tests to confirm no regression: `python3 -m pytest test/test_tui.py -k new_session_argv -q`.

### 4b — `after` callback allows the empty name

- [ ] **Step 5: Write the failing test**

Add to `test/test_tui.py` (near the other new-session / dock tests, e.g. after `test_enter_starts_and_docks_when_stopped`):

```python
async def test_blank_name_creates_unnamed_session_no_tmux(index_path, monkeypatch):
    """c with an empty name (no tmux) seeds nothing and execs claude without -n."""
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp, NewSessionScreen
    import _pkg.index as idxmod

    seeded = []
    monkeypatch.setattr(idxmod, "seed_new_session",
                        lambda *a, **k: seeded.append(a))

    app = SessionExplorerApp(index_path=index_path)  # tmux disabled by default
    async with app.run_test() as pilot:
        await pilot.pause()
        # Drive the new-session flow's callback directly with a blank name.
        app.action_new_session()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, NewSessionScreen)
        # Dismiss with an empty name (worktree off).
        screen.dismiss({"name": "", "cwd": "/tmp/demo-project",
                        "worktree": False, "worktree_name": ""})
        await pilot.pause()
    # No name was seeded, and the non-tmux launch argv carries no -n.
    assert seeded == []
    assert app._new_session_argv is not None
    assert "-n" not in app._new_session_argv
    assert app._new_session_argv[:3] == ["claude", "--session-id"] or \
        app._new_session_argv[0] == "claude"
```

Note: confirm the exact `NewSessionScreen` result dict keys by reading `action_new_session`'s `after` (`bin/_pkg/tui.py:1243-1248`) — it reads `result["name"]`, `result["cwd"]`, `result["worktree"]`, `result["worktree_name"]`. If `dismiss` cannot be called directly in the test harness, instead set the fields on the screen's inputs and press Enter, mirroring `test_new_folder_*` patterns. Prefer the direct `dismiss` form if the screen supports it.

- [ ] **Step 6: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py::test_blank_name_creates_unnamed_session_no_tmux -q`
Expected: FAIL — current `after` returns early on empty name, so `app._new_session_argv` stays `None`.

- [ ] **Step 7: Implement the blank-name path**

In `bin/_pkg/tui.py`, `action_new_session`'s `after` callback (lines 1240-1266), replace:

```python
        def after(result: "dict | None") -> None:
            if not result:
                return
            name = result["name"].strip()
            if not name:
                return
            cwd = result["cwd"].strip() or os.path.expanduser("~")
            # worktree tri-state: None (off), "" (bare -w), or a name (-w name).
            worktree = (result["worktree_name"] or "") if result["worktree"] else None
            sid = _new_sid()

            # Seed the chosen name now: claude writes no transcript (and thus no
            # custom-title) until the first turn, so without this the session
            # shows under (unnamed) until then. claude -n persists the identical
            # title later, so there's no divergence.
            _index.seed_new_session(self._index_path, sid, name, cwd)

            # No tmux → exit and execvp claude (handled in run()).
            if not self._tmux_enabled:
                self._new_session_argv = _new_session_argv(sid, name, worktree)
                self._new_session_cwd = cwd
                self.exit()
                return

            _, display = split_path(name)
            label = display or sid[:8]
            self._do_new_session(sid, cwd, name, worktree, label)
```

with:

```python
        def after(result: "dict | None") -> None:
            if not result:
                return
            name = result["name"].strip()
            cwd = result["cwd"].strip() or os.path.expanduser("~")
            # worktree tri-state: None (off), "" (bare -w), or a name (-w name).
            worktree = (result["worktree_name"] or "") if result["worktree"] else None
            sid = _new_sid()

            # A blank name starts a *temporary* unnamed session: claude writes no
            # custom-title, so it stays unnamed (hidden by default) and --gc reaps
            # it on the retention schedule. Don't seed a name in that case.
            if name:
                # Seed the chosen name now: claude writes no transcript (and thus
                # no custom-title) until the first turn, so without this the
                # session shows under (unnamed) until then. claude -n persists the
                # identical title later, so there's no divergence.
                _index.seed_new_session(self._index_path, sid, name, cwd)

            self._pending_select_sid = sid  # jump to it once its row exists

            # No tmux → exit and execvp claude (handled in run()).
            if not self._tmux_enabled:
                self._new_session_argv = _new_session_argv(sid, name, worktree)
                self._new_session_cwd = cwd
                self.exit()
                return

            _, display = split_path(name)
            label = display or sid[:8]
            self._do_new_session(sid, cwd, name, worktree, label)
```

(The `self._pending_select_sid = sid` line is added now; Task 5 initializes the attribute and consumes it. Setting it here before that attribute is initialized is fine — it is a plain instance assignment — but Task 5 adds the `__init__` default so reads elsewhere are safe.)

- [ ] **Step 8: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py::test_blank_name_creates_unnamed_session_no_tmux -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add bin/_pkg/tui.py bin/_pkg/tmux.py test/test_tui.py test/test_tmux.py
git commit -m "feat(tui): blank-name c starts a temporary unnamed session"
```

---

## Task 5: Select the newly created session in the tree

**Files:**
- Modify: `bin/_pkg/tui.py` — `__init__` (~597, add `_pending_select_sid`), end of `_populate` (~875, consume pending select), `_restore_cursor_to_sid` (~1527, optionally expand ancestors), `_do_new_session` (~1278)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_tui.py`:

```python
async def test_pending_select_moves_cursor_when_row_appears(index_path):
    """A pending-select sid jumps the cursor to that row on the next populate."""
    import json
    data = json.load(open(index_path))
    data["sessions"]["sid-2"] = {
        "project_label": "demo", "project_path": "/tmp/demo-project",
        "name_cached": "planning/another",
        "last_active_at": "2026-05-28T10:00:00Z",
        "tokens_estimate": 1, "tokens_window_pct": 1, "message_count": 1,
        "first_prompt": "hi",
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._pending_select_sid = "sid-2"
        app._populate()
        await pilot.pause()
        node = app._tree.cursor_node
        assert node is not None and node.data and node.data.get("sid") == "sid-2"
        # The flag clears after a successful select.
        assert app._pending_select_sid is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py::test_pending_select_moves_cursor_when_row_appears -q`
Expected: FAIL — `_pending_select_sid` is not an attribute (AttributeError) and nothing consumes it.

- [ ] **Step 3: Implement the pending-select attribute and consumption**

In `bin/_pkg/tui.py` `__init__`, after the `_row_nodes` init (line 597), add:

```python
        # sid to move the cursor to on the next populate where its row exists
        # (set after creating a new session). Cleared once honored.
        self._pending_select_sid: str | None = None
```

At the very end of `_populate` (after the `for project in sorted(tree): ... render(...)` loop, i.e. after line 875), add:

```python
        # Honor a pending select (e.g. just-created session) once its row exists.
        if self._pending_select_sid and self._pending_select_sid in self._row_nodes:
            self._restore_cursor_to_sid(self._pending_select_sid)
            self._pending_select_sid = None
```

In `_do_new_session` (lines 1270-1279), set the pending select before populating so the tmux path also benefits even though `after` already set it (idempotent — keep the single set in `after`). No change needed here if `after` already sets it; verify `_do_new_session` is only reached via `after`. (It is — confirm with `grep -n _do_new_session bin/_pkg/tui.py`. If another caller exists, add `self._pending_select_sid = sid` at the top of `_do_new_session`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py::test_pending_select_moves_cursor_when_row_appears -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): select the newly created session in the tree"
```

---

## Task 6: `z` toggles collapse-to-roots with sticky drill-down

**Files:**
- Modify: `bin/_pkg/tui.py` — `__init__` (~597, add `_collapse_mode` + `_expanded`), BINDINGS (~559), `check_action` (~618), `_populate` render loop (~849-875), new `action_toggle_collapse`, new `_node_key` helper + `on_tree_node_expanded`/`on_tree_node_collapsed` handlers, `_restore_cursor_to_sid` ancestor-expand (~1527)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_tui.py`:

```python
async def test_z_collapses_tree_to_project_roots(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Default: project nodes expanded.
        proj = app._tree.root.children[0]
        assert proj.is_expanded
        # z collapses to roots.
        await pilot.press("z")
        await pilot.pause()
        assert app._collapse_mode is True
        proj = app._tree.root.children[0]
        assert not proj.is_expanded
        # z again expands.
        await pilot.press("z")
        await pilot.pause()
        assert app._collapse_mode is False
        assert app._tree.root.children[0].is_expanded


async def test_collapse_mode_remembers_expanded_project_across_repopulate(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("z")  # collapse all
        await pilot.pause()
        # Mark the demo project as user-expanded, then repopulate.
        app._expanded.add("demo")
        app._populate()
        await pilot.pause()
        proj = app._tree.root.children[0]
        assert proj.is_expanded  # stuck open across the rebuild
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py::test_z_collapses_tree_to_project_roots test/test_tui.py::test_collapse_mode_remembers_expanded_project_across_repopulate -q`
Expected: FAIL — `_collapse_mode` / `_expanded` attributes do not exist.

- [ ] **Step 3: Implement collapse state, binding, action**

In `bin/_pkg/tui.py` `__init__`, after the `_pending_select_sid` init (added in Task 5), add:

```python
        # Collapse-to-roots view: when on, projects/folders render collapsed
        # except those the user has drilled into (tracked in _expanded by key).
        self._collapse_mode: bool = False
        self._expanded: set[str] = set()
```

In `BINDINGS`, after the `tab` line (added in Task 3), add:

```python
        Binding("z", "toggle_collapse", "Collapse tree"),
```

In `check_action` (line 618), add `"toggle_collapse"` to the tuple of gated actions.

Add a node-key helper and the toggle action near `action_cycle_view`:

```python
    @staticmethod
    def _node_key(project_label: str, segments: "list[str]") -> str:
        # \x00 cannot appear in a project label or folder segment, so it is a
        # safe separator for "<project>\x00<seg/seg>" folder keys.
        return project_label if not segments else \
            project_label + "\x00" + "/".join(segments)

    def action_toggle_collapse(self) -> None:
        self._collapse_mode = not self._collapse_mode
        if self._collapse_mode:
            self._expanded.clear()  # collapse everything to project roots
        self._populate()
```

Add Textual node expand/collapse handlers (they keep `_expanded` in sync; only meaningful while collapsed, harmless otherwise). Place them with the other event handlers:

```python
    def on_tree_node_expanded(self, event) -> None:
        data = getattr(event.node, "data", None) or {}
        if "project" in data:
            self._expanded.add(self._node_key(data["project"], data.get("segments") or []))

    def on_tree_node_collapsed(self, event) -> None:
        data = getattr(event.node, "data", None) or {}
        if "project" in data:
            self._expanded.discard(self._node_key(data["project"], data.get("segments") or []))
```

- [ ] **Step 4: Apply collapse state in the render loop**

In `_populate`'s `render` function and project loop (lines 849-875), make the `expand=` argument honor collapse mode.

For folder nodes (line 860-862), replace:

```python
                folder_node = parent.add(
                    f"{name}/", expand=True,
                    data={"project": project_label, "segments": child_segs},
                )
```

with:

```python
                fkey = self._node_key(project_label, child_segs)
                folder_node = parent.add(
                    f"{name}/",
                    expand=(not self._collapse_mode or fkey in self._expanded),
                    data={"project": project_label, "segments": child_segs},
                )
```

For project nodes (line 868-871), replace:

```python
            proj_node = root.add(
                f"{project} ({count(node)})", expand=True,
                data={"project": project, "segments": []},
            )
```

with:

```python
            proj_node = root.add(
                f"{project} ({count(node)})",
                expand=(not self._collapse_mode or project in self._expanded),
                data={"project": project, "segments": []},
            )
```

- [ ] **Step 5: Expand ancestors for a pending select under a collapsed branch**

So a just-created session under a collapsed project is reachable, update the pending-select consumption in `_populate` (the block added in Task 5) to open the target's ancestors first. Replace that block with:

```python
        # Honor a pending select (e.g. just-created session) once its row exists.
        if self._pending_select_sid and self._pending_select_sid in self._row_nodes:
            leaf = self._row_nodes[self._pending_select_sid][0]
            # Open every ancestor so the row is visible before moving the cursor.
            anc = leaf.parent
            while anc is not None and anc is not self._tree.root:
                anc.expand()
                d = anc.data or {}
                if "project" in d:
                    self._expanded.add(self._node_key(d["project"], d.get("segments") or []))
                anc = anc.parent
            self._restore_cursor_to_sid(self._pending_select_sid)
            self._pending_select_sid = None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py::test_z_collapses_tree_to_project_roots test/test_tui.py::test_collapse_mode_remembers_expanded_project_across_repopulate -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): z toggles collapse-to-roots with sticky drill-down"
```

---

## Task 7: Update SPEC.md and run the full suite

**Files:**
- Modify: `SPEC.md` (keybinding table + view-mode + temp-session + collapse + select-on-create notes)
- No new tests; this task verifies the whole change.

- [ ] **Step 1: Find the SPEC.md sections to update**

Run: `grep -n 'press u\|unnamed\|Toggle\|keybind\|F9\|F12\|new session\|Keys\|^| ' SPEC.md | head -40`
Identify (a) the TUI keybinding table/list, (b) the "unnamed hidden / press u" prose, (c) the new-session section.

- [ ] **Step 2: Edit SPEC.md**

Make these edits (match the file's existing table/prose style):
- Keybinding list: add `F2` (rename alias), `Tab` (cycle view: named+active → active only → all), `z` (collapse tree to project roots); remove the `u` row.
- Replace "unnamed sessions are hidden; press `u` to show them" wording with the three view-mode description (mode 0 named+active default, mode 1 active only, mode 2 all incl. unnamed).
- New-session section: note that leaving the name blank starts a *temporary unnamed* session — ephemeral, hidden by default, reaped by `--gc`; and that after creation the explorer selects the new session's row (tmux path).
- Add a one-line note that the collapse toggle's drill-down state is remembered across repopulates within a session (not persisted across restarts).

Keep the load-bearing decisions intact — do not change the "unnamed = not kept", rename-shadow, or liveness contracts.

- [ ] **Step 3: Run the full test suite**

Run: `python3 -m pytest test/ -q`
Expected: PASS (all suites). Investigate and fix any failure before continuing — in particular any lingering reference to `u`/`_show_unnamed`/`toggle_unnamed` in tests not covered above.

- [ ] **Step 4: Run the shell suite (sanity, unaffected but cheap)**

Run: `bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: PASS (these don't touch the TUI; this confirms nothing else broke).

- [ ] **Step 5: Commit**

```bash
git add SPEC.md
git commit -m "docs(spec): document view-mode cycle, temp sessions, collapse, select-on-create"
```

---

## Self-Review notes (for the implementer)

- **`NewSessionScreen` result keys:** Task 4 Step 5 assumes `{"name","cwd","worktree","worktree_name"}` — confirmed from `action_new_session`'s `after` at `bin/_pkg/tui.py:1243-1248`. If the screen's `dismiss` can't be invoked directly in the harness, fall back to setting input values and pressing Enter (mirror `test_new_folder_under_project_adds_to_folder_store`).
- **Textual event handler names:** `on_tree_node_expanded` / `on_tree_node_collapsed` correspond to `Tree.NodeExpanded` / `Tree.NodeCollapsed`. If the vendored Textual version posts differently, adjust the handler names to match (grep the vendored tree widget for `class NodeExpanded`).
- **`is_expanded` property:** Textual `TreeNode` exposes `is_expanded`. If the vendored version differs, the tests in Task 6 will surface it immediately — adjust the assertion to the available property.
- **Order matters:** Task 3 must land before Task 4/5/6 reference `self._pending_select_sid`/`action_cycle_view`, but each task's `__init__` additions are independent. Implement in the listed order.
