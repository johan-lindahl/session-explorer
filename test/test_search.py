import json

from _pkg import search


_counter = [0]


def _write(tmp_path, lines):
    _counter[0] += 1
    t = tmp_path / f"t{_counter[0]}.jsonl"
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


def _row(sid, name, path, last="2026-01-01T00:00:00Z"):
    return (sid, {"name_cached": name, "transcript_path": path, "last_active_at": last})


def test_search_project_filters_orders_and_counts(tmp_path):
    a = _write(tmp_path, [{"type": "user", "message": {"content": "media-common tag"}},
                          {"type": "assistant", "message": {"content": [{"type": "text", "text": "media-common again"}]}}])
    b = _write(tmp_path, [{"type": "user", "message": {"content": "unrelated"}}])
    c = _write(tmp_path, [{"type": "user", "message": {"content": "media-common here"}}])
    rows = [
        _row("a", "alpha", a, "2026-01-01T00:00:00Z"),
        _row("b", "beta", b, "2026-03-01T00:00:00Z"),
        _row("c", "gamma", c, "2026-02-01T00:00:00Z"),
    ]
    res = search.search_project(rows, "media-common", include_unnamed=False)
    assert [r["sid"] for r in res] == ["c", "a"]      # b has no hit; c newer than a
    assert res[1]["hit_count"] == 2                    # 'a' matched twice


def test_search_project_include_unnamed_toggle(tmp_path):
    p = _write(tmp_path, [{"type": "user", "message": {"content": "media-common"}}])
    rows = [("u", {"name_cached": None, "transcript_path": p, "last_active_at": "2026-01-01T00:00:00Z"})]
    assert search.search_project(rows, "media-common", include_unnamed=False) == []
    got = search.search_project(rows, "media-common", include_unnamed=True)
    assert len(got) == 1 and got[0]["name"] == "(unnamed)"


def test_search_project_skips_missing_transcript(tmp_path):
    rows = [_row("x", "x", str(tmp_path / "gone.jsonl"))]
    assert search.search_project(rows, "media-common", include_unnamed=False) == []


def test_search_project_caps_snippets_with_overflow(tmp_path):
    lines = [{"type": "user", "message": {"content": f"media-common {i}"}} for i in range(9)]
    p = _write(tmp_path, lines)
    res = search.search_project([_row("a", "a", p)], "media-common",
                                include_unnamed=False, max_snippets=5)
    assert res[0]["hit_count"] == 9
    assert len(res[0]["snippets"]) == 5
    assert res[0]["overflow"] == 4


def test_search_project_progress_callback(tmp_path):
    p = _write(tmp_path, [{"type": "user", "message": {"content": "media-common"}}])
    seen = []
    search.search_project([_row("a", "a", p)], "media-common",
                          include_unnamed=False, progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 1)]


def test_format_session_highlights_match_and_shows_count():
    r = {"sid": "a", "name": "team/sprint14", "last_active_at": "2026-01-01T00:00:00Z",
         "hit_count": 2, "overflow": 0,
         "snippets": [{"role": "user", "snippet": "tag media-common now",
                       "match_start": 4, "match_end": 16}]}
    out = search.format_session(r, "media-common")
    assert "team/sprint14" in out
    assert "2 hits" in out
    assert "[reverse]media-common[/reverse]" in out


def test_format_session_escapes_markup_in_snippet():
    r = {"sid": "a", "name": "n", "last_active_at": "", "hit_count": 1, "overflow": 0,
         "snippets": [{"role": "assistant", "snippet": "see [red]media-common[/red]",
                       "match_start": 10, "match_end": 22}]}
    out = search.format_session(r, "media-common")
    assert "\\[red]" in out          # literal bracket escaped, not a real tag


def test_empty_state_names_project_and_toggle():
    out = search.empty_state("media-common", "myrepo", 12, include_unnamed=False)
    assert "media-common" in out and "myrepo" in out and "12" in out


def test_format_match_block_titles_and_highlights():
    snips = [{"role": "user", "snippet": "tag media-common now", "match_start": 4, "match_end": 16},
             {"role": "assistant", "snippet": "renamed media-common", "match_start": 8, "match_end": 20}]
    out = search.format_match_block("media-common", snips)
    assert "Search matches" in out
    assert "media-common" in out
    assert "[reverse]media-common[/reverse]" in out
    assert "you" in out and "claude" in out
