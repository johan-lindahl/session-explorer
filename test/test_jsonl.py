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
    """custom-title is the only source of a session name (user /rename)."""
    name = jsonl.session_name(os.path.join(_FIX, "named.jsonl"))
    assert name == "planning-sprint14-custom"


def test_all_custom_titles_returns_every_value_in_order(tmp_path):
    """Used to shadow stale re-emits: a live Claude session re-writes its
    in-memory custom-title each turn, so the full history (with drift) matters,
    not just the last value session_name() returns."""
    p = tmp_path / "drift.jsonl"
    p.write_text(
        '{"type":"custom-title","customTitle":"old","sessionId":"X"}\n'
        '{"type":"custom-title","customTitle":"new","sessionId":"X"}\n'
        '{"type":"custom-title","customTitle":"old","sessionId":"X"}\n'
    )
    assert jsonl.all_custom_titles(str(p)) == ["old", "new", "old"]


def test_all_custom_titles_empty_when_none(tmp_path):
    p = tmp_path / "none.jsonl"
    p.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
    assert jsonl.all_custom_titles(str(p)) == []


def test_session_name_ignores_ai_title(tmp_path):
    """ai-title alone does NOT count as a session name — only /rename does.

    Without an explicit custom-title line, session_name returns None even when
    Claude has emitted ai-title events during the session.
    """
    p = tmp_path / "ai-only.jsonl"
    p.write_text(
        '{"type":"ai-title","aiTitle":"first-title","sessionId":"X"}\n'
        '{"type":"ai-title","aiTitle":"updated-title","sessionId":"X"}\n'
        '{"type":"user","sessionId":"X","timestamp":"2026-05-20T10:00:00Z","message":{"role":"user","content":"hi"}}\n'
    )
    assert jsonl.session_name(str(p)) is None


def test_session_name_unnamed_returns_none():
    assert jsonl.session_name(os.path.join(_FIX, "unnamed.jsonl")) is None


def test_session_cwd_returns_first_cwd():
    """The envelope cwd is on user/assistant lines, not the leading ai-title."""
    assert jsonl.session_cwd(os.path.join(_FIX, "named.jsonl")) == "/Users/jl/proj/foo"


def test_session_cwd_none_when_no_envelope(tmp_path):
    p = tmp_path / "no-cwd.jsonl"
    p.write_text('{"type":"ai-title","aiTitle":"x","sessionId":"X"}\n')
    assert jsonl.session_cwd(str(p)) is None


def test_relocated_cwd_returns_last_relocated_value(tmp_path):
    """Claude Code writes a `relocated` line when it moves a worktree session's
    transcript to the parent repo; the LAST one is the current cwd."""
    p = tmp_path / "reloc.jsonl"
    p.write_text(
        '{"type":"user","cwd":"/repo/.claude/worktrees/wt","message":{"role":"user","content":"hi"}}\n'
        '{"type":"relocated","relocatedCwd":"/repo","sessionId":"X"}\n'
        '{"type":"relocated","relocatedCwd":"/repo-moved-again","sessionId":"X"}\n'
    )
    assert jsonl.relocated_cwd(str(p)) == "/repo-moved-again"


def test_relocated_cwd_none_when_never_relocated(tmp_path):
    p = tmp_path / "plain.jsonl"
    p.write_text('{"type":"user","cwd":"/repo","message":{"role":"user","content":"hi"}}\n')
    assert jsonl.relocated_cwd(str(p)) is None


def test_effective_cwd_prefers_relocated_over_first_cwd(tmp_path):
    """effective_cwd must return the parent (relocatedCwd), not the dead worktree
    cwd that session_cwd() would return."""
    p = tmp_path / "eff.jsonl"
    p.write_text(
        '{"type":"user","cwd":"/repo/.claude/worktrees/wt","message":{"role":"user","content":"hi"}}\n'
        '{"type":"relocated","relocatedCwd":"/repo","sessionId":"X"}\n'
    )
    assert jsonl.session_cwd(str(p)) == "/repo/.claude/worktrees/wt"
    assert jsonl.effective_cwd(str(p)) == "/repo"


def test_effective_cwd_falls_back_to_first_cwd_when_not_relocated(tmp_path):
    p = tmp_path / "eff2.jsonl"
    p.write_text('{"type":"user","cwd":"/repo/proj","message":{"role":"user","content":"hi"}}\n')
    assert jsonl.effective_cwd(str(p)) == "/repo/proj"


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


def test_latest_model_named_fixture():
    """Reads message.model from the assistant line in named.jsonl."""
    assert jsonl.latest_model(os.path.join(_FIX, "named.jsonl")) == "claude-sonnet-4-6"


def test_latest_model_skips_synthetic(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        '{"type":"assistant","message":{"model":"claude-opus-4-8","usage":{}}}\n'
        '{"type":"assistant","message":{"model":"<synthetic>","usage":{}}}\n'
    )
    # The last *real* model wins; the trailing <synthetic> line is ignored.
    assert jsonl.latest_model(str(p)) == "claude-opus-4-8"


def test_latest_model_none_when_absent(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"type":"user","message":{"content":"hi"}}\n')
    assert jsonl.latest_model(str(p)) is None


def test_worktree_origin_returns_leaf(tmp_path):
    """The worktree leaf is recoverable from the session's cwd history even after
    Claude relocated the transcript to the parent repo (so a relocated session
    can be re-isolated via `claude -w <leaf>`)."""
    p = tmp_path / "wt.jsonl"
    p.write_text(
        '{"type":"user","cwd":"/repo/.claude/worktrees/46415-thing","message":{"role":"user","content":"hi"}}\n'
        '{"type":"relocated","relocatedCwd":"/repo","sessionId":"X"}\n'
        '{"type":"user","cwd":"/repo","message":{"role":"user","content":"more"}}\n'
    )
    assert jsonl.worktree_origin(str(p)) == "46415-thing"


def test_worktree_origin_none_when_never_in_worktree(tmp_path):
    p = tmp_path / "root.jsonl"
    p.write_text('{"type":"user","cwd":"/repo","message":{"role":"user","content":"hi"}}\n')
    assert jsonl.worktree_origin(str(p)) is None


def test_worktree_origin_ignores_deeper_subdir(tmp_path):
    """A cwd inside a subdir of the worktree still yields just the leaf."""
    p = tmp_path / "deep.jsonl"
    p.write_text('{"type":"user","cwd":"/repo/.claude/worktrees/wt1/app/code","message":{"role":"user","content":"hi"}}\n')
    assert jsonl.worktree_origin(str(p)) == "wt1"
