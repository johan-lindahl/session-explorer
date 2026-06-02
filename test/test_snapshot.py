import json
from _pkg import snapshot


def _write_jsonl(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_transcript_tail_renders_user_assistant_and_tools(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        {"type": "user", "message": {"content": "add retry to fetch"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Editing index.py"},
            {"type": "tool_use", "name": "Edit"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash"}]}},
    ])
    out = snapshot.transcript_tail(str(p), limit=10)
    assert "you: add retry to fetch" in out
    assert "claude: Editing index.py" in out
    assert "tool: Edit" in out
    assert "tool: Bash" in out


def test_transcript_tail_keeps_only_last_n(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [{"type": "user", "message": {"content": f"msg{i}"}}
                     for i in range(20)])
    out = snapshot.transcript_tail(str(p), limit=3)
    assert "msg19" in out and "msg0" not in out
    assert len(out.splitlines()) == 3


def test_transcript_tail_missing_file_is_empty(tmp_path):
    assert snapshot.transcript_tail(str(tmp_path / "nope.jsonl")) == ""


def test_snapshot_uses_capture_for_tmux_window():
    text, is_ansi = snapshot.snapshot(
        sid="sid-1", transcript_path="/x.jsonl",
        tmux_window_names=["sid-1", "sid-2"],
        capture_fn=lambda s: f"CAPTURED:{s}",
        tail_fn=lambda p, limit=12: "TAIL")
    assert text == "CAPTURED:sid-1"
    assert is_ansi is True


def test_snapshot_falls_back_to_tail_for_non_window():
    text, is_ansi = snapshot.snapshot(
        sid="sid-9", transcript_path="/x.jsonl",
        tmux_window_names=["sid-1"],
        capture_fn=lambda s: "CAPTURED",
        tail_fn=lambda p, limit=12: "TAIL")
    assert text == "TAIL"
    assert is_ansi is False
