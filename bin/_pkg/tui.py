"""Textual TUI for session-explorer.

Loaded lazily — importing this module triggers the Textual import, which is
several MB of code. Only happens when the user actually runs `tui`/`launch`.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Tree
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


class SessionExplorerApp(App):
    CSS = """
    Tree { padding: 0 1; }
    """

    BINDINGS = [
        Binding("enter", "resume", "Resume", priority=True),
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit", show=False),
    ]

    def __init__(self, index_path: str | None = None) -> None:
        super().__init__()
        self._index_path = index_path or _index_path()
        self._resume_target: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        self._tree: Tree[dict] = Tree("sessions")
        yield self._tree
        yield Footer()

    def on_mount(self) -> None:
        self.title = "session-explorer"
        self._populate()

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


def run() -> int:
    app = SessionExplorerApp()
    app.run()
    target = getattr(app, "_resume_target", None)
    if target:
        os.execvp("claude", ["claude", "--resume", target])
    return 0
