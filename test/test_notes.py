import os
import tempfile
from _pkg import index as _index
from _pkg.notes import set_notes


def _tmp_index_with_session():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    _index.save(path, {"version": 1, "folders": [],
                       "sessions": {"sid-1": {"name_cached": "x", "project_label": "p"}}})
    return path


def test_set_notes_persists():
    path = _tmp_index_with_session()
    try:
        set_notes(path, "sid-1", "hello\nworld")
        data = _index.load(path)
        assert data["sessions"]["sid-1"]["notes"] == "hello\nworld"
    finally:
        os.unlink(path)


def test_set_notes_preserves_other_fields():
    path = _tmp_index_with_session()
    try:
        set_notes(path, "sid-1", "n")
        data = _index.load(path)
        assert data["sessions"]["sid-1"]["name_cached"] == "x"
        assert data["sessions"]["sid-1"]["project_label"] == "p"
    finally:
        os.unlink(path)
