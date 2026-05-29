# Live-Indicator Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the v1.1.0 live-session indicator: redesign the modal dialogs as centered overlays like the help screen, auto-refresh live sessions' metadata so their rows fill in and stats tick live, and document the glyphs (help screen + README + regenerated screenshots).

**Architecture:** A shared `_PanelScreen(ModalScreen)` base supplies one CSS block (centered rounded panel on a dimmed translucent backdrop) that all five input/confirm dialogs inherit. The 2s live poll gains an off-thread worker that calls `index.record_session` for each live session and relabels those rows in place (no full repopulate). A committed dev-only generator renders fabricated-data screenshots via headless Textual → Chrome → ImageMagick.

**Tech Stack:** Python 3.11+, vendored Textual (`ModalScreen`, `@work(thread=True)`, `run_test`), pytest, Google Chrome headless + ImageMagick (dev-only, for screenshots).

**Design doc:** `docs/superpowers/specs/2026-05-29-live-indicator-polish-design.md`

---

## File Structure

**Modify:**
- `bin/_pkg/tui.py` — add `_PanelScreen` base; restyle `RenameScreen`/`NewFolderScreen`/`ConfirmScreen`/`NotesScreen`/`MoveScreen`; add live-metadata refresh (`_do_live_metadata_refresh`, `_refresh_live_metadata` worker, `_apply_live_metadata`) wired into `_poll_live`; extend `_help_text()`.
- `README.md` — live-indicator feature + glyph legend; note dialogs match help overlay.
- `CHANGELOG.md`, `.claude-plugin/plugin.json`, `bin/_pkg/__init__.py` — version 1.1.0 → 1.2.0.
- `SPEC.md` — one line: live rows refresh metadata ~every 2s.
- `test/test_tui.py` — modal smoke tests + help-text assertion.
- `test/test_tui_live.py` — live-metadata refresh tests.
- `docs/images/tree.png`, `docs/images/preview.png`, `docs/images/help.png` — regenerated; **Create** `docs/images/live.png`.

**Create:**
- `scripts/gen_screenshots.py` — dev-only screenshot generator (NOT under `bin/_pkg/`, so it adds no plugin runtime dep).

**Conventions:**
- Vendored Textual: type selectors match base classes, and `DEFAULT_CSS` merges across the MRO — so a `_PanelScreen` selector styles its subclasses. Translucent screen `background` (alpha < 1) renders the screen beneath dimmed (`screen.py:612`).
- `@work(thread=True, exclusive=True)` + `call_from_thread` for off-UI-thread work (see `_rescan_worker`).
- Pure/plain helpers are unit-tested; UI flows use Textual `run_test()`.

---

## Task 1: `_PanelScreen` base + restyle Rename & NewFolder dialogs

**Files:**
- Modify: `bin/_pkg/tui.py` (add base before `RenameScreen` ~line 222; edit `RenameScreen` 222-238, `NewFolderScreen` 276-294)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write failing smoke tests**

Append to `test/test_tui.py` (match the file's existing `run_test()` style; it constructs `SessionExplorerApp(index_path=...)` against a temp index — reuse whatever fixture/helper the file already defines for building an app, e.g. an existing `_app`/`_write_index` helper. The bodies below assume a helper `_app(tmp_path)` returning a mounted-capable app; if the file names it differently, adapt):

```python
import pytest
from _pkg import tui as _tui


@pytest.mark.asyncio
async def test_rename_dialog_returns_value_on_enter(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)  # see note below
    async with app.run_test() as pilot:
        await pilot.pause()
        result = {}
        def cb(v): result["v"] = v
        app.push_screen(_tui.RenameScreen("old"), cb)
        await pilot.pause()
        inp = app.query_one("#rename-input", _tui.Input)
        inp.value = "team/new"
        await pilot.press("enter")
        await pilot.pause()
        assert result["v"] == "team/new"


@pytest.mark.asyncio
async def test_rename_dialog_cancels_on_escape(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = {}
        app.push_screen(_tui.RenameScreen("old"), lambda v: result.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert result["v"] == ""


def test_panelscreen_css_defines_centered_dimmed_panel():
    # The shared base must carry the centered/dimmed-panel styling.
    css = _tui._PanelScreen.DEFAULT_CSS
    assert "align: center middle" in css
    assert "#panel" in css
    assert "border: round $accent" in css
    assert "background: $surface" in css  # solid panel surface
    assert "%" in css  # translucent backdrop (alpha) so the tree shows through
```

Add a small helper near the top of `test/test_tui.py` if one doesn't already exist (check first; reuse the existing index-writing helper if present):

```python
def _make_app_with_one_named_session(tmp_path):
    import json, os
    from _pkg.tui import SessionExplorerApp
    idx = tmp_path / "session-explorer-index.json"
    idx.write_text(json.dumps({"version": 2, "sessions": {
        "s1": {"project_label": "demo", "project_path": "/p", "name_cached": "alpha",
               "last_active_at": "2026-05-29T10:00:00+00:00", "tokens_estimate": 1,
               "tokens_window_pct": 0, "message_count": 1, "first_prompt": "hi",
               "transcript_path": "/p/s1.jsonl"}}}))
    (tmp_path / ".session-explorer.help-seen").touch()
    (tmp_path / ".session-explorer.retention-declined").touch()
    return SessionExplorerApp(index_path=str(idx))
```

(The two marker files suppress the first-run retention prompt and help overlay so the app mounts straight to the tree. Verify these marker filenames against `retention.py`/`_help_marker_path` — they are `.session-explorer.retention-declined` and `.session-explorer.help-seen` per the codebase.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py -k "rename_dialog or panelscreen" -q`
Expected: FAIL — `_PanelScreen` doesn't exist; rename dialog has no centered styling assertion target.

- [ ] **Step 3: Add the `_PanelScreen` base**

Insert immediately before `class RenameScreen` (~line 222) in `bin/_pkg/tui.py`:

```python
class _PanelScreen(ModalScreen):
    """Base for input/confirm dialogs: a centered rounded panel on a dimmed,
    translucent backdrop so the session tree shows through (matches the help
    overlay). Subclasses wrap their widgets in `Vertical(..., id="panel")` with a
    bold `.dialog-title` Label first and a dim `.dialog-hint` Label last. Each
    subclass keeps its own Esc binding/return value (unchanged behavior)."""

    DEFAULT_CSS = """
    _PanelScreen { align: center middle; background: $surface-darken-1 60%; }
    _PanelScreen #panel {
        width: auto; max-width: 80%; height: auto; max-height: 90%;
        padding: 1 2; border: round $accent; background: $surface;
    }
    _PanelScreen #panel Input,
    _PanelScreen #panel OptionList,
    _PanelScreen #panel TextArea { width: 60; }
    _PanelScreen .dialog-title { text-style: bold; }
    _PanelScreen .dialog-hint { color: $text-muted; }
    """
```

- [ ] **Step 4: Restyle `RenameScreen` and `NewFolderScreen`**

Replace `class RenameScreen(ModalScreen[str]):` … through its `compose` with:

```python
class RenameScreen(_PanelScreen):
    """Prompt for a new session name. Returns the entered string or '' on cancel."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Rename session", classes="dialog-title"),
            Input(value=self._current, id="rename-input"),
            Label("enter save · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())
```

Replace `class NewFolderScreen(ModalScreen[str]):` … through its `compose` with:

```python
class NewFolderScreen(_PanelScreen):
    """Prompt for a folder path. The Input is prefilled with `prefix` (which
    ends in '/' when creating a child of an existing folder)."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def __init__(self, project: str, prefix: str = "") -> None:
        super().__init__()
        self._project = project
        self._prefix = prefix

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New folder under '{self._project}' (use / to nest)", classes="dialog-title"),
            Input(value=self._prefix, id="newfolder-input"),
            Label("enter create · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())
```

(`Note: the `[str]` generic is dropped from the class line because `_PanelScreen` already subclasses `ModalScreen`; the return type is unchanged at runtime. If you prefer to keep typing explicit, declare `class _PanelScreen(ModalScreen):` and let subclasses pass — `dismiss` is untyped-generic-safe at runtime.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py -k "rename_dialog or panelscreen" -q`
Expected: PASS.

- [ ] **Step 6: Run the full TUI suite (no regressions)**

Run: `python3 -m pytest test/test_tui.py test/test_tui_live.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat: shared _PanelScreen base + restyle rename/new-folder dialogs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Restyle Confirm, Notes & Move dialogs onto the base

**Files:**
- Modify: `bin/_pkg/tui.py` (`MoveScreen` 241-273, `ConfirmScreen` 297-314, `NotesScreen` 317-337)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write failing smoke tests**

Append to `test/test_tui.py`:

```python
@pytest.mark.asyncio
async def test_confirm_dialog_yes_no_escape(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.ConfirmScreen("Delete?"), lambda v: got.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert got["v"] is True
        app.push_screen(_tui.ConfirmScreen("Delete?"), lambda v: got.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert got["v"] is False


@pytest.mark.asyncio
async def test_notes_dialog_saves_on_ctrl_s(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.NotesScreen("orig"), lambda v: got.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert got["v"] == "orig"


@pytest.mark.asyncio
async def test_move_dialog_typed_path_on_enter(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.MoveScreen("demo", ["team/planning"], ""),
                        lambda v: got.__setitem__("v", v))
        await pilot.pause()
        inp = app.query_one("#move-input", _tui.Input)
        inp.value = "team/new"
        await pilot.press("enter")
        await pilot.pause()
        assert got["v"] == "team/new"


def test_restyled_dialogs_use_panel_base():
    for cls in (_tui.MoveScreen, _tui.ConfirmScreen, _tui.NotesScreen,
                _tui.RenameScreen, _tui.NewFolderScreen):
        assert issubclass(cls, _tui._PanelScreen), cls.__name__
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py -k "confirm_dialog or notes_dialog or move_dialog or panel_base" -q`
Expected: FAIL — Confirm/Notes/Move still subclass `ModalScreen`, not `_PanelScreen`; `#panel` not present.

- [ ] **Step 3: Restyle `MoveScreen`**

Replace its class line and `compose` with:

```python
class MoveScreen(_PanelScreen):
    """Pick or type a folder path (e.g. 'planning/sprint14').

    Returns "" to ungroup, the chosen/typed path string, or None on cancel.
    """

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, project: str, existing_paths: list[str], current: str) -> None:
        super().__init__()
        self._project = project
        self._existing = sorted(set(existing_paths))
        self._current = current

    def compose(self) -> ComposeResult:
        opts = [Option("(ungroup)", id="__none__")] + [
            Option(p, id=p) for p in self._existing
        ]
        yield Vertical(
            Label(f"Move within '{self._project}'  (current: {self._current or '(none)'})",
                  classes="dialog-title"),
            OptionList(*opts, id="move-list"),
            Input(placeholder="…or type a new path (e.g. team/planning)", id="move-input"),
            Label("enter / select to move · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        self.dismiss("" if opt_id == "__none__" else opt_id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())
```

- [ ] **Step 4: Restyle `ConfirmScreen`**

```python
class ConfirmScreen(_PanelScreen):
    """Yes/no confirmation modal. Returns True iff the user confirmed."""

    BINDINGS = [
        Binding("escape", "dismiss(False)", "Cancel"),
        Binding("y", "dismiss(True)", "Yes", show=False),
        Binding("n", "dismiss(False)", "No", show=False),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._prompt, classes="dialog-title"),
            Label("y yes · n / esc cancel", classes="dialog-hint"),
            id="panel",
        )
```

- [ ] **Step 5: Restyle `NotesScreen`**

```python
class NotesScreen(_PanelScreen):
    """Multi-line editor. Returns the new notes (may be empty) or None on cancel."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        self._ta = TextArea(self._current)
        yield Vertical(
            Label("Notes", classes="dialog-title"),
            self._ta,
            Label("ctrl-s save · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def action_save(self) -> None:
        self.dismiss(self._ta.text)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py -k "confirm_dialog or notes_dialog or move_dialog or panel_base" -q`
Expected: PASS.

- [ ] **Step 7: Full TUI + delete/notes/move suites (no regressions)**

Run: `python3 -m pytest test/ -q`
Expected: PASS (all green — confirms `action_delete`/`action_move`/`action_notes` flows still work with the restyled dialogs).

- [ ] **Step 8: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat: restyle move/confirm/notes dialogs onto _PanelScreen

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Live-metadata refresh (off-thread, in-place row update)

**Files:**
- Modify: `bin/_pkg/tui.py` (`_poll_live` 864-889; add three methods nearby)
- Test: `test/test_tui_live.py`

- [ ] **Step 1: Write failing tests**

Append to `test/test_tui_live.py`:

```python
import json
import pytest
from _pkg import tui as _tui
from _pkg.tui import SessionExplorerApp


def _write_jsonl(path, prompt):
    # Minimal transcript: one user message (so first_user_prompt + message_count read it).
    lines = [
        {"type": "user", "message": {"role": "user", "content": prompt},
         "timestamp": "2026-05-29T10:00:00.000Z"},
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "ok"}], "model": "claude-opus-4-8",
         "usage": {"cache_read_input_tokens": 1234}},
         "timestamp": "2026-05-29T10:00:01.000Z"},
    ]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def _app_with_live_empty_session(tmp_path):
    # Index has the live session as a SessionStart snapshot: empty prompt, 0 msgs.
    tp = tmp_path / "live1.jsonl"
    _write_jsonl(tp, "the real first prompt")
    idx = tmp_path / "session-explorer-index.json"
    idx.write_text(json.dumps({"version": 2, "sessions": {
        "live1": {"project_label": "demo", "project_path": str(tmp_path),
                  "name_cached": "alpha", "last_active_at": "2026-05-29T10:00:00+00:00",
                  "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
                  "first_prompt": None, "transcript_path": str(tp)},
        "dead1": {"project_label": "demo", "project_path": str(tmp_path),
                  "name_cached": "beta", "last_active_at": "2026-05-29T09:00:00+00:00",
                  "tokens_estimate": 5, "tokens_window_pct": 0, "message_count": 3,
                  "first_prompt": "beta prompt", "transcript_path": str(tmp_path / "x.jsonl")}}}))
    (tmp_path / ".session-explorer.help-seen").touch()
    (tmp_path / ".session-explorer.retention-declined").touch()
    return SessionExplorerApp(index_path=str(idx))


def test_do_live_metadata_refresh_fills_in_index(tmp_path):
    from _pkg import index as _index
    app = _app_with_live_empty_session(tmp_path)
    app._live_states = {"live1": "working"}
    app._do_live_metadata_refresh()
    data = _index.load(app._index_path)["sessions"]
    assert data["live1"]["first_prompt"] == "the real first prompt"
    assert data["live1"]["message_count"] == 2
    # Non-live session untouched.
    assert data["dead1"]["first_prompt"] == "beta prompt"


@pytest.mark.asyncio
async def test_apply_live_metadata_updates_row_without_repopulate(tmp_path):
    app = _app_with_live_empty_session(tmp_path)
    app._live_states = {"live1": "working"}
    async with app.run_test() as pilot:
        await pilot.pause()
        # Move cursor onto a row, capture it, then refresh; cursor must not jump.
        before_line = app._tree.cursor_line
        app._do_live_metadata_refresh()
        app._apply_live_metadata()
        await pilot.pause()
        leaf, _ = app._row_nodes["live1"]
        assert leaf.data.get("first_prompt") == "the real first prompt"
        assert app._tree.cursor_line == before_line  # no full repopulate jump
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui_live.py -k "live_metadata" -q`
Expected: FAIL — `_do_live_metadata_refresh` / `_apply_live_metadata` don't exist.

- [ ] **Step 3: Add the refresh methods**

Add these three methods to `SessionExplorerApp` (place next to `_relabel_live_rows`, ~line 936):

```python
    def _do_live_metadata_refresh(self) -> None:
        """Re-index each live session from its transcript so first_prompt / msgs /
        tokens populate and tick as the agent works. Plain (no threading) so it's
        unit-testable; the worker wraps it. Only touches live sessions; swallows
        per-session errors (a transcript mid-write must never break the UI)."""
        from . import live as _live
        try:
            live = _live.load(self._live_path()).get("sessions", {})
            indexed = _index.load(self._index_path).get("sessions", {})
        except Exception:
            return
        for sid in list(self._live_states):
            entry = live.get(sid, {})
            ie = indexed.get(sid, {})
            tp = entry.get("transcript_path") or ie.get("transcript_path")
            cwd = entry.get("cwd") or ie.get("project_path")
            if not tp or not cwd:
                continue  # can't refresh without a transcript+cwd; F5 remains the catch-all
            try:
                _index.record_session(self._index_path, sid, tp, cwd)
            except Exception:
                continue

    @work(thread=True, exclusive=True, group="live-meta")
    def _refresh_live_metadata(self) -> None:
        """Off-thread wrapper: refresh live metadata on disk, then update rows on
        the UI thread. `exclusive` so a slow refresh can't stack across polls."""
        self._do_live_metadata_refresh()
        self.call_from_thread(self._apply_live_metadata)

    def _apply_live_metadata(self) -> None:
        """Reload the index and refresh only the live rows in place (update each
        live leaf's stored `data` + relabel). No full repopulate, so cursor /
        scroll / expansion are preserved."""
        try:
            data = _index.load(self._index_path).get("sessions", {})
        except Exception:
            return
        for sid, (leaf, _depth) in self._row_nodes.items():
            if sid in self._live_states and sid in data:
                leaf.data = {"sid": sid, **data[sid]}
        self._relabel_live_rows()
        # If the highlighted row is a live one, refresh the preview pane too.
        self._refresh_preview()
```

(`@work` and `_index` are already imported at the top of `tui.py`; `_refresh_preview` already exists.)

- [ ] **Step 4: Wire it into `_poll_live`**

Replace the body of `_poll_live` (864-889) with:

```python
    def _poll_live(self) -> None:
        """Refresh live-session state from the registry (called on a timer).

        Liveness changes update glyphs (or repopulate on a visibility change);
        then, regardless, live sessions' metadata is refreshed off-thread so
        first_prompt / msgs / tokens fill in and tick as the agent works.
        """
        from . import live as _live
        try:
            new_states = _live.poll(self._live_path())
        except Exception:
            return  # never let the indicator break the UI
        if new_states != self._live_states:
            old = self._live_states
            self._live_states = new_states
            if self._visibility_changed(old, new_states):
                sid = self._selected_sid()
                self._populate()
                self._restore_cursor_to_sid(sid)
            else:
                self._relabel_live_rows()
        # Always refresh live metadata (stats change even when liveness doesn't).
        if self._live_states:
            self._refresh_live_metadata()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui_live.py -k "live_metadata" -q`
Expected: PASS.

- [ ] **Step 6: Full suite (no regressions)**

Run: `python3 -m pytest test/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_live.py
git commit -m "feat: auto-refresh live sessions' metadata off-thread (stats tick live)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Help-screen "Live sessions" section

**Files:**
- Modify: `bin/_pkg/tui.py` (`_help_text()` ~177-219)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write a failing test**

Append to `test/test_tui.py`:

```python
def test_help_text_documents_live_sessions():
    txt = _tui._help_text()
    assert "Live sessions" in txt
    assert "spinner" in txt.lower()
    assert "○" in txt          # idle glyph explained
    assert "active" in txt.lower()  # the ● N active subtitle
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -k help_text_documents_live -q`
Expected: FAIL — no "Live sessions" section yet.

- [ ] **Step 3: Add the section**

In `_help_text()`, insert a new block right after the "What you see" block (after the line `'press [b]u[/] to toggle them on so you can rename or delete them.',` and its following `"",`) and before `"[b]Keys[/]"`:

```python
        "[b]Live sessions[/]",
        "Sessions running right now are flagged in the left column:",
        "  [green]⠿[/] a spinner = actively working   [dim]○[/] = open but idle",
        "The subtitle shows [b]● N active[/]. Live rows refresh from their",
        "transcript about every 2s, so the first prompt, message count, tokens",
        "and context % fill in and tick up as the agent works. Live sessions",
        "show even when unnamed.",
        "",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -k help_text_documents_live -q`
Expected: PASS.

- [ ] **Step 5: Confirm the existing version-in-help test still passes**

Run: `python3 -m pytest test/test_tui.py -q`
Expected: PASS (the `__version__`-in-help assertion is unaffected).

- [ ] **Step 6: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "docs: explain live-session glyphs in the help overlay

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Screenshot generator + regenerated images

**Files:**
- Create: `scripts/gen_screenshots.py`
- Modify/Create: `docs/images/tree.png`, `docs/images/preview.png`, `docs/images/help.png`, `docs/images/live.png`

**Reference:** the proven pipeline is headless Textual `save_screenshot` (SVG) → Google Chrome headless (SVG→PNG; ImageMagick's native SVG renderer is too low-fidelity) → ImageMagick resize. See the `reference-readme-screenshots` memory.

- [ ] **Step 1: Write the generator script**

Create `scripts/gen_screenshots.py`:

```python
#!/usr/bin/env python3
"""Dev-only generator for the README TUI screenshots.

Drives the REAL Textual app headless with a fabricated index, exports SVGs, then
converts to PNG via Chrome headless + ImageMagick. NOT shipped in the plugin
(lives under scripts/, not bin/_pkg/), so it adds no runtime dependency.

Usage:  python3 scripts/gen_screenshots.py
Requires: Google Chrome and ImageMagick (`magick`) on this machine.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "bin"))

from _pkg.tui import SessionExplorerApp  # noqa: E402

OUT = os.path.join(REPO, "docs", "images")
WORK = tempfile.mkdtemp(prefix="se-shots-")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

SESSIONS = {
    "acme-api/a-auth": {"project_label": "acme-api", "project_path": "/Users/jl/acme-api",
        "name_cached": "auth/refresh-tokens", "branch": "main",
        "last_active_at": "2026-05-29T08:00:00+00:00", "created_at": "2026-05-20T00:00:00+00:00",
        "tokens_estimate": 48000, "tokens_window_pct": 24, "message_count": 36,
        "first_prompt": "Add refresh-token rotation to the auth service", "notes": "",
        "transcript_path": "/Users/jl/.claude/projects/acme-api/a.jsonl"},
    "acme-api/a-bug": {"project_label": "acme-api", "project_path": "/Users/jl/acme-api",
        "name_cached": "fix/null-deref", "branch": "main",
        "last_active_at": "2026-05-29T07:30:00+00:00", "created_at": "2026-05-22T00:00:00+00:00",
        "tokens_estimate": 12000, "tokens_window_pct": 6, "message_count": 9,
        "first_prompt": "Investigate the null deref in the parser", "notes": "",
        "transcript_path": "/Users/jl/.claude/projects/acme-api/b.jsonl"},
    "webapp/w-feat": {"project_label": "webapp", "project_path": "/Users/jl/webapp",
        "name_cached": "feature/live-cart", "branch": "main",
        "last_active_at": "2026-05-29T09:59:00+00:00", "created_at": "2026-05-28T00:00:00+00:00",
        "tokens_estimate": 91000, "tokens_window_pct": 45, "message_count": 58,
        "first_prompt": "Build the live cart total component", "notes": "review before merge",
        "transcript_path": "/Users/jl/.claude/projects/webapp/w.jsonl"},
    "webapp/w-idle": {"project_label": "webapp", "project_path": "/Users/jl/webapp",
        "name_cached": "spike/pricing", "branch": "main",
        "last_active_at": "2026-05-29T09:40:00+00:00", "created_at": "2026-05-27T00:00:00+00:00",
        "tokens_estimate": 33000, "tokens_window_pct": 16, "message_count": 21,
        "first_prompt": "Prototype the new pricing tiers", "notes": "",
        "transcript_path": "/Users/jl/.claude/projects/webapp/wi.jsonl"},
}
LIVE = {"webapp/w-feat": "working", "webapp/w-idle": "idle"}


def _write_index(path):
    json.dump({"version": 2, "sessions": SESSIONS}, open(path, "w"))


async def _shoot(idx_path, name, live=None, open_help=False, open_preview=False):
    app = SessionExplorerApp(index_path=idx_path)
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause()
        if live:
            app._live_states = dict(live)
            app._spinner_frame = 3  # a mid-cycle braille frame
            app._apply_live_metadata() if False else app._relabel_live_rows()
            await pilot.pause()
        if open_preview:
            app.action_preview()
            await pilot.pause()
        if open_help:
            app.action_help()
            await pilot.pause()
        app.save_screenshot(os.path.join(WORK, f"{name}.svg"))


def _svg_to_png(name):
    svg = os.path.join(WORK, f"{name}.svg")
    chrome_png = os.path.join(WORK, f"{name}.chrome.png")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=2", "--default-background-color=00000000",
                    "--window-size=1439,731", f"--screenshot={chrome_png}",
                    f"file://{svg}"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["magick", chrome_png, "-resize", "1600x", "-strip",
                    os.path.join(OUT, f"{name}.png")], check=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    idx = os.path.join(WORK, "index.json")
    _write_index(idx)
    asyncio.run(_shoot(idx, "tree", live=LIVE))
    asyncio.run(_shoot(idx, "live", live=LIVE))
    asyncio.run(_shoot(idx, "preview", live=LIVE, open_preview=True))
    asyncio.run(_shoot(idx, "help", open_help=True))
    for name in ("tree", "live", "preview", "help"):
        _svg_to_png(name)
        print("wrote", os.path.join(OUT, f"{name}.png"))


if __name__ == "__main__":
    main()
```

(Remove the dead `app._apply_live_metadata() if False else` ternary — it's only there to flag that the real app calls `_apply_live_metadata`; in the generator a plain `_relabel_live_rows()` is enough since the fabricated index already has full stats. Write just `app._relabel_live_rows()`.)

- [ ] **Step 2: Make it executable and run it**

Run:
```bash
chmod +x scripts/gen_screenshots.py
python3 scripts/gen_screenshots.py
```
Expected: prints four `wrote …/docs/images/<name>.png` lines, no traceback.

- [ ] **Step 3: Verify the PNGs render the live glyphs**

Run:
```bash
for f in tree live preview help; do echo -n "$f: "; magick identify -format "%wx%h\n" docs/images/$f.png; done
```
Expected: each ~`1600x...`. Open `docs/images/live.png` and confirm by eye: a spinner glyph on the "working" row, a dim `○` on the "idle" row, and `● 2 active` in the subtitle.

> If Chrome isn't at the default path, set `CHROME` env or edit the constant. If a glyph renders as a missing box, the SVG font fallback dropped braille — rerun; if persistent, fall back to a native Terminal screenshot for `live.png` only and note it in the commit.

- [ ] **Step 4: Commit**

```bash
git add scripts/gen_screenshots.py docs/images/tree.png docs/images/live.png docs/images/preview.png docs/images/help.png
git commit -m "docs: screenshot generator + regenerate images with live glyphs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: README legend, version bump, CHANGELOG, SPEC line

**Files:**
- Modify: `README.md`, `.claude-plugin/plugin.json`, `bin/_pkg/__init__.py`, `CHANGELOG.md`, `SPEC.md`

- [ ] **Step 1: Bump the version (two files)**

In `.claude-plugin/plugin.json` change `"version": "1.1.0",` → `"version": "1.2.0",`.
In `bin/_pkg/__init__.py` change `__version__ = "1.1.0"` → `__version__ = "1.2.0"`.

- [ ] **Step 2: Verify the version (the test reads `__version__` dynamically)**

Run: `python3 bin/session-explorer --version`
Expected: `session-explorer 1.2.0`.
Run: `python3 -m pytest test/test_cli.py -k version -q`
Expected: PASS.

- [ ] **Step 3: Add the live-indicator legend + image to README**

In `README.md`, under the "What it looks like" section (after the existing tree image around line 21), add:

```markdown
### Live sessions

Sessions running right now are flagged in the left column — an animated green
spinner for a session actively working, a dim `○` for one that's open but idle —
and the subtitle shows `● N active`. Live rows refresh from their transcript
about every 2 seconds, so the first prompt, message count, tokens, and context %
fill in and tick up as the agent works. Live sessions appear even when unnamed.

![Live-session indicator](docs/images/live.png)
```

In the "TUI keybindings" table region (around lines 105-123), add a short note under the table:

```markdown
> The leftmost column shows live state: an animated spinner (working), a dim `○`
> (idle), or blank (inactive). The dialogs for rename/move/new-folder/notes now
> appear as centered overlays matching the help screen.
```

- [ ] **Step 4: Add a CHANGELOG entry**

In `CHANGELOG.md`, add above the `## 1.1.0` heading:

```markdown
## 1.2.0

### Added
- Live sessions' rows now refresh from their transcript about every 2s, so a
  freshly-started session's first prompt, message count, tokens, and context %
  fill in and tick up live (no manual F5). Persisted to the index, off the UI
  thread; only live sessions are re-read.
- README + in-app help now document the live-session glyphs, with a new
  screenshot; a dev-only `scripts/gen_screenshots.py` regenerates the images.

### Changed
- The rename / move / new-folder / delete / notes dialogs are now centered
  overlays on a dimmed backdrop (matching the help screen) instead of
  full-screen black panels.

```

- [ ] **Step 5: Add the SPEC.md cadence line**

In `SPEC.md`, in the "Live-session indicator" section's TUI subsection, add one line:

```markdown
- Live rows also refresh their index metadata (first prompt, message count,
  tokens, context %) from the transcript on each ~2s poll, off the UI thread
  (only the live sessions are re-read; F5 remains the full reindex).
```

- [ ] **Step 6: Full suite + commit**

Run: `python3 -m pytest test/ -q && bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: PASS.

```bash
git add README.md CHANGELOG.md SPEC.md .claude-plugin/plugin.json bin/_pkg/__init__.py
git commit -m "docs: README live legend + bump to 1.2.0

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (design → tasks):**
- Part 1 dialog redesign (shared base, dimmed backdrop, 5 dialogs, title/hint) → Task 1 (base + Rename/NewFolder) + Task 2 (Move/Confirm/Notes).
- Part 2 live-metadata refresh (off-thread, every poll, persist to index, in-place relabel, cursor preserved) → Task 3.
- Part 3 help text → Task 4; screenshots (programmatic, dev-only generator, tree+live) → Task 5; README legend + version + CHANGELOG + SPEC line → Task 6.
- Testing (dialog smoke, live-refresh, help text) → Tasks 1-4.
- Version 1.2.0 → Task 6.

**Placeholder scan:** No TBD/TODO. Every code step shows complete code. Two steps depend on matching existing test helpers/README anchors and say exactly what to add and where (Task 1 Step 1 helper note; Task 6 Step 3 README anchors) — these reference real, located anchors, not invented APIs.

**Type/name consistency:** `_PanelScreen` base used by all five dialogs (Tasks 1-2); `#panel`, `.dialog-title`, `.dialog-hint` consistent across dialogs; `_do_live_metadata_refresh` / `_refresh_live_metadata` (worker) / `_apply_live_metadata` consistent across Task 3 and reused by Task 5's generator note; `_live_states`/`_row_nodes`/`_relabel_live_rows`/`_refresh_preview` reused from existing code; version string `1.2.0` consistent across plugin.json/__init__.py/CHANGELOG.

**Runtime checks flagged inline:** (a) base-class `DEFAULT_CSS`/type-selector inheritance verified against vendored Textual; (b) translucent backdrop verified (`screen.py:612`); (c) the test helper marker filenames must match `retention.py`/`_help_marker_path` (noted in Task 1); (d) Chrome path / braille-glyph SVG fallback flagged in Task 5 with a documented fallback.
```
