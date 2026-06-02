import os
from _pkg import usage

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "usage_panel.txt")


def _panel() -> str:
    with open(FIX, encoding="utf-8") as f:
        return f.read()


def test_parse_usage_reads_session_percent_and_reset():
    info = usage.parse_usage(_panel())
    assert info is not None
    assert info.percent == 23                 # the SESSION bucket (first/anchored)
    assert info.reset_label == "1:29am"


def test_parse_usage_strips_ansi_escapes():
    raw = "Current session\n\x1b[34m███\x1b[0m 42% used\nResets 9:05pm (UTC)\n"
    info = usage.parse_usage(raw)
    assert info.percent == 42
    assert info.reset_label == "9:05pm"


def test_parse_usage_returns_none_when_no_percent():
    assert usage.parse_usage("just some unrelated text") is None


def test_parse_usage_returns_none_on_empty():
    assert usage.parse_usage("") is None


def test_render_bar_fills_proportionally():
    info = usage.UsageInfo(percent=50, reset_label="1:29am")
    # 50% of 12 cells = 6 filled
    assert usage.render_bar(info, cells=12) == " [██████░░░░░░] 50% ↺1:29am"


def test_render_bar_zero_and_full():
    assert usage.render_bar(usage.UsageInfo(0, "9:00am"), cells=10) == \
        " [░░░░░░░░░░] 0% ↺9:00am"
    assert usage.render_bar(usage.UsageInfo(100, "9:00am"), cells=10) == \
        " [██████████] 100% ↺9:00am"


def test_render_bar_clamps_rounding():
    # 99% of 10 cells rounds to 10 filled but must not exceed cells
    s = usage.render_bar(usage.UsageInfo(99, "9:00am"), cells=10)
    assert s.count("█") <= 10
    assert s.startswith(" [")
    assert "99% ↺9:00am" in s


def test_has_usage_panel_detects_percent_line():
    assert usage.has_usage_panel("blah 18% used blah") is True
    assert usage.has_usage_panel("welcome to claude") is False


def test_looks_like_trust_prompt():
    assert usage.looks_like_trust_prompt(
        "Do you trust the files in this folder?") is True
    assert usage.looks_like_trust_prompt("normal prompt") is False


def test_parse_usage_returns_none_when_percent_but_no_time():
    assert usage.parse_usage("Current session\n23% used\nResets someday") is None


def test_parse_usage_rejects_out_of_range_percent():
    # 4-digit and negative percents violate the 0-100 contract -> no match
    assert usage.parse_usage("Current session\n1000% used\nResets 1:00am") is None
    assert usage.parse_usage("Current session\n-5% used\nResets 1:00am") is None


def test_probe_cwd_under_claude_dir():
    assert usage.probe_cwd("/home/x/.claude") == \
        "/home/x/.claude/.session-explorer-probe"


def test_cleanup_probe_transcripts_matches_mangled_folder(tmp_path):
    # Claude mangles the probe cwd "~/.claude/.session-explorer-probe" into a
    # project-folder name where dots/slashes become dashes:
    # "...--session-explorer-probe". The cleanup glob must match that, and must
    # NOT touch a normal project's transcripts.
    projects = tmp_path / "projects"
    probe_proj = projects / "-Users-x--claude--session-explorer-probe"
    probe_proj.mkdir(parents=True)
    probe_jsonl = probe_proj / "abc.jsonl"
    probe_jsonl.write_text("{}")
    normal_proj = projects / "-Users-x-Projects-Foo"
    normal_proj.mkdir(parents=True)
    keep = normal_proj / "def.jsonl"
    keep.write_text("{}")

    usage.cleanup_probe_transcripts(str(tmp_path))

    assert not probe_jsonl.exists()   # probe transcript removed
    assert keep.exists()              # normal session untouched
