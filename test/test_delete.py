import os
import tempfile
from _pkg import index as _index
from _pkg.delete import delete_session


def _setup():
    fd, idx = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    fd, jsonl = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(jsonl, "w") as f:
        f.write('{"type":"user"}\n')
    _index.save(idx, {"version": 1, "folders": [],
                      "sessions": {"sid": {"transcript_path": jsonl}}})
    return idx, jsonl


def test_delete_removes_jsonl_and_index_entry():
    idx, jsonl = _setup()
    try:
        delete_session(idx, "sid")
        assert not os.path.exists(jsonl)
        assert "sid" not in _index.load(idx)["sessions"]
    finally:
        if os.path.exists(idx):
            os.unlink(idx)


def test_delete_tolerates_missing_jsonl():
    idx, jsonl = _setup()
    os.unlink(jsonl)
    try:
        delete_session(idx, "sid")  # should not raise
        assert "sid" not in _index.load(idx)["sessions"]
    finally:
        os.unlink(idx)


def test_delete_unknown_id_is_noop():
    idx, jsonl = _setup()
    try:
        delete_session(idx, "ghost")
        assert "sid" in _index.load(idx)["sessions"]
    finally:
        os.unlink(idx)
        os.unlink(jsonl)
