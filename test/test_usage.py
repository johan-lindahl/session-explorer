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
