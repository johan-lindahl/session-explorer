import json
import os
import tempfile
from _pkg.rename import append_custom_title
from _pkg.jsonl import session_name


def _tmp_jsonl(initial_lines=()):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(path, "w") as f:
        for line in initial_lines:
            f.write(json.dumps(line) + "\n")
    return path


def test_append_writes_minimal_custom_title():
    path = _tmp_jsonl([{"type": "user", "uuid": "u1"}])
    try:
        append_custom_title(path, session_id="sess-1", new_name="planning-sprint14")
        last = None
        with open(path) as f:
            for line in f:
                last = json.loads(line)
        # Verified empirically: exactly these three keys, nothing more.
        assert set(last.keys()) == {"type", "customTitle", "sessionId"}
        assert last["type"] == "custom-title"
        assert last["customTitle"] == "planning-sprint14"
        assert last["sessionId"] == "sess-1"
    finally:
        os.unlink(path)


def test_append_preserves_prior_lines():
    path = _tmp_jsonl([
        {"type": "user", "uuid": "u1"},
        {"type": "assistant", "uuid": "u2"},
    ])
    try:
        append_custom_title(path, session_id="sess-1", new_name="planning-sprint14")
        with open(path) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 3
        assert lines[0]["uuid"] == "u1"
        assert lines[1]["uuid"] == "u2"
        assert lines[2]["type"] == "custom-title"
    finally:
        os.unlink(path)


def test_session_name_reads_back_the_new_name():
    path = _tmp_jsonl([
        {"type": "ai-title", "aiTitle": "old"},
    ])
    try:
        append_custom_title(path, session_id="sess-1", new_name="planning-sprint14")
        assert session_name(path) == "planning-sprint14"
    finally:
        os.unlink(path)
