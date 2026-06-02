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


def test_has_usage_panel_detects_percent_line():
    assert usage.has_usage_panel("blah 18% used blah") is True
    assert usage.has_usage_panel("welcome to claude") is False


def test_looks_like_trust_prompt():
    assert usage.looks_like_trust_prompt(
        "Do you trust the files in this folder?") is True
    assert usage.looks_like_trust_prompt("normal prompt") is False


def test_probe_cwd_under_claude_dir():
    assert usage.probe_cwd("/home/x/.claude") == \
        "/home/x/.claude/.session-explorer-probe"
