"""Open projects (`o`): picker, persisted selection, tree filtering."""

import json

import pytest


def _write_index(tmp_path):
    p = str(tmp_path / "se-index.json")
    json.dump({"version": 1, "sessions": {
        "a1": {"project_label": "alpha", "project_path": "/w/alpha",
               "name_cached": "one", "last_active_at": "2026-11-20T00:00:00Z"},
        "b1": {"project_label": "beta", "project_path": "/w/beta",
               "name_cached": "bugfix/two", "last_active_at": "2026-11-21T00:00:00Z"},
        "b2": {"project_label": "beta", "project_path": "/w/beta",
               "name_cached": "bugfix/three", "last_active_at": "2026-11-01T00:00:00Z"},
        "g1": {"project_label": "gamma", "project_path": "/w/gamma",
               "name_cached": "four", "last_active_at": "2026-11-22T00:00:00Z"},
    }}, open(p, "w"))
    for m in ("help-seen", "retention-declined", "summaries-prompted"):
        (tmp_path / f".session-explorer.{m}").write_text("")
    return p


def _project_labels(app):
    return [str(n.label) for n in app._tree.root.children]


def _ui(tmp_path):
    return str(tmp_path / "session-explorer-ui.json")


async def test_no_selection_shows_all_projects_without_prompt_when_suppressed(tmp_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(_project_labels(app)) == 3
        assert "of" not in app.sub_title


async def test_first_run_picker_shows_every_launch_until_saved(tmp_path, monkeypatch):
    from _pkg.tui import SessionExplorerApp, ProjectPickerScreen
    from _pkg import ui_state
    monkeypatch.delenv("SESSION_EXPLORER_PROJECTS_NO_PROMPT", raising=False)
    p = _write_index(tmp_path)
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ProjectPickerScreen)
        await pilot.press("escape")          # skip: this run shows everything
        await pilot.pause()
        assert len(_project_labels(app)) == 3
    assert ui_state.get_open_projects(_ui(tmp_path)) is None
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ProjectPickerScreen)   # asked again


async def test_picker_pretick_launch_cwd_and_confirm_filters(tmp_path, monkeypatch):
    from _pkg.tui import SessionExplorerApp, ProjectPickerScreen
    from _pkg import ui_state
    monkeypatch.delenv("SESSION_EXPLORER_PROJECTS_NO_PROMPT", raising=False)
    (tmp_path / ".session-explorer.launch-cwd").write_text("/w/beta/src")
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ProjectPickerScreen)
        assert app.screen._ticked == {"/w/beta"}
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ProjectPickerScreen)
        labels = _project_labels(app)
        assert len(labels) == 1 and labels[0].startswith("beta")
        assert "1 of 3 projects" in app.sub_title
    assert ui_state.get_open_projects(_ui(tmp_path)) == ["/w/beta"]


async def test_picker_refuses_empty_selection(tmp_path, monkeypatch):
    from _pkg.tui import SessionExplorerApp, ProjectPickerScreen
    monkeypatch.delenv("SESSION_EXPLORER_PROJECTS_NO_PROMPT", raising=False)
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen._ticked == set()   # no launch cwd, nothing live
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ProjectPickerScreen)


async def test_o_reopens_picker_and_all_projects_unfilters(tmp_path):
    from _pkg.tui import SessionExplorerApp, ProjectPickerScreen
    from _pkg import ui_state
    ui_state.set_open_projects(_ui(tmp_path), ["/w/alpha"])
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(_project_labels(app)) == 1
        await pilot.press("o")
        await pilot.pause()
        scr = app.screen
        assert isinstance(scr, ProjectPickerScreen)
        assert scr._ticked == {"/w/alpha"}
        scr._list.highlighted = 0            # the "All projects" row
        await pilot.press("space")
        await pilot.press("enter")
        await pilot.pause()
        assert len(_project_labels(app)) == 3
    assert ui_state.get_open_projects(_ui(tmp_path)) == "*"


async def test_picker_filter_and_toggle_visible(tmp_path):
    from _pkg.tui import SessionExplorerApp, ProjectPickerScreen
    from _pkg import ui_state
    ui_state.set_open_projects(_ui(tmp_path), ["/w/alpha"])
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        scr = app.screen
        assert isinstance(scr, ProjectPickerScreen)
        for ch in "gam":
            await pilot.press(ch)
        await pilot.pause()
        assert [p["root"] for p in scr._visible()] == ["/w/gamma"]
        await pilot.press("ctrl+a")
        assert scr._ticked == {"/w/alpha", "/w/gamma"}
        await pilot.press("escape")          # cancel keeps the old selection
        await pilot.pause()
    assert ui_state.get_open_projects(_ui(tmp_path)) == ["/w/alpha"]


async def test_live_session_in_closed_project_still_shows(tmp_path):
    from _pkg.tui import SessionExplorerApp
    from _pkg import ui_state
    ui_state.set_open_projects(_ui(tmp_path), ["/w/alpha"])
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._live_states = {"b1": "idle"}
        app._populate()
        await pilot.pause()
        labels = _project_labels(app)
        assert len(labels) == 2
        beta = [l for l in labels if l.startswith("beta")][0]
        assert "not open" in beta and "(1)" in beta     # only the live session


async def test_empty_state_when_open_projects_have_no_sessions(tmp_path):
    from _pkg.tui import SessionExplorerApp
    from _pkg import ui_state
    ui_state.set_open_projects(_ui(tmp_path), ["/w/removed"])
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._empty.display is True
        assert "Press o" in str(app._empty.render())


def test_empty_state_text_projects_branch():
    from _pkg.tui import _empty_state_text
    msg = _empty_state_text(total_indexed=4, visible=0, unnamed_hidden=0,
                            filter_active=False, scanned=False,
                            projects_filtered=True)
    assert "Press o" in msg


async def test_settings_projects_row(tmp_path):
    from _pkg.tui import SessionExplorerApp, ProjectPickerScreen
    from _pkg import ui_state
    ui_state.set_open_projects(_ui(tmp_path), ["/w/alpha", "/w/gone"])
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        rows = dict(app._settings_rows())
        assert "1 of 3 open" in rows["projects"]
        app._settings_activate("projects")
        await pilot.pause()
        assert isinstance(app.screen, ProjectPickerScreen)


async def test_o_blocked_while_filter_focused(tmp_path):
    from _pkg.tui import SessionExplorerApp, ProjectPickerScreen
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.press("o")
        await pilot.pause()
        assert not isinstance(app.screen, ProjectPickerScreen)
        assert app._filter.value == "o"


def test_help_mentions_open_projects():
    from _pkg.tui import _help_text
    assert "Open projects" in _help_text()


async def test_picker_keyboard_tick_with_arrows(tmp_path, monkeypatch):
    from _pkg.tui import SessionExplorerApp
    from _pkg import ui_state
    monkeypatch.delenv("SESSION_EXPLORER_PROJECTS_NO_PROMPT", raising=False)
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Rows: All, gamma (newest), beta, alpha. Highlight starts on gamma.
        await pilot.press("down")
        await pilot.press("space")
        assert app.screen._input.value == ""     # space ticked, didn't type
        await pilot.press("enter")
        await pilot.pause()
    assert ui_state.get_open_projects(_ui(tmp_path)) == ["/w/beta"]


async def test_first_run_picker_sees_live_sessions(tmp_path, monkeypatch):
    # The picker opens from on_mount; the live registry must already be read so
    # a project with a running session is pre-ticked and marked ●.
    from _pkg.tui import SessionExplorerApp, ProjectPickerScreen
    from _pkg import live as _live
    monkeypatch.delenv("SESSION_EXPLORER_PROJECTS_NO_PROMPT", raising=False)
    monkeypatch.setattr(_live, "poll", lambda path: {"b1": "idle"})
    app = SessionExplorerApp(index_path=_write_index(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        scr = app.screen
        assert isinstance(scr, ProjectPickerScreen)
        assert scr._ticked == {"/w/beta"}
        beta = [p for p in scr._catalog if p["root"] == "/w/beta"][0]
        assert beta["live"] is True
