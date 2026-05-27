"""Textual TUI for session-explorer.

Loaded lazily — importing this module triggers the Textual import, which is
several MB of code. Only happens when the user actually runs `tui`/`launch`.
"""

from __future__ import annotations

import os

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, OptionList, Static, TextArea, Tree
from textual.widgets.option_list import Option

from . import index as _index
from .format import fmt_age, fmt_pct, fmt_tokens
from .tree_model import build_nested_tree, split_path


def _index_path() -> str:
    return os.environ.get("SESSION_EXPLORER_INDEX") or os.path.expanduser(
        "~/.claude/session-explorer-index.json"
    )


# Layout constants. The stat columns must begin at the same absolute screen
# column for every leaf regardless of tree depth, so the Static header (which
# has no tree prefix) can label them. NAME_W is the base name-field width for a
# folder-grouped leaf; an ungrouped leaf sits one level shallower, so its name
# field is widened by GUIDE_DEPTH to push the stats back to the same column.
NAME_W = 24
GUIDE_DEPTH = 4  # cells Textual indents per tree level (Tree.guide_depth)


def _stat_suffix(age: str, tok: str, pct: str, msgs: str, msgs_unit: str, prompt: str) -> str:
    """Render the stat block after the name field. Used for both data rows and
    the header line so the columns line up by construction."""
    return f" {age:>4}  {tok:>6} {pct:>5}  {msgs:>4} {msgs_unit}   {prompt}"


def _row_label(sid: str, s: dict, depth: int) -> str:
    """Leaf row. `depth` is the number of tree levels above the leaf
    (project = 1 level above ungrouped leaves; folder above that = 2 levels;
    etc.). Used to choose the name_field width so stat columns align."""
    _, display = split_path(s.get("name_cached"))
    display = display or sid[:8]
    # Each level of indent steals GUIDE_DEPTH cells from the name field.
    # In practice depth=2 (ungrouped, direct project child) is the shallowest
    # leaf and gets the widest field (= NAME_W + 0); depth=3 (one folder deep)
    # → minus 1*G; depth=4 (two folders deep) → minus 2*G; and so on.
    name_w = max(8, NAME_W + 2 * GUIDE_DEPTH - depth * GUIDE_DEPTH)
    if len(display) > name_w:
        display = display[: name_w - 1] + "…"
    age = fmt_age(s.get("last_active_at"))
    tokens = fmt_tokens(s.get("tokens_estimate", 0))
    pct = fmt_pct(s.get("tokens_window_pct", 0))
    msgs = str(s.get("message_count", 0))
    prompt = (s.get("first_prompt") or "").replace("\n", " ")[:40]
    return f"{display:<{name_w}}" + _stat_suffix(age, tokens, pct, msgs, "msgs", prompt)


def _column_header() -> str:
    """Header line whose labels sit above the stat columns. Pads to a depth-2
    leaf's absolute stat offset (2 levels of guide × GUIDE_DEPTH + NAME_W)."""
    name_region = NAME_W + 2 * GUIDE_DEPTH
    return f"{'NAME':<{name_region}}" + _stat_suffix("AGE", "~TOK", "CTX", "MSGS", "    ", "FIRST PROMPT")


class RenameScreen(ModalScreen[str]):
    """Prompt for a new session name. Returns the entered string or '' on cancel."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Rename session (Enter to confirm, Esc to cancel):"),
            Input(value=self._current, id="rename-input"),
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class MoveScreen(ModalScreen[str]):
    """Pick or type a folder path (e.g. 'planning/sprint14').

    Returns "" to ungroup, the chosen/typed path string, or None on cancel.
    """

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, project: str, existing_paths: list[str], current: str) -> None:
        super().__init__()
        self._project = project
        self._existing = sorted(set(existing_paths))
        self._current = current

    def compose(self) -> ComposeResult:
        opts = [Option("(ungroup)", id="__none__")] + [
            Option(p, id=p) for p in self._existing
        ]
        yield Vertical(
            Label(
                f"Move within '{self._project}' (current: {self._current or '(none)'})."
                " Pick or type a path (use / for nesting):"
            ),
            OptionList(*opts, id="move-list"),
            Input(placeholder="…or type a new path (e.g. team/planning)", id="move-input"),
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        self.dismiss("" if opt_id == "__none__" else opt_id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class NewFolderScreen(ModalScreen[str]):
    """Prompt for a folder path. The Input is prefilled with `prefix` (which
    ends in '/' when creating a child of an existing folder)."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def __init__(self, project: str, prefix: str = "") -> None:
        super().__init__()
        self._project = project
        self._prefix = prefix

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New folder path under '{self._project}' (use / for nesting):"),
            Input(value=self._prefix, id="newfolder-input"),
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no confirmation modal. Returns True iff the user confirmed."""

    BINDINGS = [
        Binding("escape", "dismiss(False)", "Cancel"),
        Binding("y", "dismiss(True)", "Yes", show=False),
        Binding("n", "dismiss(False)", "No", show=False),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._prompt),
            Label("[y] yes   [n / esc] cancel"),
        )


class NotesScreen(ModalScreen[str]):
    """Multi-line editor. Returns the new notes (may be empty) or None on cancel."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        self._ta = TextArea(self._current)
        yield Vertical(
            Label("Notes (Ctrl+S to save, Esc to cancel):"),
            self._ta,
        )

    def action_save(self) -> None:
        self.dismiss(self._ta.text)


class SessionExplorerApp(App):
    CSS = """
    #treepane { width: 1fr; }
    #colheader { height: 1; padding: 0 1; color: $accent; text-style: bold; }
    Tree { padding: 0 1; width: 1fr; }
    #preview { width: 1fr; padding: 0 1; border-left: solid $accent; }
    """

    BINDINGS = [
        Binding("enter", "resume", "Resume", priority=True),
        Binding("r", "rename", "Rename"),
        Binding("m", "move", "Move"),
        Binding("n", "new_folder", "New folder"),
        Binding("d", "delete", "Delete"),
        Binding("e", "notes", "Edit notes"),
        Binding("u", "toggle_unnamed", "Toggle unnamed"),
        Binding("space", "preview", "Preview", priority=True),
        Binding("slash", "filter", "Filter"),
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit", show=False),
    ]

    def __init__(self, index_path: str | None = None) -> None:
        super().__init__()
        self._index_path = index_path or _index_path()
        self._resume_target: str | None = None
        self._resume_cwd: str | None = None
        self._filter_needle: str = ""
        self._show_unnamed: bool = False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # App-level bindings (especially priority ones like Enter→resume) must
        # not fire while a modal screen is up; otherwise the modal's own Enter
        # handler (e.g. Input submit) never runs.
        if action in ("resume", "rename", "move", "new_folder", "delete", "notes", "preview", "filter", "toggle_unnamed") and isinstance(self.screen, ModalScreen):
            return False
        # When the filter Input is focused, swallow the global Esc→quit so Esc
        # can hide the filter (handled in on_key) instead of killing the TUI.
        if action == "quit" and getattr(self, "_filter", None) is not None and self._filter.has_focus:
            return False
        return True

    def on_key(self, event) -> None:
        # Hide filter on Esc and refocus the tree; the bubble-up Esc-quit is
        # gated off by check_action while the filter has focus.
        if event.key == "escape" and self._filter.has_focus:
            self._filter.value = ""
            self._filter_needle = ""
            self._filter.display = False
            self._filter.disabled = True
            self._tree.focus()
            self._populate()
            event.stop()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        self._colheader = Static(_column_header(), id="colheader")
        self._tree: Tree[dict] = Tree("sessions")
        self._tree.show_root = False  # root is redundant with the Header title
        self._tree.guide_depth = GUIDE_DEPTH
        self._preview = Static("", id="preview")
        self._preview.display = False
        yield Horizontal(
            Vertical(self._colheader, self._tree, id="treepane"),
            self._preview,
        )
        self._filter = Input(placeholder="filter…", id="filter")
        self._filter.display = False
        self._filter.disabled = True  # also prevents focus while hidden
        yield self._filter
        yield Footer()

    def on_mount(self) -> None:
        self.title = "session-explorer"
        self._populate()
        # Belt-and-braces: ensure preview is hidden after first compose pass.
        self._preview.display = False

    def _matches(self, sid: str, s: dict) -> bool:
        if not self._filter_needle:
            return True
        n = self._filter_needle
        haystacks = [
            s.get("name_cached") or "",
            s.get("notes") or "",
            s.get("first_prompt") or "",
            s.get("summary") or "",
            sid,
        ]
        return any(n in h.lower() for h in haystacks)

    def _populate(self) -> None:
        from . import folder_store as _fs
        self._tree.clear()
        data = _index.load(self._index_path)
        fs_data = _fs.load(_fs.default_path_for(self._index_path))
        tree = build_nested_tree(data, fs_data, include_unnamed=self._show_unnamed)
        unnamed_hidden = 0
        if not self._show_unnamed:
            unnamed_hidden = sum(
                1 for s in data.get("sessions", {}).values() if not s.get("name_cached")
            )
        root = self._tree.root
        root.expand()

        def count(node):
            return len(node["_sessions"]) + sum(count(c) for c in node["_folders"].values())

        total = sum(count(p) for p in tree.values())
        if unnamed_hidden:
            self.sub_title = f"{total} sessions across {len(tree)} projects · {unnamed_hidden} unnamed hidden (u)"
        else:
            self.sub_title = f"{total} sessions across {len(tree)} projects"

        # `child_depth` is the tree depth (number of guide levels) of any leaf
        # or folder added at this level. With show_root=False, a session that
        # sits directly under a project node is at tree depth 1; one under a
        # folder is at depth 2; and so on. `_row_label` uses the same number
        # to choose name-field width so stat columns land at a constant
        # absolute screen column.
        # `child_depth` is the tree depth (number of guide levels) of any leaf
        # or folder added at this level. With show_root=False, a session that
        # sits directly under a project node is at tree depth 1; one under a
        # folder is at depth 2; and so on. `_row_label` uses the same number
        # to choose name-field width so stat columns land at a constant
        # absolute screen column.
        #
        # We also attach structured `data` to every project and folder node so
        # `_project_and_prefix_for_cursor` can read project_label and folder
        # segments directly instead of reverse-parsing the rendered label.
        def render(parent, project_label, segments, node, child_depth):
            for sid, s in node["_sessions"]:
                if self._matches(sid, s):
                    parent.add_leaf(_row_label(sid, s, child_depth), data={"sid": sid, **s})
            for name in sorted(node["_folders"]):
                child = node["_folders"][name]
                child_segs = segments + [name]
                folder_node = parent.add(
                    f"{name}/", expand=True,
                    data={"project": project_label, "segments": child_segs},
                )
                render(folder_node, project_label, child_segs, child, child_depth + 1)

        for project in sorted(tree):
            node = tree[project]
            proj_node = root.add(
                f"{project} ({count(node)})", expand=True,
                data={"project": project, "segments": []},
            )
            # Project sits at tree depth 0 (show_root=False); its direct
            # children — both ungrouped sessions and top-level folders — are at
            # tree depth 1.
            render(proj_node, project, [], node, child_depth=1)

    def action_resume(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        self._resume_target = node.data["sid"]
        self._resume_cwd = node.data.get("project_path")
        self.exit()

    def action_rename(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        sid = node.data["sid"]
        current = node.data.get("name_cached") or ""
        transcript = node.data.get("transcript_path")

        def after(new_name: str | None) -> None:
            if not new_name or new_name == current or not transcript:
                return
            from .rename import append_custom_title
            append_custom_title(transcript, session_id=sid, new_name=new_name)
            # Update cache immediately so the UI reflects the new name pre-hook.
            from . import index as _index
            def _mut(d: dict) -> dict:
                d["sessions"].setdefault(sid, {})["name_cached"] = new_name
                return d
            _index.mutate(self._index_path, _mut)
            self._populate()

        self.push_screen(RenameScreen(current), after)

    def action_move(self) -> None:
        from . import folder_store as _fs
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell(); return
        sid = node.data["sid"]
        name = node.data.get("name_cached") or ""
        transcript = node.data.get("transcript_path")
        project = node.data.get("project_label")
        if not project:
            self.bell(); return
        segments, display = split_path(name)
        current_folder = "/".join(segments)

        fs_path = _fs.default_path_for(self._index_path)
        # Folder list = store ∪ folders implied by indexed session names in this project.
        paths = set(_fs.list_paths(fs_path, project))
        data = _index.load(self._index_path)
        for s in data.get("sessions", {}).values():
            if s.get("project_label") != project:
                continue
            segs, _ = split_path(s.get("name_cached"))
            for i in range(1, len(segs) + 1):
                paths.add("/".join(segs[:i]))

        def after(target: "str | None") -> None:
            if target is None or not transcript:
                return
            # Normalise the typed path: drop empty/whitespace segments from
            # `foo//bar`, `/foo/bar`, `foo/bar/`, etc., before persisting or
            # joining with the leaf name.
            if target:
                target = "/".join(seg for seg in target.split("/") if seg.strip())
            leaf = display or sid[:8]
            new_name = leaf if not target else f"{target}/{leaf}"
            from .rename import append_custom_title
            append_custom_title(transcript, session_id=sid, new_name=new_name)
            if target:
                _fs.add(fs_path, project, target)
            def _mut(d: dict) -> dict:
                d["sessions"].setdefault(sid, {})["name_cached"] = new_name
                return d
            _index.mutate(self._index_path, _mut)
            self._populate()

        self.push_screen(MoveScreen(project, sorted(paths), current_folder), after)

    def action_new_folder(self) -> None:
        from . import folder_store as _fs
        project, prefix = self._project_and_prefix_for_cursor()
        if not project:
            self.bell(); return

        def after(path: str) -> None:
            if not path:
                return
            # Normalize: collapse repeated/leading/trailing slashes; drop empty segs.
            segs = [seg.strip() for seg in path.split("/") if seg.strip()]
            if not segs:
                return
            _fs.add(_fs.default_path_for(self._index_path), project, "/".join(segs))
            self._populate()

        self.push_screen(NewFolderScreen(project, prefix), after)

    def _project_and_prefix_for_cursor(self) -> "tuple[str | None, str]":
        """Return (project_label, prefix). prefix ends in '/' when the cursor sits
        on a folder so child creation is one segment away from done.

        Reads structured `data` attached to project and folder nodes by
        `_populate` — we used to reverse-parse rendered labels, which coupled
        this function to the rendering convention.
        """
        node = self._tree.cursor_node
        if node is None or node is self._tree.root:
            return (None, "")
        # Treat a session leaf as its containing folder/project node.
        if node.data and "sid" in node.data:
            node = node.parent
            if node is None or node is self._tree.root:
                return (None, "")
        data = node.data if node is not None else None
        if not data or "project" not in data:
            return (None, "")
        project = data["project"]
        segments = data.get("segments") or []
        prefix = "/".join(segments) + "/" if segments else ""
        return (project, prefix)

    def action_delete(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        sid = node.data["sid"]
        name = node.data.get("name_cached") or sid[:8]

        def after(ok: bool) -> None:
            if not ok:
                return
            from .delete import delete_session
            delete_session(self._index_path, sid)
            self._populate()

        self.push_screen(
            ConfirmScreen(f"Delete '{name}'? This removes the JSONL too."), after
        )

    def action_notes(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        sid = node.data["sid"]
        current = node.data.get("notes") or ""

        def after(new_notes: str | None) -> None:
            if new_notes is None:
                return
            from .notes import set_notes
            set_notes(self._index_path, sid, new_notes)
            self._populate()

        self.push_screen(NotesScreen(current), after)

    def action_preview(self) -> None:
        self._preview.display = not self._preview.display
        self._refresh_preview()

    def action_toggle_unnamed(self) -> None:
        self._show_unnamed = not self._show_unnamed
        self._populate()

    def action_filter(self) -> None:
        self._filter.display = True
        self._filter.disabled = False
        self._filter.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is self._filter:
            self._filter_needle = event.value.lower().strip()
            self._populate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Pressing Enter on the filter input commits the needle, hides the bar,
        # and returns focus to the tree. The needle stays applied; Esc clears it.
        if event.input is self._filter:
            self._filter.display = False
            self._filter.disabled = True
            self._tree.focus()

    def _refresh_preview(self) -> None:
        if not self._preview.display:
            return
        node = self._tree.cursor_node
        s = node.data if node and node.data else {}
        notes = s.get("notes") or "(no notes)"
        prompt = s.get("first_prompt") or "(no first prompt recorded)"
        summary = s.get("summary") or "(no summary)"
        path = s.get("transcript_path") or "(unknown path)"
        self._preview.update(
            f"[b]Notes[/]\n{notes}\n\n"
            f"[b]First prompt[/]\n{prompt}\n\n"
            f"[b]Summary[/]\n{summary}\n\n"
            f"[b]Path[/]\n{path}"
        )

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self._refresh_preview()


def run() -> int:
    app = SessionExplorerApp()
    app.run()
    target = getattr(app, "_resume_target", None)
    if target:
        # chdir into the session's original project so `claude --resume`
        # opens in the right workspace — without this, Claude inherits the
        # spawned terminal's cwd (usually $HOME) and shows a fresh "trust
        # folder" prompt instead of restoring the session.
        cwd = getattr(app, "_resume_cwd", None)
        if cwd and os.path.isdir(cwd):
            os.chdir(cwd)
        os.execvp("claude", ["claude", "--resume", target])
    return 0
