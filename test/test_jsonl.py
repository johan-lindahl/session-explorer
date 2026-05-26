"""Tests for _pkg.jsonl."""

import os

from _pkg import jsonl

_FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_message_count_named():
    assert jsonl.message_count(os.path.join(_FIX, "named.jsonl")) == 5


def test_message_count_empty():
    assert jsonl.message_count(os.path.join(_FIX, "empty.jsonl")) == 0


def test_first_user_prompt_named():
    assert jsonl.first_user_prompt(os.path.join(_FIX, "named.jsonl")) == "plan sprint 14 work"


def test_first_user_prompt_empty():
    assert jsonl.first_user_prompt(os.path.join(_FIX, "empty.jsonl")) is None


def test_session_name_named_returns_custom_title():
    """custom-title wins over ai-title (precedence rule)."""
    name = jsonl.session_name(os.path.join(_FIX, "named.jsonl"))
    assert name == "planning-sprint14-custom"


def test_session_name_falls_back_to_ai_title(tmp_path):
    """Without a custom-title line, the last ai-title value wins."""
    p = tmp_path / "ai-only.jsonl"
    p.write_text(
        '{"type":"ai-title","aiTitle":"first-title","sessionId":"X"}\n'
        '{"type":"ai-title","aiTitle":"updated-title","sessionId":"X"}\n'
        '{"type":"user","sessionId":"X","timestamp":"2026-05-20T10:00:00Z","message":{"role":"user","content":"hi"}}\n'
    )
    assert jsonl.session_name(str(p)) == "updated-title"


def test_session_name_unnamed_returns_none():
    assert jsonl.session_name(os.path.join(_FIX, "unnamed.jsonl")) is None


def test_tokens_estimate_named_uses_cache_read():
    # Latest assistant message has cache_read_input_tokens=15234
    assert jsonl.tokens_estimate(os.path.join(_FIX, "named.jsonl")) == 15234


def test_tokens_estimate_unnamed_falls_back_to_bytes_over_4():
    """No assistant messages → fall back to bytes/4."""
    path = os.path.join(_FIX, "unnamed.jsonl")
    expected = os.path.getsize(path) // 4
    assert jsonl.tokens_estimate(path) == expected


def test_tokens_estimate_empty():
    assert jsonl.tokens_estimate(os.path.join(_FIX, "empty.jsonl")) == 0


def test_last_active_named():
    """Returns the timestamp of the latest line in the file."""
    # named.jsonl ends with a custom-title at 10:03:00 (after the user msg at 10:02:00)
    assert jsonl.last_active_at(os.path.join(_FIX, "named.jsonl")) == "2026-05-20T10:03:00Z"
