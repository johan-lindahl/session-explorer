import json

from _pkg import search


def _write(tmp_path, lines):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(t)


def test_iter_text_messages_keeps_user_and_assistant_drops_noise(tmp_path):
    p = _write(tmp_path, [
        {"type": "user", "message": {"content": "tag it media-common please"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Renamed media-common."}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "NOISE"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "file-history-snapshot", "snapshot": "x"},
        {"type": "system", "content": "sys"},
    ])
    got = list(search.iter_text_messages(p))
    assert got == [
        ("user", "tag it media-common please"),
        ("assistant", "Renamed media-common."),
    ]


def test_search_transcript_case_insensitive_substring(tmp_path):
    p = _write(tmp_path, [
        {"type": "user", "message": {"content": "Use the Media-Common bucket"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "no match here"}]}},
    ])
    hits = search.search_transcript(p, "media-common")
    assert len(hits) == 1
    assert hits[0]["role"] == "user"
    snip = hits[0]["snippet"]
    assert snip[hits[0]["match_start"]:hits[0]["match_end"]].lower() == "media-common"


def test_search_transcript_empty_needle_returns_nothing(tmp_path):
    p = _write(tmp_path, [{"type": "user", "message": {"content": "anything"}}])
    assert search.search_transcript(p, "") == []


def test_search_transcript_missing_file_is_empty(tmp_path):
    assert search.search_transcript(str(tmp_path / "nope.jsonl"), "x") == []


def test_snippet_collapses_whitespace_and_marks_clipping(tmp_path):
    long_pre = "word " * 40
    long_post = " tail" * 40
    p = _write(tmp_path, [
        {"type": "user", "message": {"content": long_pre + "NEEDLE\nhere" + long_post}},
    ])
    hits = search.search_transcript(p, "needle")
    snip = hits[0]["snippet"]
    assert "\n" not in snip                 # newlines collapsed to spaces
    assert snip.startswith("…") and snip.endswith("…")  # clipped both sides
    assert len(snip) <= search.SNIPPET_WIDTH + 8        # window + ellipses slack
    assert snip[hits[0]["match_start"]:hits[0]["match_end"]] == "NEEDLE"
