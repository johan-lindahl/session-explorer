# Create New Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `c` keybinding that opens a dialog to create a new Claude session from a project or folder node, naming it directly and optionally requesting a git worktree.

**Architecture:** A new modal (`NewSessionScreen`) collects name, working directory, and an optional worktree. The TUI generates a session UUID up front and launches `claude -n <name> --session-id <uuid> [-w [<wt>]]` — Claude itself writes the name (`custom-title`) and creates any worktree. Under tmux the session starts as a sibling window named by the UUID (reusing all existing resume/live machinery); without tmux it falls back to the existing `execvp` pattern. The session-explorer writes no `git worktree` logic and no naming logic of its own.

**Tech Stack:** Python 3.11+, Textual (vendored), pytest + pytest-asyncio, tmux CLI wrapper.

---

## File Structure

- **`bin/_pkg/tmux.py`** (modify) — add the pure builder `build_new_session_window` and the thin wrapper `start_new_session_window`. Mirrors the existing `build_start_window` / `start_window` pair.
- **`bin/_pkg/tui.py`** (modify) — add `import uuid` and `Checkbox`; add module-level helpers `_derive_project_cwd`, `_new_session_argv`, `_new_sid`; add the `NewSessionScreen` dialog; add `action_new_session`; add the `c` binding; extend `check_action`; init two new instance attrs; extend `run()` for the no-tmux fallback.
- **`test/test_tmux.py`** (modify) — unit tests for `build_new_session_window`.
- **`test/test_tui.py`** (modify) — tests for the dialog, the tmux launch path, and the no-tmux fallback; plus a `_derive_project_cwd` unit test.
- **`SPEC.md`** (modify) — keybindings table + "New session" subsection.
- **`bin/_pkg/tui.py` `_help_text()`** (modify) — add `c` to the help overlay.

---

## Task 1: tmux builder + wrapper for a new session window

**Files:**
- Modify: `bin/_pkg/tmux.py`
- Test: `test/test_tmux.py`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_tmux.py`:

```python
def test_build_new_session_window_bare():
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature")
    assert argv == [
        "tmux", "-L", "session-explorer", "new-window", "-d",
        "-n", "sid-9", "-c", "/proj",
        "exec claude --session-id sid-9 -n feature",
    ]


def test_build_new_session_window_with_folder_name_no_quoting():
    argv = tmux.build_new_session_window("sid-9", "/proj", "planning/sprint14")
    assert argv[-1] == "exec claude --session-id sid-9 -n planning/sprint14"


def test_build_new_session_window_quotes_name_with_spaces():
    argv = tmux.build_new_session_window("sid-9", "/proj", "my session")
    assert argv[-1] == "exec claude --session-id sid-9 -n 'my session'"


def test_build_new_session_window_bare_worktree():
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature", worktree="")
    assert argv[-1] == "exec claude --session-id sid-9 -n feature -w"


def test_build_new_session_window_named_worktree():
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature", worktree="wt1")
    assert argv[-1] == "exec claude --session-id sid-9 -n feature -w wt1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_tmux.py -q -k new_session`
Expected: FAIL with `AttributeError: module '_pkg.tmux' has no attribute 'build_new_session_window'`

- [ ] **Step 3: Implement the builder and wrapper**

In `bin/_pkg/tmux.py`, add `import shlex` to the imports block (alphabetical, before `import shutil`):

```python
import os
import re
import shlex
import shutil
import subprocess
```

Add the builder right after `build_start_window` (after line 48):

```python
def build_new_session_window(sid: str, cwd: str, name: str,
                             worktree: "str | None" = None) -> List[str]:
    """new-window argv for starting a *fresh* claude session (not a resume).

    The window command is one shell string tmux runs via /bin/sh -c, so the
    name (which carries spaces and '/') is composed with shlex so it can never
    be re-split or injected. `--session-id <sid>` forces a known UUID up front,
    so the window name (`-n <sid>`) matches the real session id and all the
    existing resume/live machinery applies unchanged. `claude -n` writes the
    custom-title; `claude -w` owns worktree creation. `worktree` is None for no
    worktree, "" for a bare `-w` (claude auto-names), or a name for `-w <name>`.
    """
    inner = ["exec", "claude", "--session-id", sid, "-n", name]
    if worktree is not None:
        inner.append("-w")
        if worktree:
            inner.append(worktree)
    return build_base() + [
        "new-window", "-d", "-n", sid, "-c", cwd, shlex.join(inner)]
```

Add the wrapper right after `start_window` (after line 163):

```python
def start_new_session_window(sid: str, cwd: str, name: str,
                             worktree: "str | None" = None,
                             label: "str | None" = None) -> int:
    rc = _call(build_new_session_window(sid, cwd, name, worktree))
    if label:
        _call(build_set_label(sid, label))
    return rc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_tmux.py -q -k new_session`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): build_new_session_window for starting fresh sessions"
```

---

## Task 2: cwd-derivation and argv helpers in tui.py

**Files:**
- Modify: `bin/_pkg/tui.py`
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_tui.py`:

```python
def test_derive_project_cwd_picks_most_recent_and_strips_worktree():
    from _pkg.tui import _derive_project_cwd
    sessions = {
        "a": {"project_label": "demo", "project_path": "/repo/old",
              "last_active_at": "2026-05-01T00:00:00Z"},
        "b": {"project_label": "demo",
              "project_path": "/repo/main/.claude/worktrees/wt",
              "last_active_at": "2026-05-09T00:00:00Z"},
        "c": {"project_label": "other", "project_path": "/elsewhere",
              "last_active_at": "2026-05-20T00:00:00Z"},
    }
    # Most recent demo session is the worktree one; strip back to the repo root.
    assert _derive_project_cwd(sessions, "demo") == "/repo/main"


def test_derive_project_cwd_returns_none_when_no_match():
    from _pkg.tui import _derive_project_cwd
    assert _derive_project_cwd({}, "demo") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k derive_project_cwd`
Expected: FAIL with `ImportError: cannot import name '_derive_project_cwd'`

- [ ] **Step 3: Implement the helpers**

In `bin/_pkg/tui.py`, add `import uuid` to the stdlib imports (after `import os` at line 9):

```python
import os
import uuid
```

Add `Checkbox` to the Textual widgets import at line 16 (alphabetical):

```python
from textual.widgets import Checkbox, Footer, Header, Input, Label, OptionList, ProgressBar, Static, TextArea, Tree
```

Add these module-level helpers immediately after `_resume_argv` (after line 1502, before `def run()`):

```python
def _new_sid() -> str:
    """Fresh session UUID for a new session. Isolated so tests can stub it."""
    return str(uuid.uuid4())


def _derive_project_cwd(sessions: dict, project_label: str) -> "str | None":
    """Launch cwd for a new session in `project_label`: the project_path of its
    most-recently-active session, with any git-worktree suffix stripped back to
    the repo root so `claude -w` branches from the real repository. None when the
    project has no session with a usable path."""
    best = None
    best_key = ""
    for s in sessions.values():
        if s.get("project_label") != project_label:
            continue
        path = s.get("project_path")
        if not path:
            continue
        key = s.get("last_active_at") or ""
        if best is None or key >= best_key:
            best, best_key = path, key
    if not best:
        return None
    if _WORKTREE_MARKER in best:
        best = best.split(_WORKTREE_MARKER, 1)[0]
    return best


def _new_session_argv(sid: str, name: str, worktree: "str | None" = None) -> list[str]:
    """argv for `os.execvp` to start a fresh session without tmux. A list (no
    shell), so the name needs no quoting. `worktree`: None → no `-w`; "" → bare
    `-w`; otherwise `-w <name>`."""
    argv = ["claude", "--session-id", sid, "-n", name]
    if worktree is not None:
        argv.append("-w")
        if worktree:
            argv.append(worktree)
    return argv
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -q -k derive_project_cwd`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): cwd-derivation and new-session argv helpers"
```

---

## Task 3: NewSessionScreen dialog

**Files:**
- Modify: `bin/_pkg/tui.py`
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_tui.py`:

```python
async def test_new_session_dialog_returns_dict(index_path):
    from _pkg.tui import SessionExplorerApp, NewSessionScreen
    app = SessionExplorerApp(index_path=index_path)
    captured = {}
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(
            NewSessionScreen("demo", "planning/", "/tmp/demo-project"),
            lambda r: captured.update(r or {}),
        )
        await pilot.pause()
        for ch in "sprint15":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
    assert captured["name"] == "planning/sprint15"
    assert captured["cwd"] == "/tmp/demo-project"
    assert captured["worktree"] is False
    assert captured["worktree_name"] == ""


async def test_new_session_dialog_captures_worktree(index_path):
    from _pkg.tui import SessionExplorerApp, NewSessionScreen
    from textual.widgets import Checkbox, Input
    app = SessionExplorerApp(index_path=index_path)
    captured = {}
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(
            NewSessionScreen("demo", "", "/tmp/demo-project"),
            lambda r: captured.update(r or {}),
        )
        await pilot.pause()
        for ch in "feature":
            await pilot.press(ch)
        app.screen.query_one("#ns-wt", Checkbox).value = True
        app.screen.query_one("#ns-wtname", Input).value = "wt1"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert captured["name"] == "feature"
    assert captured["worktree"] is True
    assert captured["worktree_name"] == "wt1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py -q -k new_session_dialog`
Expected: FAIL with `ImportError: cannot import name 'NewSessionScreen'`

- [ ] **Step 3: Implement the dialog**

In `bin/_pkg/tui.py`, add this class immediately after `NewFolderScreen` (after line 359):

```python
class NewSessionScreen(_PanelScreen):
    """Create a new Claude session. Returns
    {name, cwd, worktree: bool, worktree_name: str} or None on cancel.

    The name Input prefills with the folder prefix (ends in '/') so the session
    nests in the current folder; a slash-path is folder placement exactly like
    rename/move. Enter from any Input gathers all fields and submits."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, project: str, name_prefix: str = "", cwd: str = "") -> None:
        super().__init__()
        self._project = project
        self._name_prefix = name_prefix
        self._cwd = cwd

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New session in '{self._project}' (use / to nest)",
                  classes="dialog-title"),
            Input(value=self._name_prefix, placeholder="session name", id="ns-name"),
            Input(value=self._cwd, placeholder="working directory", id="ns-cwd"),
            Checkbox("Create git worktree (-w)", id="ns-wt"),
            Input(placeholder="worktree name (optional)", id="ns-wtname"),
            Label("enter create · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self._result())

    def _result(self) -> dict:
        return {
            "name": self.query_one("#ns-name", Input).value.strip(),
            "cwd": self.query_one("#ns-cwd", Input).value.strip(),
            "worktree": self.query_one("#ns-wt", Checkbox).value,
            "worktree_name": self.query_one("#ns-wtname", Input).value.strip(),
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py -q -k new_session_dialog`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): NewSessionScreen dialog (name, cwd, worktree)"
```

---

## Task 4: action_new_session, binding, and check_action (tmux path)

**Files:**
- Modify: `bin/_pkg/tui.py`
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_tui.py`:

```python
async def test_new_session_tmux_starts_window(index_path, monkeypatch):
    import _pkg.tui as tui_mod
    import _pkg.tmux as tmux_mod
    calls = {}
    monkeypatch.setattr(tui_mod, "_new_sid", lambda: "fixed-sid")
    monkeypatch.setattr(
        tmux_mod, "start_new_session_window",
        lambda *a, **k: calls.setdefault("start", (a, k)))
    monkeypatch.setattr(
        tmux_mod, "select_window", lambda t: calls.setdefault("select", t))

    app = tui_mod.SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._tmux_enabled = True
        app._poll_live = lambda: None
        await pilot.press("down")  # demo project node
        await pilot.press("down")  # planning/ folder node
        await pilot.press("c")
        await pilot.pause()
        for ch in "sprint15":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()

    args, _kw = calls["start"]
    # start_new_session_window(sid, cwd, name, worktree, label)
    assert args[0] == "fixed-sid"
    assert args[1] == "/tmp/demo-project"
    assert args[2] == "planning/sprint15"   # folder prefix auto-applied
    assert args[3] is None                  # no worktree
    assert calls["select"] == "fixed-sid"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k new_session_tmux`
Expected: FAIL — pressing `c` does nothing (no `action_new_session`), so `calls` stays empty and `calls["start"]` raises `KeyError`.

- [ ] **Step 3: Implement the action, binding, attrs, and check_action**

In `bin/_pkg/tui.py`:

(a) Add the binding to the `BINDINGS` list after the `new_folder` line (after line 496):

```python
        Binding("n", "new_folder", "New folder"),
        Binding("c", "new_session", "New session"),
```

(b) Initialize the two instance attrs in `__init__`, right after the `_resume_cwd` line (after line 521):

```python
        self._resume_target: str | None = None
        self._resume_cwd: str | None = None
        self._new_session_argv: list[str] | None = None
        self._new_session_cwd: str | None = None
```

(c) Add `"new_session"` to the modal-guard tuple in `check_action` (line 546), inserting it alongside the other actions:

```python
        if action in ("resume", "rename", "move", "new_folder", "new_session", "delete", "notes", "preview", "close_preview", "filter", "toggle_unnamed", "rescan", "help", "expand_node", "collapse_node", "quit") and isinstance(self.screen, ModalScreen):
            return False
```

(d) Add the action method immediately after `action_new_folder` (after line 1078):

```python
    def action_new_session(self) -> None:
        project, prefix = self._project_and_prefix_for_cursor()
        if not project:
            self.bell(); return
        sessions = _index.load(self._index_path).get("sessions", {})
        default_cwd = _derive_project_cwd(sessions, project) or os.path.expanduser("~")

        def after(result: "dict | None") -> None:
            if not result:
                return
            name = result["name"].strip()
            if not name:
                return
            cwd = result["cwd"].strip() or os.path.expanduser("~")
            worktree = result["worktree_name"] if result["worktree"] else None
            sid = _new_sid()

            # No tmux → exit and execvp claude (handled in run()).
            if not self._tmux_enabled:
                self._new_session_argv = _new_session_argv(sid, name, worktree)
                self._new_session_cwd = cwd
                self.exit()
                return

            _, display = split_path(name)
            label = display or sid[:8]
            _tmux.start_new_session_window(sid, cwd, name, worktree, label)
            _tmux.select_window(sid)   # land straight in the new session
            self._poll_live()

        self.push_screen(NewSessionScreen(project, prefix, default_cwd), after)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -q -k new_session_tmux`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): action_new_session + c binding (tmux path)"
```

---

## Task 5: no-tmux fallback (execvp in run())

**Files:**
- Modify: `bin/_pkg/tui.py`
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_tui.py`:

```python
async def test_new_session_no_tmux_sets_argv(index_path, monkeypatch):
    import _pkg.tui as tui_mod
    monkeypatch.setattr(tui_mod, "_new_sid", lambda: "fixed-sid")
    app = tui_mod.SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # _tmux_enabled defaults False (env suppressed suite-wide).
        await pilot.press("down")  # demo project node
        await pilot.press("c")
        await pilot.pause()
        for ch in "feature":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
    assert app._new_session_argv == ["claude", "--session-id", "fixed-sid", "-n", "feature"]
    assert app._new_session_cwd == "/tmp/demo-project"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -q -k new_session_no_tmux`
Expected: This test should already PASS for the attribute-setting part, since Task 4 wired the no-tmux branch. Run it to confirm — if it passes, the action wiring is correct. (The `run()` execvp change below is what makes the attributes actually launch claude; it is covered by Step 3's test.)

If it fails because `_new_session_argv` is still `None`, re-check that the no-tmux branch in `action_new_session` runs (the fixture leaves `_tmux_enabled` False).

- [ ] **Step 3: Extend run() to launch a new session, with a test**

In `bin/_pkg/tui.py`, modify `run()` (lines 1505-1518) to handle the new-session argv before the resume path:

```python
def run() -> int:
    app = SessionExplorerApp()
    app.run()
    new_argv = getattr(app, "_new_session_argv", None)
    if new_argv:
        # chdir into the chosen project dir so claude (and `-w`) operate in the
        # right repo, then hand the window over to a fresh claude session.
        cwd = getattr(app, "_new_session_cwd", None)
        if cwd and os.path.isdir(cwd):
            os.chdir(cwd)
        os.execvp("claude", new_argv)
    target = getattr(app, "_resume_target", None)
    if target:
        # chdir into the session's original project so `claude --resume`
        # opens in the right workspace — without this, Claude inherits the
        # spawned terminal's cwd (usually $HOME) and shows a fresh "trust
        # folder" prompt instead of restoring the session.
        cwd = _resolve_resume_cwd(getattr(app, "_resume_cwd", None))
        if cwd:
            os.chdir(cwd)
        os.execvp("claude", _resume_argv(target))
    return 0
```

Add a test for the `run()` launch path to `test/test_tui.py`:

```python
def test_run_execvps_new_session(monkeypatch, tmp_path):
    import _pkg.tui as tui_mod

    target_dir = tmp_path / "proj"
    target_dir.mkdir()

    class FakeApp:
        _new_session_argv = ["claude", "--session-id", "fixed-sid", "-n", "feature"]
        _new_session_cwd = str(target_dir)
        _resume_target = None
        def run(self):
            pass

    monkeypatch.setattr(tui_mod, "SessionExplorerApp", lambda *a, **k: FakeApp())
    chdirs, execs = [], []
    monkeypatch.setattr(tui_mod.os, "chdir", lambda p: chdirs.append(p))
    monkeypatch.setattr(tui_mod.os, "execvp",
                        lambda f, argv: execs.append((f, argv)))

    tui_mod.run()
    assert chdirs == [str(target_dir)]
    assert execs == [("claude",
                      ["claude", "--session-id", "fixed-sid", "-n", "feature"])]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py -q -k "new_session_no_tmux or run_execvps_new_session"`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): no-tmux execvp fallback for new sessions"
```

---

## Task 6: Documentation — SPEC.md and help overlay

**Files:**
- Modify: `SPEC.md`
- Modify: `bin/_pkg/tui.py` (`_help_text()`)

- [ ] **Step 1: Add `c` to the SPEC keybindings table**

In `SPEC.md`, in the `### Keybindings` table, add this row immediately after the `n` (New folder) row (after line 122):

```markdown
| `c` | New session. On a **project** or **folder** node (or a session leaf, treated as its container): opens a dialog to name a new Claude session, pick its working directory, and optionally create a git worktree. Launches `claude -n <name> --session-id <uuid> [-w [<wt>]]` as a sibling tmux window (or via `execvp` without tmux). |
```

- [ ] **Step 2: Add a "New session" subsection to the SPEC TUI section**

In `SPEC.md`, immediately before `### Resume flow` (before line 166), add:

```markdown
### New session flow

`c` creates a new Claude session in the current project/folder context. A modal
collects the **name** (prefilled with the folder prefix so a slash-path nests it
exactly like rename/move), the **working directory** (derived from the project's
most-recently-active session, with any worktree suffix stripped to the repo root;
editable), and an optional **git worktree** (a checkbox plus an optional worktree
name).

The explorer generates the session UUID up front and launches
`claude -n <name> --session-id <uuid>` (plus `-w` / `-w <name>` when requested).
Claude itself writes the `custom-title` (via `-n`) and owns all worktree/branch
creation (via `-w`) — the plugin writes neither. The UUID is the tmux window name,
so the new window reconciles through the same live-registry / `list-windows`
machinery as resume, and the named session surfaces in the tree on the next live
poll. Without tmux, the explorer `execvp`s into the new session (same exit-and-
replace pattern as resume). Claude's own `--tmux` flag is deliberately not used —
sessions are hosted in the dedicated `-L session-explorer` server.

If the chosen directory is not a git repository and a worktree was requested,
`claude -w` reports the error inside the session window; v1 does not pre-validate.
```

- [ ] **Step 3: Add `c` to the help-text key list**

In `bin/_pkg/tui.py`, in `_help_text()`, the key list uses a `key(k, desc)` helper. Find the `n` line:

```python
        key("n", "New folder under the current project/folder"),
```

Add a `c` line directly after it:

```python
        key("n", "New folder under the current project/folder"),
        key("c", "New session in the current project/folder (names it; optional worktree)"),
```

- [ ] **Step 4: Verify the help text renders and run the full suite**

Run: `python3 -m pytest test/ -q`
Expected: PASS (all tests green, including the new ones).

- [ ] **Step 5: Commit**

```bash
git add SPEC.md bin/_pkg/tui.py
git commit -m "docs: document new-session (c) in SPEC and help overlay"
```

---

## Final verification

- [ ] **Run the full Python suite**

Run: `python3 -m pytest test/ -q`
Expected: all pass.

- [ ] **Run the shell suite (unaffected, sanity check)**

Run: `bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: all pass.

- [ ] **Manual smoke (optional, requires tmux + claude):** Launch the TUI, put the cursor on a project, press `c`, name a session `demo/smoke`, leave the worktree box unchecked, press Enter. Confirm a new tmux window opens running `claude` with the name `demo/smoke`, and that the row appears under the `demo/` folder within ~2s.

---

## Notes for the implementer

- **Why `--session-id`:** a brand-new session has no id until claude starts, but the whole tmux layer keys windows by `window-name == session-id`. Generating the UUID first and passing `--session-id` makes the window name correct from frame one — no placeholder/reconcile step.
- **`shlex.join` (Task 1)** is the single quoting boundary for the tmux shell-string path. The `execvp` path (Task 2/5) passes a list and needs no quoting.
- **`worktree` tri-state** (`None` / `""` / `"<name>"`) is shared by `build_new_session_window` and `_new_session_argv`; keep them in sync — both append `-w` only when not `None`, and append a name only when truthy.
- **Do not** add a `git worktree add` call or any branch logic — `claude -w` owns it (a load-bearing decision in the design doc).
- The new-session row appearing in the tree is automatic (SessionStart hook + ~2s live poll); no explicit `_populate()` is needed after launch.
