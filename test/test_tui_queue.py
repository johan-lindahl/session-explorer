import os

import pytest

# Import _pkg.tui BEFORE textual: _pkg/__init__ appends the vendored Textual
# (bin/_pkg/_vendor) to sys.path, so `textual` is only importable once _pkg has
# been imported. conftest adds bin/ but not _vendor, so a bare
# `from textual.widgets import ...` at module top would fail on a clean env with
# no site-packages Textual. Order matters here.
from _pkg.tui import SessionExplorerApp
from textual.widgets import Checkbox, Input, Label, TextArea


def _binding_keys(action):
    return {b.key for b in SessionExplorerApp.BINDINGS if b.action == action}


def test_q_bound_to_toggle_queues_not_quit():
    assert "q" in _binding_keys("toggle_queues")
    assert "q" not in _binding_keys("quit")


def test_x_bound_to_quit():
    assert "x" in _binding_keys("quit")


@pytest.mark.asyncio
async def test_x_exits_app(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
    # run_test context exiting cleanly is the assertion; no hang.


from _pkg import queue_config, ui_state


@pytest.mark.asyncio
async def test_queue_pane_hidden_by_default(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#queues").display is False


@pytest.mark.asyncio
async def test_q_with_no_resources_shows_hint_then_persists_off_render(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        pane = app.query_one("#queues")
        assert pane.display is True
        assert "Set up" in str(pane.render()) or "shared resources" in str(pane.render()).lower()


@pytest.mark.asyncio
async def test_q_toggle_persists_flag(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    ui_path = ui_state.default_path_for(index_path)
    assert ui_state.load(ui_path)["queue_pane_visible"] is True


@pytest.mark.asyncio
async def test_persisted_visible_with_only_unrelated_idle_renders_nothing(
        index_path, tmp_path, monkeypatch):
    # Zero-footprint regression (spec §9): a persisted queue_pane_visible=true,
    # whose ONLY configured resource is idle AND belongs to an unrelated project
    # (not the selected one), must render NOTHING on launch — never the row.
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    monkeypatch.setenv("SESSION_EXPLORER_QUEUES_ROOT", str(tmp_path / "queues"))
    queue_config.add_resource(
        qcfg, project_id="zzz999", display_path="/repo/Other", resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    # The fixture's session project '/tmp/demo-project' is not a git repo, so its
    # project_id is None — it can never match the unrelated 'zzz999' resource.
    ui_state.set_queue_pane_visible(ui_state.default_path_for(index_path), True)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#queues").display is False


@pytest.mark.asyncio
async def test_poll_live_refreshes_the_pane(index_path, tmp_path, monkeypatch):
    from _pkg import queue_config, queue_run, queue_store
    qcfg = str(tmp_path / "qc.json")
    queues = str(tmp_path / "queues")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    monkeypatch.setenv("SESSION_EXPLORER_QUEUES_ROOT", queues)
    queue_config.add_resource(
        qcfg, project_id="abc123", display_path="/repo/Gym", resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")              # show pane FIRST, no ticket yet
        await pilot.pause()
        # Pane is up but the resource is idle and unselected → no holder shown.
        assert "holder:" not in str(app.query_one("#queues").render())
        # Now a holder appears AFTER the pane is shown; only _poll_live can
        # surface it (action_toggle_queues already ran).
        qdir = queue_run.queue_dir(queues, "abc123", "db")
        ticket = queue_store.take_ticket(qdir, sid="feat-auth", cwd="/x",
                                         command=["t"], pid=1, label="Gym/db",
                                         now_iso="2026-06-06T11:00:00+00:00")
        try:
            app._poll_live()
            await pilot.pause()
            assert "holder: Gym/db" in str(app.query_one("#queues").render())
        finally:
            ticket.release()
