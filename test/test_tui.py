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
