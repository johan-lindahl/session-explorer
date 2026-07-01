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
    # Suppress the on-mount first-run prompts (same markers the shared
    # index_path fixture creates) so no ConfirmScreen steals the `f` keypress.
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    (tmp_path / ".session-explorer.summaries-prompted").write_text("")
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
        ol = screen.query_one("#search-results")
        assert ol.display is False           # hidden until a search runs (no empty box)
        screen.query_one("#search-input").value = "media-common"
        await screen._run_search()          # await the worker deterministically
        await pilot.pause()
        # exactly one matching session, and its option id is the sid
        assert ol.option_count == 1
        assert ol.get_option_at_index(0).id == "a"
        assert ol.display is True            # shown once there are results


async def test_search_screen_hides_results_when_no_match(tmp_path):
    idx, proj = _seed_index(tmp_path)
    rows = [("b", {"name_cached": "beta", "transcript_path": str(tmp_path / "b.jsonl"),
                   "last_active_at": "2026-01-01T00:00:00Z"})]
    app = tui.SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        screen = tui.SearchScreen(rows, "repo")
        await app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#search-input").value = "media-common"
        await screen._run_search()
        await pilot.pause()
        ol = screen.query_one("#search-results")
        assert ol.option_count == 0
        assert ol.display is False           # no empty box to Tab into


async def test_search_ctrl_u_toggles_include_unnamed(tmp_path):
    idx, proj = _seed_index(tmp_path)
    app = tui.SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        screen = tui.SearchScreen([], "repo")
        await app.push_screen(screen)
        await pilot.pause()
        assert screen.include_unnamed is False
        screen.action_toggle_unnamed()       # the ctrl+u action
        assert screen.include_unnamed is True
        screen.action_toggle_unnamed()
        assert screen.include_unnamed is False


def test_rows_for_project_groups_by_repo_root(tmp_path):
    idx, proj = _seed_index(tmp_path)
    # add a worktree session of the same repo + a session of a different repo
    data = json.loads((tmp_path / "se-index.json").read_text())
    data["sessions"]["wt"] = {"name_cached": "wt", "project_path": proj + "/.claude/worktrees/x",
                              "transcript_path": str(tmp_path / "a.jsonl"), "last_active_at": "z"}
    data["sessions"]["other"] = {"name_cached": "o", "project_path": str(tmp_path / "elsewhere"),
                                 "transcript_path": str(tmp_path / "a.jsonl"), "last_active_at": "z"}
    (tmp_path / "se-index.json").write_text(json.dumps(data))
    app = tui.SessionExplorerApp(index_path=idx)
    rows = app._rows_for_project(proj)
    sids = {sid for sid, _ in rows}
    assert sids == {"a", "b", "wt"}          # worktree collapses in; other repo excluded


async def test_f_key_opens_search_and_selection_moves_cursor(tmp_path):
    idx, proj = _seed_index(tmp_path)
    app = tui.SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        app._scanned = True
        app._populate()
        await pilot.pause()
        await pilot.press("down")   # move cursor off the hidden root onto the project
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, tui.SearchScreen)
        screen = app.screen
        screen.query_one("#search-input").value = "media-common"
        await screen._run_search()
        await pilot.pause()
        screen.dismiss("a")                  # simulate picking session 'a'
        await pilot.pause()
        node = app._tree.cursor_node
        assert node is not None and (node.data or {}).get("sid") == "a"


async def test_search_via_worker_path_renders_results(tmp_path):
    # Reproduces the real Enter path: on_input_submitted wraps _run_search in a
    # worker; an exclusive inner scan worker must NOT cancel that outer worker
    # (which raised WorkerCancelled → "search failed").
    idx, proj = _seed_index(tmp_path)
    rows = [("a", {"name_cached": "alpha", "transcript_path": str(tmp_path / "a.jsonl"),
                   "last_active_at": "2026-01-02T00:00:00Z"})]
    app = tui.SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        screen = tui.SearchScreen(rows, "repo")
        await app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#search-input").value = "media-common"
        w = screen.run_worker(screen._run_search())   # mimic on_input_submitted
        await w.wait()
        await pilot.pause()
        ol = screen.query_one("#search-results")
        assert ol.option_count == 1
        assert ol.display is True
