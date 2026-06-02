# Split-pane explorer + claude Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the windows-flipping tmux interaction with a two-pane split — explorer left, one active claude session right — where Enter docks a session beside the tree, F9 toggles focus, and F12 zooms the focused pane fullscreen.

**Architecture:** Keep the existing windows engine as the *substrate*. Inactive sessions stay as background `new-window -d` windows; the active one is `join-pane`d into the explorer window (window `explorer`) as a right-hand pane. Swapping breaks the current claude pane back out to a background window, then joins the next in. The explorer identifies "its own" pane via `$TMUX_PANE` and treats the *other* pane in its window as the docked claude. No-tmux fallback (`execvp`) is unchanged.

**Tech Stack:** Python 3.11+, vendored Textual TUI, tmux ≥3.0 (dedicated `-L session-explorer` server), pytest + pytest-asyncio. Pure `build_*` argv builders are unit-tested; thin executing wrappers are covered by mocked-tmux TUI tests.

Spec: `docs/superpowers/specs/2026-06-02-split-pane-explorer-claude-design.md`

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `bin/_pkg/tmux.py` | argv builders + thin wrappers for the dedicated tmux server | **Modify**: add dock/undock/list-panes/select-pane builders + wrappers; rewrite `build_config` (F9/F12 bindings, drop window tabs, new status-right) |
| `bin/_pkg/tui.py` | Textual app; resume/new-session actions; help text | **Modify**: dock-based `action_resume`, `action_new_session`, dead-worktree path; new `_self_pane`/`_docked_sid` state + `_dock`/`_undock_current` helpers; updated help text |
| `bin/_pkg/cli.py:195` | writes the generated tmux config | **No change** (calls `build_config(persist_flag_path=…)` with key defaults) |
| `test/test_tmux.py` | unit tests for builders + config | **Modify**: new builder tests; rewrite `build_config` assertions |
| `test/test_tui.py` | mocked-tmux TUI behavior tests | **Modify**: rewrite resume/double-click tests for docking |
| `SPEC.md` | authoritative architecture doc | **Modify**: rewrite the "tmux interaction layer" section |

---

## Task 1: tmux dock/undock/pane argv builders

Pure functions, unit-tested. These are the new primitives the docking layer is built from.

**Files:**
- Modify: `bin/_pkg/tmux.py` (add after `build_select_window`, ~line 82)
- Test: `test/test_tmux.py`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_tmux.py`:

```python
def test_build_dock_joins_session_into_explorer_on_the_right():
    # -h = horizontal split (side by side); source window `sid` becomes the
    # right pane of the `explorer` window. `-p 65` sizes the joined (claude)
    # pane to ~65% width.
    assert tmux.build_dock("sid-1") == [
        "tmux", "-L", "session-explorer",
        "join-pane", "-h", "-p", "65", "-s", "sid-1", "-t", "explorer"]


def test_build_dock_respects_custom_pct():
    assert tmux.build_dock("sid-1", pct=50)[-5:] == [
        "-p", "50", "-s", "sid-1", "-t", "explorer"]


def test_build_undock_breaks_pane_back_to_named_background_window():
    # -d keeps the broken-out window in the background; -n names it the sid so
    # session_windows()/reconciliation finds it again.
    assert tmux.build_undock("%7", "sid-1") == [
        "tmux", "-L", "session-explorer",
        "break-pane", "-d", "-s", "%7", "-n", "sid-1"]


def test_build_list_panes_lists_explorer_window_pane_ids():
    assert tmux.build_list_panes() == [
        "tmux", "-L", "session-explorer",
        "list-panes", "-t", "explorer", "-F", "#{pane_id}"]


def test_build_select_pane_targets_pane_id():
    assert tmux.build_select_pane("%7") == [
        "tmux", "-L", "session-explorer", "select-pane", "-t", "%7"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tmux.py -q -k "dock or list_panes or select_pane"`
Expected: FAIL with `AttributeError: module '_pkg.tmux' has no attribute 'build_dock'`

- [ ] **Step 3: Implement the builders**

In `bin/_pkg/tmux.py`, add a `DOCK_PCT` constant near the top (after `EXPLORER_WINDOW = "explorer"`, ~line 20):

```python
DOCK_PCT = 65  # claude pane width when docked beside the explorer tree
```

Add these functions after `build_select_window` (~line 82):

```python
def build_dock(sid: str, pct: int = DOCK_PCT) -> List[str]:
    """Join the background window `sid` into the explorer window as a right-hand
    pane. `-h` makes the split horizontal (side by side); the joined (claude)
    pane lands on the right at ~`pct`% width. `-p` (percentage) is used rather
    than `-l <n>%` because the `%` suffix on `-l` requires tmux 3.1 while our
    floor is 3.0."""
    return build_base() + [
        "join-pane", "-h", "-p", str(pct), "-s", sid, "-t", EXPLORER_WINDOW]


def build_undock(pane_id: str, sid: str) -> List[str]:
    """Break the docked claude pane back out into its own background window
    (named `sid` so reconciliation finds it). `-d` keeps it off-screen."""
    return build_base() + ["break-pane", "-d", "-s", pane_id, "-n", sid]


def build_list_panes() -> List[str]:
    return build_base() + [
        "list-panes", "-t", EXPLORER_WINDOW, "-F", "#{pane_id}"]


def build_select_pane(pane_id: str) -> List[str]:
    return build_base() + ["select-pane", "-t", pane_id]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tmux.py -q -k "dock or list_panes or select_pane"`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): dock/undock/list-panes/select-pane argv builders"
```

---

## Task 2: Rewrite `build_config` for the split layout

Replace the F12-back-to-explorer binding and window-tab status bar with F9 (switch focus) + F12 (zoom) bindings and a hint-only status line.

**Files:**
- Modify: `bin/_pkg/tmux.py:106-143` (`build_config`)
- Test: `test/test_tmux.py:62-87`

- [ ] **Step 1: Rewrite the failing tests**

Replace `test_build_config_contains_core_settings` and `test_build_config_respects_custom_back_key` in `test/test_tmux.py` with:

```python
def test_build_config_contains_core_settings():
    conf = tmux.build_config(persist_flag_path="/tmp/se.flag")
    assert "set -g mouse on" in conf
    assert "set -g status on" in conf
    # remain-on-exit must NOT be set — exited claude panes auto-close so the
    # explorer reclaims the full width.
    assert "remain-on-exit" not in conf
    # F9 switches focus between the two panes; F12 zooms the focused pane.
    assert "bind -n F9 select-pane -t :.+" in conf
    assert "bind -n F12 resize-pane -Z" in conf
    # Window tabs are gone — the explorer tree is the only session switcher.
    assert 'window-status-format ""' in conf
    assert 'window-status-current-format ""' in conf
    # Status-right advertises both keys (always visible, incl. when zoomed).
    assert "F9" in conf and "F12" in conf
    assert "switch" in conf and "full" in conf
    # Option C: kill the server on detach unless the persist-flag is present.
    assert "client-detached" in conf
    assert "/tmp/se.flag" in conf
    assert "kill-server" in conf


def test_build_config_respects_custom_keys():
    conf = tmux.build_config(persist_flag_path="/tmp/f",
                             switch_key="C-g", zoom_key="C-f")
    assert "bind -n C-g select-pane -t :.+" in conf
    assert "bind -n C-f resize-pane -Z" in conf
    assert "C-g" in conf and "C-f" in conf
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tmux.py -q -k build_config`
Expected: FAIL (asserts on `select-pane`/`resize-pane`/`window-status-format ""` not yet present)

- [ ] **Step 3: Rewrite `build_config`**

Replace `bin/_pkg/tmux.py:106-143` with:

```python
def build_config(*, persist_flag_path: str, switch_key: str = "F9",
                 zoom_key: str = "F12", socket: str = SOCKET) -> str:
    """tmux config for the dedicated server. Self-contained; never touches the
    user's ~/.tmux.conf. The split-pane layout (spec
    2026-06-02-split-pane-explorer-claude): the explorer is the left pane and the
    active claude session is docked as a right pane. `switch_key` flips focus
    between the two panes; `zoom_key` toggles the focused pane fullscreen. The
    client-detached hook implements Option C: an abrupt window close (no
    persist-flag) kills the server; a deliberate detach that first touched the
    flag is left to persist."""
    detach_hook = (
        f"set-hook -g client-detached "
        f"'run-shell -b \"if [ ! -f {persist_flag_path} ]; then "
        f"tmux -L {socket} kill-server; fi\"'"
    )
    # Hints live in the status line so they survive the zoomed-fullscreen case
    # (where the Textual footer is hidden). Always shown — there is effectively
    # one window now, so no per-window suppression.
    hint = (f"#[fg=black,bg=green] {switch_key} ⇄ switch "
            f"· {zoom_key} ⤢ full #[default]")
    return "\n".join([
        "set -g mouse on",
        "set -g status on",
        'set -g status-left ""',
        # No window-tab list: sessions are panes/background windows, not
        # user-facing window tabs. The explorer tree is the only switcher.
        'set -g window-status-format ""',
        'set -g window-status-current-format ""',
        f'set -g status-right "{hint}"',
        "set -g status-right-length 40",
        # No `remain-on-exit`: when claude exits its pane closes and the
        # explorer reclaims the full width.
        f"bind -n {switch_key} select-pane -t :.+",
        f"bind -n {zoom_key} resize-pane -Z",
        detach_hook,
        "",
    ])
```

Also delete the now-unused `win_fmt`/`status_right` helper lines and the
`@se_label` comment block they belonged to (old lines 117-128) — they are
replaced by the body above. Leave `build_set_label`/`build_select_window`
themselves in place (still imported elsewhere; harmless).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: PASS (all tmux tests, including the two rewritten ones)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): split-pane config — F9 switch, F12 zoom, no window tabs"
```

---

## Task 3: tmux executing wrappers for docking

Thin subprocess wrappers + one pure-ish helper (`docked_pane`) that is unit-testable via an injected lister.

**Files:**
- Modify: `bin/_pkg/tmux.py` (add after `select_window`, ~line 200)
- Test: `test/test_tmux.py`

- [ ] **Step 1: Write the failing test for `docked_pane`**

Add to `test/test_tmux.py`:

```python
def test_docked_pane_returns_the_pane_that_is_not_the_explorer():
    # list_panes returns both panes; the explorer's own pane id ($TMUX_PANE)
    # is filtered out, leaving the docked claude pane.
    panes = lambda: ["%0", "%3"]
    assert tmux.docked_pane("%0", _list=panes) == "%3"


def test_docked_pane_returns_none_when_only_explorer_pane():
    panes = lambda: ["%0"]
    assert tmux.docked_pane("%0", _list=panes) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tmux.py -q -k docked_pane`
Expected: FAIL with `AttributeError: module '_pkg.tmux' has no attribute 'docked_pane'`

- [ ] **Step 3: Implement the wrappers**

In `bin/_pkg/tmux.py`, add after `select_window` (~line 200):

```python
def dock(sid: str, pct: int = DOCK_PCT) -> int:
    """Join the background window `sid` into the explorer window as the right
    pane. join-pane consumes the source window and focuses the joined pane, so
    the user lands in claude ready to type."""
    return _call(build_dock(sid, pct))


def undock(pane_id: str, sid: str) -> int:
    return _call(build_undock(pane_id, sid))


def list_panes() -> List[str]:
    out = _capture(build_list_panes())
    return [ln for ln in out.splitlines() if ln]


def docked_pane(self_pane: "str | None",
                _list: Callable[[], List[str]] = list_panes) -> "str | None":
    """The id of the docked claude pane: the one pane in the explorer window
    that is NOT the explorer's own pane (`self_pane`, from $TMUX_PANE).
    Returns None when nothing is docked."""
    for p in _list():
        if p != self_pane:
            return p
    return None


def select_pane(pane_id: str) -> int:
    return _call(build_select_pane(pane_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tmux.py -q -k docked_pane`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): dock/undock/docked_pane/select_pane executing wrappers"
```

---

## Task 4: TUI docking state + helpers

Add `_self_pane` / `_docked_sid` state and the `_undock_current` / `_dock` helpers that orchestrate the wrappers. No behavior wired to keys yet — that's Task 5.

**Files:**
- Modify: `bin/_pkg/tui.py:567-592` (`__init__`) and add helpers near `action_resume` (~line 864)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_tui.py`:

```python
async def test_dock_helper_swaps_docked_session(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = []
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "docked_pane", lambda self_pane: "%9")
    monkeypatch.setattr(tuimod._tmux, "undock",
                        lambda pane, sid: calls.append(("undock", pane, sid)) or 0)
    monkeypatch.setattr(tuimod._tmux, "start_window",
                        lambda sid, cwd, label=None: calls.append(("start", sid)) or 0)
    monkeypatch.setattr(tuimod._tmux, "dock",
                        lambda sid: calls.append(("dock", sid)) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._docked_sid = "old"                         # something already docked
        app._dock("new", "/proj", "New", already_running=False)
    assert calls == [("undock", "%9", "old"), ("start", "new"), ("dock", "new")]
    assert app._docked_sid == "new"


async def test_dock_helper_refocuses_when_same_session(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = []
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "docked_pane", lambda self_pane: "%9")
    monkeypatch.setattr(tuimod._tmux, "select_pane",
                        lambda pane: calls.append(("select", pane)) or 0)
    monkeypatch.setattr(tuimod._tmux, "dock",
                        lambda sid: calls.append(("dock", sid)) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._docked_sid = "same"
        app._dock("same", None, None, already_running=True)
    assert calls == [("select", "%9")]                  # refocus only, no re-dock
    assert app._docked_sid == "same"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k dock_helper`
Expected: FAIL with `AttributeError: 'SessionExplorerApp' object has no attribute '_dock'`

- [ ] **Step 3: Add state + helpers**

In `bin/_pkg/tui.py` `__init__`, after `self._resume_cwd: str | None = None` (line 575), add:

```python
        # Split-pane docking (spec 2026-06-02-split-pane-explorer-claude):
        # our own tmux pane id (from $TMUX_PANE), and the sid currently docked
        # as the right pane (None when only the explorer is shown).
        self._self_pane: str | None = os.environ.get("TMUX_PANE")
        self._docked_sid: str | None = None
```

Add these two methods immediately before `action_resume` (~line 864):

```python
    def _undock_current(self) -> None:
        """Break the docked claude pane back out to a background window so it
        keeps running off-screen. No-op when nothing is docked."""
        if not self._docked_sid:
            return
        pane = _tmux.docked_pane(self._self_pane)
        if pane:
            _tmux.undock(pane, self._docked_sid)
        self._docked_sid = None

    def _dock(self, sid: str, cwd: "str | None", label: "str | None",
              *, already_running: bool) -> None:
        """Make `sid` the docked right pane. If it is already docked, just
        refocus it; otherwise undock whatever is docked, (re)start the session
        as a background window when needed, and join it in."""
        if self._docked_sid == sid:
            pane = _tmux.docked_pane(self._self_pane)
            if pane:
                _tmux.select_pane(pane)            # refocus claude
            return
        self._undock_current()
        if not already_running:
            _tmux.start_window(sid, cwd, label)    # background window first
        _tmux.dock(sid)                            # join into the explorer
        self._docked_sid = sid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -q -k dock_helper`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): docking state and _dock/_undock_current helpers"
```

---

## Task 5: Wire `action_resume` to docking

Replace the `select_window`/`start_window`+`select_window` logic with docking. The docked session is tracked by `_docked_sid` (it is a pane, so it no longer appears in `session_windows()`).

**Files:**
- Modify: `bin/_pkg/tui.py:880-907` (the tmux branch of `action_resume`)
- Test: `test/test_tui.py:1452-1539` (rewrite three existing tests)

- [ ] **Step 1: Rewrite the failing tests**

Replace `test_enter_starts_and_switches_when_stopped`, `test_double_click_resumes_like_enter`, and `test_enter_flips_into_running_window` in `test/test_tui.py` with:

```python
async def test_enter_starts_and_docks_when_stopped(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: [])   # nothing running
    monkeypatch.setattr(tuimod._tmux, "docked_pane", lambda self_pane: None)
    monkeypatch.setattr(tuimod._tmux, "start_window",
                        lambda sid, cwd, label=None: calls.setdefault("start", (sid, cwd, label)) or 0)
    monkeypatch.setattr(tuimod._tmux, "dock",
                        lambda sid: calls.setdefault("dock", sid) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")            # project node
        await pilot.press("down")            # folder node
        await pilot.press("down")            # session leaf (sid-1)
        await pilot.press("enter")
        await pilot.pause()
    assert calls["start"][0] == "sid-1"      # started the session as a window
    assert calls["start"][2] == "sprint14"   # human label (name_cached planning/sprint14)
    assert calls["dock"] == "sid-1"          # docked it beside the tree
    assert app._docked_sid == "sid-1"
    assert app._resume_target is None        # did NOT exit-to-resume


async def test_double_click_docks_like_enter(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: [])
    monkeypatch.setattr(tuimod._tmux, "docked_pane", lambda self_pane: None)
    monkeypatch.setattr(tuimod._tmux, "start_window",
                        lambda sid, cwd, label=None: calls.setdefault("start", sid) or 0)
    monkeypatch.setattr(tuimod._tmux, "dock",
                        lambda sid: calls.setdefault("dock", sid) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("down")            # session leaf (sid-1)

        class _Click:
            widget = app._tree
            chain = 2
        class _Single(_Click):
            chain = 1
        app.on_click(_Single())
        assert "start" not in calls          # single click does NOT resume
        app.on_click(_Click())               # double click docks, like Enter
        await pilot.pause()
    assert calls.get("start") == "sid-1"
    assert calls.get("dock") == "sid-1"


async def test_enter_docks_a_running_background_session(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: ["sid-1"])  # already a window
    monkeypatch.setattr(tuimod._tmux, "docked_pane", lambda self_pane: None)
    monkeypatch.setattr(tuimod._tmux, "start_window",
                        lambda sid, cwd, label=None: calls.setdefault("start", sid) or 0)
    monkeypatch.setattr(tuimod._tmux, "dock",
                        lambda sid: calls.setdefault("dock", sid) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("down")            # session leaf (sid-1)
        await pilot.press("enter")
        await pilot.pause()
    assert "start" not in calls              # already running → no re-start
    assert calls["dock"] == "sid-1"          # docked the existing window
    assert app._docked_sid == "sid-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py -q -k "docks or starts_and_docks"`
Expected: FAIL (current `action_resume` calls `select_window`, not `dock`)

- [ ] **Step 3: Rewrite the tmux branch of `action_resume`**

Replace `bin/_pkg/tui.py:880-907` (everything from `running = _tmux.session_windows()` to the end of the method) with:

```python
        running = _tmux.session_windows()
        # Already docked, or a running background window → (re)dock it. _dock
        # refocuses if it is the current dock, else undocks-current and joins.
        if sid == self._docked_sid or sid in running:
            self._dock(sid, None, label, already_running=True)
            self._poll_live()
            return
        if sid in self._live_states:
            # Live in another terminal, not one of ours: never start a second
            # claude on the same transcript (spec §5).
            self.push_screen(ConfirmScreen(
                "This session is already running in another terminal.\n"
                "Showing its progress here; press space to peek. (y/esc)"))
            return
        # Stopped → start it as a background window and dock it beside the tree.
        if _dead_worktree_repo(project_path):
            def after(ok: bool) -> None:
                if ok:
                    cwd = _resolve_resume_cwd(project_path) or os.path.expanduser("~")
                    self._dock(sid, cwd, label, already_running=False)
                    self._poll_live()
            self.push_screen(ConfirmScreen(
                "This session is from a deleted git worktree.\n"
                "Resume anyway? This re-creates an empty directory:\n"
                f"{project_path}"), after)
        else:
            cwd = _resolve_resume_cwd(project_path) or os.path.expanduser("~")
            self._dock(sid, cwd, label, already_running=False)
            self._poll_live()
```

- [ ] **Step 4: Run the full TUI suite to verify pass**

Run: `python3 -m pytest test/test_tui.py -q`
Expected: PASS (the three rewritten tests plus all others)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): Enter docks a session into the split instead of flipping windows"
```

---

## Task 6: Dock new sessions (`c`) into the split

`action_new_session` currently `select_window`s into the new window; make it dock instead, consistent with resume.

**Files:**
- Modify: `bin/_pkg/tui.py:1169-1172`
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_tui.py`:

```python
async def test_new_session_docks_into_the_split(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "docked_pane", lambda self_pane: None)
    monkeypatch.setattr(tuimod._tmux, "start_new_session_window",
                        lambda sid, cwd, name, worktree, label=None:
                            calls.setdefault("new", (sid, name)) or 0)
    monkeypatch.setattr(tuimod._tmux, "dock",
                        lambda sid: calls.setdefault("dock", sid) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Drive the new-session callback directly with a known argv tuple.
        app._do_new_session("sid-new", "/proj", "feat/x", None, "feat/x")
    assert calls["new"][0] == "sid-new"
    assert calls["dock"] == "sid-new"
    assert app._docked_sid == "sid-new"
```

Note: the test calls `_do_new_session`, the extracted helper introduced in Step 3. If the existing new-session callback has a different name/shape, adapt the test to call the same helper the dialog callback invokes.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k new_session_docks`
Expected: FAIL (`start_new_session_window` still followed by `select_window`, or `_do_new_session` not defined)

- [ ] **Step 3: Make new-session dock**

Read `bin/_pkg/tui.py:1136-1172` first to see the exact callback shape. In the new-session completion path, replace the pair:

```python
            _tmux.start_new_session_window(sid, cwd, name, worktree, label)
            _tmux.select_window(sid)   # land straight in the new session
```

with a dock-based version. Extract the body into a helper so it is unit-testable, and have the dialog callback call it:

```python
    def _do_new_session(self, sid: str, cwd: str, name: str,
                        worktree: "str | None", label: "str | None") -> None:
        self._undock_current()
        _tmux.start_new_session_window(sid, cwd, name, worktree, label)
        _tmux.dock(sid)
        self._docked_sid = sid
        self._poll_live()
```

Then in the existing callback, replace the two lines above with:

```python
            self._do_new_session(sid, cwd, name, worktree, label)
```

Keep the surrounding `self._poll_live()` only once (it now lives in `_do_new_session`; remove any duplicate that followed the old `select_window`).

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m pytest test/test_tui.py -q -k "new_session"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): new sessions (c) dock into the split"
```

---

## Task 7: Update help text for F9/F12

The help overlay still describes the old window-flipping model. Rewrite it for the split, and advertise F9 (switch) and F12 (fullscreen) the way F12 was advertised before.

**Files:**
- Modify: `bin/_pkg/tui.py:224-241` (`_help_text`)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_tui.py`:

```python
def test_help_text_describes_split_pane_keys():
    from _pkg.tui import _help_text
    txt = _help_text()
    assert "F9" in txt and "F12" in txt
    # F9 switches panes; F12 zooms claude fullscreen.
    assert "switch" in txt.lower() or "focus" in txt.lower()
    assert "fullscreen" in txt.lower() or "full screen" in txt.lower()
    # The old window-tab wording is gone.
    assert "explorer tab" not in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k help_text_describes_split`
Expected: FAIL (`assert "explorer tab" not in txt` — current text mentions the tab)

- [ ] **Step 3: Update the help text**

In `bin/_pkg/tui.py`, replace the block at lines 224-231:

```python
        "[b]Running sessions in tmux[/]",
        "When launched with tmux, the explorer stays open and each session you",
        "resume runs in its own tmux window:",
        "  • [b]Enter[/] (or double-click) starts a stopped session and switches you",
        "    in; on a running session it flips you straight into it.",
        "  • [b]F12[/] (or click the [b]explorer[/] tab in the bottom bar) returns",
        "    to the tree; the session keeps running in the background.",
        "  • [b]Space[/] peeks a live snapshot without leaving the explorer.",
```

with:

```python
        "[b]Running sessions in tmux[/]",
        "When launched with tmux, the explorer stays in the left pane and the",
        "session you resume docks in a pane on the right:",
        "  • [b]Enter[/] (or double-click) docks a session beside the tree and",
        "    puts you in it; Enter on another session swaps it in (the previous",
        "    one keeps running in the background).",
        "  • [b]F9[/] (or click a pane) switches focus between tree and session.",
        "  • [b]F12[/] zooms the focused pane fullscreen; press again to restore.",
        "  • [b]Space[/] peeks a live snapshot of any session without docking it.",
```

And replace the key line at 241:

```python
        key("F12", "Return to the explorer from inside a session (tmux)"),
```

with:

```python
        key("F9", "Switch focus between the explorer tree and the session"),
        key("F12", "Zoom the focused pane fullscreen (toggle)"),
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m pytest test/test_tui.py -q -k help_text`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "docs(tui): help text describes the split-pane F9/F12 model"
```

---

## Task 8: Update SPEC.md

Per CLAUDE.md, the spec and code change together. Rewrite the "tmux interaction layer" section (`SPEC.md:389+`) to describe the split-pane model.

**Files:**
- Modify: `SPEC.md` (the "tmux interaction layer" section, ~389-476)

- [ ] **Step 1: Read the current section**

Run: `sed -n '389,476p' SPEC.md`
Expected: the existing windows-flipping description (window 0 + sibling windows, F12 back-to-explorer, clickable tabs).

- [ ] **Step 2: Rewrite the section**

Update the section so it states:
- The explorer is the **left pane**; the active session docks as the **right pane** of the explorer window via `join-pane -h`. Inactive sessions remain as background windows; swapping uses `break-pane -d` then `join-pane`.
- **Enter** docks + focuses the session (swapping out the previous one). **F9** toggles focus between panes (also mouse-click). **F12** zooms the focused pane fullscreen (`resize-pane -Z`). No window-tab status bar; hints (`F9 ⇄ switch · F12 ⤢ full`) live in the status-right so they survive the zoomed case.
- The explorer identifies its own pane via `$TMUX_PANE`; the *other* pane in its window is the docked claude (`docked_pane`).
- Unchanged: no-tmux `execvp` fallback, liveness/snapshots/reconciliation (operate on background windows), Option-C client-detached persist logic, the "already live elsewhere → refuse + peek" guard.
- Point the "Full design rationale" line at `docs/superpowers/specs/2026-06-02-split-pane-explorer-claude-design.md`.

Update the config table near the bottom (`SPEC.md:467+`) entries that mention F12 / `build_select_window` to the new wrappers (`build_dock`/`build_undock`/`docked_pane`/`select_pane`) and the F9/F12 bindings.

Also update CLAUDE.md's "Resume is non-destructive when tmux-hosted" bullet so it reads "docks as a right pane" rather than "runs as sibling windows".

- [ ] **Step 3: Sanity-check the docs build/readability**

Run: `grep -n "split-pane\|join-pane\|F9\|resize-pane -Z" SPEC.md`
Expected: the new wording is present and the old `select-window -t explorer` / "sibling window" phrasing for resume is gone.

- [ ] **Step 4: Commit**

```bash
git add SPEC.md CLAUDE.md
git commit -m "docs: SPEC/CLAUDE describe the split-pane tmux interaction"
```

---

## Task 9: Full suite + manual churn check

**Files:** none (verification only)

- [ ] **Step 1: Run the full Python suite**

Run: `python3 -m pytest test/ -q`
Expected: PASS (all tests green)

- [ ] **Step 2: Run the shell suite**

Run: `bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: PASS (these don't exercise the TUI but confirm nothing else regressed)

- [ ] **Step 3: Manual smoke test (the novel re-parent path)**

This is the risk the branch exists to evaluate (spec "Risk to exercise"). With tmux installed:
1. `pkill -f "session-explorer tui"` first (avoid a stale orphan clobbering the index).
2. Launch the explorer via the plugin `/open` (or `bin/session-explorer tui` inside the dedicated server).
3. Enter a session → confirm it docks on the right and you land in claude.
4. F9 → focus returns to the tree; F9 again → back to claude. Click each pane → focus follows.
5. F12 in the claude pane → fullscreen; F12 → restored split. Repeat rapidly 10× while claude is mid-render (paste a prompt that streams) to stress the re-parent/SIGWINCH path.
6. Enter a *different* session → previous breaks out to background (still running), new one docks.
7. Let a docked claude exit (`/exit`) → its pane closes and the explorer reclaims full width.
8. `q` with a docked session → confirm shutdown/persist prompt still behaves.

Record any fl__icker/corruption in the docked claude during rapid F12/swap churn; if present, apply a debounce or the spare-hidden-pane fallback noted in the spec.

- [ ] **Step 4: Commit nothing / open PR**

If all green and the manual churn check is clean, the branch is ready for review:

```bash
git push -u origin feat/split-pane-explorer
gh pr create --fill
```

---

## Self-review

**Spec coverage:**
- Docking layer over windows substrate → Tasks 1, 3, 4, 5.
- Enter docks + focuses; swap breaks out previous → Task 5 (`_dock`/`_undock_current`).
- No session → explorer fills width (claude pane closes on exit, `remain-on-exit` off) → Task 2 (config) + verified in Task 9 step 7.
- F9 switch (+ mouse), F12 zoom → Task 2 (bindings) + Task 7 (help) + Task 9 (manual).
- Drop window-tab list; hints in status-right (survive zoom) → Task 2.
- Textual footer/help advertises F9 + F12 → Task 7.
- Untouched: no-tmux execvp, liveness/snapshots, `c` flow (now docks), quit/persist, live-elsewhere guard → Task 5 (guard kept), Task 6 (`c`), Task 8 (documented), Task 9 (verified).
- Pane identification via `$TMUX_PANE` / `docked_pane` → Tasks 3, 4.
- SPEC.md updated alongside code → Task 8.
- Re-parent churn risk exercised → Task 9 step 3.

**Placeholder scan:** No TBD/TODO; every code step shows complete code. Task 6 step 1 carries an explicit note to adapt to the real callback shape after reading lines 1136-1172 (the one place the exact existing structure must be confirmed in-editor).

**Type/name consistency:** `build_dock`/`dock`, `build_undock`/`undock`, `build_list_panes`/`list_panes`, `docked_pane`, `build_select_pane`/`select_pane`, `_dock`, `_undock_current`, `_do_new_session`, `_self_pane`, `_docked_sid`, `DOCK_PCT` used consistently across tasks. `build_config(switch_key=, zoom_key=)` matches the cli.py caller (defaults preserved, no call-site change needed). The `_dock(sid, cwd, label, *, already_running)` signature is identical in Tasks 4, 5, 6.
