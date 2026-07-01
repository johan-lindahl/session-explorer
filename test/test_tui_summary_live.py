import json

from _pkg import tui, summary as _summary


def _seed(tmp_path, name="alpha", msgs=12):
    proj = str(tmp_path / "repo")
    ta = tmp_path / "a.jsonl"
    lines = [{"type": "user", "message": {"content": f"message {i}"}} for i in range(msgs)]
    ta.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    idx = tmp_path / "se-index.json"
    idx.write_text(json.dumps({"version": 2, "sessions": {
        "a": {"name_cached": name, "project_path": proj, "transcript_path": str(ta),
              "last_active_at": "2026-01-02T00:00:00Z", "message_count": msgs},
    }}))
    for m in ("help-seen", "retention-declined", "summaries-prompted"):
        (tmp_path / f".session-explorer.{m}").write_text("")
    return str(idx), proj


async def test_u_on_live_session_summarizes_as_provisional(tmp_path, monkeypatch):
    idx, proj = _seed(tmp_path)
    app = tui.SessionExplorerApp(index_path=idx)
    calls = []
    async with app.run_test() as pilot:
        app._scanned = True
        app._live_states["a"] = "idle"          # mark the session live
        app._populate()
        await pilot.pause()
        app._restore_cursor_to_sid("a")
        monkeypatch.setattr(app, "_start_summarize",
                            lambda *a, **k: calls.append((a, k)))
        app.action_update_summary()
        assert calls, "u should summarise a live session, not refuse it"
        assert calls[0][1].get("provisional") is True


async def test_u_ignored_while_already_summarizing(tmp_path, monkeypatch):
    idx, proj = _seed(tmp_path)
    app = tui.SessionExplorerApp(index_path=idx)
    calls = []
    async with app.run_test() as pilot:
        app._scanned = True
        app._populate()
        await pilot.pause()
        app._restore_cursor_to_sid("a")
        app._summarizing.add("a")               # a run is already in flight
        monkeypatch.setattr(app, "_start_summarize", lambda *a, **k: calls.append(1))
        app.action_update_summary()
        assert not calls, "a second u while summarising must be ignored"


async def test_exit_regenerates_provisional_even_without_auto(tmp_path, monkeypatch):
    idx, proj = _seed(tmp_path)
    app = tui.SessionExplorerApp(index_path=idx)
    calls = []
    async with app.run_test() as pilot:
        # a provisional (live-made) summary exists; auto-summaries is OFF (no marker)
        _summary.set(_summary.default_path_for(idx), "a",
                     {"text": "snapshot", "generated_at": "2026-01-01T00:00:00Z",
                      "msg_count": 5, "model": "m", "provisional": True})
        monkeypatch.setattr(app, "_start_summarize", lambda *a, **k: calls.append((a, k)))
        app._maybe_summarize({"a"})
        assert calls, "a provisional summary must be regenerated on exit"
        assert calls[0][1].get("provisional", False) is False   # regenerated as final


async def test_exit_no_regen_for_final_summary_without_auto(tmp_path, monkeypatch):
    idx, proj = _seed(tmp_path)
    app = tui.SessionExplorerApp(index_path=idx)
    calls = []
    async with app.run_test() as pilot:
        _summary.set(_summary.default_path_for(idx), "a",
                     {"text": "final", "generated_at": "x", "msg_count": 12,
                      "model": "m", "provisional": False})
        monkeypatch.setattr(app, "_start_summarize", lambda *a, **k: calls.append(1))
        app._maybe_summarize({"a"})
        assert not calls, "a final summary must not regenerate on exit when auto is off"
