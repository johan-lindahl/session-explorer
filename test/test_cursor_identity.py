"""The tree cursor is an identity, not a line number.

Regression for "moving a running story starts an old session": the move
rebuilt the tree with the cursor left on its old *line*, which after the move
held a different (stopped) session — cursor-follow then closed the docked pane
and the next Enter resumed the stranger. Also: a double-click whose first click
landed in a modal (e.g. picking a folder in the Move dialog) must not resume
the row the second click hits after the modal is gone.
"""
import json

import pytest


def _session(name, last="2026-01-01T00:00:00Z"):
    return {
        "project_label": "demo", "project_path": "/tmp/demo-project",
        "name_cached": name, "last_active_at": last, "message_count": 5,
        "tokens_estimate": 0, "tokens_window_pct": 0,
    }


@pytest.fixture
def storefront_index(index_path):
    """A folder of old stopped stories plus one live story — the shape of the
    real-world tree where a moved live story's line fell onto an old one."""
    data = json.load(open(index_path))
    data["sessions"] = {
        f"old-{i}": _session(f"storefront/580{i} old story") for i in range(20, 40)
    }
    data["sessions"]["live"] = _session("storefront/59623 live story",
                                        "2026-11-27T00:00:00Z")
    json.dump(data, open(index_path, "w"))
    return index_path


@pytest.fixture
def fake_tmux(monkeypatch):
    from _pkg import tui as tuimod
    started = []
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: [])
    monkeypatch.setattr(tuimod._tmux, "docked_pane", lambda self_pane: "%9")
    monkeypatch.setattr(tuimod._tmux, "select_pane", lambda p: 0)
    monkeypatch.setattr(tuimod._tmux, "undock", lambda *a, **k: 0)
    monkeypatch.setattr(tuimod._tmux, "start_window",
                        lambda sid, cwd, label=None, worktree=None:
                        started.append(sid) or 0)
    monkeypatch.setattr(tuimod._tmux, "dock", lambda sid, focus=True: 0)
    return started


async def _dock_live(app, pilot):
    app._live_states = {"live": "idle"}
    app._docked_sid = "live"
    leaf = app._row_nodes["live"][0]
    app._tree.move_cursor(leaf)
    await pilot.pause()
    assert app._selected_sid() == "live"


async def test_move_keeps_cursor_on_moved_live_session(storefront_index, fake_tmux):
    from _pkg.tui import SessionExplorerApp, MoveScreen
    app = SessionExplorerApp(index_path=storefront_index)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await _dock_live(app, pilot)
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        app.screen.dismiss("storefront/account-pages")
        await pilot.pause(); await pilot.pause()
        assert app._selected_sid() == "live"
        # Enter on the (still docked) moved row refocuses it — never starts
        # whichever stopped story slid onto the cursor's old line.
        await pilot.press("enter"); await pilot.pause()
    assert fake_tmux == []
    assert app._docked_sid == "live"


async def test_move_into_collapsed_folder_reveals_and_follows(storefront_index, fake_tmux):
    """Collapse mode: the destination folder starts collapsed, so the moved
    row would be hidden — the cursor must still land on it (folder opened)."""
    from _pkg import folder_store as fs
    from _pkg.tui import SessionExplorerApp, MoveScreen
    fs.add(fs.default_path_for(storefront_index), "/tmp/demo-project",
           "storefront/account-pages")
    app = SessionExplorerApp(index_path=storefront_index)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        if not app._collapse_mode:
            await pilot.press("z"); await pilot.pause()
        app._expanded.update({app._node_key("/tmp/demo-project", []),
                              app._node_key("/tmp/demo-project", ["storefront"])})
        app._populate(); await pilot.pause()
        await _dock_live(app, pilot)
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        app.screen.dismiss("storefront/account-pages")
        await pilot.pause(); await pilot.pause()
        assert app._selected_sid() == "live"


async def test_rename_into_folder_keeps_cursor(storefront_index, fake_tmux):
    from _pkg.tui import SessionExplorerApp, RenameScreen
    app = SessionExplorerApp(index_path=storefront_index)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        await _dock_live(app, pilot)
        await pilot.press("r"); await pilot.pause()
        assert isinstance(app.screen, RenameScreen)
        app.screen.dismiss("storefront/zz/59623 live story")
        await pilot.pause(); await pilot.pause()
        assert app._selected_sid() == "live"


async def test_any_rebuild_keeps_cursor_on_same_session(storefront_index, fake_tmux):
    """A bare _populate() (notes, cycle view, filter, …) must not slide the
    cursor onto a neighbour when rows are inserted above it."""
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=storefront_index)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        leaf = app._row_nodes["old-30"][0]
        app._tree.move_cursor(leaf); await pilot.pause()
        line_before = app._tree.cursor_line
        data = json.load(open(storefront_index))
        for i in range(3):
            data["sessions"][f"new-{i}"] = _session(f"storefront/1000{i} new", "2026-11-26T00:00:00Z")
        json.dump(data, open(storefront_index, "w"))
        app._populate(); await pilot.pause()
        assert app._selected_sid() == "old-30"
        assert app._tree.cursor_line != line_before   # it really did shift


async def test_folder_move_keeps_cursor_on_folder(storefront_index, fake_tmux):
    from _pkg.tui import SessionExplorerApp, MoveScreen, ConfirmScreen
    data = json.load(open(storefront_index))
    data["sessions"]["acc"] = _session("storefront/account-pages/59627 x")
    json.dump(data, open(storefront_index, "w"))
    app = SessionExplorerApp(index_path=storefront_index)
    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.pause()
        folder = app._row_nodes["acc"][0].parent
        app._tree.move_cursor(folder); await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        app.screen.dismiss("")                    # re-parent to project top level
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        app.screen.dismiss(True)
        await pilot.pause(); await pilot.pause()
        d = app._tree.cursor_node.data
        assert "sid" not in d
        assert d["segments"] == ["account-pages"]


async def test_double_click_started_in_modal_does_not_resume(storefront_index, fake_tmux):
    """Real mouse events through App.on_event (the pilot bypasses the click
    chain): click 1 picks an option in the Move dialog and dismisses it; click 2
    — same cell, < 0.5s — lands on the tree beneath as chain=2."""
    from rich.style import Style
    from textual import events as ev
    from _pkg.tui import SessionExplorerApp, MoveScreen
    data = json.load(open(storefront_index))
    for f in ("archive", "release", "team", "zeta"):
        data["sessions"][f"f-{f}"] = _session(f"{f}/x")
    json.dump(data, open(storefront_index, "w"))
    app = SessionExplorerApp(index_path=storefront_index)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        await _dock_live(app, pilot)
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        ol = app.screen.query_one("#move-list")
        x = ol.region.x + 4
        # A cell that is an option in the dialog AND a session row beneath it.
        ys = [y for y in range(ol.region.y + 1, ol.region.bottom - 1)
              if (n := app._tree.get_node_at_line(
                  y - app._tree.region.y + int(app._tree.scroll_offset.y)))
              is not None and (n.data or {}).get("sid")]
        assert ys
        y = ys[0]

        def click():
            st = None
            if app.get_widget_at(x, y)[0] is app._tree:
                st = Style(meta={"line": y - app._tree.region.y
                                 + int(app._tree.scroll_offset.y)})
            kw = dict(widget=None, x=x, y=y, delta_x=0, delta_y=0, button=1,
                      shift=False, meta=False, ctrl=False, screen_x=x,
                      screen_y=y, style=st)
            app.post_message(ev.MouseDown(**kw))
            app.post_message(ev.MouseUp(**kw))

        click(); await pilot.pause(); await pilot.pause()
        assert not isinstance(app.screen, MoveScreen)   # click 1 picked + closed
        assert app.get_widget_at(x, y)[0] is app._tree
        click(); await pilot.pause(); await pilot.pause()
    assert fake_tmux == []


async def test_real_double_click_on_tree_still_resumes(storefront_index, fake_tmux):
    """The guard must not break a genuine double-click on a tree row."""
    from rich.style import Style
    from textual import events as ev
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=storefront_index)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        leaf = app._row_nodes["old-25"][0]
        app._tree._tree_lines  # noqa: B018
        line = leaf._line - int(app._tree.scroll_offset.y)
        x, y = app._tree.region.x + 6, app._tree.region.y + line

        def click():
            kw = dict(widget=None, x=x, y=y, delta_x=0, delta_y=0, button=1,
                      shift=False, meta=False, ctrl=False, screen_x=x,
                      screen_y=y, style=Style(meta={"line": leaf._line}))
            app.post_message(ev.MouseDown(**kw))
            app.post_message(ev.MouseUp(**kw))

        click(); click(); await pilot.pause(); await pilot.pause()
    assert fake_tmux == ["old-25"]
