"""Folder expiry rules: pure evaluation, rule storage, gc.collect_expired, CLI."""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


# --- pure ------------------------------------------------------------------

def test_resolve_nearest_rule_wins_and_project_default():
    from _pkg.expiry import resolve
    rules = {"": {"expire_days": 30}, "bugfix": {"expire_days": 14},
             "planning": {"keep": True}}
    assert resolve(rules, ["bugfix", "urgent"]) == ({"expire_days": 14}, "bugfix")
    assert resolve(rules, ["planning"]) == ({"keep": True}, "planning")
    assert resolve(rules, ["misc"]) == ({"expire_days": 30}, "")
    assert resolve(rules, []) == ({"expire_days": 30}, "")
    assert resolve({"bugfix": {"keep": True}}, ["other"]) == (None, None)


def test_project_rules_drops_invalid():
    from _pkg.expiry import project_rules
    fs = {"rules": {"/r": {"a": {"expire_days": 0}, "b": {"expire_days": "3"},
                           "c": {"expire_days": True}, "d": {"keep": True},
                           "e": {"expire_days": 5}}}}
    assert project_rules(fs, "/r") == {"d": {"keep": True}, "e": {"expire_days": 5}}
    assert project_rules(fs, "/other") == {}


def test_status_kinds_and_remaining():
    from _pkg.expiry import status, is_expired
    rules = {"bugfix": {"expire_days": 14}, "planning": {"keep": True}}
    old = status({"name_cached": "bugfix/x", "last_active_at": _iso(20)}, rules, NOW)
    assert old["kind"] == "expires" and is_expired(old) and old["source"] == "bugfix"
    fresh = status({"name_cached": "bugfix/y", "last_active_at": _iso(10)}, rules, NOW)
    assert not is_expired(fresh)
    assert 3.9 * 86400 < fresh["remaining"] < 4.1 * 86400
    assert status({"name_cached": "planning/p", "last_active_at": _iso(400)},
                  rules, NOW)["kind"] == "keep"
    assert status({"name_cached": "top", "last_active_at": _iso(400)},
                  rules, NOW)["kind"] == "none"
    assert status({"name_cached": None, "last_active_at": _iso(400)},
                  rules, NOW)["kind"] == "none"


def test_idle_reference_matches_age_column_not_transcript_mtime(tmp_path):
    # A rename/move appends to the JSONL (fresh mtime) without being activity;
    # the expiry clock must follow last_active_at like the AGE column does.
    from _pkg.expiry import idle_reference
    t = tmp_path / "t.jsonl"
    t.write_text("{}\n")
    ref = idle_reference({"last_active_at": "2020-01-01T00:00:00Z",
                          "transcript_path": str(t)})
    assert ref.year == 2020
    # mtime is only the fallback when no activity is recorded.
    assert idle_reference({"transcript_path": str(t)}).year >= 2026
    assert idle_reference({}) is None


def test_preview_line_and_badge():
    from _pkg.expiry import preview_line, badge, fmt_remaining
    assert badge({"expire_days": 14}) == "⏱14d" and badge({"keep": True}) == "∞"
    assert badge(None) == ""
    st = {"kind": "expires", "source": "bugfix", "days": 14, "remaining": 3 * 86400 + 5}
    assert preview_line(st) == "in 3d (bugfix/ · 14d idle)"
    assert preview_line({"kind": "keep", "source": "", "days": None, "remaining": None},
                        "magento-os") == "never (magento-os)"
    st2 = dict(st, remaining=-1, skip_reason="worktree has uncommitted changes")
    assert "kept: worktree has uncommitted changes" in preview_line(st2)
    assert fmt_remaining(-5) == "now" and fmt_remaining(7200) == "2h"


def test_expired_sessions_scoped_to_project():
    from _pkg.expiry import expired_sessions
    data = {"sessions": {
        "a": {"project_path": "/r1", "name_cached": "bugfix/a", "last_active_at": _iso(30)},
        "b": {"project_path": "/r1/.claude/worktrees/x", "name_cached": "bugfix/b",
              "last_active_at": _iso(30)},
        "c": {"project_path": "/r2", "name_cached": "bugfix/c", "last_active_at": _iso(30)},
        "d": {"project_path": "/r1", "name_cached": "bugfix/d", "last_active_at": _iso(1)},
    }}
    fs = {"rules": {"/r1": {"bugfix": {"expire_days": 14}}}}
    assert sorted(s for s, _, _ in expired_sessions(data, fs, NOW)) == ["a", "b"]
    assert expired_sessions(data, fs, NOW, project="/r2") == []


# --- folder store ----------------------------------------------------------

def test_set_rule_get_rules_and_clear(tmp_path):
    from _pkg import folder_store as fs
    p = str(tmp_path / "f.json")
    fs.set_rule(p, "/r", "bugfix", {"expire_days": 14})
    fs.set_rule(p, "/r", "", {"expire_days": 30})
    assert fs.get_rules(p, "/r") == {"bugfix": {"expire_days": 14}, "": {"expire_days": 30}}
    assert "bugfix" in fs.list_paths(p, "/r")      # folder kept alive by its rule
    fs.set_rule(p, "/r", "bugfix", None)
    fs.set_rule(p, "/r", "", None)
    assert fs.get_rules(p, "/r") == {}
    assert "/r" not in fs.load(p).get("rules", {})


def test_rename_subtree_rekeys_rules_and_remove_subtree_drops(tmp_path):
    from _pkg import folder_store as fs
    p = str(tmp_path / "f.json")
    fs.set_rule(p, "/r", "bugfix", {"expire_days": 14})
    fs.set_rule(p, "/r", "bugfix/urgent", {"expire_days": 3})
    fs.set_rule(p, "/r", "bugfixes", {"keep": True})     # shares a string prefix only
    fs.rename_subtree(p, "/r", "bugfix", "work/bugs")
    assert fs.get_rules(p, "/r") == {"work/bugs": {"expire_days": 14},
                                     "work/bugs/urgent": {"expire_days": 3},
                                     "bugfixes": {"keep": True}}
    fs.remove_subtree(p, "/r", "work/bugs")
    assert fs.get_rules(p, "/r") == {"bugfixes": {"keep": True}}


# --- gc.collect_expired ----------------------------------------------------

def _old_file(path, days):
    path.write_text('{"type":"user"}\n')
    past = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (past, past))
    return str(path)


def _setup(tmp_path, sessions, rules):
    idx = str(tmp_path / "session-explorer-index.json")
    json.dump({"version": 2, "sessions": sessions}, open(idx, "w"))
    from _pkg import folder_store as fs
    store = fs.default_path_for(idx)
    for project, prules in rules.items():
        for folder, rule in prules.items():
            fs.set_rule(store, project, folder, rule)
    return idx


def test_collect_expired_deletes_only_expired_and_respects_keep_and_live(tmp_path):
    from _pkg import gc, index
    s = {
        "old": {"project_path": "/r", "name_cached": "bugfix/old",
                "last_active_at": _iso(30), "transcript_path": _old_file(tmp_path / "old.jsonl", 30)},
        "new": {"project_path": "/r", "name_cached": "bugfix/new",
                "last_active_at": _iso(2), "transcript_path": _old_file(tmp_path / "new.jsonl", 2)},
        "plan": {"project_path": "/r", "name_cached": "planning/p",
                 "last_active_at": _iso(300), "transcript_path": _old_file(tmp_path / "p.jsonl", 300)},
        "live": {"project_path": "/r", "name_cached": "bugfix/live",
                 "last_active_at": _iso(30), "transcript_path": _old_file(tmp_path / "l.jsonl", 30)},
        "anon": {"project_path": "/r", "name_cached": None,
                 "last_active_at": _iso(300), "transcript_path": _old_file(tmp_path / "a.jsonl", 300)},
    }
    idx = _setup(tmp_path, s, {"/r": {"": {"expire_days": 7}, "bugfix": {"expire_days": 14},
                                       "planning": {"keep": True}}})
    dry = gc.collect_expired(idx, now=NOW, dry_run=True, live_ids={"live"})
    assert [it["sid"] for it in dry["removed"]] == ["old"]
    assert os.path.exists(tmp_path / "old.jsonl")
    res = gc.collect_expired(idx, now=NOW, live_ids={"live"})
    assert [it["sid"] for it in res["removed"]] == ["old"]
    assert res["skipped_live"] == ["live"]
    assert not os.path.exists(tmp_path / "old.jsonl")
    assert set(index.load(idx)["sessions"]) == {"new", "plan", "live", "anon"}


def test_collect_expired_noop_without_rules(tmp_path):
    from _pkg import gc
    s = {"old": {"project_path": "/r", "name_cached": "bugfix/old", "last_active_at": _iso(300)}}
    idx = _setup(tmp_path, s, {})
    assert gc.collect_expired(idx, now=NOW)["removed"] == []


def _repo_with_worktree(tmp_path, leaf):
    repo = tmp_path / "repo"
    if not repo.exists():
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, check=True)
    wt = str(repo / ".claude" / "worktrees" / leaf)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b",
                    f"worktree-{leaf}", wt], check=True, capture_output=True)
    return str(repo), wt


def test_collect_expired_purges_clean_worktree_and_keeps_dirty(tmp_path):
    from _pkg import gc, index
    repo, clean = _repo_with_worktree(tmp_path, "clean")
    _, dirty = _repo_with_worktree(tmp_path, "dirty")
    open(os.path.join(dirty, "wip.txt"), "w").write("unsaved")
    s = {
        "c": {"project_path": clean, "name_cached": "bugfix/c", "last_active_at": _iso(30),
              "transcript_path": _old_file(tmp_path / "c.jsonl", 30)},
        "d": {"project_path": dirty, "name_cached": "bugfix/d", "last_active_at": _iso(30),
              "transcript_path": _old_file(tmp_path / "d.jsonl", 30)},
    }
    idx = _setup(tmp_path, s, {repo: {"bugfix": {"expire_days": 14}}})
    res = gc.collect_expired(idx, now=NOW)
    assert [it["sid"] for it in res["removed"]] == ["c"]
    assert res["skipped_dirty"] == ["d"]
    assert not os.path.isdir(clean)
    assert os.path.isdir(dirty) and os.path.exists(tmp_path / "d.jsonl")
    assert set(index.load(idx)["sessions"]) == {"d"}


def test_cli_gc_reports_expired(tmp_path, monkeypatch):
    from _pkg import cli
    s = {"old": {"project_path": "/r", "name_cached": "bugfix/old",
                 "last_active_at": "2020-01-01T00:00:00Z",
                 "transcript_path": _old_file(tmp_path / "old.jsonl", 2000)}}
    idx = _setup(tmp_path, s, {"/r": {"bugfix": {"expire_days": 14}}})
    monkeypatch.setenv("SESSION_EXPLORER_INDEX", idx)
    monkeypatch.setenv("SESSION_EXPLORER_LIVE", str(tmp_path / "live.json"))
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(["index", "--gc", "--dry-run"])
    assert rc == 0
    out = buf.getvalue()
    assert "Would delete 1 expired session(s) by folder rule" in out
    assert "bugfix/old" in out
    assert os.path.exists(tmp_path / "old.jsonl")
