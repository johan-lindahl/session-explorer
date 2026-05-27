"""Textual TUI for session-explorer.

Loaded lazily — importing this module triggers the Textual import, which is
several MB of code. Only happens when the user actually runs `tui`/`launch`.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, OptionList, Static, TextArea, Tree
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from . import index as _index
from .format import fmt_age, fmt_pct, fmt_tokens
from .tree_model import build_tree, split_folder


def _index_path() -> str:
    return os.environ.get("SESSION_EXPLORER_INDEX") or os.path.expanduser(
        "~/.claude/session-explorer-index.json"
    )


def _row_label(sid: str, s: dict) -> str:
    _, display = split_folder(s.get("name_cached"))
    display = display or sid[:8]
    age = fmt_age(s.get("last_active_at"))
    tokens = fmt_tokens(s.get("tokens_estimate", 0))
    pct = fmt_pct(s.get("tokens_window_pct", 0))
    msgs = s.get("message_count", 0)
    prompt = (s.get("first_prompt") or "").replace("\n", " ")[:40]
    return f"{display:<24} {age:>4}  {tokens:>6} {pct:>5}  {msgs:>4} msgs   {prompt}"


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
    """Pick or type a folder.

    Returns the chosen folder name (empty string to ungroup, or None on cancel).
    """

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, existing_folders: list[str], current: str) -> None:
        super().__init__()
        self._existing = sorted(set(existing_folders))
        self._current = current

    def compose(self) -> ComposeResult:
        opts = [Option("(ungroup)", id="__none__")] + [
            Option(f, id=f) for f in self._existing
        ]
        yield Vertical(
            Label(
                f"Move to folder (current: {self._current or '(none)'}). Pick or type:"
            ),
            OptionList(*opts, id="move-list"),
            Input(placeholder="…or type a new folder name", id="move-input"),
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        self.dismiss("" if opt_id == "__none__" else opt_id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class NewFolderScreen(ModalScreen[str]):
    """Prompt for a new folder name. Returns the entered string or '' on cancel."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("New folder name (Enter to confirm, Esc to cancel):"),
            Input(id="newfolder-input"),
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
        Binding("space", "preview", "Preview", priority=True),
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit", show=False),
    ]

    def __init__(self, index_path: str | None = None) -> None:
        super().__init__()
        self._index_path = index_path or _index_path()
        self._resume_target: str | None = None

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # App-level bindings (especially priority ones like Enter→resume) must
        # not fire while a modal screen is up; otherwise the modal's own Enter
        # handler (e.g. Input submit) never runs.
        if action in ("resume", "rename", "move", "new_folder", "delete", "notes", "preview") and isinstance(self.screen, ModalScreen):
            return False
        return True

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        self._tree: Tree[dict] = Tree("sessions")
        self._preview = Static("", id="preview")
        self._preview.display = False
        yield Horizontal(self._tree, self._preview)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "session-explorer"
        self._populate()
        # Belt-and-braces: ensure preview is hidden after first compose pass.
        self._preview.display = False

    def _populate(self) -> None:
        self._tree.clear()
        data = _index.load(self._index_path)
        tree = build_tree(data)
        root = self._tree.root
        root.expand()
        total = sum(
            len(sessions)
            for folders in tree.values()
            for sessions in folders.values()
        )
        self.sub_title = f"{total} sessions across {len(tree)} projects"
        for project in sorted(tree):
            folders = tree[project]
            proj_node = root.add(f"▼ {project} ({sum(len(v) for v in folders.values())})", expand=True)
            for folder in sorted(folders):
                sessions = folders[folder]
                if folder and folder != "(unnamed)":
                    folder_node = proj_node.add(f"{folder}/", expand=True)
                else:
                    folder_node = proj_node
                for sid, s in sessions:
                    folder_node.add_leaf(_row_label(sid, s), data={"sid": sid, **s})

    def action_resume(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        self._resume_target = node.data["sid"]
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
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        sid = node.data["sid"]
        name = node.data.get("name_cached") or ""
        transcript = node.data.get("transcript_path")
        current_folder, display = split_folder(name)
        # Folder list = folders[] ∪ {folders seen in sessions}
        data = _index.load(self._index_path)
        folders = set(data.get("folders") or [])
        for s in data.get("sessions", {}).values():
            f, _ = split_folder(s.get("name_cached"))
            if f:
                folders.add(f)

        def after(target: str | None) -> None:
            if target is None or not transcript:
                return
            new_name = display if not target else f"{target}-{display or sid[:8]}"
            from .rename import append_custom_title
            append_custom_title(transcript, session_id=sid, new_name=new_name)

            def _mut(d: dict) -> dict:
                d["sessions"].setdefault(sid, {})["name_cached"] = new_name
                return d
            _index.mutate(self._index_path, _mut)
            self._populate()

        self.push_screen(MoveScreen(sorted(folders), current_folder), after)

    def action_new_folder(self) -> None:
        def after(name: str) -> None:
            if not name:
                return
            from .folders import add_folder
            add_folder(self._index_path, name)
            self._populate()

        self.push_screen(NewFolderScreen(), after)

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
        os.execvp("claude", ["claude", "--resume", target])
    return 0
