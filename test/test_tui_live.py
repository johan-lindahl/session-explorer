import json

from _pkg import tui


def _write_index(tmp_path, sessions):
    """Write a minimal index file and silence the first-run modals so the App
    can be constructed without a running event loop interfering."""
    path = str(tmp_path / "se-index.json")
    json.dump({"version": 1, "folders": [], "sessions": sessions}, open(path, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    return path


def _app(index_path):
    # __init__ only sets attributes; no event loop is needed to call the pure-ish
    # _visibility_changed (which just reads the index off disk).
    return tui.SessionExplorerApp(index_path=index_path)


def test_visibility_changed_unnamed_entering_live_returns_true(tmp_path):
    path = _write_index(tmp_path, {
        "named": {"name_cached": "kept", "project_label": "demo"},
        "stub": {"name_cached": None, "project_label": "demo"},
    })
    app = _app(path)
    # The unnamed "stub" appears in the live set -> its tree membership changes.
    assert app._visibility_changed({}, {"stub": "working"}) is True
    # And leaving the set is equally a membership change.
    assert app._visibility_changed({"stub": "idle"}, {}) is True


def test_visibility_changed_named_entering_live_returns_false(tmp_path):
    path = _write_index(tmp_path, {
        "named": {"name_cached": "kept", "project_label": "demo"},
    })
    app = _app(path)
    # A named session is always shown, so its liveness never alters membership.
    assert app._visibility_changed({}, {"named": "working"}) is False


def test_visibility_changed_when_showing_unnamed_returns_false(tmp_path):
    path = _write_index(tmp_path, {
        "stub": {"name_cached": None, "project_label": "demo"},
    })
    app = _app(path)
    app._show_unnamed = True  # all unnamed already visible -> liveness is moot
    assert app._visibility_changed({}, {"stub": "working"}) is False


def test_visibility_changed_unknown_sid_returns_false(tmp_path):
    # A live sid the index has never heard of can't change tree membership.
    path = _write_index(tmp_path, {
        "named": {"name_cached": "kept", "project_label": "demo"},
    })
    app = _app(path)
    assert app._visibility_changed({}, {"ghost": "working"}) is False


def test_glyph_inactive_is_two_blank_cells():
    # None state -> a non-markup 2-cell prefix so columns stay aligned.
    assert tui._glyph(None, frame=0) == "  "


def test_glyph_idle_is_dim_circle():
    assert tui._glyph("idle", frame=0) == "[dim]○[/] "


def test_glyph_working_cycles_spinner_frames():
    g0 = tui._glyph("working", frame=0)
    g1 = tui._glyph("working", frame=1)
    assert g0.startswith("[green]") and g0.endswith("[/] ")
    assert g0 != g1  # frame advanced -> different braille glyph


def test_glyph_is_always_glyph_w_cells_wide():
    # rich is vendored under _pkg._vendor; conftest only puts bin/ on the path,
    # so the plain `rich` package isn't importable here.
    from _pkg._vendor.rich.text import Text
    for state in (None, "idle", "working"):
        for frame in range(len(tui.SPINNER_FRAMES)):
            width = Text.from_markup(tui._glyph(state, frame)).cell_len
            assert width == tui.GLYPH_W, f"{state!r} frame {frame} -> {width} cells"


def test_row_label_prepends_glyph_without_disturbing_name():
    s = {"name_cached": "myname", "last_active_at": None, "tokens_estimate": 0,
         "tokens_window_pct": 0, "message_count": 0, "first_prompt": ""}
    label = tui._row_label("sid12345", s, depth=2, glyph="[green]⠋[/] ")
    assert label.startswith("[green]⠋[/] ")
    assert "myname" in label


async def test_poll_live_repopulate_preserves_cursor(tmp_path):
    # One named (always visible) + one unnamed (visible only while live) session.
    path = _write_index(tmp_path, {
        "named": {
            "name_cached": "kept", "project_label": "demo",
            "project_path": "/tmp/demo", "last_active_at": None,
            "tokens_estimate": 0, "tokens_window_pct": 0,
            "message_count": 0, "first_prompt": "",
        },
        "stub": {
            "name_cached": None, "project_label": "demo",
            "project_path": "/tmp/demo", "last_active_at": None,
            "tokens_estimate": 0, "tokens_window_pct": 0,
            "message_count": 0, "first_prompt": "",
        },
    })
    app = _app(path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # The unnamed stub is live, so a repopulate surfaces it (visibility change).
        app._live_states = {"stub": "working"}
        app._populate()
        await pilot.pause()
        # Park the cursor on the named session's row.
        named_leaf = app._row_nodes["named"][0]
        app._tree.move_cursor(named_leaf)
        assert app._selected_sid() == "named"

        # The stub drops out of the live set -> next poll triggers a full
        # repopulate (the stub's row disappears). Cursor must stay on "named".
        from _pkg import live as _live
        orig_poll = _live.poll
        _live.poll = lambda *a, **k: {}
        try:
            app._poll_live()
        finally:
            _live.poll = orig_poll
        await pilot.pause()

        assert app._selected_sid() == "named"
