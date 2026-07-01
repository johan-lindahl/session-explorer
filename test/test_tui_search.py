import json

from _pkg import tui


def _seed_index(tmp_path):
    """One project 'repo' with two named sessions; only one mentions the needle."""
    proj = str(tmp_path / "repo")
    ta = tmp_path / "a.jsonl"
    ta.write_text(json.dumps({"type": "user", "message": {"content": "tag media-common"}}) + "\n")
    tb = tmp_path / "b.jsonl"
    tb.write_text(json.dumps({"type": "user", "message": {"content": "nothing here"}}) + "\n")
    idx = tmp_path / "se-index.json"
    idx.write_text(json.dumps({"version": 2, "sessions": {
        "a": {"name_cached": "alpha", "project_path": proj, "transcript_path": str(ta),
              "last_active_at": "2026-01-02T00:00:00Z"},
        "b": {"name_cached": "beta", "project_path": proj, "transcript_path": str(tb),
              "last_active_at": "2026-01-01T00:00:00Z"},
    }}))
    return str(idx), proj


async def test_search_screen_lists_only_matching_sessions(tmp_path):
    idx, proj = _seed_index(tmp_path)
    rows = [("a", {"name_cached": "alpha", "transcript_path": str(tmp_path / "a.jsonl"),
                   "last_active_at": "2026-01-02T00:00:00Z"}),
            ("b", {"name_cached": "beta", "transcript_path": str(tmp_path / "b.jsonl"),
                   "last_active_at": "2026-01-01T00:00:00Z"})]
    app = tui.SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        screen = tui.SearchScreen(rows, "repo")
        await app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#search-input").value = "media-common"
        await screen._run_search()          # await the worker deterministically
        await pilot.pause()
        ol = screen.query_one("#search-results")
        # exactly one matching session, and its option id is the sid
        assert ol.option_count == 1
        assert ol.get_option_at_index(0).id == "a"
