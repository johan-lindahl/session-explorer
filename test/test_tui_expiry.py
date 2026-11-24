"""Folder expiry rules in the TUI: `t` dialog, confirm, badges, preview, pass."""

import json
import os
from datetime import datetime, timedelta, timezone


def _iso(days_ago):
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat().replace("+00:00", "Z")


def _write(tmp_path, sessions, rules=None):
    p = str(tmp_path / "se-index.json")
    json.dump({"version": 2, "sessions": sessions}, open(p, "w"))
    for m in ("help-seen", "retention-declined", "summaries-prompted"):
        (tmp_path / f".session-explorer.{m}").write_text("")
    if rules:
        from _pkg import folder_store as fs
        for folder, rule in rules.items():
            fs.set_rule(fs.default_path_for(p), "/w/alpha", folder, rule)
    return p


def _sess(name, days, tmp_path=None, sid=None):
    s = {"project_label": "alpha", "project_path": "/w/alpha",
         "name_cached": name, "last_active_at": _iso(days)}
    if tmp_path is not None:
        t = tmp_path / f"{sid}.jsonl"
        t.write_text('{"type":"user"}\n')
        past = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        os.utime(t, (past, past))
        s["transcript_path"] = str(t)
    return s


def _node_by_label(app, prefix):
    stack = list(app._tree.root.children)
    while stack:
        n = stack.pop(0)
        if str(n.label).startswith(prefix):
            return n
        stack.extend(n.children)
    return None


def _rules(p):
    from _pkg import folder_store as fs
    return fs.get_rules(fs.default_path_for(p), "/w/alpha")


async def _cursor_to(app, pilot, prefix):
    node = _node_by_label(app, prefix)
    assert node is not None, prefix
    app._tree.move_cursor(node)
    await pilot.pause()


async def test_t_sets_keep_rule_and_badge(tmp_path):
    from _pkg.tui import SessionExplorerApp, ExpiryRuleScreen
    p = _write(tmp_path, {"a": _sess("planning/x", 3)})
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _cursor_to(app, pilot, "planning/")
        await pilot.press("t")
        await pilot.pause()
        assert isinstance(app.screen, ExpiryRuleScreen)
        await pilot.press("down")             # Inherit → Keep forever
        await pilot.press("enter")
        await pilot.pause()
        assert _rules(p) == {"planning": {"keep": True}}
        assert "∞" in str(_node_by_label(app, "planning/").label)


async def test_t_days_rule_confirms_before_deleting_and_cancel_saves_nothing(tmp_path):
    from _pkg.tui import SessionExplorerApp, ConfirmScreen, RenameScreen
    p = _write(tmp_path, {"old": _sess("bugfix/old", 30, tmp_path, "old"),
                          "new": _sess("bugfix/new", 1, tmp_path, "new")})
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _cursor_to(app, pilot, "bugfix/")
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("down", "down", "enter")   # Delete after N days…
        await pilot.pause()
        assert isinstance(app.screen, RenameScreen)
        inp = app.screen.query_one("Input")
        inp.value = "14"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "deletes 1 session" in str(app.screen._prompt)
        await pilot.press("n")
        await pilot.pause()
    assert _rules(p) == {}
    assert os.path.exists(tmp_path / "old.jsonl")


async def test_t_days_rule_confirmed_deletes_expired(tmp_path):
    from _pkg.tui import SessionExplorerApp, ConfirmScreen
    from _pkg import index
    p = _write(tmp_path, {"old": _sess("bugfix/old", 30, tmp_path, "old"),
                          "new": _sess("bugfix/new", 1, tmp_path, "new")})
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._save_expiry_rule("/w/alpha", "bugfix", {"expire_days": 14})
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert _rules(p) == {"bugfix": {"expire_days": 14}}
        assert "⏱14d" in str(_node_by_label(app, "bugfix/").label)
    assert set(index.load(p)["sessions"]) == {"new"}
    assert not os.path.exists(tmp_path / "old.jsonl")


async def test_rule_with_nothing_expiring_saves_without_confirm(tmp_path):
    from _pkg.tui import SessionExplorerApp, ConfirmScreen
    p = _write(tmp_path, {"new": _sess("bugfix/new", 1)})
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._save_expiry_rule("/w/alpha", "", {"expire_days": 30})
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        assert _rules(p) == {"": {"expire_days": 30}}
        assert "⏱30d" in str(_node_by_label(app, "alpha").label)


async def test_launch_pass_deletes_expired_and_preview_shows_countdown(tmp_path):
    from _pkg.tui import SessionExplorerApp
    from _pkg import index
    p = _write(tmp_path, {"old": _sess("bugfix/old", 30, tmp_path, "old"),
                          "new": _sess("bugfix/new", 11, tmp_path, "new")},
               rules={"bugfix": {"expire_days": 14}})
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert set(index.load(p)["sessions"]) == {"new"}
        leaf = app._row_nodes["new"][0]
        st = leaf.data["expiry"]
        assert st["kind"] == "expires" and st["source"] == "bugfix"
        from _pkg.tui import _preview_text
        text = _preview_text(leaf.data)
        assert "Expires" in text and "in 2d (bugfix/ · 14d idle)" in text


async def test_live_session_not_deleted_by_launch_pass(tmp_path, monkeypatch):
    from _pkg.tui import SessionExplorerApp
    from _pkg import index, live as _live
    monkeypatch.setattr(_live, "poll", lambda path: {"old": "idle"})
    p = _write(tmp_path, {"old": _sess("bugfix/old", 30, tmp_path, "old")},
               rules={"bugfix": {"expire_days": 14}})
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
    assert "old" in index.load(p)["sessions"]


def test_row_label_tints_age_within_last_48h():
    from _pkg.tui import _row_label
    s = {"name_cached": "bugfix/x", "last_active_at": _iso(13),
         "expiry": {"kind": "expires", "source": "bugfix", "days": 14,
                    "remaining": 86400}}
    assert "[red]" in _row_label("sid", s, 2)
    s["expiry"]["remaining"] = 5 * 86400
    assert "[red]" not in _row_label("sid", s, 2)


async def test_t_on_unnamed_group_warns(tmp_path):
    from _pkg.tui import SessionExplorerApp, ExpiryRuleScreen
    p = _write(tmp_path, {"a": _sess(None, 1), "b": _sess("x", 1)})
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab", "tab")        # mode 2: show unnamed
        await pilot.pause()
        await _cursor_to(app, pilot, "(unnamed)/")
        await pilot.press("t")
        await pilot.pause()
        assert not isinstance(app.screen, ExpiryRuleScreen)


def test_help_mentions_expiry_rules():
    from _pkg.tui import _help_text
    assert "Expiry rules" in _help_text()


async def test_confirm_counts_only_newly_expired(tmp_path, monkeypatch):
    # Sessions already expired under the existing rules aren't attributed to
    # the new rule (here: the pass is disabled, so `mig` stays expired).
    from _pkg.tui import SessionExplorerApp, ConfirmScreen
    p = _write(tmp_path, {"mig": _sess("migration/m", 90, tmp_path, "mig"),
                          "bug": _sess("bugfix/b", 30, tmp_path, "bug")},
               rules={"": {"expire_days": 60}})
    monkeypatch.setattr(SessionExplorerApp, "_expiry_pass", lambda self: None)
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._save_expiry_rule("/w/alpha", "bugfix", {"expire_days": 14})
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "deletes 1 session" in app.screen._prompt
        assert "bugfix/b" in app.screen._prompt and "migration/m" not in app.screen._prompt
        await pilot.press("n")
