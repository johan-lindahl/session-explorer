import json
import os
import pytest


@pytest.fixture
def index_path(tmp_path):
    """Per-test index in an isolated directory.

    Critical: the folder store file is derived as a sibling of the index via
    folder_store.default_path_for(), so co-locating the index inside the
    pytest-provided tmp_path (which is unique per test) ensures the folder
    store is also test-isolated. Earlier versions used tempfile.mkstemp,
    which dropped the file into the shared system tmp dir — every test then
    pointed at the same sibling folder-store path and they polluted each
    other on any change to that file.
    """
    path = str(tmp_path / "se-index.json")
    json.dump({
        "version": 1, "folders": [],
        "sessions": {
            "sid-1": {
                "project_label": "demo",
                "project_path": "/tmp/demo-project",
                "name_cached": "planning/sprint14",
                "last_active_at": "2026-05-27T10:00:00Z",
                "tokens_estimate": 12345,
                "tokens_window_pct": 6,
                "message_count": 18,
                "first_prompt": "hello",
            }
        }
    }, open(path, "w"))
    # Mark help as already seen AND retention as decided so neither first-launch
    # modal (help / retention prompt) pops over the tree these tests drive. Both
    # have their own dedicated tests using marker-free index paths.
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    yield path


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
    # Resume must also capture the session's project_path so run() can chdir
    # there before exec'ing `claude --resume`.
    assert getattr(app, "_resume_cwd", None) == "/tmp/demo-project"


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
    """Moving a session to folder 'release' rewrites its custom-title to release/<display>."""
    import json
    data = json.load(open(index_path))
    transcript = tmp_path / "t2.jsonl"
    transcript.write_text('{"type":"user","uuid":"u1"}\n')
    data["sessions"]["sid-1"]["name_cached"] = "planning/sprint14"
    data["sessions"]["sid-1"]["transcript_path"] = str(transcript)
    data["sessions"]["sid-2"] = {
        "project_label": "demo", "project_path": "/tmp/demo",
        "name_cached": "archive/old",
        "last_active_at": "2026-01-01T00:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp, MoveScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Navigate to sid-1's leaf by searching.
        def find_leaf(node, sid):
            for c in node.children:
                if c.data and c.data.get("sid") == sid:
                    return c
                got = find_leaf(c, sid)
                if got:
                    return got
            return None
        leaf = find_leaf(app._tree.root, "sid-1")
        assert leaf is not None
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        app.screen.dismiss("release")
        await pilot.pause()

    name = json.load(open(index_path))["sessions"]["sid-1"]["name_cached"]
    assert name == "release/sprint14"
    last = json.loads(transcript.read_text().splitlines()[-1])
    assert last == {"type": "custom-title", "customTitle": "release/sprint14", "sessionId": "sid-1"}


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


def test_preview_text_headline_is_full_display_name():
    """The headline is the full (un-truncated) display segment — the part the
    grid truncates — and the folder path is surfaced separately."""
    from _pkg.tui import _preview_text
    long_name = "sprint14-a-very-long-name-that-the-grid-would-truncate"
    s = {
        "sid": "sid-1",
        "name_cached": f"planning/{long_name}",
        "project_label": "demo",
        "last_active_at": None,
        "tokens_estimate": 0,
        "tokens_window_pct": 0,
        "message_count": 3,
        "first_prompt": "hello",
        "transcript_path": "/p/t.jsonl",
    }
    text = _preview_text(s)
    assert long_name in text          # full display name present, not truncated
    assert "planning" in text          # folder path surfaced


def test_preview_text_includes_relevant_fields():
    from _pkg.tui import _preview_text
    s = {
        "sid": "abc12345-def-6789",
        "name_cached": "auth/login",
        "project_label": "myproj",
        "branch": "feat/login",
        "last_active_at": None,
        "created_at": "2026-05-20T10:00:00Z",
        "tokens_estimate": 18000,
        "tokens_window_pct": 9,
        "message_count": 42,
        "first_prompt": "do the thing",
        "notes": "remember this",
        "transcript_path": "/x/y/abc.jsonl",
    }
    text = _preview_text(s)
    for needle in (
        "login",            # headline (display segment)
        "auth",             # folder
        "myproj",           # project
        "feat/login",       # branch
        "2026-05-20",       # created date
        "42",               # message count
        "abc12345-def-6789",  # full session id
        "remember this",    # notes
        "do the thing",     # first prompt
        "/x/y/abc.jsonl",   # transcript path
    ):
        assert needle in text, needle
    assert "18K" in text and "9%" in text   # context size + window pct
    assert "Summary" not in text             # summary block dropped


async def test_esc_closes_preview_then_does_not_quit(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")          # open preview
        await pilot.pause()
        assert app._preview.display is True
        await pilot.press("escape")         # closes the preview …
        await pilot.pause()
        assert app._preview.display is False
        assert app.is_running               # … without quitting
        await pilot.press("escape")         # preview already closed → no-op
        await pilot.pause()
        assert app.is_running               # still must not quit


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


async def test_move_ungroup_unnamed_session_uses_sid_prefix(index_path, tmp_path):
    """Regression: move-to-(ungroup) of an unnamed session must write a non-empty
    customTitle (sid[:8]) and must not contain /."""
    import json
    data = json.load(open(index_path))
    transcript = tmp_path / "tu.jsonl"
    transcript.write_text('{"type":"user"}\n')
    data["sessions"]["unnamed-sid-xyz"] = {
        "project_label": "demo", "project_path": "/tmp/demo",
        "name_cached": None,
        "last_active_at": "2026-05-25T00:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
        "transcript_path": str(transcript),
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    from textual.screen import ModalScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u"); await pilot.pause()  # surface unnamed
        def find(node, sid):
            for c in node.children:
                if c.data and c.data.get("sid") == sid:
                    return c
                got = find(c, sid)
                if got: return got
            return None
        leaf = find(app._tree.root, "unnamed-sid-xyz")
        assert leaf is not None
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        app.screen.dismiss("")  # (ungroup)
        await pilot.pause()

    name = json.load(open(index_path))["sessions"]["unnamed-sid-xyz"]["name_cached"]
    assert name == "unnamed-"
    assert "/" not in name
    last = json.loads(transcript.read_text().splitlines()[-1])
    assert last["customTitle"] == "unnamed-"


def test_row_label_columns_align_across_depth():
    """A leaf one level shallower (depth=1, ungrouped) and a leaf one level
    deeper (depth=2, folder-grouped) must place stat columns at the same
    absolute screen column. In the bare row string this means the stat suffix
    sits at `name_w` in each, and that `name_w` differs by GUIDE_DEPTH, which
    exactly equals one tree-indent level."""
    from _pkg.tui import _row_label, _stat_suffix, NAME_W, GUIDE_DEPTH, GLYPH_W
    s = {"name_cached": "x", "last_active_at": None,
         "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 7,
         "first_prompt": "hello"}
    ungrouped = _row_label("sid", s, depth=1)
    grouped = _row_label("sid", s, depth=2)
    # Every row carries a GLYPH_W-wide live-state prefix before the name field.
    # At depth=2: name_w = NAME_W. At depth=1: name_w = NAME_W + GUIDE_DEPTH.
    name_w_grouped = GLYPH_W + NAME_W
    name_w_ungrouped = GLYPH_W + NAME_W + GUIDE_DEPTH
    assert grouped[name_w_grouped:] == ungrouped[name_w_ungrouped:]
    assert grouped[name_w_grouped:] == _stat_suffix("—", "~0", "(0%)", "7", "msgs", "hello")


def test_column_header_offset_matches_grouped_leaf():
    from _pkg.tui import _column_header, _stat_suffix, NAME_W, GUIDE_DEPTH, GLYPH_W
    header = _column_header()
    name_region = GLYPH_W + NAME_W + 2 * GUIDE_DEPTH
    # Left region is the GLYPH_W glyph cells + NAME label; the stat labels begin
    # at the same absolute column a grouped leaf's stats do (prefix GLYPH_W +
    # 2*GUIDE_DEPTH + NAME_W).
    assert header[:name_region].strip() == "NAME"
    assert header[name_region:] == _stat_suffix("AGE", "~TOK", "CTX", "MSGS", "    ", "FIRST PROMPT")


def test_long_name_truncates_to_field_width():
    from _pkg.tui import _row_label, NAME_W, GLYPH_W
    s = {"name_cached": "a" * 100, "last_active_at": None,
         "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
         "first_prompt": ""}
    # depth=2 → name_w == NAME_W, preceded by the GLYPH_W live-state prefix.
    row = _row_label("sid", s, depth=2)
    assert row[GLYPH_W:GLYPH_W + NAME_W].endswith("…")
    assert row[GLYPH_W + NAME_W] == " "  # stat suffix's leading space sits exactly after the name field


async def test_column_header_rendered(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._tree.show_root is False
        header_text = str(app._colheader.render())
        for col in ("NAME", "AGE", "~TOK", "CTX", "MSGS", "FIRST PROMPT"):
            assert col in header_text


async def test_unnamed_hidden_by_default_toggle_with_u(index_path):
    import json
    data = json.load(open(index_path))
    data["sessions"]["unnamed-xyz"] = {
        "project_label": "demo", "name_cached": None,
        "last_active_at": "2026-05-25T00:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        sids = _collect_leaf_sids(app._tree.root)
        assert "sid-1" in sids
        assert "unnamed-xyz" not in sids
        # Subtitle should advertise the hidden count.
        assert "unnamed hidden" in app.sub_title

        await pilot.press("u")
        await pilot.pause()
        sids = _collect_leaf_sids(app._tree.root)
        assert "unnamed-xyz" in sids

        await pilot.press("u")
        await pilot.pause()
        sids = _collect_leaf_sids(app._tree.root)
        assert "unnamed-xyz" not in sids


async def test_new_folder_under_project_adds_to_folder_store(index_path):
    """`n` on a project node creates a top-level folder in that project."""
    from _pkg.tui import SessionExplorerApp, NewFolderScreen
    from _pkg import folder_store
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # cursor sits on the demo project (first child of the hidden root).
        proj = app._tree.root.children[0]
        app._tree.select_node(proj); app._tree.cursor_line = proj.line
        await pilot.pause()
        await pilot.press("n"); await pilot.pause()
        assert isinstance(app.screen, NewFolderScreen)
        # Cursor on a project node → modal opens with an empty prefix so the
        # user types a top-level folder name unprefixed.
        assert app.screen._prefix == ""
        app.screen.dismiss("audits/q1")
        await pilot.pause()

    fs_path = folder_store.default_path_for(index_path)
    assert "audits/q1" in folder_store.list_paths(fs_path, "demo")


async def test_new_folder_under_folder_creates_child(index_path):
    """`n` on a folder node creates a child path under it."""
    from _pkg.tui import SessionExplorerApp, NewFolderScreen
    from _pkg import folder_store
    import json
    data = json.load(open(index_path))
    data["sessions"]["sid-1"]["name_cached"] = "planning/sprint14"
    json.dump(data, open(index_path, "w"))

    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Find the planning/ folder node.
        def find(node, label_contains):
            for c in node.children:
                if label_contains in str(c.label): return c
                got = find(c, label_contains)
                if got: return got
            return None
        planning = find(app._tree.root, "planning/")
        assert planning is not None
        app._tree.select_node(planning); app._tree.cursor_line = planning.line
        await pilot.pause()
        await pilot.press("n"); await pilot.pause()
        assert isinstance(app.screen, NewFolderScreen)
        # The modal must prefill with "planning/" — the engineer types "retro".
        screen = app.screen
        assert screen._prefix == "planning/"
        screen.dismiss("planning/retro")
        await pilot.pause()

    fs_path = folder_store.default_path_for(index_path)
    paths = folder_store.list_paths(fs_path, "demo")
    assert "planning/retro" in paths


async def test_populate_renders_nested_folders(index_path, tmp_path):
    """A session named foo/bar should render as project → foo/ → bar leaf."""
    import json
    data = json.load(open(index_path))
    data["sessions"]["sid-nested"] = {
        "project_label": "demo",
        "project_path": "/tmp/demo",
        "name_cached": "planning/sprint99",
        "last_active_at": "2026-05-27T11:00:00Z",
        "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
    }
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Find the planning/ folder node and check its child carries sid-nested.
        def find(node, label_contains):
            for c in node.children:
                if label_contains in str(c.label):
                    return c
                got = find(c, label_contains)
                if got:
                    return got
            return None
        planning = find(app._tree.root, "planning/")
        assert planning is not None
        leaf = next((c for c in planning.children if c.data and c.data.get("sid") == "sid-nested"), None)
        assert leaf is not None


async def test_move_to_new_path_adds_to_folder_store(index_path, tmp_path):
    """Typing a new path in MoveScreen auto-creates it in the folder store."""
    import json
    from _pkg import folder_store
    data = json.load(open(index_path))
    transcript = tmp_path / "tn.jsonl"
    transcript.write_text('{"type":"user"}\n')
    data["sessions"]["sid-1"]["transcript_path"] = str(transcript)
    data["sessions"]["sid-1"]["name_cached"] = "sprint14"
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp, MoveScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # cursor onto sid-1
        def find(node, sid):
            for c in node.children:
                if c.data and c.data.get("sid") == sid: return c
                got = find(c, sid)
                if got: return got
            return None
        leaf = find(app._tree.root, "sid-1")
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        app.screen.dismiss("team/new-folder")  # auto-creates this path
        await pilot.pause()

    fs_path = folder_store.default_path_for(index_path)
    paths = folder_store.list_paths(fs_path, "demo")
    assert "team/new-folder" in paths
    assert json.load(open(index_path))["sessions"]["sid-1"]["name_cached"] == "team/new-folder/sprint14"


def _find_node_by_segments(root, project, segments):
    """Locate the folder node whose attached data matches (project, segments)."""
    def walk(node):
        d = node.data or {}
        if (d.get("project") == project and d.get("segments") == segments
                and "sid" not in d):
            return node
        for c in node.children:
            got = walk(c)
            if got:
                return got
        return None
    return walk(root)


def _folder_index(tmp_path):
    """Index + transcripts modelling a populated folder subtree:

        team/planning/sprint14          (sid-a)
        team/planning/q1/notes          (sid-b, deeper)
        team/planning-extra/keep        (sid-c, sibling sharing a string prefix)
        other/elsewhere                 (sid-d, unrelated)

    Plus a store-only empty subfolder team/planning/archive.
    """
    import json
    from _pkg import folder_store
    path = str(tmp_path / "se-index.json")
    sessions = {}
    for sid, name in [
        ("sid-a", "team/planning/sprint14"),
        ("sid-b", "team/planning/q1/notes"),
        ("sid-c", "team/planning-extra/keep"),
        ("sid-d", "other/elsewhere"),
    ]:
        tr = tmp_path / f"{sid}.jsonl"
        tr.write_text('{"type":"user"}\n')
        sessions[sid] = {
            "project_label": "demo", "project_path": "/tmp/demo",
            "name_cached": name, "last_active_at": "2026-05-27T10:00:00Z",
            "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0,
            "transcript_path": str(tr),
        }
    json.dump({"version": 1, "sessions": sessions}, open(path, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    folder_store.add(folder_store.default_path_for(path), "demo", "team/planning/archive")
    return path


async def test_rename_folder_cascades_to_sessions_and_store(tmp_path):
    """`r` on a folder renames its last segment in place and rewrites every
    contained session (and store subtree); prefix-only siblings stay put."""
    import json
    from _pkg.tui import SessionExplorerApp, RenameScreen
    from _pkg import folder_store
    index_path = _folder_index(tmp_path)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        node = _find_node_by_segments(app._tree.root, "demo", ["team", "planning"])
        assert node is not None
        app._tree.select_node(node); app._tree.cursor_line = node.line
        await pilot.pause()
        await pilot.press("r"); await pilot.pause()
        assert isinstance(app.screen, RenameScreen)
        # Prefilled with the leaf only (rename-in-place).
        assert app.screen._current == "planning"
        app.screen.dismiss("strategy")
        await pilot.pause()
        # A confirmation naming the affected count gates the cascade.
        await pilot.press("y"); await pilot.pause()

    sessions = json.load(open(index_path))["sessions"]
    assert sessions["sid-a"]["name_cached"] == "team/strategy/sprint14"
    assert sessions["sid-b"]["name_cached"] == "team/strategy/q1/notes"
    assert sessions["sid-c"]["name_cached"] == "team/planning-extra/keep"  # untouched
    assert sessions["sid-d"]["name_cached"] == "other/elsewhere"           # untouched
    fs_path = folder_store.default_path_for(index_path)
    paths = folder_store.list_paths(fs_path, "demo")
    assert "team/strategy/archive" in paths
    assert "team/planning/archive" not in paths


async def test_move_folder_reparents_subtree(tmp_path):
    """`m` on a folder keeps its leaf and re-parents it under the chosen path."""
    import json
    from _pkg.tui import SessionExplorerApp, MoveScreen
    index_path = _folder_index(tmp_path)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        node = _find_node_by_segments(app._tree.root, "demo", ["team", "planning"])
        app._tree.select_node(node); app._tree.cursor_line = node.line
        await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        app.screen.dismiss("archive")  # new parent
        await pilot.pause()
        await pilot.press("y"); await pilot.pause()

    sessions = json.load(open(index_path))["sessions"]
    assert sessions["sid-a"]["name_cached"] == "archive/planning/sprint14"
    assert sessions["sid-b"]["name_cached"] == "archive/planning/q1/notes"
    assert sessions["sid-c"]["name_cached"] == "team/planning-extra/keep"


async def test_move_folder_into_own_descendant_is_rejected(tmp_path):
    """Re-parenting a folder beneath itself would be nonsensical; reject it."""
    import json
    from _pkg.tui import SessionExplorerApp, MoveScreen
    index_path = _folder_index(tmp_path)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        node = _find_node_by_segments(app._tree.root, "demo", ["team", "planning"])
        app._tree.select_node(node); app._tree.cursor_line = node.line
        await pilot.pause()
        await pilot.press("m"); await pilot.pause()
        assert isinstance(app.screen, MoveScreen)
        app.screen.dismiss("team/planning/q1")  # a descendant — illegal target
        await pilot.pause()
        # No confirmation should appear and nothing should change.
        assert not isinstance(app.screen, MoveScreen)

    sessions = json.load(open(index_path))["sessions"]
    assert sessions["sid-a"]["name_cached"] == "team/planning/sprint14"


async def test_rename_project_node_is_rejected(tmp_path):
    """`r` on a project node (no folder segments) must not start a folder rename."""
    from _pkg.tui import SessionExplorerApp, RenameScreen
    index_path = _folder_index(tmp_path)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        proj = app._tree.root.children[0]
        app._tree.select_node(proj); app._tree.cursor_line = proj.line
        await pilot.pause()
        await pilot.press("r"); await pilot.pause()
        assert not isinstance(app.screen, RenameScreen)


def test_help_text_explains_naming_visibility_and_credit():
    from _pkg.tui import _help_text
    text = _help_text()
    # Slash-folder naming explanation, with a concrete example.
    assert "/" in text
    assert "team/planning/sprint14" in text
    # Named-only default + `u` to toggle unnamed.
    assert "u" in text
    lowered = text.lower()
    assert "unnamed" in lowered
    assert "rename" in lowered or "named" in lowered
    # Credit line.
    assert "Johan Lindahl" in text
    assert "johan.lindahl@snojken.com" in text
    # Repo link in credits (visible URL, also an OSC-8 hyperlink where supported).
    assert "github.com/johan-lindahl/session-explorer" in text
    # Version is shown so users can see which build they're running.
    from _pkg import __version__
    assert __version__ in text


def test_empty_state_text_none_when_rows_visible():
    from _pkg.tui import _empty_state_text
    assert _empty_state_text(total_indexed=3, visible=3, unnamed_hidden=0,
                             filter_active=False, scanned=False) is None


def test_empty_state_text_prompts_rescan_on_empty_index():
    from _pkg.tui import _empty_state_text
    msg = _empty_state_text(total_indexed=0, visible=0, unnamed_hidden=0,
                            filter_active=False, scanned=False)
    assert "Press F5" in msg
    assert "~/.claude/projects" in msg


def test_empty_state_text_after_scan_found_nothing():
    from _pkg.tui import _empty_state_text
    msg = _empty_state_text(total_indexed=0, visible=0, unnamed_hidden=0,
                            filter_active=False, scanned=True)
    assert "No sessions found" in msg


def test_empty_state_text_prompts_u_when_all_unnamed_hidden():
    from _pkg.tui import _empty_state_text
    msg = _empty_state_text(total_indexed=5, visible=0, unnamed_hidden=5,
                            filter_active=False, scanned=False)
    assert "Press u" in msg
    assert "5" in msg


def test_empty_state_text_filter_no_match_takes_precedence():
    from _pkg.tui import _empty_state_text
    # Filter active wins even if unnamed sessions are also hidden.
    msg = _empty_state_text(total_indexed=5, visible=0, unnamed_hidden=2,
                            filter_active=True, scanned=False)
    assert "filter" in msg.lower()
    assert "Esc" in msg


async def test_empty_state_shown_when_only_unnamed(tmp_path):
    """An index with only unnamed sessions shows the 'press u' empty-state; u hides it."""
    import json
    fresh = tmp_path / "only-unnamed"
    fresh.mkdir()
    idx = str(fresh / "se-index.json")
    json.dump({"version": 2, "sessions": {
        "u1": {"project_label": "demo", "name_cached": None,
               "last_active_at": "2026-05-25T00:00:00Z",
               "tokens_estimate": 0, "tokens_window_pct": 0, "message_count": 0},
    }}, open(idx, "w"))
    (fresh / ".session-explorer.help-seen").write_text("")
    (fresh / ".session-explorer.retention-declined").write_text("")

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._empty.display is True
        assert "Press u" in str(app._empty.render())
        await pilot.press("u")
        await pilot.pause()
        assert app._empty.display is False   # unnamed now visible


async def test_rescan_imports_sessions_from_projects_root(tmp_path):
    """Empty index → empty-state prompts R → pressing R imports transcripts."""
    import json, shutil, os
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    idx = str(fresh / "se-index.json")
    json.dump({"version": 2, "sessions": {}}, open(idx, "w"))
    (fresh / ".session-explorer.help-seen").write_text("")
    (fresh / ".session-explorer.retention-declined").write_text("")

    projects = tmp_path / "projects"
    proj = projects / "-Users-jl-proj-foo"
    proj.mkdir(parents=True)
    FIX = os.path.join(os.path.dirname(__file__), "fixtures")
    shutil.copy(os.path.join(FIX, "named.jsonl"), proj / "AAA.jsonl")

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=idx, projects_root=str(projects))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._empty.display is True            # empty index → prompt
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app._empty.display is False           # imported (named) session visible
        from _pkg.tui import RescanScreen
        assert not isinstance(app.screen, RescanScreen)  # progress modal dismissed
        assert app._rescan_screen is None

    assert "AAA" in json.load(open(idx))["sessions"]


async def test_rescan_progress_updates_modal(index_path):
    """The rescan modal shows the bar with the right total/progress and an X/N
    label; _on_progress feeds the pushed RescanScreen."""
    from _pkg.tui import SessionExplorerApp, RescanScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = RescanScreen()
        app._rescan_screen = screen
        app.push_screen(screen)
        await pilot.pause()
        app._on_progress(2, 5)
        await pilot.pause()
        assert isinstance(app.screen, RescanScreen)
        assert screen._progress.total == 5
        assert screen._progress.progress == 2
        assert "2/5" in str(screen._status.render())


async def test_h_opens_help_and_esc_dismisses(index_path):
    from _pkg.tui import SessionExplorerApp, HelpScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
        await pilot.press("h")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
        assert app.is_running   # dismissing help must not quit


async def test_help_quit_key_dismisses_not_quits(index_path):
    from _pkg.tui import SessionExplorerApp, HelpScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("q")     # dismisses the help overlay …
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
        assert app.is_running       # … rather than quitting the app


async def test_help_auto_opens_on_first_launch_and_writes_marker(tmp_path):
    import json
    from _pkg.tui import SessionExplorerApp, HelpScreen
    # A fresh index with NO help-seen marker beside it. Retention is pre-decided
    # so only the help modal is exercised here (retention prompt has its own test).
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    idx = str(fresh / "se-index.json")
    json.dump({"version": 2, "sessions": {}}, open(idx, "w"))
    (fresh / ".session-explorer.retention-declined").write_text("")
    marker = fresh / ".session-explorer.help-seen"
    assert not marker.exists()

    app = SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)   # auto-opened
    assert marker.exists()                           # marker now recorded

    # Second launch: marker present → help must NOT auto-open.
    app2 = SessionExplorerApp(index_path=idx)
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert not isinstance(app2.screen, HelpScreen)


# --- opt-in retention prompt on first launch ---

def _fresh_index(tmp_path, settings=None):
    import json
    d = tmp_path / "fresh"
    d.mkdir()
    idx = str(d / "se-index.json")
    json.dump({"version": 2, "sessions": {}}, open(idx, "w"))
    (d / ".session-explorer.help-seen").write_text("")  # isolate retention modal
    if settings is not None:
        (d / "settings.json").write_text(json.dumps(settings))
    return d, idx


async def test_retention_prompt_enable_on_first_launch(tmp_path):
    import json
    from _pkg.tui import SessionExplorerApp
    from textual.screen import ModalScreen
    d, idx = _fresh_index(tmp_path, settings={"cleanupPeriodDays": 10})
    app = SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)   # retention prompt
        app.screen.dismiss(True); await pilot.pause()
    assert (d / ".session-explorer.backup").read_text().strip() == "10"
    assert json.loads((d / "settings.json").read_text())["cleanupPeriodDays"] == 36500


async def test_retention_prompt_decline_leaves_settings(tmp_path):
    import json
    from _pkg.tui import SessionExplorerApp
    from textual.screen import ModalScreen
    d, idx = _fresh_index(tmp_path, settings={"cleanupPeriodDays": 10})
    app = SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)
        app.screen.dismiss(False); await pilot.pause()
    assert (d / ".session-explorer.retention-declined").exists()
    assert not (d / ".session-explorer.backup").exists()
    assert json.loads((d / "settings.json").read_text())["cleanupPeriodDays"] == 10  # untouched


async def test_retention_not_prompted_once_decided(tmp_path):
    from _pkg.tui import SessionExplorerApp
    from textual.screen import ModalScreen
    d, idx = _fresh_index(tmp_path)
    (d / ".session-explorer.retention-declined").write_text("")
    app = SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, ModalScreen)  # no prompt


# --- resume argv: bind the id to --resume via `=`. `--resume` takes an OPTIONAL
#     value ([value] in claude --help), so `--resume <id>`'s value can be lost
#     and `--resume -- <id>` opens the interactive picker. `--resume=<id>` binds
#     the id as the option's value AND is injection-safe (a leading-'-' id stays
#     part of the single token, never parsed as a separate flag). ---

def test_resume_argv_binds_id_as_resume_value():
    from _pkg.tui import _resume_argv
    assert _resume_argv("01ABC") == ["claude", "--resume=01ABC"]


def test_resume_argv_dash_leading_id_stays_a_value():
    from _pkg.tui import _resume_argv
    # "-foo" must stay bound to --resume, not become a separate flag.
    assert _resume_argv("-foo") == ["claude", "--resume=-foo"]


def test_preview_text_shows_model():
    from _pkg.tui import _preview_text
    s = {"sid": "x", "name_cached": "n", "tokens_estimate": 620000,
         "tokens_window_pct": 62, "model": "claude-opus-4-8"}
    text = _preview_text(s)
    assert "claude-opus-4-8" in text
    assert "Model" in text


def test_preview_text_model_unknown_when_absent():
    from _pkg.tui import _preview_text
    s = {"sid": "x", "name_cached": "n"}
    text = _preview_text(s)
    assert "(unknown)" in text


# --- delete empty folders (d on a folder node) ---

def test_folder_has_sessions_detects_session_under_folder():
    from _pkg.tui import _folder_has_sessions
    data = {"sessions": {"s1": {"project_label": "demo", "name_cached": "planning/sprint14"}}}
    assert _folder_has_sessions(data, "demo", ["planning"]) is True
    assert _folder_has_sessions(data, "demo", ["audits"]) is False


def test_folder_has_sessions_ignores_other_projects_and_unnamed():
    from _pkg.tui import _folder_has_sessions
    data = {"sessions": {
        "s1": {"project_label": "other", "name_cached": "planning/x"},
        "s2": {"project_label": "demo", "name_cached": None},
    }}
    assert _folder_has_sessions(data, "demo", ["planning"]) is False


async def test_delete_empty_folder_removes_it(index_path):
    from _pkg.tui import SessionExplorerApp
    from _pkg import folder_store
    from textual.screen import ModalScreen
    fs_path = folder_store.default_path_for(index_path)
    folder_store.add(fs_path, "demo", "scratch")  # empty folder, no sessions under it

    def find(node, lbl):
        for c in node.children:
            if lbl in str(c.label):
                return c
            g = find(c, lbl)
            if g:
                return g
        return None

    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        scratch = find(app._tree.root, "scratch/")
        assert scratch is not None
        app._tree.select_node(scratch); app._tree.cursor_line = scratch.line
        await pilot.pause()
        await pilot.press("d"); await pilot.pause()
        assert isinstance(app.screen, ModalScreen)  # confirm dialog opened
        app.screen.dismiss(True)
        await pilot.pause()

    assert "scratch" not in folder_store.list_paths(fs_path, "demo")


async def test_delete_nonempty_folder_refuses(index_path):
    from _pkg.tui import SessionExplorerApp
    from textual.screen import ModalScreen
    import json

    def find(node, lbl):
        for c in node.children:
            if lbl in str(c.label):
                return c
            g = find(c, lbl)
            if g:
                return g
        return None

    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # The fixture's sid-1 is named "planning/sprint14", so "planning/" has a session.
        planning = find(app._tree.root, "planning/")
        assert planning is not None
        app._tree.select_node(planning); app._tree.cursor_line = planning.line
        await pilot.pause()
        await pilot.press("d"); await pilot.pause()
        # Refused: no confirm dialog, session untouched.
        assert not isinstance(app.screen, ModalScreen)

    assert "sid-1" in json.load(open(index_path))["sessions"]


async def test_left_right_collapse_and_expand_folder(index_path):
    """left collapses / right expands the folder under the cursor.

    Regression guard: the app overrides enter and space (the Tree's own toggle
    keys), and this Textual Tree has no left/right binding, so without explicit
    app bindings keyboard expand/collapse does nothing (mouse-only).
    """
    from _pkg.tui import SessionExplorerApp

    def find(node, lbl):
        for c in node.children:
            if lbl in str(c.label):
                return c
            g = find(c, lbl)
            if g:
                return g
        return None

    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        fol = find(app._tree.root, "planning/")
        assert fol is not None and fol.is_expanded  # folders render expanded
        app._tree.select_node(fol); app._tree.cursor_line = fol.line
        await pilot.pause()
        await pilot.press("left"); await pilot.pause()
        assert find(app._tree.root, "planning/").is_expanded is False
        await pilot.press("right"); await pilot.pause()
        assert find(app._tree.root, "planning/").is_expanded is True


def test_preview_text_shows_full_project_path():
    """Several projects can share a basename (e.g. magento2) under different
    roots, so the preview shows the full project_path to disambiguate."""
    from _pkg.tui import _preview_text
    s = {"sid": "x", "name_cached": "magento2",
         "project_label": "magento2",
         "project_path": "/Users/jl/clients/acme/magento2"}
    text = _preview_text(s)
    assert "/Users/jl/clients/acme/magento2" in text


# --- resume cwd resolution: fall back to the parent repo when a worktree path
#     no longer exists (deleted git worktree). ---

def test_resolve_resume_cwd_returns_existing_path(tmp_path):
    from _pkg.tui import _resolve_resume_cwd
    assert _resolve_resume_cwd(str(tmp_path)) == str(tmp_path)


def test_resolve_resume_cwd_recreates_dead_worktree(tmp_path):
    import os as _os
    from _pkg.tui import _resolve_resume_cwd
    repo = tmp_path / "magento-os"
    repo.mkdir()
    dead_wt = str(repo / ".claude" / "worktrees" / "brainstorm-x")  # never created
    # claude --resume is cwd-scoped, so to find the worktree-filed transcript we
    # recreate the (empty) worktree dir and resume there.
    assert _resolve_resume_cwd(dead_wt) == dead_wt
    assert _os.path.isdir(dead_wt)


def test_dead_worktree_repo_detection(tmp_path):
    from _pkg.tui import _dead_worktree_repo
    repo = tmp_path / "repo"
    repo.mkdir()
    dead = str(repo / ".claude" / "worktrees" / "wt")
    assert _dead_worktree_repo(dead) == str(repo)          # recreatable
    assert _dead_worktree_repo(str(repo)) is None          # path exists
    assert _dead_worktree_repo(str(tmp_path / "gone" / ".claude" / "worktrees" / "wt")) is None  # repo gone
    assert _dead_worktree_repo(str(tmp_path / "nope")) is None  # no worktree marker
    assert _dead_worktree_repo(None) is None


def test_resolve_resume_cwd_dead_worktree_and_missing_repo_is_none(tmp_path):
    from _pkg.tui import _resolve_resume_cwd
    dead = str(tmp_path / "gone" / ".claude" / "worktrees" / "wt")
    assert _resolve_resume_cwd(dead) is None


def test_resolve_resume_cwd_missing_nonworktree_is_none(tmp_path):
    from _pkg.tui import _resolve_resume_cwd
    assert _resolve_resume_cwd(str(tmp_path / "nope")) is None


def test_resolve_resume_cwd_none_or_empty():
    from _pkg.tui import _resolve_resume_cwd
    assert _resolve_resume_cwd(None) is None
    assert _resolve_resume_cwd("") is None


def _wt_index(tmp_path, dead_wt):
    import json
    idx = str(tmp_path / "i.json")
    json.dump({"version": 2, "sessions": {"wt1": {
        "project_label": "repo", "project_path": dead_wt, "name_cached": "wt-session",
        "last_active_at": "2026-05-27T10:00:00Z", "tokens_estimate": 1,
        "tokens_window_pct": 0, "message_count": 1, "first_prompt": "x"}}}, open(idx, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    return idx


def _find(node, lbl):
    for c in node.children:
        if lbl in str(c.label):
            return c
        g = _find(c, lbl)
        if g:
            return g
    return None


async def test_resume_dead_worktree_asks_to_confirm(tmp_path):
    from _pkg.tui import SessionExplorerApp
    from textual.screen import ModalScreen
    repo = tmp_path / "repo"; repo.mkdir()
    dead_wt = str(repo / ".claude" / "worktrees" / "feat-x")
    app = SessionExplorerApp(index_path=_wt_index(tmp_path, dead_wt))
    async with app.run_test() as pilot:
        await pilot.pause()
        leaf = _find(app._tree.root, "wt-session")
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("enter"); await pilot.pause()
        # Confirmation appears; resume target NOT set yet.
        assert isinstance(app.screen, ModalScreen)
        assert getattr(app, "_resume_target", None) is None
        app.screen.dismiss(True); await pilot.pause()
    assert app._resume_target == "wt1"


async def test_resume_dead_worktree_cancel_does_not_resume(tmp_path):
    from _pkg.tui import SessionExplorerApp
    repo = tmp_path / "repo"; repo.mkdir()
    dead_wt = str(repo / ".claude" / "worktrees" / "feat-x")
    app = SessionExplorerApp(index_path=_wt_index(tmp_path, dead_wt))
    async with app.run_test() as pilot:
        await pilot.pause()
        leaf = _find(app._tree.root, "wt-session")
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("enter"); await pilot.pause()
        app.screen.dismiss(False); await pilot.pause()
        assert getattr(app, "_resume_target", None) is None  # cancelled


# --- _PanelScreen dialog restyle (centered rounded panel on dimmed backdrop) ---

from _pkg import tui as _tui


def _make_app_with_one_named_session(tmp_path):
    import json
    from _pkg.tui import SessionExplorerApp
    idx = tmp_path / "session-explorer-index.json"
    idx.write_text(json.dumps({"version": 2, "sessions": {
        "s1": {"project_label": "demo", "project_path": "/p", "name_cached": "alpha",
               "last_active_at": "2026-05-29T10:00:00+00:00", "tokens_estimate": 1,
               "tokens_window_pct": 0, "message_count": 1, "first_prompt": "hi",
               "transcript_path": "/p/s1.jsonl"}}}))
    (tmp_path / ".session-explorer.help-seen").touch()
    (tmp_path / ".session-explorer.retention-declined").touch()
    return SessionExplorerApp(index_path=str(idx))


async def test_rename_dialog_returns_value_on_enter(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = {}
        app.push_screen(_tui.RenameScreen("old"), lambda v: result.__setitem__("v", v))
        await pilot.pause()
        app.screen.query_one("#rename-input", _tui.Input).value = "team/new"
        await pilot.press("enter")
        await pilot.pause()
        assert result["v"] == "team/new"


async def test_rename_dialog_cancels_on_escape(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = {}
        app.push_screen(_tui.RenameScreen("old"), lambda v: result.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert result["v"] == ""


@pytest.mark.asyncio
async def test_new_folder_dialog_returns_path_on_enter(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = {}
        app.push_screen(_tui.NewFolderScreen("demo", "team/"), lambda v: result.__setitem__("v", v))
        await pilot.pause()
        app.screen.query_one("#newfolder-input", _tui.Input).value = "team/sprint"
        await pilot.press("enter")
        await pilot.pause()
        assert result["v"] == "team/sprint"


@pytest.mark.asyncio
async def test_new_folder_dialog_cancels_on_escape(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = {}
        app.push_screen(_tui.NewFolderScreen("demo", "team/"), lambda v: result.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert result["v"] == ""


def test_panelscreen_css_defines_centered_dimmed_panel():
    css = _tui._PanelScreen.DEFAULT_CSS
    assert "align: center middle" in css
    assert "#panel" in css
    assert "border: round $accent" in css
    assert "background: $surface" in css
    assert "%" in css  # translucent backdrop


@pytest.mark.asyncio
async def test_confirm_dialog_yes_no_escape(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.ConfirmScreen("Delete?"), lambda v: got.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert got["v"] is True
        app.push_screen(_tui.ConfirmScreen("Delete?"), lambda v: got.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert got["v"] is False


@pytest.mark.asyncio
async def test_notes_dialog_saves_on_ctrl_s(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.NotesScreen("orig"), lambda v: got.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert got["v"] == "orig"


@pytest.mark.asyncio
async def test_move_dialog_typed_path_on_enter(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.MoveScreen("demo", ["team/planning"], ""),
                        lambda v: got.__setitem__("v", v))
        await pilot.pause()
        inp = app.screen.query_one("#move-input", _tui.Input)
        inp.focus()
        await pilot.pause()
        inp.value = "team/new"
        await pilot.press("enter")
        await pilot.pause()
        assert got["v"] == "team/new"


def test_restyled_dialogs_use_panel_base():
    for cls in (_tui.MoveScreen, _tui.ConfirmScreen, _tui.NotesScreen,
                _tui.RenameScreen, _tui.NewFolderScreen):
        assert issubclass(cls, _tui._PanelScreen), cls.__name__


@pytest.mark.asyncio
async def test_move_dialog_select_ungroup_returns_empty(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.MoveScreen("demo", ["team/planning"], "team/planning"),
                        lambda v: got.__setitem__("v", v))
        await pilot.pause()
        ol = app.screen.query_one("#move-list", _tui.OptionList)
        ol.focus()
        ol.highlighted = 0  # the "(ungroup)" option is first
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert got["v"] == ""  # __none__ maps to "" (ungroup)


@pytest.mark.asyncio
async def test_move_dialog_select_existing_path_returns_it(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.MoveScreen("demo", ["team/planning"], ""),
                        lambda v: got.__setitem__("v", v))
        await pilot.pause()
        ol = app.screen.query_one("#move-list", _tui.OptionList)
        ol.focus()
        ol.highlighted = 1  # first existing path after "(ungroup)"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert got["v"] == "team/planning"


@pytest.mark.asyncio
async def test_confirm_dialog_n_returns_false(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.ConfirmScreen("Delete?"), lambda v: got.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert got["v"] is False


@pytest.mark.asyncio
async def test_notes_dialog_cancel_returns_none(tmp_path):
    app = _make_app_with_one_named_session(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        got = {}
        app.push_screen(_tui.NotesScreen("orig"), lambda v: got.__setitem__("v", v))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert got["v"] is None


def test_help_text_documents_live_sessions():
    txt = _tui._help_text()
    assert "Live sessions" in txt
    assert "spinner" in txt.lower()
    assert "○" in txt
    assert "active" in txt.lower()


async def test_enter_starts_and_switches_when_stopped(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: [])   # nothing running
    monkeypatch.setattr(tuimod._tmux, "start_window",
                        lambda sid, cwd, label=None: calls.setdefault("start", (sid, cwd, label)) or 0)
    monkeypatch.setattr(tuimod._tmux, "select_window",
                        lambda t: calls.setdefault("select", t) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")            # project node
        await pilot.press("down")            # folder node
        await pilot.press("down")            # session leaf (sid-1)
        await pilot.press("enter")
        await pilot.pause()
    assert calls["start"][0] == "sid-1"      # started the session
    assert calls["start"][2] == "sprint14"   # human label, not the sid (name_cached planning/sprint14)
    assert calls["select"] == "sid-1"        # auto-switched into it (no second Enter)
    assert app._resume_target is None        # did NOT exit-to-resume


def test_glyph_distinguishes_ownership():
    from _pkg.tui import _glyph
    # legacy (no tmux distinction): green spinner / dim hollow ○
    assert "green" in _glyph("working", 0, None)
    assert "○" in _glyph("idle", 0, None)
    # accessible (our tmux window): solid ● for idle, green spinner for working
    assert "●" in _glyph("idle", 0, True)
    assert "green" in _glyph("working", 0, True)
    # elsewhere (peek-only): hollow ○ for idle, dim spinner for working
    assert "○" in _glyph("idle", 0, False)
    assert "dim" in _glyph("working", 0, False)
    # not live → blank cell
    assert _glyph(None, 0, True).strip() == ""


async def test_enter_flips_into_running_window(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: ["sid-1"])
    monkeypatch.setattr(tuimod._tmux, "select_window",
                        lambda t: calls.setdefault("select", t) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")            # project node
        await pilot.press("down")            # folder node
        await pilot.press("down")            # session leaf (sid-1)
        await pilot.press("enter")
        await pilot.pause()
    assert calls["select"] == "sid-1"


async def test_preview_shows_snapshot_for_live_session(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: ["sid-1"])
    monkeypatch.setattr(tuimod._tmux, "capture_pane", lambda s: "LIVE FRAME for " + s)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._live_states = {"sid-1": "working"}
        group = app._render_live_preview(
            {"sid": "sid-1", "transcript_path": "/x", "name_cached": "planning/sprint14"},
            "sid-1")
    # Group.renderables is a list of rich Text objects; check the captured frame
    # made it into the body.
    bodies = " ".join(r.plain for r in group.renderables if hasattr(r, "plain"))
    assert "LIVE FRAME for sid-1" in bodies


@pytest.mark.asyncio
async def test_quit_with_live_sessions_shuts_down(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: ["sid-1"])
    monkeypatch.setattr(tuimod._tmux, "kill_server", lambda: calls.setdefault("kill", True) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_quit()                    # live sessions → guard modal
        await pilot.pause()
        await pilot.press("s")               # shut down all
        await pilot.pause()
    assert calls.get("kill") is True


@pytest.mark.asyncio
async def test_quit_without_sessions_exits_directly(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: [])
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_quit()                    # no sessions → no modal, just exit
        await pilot.pause()
    # Reaching here without a hanging modal means it exited cleanly.


@pytest.mark.asyncio
async def test_mount_does_not_crash_without_tmux(index_path):
    # Sanity: mount path must be safe when not tmux-hosted (no env var).
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._tmux_enabled is False


@pytest.mark.asyncio
async def test_tmux_offer_shown_once_then_marked(tmp_path, monkeypatch):
    import json
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    # Fresh index dir WITHOUT the tmux-declined marker; retention already decided,
    # help already seen, so the tmux offer is the only modal.
    path = str(tmp_path / "se-index.json")
    json.dump({"version": 1, "folders": [], "sessions": {}}, open(path, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    monkeypatch.delenv("SESSION_EXPLORER_TMUX", raising=False)   # plain launch
    # Force "tmux not installed" regardless of the test machine, so the offer fires.
    monkeypatch.setattr(tuimod._tmux, "available", lambda which=None: False)
    app = SessionExplorerApp(index_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")           # decline the offer
        await pilot.pause()
    assert (tmp_path / ".session-explorer.tmux-declined").exists()


@pytest.mark.asyncio
async def test_enter_refuses_session_live_elsewhere(index_path, monkeypatch):
    # Session is live (in the registry) but NOT one of our tmux windows: Enter
    # must refuse to start a duplicate claude and must not exit-to-resume.
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: [])
    monkeypatch.setattr(tuimod._tmux, "start_window",
                        lambda sid, cwd, label=None: calls.setdefault("start", True) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "_poll_live", lambda: None)   # freeze live state
        app._live_states = {"sid-1": "working"}
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("down")
        app.action_resume()
        await pilot.pause()
    assert "start" not in calls          # no duplicate claude started
    assert app._resume_target is None    # did not exit-to-resume


@pytest.mark.asyncio
async def test_quit_background_persists_and_detaches(index_path, monkeypatch):
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    calls = {}
    monkeypatch.setenv("SESSION_EXPLORER_TMUX", "1")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: ["sid-1"])
    monkeypatch.setattr(tuimod._tmux, "set_persist_flag",
                        lambda p: calls.setdefault("flag", p))
    monkeypatch.setattr(tuimod._tmux, "detach_client",
                        lambda: calls.setdefault("detach", True) or 0)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_quit()
        await pilot.pause()
        await pilot.press("b")            # leave running in background
        await pilot.pause()
    assert "flag" in calls               # persist-flag set before detaching
    assert calls.get("detach") is True
