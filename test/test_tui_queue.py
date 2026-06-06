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


@pytest.mark.asyncio
async def test_s_disabled_without_project_selection(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # No project node selected (empty tree) → resource_setup is disabled.
        assert app.check_action("resource_setup", ()) is False


@pytest.mark.asyncio
async def test_resource_list_lists_configured_resources(index_path, tmp_path, monkeypatch):
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceListScreen
    # A real git repo so project_id resolves.
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    queue_config.add_resource(
        qcfg, project_id=pid, display_path=str(repo), resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    screen = ResourceListScreen(project_root=str(repo), project_id=pid,
                                config_path=qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(screen)
        await pilot.pause()
        # The OptionList contains the resource id.
        assert any("db" in str(o.prompt) for o in screen.query_one("#reslist").options)


@pytest.mark.asyncio
async def test_editor_saves_a_resource(index_path, tmp_path, monkeypatch):
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "ios-sim"
        screen._template_key = "ios-sim"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "ios-sim")
    assert res is not None and res["kind"] == "device" and res["run_in"] == "worktree"


@pytest.mark.asyncio
async def test_editor_saves_guard_and_protect_for_root_dir(index_path, tmp_path, monkeypatch):
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "root"
        screen._template_key = "root-env"                # root-dir · sync
        screen.query_one("#res-guard", TextArea).text = "docker compose up"
        screen.query_one("#res-protect", TextArea).text = "/.git\n/.env\n/certs"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "root")
    assert res["kind"] == "root-dir"
    # A custom guard and a custom protect entry both round-trip into the save.
    assert {"exe": "docker", "sub": ["compose", "up"]} in res["guard"]
    assert "/certs" in res["sync"]["protect"]


@pytest.mark.asyncio
async def test_root_dir_path_is_main_worktree_not_the_selected_worktree(
        index_path, tmp_path, monkeypatch):
    # Finding-1 regression: standing on an arbitrary `git worktree add` node, the
    # saved root-dir path must be the repo's MAIN working tree (spec §1), not the
    # worktree we happened to select.
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "x", "-q"],
                   check=True, env=env)
    wt = tmp_path / "repo-feat"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", str(wt), "-b", "feat"],
                   check=True, env=env)
    pid = project_id.project_id(str(wt))   # same id as repo (git-common-dir)
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(wt), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "root"
        screen._template_key = "root-env"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "root")
    assert res["path"] == project_id.main_root(str(repo))
    assert res["path"] != str(wt)


@pytest.mark.asyncio
async def test_root_dir_ignores_path_edits_and_saves_wait_for(
        index_path, tmp_path, monkeypatch):
    # Finding 1: a typed path is ignored for root-dir (always canonical).
    # Finding 2: wait_for is editable and round-trips into the save.
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "root"
        screen._template_key = "root-env"
        screen.query_one("#res-path", Input).value = "/totally/wrong"   # tampered
        screen.query_one("#res-wait", Input).value = "url http://localhost:8080"
        screen.query_one("#res-wait-timeout", Input).value = "90"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "root")
    assert res["path"] == project_id.main_root(str(repo))     # tamper ignored
    assert res["wait_for"] == {"type": "url",
                               "target": "http://localhost:8080", "timeout": 90.0}


@pytest.mark.asyncio
async def test_editing_clears_stale_command_and_health(index_path, tmp_path, monkeypatch):
    # Finding 3: clearing a field in the editor removes the stale value (and
    # reverts a now-empty command strategy to 'none'), not leaves the old one.
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    queue_config.add_resource(
        qcfg, project_id=pid, display_path=str(repo), resource_id="db",
        resource={"kind": "port", "run_in": "worktree", "acquire": "command",
                  "release": "none", "command_acquire": "reset-db",
                  "health": "pg_isready"})
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id="db")
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-acq", Input).value = ""
        screen.query_one("#res-health", Input).value = ""
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "db")
    assert "command_acquire" not in res
    assert res["acquire"] == "none"         # reverted from 'command'
    assert "health" not in res


@pytest.mark.asyncio
async def test_malformed_wait_for_is_refused_not_dropped(index_path, tmp_path, monkeypatch):
    # Finding (polish): a non-empty but invalid readiness field must block the
    # save with an error, not silently behave like "no readiness check".
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "root"
        screen._template_key = "root-env"
        screen.query_one("#res-wait", Input).value = "urls http://localhost:8080"  # typo
        screen.action_save()
        await pilot.pause()
        assert "readiness" in str(screen.query_one("#res-error", Label).render()).lower()
    # Save was refused → nothing persisted.
    assert queue_config.get_resource(qcfg, pid, "root") is None
