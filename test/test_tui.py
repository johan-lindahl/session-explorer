import json
import os
import tempfile
import pytest


@pytest.fixture
def index_path():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    json.dump({
        "version": 1, "folders": [],
        "sessions": {
            "sid-1": {
                "project_label": "demo",
                "name_cached": "planning-sprint14",
                "last_active_at": "2026-05-27T10:00:00Z",
                "tokens_estimate": 12345,
                "tokens_window_pct": 6,
                "message_count": 18,
                "first_prompt": "hello",
            }
        }
    }, open(path, "w"))
    yield path
    os.unlink(path)


async def test_tui_starts_and_renders_tree(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Tree must contain the project label.
        assert "demo" in str(app._tree.root.children[0].label)


async def test_tui_quit(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
    # Reaching here without timeout means quit worked.


async def test_enter_sets_resume_target(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Navigate from root → project → folder → leaf.
        # Tree shape: root (cursor starts here) → demo project → planning/ → sid-1 leaf
        await pilot.press("down")  # project node
        await pilot.press("down")  # folder node
        await pilot.press("down")  # session leaf
        await pilot.press("enter")
        await pilot.pause()
    assert getattr(app, "_resume_target", None) == "sid-1"


async def test_rename_updates_index(index_path, tmp_path):
    # Add a transcript path to the session so rename can write to it.
    import json
    data = json.load(open(index_path))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"user","uuid":"u1"}\n')
    data["sessions"]["sid-1"]["transcript_path"] = str(transcript)
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Navigate to the leaf and press r
        await pilot.press("down")  # project node
        await pilot.press("down")  # folder node
        await pilot.press("down")  # session leaf
        await pilot.press("r")
        await pilot.pause()
        # Type new name and submit
        for ch in "renamed":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()

    assert json.load(open(index_path))["sessions"]["sid-1"]["name_cached"] == "renamed"
    # Confirm the JSONL got the custom-title event appended.
    lines = transcript.read_text().splitlines()
    last = json.loads(lines[-1])
    assert last == {"type": "custom-title", "customTitle": "renamed", "sessionId": "sid-1"}


async def test_move_changes_folder(index_path, tmp_path):
    # Add a transcript path + a second session contributing an existing folder.
    import json
    data = json.load(open(index_path))
    transcript = tmp_path / "t2.jsonl"
    transcript.write_text('{"type":"user","uuid":"u1"}\n')
    data["sessions"]["sid-1"]["transcript_path"] = str(transcript)
    data["sessions"]["sid-2"] = {
        "project_label": "demo", "name_cached": "archive-old",
        "last_active_at": "2026-01-01T00:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp, MoveScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Navigate to sid-1 leaf. Tree layout (folders sorted alphabetically):
        #   root → demo (project) → archive/ → archive-old (sid-2)
        #                          → planning/ → planning-sprint14 (sid-1)
        # So 5 downs: demo, archive/, old, planning/, sprint14.
        for _ in range(5):
            await pilot.press("down")
        assert app._tree.cursor_node.data["sid"] == "sid-1"
        await pilot.press("m")
        await pilot.pause()
        # Modal should now be on top of the stack.
        assert isinstance(app.screen, MoveScreen)
        # Pilot navigation of OptionList is flaky; dismiss the screen directly
        # with a chosen folder name and let the callback run.
        app.screen.dismiss("release")
        await pilot.pause()

    name = json.load(open(index_path))["sessions"]["sid-1"]["name_cached"]
    # Display was 'sprint14' (from 'planning-sprint14'); new prefix is 'release'.
    assert name == "release-sprint14"
    # The JSONL got a custom-title event appended.
    lines = transcript.read_text().splitlines()
    last = json.loads(lines[-1])
    assert last == {"type": "custom-title", "customTitle": "release-sprint14", "sessionId": "sid-1"}


async def test_delete_removes_session(index_path, tmp_path):
    import json, os
    data = json.load(open(index_path))
    transcript = tmp_path / "td.jsonl"
    transcript.write_text('{"type":"user"}\n')
    data["sessions"]["sid-1"]["transcript_path"] = str(transcript)
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    from textual.screen import ModalScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # navigate to leaf
        await pilot.press("right"); await pilot.press("down")
        await pilot.press("down"); await pilot.press("down")
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        app.screen.dismiss(True)
        await pilot.pause()

    assert "sid-1" not in json.load(open(index_path))["sessions"]
    assert not os.path.exists(transcript)


async def test_notes_persists(index_path):
    from _pkg.tui import SessionExplorerApp
    from textual.screen import ModalScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # nav to leaf
        await pilot.press("right"); await pilot.press("down")
        await pilot.press("down"); await pilot.press("down")
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        # Bypass TextArea input quirks — dismiss directly with new value
        app.screen.dismiss("hello world")
        await pilot.pause()

    import json
    assert json.load(open(index_path))["sessions"]["sid-1"]["notes"] == "hello world"


async def test_preview_toggles(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._preview.display is False
        await pilot.press("space")
        await pilot.pause()
        assert app._preview.display is True
        await pilot.press("space")
        await pilot.pause()
        assert app._preview.display is False


def _collect_leaf_sids(node):
    sids = set()
    for child in node.children:
        if child.data and "sid" in child.data:
            sids.add(child.data["sid"])
        sids |= _collect_leaf_sids(child)
    return sids


async def test_filter_narrows_tree(index_path):
    import json
    data = json.load(open(index_path))
    data["sessions"]["sid-2"] = {
        "project_label": "demo", "name_cached": "release-x",
        "last_active_at": "2026-05-26T00:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Without filter, both sessions render.
        sids_before = _collect_leaf_sids(app._tree.root)
        assert "sid-1" in sids_before and "sid-2" in sids_before

        app._filter_needle = "planning"
        app._populate()
        sids_after = _collect_leaf_sids(app._tree.root)
        assert "sid-1" in sids_after
        assert "sid-2" not in sids_after


async def test_new_folder_adds_to_index(index_path):
    from _pkg.tui import SessionExplorerApp, NewFolderScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, NewFolderScreen)
        # Dismiss the modal directly with a chosen folder name; the callback
        # runs add_folder() and repopulates the tree.
        app.screen.dismiss("audits/empty-shelf")
        await pilot.pause()

    folders = json.load(open(index_path)).get("folders", [])
    assert "audits/empty-shelf" in folders
