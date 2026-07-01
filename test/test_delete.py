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


def _write_index(tmp_path, entry):
    import json
    p = str(tmp_path / "se-index.json")
    json.dump({"version": 1, "sessions": {"sid-1": entry}}, open(p, "w"))
    return p


def test_delete_drops_summary_entry(tmp_path):
    from _pkg import summary as _summary
    idx = _write_index(tmp_path, {"project_path": "/tmp/x"})
    sp = _summary.default_path_for(idx)
    _summary.set(sp, "sid-1", {"text": "s", "msg_count": 5})
    delete_session(idx, "sid-1")
    assert _summary.get(sp, "sid-1") is None


def test_delete_returns_none_without_worktree(tmp_path):
    idx = _write_index(tmp_path, {"project_path": "/tmp/plain"})
    assert delete_session(idx, "sid-1") is None


def test_delete_purges_worktree(tmp_path, monkeypatch):
    wt = "/repo/.claude/worktrees/feat"
    idx = _write_index(tmp_path, {"project_path": wt})
    calls = {}
    monkeypatch.setattr("_pkg.worktree.purge", lambda p: calls.update(p=p) or "removed")
    assert delete_session(idx, "sid-1") == "removed"
    assert calls["p"] == wt
