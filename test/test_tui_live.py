from _pkg import tui


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


def test_row_label_prepends_glyph_without_disturbing_name():
    s = {"name_cached": "myname", "last_active_at": None, "tokens_estimate": 0,
         "tokens_window_pct": 0, "message_count": 0, "first_prompt": ""}
    label = tui._row_label("sid12345", s, depth=2, glyph="[green]⠋[/] ")
    assert label.startswith("[green]⠋[/] ")
    assert "myname" in label
