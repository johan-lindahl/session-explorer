"""Textual TUI for session-explorer.

Loaded lazily — importing this module triggers the Textual import, which is
several MB of code. Only happens when the user actually runs `tui`/`launch`.
"""

from __future__ import annotations

import os
import uuid

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Footer, Header, Input, Label, OptionList, ProgressBar, Static, TextArea, Tree
from textual.widgets.option_list import Option

from . import __version__
from . import index as _index
from . import snapshot as _snapshot
from . import tmux as _tmux
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
GLYPH_W = 2  # leading cells reserved on every row for the live-state glyph
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
IDLE_GLYPH = "○"   # idle, peek-only (running in a separate terminal)
OURS_GLYPH = "●"   # idle, accessible (running in our tmux — Enter to jump in)
SPINNER_INTERVAL = 0.2   # seconds between spinner frames
LIVE_POLL_INTERVAL = 2.0  # seconds between registry polls
SNAPSHOT_POLL_INTERVAL = 1.0  # seconds between preview snapshot refreshes
LIVE_PREVIEW_LINES = 24  # max lines of live snapshot shown below the metadata


def _glyph(state: "str | None", frame: int, ours: "bool | None" = None) -> str:
    """A GLYPH_W-wide leading cell for a row's live state. Returns Textual
    console markup (rendered by Tree.process_label). Pure for unit testing.

    `ours` distinguishes a session running in *our* tmux (accessible — press
    Enter to jump in) from one running in a separate terminal (peek-only). All
    live glyphs are green (visible); the SHAPE carries the distinction:
      None  → legacy non-tmux look: green spinner / dim ○
      True  → accessible: green spinner / solid green ● (jump in)
      False → elsewhere:  green spinner / hollow green ○ (peek-only)

    Display width is always GLYPH_W cells after markup is stripped (the markup
    glyph is 1 cell + 1 separating space), so stat columns stay aligned."""
    if state == "working":
        ch = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
        return f"[green]{ch}[/] "
    if state == "idle":
        if ours is True:
            return f"[green]{OURS_GLYPH}[/] "      # accessible: solid ●
        if ours is False:
            return f"[green]{IDLE_GLYPH}[/] "      # elsewhere: hollow ○ (visible)
        return f"[dim]{IDLE_GLYPH}[/] "            # legacy non-tmux: unchanged
    return " " * GLYPH_W


def _stat_suffix(age: str, tok: str, pct: str, msgs: str, msgs_unit: str, prompt: str) -> str:
    """Render the stat block after the name field. Used for both data rows and
    the header line so the columns line up by construction."""
    return f" {age:>4}  {tok:>6} {pct:>5}  {msgs:>4} {msgs_unit}   {prompt}"


def _row_label(sid: str, s: dict, depth: int, glyph: str = "  ") -> str:
    """Leaf row. `depth` is the number of tree levels above the leaf
    (project = 1 level above ungrouped leaves; folder above that = 2 levels;
    etc.). Used to choose the name_field width so stat columns align.
    `glyph` is a GLYPH_W-wide live-state prefix (see _glyph); default is blank
    so non-live rows and existing callers are unaffected."""
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
    return glyph + f"{display:<{name_w}}" + _stat_suffix(age, tokens, pct, msgs, "msgs", prompt)


def _column_header() -> str:
    """Header line whose labels sit above the stat columns. Pads to a depth-2
    leaf's absolute stat offset (GLYPH_W glyph cells + 2 levels of guide ×
    GUIDE_DEPTH + NAME_W)."""
    name_region = NAME_W + 2 * GUIDE_DEPTH
    return " " * GLYPH_W + f"{'NAME':<{name_region}}" + _stat_suffix("AGE", "~TOK", "CTX", "MSGS", "    ", "FIRST PROMPT")


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
        "[b]Live sessions[/]",
        "Sessions running right now are flagged in the left column:",
        "  [green]⠿[/] spinner = working    [green]●[/] = idle, started here (Enter to jump in)",
        "  [green]○[/] = idle, running in another terminal (peek-only via Space)",
        "The subtitle shows [b]● N active[/]. Live rows refresh from their",
        "transcript about every 2s, so the first prompt, message count, tokens",
        "and context % fill in and tick up as the agent works. Live sessions",
        "show even when unnamed.",
        "",
        "[b]Running sessions in tmux[/]",
        "When launched with tmux, the explorer stays in the left pane and the",
        "session you resume docks in a pane on the right:",
        "  • [b]Enter[/] (or double-click) docks a session beside the tree and",
        "    puts you in it; Enter on another session swaps it in (the previous",
        "    one keeps running in the background).",
        "  • [b]F9[/] (or click a pane) switches focus between tree and session.",
        "  • [b]F12[/] zooms the focused pane fullscreen; press again to restore.",
        "  • [b]Space[/] peeks a live snapshot of any session without docking it.",
        "  • [b]q[/] with sessions running asks whether to shut them all down or",
        "    leave them running (reattach next time you open the explorer).",
        "",
        "[b]Keys[/]",
        key("↑ ↓", "Move between rows"),
        key("← →", "Collapse / expand a folder or project"),
        key("Enter", "Resume: start & switch into the session (flip into a running one)"),
        key("2×click", "Same as Enter on a session row"),
        key("Space", "Peek a live snapshot / toggle the preview pane"),
        key("F9", "Switch focus between the explorer tree and the session"),
        key("F12", "Zoom the focused pane fullscreen (toggle)"),
        key("r", "Rename a session (re-files it) or a folder (renames its subtree)"),
        key("m", "Move a session, or re-parent a whole folder, to another path"),
        key("n", "New folder under the current project/folder"),
        key("c", "New session in the current project/folder (names it; optional worktree)"),
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


class _PanelScreen(ModalScreen):
    """Base for the modal dialogs (rename, move, new folder, delete, notes, rescan progress): a centered rounded panel on a dimmed,
    translucent backdrop so the session tree shows through (matches the help
    overlay). Subclasses wrap their widgets in `Vertical(..., id="panel")` with a
    bold `.dialog-title` Label first and a dim `.dialog-hint` Label last. Each
    subclass keeps its own Esc binding/return value (unchanged behavior)."""

    DEFAULT_CSS = """
    _PanelScreen { align: center middle; background: $surface-darken-1 60%; }
    _PanelScreen #panel {
        width: auto; max-width: 80%; height: auto; max-height: 90%;
        padding: 1 2; border: round $accent; background: $surface;
    }
    _PanelScreen #panel Input,
    _PanelScreen #panel OptionList,
    _PanelScreen #panel TextArea { width: 60; }
    _PanelScreen #panel TextArea { min-height: 8; }
    _PanelScreen .dialog-title { text-style: bold; }
    _PanelScreen .dialog-hint { color: $text-muted; }
    """


class RenameScreen(_PanelScreen):
    """Prompt for a new session name. Returns the entered string or '' on cancel."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def __init__(self, current: str, title: str = "Rename session") -> None:
        super().__init__()
        self._current = current
        self._title = title

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._title, classes="dialog-title"),
            Input(value=self._current, id="rename-input"),
            Label("enter save · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class MoveScreen(_PanelScreen):
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
            Label(f"Move within '{self._project}'  (current: {self._current or '(none)'})",
                  classes="dialog-title"),
            OptionList(*opts, id="move-list"),
            Input(placeholder="…or type a new path (e.g. team/planning)", id="move-input"),
            Label("enter / select to move · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        self.dismiss("" if opt_id == "__none__" else opt_id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class NewFolderScreen(_PanelScreen):
    """Prompt for a folder path. The Input is prefilled with `prefix` (which
    ends in '/' when creating a child of an existing folder)."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def __init__(self, project: str, prefix: str = "") -> None:
        super().__init__()
        self._project = project
        self._prefix = prefix

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New folder under '{self._project}' (use / to nest)", classes="dialog-title"),
            Input(value=self._prefix, id="newfolder-input"),
            Label("enter create · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


class NewSessionScreen(_PanelScreen):
    """Create a new Claude session. Returns
    {name, cwd, worktree: bool, worktree_name: str} or None on cancel.

    The name Input prefills with the folder prefix (ends in '/') so the session
    nests in the current folder; a slash-path is folder placement exactly like
    rename/move. Enter from any Input gathers all fields and submits."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, project: str, name_prefix: str = "", cwd: str = "") -> None:
        super().__init__()
        self._project = project
        self._name_prefix = name_prefix
        self._cwd = cwd

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New session in '{self._project}' (use / to nest)",
                  classes="dialog-title"),
            Input(value=self._name_prefix, placeholder="session name", id="ns-name"),
            Input(value=self._cwd, placeholder="working directory", id="ns-cwd"),
            Checkbox("Create git worktree (-w)", id="ns-wt"),
            Input(placeholder="worktree name (optional)", id="ns-wtname", disabled=True),
            Label("enter create · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def on_mount(self) -> None:
        # Textual selects all text on first focus, which would replace the
        # prefilled folder prefix on the first keystroke. Move the cursor to
        # the end so typing appends rather than overwrites.
        inp = self.query_one("#ns-name", Input)
        inp.cursor_position = len(inp.value)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "ns-wt":
            self.query_one("#ns-wtname", Input).disabled = not event.value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self._result())

    def _result(self) -> dict:
        return {
            "name": self.query_one("#ns-name", Input).value.strip(),
            "cwd": self.query_one("#ns-cwd", Input).value.strip(),
            "worktree": self.query_one("#ns-wt", Checkbox).value,
            "worktree_name": self.query_one("#ns-wtname", Input).value.strip(),
        }


class ConfirmScreen(_PanelScreen):
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
            Label(self._prompt, classes="dialog-title"),
            Label("y yes · n / esc cancel", classes="dialog-hint"),
            id="panel",
        )


class QuitScreen(_PanelScreen):
    """Exit guard when sessions are still running. Returns 'shutdown',
    'background', or None (cancel)."""

    BINDINGS = [
        Binding("s", "dismiss('shutdown')", "Shut down all", show=False),
        Binding("b", "dismiss('background')", "Leave running", show=False),
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("c", "dismiss(None)", "Cancel", show=False),
    ]

    def __init__(self, names: list) -> None:
        super().__init__()
        self._names = names

    def compose(self) -> ComposeResult:
        listing = "\n".join(f"  • {n}" for n in self._names)
        yield Vertical(
            Label(f"{len(self._names)} Claude session(s) still running:\n{listing}",
                  classes="dialog-title"),
            Label("s shut down all · b leave running · esc/c cancel",
                  classes="dialog-hint"),
            id="panel",
        )


class NotesScreen(_PanelScreen):
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
            Label("Notes", classes="dialog-title"),
            self._ta,
            Label("ctrl-s save · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def action_save(self) -> None:
        self.dismiss(self._ta.text)


class RescanScreen(_PanelScreen):
    """Progress panel for the F5 rescan. A centered _PanelScreen (matching the
    other dialogs) holding an indeterminate-then-determinate bar and an X/N
    status line. The app owns the scan worker and feeds this via
    `update_progress`; it dismisses the screen when the scan finishes. No
    bindings — the scan can't be cancelled, so Esc is intentionally inert."""

    BINDINGS: list = []

    DEFAULT_CSS = """
    RescanScreen #scanbar { width: 60; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        self._status = Label("Scanning ~/.claude/projects/…", classes="dialog-hint")
        self._progress = ProgressBar(show_eta=False, id="scanbar")
        yield Vertical(
            Label("Rescanning", classes="dialog-title"),
            self._progress,
            self._status,
            id="panel",
        )

    def on_mount(self) -> None:
        # Indeterminate until the worker's pre-count lands the first total.
        self._progress.update(total=None, progress=0)

    def update_progress(self, done: int, total: int) -> None:
        self._status.update(f"Scanning ~/.claude/projects/…  {done}/{total}")
        self._progress.update(total=total or None, progress=done)


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
        Binding("c", "new_session", "New session"),
        Binding("d", "delete", "Delete"),
        Binding("e", "notes", "Edit notes"),
        Binding("u", "toggle_unnamed", "Toggle unnamed"),
        Binding("f5", "rescan", "Rescan", key_display="F5"),
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
        self._new_session_argv: list[str] | None = None
        self._new_session_cwd: str | None = None
        self._filter_needle: str = ""
        self._show_unnamed: bool = False
        # Flips after the first rescan so the empty-state can switch from
        # "press F5 to scan" to "no sessions found".
        self._scanned: bool = False
        # The live rescan progress modal while a scan runs, else None.
        self._rescan_screen: RescanScreen | None = None
        # Live-session state: sid -> "working"|"idle", refreshed by _poll_live.
        self._live_states: dict[str, str] = {}
        self._spinner_frame: int = 0
        # sid -> (TreeNode, child_depth) for in-place glyph updates without a
        # full rebuild. Rebuilt by _populate.
        self._row_nodes: dict[str, tuple] = {}
        # tmux-hosted interaction layer (spec §1). The launcher sets this env
        # var only when it wrapped the explorer in our dedicated tmux server.
        self._tmux_enabled: bool = os.environ.get("SESSION_EXPLORER_TMUX") == "1"
        # sids that are live windows in *our* tmux server (accessible via flip),
        # refreshed by _poll_live. Distinct from sessions live in other terminals.
        self._our_windows: set = set()
        # Split-pane docking (spec 2026-06-02-split-pane-explorer-claude):
        # our own tmux pane id (from $TMUX_PANE), and the sid currently docked
        # as the right pane (None when only the explorer is shown).
        self._self_pane: str | None = os.environ.get("TMUX_PANE")
        self._docked_sid: str | None = None

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # App-level bindings (especially priority ones like Enter→resume) must
        # not fire while a modal screen is up; otherwise the modal's own Enter
        # handler (e.g. Input submit) never runs.
        if action in ("resume", "rename", "move", "new_folder", "new_session", "delete", "notes", "preview", "close_preview", "filter", "toggle_unnamed", "rescan", "help", "expand_node", "collapse_node", "quit") and isinstance(self.screen, ModalScreen):
            return False
        # While the filter Input is focused, never let `q` quit the TUI — the
        # keystroke belongs in the filter text, not the global quit binding.
        if action == "quit" and getattr(self, "_filter", None) is not None and self._filter.has_focus:
            return False
        return True

    def on_click(self, event) -> None:
        # Double-click on a session row == pressing Enter on it. The single click
        # that precedes it has already moved the tree cursor to that row, so
        # action_resume acts on the right session. Scoped to the tree so clicks
        # elsewhere (preview pane, etc.) don't resume.
        if getattr(event, "chain", 1) == 2 and event.widget is self._tree:
            node = self._tree.cursor_node
            if node and node.data and "sid" in node.data:
                self.action_resume()

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
        yield Horizontal(
            Vertical(self._colheader, self._tree, self._empty, id="treepane"),
            self._preview,
        )
        self._filter = Input(placeholder="filter…", id="filter")
        self._filter.display = False
        self._filter.disabled = True  # also prevents focus while hidden
        yield self._filter
        yield Footer()

    def _claude_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self._index_path))

    def _live_path(self) -> str:
        from . import live as _live
        return os.environ.get("SESSION_EXPLORER_LIVE") or _live.default_path_for(self._index_path)

    def on_mount(self) -> None:
        self.title = "session-explorer"
        self._populate()
        # Belt-and-braces: ensure preview is hidden after first compose pass.
        self._preview.display = False
        # Opt-in retention: neutralising cleanupPeriodDays modifies the user's
        # settings.json, so ask once on first launch rather than have the hook
        # do it silently. Then (after the choice) pop the first-run help.
        from . import retention
        cd = self._claude_dir()
        if not retention.is_decided(cd):
            def after(ok: bool) -> None:
                if ok:
                    retention.enable(cd)
                else:
                    retention.decline(cd)
                self._maybe_open_help()
            self.push_screen(
                ConfirmScreen(
                    "Let session-explorer manage session retention?\n\n"
                    "This sets Claude Code's cleanupPeriodDays to 36500 (your "
                    "current value is backed up) so old sessions expire on the "
                    "plugin's schedule instead of Claude's. Restored on uninstall.\n"
                    "Choose No to leave Claude's native cleanup in charge."
                ),
                after,
            )
        else:
            self._maybe_open_help()
        # Live-session indicator: poll the registry, then animate working rows.
        self._poll_live()
        self.set_interval(LIVE_POLL_INTERVAL, self._poll_live)
        self.set_interval(SPINNER_INTERVAL, self._tick_spinner)
        self.set_interval(SNAPSHOT_POLL_INTERVAL, self._refresh_preview)

    def _tmux_decline_marker(self) -> str:
        return os.path.join(self._claude_dir(), ".session-explorer.tmux-declined")

    def _maybe_offer_tmux(self) -> None:
        # Only when NOT tmux-hosted (tmux was absent at /open) and not already
        # declined. Mirrors the retention one-time prompt.
        # Escape hatch for automated/non-interactive runs (CI, scripted launch):
        # SESSION_EXPLORER_TMUX_NO_OFFER=1 suppresses the install nag entirely.
        if os.environ.get("SESSION_EXPLORER_TMUX_NO_OFFER"):
            return
        if self._tmux_enabled or os.path.exists(self._tmux_decline_marker()):
            return
        if _tmux.available():       # present now but launch wasn't wrapped; skip
            return

        def after(ok: bool) -> None:
            if not ok:
                open(self._tmux_decline_marker(), "a").close()
            else:
                import platform
                from . import tmux_install
                cmd = tmux_install.install_command(platform.system()) \
                    or "see https://github.com/tmux/tmux/wiki/Installing"
                self.push_screen(ConfirmScreen(
                    f"Run this, then re-open the explorer:\n\n  {cmd}\n\n(y/esc)"))
        self.push_screen(ConfirmScreen(
            "Run multiple sessions and monitor them live inside the explorer?\n"
            "This needs tmux, which isn't installed. Set it up? (y = how, n = no)"),
            after)

    def _maybe_open_help(self) -> None:
        self._maybe_offer_tmux()
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
        self._row_nodes = {}
        data = _index.load(self._index_path)
        fs_data = _fs.load(_fs.default_path_for(self._index_path))
        tree = build_nested_tree(data, fs_data, include_unnamed=self._show_unnamed,
                                 live_ids=set(self._live_states))
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
        active = len(self._live_states)
        active_suffix = f" · ● {active} active" if active else ""
        if unnamed_hidden:
            self.sub_title = (f"{total} sessions across {len(tree)} projects · "
                              f"{unnamed_hidden} unnamed hidden (u){active_suffix}")
        else:
            self.sub_title = f"{total} sessions across {len(tree)} projects{active_suffix}"

        # Empty-state: when no session rows would render (after the filter),
        # show an actionable message in place of the tree instead of blank space.
        def visible_count(node):
            n = sum(1 for sid, s in node["_sessions"] if self._matches(sid, s))
            return n + sum(visible_count(c) for c in node["_folders"].values())

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
                    glyph = _glyph(self._live_states.get(sid), self._spinner_frame,
                                   self._ours_flag(sid))
                    leaf = parent.add_leaf(_row_label(sid, s, child_depth, glyph),
                                           data={"sid": sid, **s})
                    self._row_nodes[sid] = (leaf, child_depth)
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

    def _undock_current(self) -> None:
        """Break the docked claude pane back out to a background window so it
        keeps running off-screen. No-op when nothing is docked."""
        if not self._docked_sid:
            return
        pane = _tmux.docked_pane(self._self_pane)
        if pane:
            _tmux.undock(pane, self._docked_sid)
        self._docked_sid = None

    def _dock(self, sid: str, cwd: "str | None", label: "str | None",
              *, already_running: bool) -> None:
        """Make `sid` the docked right pane. If it is already docked, just
        refocus it; otherwise undock whatever is docked, (re)start the session
        as a background window when needed, and join it in."""
        if self._docked_sid == sid:
            pane = _tmux.docked_pane(self._self_pane)
            if pane:
                _tmux.select_pane(pane)            # refocus claude
            return
        self._undock_current()
        if not already_running:
            _tmux.start_window(sid, cwd, label)    # background window first
        _tmux.dock(sid)                            # join into the explorer
        self._docked_sid = sid

    def action_resume(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        sid = node.data["sid"]
        project_path = node.data.get("project_path")
        # Human label for the tmux status bar (the window name stays the sid).
        _, _display = split_path(node.data.get("name_cached"))
        label = _display or sid[:8]

        # No tmux → today's behaviour: exit and execvp claude (handled in run()).
        if not self._tmux_enabled:
            self._exit_to_resume(sid, project_path)
            return

        running = _tmux.session_windows()
        # Already docked, or a running background window → (re)dock it. _dock
        # refocuses if it is the current dock, else undocks-current and joins.
        if sid == self._docked_sid or sid in running:
            self._dock(sid, None, label, already_running=True)
            self._poll_live()
            return
        if sid in self._live_states:
            # Live in another terminal, not one of ours: never start a second
            # claude on the same transcript (spec §5).
            self.push_screen(ConfirmScreen(
                "This session is already running in another terminal.\n"
                "Showing its progress here; press space to peek. (y/esc)"))
            return
        # Stopped → start it as a background window and dock it beside the tree.
        if _dead_worktree_repo(project_path):
            def after(ok: bool) -> None:
                if ok:
                    cwd = _resolve_resume_cwd(project_path) or os.path.expanduser("~")
                    self._dock(sid, cwd, label, already_running=False)
                    self._poll_live()
            self.push_screen(ConfirmScreen(
                "This session is from a deleted git worktree.\n"
                "Resume anyway? This re-creates an empty directory:\n"
                f"{project_path}"), after)
        else:
            cwd = _resolve_resume_cwd(project_path) or os.path.expanduser("~")
            self._dock(sid, cwd, label, already_running=False)
            self._poll_live()

    def _exit_to_resume(self, sid: str, project_path: "str | None") -> None:
        def proceed() -> None:
            self._resume_target = sid
            self._resume_cwd = project_path
            self.exit()
        if _dead_worktree_repo(project_path):
            self.push_screen(ConfirmScreen(
                "This session is from a deleted git worktree.\n"
                "Resume anyway? This re-creates an empty directory:\n"
                f"{project_path}"), lambda ok: proceed() if ok else None)
        else:
            proceed()

    def _folder_node_target(self) -> "tuple[str, list[str]] | None":
        """If the cursor sits on a folder node (project + segments, no sid),
        return (project, segments). Project nodes (empty segments) and session
        leaves return None."""
        node = self._tree.cursor_node
        data = node.data if (node and node.data) else {}
        if "sid" in data:
            return None
        segments = data.get("segments") or []
        project = data.get("project")
        if project and segments:
            return (project, list(segments))
        return None

    def _folder_paths(self, project: str) -> "set[str]":
        """All folder paths in `project`: the store ∪ folders implied by indexed
        session names. Shared by session-move and folder-move candidate lists."""
        from . import folder_store as _fs
        paths = set(_fs.list_paths(_fs.default_path_for(self._index_path), project))
        data = _index.load(self._index_path)
        for s in data.get("sessions", {}).values():
            if s.get("project_label") != project:
                continue
            segs, _ = split_path(s.get("name_cached"))
            for i in range(1, len(segs) + 1):
                paths.add("/".join(segs[:i]))
        return paths

    def action_rename(self) -> None:
        node = self._tree.cursor_node
        data = node.data if (node and node.data) else {}

        # Folder node: rename its last segment in place, cascading to contents.
        if "sid" not in data:
            target = self._folder_node_target()
            if target is None:
                self.bell()
                return
            project, segments = target
            parent, leaf = segments[:-1], segments[-1]

            def after_folder(new_leaf: "str | None") -> None:
                typed = [seg for seg in (new_leaf or "").split("/") if seg.strip()]
                if not typed:
                    return
                new_segs = parent + typed
                if new_segs == segments:
                    return
                self._relabel_folder(project, segments, new_segs, verb="Rename")

            self.push_screen(RenameScreen(leaf, title="Rename folder"), after_folder)
            return

        sid = data["sid"]
        current = data.get("name_cached") or ""
        transcript = data.get("transcript_path")

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

    def _relabel_folder(self, project: str, old_segs: "list[str]",
                        new_segs: "list[str]", verb: str) -> None:
        """Cascade a folder rename/move: rewrite every session whose name lives
        under `old_segs` (appending a custom-title event to its JSONL), re-prefix
        the folder store subtree, and refresh the tree — all behind a single
        confirmation that names the affected session count."""
        from . import folder_store as _fs
        from .rename import append_custom_title
        from .tree_model import replace_folder_prefix

        old_path, new_path = "/".join(old_segs), "/".join(new_segs)
        data = _index.load(self._index_path)
        affected = []  # (sid, transcript_path, new_name)
        for sid, s in data.get("sessions", {}).items():
            if s.get("project_label") != project:
                continue
            new_name = replace_folder_prefix(s.get("name_cached"), old_segs, new_segs)
            if new_name is not None:
                affected.append((sid, s.get("transcript_path"), new_name))

        def do() -> None:
            for sid, transcript, new_name in affected:
                if transcript:
                    append_custom_title(transcript, session_id=sid, new_name=new_name)

            def _mut(d: dict) -> dict:
                for sid, _t, new_name in affected:
                    d["sessions"].setdefault(sid, {})["name_cached"] = new_name
                return d
            _index.mutate(self._index_path, _mut)
            # An empty folder lives only in the store; populated ones are implied
            # by their (now-rewritten) session names. rename_subtree handles both
            # by moving any store entries under the old path.
            _fs.rename_subtree(_fs.default_path_for(self._index_path),
                               project, old_path, new_path)
            self._populate()

        n = len(affected)
        plural = "session" if n == 1 else "sessions"
        msg = f"{verb} folder '{old_path}' → '{new_path}'?\nUpdates {n} {plural}."

        def after(ok: bool) -> None:
            if ok:
                do()

        self.push_screen(ConfirmScreen(msg), after)

    def action_move(self) -> None:
        from . import folder_store as _fs
        node = self._tree.cursor_node
        data = node.data if (node and node.data) else {}

        # Folder node: re-parent the whole subtree, keeping its leaf name.
        if "sid" not in data:
            target = self._folder_node_target()
            if target is None:
                self.bell(); return
            project, segments = target
            self._move_folder(project, segments)
            return

        sid = data["sid"]
        name = data.get("name_cached") or ""
        transcript = data.get("transcript_path")
        project = data.get("project_label")
        if not project:
            self.bell(); return
        segments, display = split_path(name)
        current_folder = "/".join(segments)

        fs_path = _fs.default_path_for(self._index_path)
        paths = self._folder_paths(project)

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

    def _move_folder(self, project: str, segments: "list[str]") -> None:
        """Re-parent folder `segments` (keeping its leaf) under a chosen path.
        Candidate parents exclude the folder itself and its descendants, so a
        folder can't be moved inside its own subtree."""
        leaf = segments[-1]
        old_path = "/".join(segments)
        current_parent = "/".join(segments[:-1])
        parents = sorted(
            p for p in self._folder_paths(project)
            if p != old_path and not p.startswith(old_path + "/")
        )

        def after(target: "str | None") -> None:
            if target is None:
                return
            parent_segs = [seg for seg in target.split("/") if seg.strip()]
            new_segs = parent_segs + [leaf]
            if new_segs == segments:  # same parent → no-op
                return
            # Guard against re-parenting into self/a descendant.
            if parent_segs[:len(segments)] == segments:
                self.notify(
                    f"Cannot move '{old_path}' into itself.", severity="warning",
                )
                return
            self._relabel_folder(project, segments, new_segs, verb="Move")

        self.push_screen(MoveScreen(project, parents, current_parent), after)

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

    def action_new_session(self) -> None:
        project, prefix = self._project_and_prefix_for_cursor()
        if not project:
            self.bell(); return
        sessions = _index.load(self._index_path).get("sessions", {})
        default_cwd = _derive_project_cwd(sessions, project) or os.path.expanduser("~")

        def after(result: "dict | None") -> None:
            if not result:
                return
            name = result["name"].strip()
            if not name:
                return
            cwd = result["cwd"].strip() or os.path.expanduser("~")
            # worktree tri-state: None (off), "" (bare -w), or a name (-w name).
            worktree = (result["worktree_name"] or "") if result["worktree"] else None
            sid = _new_sid()

            # Seed the chosen name now: claude writes no transcript (and thus no
            # custom-title) until the first turn, so without this the session
            # shows under (unnamed) until then. claude -n persists the identical
            # title later, so there's no divergence.
            _index.seed_new_session(self._index_path, sid, name, cwd)

            # No tmux → exit and execvp claude (handled in run()).
            if not self._tmux_enabled:
                self._new_session_argv = _new_session_argv(sid, name, worktree)
                self._new_session_cwd = cwd
                self.exit()
                return

            _, display = split_path(name)
            label = display or sid[:8]
            self._do_new_session(sid, cwd, name, worktree, label)

        self.push_screen(NewSessionScreen(project, prefix, default_cwd), after)

    def _do_new_session(self, sid: str, cwd: str, name: str,
                        worktree: "str | None", label: "str | None") -> None:
        """Start a fresh claude session as a background window and dock it as
        the right pane, swapping out whatever was docked. Mirrors _dock but
        uses start_new_session_window (a new session, not a resume)."""
        self._undock_current()
        _tmux.start_new_session_window(sid, cwd, name, worktree, label)
        _tmux.dock(sid)
        self._docked_sid = sid
        self._populate()           # show the newly-named session immediately
        self._poll_live()

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

    def action_quit(self) -> None:
        if not self._tmux_enabled:
            self.exit()
            return
        running = _tmux.session_windows()
        if not running:
            self.exit()
            return

        def after(choice) -> None:
            if choice == "shutdown":
                _tmux.kill_server()
                self.exit()
            elif choice == "background":
                flag = os.path.join(self._claude_dir(),
                                    ".session-explorer.tmux-persist")
                _tmux.set_persist_flag(flag)   # Option C: this detach is deliberate
                _tmux.detach_client()
            # None → cancel: stay in the explorer.
        self.push_screen(QuitScreen(running), after)

    def action_toggle_unnamed(self) -> None:
        self._show_unnamed = not self._show_unnamed
        self._populate()

    def _poll_live(self) -> None:
        """Refresh live-session state from the registry (called on a timer).

        Liveness changes update glyphs (or repopulate on a visibility change);
        then, regardless, live sessions' metadata is refreshed off-thread so
        first_prompt / msgs / tokens fill in and tick as the agent works.
        """
        from . import live as _live
        try:
            new_states = _live.poll(self._live_path())
        except Exception:
            return  # never let the indicator break the UI
        # Which live sessions are *our* tmux windows (accessible) vs elsewhere.
        new_ours: set = set()
        if self._tmux_enabled:
            try:
                new_ours = set(_tmux.session_windows())
            except Exception:
                new_ours = self._our_windows  # keep last good on a tmux hiccup
        states_changed = new_states != self._live_states
        ours_changed = new_ours != self._our_windows
        if states_changed or ours_changed:
            old = self._live_states
            self._live_states = new_states
            self._our_windows = new_ours
            if states_changed and self._visibility_changed(old, new_states):
                sid = self._selected_sid()
                self._populate()
                self._restore_cursor_to_sid(sid)
            else:
                self._relabel_live_rows()
        # Always refresh live metadata (stats change even when liveness doesn't).
        if self._live_states:
            self._refresh_live_metadata()

    def _ours_flag(self, sid: str) -> "bool | None":
        """For _glyph: None when not tmux-hosted (no accessibility distinction),
        else True if `sid` is one of our tmux windows, False if live elsewhere."""
        if not self._tmux_enabled:
            return None
        return sid in self._our_windows

    def _selected_sid(self) -> "str | None":
        """The sid of the currently-selected row, or None if on a non-session node."""
        node = self._tree.cursor_node
        data = node.data if node and node.data else {}
        return data.get("sid")

    def _restore_cursor_to_sid(self, sid: "str | None") -> None:
        """Move the tree cursor back to a session's row after a rebuild.

        Safe no-op when sid is None (cursor was on a project/folder node) or
        the session no longer has a row (it was hidden/deleted)."""
        if not sid:
            return
        node = self._row_nodes.get(sid)
        if node is None:
            return
        leaf = node[0]
        try:
            # A fresh _populate() clears the tree's line cache, leaving every
            # TreeNode._line at -1 until the next layout pass. move_cursor reads
            # leaf._line, so force the line cache to rebuild first; otherwise it
            # would set cursor_line to -1 (top/reset). Accessing _tree_lines
            # triggers _build(), which assigns _line to every node.
            self._tree._tree_lines  # noqa: B018  (property access rebuilds the cache)
            self._tree.move_cursor(leaf)
        except Exception:
            pass

    def _visibility_changed(self, old: dict, new: dict) -> bool:
        """True if any session whose membership depends on liveness flipped.

        Only unnamed sessions are conditionally visible, and only while not
        showing all unnamed. A named session is always present regardless of
        live state, so its appearance never forces a repopulate."""
        if self._show_unnamed:
            return False
        data = _index.load(self._index_path)
        sessions = data.get("sessions", {})
        flipped = set(old) ^ set(new)  # sids that entered or left the live set
        for sid in flipped:
            s = sessions.get(sid)
            if s is not None and not s.get("name_cached"):
                return True  # an unnamed session entered/left -> membership change
        return False

    def _relabel_live_rows(self) -> None:
        """Rewrite glyphs for all rows currently tracked, without rebuilding."""
        for sid, (leaf, depth) in self._row_nodes.items():
            data = leaf.data or {}
            glyph = _glyph(self._live_states.get(sid), self._spinner_frame,
                           self._ours_flag(sid))
            leaf.set_label(_row_label(sid, data, depth, glyph))

    def _do_live_metadata_refresh(self) -> None:
        """Re-index each live session from its transcript so first_prompt / msgs /
        tokens populate and tick as the agent works. Plain (no threading) so it's
        unit-testable; the worker wraps it. Only touches live sessions; swallows
        per-session errors (a transcript mid-write must never break the UI)."""
        from . import live as _live
        try:
            live = _live.load(self._live_path()).get("sessions", {})
            indexed = _index.load(self._index_path).get("sessions", {})
        except Exception:
            return
        for sid in list(self._live_states):
            entry = live.get(sid, {})
            ie = indexed.get(sid, {})
            tp = entry.get("transcript_path") or ie.get("transcript_path")
            cwd = entry.get("cwd") or ie.get("project_path")
            if not tp or not cwd:
                continue  # can't refresh without transcript+cwd; F5 remains the catch-all
            try:
                # skip_git: the branch is static for a live session — avoid forking git every 2s.
                _index.record_session(self._index_path, sid, tp, cwd, skip_git=True)
            except Exception:
                continue

    @work(thread=True, exclusive=True, group="live-meta")
    def _refresh_live_metadata(self) -> None:
        """Off-thread wrapper: refresh live metadata on disk, then update rows on
        the UI thread. `exclusive` so a slow refresh can't stack across polls."""
        self._do_live_metadata_refresh()
        self.call_from_thread(self._apply_live_metadata)

    def _apply_live_metadata(self) -> None:
        """Reload the index and refresh only the live rows in place (update each
        live leaf's stored `data` + relabel). No full repopulate, so cursor /
        scroll / expansion are preserved."""
        try:
            data = _index.load(self._index_path).get("sessions", {})
        except Exception:
            return
        for sid, (leaf, _depth) in self._row_nodes.items():
            if sid in self._live_states and sid in data:
                leaf.data = {"sid": sid, **data[sid]}
        self._relabel_live_rows()
        self._refresh_preview()

    def _tick_spinner(self) -> None:
        """Advance the spinner frame and relabel only the working rows."""
        if not any(st == "working" for st in self._live_states.values()):
            return  # nothing animating -> cheap no-op
        self._spinner_frame += 1
        for sid, state in self._live_states.items():
            node = self._row_nodes.get(sid)
            if node is None or state != "working":
                continue
            leaf, depth = node
            leaf.set_label(_row_label(sid, leaf.data or {}, depth,
                                      _glyph(state, self._spinner_frame,
                                             self._ours_flag(sid))))

    def action_rescan(self) -> None:
        # reindex shells out to `git` per session, so it runs in a worker thread
        # to keep the UI responsive on large histories. Progress shows in a
        # modal panel (consistent with the other dialogs) overlaid on the dimmed
        # tree, rather than blanking the tree pane. The worker dismisses it.
        self.sub_title = "scanning ~/.claude/projects/…"
        self._rescan_screen = RescanScreen()
        self.push_screen(self._rescan_screen)
        self._rescan_worker()

    def _on_progress(self, done: int, total: int) -> None:
        """Feed the rescan modal. Called on the main thread (marshalled from the
        worker via call_from_thread)."""
        if self._rescan_screen is not None:
            self._rescan_screen.update_progress(done, total)

    def _finish_rescan(self) -> None:
        """Dismiss the modal and repaint the tree. Main thread."""
        if self._rescan_screen is not None:
            self._rescan_screen.dismiss()
            self._rescan_screen = None
        self._populate()

    @work(thread=True, exclusive=True)
    def _rescan_worker(self) -> None:
        def progress(done: int, total: int) -> None:
            self.call_from_thread(self._on_progress, done, total)
        try:
            _index.reindex(self._index_path, projects_root=self._projects_root,
                           progress=progress)
        finally:
            self._scanned = True
            self.call_from_thread(self._finish_rescan)

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
            self._preview.update("[dim]Select a session to preview.[/]")
            return
        sid = data["sid"]
        if self._tmux_enabled and sid in self._live_states:
            self._preview.update(self._render_live_preview(data, sid))
            return
        self._preview.update(_preview_text(data))

    def _render_live_preview(self, data: dict, sid: str):
        """Full metadata block (same as a stopped session) followed by a live
        section: the captured tmux frame for our windows, or a transcript tail."""
        from rich.console import Group
        from rich.text import Text
        text, is_ansi = _snapshot.snapshot(
            sid=sid,
            transcript_path=data.get("transcript_path", ""),
            tmux_window_names=_tmux.session_windows(),
            capture_fn=_tmux.capture_pane)
        meta = Text.from_markup(_preview_text(data))
        state = self._live_states.get(sid, "")
        divider = Text.from_markup(f"\n[dim]──[/] [green]live[/] [dim]({state}) ──────[/]\n")
        # Keep the metadata block visible: cap the live section to its last lines
        # (a full-screen capture-pane can be 40+ rows). Splitting on \n is safe
        # for ANSI — escape sequences never span lines.
        lines = text.rstrip("\n").splitlines()
        if len(lines) > LIVE_PREVIEW_LINES:
            lines = lines[-LIVE_PREVIEW_LINES:]
        clipped = "\n".join(lines)
        body = Text.from_ansi(clipped) if is_ansi else Text(clipped)
        return Group(meta, divider, body)

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


def _new_sid() -> str:
    """Fresh session UUID for a new session. Isolated so tests can stub it."""
    return str(uuid.uuid4())


def _derive_project_cwd(sessions: dict, project_label: str) -> "str | None":
    """Launch cwd for a new session in `project_label`: the project_path of its
    most-recently-active session, with any git-worktree suffix stripped back to
    the repo root so `claude -w` branches from the real repository. None when the
    project has no session with a usable path."""
    best = None
    best_key = ""
    for s in sessions.values():
        if s.get("project_label") != project_label:
            continue
        path = s.get("project_path")
        if not path:
            continue
        key = s.get("last_active_at") or ""
        # >= so the last session encountered wins on equal/missing timestamps;
        # any recent path in the project is an acceptable cwd, so ties are fine.
        if best is None or key >= best_key:
            best, best_key = path, key
    if not best:
        return None
    if _WORKTREE_MARKER in best:
        best = best.split(_WORKTREE_MARKER, 1)[0]
    return best


def _new_session_argv(sid: str, name: str, worktree: "str | None" = None) -> list[str]:
    """argv for `os.execvp` to start a fresh session without tmux. A list (no
    shell), so the name needs no quoting. `worktree`: None → no `-w`; "" → bare
    `-w`; otherwise `-w <name>`."""
    argv = ["claude", "--session-id", sid, "-n", name]
    if worktree is not None:
        argv.append("-w")
        if worktree:
            argv.append(worktree)
    return argv


def run() -> int:
    app = SessionExplorerApp()
    app.run()
    new_argv = getattr(app, "_new_session_argv", None)
    if new_argv:
        # chdir into the chosen project dir so claude (and `-w`) operate in the
        # right repo, then hand the window over to a fresh claude session.
        cwd = getattr(app, "_new_session_cwd", None)
        # Fail open: if the chosen dir no longer exists, start from cwd as-is
        # rather than aborting the launch.
        if cwd and os.path.isdir(cwd):
            os.chdir(cwd)
        os.execvp("claude", new_argv)
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
