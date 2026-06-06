import json
import os

from _pkg import ui_state


def test_default_path_is_sibling_of_index(tmp_path):
    idx = str(tmp_path / "session-explorer-index.json")
    assert ui_state.default_path_for(idx) == str(tmp_path / "session-explorer-ui.json")


def test_load_missing_returns_default(tmp_path):
    p = str(tmp_path / "session-explorer-ui.json")
    assert ui_state.load(p) == {"version": 1, "queue_pane_visible": False}


def test_load_corrupt_returns_default(tmp_path):
    p = tmp_path / "session-explorer-ui.json"
    p.write_text("{not json")
    assert ui_state.load(str(p)) == {"version": 1, "queue_pane_visible": False}


def test_set_and_load_roundtrip(tmp_path):
    p = str(tmp_path / "session-explorer-ui.json")
    ui_state.set_queue_pane_visible(p, True)
    assert ui_state.load(p)["queue_pane_visible"] is True
    ui_state.set_queue_pane_visible(p, False)
    assert ui_state.load(p)["queue_pane_visible"] is False


def test_set_preserves_unknown_keys(tmp_path):
    p = tmp_path / "session-explorer-ui.json"
    p.write_text(json.dumps({"version": 1, "queue_pane_visible": False, "future": 7}))
    ui_state.set_queue_pane_visible(str(p), True)
    data = json.loads(p.read_text())
    assert data["future"] == 7 and data["queue_pane_visible"] is True
