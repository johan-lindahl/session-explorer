import os

import pytest

# Import _pkg.tui BEFORE textual: _pkg/__init__ appends the vendored Textual
# (bin/_pkg/_vendor) to sys.path, so `textual` is only importable once _pkg has
# been imported. conftest adds bin/ but not _vendor, so a bare
# `from textual.widgets import ...` at module top would fail on a clean env with
# no site-packages Textual. Order matters here.
from _pkg.tui import SessionExplorerApp
from textual.widgets import Checkbox, Input, TextArea


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
        # sid-1 is seeded in the index fixture as "planning/sprint14": the pane
        # must show that session NAME, not the redundant project/resource label.
        ticket = queue_store.take_ticket(qdir, sid="sid-1", cwd="/x",
                                         command=["t"], pid=1, label="Gym/db",
                                         now_iso="2026-06-06T11:00:00+00:00")
        try:
            app._poll_live()
            await pilot.pause()
            rendered = str(app.query_one("#queues").render())
            assert "holder: ‹planning/sprint14›" in rendered
            assert "Gym/db" not in rendered      # never the redundant label
        finally:
            ticket.release()


@pytest.mark.asyncio
async def test_s_disabled_without_project_selection(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # No project node selected (empty tree) → resource_setup is disabled.
        assert app.check_action("resource_setup", ()) is False


@pytest.mark.asyncio
async def test_queue_help_mentions_leased_ground_and_guide(index_path):
    from _pkg.tui import _queue_help_text, QUEUE_GUIDE_URL
    text = _queue_help_text()
    assert "leased ground" in text.lower()
    # The full, copyable GitHub URL must be present as plain text.
    assert QUEUE_GUIDE_URL in text
    assert QUEUE_GUIDE_URL.startswith("https://github.com/")
    assert QUEUE_GUIDE_URL.endswith("/docs/queue-guide.md")


@pytest.mark.asyncio
async def test_new_session_autoslug_syncs_until_manual_edit(index_path):
    from _pkg.tui import NewSessionScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = NewSessionScreen("proj", name_prefix="", cwd="/x",
                                  root_is_shared=False)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#ns-wt", Checkbox).value = True
        await pilot.pause()
        name = screen.query_one("#ns-name", Input)
        name.value = "Sprint 14 Auth"
        screen.on_input_changed(Input.Changed(name, "Sprint 14 Auth"))
        assert screen.query_one("#ns-wtname", Input).value == "sprint-14-auth"


@pytest.mark.asyncio
async def test_new_session_defaults_worktree_on_for_root_dir_project(index_path):
    from _pkg.tui import NewSessionScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = NewSessionScreen("proj", name_prefix="", cwd="/x",
                                  root_is_shared=True)
        app.push_screen(screen)
        await pilot.pause()
        assert screen.query_one("#ns-wt", Checkbox).value is True


@pytest.mark.asyncio
async def test_manual_worktree_edit_persists_even_when_value_equals_slug(index_path):
    # Finding 4: a user edit to the worktree field stops auto-sync even when the
    # typed value happens to equal worktree_slug(name) — focus, not value, is the
    # signal, so retyping the same slug still counts as manual.
    from _pkg.tui import NewSessionScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = NewSessionScreen("proj", name_prefix="", cwd="/x", root_is_shared=True)
        app.push_screen(screen)
        await pilot.pause()
        name = screen.query_one("#ns-name", Input)
        wt = screen.query_one("#ns-wtname", Input)
        name.focus()
        await pilot.pause()
        name.value = "auth"
        screen.on_input_changed(Input.Changed(name, "auth"))
        assert wt.value == "auth"                 # auto-filled (name focused)
        # User focuses the worktree field and retypes the SAME value → manual.
        wt.focus()
        await pilot.pause()
        screen.on_input_changed(Input.Changed(wt, "auth"))
        # A later name change must NOT overwrite the manual worktree name.
        name.value = "auth two"
        screen.on_input_changed(Input.Changed(name, "auth two"))
        assert screen.query_one("#ns-wtname", Input).value == "auth"


@pytest.mark.asyncio
async def test_activation_hint_is_labeled_experimental(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")          # open the pane on a no-resource project
        await pilot.pause()
        pane = app.query_one("#queues")
        assert pane.display is True
        rendered = str(pane.render()).lower()
        assert "experimental" in rendered
        assert "shared resources" in rendered   # pins the no-resource hint branch


@pytest.mark.asyncio
async def test_shared_root_screen_saves_overlay_shape(index_path, tmp_path,
                                                      monkeypatch):
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import SharedRootScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SharedRootScreen(project_root=str(repo), project_id=pid,
                                  config_path=qcfg)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#sr-protect", TextArea).text = "/.env\n/certs"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "root")
    assert res["acquire"] == "command"
    assert res["command_acquire"] == "session-explorer queue-overlay in"
    assert res["command_release"] == "session-explorer queue-overlay out"
    assert res["sync"]["protect"] == ["/.env", "/certs"]
    assert "guard" not in res


@pytest.mark.asyncio
async def test_shared_root_screen_migrates_existing_root_resource(
        index_path, tmp_path, monkeypatch):
    # An old sync-shaped root resource (e.g. the misapplied bind-mounted-stack
    # template) is migrated onto the overlay shape on save, keeping its id.
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import SharedRootScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    queue_config.add_resource(
        qcfg, project_id=pid, display_path=str(repo),
        resource_id="royal-magento-docker",
        resource={"kind": "root-dir", "path": str(repo), "run_in": "root",
                  "acquire": "sync", "release": "none",
                  "guard": [{"exe": "docker", "sub": ["compose", "up"]}],
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env"]}})
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SharedRootScreen(project_root=str(repo), project_id=pid,
                                  config_path=qcfg)
        app.push_screen(screen)
        await pilot.pause()
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "royal-magento-docker")
    assert res["acquire"] == "command"          # migrated off sync
    assert res["sync"]["protect"] == ["/.git", "/.env"]  # protect carried over
    assert queue_config.get_resource(qcfg, pid, "root") is None  # id kept
