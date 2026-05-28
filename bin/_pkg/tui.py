"""Textual TUI for session-explorer.

Loaded lazily — importing this module triggers the Textual import, which is
several MB of code. Only happens when the user actually runs `tui`/`launch`.
"""

from __future__ import annotations

import os

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, OptionList, ProgressBar, Static, TextArea, Tree
from textual.widgets.option_list import Option

from . import __version__
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


def _preview_text(s: dict) -> str:
    """Markup for the preview pane of a session `data` dict. Pure so it can be
    unit-tested without spinning up the app.

    The headline is the full display segment — the grid truncates the name to a
    fixed column width, so the preview is where you see it whole. The folder
    path, project, and the rest of the metadata follow as labelled fields."""
    segments, display = split_path(s.get("name_cached"))
    sid = s.get("sid") or ""
    headline = display or (sid[:8] if sid else "(unnamed)")
    context = f"{fmt_tokens(s.get('tokens_estimate', 0))} {fmt_pct(s.get('tokens_window_pct', 0))}"

    def field(label: str, value: str) -> str:
        return f"[b]{label:<10}[/]{value}"

    lines = [
        f"[b]{headline}[/]",
        "",
        field("Project", s.get("project_label") or "(unknown)"),
        field("Path", s.get("project_path") or "(unknown)"),
        field("Folder", "/".join(segments) or "(none)"),
        field("Branch", s.get("branch") or "(none)"),
        field("Active", fmt_age(s.get("last_active_at"))),
        field("Created", (s.get("created_at") or "")[:10] or "—"),
        field("Messages", str(s.get("message_count", 0))),
        field("Context", context),
        field("Model", s.get("model") or "(unknown)"),
        field("Session", sid or "—"),
        "",
        "[b]Notes[/]",
        s.get("notes") or "(no notes)",
        "",
        "[b]First prompt[/]",
        s.get("first_prompt") or "(no first prompt recorded)",
        "",
        "[b]Transcript[/]",
        s.get("transcript_path") or "(unknown path)",
    ]
    return "\n".join(lines)


def _folder_has_sessions(index_data: dict, project: str, folder_segments: list) -> bool:
    """True if any session in `project` lives under the folder `folder_segments`
    (its name's folder path has them as a prefix). Pure, so it can be
    unit-tested. Unnamed sessions have no folder and never count."""
    n = len(folder_segments)
    for s in index_data.get("sessions", {}).values():
        if s.get("project_label") != project:
            continue
        name = s.get("name_cached")
        if not name:
            continue
        segs, _ = split_path(name)
        if segs[:n] == folder_segments:
            return True
    return False


def _empty_state_text(total_indexed: int, visible: int, unnamed_hidden: int,
                      filter_active: bool, scanned: bool) -> "str | None":
    """Message for the tree pane when no rows are visible, else None.

    Pure so it can be unit-tested. Branch order is deliberate: an active filter
    explains itself first (the user is mid-search), then hidden-unnamed, then
    the empty-index prompts — split by whether a rescan has already run, so a
    fruitless scan doesn't keep telling the user to "press F5"."""
    if visible > 0:
        return None
    if filter_active:
        return "No sessions match the current filter.\nPress Esc to clear it."
    if unnamed_hidden > 0:
        return (f"{unnamed_hidden} unnamed session(s) hidden.\n"
                "Press u to show them, then r to name one.")
    if total_indexed == 0:
        if scanned:
            return "No sessions found under ~/.claude/projects/."
        return ("No sessions indexed yet.\n"
                "Press F5 to scan ~/.claude/projects/ for your sessions.")
    return None


def _help_text() -> str:
    """Markup for the help overlay. Pure so it can be unit-tested. Explains the
    two non-obvious concepts (slash-folders, named-only visibility) and lists
    every keybinding, then credits the author."""

    def key(k: str, desc: str) -> str:
        return f"  [b]{k:<7}[/]{desc}"

    return "\n".join([
        "[b]session-explorer — help[/]",
        "",
        "[b]Naming & folders[/]",
        "A session's name doubles as its folder path. Slashes split it: the last",
        "segment is the display name, everything before it is the folder tree.",
        "  [b]team/planning/sprint14[/]  →  folders [b]team/planning[/], shown as [b]sprint14[/]",
        "Rename (r) or move (m) to re-file a session — there are no separate tags.",
        "",
        "[b]What you see[/]",
        "Only named (renamed) sessions show by default. Unnamed stubs are hidden;",
        "press [b]u[/] to toggle them on so you can rename or delete them.",
        "",
        "[b]Keys[/]",
        key("↑ ↓", "Move between rows"),
        key("← →", "Collapse / expand a folder or project"),
        key("Enter", "Resume the selected session"),
        key("Space", "Toggle the preview pane"),
        key("r", "Rename (also re-files into a different folder)"),
        key("m", "Move the selected session to a folder"),
        key("n", "New folder under the current project/folder"),
        key("d", "Delete the selected session, or an empty folder (confirms)"),
        key("e", "Edit notes (Ctrl+S to save)"),
        key("u", "Toggle visibility of unnamed sessions"),
        key("F5", "Rescan ~/.claude/projects/ — import pre-existing sessions"),
        key("/", "Live filter across name, notes, first prompt"),
        key("h", "Show this help"),
        key("Esc", "Close the preview, filter, or this help"),
        key("q", "Quit"),
        "",
        "[dim]Esc, q, h, or Space closes this help.[/]",
        "",
        f"[b]session-explorer v{__version__}[/]  ·  Made by Johan Lindahl  <johan.lindahl@snojken.com>",
        '[link="https://github.com/johan-lindahl/session-explorer"]https://github.com/johan-lindahl/session-explorer[/link]',
    ])


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


class HelpScreen(ModalScreen[None]):
    """Read-only help overlay. Any of Esc / q / h / Space closes it."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close", show=False),
        Binding("h", "dismiss", "Close", show=False),
        Binding("space", "dismiss", "Close", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield VerticalScroll(Static(_help_text(), id="help-body"), id="help")


class SessionExplorerApp(App):
    CSS = """
    #treepane { width: 1fr; }
    #colheader { height: 1; padding: 0 1; color: $accent; text-style: bold; }
    #empty-state { padding: 2 2; color: $text-muted; }
    #scanbar { margin: 0 2 1 2; }
    Tree { padding: 0 1; width: 1fr; }
    #preview { width: 1fr; padding: 0 1; border-left: solid $accent; }
    HelpScreen { align: center middle; }
    #help { width: 78; max-width: 90%; height: auto; max-height: 90%;
            padding: 1 2; border: round $accent; background: $surface; }
    """

    BINDINGS = [
        Binding("enter", "resume", "Resume", priority=True),
        Binding("r", "rename", "Rename"),
        Binding("m", "move", "Move"),
        Binding("n", "new_folder", "New folder"),
        Binding("d", "delete", "Delete"),
        Binding("e", "notes", "Edit notes"),
        Binding("u", "toggle_unnamed", "Toggle unnamed"),
        Binding("f5", "rescan", "Rescan"),
        Binding("space", "preview", "Preview", priority=True),
        Binding("slash", "filter", "Filter"),
        Binding("h", "help", "Help"),
        Binding("q", "quit", "Quit"),
        Binding("escape", "close_preview", "Close preview", show=False),
        # The Tree's own toggle keys (enter/space) are taken over by resume and
        # preview above, and this Textual version has no left/right binding, so
        # bind them explicitly or keyboard expand/collapse wouldn't work at all.
        Binding("right", "expand_node", "Expand", show=False),
        Binding("left", "collapse_node", "Collapse", show=False),
    ]

    def __init__(self, index_path: str | None = None,
                 projects_root: str | None = None) -> None:
        super().__init__()
        self._index_path = index_path or _index_path()
        # None → index.reindex/backfill use the default ~/.claude/projects.
        # Injected in tests to point the rescan at a fixture tree.
        self._projects_root = projects_root
        self._resume_target: str | None = None
        self._resume_cwd: str | None = None
        self._filter_needle: str = ""
        self._show_unnamed: bool = False
        # Flips after the first rescan so the empty-state can switch from
        # "press F5 to scan" to "no sessions found".
        self._scanned: bool = False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # App-level bindings (especially priority ones like Enter→resume) must
        # not fire while a modal screen is up; otherwise the modal's own Enter
        # handler (e.g. Input submit) never runs.
        if action in ("resume", "rename", "move", "new_folder", "delete", "notes", "preview", "close_preview", "filter", "toggle_unnamed", "rescan", "help", "expand_node", "collapse_node") and isinstance(self.screen, ModalScreen):
            return False
        # While the filter Input is focused, never let `q` quit the TUI — the
        # keystroke belongs in the filter text, not the global quit binding.
        if action == "quit" and getattr(self, "_filter", None) is not None and self._filter.has_focus:
            return False
        return True

    def on_key(self, event) -> None:
        # Hide the filter on Esc and refocus the tree. Stopping the event here
        # keeps the global Esc→close_preview binding from also firing.
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
        self._empty = Static("", id="empty-state")
        self._empty.display = False
        self._progress = ProgressBar(show_eta=False, id="scanbar")
        self._progress.display = False
        yield Horizontal(
            Vertical(self._colheader, self._tree, self._empty, self._progress, id="treepane"),
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
        # First run only: pop the help overlay so newcomers learn the slash-
        # folder naming and the named-only default. The marker is written up
        # front so a crash mid-session still counts as "seen".
        if not os.path.exists(self._help_marker_path()):
            self._mark_help_seen()
            self.action_help()

    def _help_marker_path(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.abspath(self._index_path)),
            ".session-explorer.help-seen",
        )

    def _mark_help_seen(self) -> None:
        try:
            open(self._help_marker_path(), "a").close()
        except OSError:
            pass

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

        # Empty-state: when no session rows would render (after the filter),
        # show an actionable message in place of the tree instead of blank space.
        def visible_count(node):
            n = sum(1 for sid, s in node["_sessions"] if self._matches(sid, s))
            return n + sum(visible_count(c) for c in node["_folders"].values())

        # The scan UI is transient — only action_rescan/_on_progress show it.
        # _populate always runs after a scan completes, so clear it here.
        self._progress.display = False

        visible = sum(visible_count(p) for p in tree.values())
        msg = _empty_state_text(
            total_indexed=len(data.get("sessions", {})),
            visible=visible,
            unnamed_hidden=unnamed_hidden,
            filter_active=bool(self._filter_needle),
            scanned=self._scanned,
        )
        if msg is None:
            self._empty.display = False
            self._tree.display = True
            self._colheader.display = True
        else:
            self._empty.update(msg)
            self._empty.display = True
            self._tree.display = False
            self._colheader.display = False

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

    def action_expand_node(self) -> None:
        node = self._tree.cursor_node
        if node is not None and node.allow_expand:
            node.expand()

    def action_collapse_node(self) -> None:
        node = self._tree.cursor_node
        if node is not None and node.allow_expand:
            node.collapse()

    def action_resume(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        sid = node.data["sid"]
        project_path = node.data.get("project_path")

        def proceed() -> None:
            self._resume_target = sid
            self._resume_cwd = project_path
            self.exit()

        # If the session's git worktree was deleted, resuming has to recreate
        # that directory (claude --resume is cwd-scoped). Warn before doing so.
        if _dead_worktree_repo(project_path):
            def after(ok: bool) -> None:
                if ok:
                    proceed()
            self.push_screen(
                ConfirmScreen(
                    "This session is from a deleted git worktree.\n"
                    "Resume anyway? This re-creates an empty directory:\n"
                    f"{project_path}"
                ),
                after,
            )
        else:
            proceed()

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
        data = node.data if (node and node.data) else {}

        # Session leaf: delete the session + its JSONL (with confirmation).
        if "sid" in data:
            sid = data["sid"]
            name = data.get("name_cached") or sid[:8]

            def after(ok: bool) -> None:
                if not ok:
                    return
                from .delete import delete_session
                delete_session(self._index_path, sid)
                self._populate()

            self.push_screen(
                ConfirmScreen(f"Delete '{name}'? This removes the JSONL too."), after
            )
            return

        # Folder node (has segments, no sid): delete it only if empty.
        segments = data.get("segments") or []
        project = data.get("project")
        if project and segments:
            self._delete_folder(project, segments)
            return

        # Project node or non-selectable row.
        self.bell()

    def _delete_folder(self, project: str, segments: list) -> None:
        from . import folder_store as _fs
        folder_path = "/".join(segments)
        if _folder_has_sessions(_index.load(self._index_path), project, segments):
            self.notify(
                f"Cannot delete '{folder_path}': folder is not empty.",
                severity="warning",
            )
            return

        def after(ok: bool) -> None:
            if not ok:
                return
            _fs.remove_subtree(_fs.default_path_for(self._index_path), project, folder_path)
            self._populate()

        self.push_screen(ConfirmScreen(f"Delete empty folder '{folder_path}'?"), after)

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

    def action_close_preview(self) -> None:
        # Esc hides the preview when it's open; otherwise it does nothing.
        # (Quit moved to `q` only — Esc no longer kills the explorer.)
        if self._preview.display:
            self._preview.display = False

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_toggle_unnamed(self) -> None:
        self._show_unnamed = not self._show_unnamed
        self._populate()

    def action_rescan(self) -> None:
        # reindex shells out to `git` per session, so it runs in a worker thread
        # to keep the UI responsive on large histories. Show the scan UI now so
        # there's no flash of the old tree before the first progress callback.
        self.sub_title = "scanning ~/.claude/projects/…"
        self._empty.update("Scanning ~/.claude/projects/…")
        self._empty.display = True
        self._tree.display = False
        self._colheader.display = False
        self._progress.update(total=None, progress=0)  # indeterminate until pre-count
        self._progress.display = True
        self._rescan_worker()

    def _on_progress(self, done: int, total: int) -> None:
        """Update the scan UI. Called on the main thread (marshalled from the
        worker via call_from_thread)."""
        self._empty.update(f"Scanning ~/.claude/projects/…  {done}/{total}")
        self._empty.display = True
        self._tree.display = False
        self._colheader.display = False
        self._progress.update(total=total or None, progress=done)
        self._progress.display = True

    @work(thread=True, exclusive=True)
    def _rescan_worker(self) -> None:
        def progress(done: int, total: int) -> None:
            self.call_from_thread(self._on_progress, done, total)
        try:
            _index.reindex(self._index_path, projects_root=self._projects_root,
                           progress=progress)
        finally:
            self._scanned = True
            self.call_from_thread(self._populate)

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
        data = node.data if node and node.data else {}
        if "sid" not in data:
            # Cursor is on a project/folder node, not a session.
            self._preview.update("[dim]Select a session to preview.[/]")
            return
        self._preview.update(_preview_text(data))

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self._refresh_preview()


_WORKTREE_MARKER = "/.claude/worktrees/"


def _dead_worktree_repo(project_path: "str | None") -> "str | None":
    """If `project_path` is a deleted git-worktree path whose parent repo still
    exists, return that repo root; else None. Pure — lets the TUI decide whether
    to warn before recreating the worktree dir on resume."""
    if not project_path or os.path.isdir(project_path):
        return None
    if _WORKTREE_MARKER not in project_path:
        return None
    root = project_path.split(_WORKTREE_MARKER, 1)[0]
    return root if os.path.isdir(root) else None


def _resolve_resume_cwd(project_path: "str | None") -> "str | None":
    """Directory to chdir into before `claude --resume`.

    `claude --resume` is scoped to the exact cwd that recorded the session, so
    to resume a session whose git worktree was deleted we recreate that (empty)
    worktree dir — the only way claude can locate the transcript filed under the
    worktree's project key. The TUI confirms this side effect first (see
    action_resume / _dead_worktree_repo). Returns a usable directory, or None
    when there's nothing to chdir into (caller leaves cwd alone)."""
    if not project_path:
        return None
    if os.path.isdir(project_path):
        return project_path
    if _dead_worktree_repo(project_path):
        try:
            os.makedirs(project_path, exist_ok=True)
            return project_path
        except OSError:
            return None
    return None


def _resume_argv(target: str) -> list[str]:
    """argv for resuming a session.

    `claude --resume` takes an OPTIONAL value (`-r, --resume [value]`), so the
    space form `--resume <id>` can lose the value and the earlier `--resume --
    <id>` hardening made `--resume` valueless entirely — opening the interactive
    session picker instead of resuming. Bind the id with `=` so it's
    unambiguously the option's value. This is also injection-safe: a session id
    that starts with '-' stays inside the single `--resume=<id>` token and can
    never be parsed as a separate `claude` flag."""
    return ["claude", f"--resume={target}"]


def run() -> int:
    app = SessionExplorerApp()
    app.run()
    target = getattr(app, "_resume_target", None)
    if target:
        # chdir into the session's original project so `claude --resume`
        # opens in the right workspace — without this, Claude inherits the
        # spawned terminal's cwd (usually $HOME) and shows a fresh "trust
        # folder" prompt instead of restoring the session.
        cwd = _resolve_resume_cwd(getattr(app, "_resume_cwd", None))
        if cwd:
            os.chdir(cwd)
        os.execvp("claude", _resume_argv(target))
    return 0
