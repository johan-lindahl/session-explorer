import json
import os

from _pkg import summary


def test_set_get_remove_roundtrip(tmp_path):
    p = str(tmp_path / "sum.json")
    assert summary.get(p, "sid-1") is None
    summary.set(p, "sid-1", {"text": "did stuff", "generated_at": "2026-07-01T00:00:00Z",
                             "msg_count": 20, "model": "claude-haiku-4-5"})
    got = summary.get(p, "sid-1")
    assert got["text"] == "did stuff" and got["msg_count"] == 20
    summary.remove(p, "sid-1")
    assert summary.get(p, "sid-1") is None


def test_remove_missing_is_noop(tmp_path):
    p = str(tmp_path / "sum.json")
    summary.remove(p, "nope")  # must not raise


def test_load_corrupt_returns_default(tmp_path):
    p = str(tmp_path / "sum.json")
    open(p, "w").write("{not json")
    assert summary.load(p) == {"version": 1, "summaries": {}}


def test_default_path_is_sibling_of_index(tmp_path):
    idx = str(tmp_path / "se-index.json")
    assert summary.default_path_for(idx) == str(tmp_path / "session-explorer-summaries.json")


def test_build_digest_keeps_user_and_assistant_text_drops_tool_noise(tmp_path):
    t = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "message": {"content": "please refactor auth"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Sure, doing it."}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "BIG NOISY OUTPUT"}]}},
        {"type": "file-history-snapshot", "snapshot": "x" * 500},
    ]
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    d = summary.build_digest(str(t))
    assert "please refactor auth" in d
    assert "Sure, doing it." in d
    assert "BIG NOISY OUTPUT" not in d
    assert "file-history-snapshot" not in d


def test_build_digest_elides_when_too_long(tmp_path):
    t = tmp_path / "t.jsonl"
    many = [{"type": "user", "message": {"content": f"line {i} " + "x" * 200}} for i in range(2000)]
    t.write_text("\n".join(json.dumps(x) for x in many) + "\n")
    d = summary.build_digest(str(t), max_chars=1000)
    assert len(d) <= 1200  # cap + elision marker slack
    assert "…" in d  # middle elided


def test_is_stale(tmp_path):
    entry = {"msg_count": 20}
    assert summary.is_stale(entry, 25) is True
    assert summary.is_stale(entry, 20) is False


def test_auto_marker_toggle(tmp_path):
    cd = str(tmp_path)
    assert summary.auto_enabled(cd) is False
    summary.set_auto(cd, True)
    assert summary.auto_enabled(cd) is True
    summary.set_auto(cd, False)
    assert summary.auto_enabled(cd) is False


def test_prompted_marker(tmp_path):
    cd = str(tmp_path)
    assert summary.prompted(cd) is False
    summary.mark_prompted(cd)
    assert summary.prompted(cd) is True
