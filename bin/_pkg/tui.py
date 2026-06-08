"""Textual TUI for session-explorer.

Loaded lazily — importing this module triggers the Textual import, which is
several MB of code. Only happens when the user actually runs `tui`/`launch`.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
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
from . import usage as _usage
from . import worktree
from .format import fmt_age, fmt_pct, fmt_tokens
from .tree_model import build_nested_tree, split_path, session_root


LAUNCH_CHECK_DELAY = 1.5  # seconds after a new-session launch to verify it stuck


def _launch_err_path(sid: str) -> str:
    """Per-sid temp file capturing a new session's startup stderr."""
    return os.path.join(tempfile.gettempdir(), f"session-explorer-launch-{sid}.err")


def _summarize_launch_error(raw: str) -> str:
    """One-line summary of captured startup stderr for a toast/preview. Prefers
    the line that names the failure ('Error creating worktree…'); else the first
    non-empty line. Truncated. Blank in → blank out."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    chosen = next((ln for ln in lines if "Error creating worktree" in ln), None)
    chosen = chosen or next((ln for ln in lines if "worktree" in ln.lower()
                             or ln.lower().startswith("error")), None)
    chosen = chosen or lines[0]
    return chosen[:200]


def _log_line(msg: str) -> None:
    """Best-effort append to ~/.claude/session-explorer.log. Never raises."""
    try:
        from datetime import datetime, timezone
        log = os.path.expanduser("~/.claude/session-explorer.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")
    except Exception:
        pass


def worktree_slug(name: str) -> str:
    """Slug a session name into a git-worktree name (spec §9).

    Uses the display portion after the last '/'; lowercases; turns spaces,
    underscores and dots into '-'; drops anything outside [a-z0-9-]; collapses
    and trims dashes. Blank in → blank out (so a temporary unnamed session
    leaves the worktree name empty, i.e. a bare `-w`).
    """
    import re
    display = name.rsplit("/", 1)[-1].strip().lower()
    display = re.sub(r"[ _.]+", "-", display)
    display = re.sub(r"[^a-z0-9-]+", "", display)
    display = re.sub(r"-{2,}", "-", display).strip("-")
    return display


# Spec §7 template catalog. `defaults` is merged into a resource dict; the editor
# overlays user edits. Kept as pure data so it is unit-tested without Textual.
QUEUE_TEMPLATES = [
    {"key": "bind-mounted-stack", "title": "Bind-mounted stack, well-known ports",
     "defaults": {"kind": "root-dir", "acquire": "sync", "release": "none",
                  "run_in": "root",
                  "guard": [{"exe": "docker", "sub": ["compose", "up"]},
                            {"exe": "docker", "sub": ["compose", "run"]}],
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env", "/.env.*"]},
                  "wait_for": {"type": "url", "target": "http://localhost:8080",
                               "timeout": 120}}},
    {"key": "browser-e2e", "title": "Browser e2e vs fixed-URL app",
     "defaults": {"kind": "root-dir", "acquire": "sync", "release": "none",
                  "run_in": "root",
                  "guard": [{"exe": "playwright", "sub": ["test"]},
                            {"exe": "cypress", "sub": ["run"]}],
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env", "/.env.*"]},
                  "wait_for": {"type": "url", "target": "http://localhost:3000",
                               "timeout": 120}}},
    {"key": "ios-sim", "title": "iOS simulator / xcodebuild",
     "defaults": {"kind": "device", "acquire": "none", "release": "none",
                  "run_in": "worktree",
                  "guard": [{"exe": "xcodebuild", "sub": ["test"]}]}},
    # acquire defaults to "none" (valid as-saved); filling the editor's acquire
    # field promotes it to "command" with the user's DB-reset shell. Shipping
    # acquire="command" with an empty command_acquire would fail queue_config
    # validation on save (queue_config.py:121-122).
    {"key": "shared-db", "title": "Single shared database",
     "defaults": {"kind": "port", "acquire": "none", "release": "none",
                  "run_in": "worktree",
                  "guard": [{"exe": "npm", "sub": ["run", "migrate"]}]}},
    {"key": "root-env", "title": "Root-only credentials / .env",
     "defaults": {"kind": "root-dir", "acquire": "sync", "release": "none",
                  "run_in": "root", "guard": [],
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env", "/.env.*"]}}},
    {"key": "device-seat", "title": "Single device / HIL / license seat",
     "defaults": {"kind": "name", "acquire": "none", "release": "none",
                  "run_in": "worktree", "guard": []}},
    {"key": "custom", "title": "Custom / blank",
     "defaults": {"kind": "name", "acquire": "none", "release": "none",
                  "run_in": "worktree", "guard": []}},
]


def template_resource(key: str, *, path: str) -> dict:
    """Build a fresh resource dict from a template key. Pure."""
    import copy
    tpl = next((t for t in QUEUE_TEMPLATES if t["key"] == key), None)
    if tpl is None:
        tpl = next(t for t in QUEUE_TEMPLATES if t["key"] == "custom")
    res = copy.deepcopy(tpl["defaults"])
    if res.get("kind") in ("root-dir", "path") and path:
        res["path"] = path
    return res


# --- Editor form <-> resource-dict conversions (pure, unit-tested) ---

def parse_guard_lines(text: str) -> list:
    """Each non-empty line 'exe sub1 sub2' -> {'exe': exe, 'sub': [sub1, ...]}.
    Blank/whitespace-only lines are dropped. So 'docker compose up' becomes
    {'exe': 'docker', 'sub': ['compose', 'up']}. Pure."""
    rules = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        rules.append({"exe": parts[0], "sub": parts[1:]})
    return rules


def format_guard_lines(rules: list) -> str:
    """Inverse of parse_guard_lines, to pre-fill the guard editor."""
    return "\n".join(" ".join([r.get("exe", "")] + list(r.get("sub", [])))
                     for r in (rules or []))


def parse_path_lines(text: str) -> list:
    """One path per line; blanks dropped, whitespace trimmed (for protect)."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def parse_wait_for(text: str, timeout_text: str) -> "dict | None":
    """'<url|port|command> <target>' + a timeout string -> a wait_for spec, or
    None when empty/invalid (so a cleared field removes the spec). Pure."""
    parts = text.split(None, 1)
    if len(parts) < 2:
        return None
    wtype, target = parts[0], parts[1].strip()
    if wtype not in ("url", "port", "command") or not target:
        return None
    try:
        timeout = float(timeout_text.strip()) if timeout_text.strip() else 60.0
    except ValueError:
        timeout = 60.0
    return {"type": wtype, "target": target, "timeout": timeout}


def format_wait_for(spec: dict) -> tuple:
    """Inverse of parse_wait_for: ('type target', 'timeout') for pre-filling."""
    if not spec:
        return ("", "")
    line = f"{spec.get('type', '')} {spec.get('target', '')}".strip()
    t = spec.get("timeout")
    return (line, "" if t is None else str(t))


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
WT_W = 4  # display width of the worktree-indicator column (after the name field)
SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
IDLE_GLYPH = "○"   # idle, peek-only (running in a separate terminal)
OURS_GLYPH = "●"   # idle, accessible (running in our tmux — Enter to jump in)
WT_GLYPH = "⎇"  # marks a git-worktree session (blank = normal "root" checkout)
SPINNER_INTERVAL = 0.2   # seconds between spinner frames
LIVE_POLL_INTERVAL = 2.0  # seconds between registry polls
USAGE_POLL_INTERVAL = 300.0  # seconds between usage-bar refreshes (5 min)
SNAPSHOT_POLL_INTERVAL = 1.0  # seconds between preview snapshot refreshes
LIVE_PREVIEW_LINES = 24  # max lines of live snapshot shown below the metadata
DOCK_SYNC_DEBOUNCE = 0.2  # seconds the tree cursor must settle before the
                          # docked pane follows it (coalesces hold-scroll churn)


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


def _wt_glyph(state: "str | None") -> str:
    """Inner markup for the worktree column: dark-green glyph for a live
    worktree, dark red for a deleted one, a single space for a root checkout.
    Always one display cell wide after markup is stripped. Pure for unit testing."""
    if state == "live":
        return f"[dark_green]{WT_GLYPH}[/]"
    if state == "dead":
        return f"[dark_red]{WT_GLYPH}[/]"
    return " "


def _wt_cell(state: "str | None") -> str:
    """A WT_W-wide column cell: one space of gap, the worktree glyph, then
    padding out to WT_W. Inserted between the name field and the stat block."""
    return " " + _wt_glyph(state) + " " * (WT_W - 2)


def _stat_suffix(age: str, tok: str, pct: str, msgs: str, msgs_unit: str, prompt: str) -> str:
    """Render the stat block after the name field. Used for both data rows and
    the header line so the columns line up by construction."""
    return f" {age:>4}  {tok:>6} {pct:>5}  {msgs:>4} {msgs_unit}   {prompt}"


def _row_label(sid: str, s: dict, depth: int, glyph: str = "  ", wt_state: "str | None" = None) -> str:
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
    return glyph + f"{display:<{name_w}}" + _wt_cell(wt_state) + _stat_suffix(age, tokens, pct, msgs, "msgs", prompt)


def _column_header() -> str:
    """Header line whose labels sit above the stat columns. Pads to a depth-2
    leaf's absolute stat offset (GLYPH_W glyph cells + 2 levels of guide ×
    GUIDE_DEPTH + NAME_W)."""
    name_region = NAME_W + 2 * GUIDE_DEPTH
    return " " * GLYPH_W + f"{'NAME':<{name_region}}" + " " * WT_W + _stat_suffix("AGE", "~TOK", "CTX", "MSGS", "    ", "FIRST PROMPT")


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
        field("Project", s.get("project_display") or s.get("project_label") or "(unknown)"),
        field("Path", s.get("project_path") or "(unknown)"),
    ]
    if s.get("worktree_size"):
        lines.append(field("Worktree", f"{s['worktree_size']} on disk"))
    lines += [
        field("Folder", "/".join(segments) or "(none)"),
        field("Branch", s.get("branch") or "(none)"),
        field("Active", fmt_age(s.get("last_active_at"))),
        field("Created", (s.get("created_at") or "")[:10] or "—"),
        field("Messages", str(s.get("message_count", 0))),
        field("Context", context),
        field("Model", s.get("model") or "(unknown)"),
        field("Session", sid or "—"),
    ]
    if s.get("last_launch_error"):
        lines += ["", "[b]Launch[/]", f"failed: {s['last_launch_error']}"]
    lines += [
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
    """True if any session in `project` (a repo root) lives under the folder
    `folder_segments` (its name's folder path has them as a prefix). Pure, so it
    can be unit-tested. Unnamed sessions have no folder and never count."""
    n = len(folder_segments)
    for s in index_data.get("sessions", {}).values():
        if session_root(s) != project:
            continue
        name = s.get("name_cached")
        if not name:
            continue
        segs, _ = split_path(name)
        if segs[:n] == folder_segments:
            return True
    return False


def _empty_state_text(total_indexed: int, visible: int, unnamed_hidden: int,
                      filter_active: bool, scanned: bool,
                      view_mode: int = 0) -> "str | None":
    """Message for the tree pane when no rows are visible, else None.

    Pure so it can be unit-tested. Branch order is deliberate: an active filter
    explains itself first (the user is mid-search), then the active-only mode,
    then hidden-unnamed, then the empty-index prompts — split by whether a
    rescan has already run, so a fruitless scan doesn't keep telling the user
    to "press F5"."""
    if visible > 0:
        return None
    if filter_active:
        return "No sessions match the current filter.\nPress Esc to clear it."
    if view_mode == 1:
        return "No active sessions right now.\nPress Tab to show all sessions."
    if unnamed_hidden > 0:
        return (f"{unnamed_hidden} unnamed session(s) hidden.\n"
                "Press Tab to cycle views, then r to name one.")
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
        "Only named (renamed) sessions show by default. Press [b]Tab[/] to cycle",
        "the view: named+active → active only → all (incl. unnamed) → back.",
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
        "[b]Worktrees[/]",
        "A [dark_green]⎇[/] after the name marks a session running in a git",
        "worktree; it turns [dark_red]⎇[/] if that worktree directory was deleted.",
        "Plain (no glyph) means a normal checkout. Updated on rescan ([b]F5[/]).",
        "Press [b]w[/] to reclaim a stopped worktree's directory — resume rebuilds it.",
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
        "  • [b]x[/] with sessions running asks whether to shut them all down or",
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
        key("r / F2", "Rename a session (re-files it) or a folder (renames its subtree)"),
        key("m", "Move a session, or re-parent a whole folder, to another path"),
        key("n", "New folder under the current project/folder"),
        key("c", "New session (blank name → temporary unnamed; optional worktree)"),
        key("d", "Delete the selected session, or an empty folder (confirms)"),
        key("w", "Remove the selected worktree's directory (branch + transcript kept)"),
        key("e", "Edit notes (Ctrl+S to save)"),
        key("Tab", "Cycle view: named+active → active only → all"),
        key("z", "Collapse the tree to project roots (toggle)"),
        key("F5", "Rescan ~/.claude/projects/ — import pre-existing sessions"),
        key("/", "Live filter across name, notes, first prompt"),
        key("h", "Show this help"),
        key("Esc", "Close the preview, filter, or this help"),
        key("q", "Toggle the Queues pane (shared-resource leases)"),
        key("x", "Exit"),
        "",
        "[dim]Esc, q, h, or Space closes this help.[/]",
        "",
        f"[b]session-explorer v{__version__}[/]  ·  Made by Johan Lindahl  <johan.lindahl@snojken.com>",
        '[link="https://github.com/johan-lindahl/session-explorer"]https://github.com/johan-lindahl/session-explorer[/link]',
    ])


def _render_queue_rows(rows: list) -> str:
    """Render queue_view.snapshot() rows as pane markup (spec §9 mockup)."""
    lines = ["[b]Queues[/]"]
    for r in rows:
        name = f"{_basename(r['project'])} / {r['resource']}"
        if r["live_root_block"]:
            who = r["live_root_block"].get("name", "?")
            lines.append(f"  {name:<26}⛔ held by live session ‹{who}›")
            continue
        if r["holder"]:
            h = r["holder"]
            lines.append(f"  {name:<26}● holder: {h['label']} ({h['elapsed']})")
            if r["waiting"]:
                waits = " · ".join(f"{w['label']} ({w['pos']})" for w in r["waiting"])
                lines.append(f"  {'':<26}waiting: {waits}")
        else:
            lines.append(f"  {name:<26}○ free")
    return "\n".join(lines)


def _basename(path: str) -> str:
    return os.path.basename(path.rstrip("/")) or path


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

    def __init__(self, project: str, name_prefix: str = "", cwd: str = "",
                 *, root_is_shared: bool = False) -> None:
        super().__init__()
        self._project = project
        self._name_prefix = name_prefix
        self._cwd = cwd
        self._root_is_shared = root_is_shared
        self._wt_manual = False  # set once the user edits the worktree field

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New session in '{self._project}' (use / to nest)",
                  classes="dialog-title"),
            Input(value=self._name_prefix, placeholder="session name", id="ns-name"),
            Input(value=self._cwd, placeholder="working directory", id="ns-cwd"),
            Checkbox("Create git worktree (-w)", value=self._root_is_shared, id="ns-wt"),
            Input(value=(worktree_slug(self._name_prefix) if self._root_is_shared else ""),
                  placeholder="worktree name (optional)", id="ns-wtname",
                  disabled=not self._root_is_shared),
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
            wt = self.query_one("#ns-wtname", Input)
            wt.disabled = not event.value
            if event.value and not self._wt_manual and not wt.value:
                wt.value = worktree_slug(self.query_one("#ns-name", Input).value)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "ns-name" and not self._wt_manual:
            if self.query_one("#ns-wt", Checkbox).value:
                # Programmatic auto-fill: happens while #ns-name has focus, so
                # the resulting #ns-wtname Changed below sees an unfocused field
                # and is NOT treated as a manual edit.
                self.query_one("#ns-wtname", Input).value = worktree_slug(event.value)
        elif event.input.id == "ns-wtname":
            # A *user* edit requires #ns-wtname to be focused (the user is typing
            # in it); our auto-fill writes it while #ns-name is focused. Using
            # focus — not value comparison — correctly handles a user retyping the
            # SAME slug, which a value check (value == slug(name)) would miss.
            if event.input.has_focus:
                self._wt_manual = True

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


class ResourceListScreen(_PanelScreen):
    """Per-project shared-resource list (spec §6). a add · e edit · Del remove ·
    ? help · esc close. The destructive editor + test panel live in
    ResourceEditorScreen."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close"),
        Binding("a", "add", "Add", show=False),
        Binding("e", "edit", "Edit", show=False),
        Binding("delete", "remove", "Remove", show=False),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def __init__(self, *, project_root: str, project_id: str, config_path: str) -> None:
        super().__init__()
        self._project_root = project_root
        self._project_id = project_id
        self._config_path = config_path

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"Shared resources — {_basename(self._project_root)}",
                  classes="dialog-title"),
            OptionList(id="reslist"),
            Label("a add · e edit · Del remove · ? help · esc close",
                  classes="dialog-hint"),
            id="panel",
        )

    def on_mount(self) -> None:
        self._reload()

    def _reload(self) -> None:
        from . import queue_config as _qc
        ol = self.query_one("#reslist", OptionList)
        ol.clear_options()
        resources = _qc.list_resources(self._config_path, self._project_id)
        if not resources:
            ol.add_option(Option("(no resources yet — press a to add)", id=None))
            return
        for rid, res in sorted(resources.items()):
            label = (f"{rid:<14} {res.get('kind',''):<9} "
                     f"acquire:{res.get('acquire','')}  run_in:{res.get('run_in','')}")
            ol.add_option(Option(label, id=rid))

    def action_add(self) -> None:
        def after(saved):
            if saved:
                self._reload()
        self.app.push_screen(
            ResourceEditorScreen(project_root=self._project_root,
                                 project_id=self._project_id,
                                 config_path=self._config_path,
                                 resource_id=None),
            after)

    def action_edit(self) -> None:
        rid = self._selected_rid()
        if not rid:
            return
        def after(saved):
            if saved:
                self._reload()
        self.app.push_screen(
            ResourceEditorScreen(project_root=self._project_root,
                                 project_id=self._project_id,
                                 config_path=self._config_path,
                                 resource_id=rid),
            after)

    def action_remove(self) -> None:
        from . import queue_config as _qc
        rid = self._selected_rid()
        if not rid:
            return
        def after(ok: bool) -> None:
            if ok:
                _qc.remove_resource(self._config_path, self._project_id, rid)
                self._reload()
        self.app.push_screen(
            ConfirmScreen(f"Remove shared resource '{rid}'? (queue config only; "
                          "no files are touched)"), after)

    def action_help(self) -> None:
        self.app.push_screen(QueueHelpScreen())

    def _selected_rid(self) -> "str | None":
        ol = self.query_one("#reslist", OptionList)
        idx = ol.highlighted
        if idx is None:
            return None
        opt = ol.get_option_at_index(idx)
        return opt.id


class ResourceEditorScreen(_PanelScreen):
    """Template-first resource editor that reflows per kind (spec §6). Saves via
    queue_config.add_resource (which enforces the §2 invariants). Returns True on
    a successful save, False on cancel. The destructive test panel is mounted by
    the test-panel task."""

    BINDINGS = [
        Binding("escape", "dismiss(False)", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+t", "test_guard", "Test guard"),
        Binding("ctrl+r", "dry_run", "Dry-run"),
        Binding("ctrl+h", "health", "Health"),
    ]

    def __init__(self, *, project_root, project_id, config_path, resource_id) -> None:
        super().__init__()
        self._project_root = project_root
        self._project_id = project_id
        self._config_path = config_path
        self._resource_id = resource_id          # None == add
        self._template_key = "custom"
        from . import project_id as _pid
        # A root-dir resource's path is the repo's MAIN working tree, never the
        # selected tree node — which can be an arbitrary `git worktree add`
        # shown as its own project (spec §1). Derive it via the git-common-dir
        # helper; fall back to the node path if git can't resolve it.
        self._root_path = _pid.main_root(project_root) or project_root
        self._existing = None
        self._kind = "name"

    def compose(self) -> ComposeResult:
        from . import queue_config as _qc
        existing = (_qc.get_resource(self._config_path, self._project_id,
                                     self._resource_id) if self._resource_id else None)
        self._existing = existing
        if existing:
            self._template_key = "custom"   # edit: seed fields from the stored shape
        base = existing or template_resource("custom", path=self._root_path)
        self._kind = base.get("kind", "name")
        title = ("Edit resource" if existing else "Add resource") + \
                f" — {_basename(self._project_root)}"
        opts = [Option(t["title"], id=t["key"]) for t in QUEUE_TEMPLATES]
        yield Vertical(
            Label(title, classes="dialog-title"),
            Label("Template", classes="dialog-hint"),
            OptionList(*opts, id="res-template"),
            Input(value=self._resource_id or "", placeholder="resource id (e.g. ios-sim)",
                  id="res-id", disabled=bool(self._resource_id)),
            Label(f"kind: {self._kind}", id="res-kind", classes="dialog-hint"),
            Input(value=base.get("path", self._root_path),
                  placeholder="path (path-kind editable; root-dir is auto-derived)",
                  id="res-path"),
            Label("Guard — one 'exe sub…' rule per line (empty = unguarded)",
                  classes="dialog-hint"),
            TextArea(format_guard_lines(base.get("guard", [])), id="res-guard"),
            Label("Protect — root-only paths to keep, one per line (root-dir/path)",
                  id="res-protect-label", classes="dialog-hint"),
            TextArea("\n".join(base.get("sync", {}).get("protect", [])), id="res-protect"),
            Input(value=base.get("command_acquire", ""),
                  placeholder="acquire command (when acquire=command)", id="res-acq"),
            Input(value=base.get("command_release", ""),
                  placeholder="release command (optional)", id="res-rel"),
            Checkbox("release required (fail the run if release fails)",
                     value=bool(base.get("release_required")), id="res-req"),
            Input(value=base.get("health", ""),
                  placeholder="health check command (optional)", id="res-health"),
            Input(value=format_wait_for(base.get("wait_for"))[0],
                  placeholder="readiness: '<url|port|command> <target>' (optional)",
                  id="res-wait"),
            Input(value=format_wait_for(base.get("wait_for"))[1],
                  placeholder="readiness timeout seconds (default 60)", id="res-wait-timeout"),
            Label("", id="res-error", classes="dialog-hint"),
            Label("Test panel", classes="dialog-title"),
            Input(placeholder="command to test against the guard", id="test-cmd"),
            Label("", id="test-out", classes="dialog-hint"),
            Label("Dry-run is safe only for sync and needs a worktree source "
                  "distinct from root; custom shells can't be simulated.",
                  classes="dialog-hint"),
            Label("ctrl-s save · ctrl-t guard · ctrl-r dry-run · ctrl-h health · esc cancel",
                  classes="dialog-hint"),
            id="panel",
        )

    def on_mount(self) -> None:
        self._reflow(self._kind)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "res-template":
            return
        self._template_key = event.option.id or "custom"
        res = template_resource(self._template_key, path=self._root_path)
        self._kind = res["kind"]
        self.query_one("#res-kind", Label).update(f"kind: {res['kind']}")
        # Repopulate the form from the chosen template's defaults.
        self.query_one("#res-guard", TextArea).text = format_guard_lines(res.get("guard", []))
        self.query_one("#res-protect", TextArea).text = "\n".join(
            res.get("sync", {}).get("protect", []))
        self.query_one("#res-acq", Input).value = res.get("command_acquire", "")
        wline, wt = format_wait_for(res.get("wait_for"))
        self.query_one("#res-wait", Input).value = wline
        self.query_one("#res-wait-timeout", Input).value = wt
        self._reflow(res["kind"])

    def _reflow(self, kind: str) -> None:
        """Hide the path + protect inputs for non-file kinds (spec §6 reflow). A
        root-dir path is auto-derived (spec §1), so its input is shown read-only
        as the canonical main-worktree path; only a `path`-kind path is editable."""
        is_file = kind in ("root-dir", "path")
        path_input = self.query_one("#res-path", Input)
        path_input.display = is_file
        path_input.disabled = (kind == "root-dir")
        if kind == "root-dir":
            path_input.value = self._root_path     # canonical, not user-editable
        self.query_one("#res-protect", TextArea).display = is_file
        self.query_one("#res-protect-label", Label).display = is_file

    def _build_resource(self) -> dict:
        if self._existing is not None and self._template_key == "custom":
            res = dict(self._existing)
        else:
            res = template_resource(self._template_key, path=self._root_path)
        kind = res.get("kind")
        # root-dir path is ALWAYS the canonical main worktree (spec §1) — never
        # the editable input. Only a `path`-kind path is taken from the form.
        if kind == "root-dir":
            res["path"] = self._root_path
        elif kind == "path":
            p = self.query_one("#res-path", Input).value.strip()
            if p:
                res["path"] = p
        # The guard form is authoritative — an empty form means unguarded.
        res["guard"] = parse_guard_lines(self.query_one("#res-guard", TextArea).text)
        # Protect only has meaning when syncing; fold the form into the sync dict.
        if res.get("acquire") == "sync":
            sync = res.setdefault("sync", {"delete": True, "exclude": ["/.git"]})
            sync["protect"] = parse_path_lines(self.query_one("#res-protect", TextArea).text)
        # Command/health/wait_for: the form is authoritative, so a CLEARED field
        # removes the stale value (and reverts a now-empty command strategy to
        # 'none') rather than silently keeping the old one (Finding 3).
        acq = self.query_one("#res-acq", Input).value.strip()
        if acq:
            res["command_acquire"] = acq
            res["acquire"] = "command"
        else:
            res.pop("command_acquire", None)
            if res.get("acquire") == "command":
                res["acquire"] = "none"
        rel = self.query_one("#res-rel", Input).value.strip()
        if rel:
            res["command_release"] = rel
            res["release"] = "command"
        else:
            res.pop("command_release", None)
            if res.get("release") == "command":
                res["release"] = "none"
        res["release_required"] = self.query_one("#res-req", Checkbox).value
        health = self.query_one("#res-health", Input).value.strip()
        if health:
            res["health"] = health
        else:
            res.pop("health", None)
        wf = parse_wait_for(self.query_one("#res-wait", Input).value,
                            self.query_one("#res-wait-timeout", Input).value)
        if wf:
            res["wait_for"] = wf
        else:
            res.pop("wait_for", None)
        return res

    def action_save(self) -> None:
        from . import queue_config as _qc
        rid = self.query_one("#res-id", Input).value.strip()
        # A non-empty but unparseable readiness field is a typo, not "no
        # readiness" — refuse rather than silently drop it. (Empty still clears,
        # handled in _build_resource.) Kept here, not in _build_resource, so the
        # test-panel actions that also build the resource never raise on a typo.
        wait_text = self.query_one("#res-wait", Input).value.strip()
        if wait_text and parse_wait_for(
                self.query_one("#res-wait", Input).value,
                self.query_one("#res-wait-timeout", Input).value) is None:
            self.query_one("#res-error", Label).update(
                "[red]readiness must be '<url|port|command> <target>'[/]")
            return
        try:
            res = self._build_resource()
            _qc.add_resource(self._config_path, project_id=self._project_id,
                             display_path=self._project_root, resource_id=rid,
                             resource=res)
        except ValueError as e:
            self.query_one("#res-error", Label).update(f"[red]{e}[/]")
            return
        self.dismiss(True)

    def action_test_guard(self) -> None:
        from . import guard_match as _gm
        # Build from the CURRENT form (edits + existing saved guard), not the
        # bare template — otherwise an edited or existing guard isn't tested.
        rules = self._build_resource().get("guard") or []
        cmd = self.query_one("#test-cmd", Input).value.strip()
        if not cmd:
            return
        if _gm.matches(cmd, rules):
            self.query_one("#test-out", Label).update("[yellow]→ QUEUED (guarded)[/]")
        else:
            self.query_one("#test-out", Label).update("[green]→ RUNS FREE (unguarded)[/]")

    def action_dry_run(self) -> None:
        """rsync --dry-run preview for sync resources: deletions PLUS the
        exclusive-or check (spec §6) — a live root session and/or a dirty root
        that would block/refuse the real acquire, surfaced before the user trusts
        the preview."""
        from . import qsync as _qs, exclusive as _ex
        res = self._build_resource()
        if res.get("acquire") != "sync":
            self.query_one("#test-out", Label).update(
                "[dim]dry-run only applies to acquire=sync[/]")
            return
        # A real lease syncs from a WORKTREE over root. If the panel's source
        # (the selected node) is the root itself, rsync would diff root against
        # root and report "no deletions" — false safety. Refuse rather than lie.
        src = self._project_root
        if os.path.realpath(src) == os.path.realpath(res["path"]):
            self.query_one("#test-out", Label).update(
                "[yellow]dry-run needs a worktree source distinct from root — "
                "open this panel from a worktree session to preview deletions[/]")
            return
        lines = []
        # Exclusive-or check (spec §6): these would block/refuse the real acquire.
        # NB: _live_path() lives on the App, not this modal screen — use self.app.
        block = _ex.live_root_session(self.app._live_path(), res["path"])
        if block:
            lines.append(f"[red]⛔ root held by live session ‹{block.get('name')}› "
                         f"— acquire would block[/]")
        tg = _ex.transition_guard(res["path"])
        if tg:
            lines.append(f"[yellow]{tg}[/]")
        sync = res.get("sync", {})
        try:
            dels = _qs.dry_run_deletions(src, res["path"],
                                         exclude=sync.get("exclude", []),
                                         protect=sync.get("protect", []))
        except _qs.SyncDryRunError as e:
            lines.append(f"[red]dry-run failed: {e}[/]")
            self.query_one("#test-out", Label).update("\n".join(lines))
            return
        if dels:
            shown = ", ".join(dels[:6]) + (" …" if len(dels) > 6 else "")
            lines.append(f"[red]would DELETE {len(dels)}: {shown}[/]")
        else:
            lines.append("[green]no deletions[/]")
        self.query_one("#test-out", Label).update("\n".join(lines))

    def action_health(self) -> None:
        from . import probes as _p
        cmd = self.query_one("#res-health", Input).value.strip() or None
        ok, detail = _p.health_check(cmd)
        sev = "[green]" if ok else "[red]"
        self.query_one("#test-out", Label).update(f"{sev}health: {detail}[/]")


QUEUE_GUIDE_URL = ("https://github.com/johan-lindahl/session-explorer"
                   "/blob/main/docs/queue-guide.md")


def _queue_help_text() -> str:
    return "\n".join([
        "[b]Shared resources — quick help[/]",
        "",
        "[b]Isolate first.[/] If you can give each worktree its own port, DB, or",
        "derived-data dir, do that instead — this engine is for singletons that",
        "genuinely can't be isolated (one bind-mounted root, one simulator, one DB).",
        "",
        "[b]sync is destructive.[/] A root-dir 'sync' acquire runs",
        "[b]rsync --delete[/] from your worktree over the shared root — it blows",
        "away whatever the previous holder left. [b]protect[/] lists root-only",
        "paths to keep untouched (secrets, certs); [b].git/.env[/] are protected",
        "by default. Use the [b]dry-run[/] test (ctrl-r) to see deletions first.",
        "",
        "[b]Guards[/] are matched on the parsed command (exe + subcommand), never",
        "as substrings. Test a command with ctrl-t before relying on it.",
        "",
        # Plain, copyable URL (always visible) wrapped in a best-effort click
        # link — terminals without OSC-8 still show the bare URL to copy.
        "Full guide (opens / copyable):",
        f"  [link={QUEUE_GUIDE_URL}]{QUEUE_GUIDE_URL}[/link]",
    ])


class QueueHelpScreen(_PanelScreen):
    """Offline shared-resource help. esc closes."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(_queue_help_text()),
            Label("esc close", classes="dialog-hint"),
            id="panel",
        )


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
    #queues { height: auto; max-height: 40%; padding: 0 1; border-top: solid $accent; }
    Tree { padding: 0 1; width: 1fr; }
    #preview { width: 1fr; padding: 0 1; border-left: solid $accent; }
    HelpScreen { align: center middle; }
    #help { width: 78; max-width: 90%; height: auto; max-height: 90%;
            padding: 1 2; border: round $accent; background: $surface; }
    """

    BINDINGS = [
        Binding("enter", "resume", "Resume", priority=True),
        Binding("r", "rename", "Rename"),
        Binding("f2", "rename", "Rename", key_display="F2", show=False),
        Binding("m", "move", "Move"),
        Binding("n", "new_folder", "New folder"),
        Binding("c", "new_session", "New session"),
        Binding("d", "delete", "Delete"),
        Binding("w", "remove_worktree", "Remove worktree"),
        Binding("e", "notes", "Edit notes"),
        Binding("tab", "cycle_view", "Cycle view", key_display="Tab", priority=True),
        Binding("z", "toggle_collapse", "Collapse tree"),
        Binding("g", "toggle_usage", "Usage bar"),
        Binding("f5", "rescan", "Rescan", key_display="F5"),
        Binding("space", "preview", "Preview", priority=True),
        Binding("slash", "filter", "Filter"),
        Binding("h", "help", "Help"),
        Binding("q", "toggle_queues", "Queues"),
        Binding("x", "quit", "Exit"),
        Binding("s", "resource_setup", "Shared resources", show=False),
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
        # One-shot guard for the legacy basename→root folder-store re-key.
        self._fs_keys_migrated: bool = False
        self._resume_target: str | None = None
        self._resume_cwd: str | None = None
        self._new_session_argv: list[str] | None = None
        self._new_session_cwd: str | None = None
        self._filter_needle: str = ""
        # Display mode cycled by Tab: 0 = named + active (default),
        # 1 = active only, 2 = all (incl. unnamed).
        self._view_mode: int = 0
        # Flips after the first rescan so the empty-state can switch from
        # "press F5 to scan" to "no sessions found".
        self._scanned: bool = False
        # The live rescan progress modal while a scan runs, else None.
        self._rescan_screen: RescanScreen | None = None
        # Live-session state: sid -> "working"|"idle", refreshed by _poll_live.
        self._live_states: dict[str, str] = {}
        self._spinner_frame: int = 0
        self._wt_size_cache: dict[str, str] = {}   # sid -> human size, lazy
        self._offered_cleanup: set[str] = set()     # sids already asked to clean
        # sid -> (TreeNode, child_depth) for in-place glyph updates without a
        # full rebuild. Rebuilt by _populate.
        self._row_nodes: dict[str, tuple] = {}
        # sid to move the cursor to on the next populate where its row exists
        # (set after creating a new session). Cleared once honored.
        self._pending_select_sid: str | None = None
        # Collapse-to-roots view: when on, projects/folders render collapsed
        # except those the user has drilled into (tracked in _expanded by key).
        # Stale keys (renamed/deleted nodes) are benign — they never match and
        # accumulate at most O(projects × folder-depth) per session.
        self._collapse_mode: bool = False
        self._expanded: set[str] = set()
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
        # Debounce timer for cursor-follow docking; reset on every cursor move.
        self._sync_timer = None
        # Usage-bar refresh timer (5-min interval); None when the bar is off.
        self._usage_timer = None
        # Queues pane (shared-resource leases). Visibility persists globally;
        # _queue_hint_forced shows a one-line activation hint after an explicit
        # `q` press even when nothing is configured (cleared on next toggle-off).
        self._queue_visible: bool = False
        self._queue_hint_forced: bool = False
        # Best-effort out-of-lease detector: per-resource last top-level snapshot
        # and a debounce set. A change-burst toasts once; the set re-arms after a
        # stable (unchanged) poll, so a *later* distinct change toasts again.
        self._detect_snaps: dict[str, dict] = {}
        self._detect_warned: set[str] = set()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # App-level bindings (especially priority ones like Enter→resume) must
        # not fire while a modal screen is up; otherwise the modal's own Enter
        # handler (e.g. Input submit) never runs.
        if action in ("resume", "rename", "move", "new_folder", "new_session", "delete", "notes", "preview", "close_preview", "filter", "cycle_view", "toggle_collapse", "toggle_usage", "rescan", "help", "expand_node", "collapse_node", "quit", "toggle_queues", "resource_setup") and isinstance(self.screen, ModalScreen):
            return False
        # While the filter Input is focused, never let `q`/`x` fire the global
        # Queues-toggle or Exit bindings — the keystrokes belong in the text.
        if action in ("quit", "toggle_queues") and getattr(self, "_filter", None) is not None and self._filter.has_focus:
            return False
        # Tab is a priority binding (it must beat Textual's focus traversal), so
        # explicitly suppress it while the filter Input is focused — there, Tab
        # should not cycle the view.
        if action == "cycle_view" and getattr(self, "_filter", None) is not None and self._filter.has_focus:
            return False
        # The usage bar only exists in the tmux-hosted layout.
        if action == "toggle_usage" and not self._tmux_enabled:
            return False
        # `s` (shared-resource setup) is only meaningful with a project selected.
        if action == "resource_setup":
            proj, _ = self._project_and_prefix_for_cursor()
            return proj is not None
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
        self._queues = Static("", id="queues")
        self._queues.display = False
        yield Horizontal(
            Vertical(self._colheader, self._tree, self._queues, self._empty, id="treepane"),
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

    def _ui_path(self) -> str:
        from . import ui_state as _ui
        return _ui.default_path_for(self._index_path)

    def _queue_config_path(self) -> str:
        from . import queue_config as _qc
        return os.environ.get("SESSION_EXPLORER_QUEUE_CONFIG") or _qc.default_path_for(self._index_path)

    def _queues_root(self) -> str:
        return os.environ.get("SESSION_EXPLORER_QUEUES_ROOT") or os.path.join(self._claude_dir(), "session-explorer-queues")

    def on_mount(self) -> None:
        self.title = "session-explorer"
        self._populate()
        from . import ui_state as _ui
        self._queue_visible = bool(_ui.load(self._ui_path()).get("queue_pane_visible"))
        self._render_queues()  # content-gated; renders nothing if empty
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
        # Usage bar: restore it if the user left it enabled, but only in the
        # tmux-hosted layout (it has nowhere to render otherwise).
        if self._tmux_enabled and self._usage_enabled():
            self._start_usage()

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
        if not self._fs_keys_migrated:
            # One-shot: re-key any legacy basename-keyed folder store to repo
            # roots before the first render so it lines up with the root-keyed
            # tree (see index.migrate_folder_store_keys). Idempotent + cheap.
            _index.migrate_folder_store_keys(
                self._index_path, _fs.default_path_for(self._index_path))
            self._fs_keys_migrated = True
        data = _index.load(self._index_path)
        fs_data = _fs.load(_fs.default_path_for(self._index_path))
        live_ids = set(self._live_states)
        tree = build_nested_tree(
            data, fs_data,
            include_unnamed=(self._view_mode == 2),
            live_ids=live_ids,
            live_only=(self._view_mode == 1),
        )
        # Only mode 0 hides unnamed stubs; surface the count so the subtitle and
        # empty-state can advertise the Tab cycle.
        unnamed_hidden = 0
        if self._view_mode == 0:
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
        if self._view_mode == 1:
            # In active-only mode `total` already counts only live sessions, so
            # active_suffix would just repeat it — omit it here.
            self.sub_title = f"Active only — {total} sessions (Tab)"
        elif self._view_mode == 2:
            self.sub_title = (f"All sessions incl. unnamed — {total} across "
                              f"{len(tree)} projects{active_suffix} (Tab)")
        elif unnamed_hidden:
            self.sub_title = (f"{total} sessions across {len(tree)} projects · "
                              f"{unnamed_hidden} unnamed hidden (Tab){active_suffix}")
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
            view_mode=self._view_mode,
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
        # `_project_and_prefix_for_cursor` can read the project root and folder
        # segments directly instead of reverse-parsing the rendered label.
        # `project` carries the repo *root* (stable node identity); `label` is
        # the disambiguated display name shown to the user.
        def render(parent, project, label, segments, node, child_depth):
            for sid, s in node["_sessions"]:
                if self._matches(sid, s):
                    glyph = _glyph(self._live_states.get(sid), self._spinner_frame,
                                   self._ours_flag(sid))
                    wt = _worktree_state(s.get("project_path"))
                    leaf = parent.add_leaf(
                        _row_label(sid, s, child_depth, glyph, wt),
                        data={"sid": sid, **s, "worktree_state": wt,
                              "project_display": label})
                    self._row_nodes[sid] = (leaf, child_depth)
            for name in sorted(node["_folders"]):
                child = node["_folders"][name]
                child_segs = segments + [name]
                fkey = self._node_key(project, child_segs)
                folder_node = parent.add(
                    f"{name}/",
                    expand=self._should_expand(fkey),
                    data={"project": project, "segments": child_segs},
                )
                render(folder_node, project, label, child_segs, child, child_depth + 1)

        for project in sorted(tree, key=lambda r: tree[r]["_label"].lower()):
            node = tree[project]
            label = node["_label"]
            proj_node = root.add(
                f"{label} ({count(node)})",
                expand=self._should_expand(project),
                data={"project": project, "segments": []},
            )
            # Project sits at tree depth 0 (show_root=False); its direct
            # children — both ungrouped sessions and top-level folders — are at
            # tree depth 1.
            render(proj_node, project, label, [], node, child_depth=1)

        # Honor a pending select (e.g. just-created session) once its row exists.
        if self._pending_select_sid and self._pending_select_sid in self._row_nodes:
            leaf = self._row_nodes[self._pending_select_sid][0]
            # Open every ancestor so the row is visible before moving the cursor
            # (needed when collapse mode collapsed the enclosing project/folder).
            anc = leaf.parent
            while anc is not None and anc is not self._tree.root:
                anc.expand()
                d = anc.data or {}
                if "project" in d:
                    self._expanded.add(self._node_key(d["project"], d.get("segments") or []))
                anc = anc.parent
            self._restore_cursor_to_sid(self._pending_select_sid)
            self._pending_select_sid = None

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

    def _join_docked(self, sid: str, *, focus: bool = True) -> None:
        """Join `sid` into the explorer as the right pane, recording it as the
        dock **only** when join-pane succeeds. A failed join must not leave a
        phantom `_docked_sid` — that would lie to the refocus path (Enter on
        the "docked" row would select a pane that isn't there) and hide the
        failure. The session still runs as a background window, so surface the
        problem and leave it re-dockable on the next Enter. `focus=False` keeps
        focus in the explorer tree (cursor-follow sync)."""
        if _tmux.dock(sid, focus=focus) == 0:
            self._docked_sid = sid
        else:
            self.notify("Could not dock the session (tmux join-pane failed); "
                        "it's running in the background.", severity="warning")

    def _dock(self, sid: str, cwd: "str | None", label: "str | None",
              *, already_running: bool, focus: bool = True) -> None:
        """Make `sid` the docked right pane. If it is already docked, just
        refocus it (when `focus`); otherwise undock whatever is docked, (re)start
        the session as a background window when needed, and join it in. With
        `focus=False` the explorer tree keeps focus (cursor-follow sync)."""
        if self._docked_sid == sid:
            # Refocus claude (Enter on the already-docked row). The cursor-follow
            # sync never reaches here — it early-returns on sid == _docked_sid —
            # so the `focus=False` case of this branch is only a defensive guard.
            if focus:
                pane = _tmux.docked_pane(self._self_pane)
                if pane:
                    _tmux.select_pane(pane)        # refocus claude
            return
        self._undock_current()
        if not already_running:
            _tmux.start_window(sid, cwd, label)    # background window first
        self._join_docked(sid, focus=focus)        # join into the explorer

    def _schedule_dock_sync(self) -> None:
        """Queue a cursor-follow dock sync, debounced: every cursor move resets
        the timer, so holding an arrow to scroll past several running sessions
        coalesces to where the cursor settles instead of re-parenting the live
        claude pane on every keypress."""
        if not self._tmux_enabled:
            return
        if self._sync_timer is not None:
            self._sync_timer.stop()
        self._sync_timer = self.set_timer(
            DOCK_SYNC_DEBOUNCE, self._sync_dock_to_cursor)

    def _sync_dock_to_cursor(self) -> None:
        """Keep the docked pane in step with the tree cursor: show the selected
        session when it's a running, dockable session of ours; otherwise close
        the pane. Never starts a stopped session (that's Enter) and never steals
        focus from the tree — so you can navigate and have the pane follow.

        A docked session is a pane (absent from `session_windows()`); a
        re-dockable running session is one of our background windows. Anything
        else — a stopped session, a session live in another terminal (we can't
        host a second claude on its transcript), or a folder/project node —
        closes the pane."""
        if not self._tmux_enabled:
            return
        sid = self._selected_sid()
        if sid and sid == self._docked_sid:
            return                                 # already shown; leave focus
        if sid and sid in _tmux.session_windows():
            self._dock(sid, None, None, already_running=True, focus=False)
        else:
            self._undock_current()

    def action_resume(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        data = node.data
        sid = data["sid"]
        # A transcript-less stub has no conversation to --resume (its first turn
        # never happened, e.g. `claude -w` failed at startup). record_session
        # always writes transcript_path AND message_count together, and a
        # seed-only stub has neither — so "no transcript and no messages" is the
        # stub signal. Start it fresh (reusing the seeded id + name) instead.
        if not data.get("transcript_path") and not data.get("message_count"):
            self._start_stub_fresh(sid, data)
            return
        project_path = data.get("project_path")
        # Human label for the tmux status bar (the window name stays the sid).
        _, _display = split_path(data.get("name_cached"))
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
                    # The worktree was just recreated on disk — repaint the
                    # indicator (green if it's now a real worktree) right away.
                    self._set_worktree_state(sid, _worktree_state(project_path))
                    self._dock(sid, cwd, label, already_running=False)
                    self._poll_live()
            self.push_screen(ConfirmScreen(
                "This session is from a deleted git worktree.\n"
                "Recreate the worktree and resume?\n"
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
                "Recreate the worktree and resume?\n"
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
            if session_root(s) != project:
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
            # Record the rename as authoritative: a live session re-emits its old
            # in-memory title every turn, so set_name shadows the prior title(s)
            # to stop the next re-emit reverting the name (see index.set_name).
            from . import index as _index
            _index.set_name(self._index_path, sid, new_name, transcript)
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
            if session_root(s) != project:
                continue
            new_name = replace_folder_prefix(s.get("name_cached"), old_segs, new_segs)
            if new_name is not None:
                affected.append((sid, s.get("transcript_path"), new_name))

        def do() -> None:
            for sid, transcript, new_name in affected:
                if transcript:
                    append_custom_title(transcript, session_id=sid, new_name=new_name)
            # set_name per session so each shadows its own prior title(s); this
            # keeps a live session's re-emit from reverting the cascade rename.
            for sid, transcript, new_name in affected:
                _index.set_name(self._index_path, sid, new_name, transcript)
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
        project = session_root(data)  # repo root = folder-store key for this repo
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
            # Authoritative rename (shadows the prior title so a live re-emit
            # can't revert the move — see index.set_name).
            _index.set_name(self._index_path, sid, new_name, transcript)
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

    def action_resource_setup(self) -> None:
        from . import project_id as _pid
        project, _ = self._project_and_prefix_for_cursor()
        if not project:
            self.bell(); return
        pid = _pid.project_id(project)
        if pid is None:
            self.notify("This project is not a git repository — shared resources "
                        "need a repo.", severity="warning")
            return
        self.push_screen(ResourceListScreen(project_root=project, project_id=pid,
                                             config_path=self._queue_config_path()))

    def _project_root_is_shared(self, cwd: str) -> bool:
        """True if the project containing `cwd` has a `root-dir` shared resource
        configured — so a new session there should default to a worktree."""
        from . import project_id as _pid, queue_config as _qc
        pid = _pid.project_id(cwd)
        return bool(pid and any(
            r.get("kind") == "root-dir"
            for r in _qc.list_resources(self._queue_config_path(), pid).values()))

    def action_new_session(self) -> None:
        project, prefix = self._project_and_prefix_for_cursor()
        if not project:
            self.bell(); return
        sessions = _index.load(self._index_path).get("sessions", {})
        default_cwd = _derive_project_cwd(sessions, project) or os.path.expanduser("~")
        root_is_shared = self._project_root_is_shared(project)

        def after(result: "dict | None") -> None:
            if not result:
                return
            if root_is_shared and not result["worktree"]:
                def confirmed(ok: bool) -> None:
                    if ok:
                        self._finish_new_session(project, result)
                self.push_screen(
                    ConfirmScreen("This project's root is a shared sandbox; a "
                                  "plain root session can be clobbered by a lease. "
                                  "Create it anyway?"),
                    confirmed)
                return
            self._finish_new_session(project, result)

        self.push_screen(
            NewSessionScreen(project, prefix, default_cwd,
                             root_is_shared=root_is_shared),
            after)

    def _finish_new_session(self, project: str, result: dict) -> None:
        name = result["name"].strip()
        cwd = result["cwd"].strip() or os.path.expanduser("~")
        # worktree tri-state: None (off), "" (bare -w), or a name (-w name).
        worktree = (result["worktree_name"] or "") if result["worktree"] else None
        sid = _new_sid()

        # A blank name starts a *temporary* unnamed session: claude writes no
        # custom-title, so it stays unnamed (hidden by default) and --gc reaps
        # it on the retention schedule. Don't seed a name in that case.
        if name:
            # Seed the chosen name now: claude writes no transcript (and thus
            # no custom-title) until the first turn, so without this the
            # session shows under (unnamed) until then. claude -n persists the
            # identical title later, so there's no divergence.
            _index.seed_new_session(self._index_path, sid, name, cwd)

        # Remember the new sid so the cursor jumps to its row once it
        # appears in the tree (consumed by _populate; see select-on-create).
        self._pending_select_sid = sid

        # No tmux → exit and execvp claude (handled in run()).
        if not self._tmux_enabled:
            self._new_session_argv = _new_session_argv(sid, name, worktree)
            self._new_session_cwd = cwd
            self.exit()
            return

        _, display = split_path(name)
        label = display or sid[:8]
        self._do_new_session(sid, cwd, name, worktree, label)

    def _do_new_session(self, sid: str, cwd: str, name: str,
                        worktree: "str | None", label: "str | None") -> None:
        """Start a fresh claude session as a background window and dock it as
        the right pane, swapping out whatever was docked. A short-delay liveness
        check surfaces a startup failure (e.g. `claude -w` could not create its
        worktree) instead of letting it vanish into a closed window."""
        self._undock_current()
        err_path = _launch_err_path(sid)
        _tmux.start_new_session_window(sid, cwd, name, worktree, label,
                                       err_path=err_path)
        self._join_docked(sid)
        self._populate()           # show the newly-named session immediately
        self._poll_live()
        # Textual cancels pending timers on app exit, so quitting within the
        # delay window simply skips the check (the errfile is left in tmp).
        self.set_timer(LAUNCH_CHECK_DELAY,
                       lambda: self._check_launch(sid, err_path, name))

    def _check_launch(self, sid: str, err_path: str, name: str) -> None:
        """~1.5 s after a new-session launch, verify it actually stuck. If the
        window died at startup (claude exited — e.g. `claude -w` failed), read
        the captured stderr, surface it, log it, and stamp the stub so the row
        explains itself. Alive → just clean up the errfile."""
        alive = (sid in _tmux.session_windows()
                 or sid == self._docked_sid
                 or sid in self._live_states)
        if not alive:
            raw = ""
            try:
                with open(err_path, encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except OSError:
                pass
            msg = _summarize_launch_error(raw) or "Session failed to start."
            _log_line(f"launch failed sid={sid} name={name!r}: {raw!r}")
            self.notify(f"Couldn't start “{name or sid[:8]}”: {msg}",
                        severity="warning", timeout=10)
            _index.set_launch_error(self._index_path, sid, msg)
            self._populate()
        try:
            os.remove(err_path)
        except OSError:
            pass

    def _start_stub_fresh(self, sid: str, data: dict) -> None:
        """Launch a transcript-less stub as a fresh session, reusing its sid and
        name. Worktree defaults exactly as `c` does for the project: a
        shared-resource root → `-w <slug>`, else no worktree."""
        name = data.get("name_cached") or ""
        cwd = data.get("project_path") or os.path.expanduser("~")
        root_is_shared = self._project_root_is_shared(cwd)
        slug = worktree_slug(name)
        worktree = slug if (root_is_shared and slug) else None
        _, display = split_path(name)
        label = display or sid[:8]
        self._pending_select_sid = sid
        if not self._tmux_enabled:
            self._new_session_argv = _new_session_argv(sid, name, worktree)
            self._new_session_cwd = cwd
            self.exit()
            return
        self._do_new_session(sid, cwd, name, worktree, label)

    def _project_and_prefix_for_cursor(self) -> "tuple[str | None, str]":
        """Return (project, prefix), where `project` is the repo root (the
        folder-store key). prefix ends in '/' when the cursor sits on a folder
        so child creation is one segment away from done.

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

    def action_remove_worktree(self) -> None:
        """Reclaim the selected session's worktree directory (keeps branch +
        transcript; resume rebuilds it). Refuses live sessions; git refuses dirty
        ones."""
        node = self._tree.cursor_node
        data = node.data if (node and node.data) else {}
        sid = data.get("sid")
        path = data.get("project_path") or ""
        if not sid or worktree.MARKER not in path:
            self.bell()
            return
        # _running_sids() already unions in the docked pane when tmux is on; the
        # explicit _docked_sid check is belt-and-suspenders for the tmux-off path.
        running = set(self._running_sids()) if self._tmux_enabled else set()
        if sid in self._live_states or sid in running or sid == self._docked_sid:
            self.notify("Stop the session before removing its worktree.",
                        severity="warning")
            return
        if not os.path.isdir(path):
            self.notify("Worktree directory is already gone.", severity="warning")
            return
        size = self._wt_size_cache.get(sid) or worktree.size(path)
        self.push_screen(ConfirmScreen(
            f"Remove this worktree to free {size}?\n{path}\n"
            f"(The branch and transcript are kept; resume rebuilds it.)"),
            lambda ok: self._apply_worktree_removal(sid, path, size) if ok else None)

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

    def _gating_rows(self) -> list:
        """The rows that justify showing the pane AND are rendered in it (spec
        §9): every *active* queue across all projects, plus all resources of the
        currently-selected project (so its idle ones are visible). An idle,
        unrelated resource is in neither set, so it never opens the pane and is
        never rendered."""
        from . import project_id as _pid, queue_view as _qv
        try:
            rows = _qv.snapshot(self._queue_config_path(), self._queues_root(),
                                self._live_path())
        except Exception:
            rows = []
        sel_proj, _ = self._project_and_prefix_for_cursor()
        sel_pid = _pid.project_id(sel_proj) if sel_proj else None
        return [r for r in rows
                if r["active"] or (sel_pid is not None and r["project_id"] == sel_pid)]

    def action_toggle_queues(self) -> None:
        from . import ui_state as _ui
        self._queue_visible = not self._queue_visible
        _ui.set_queue_pane_visible(self._ui_path(), self._queue_visible)
        # Force the one-line hint ONLY when turning the pane on with nothing to
        # show — so the keypress isn't a silent no-op on an unconfigured project.
        # Never force when there is real content (else the pane stays stuck open
        # as a "hint" if that content later disappears this session).
        self._queue_hint_forced = self._queue_visible and not self._gating_rows()
        self._render_queues()

    def _render_queues(self) -> None:
        """Content-gated render (spec §9). The gating set and the rendered set are
        the SAME filtered rows, so the pane never shows an unrelated idle resource
        — when that set is empty it shows the activation hint (or nothing, if the
        hint wasn't forced this session)."""
        gating = self._gating_rows()
        show = self._queue_visible and (bool(gating) or self._queue_hint_forced)
        self._queues.display = show
        if not show:
            return
        if not gating:
            self._queues.update(
                "[b]Queues[/]  ·  this project is not using shared resources\n"
                "[dim]Select a project and press [b]s[/] to set up · "
                "guide: docs/queue-guide.md[/]")
            return
        self._queues.update(_render_queue_rows(gating))

    def _running_sids(self) -> list:
        """All sessions running in our server: background windows plus the
        currently-docked session. The docked session is a *pane* in the
        explorer window, not a window of its own, so `session_windows()` misses
        it — callers that reason about "what's running" (the quit-guard, the
        liveness-accessibility flag) must union it back in. Snapshots are the
        deliberate exception: a docked pane can't be captured by window name,
        so the live preview keeps using `session_windows()` and falls through
        to the transcript tail (the docked session is already visible live)."""
        sids = _tmux.session_windows()
        if self._docked_sid and self._docked_sid not in sids:
            sids.append(self._docked_sid)
        return sids

    def action_quit(self) -> None:
        if not self._tmux_enabled:
            self.exit()
            return
        running = self._running_sids()
        if not running:
            _tmux.set_status_left("")
            self.exit()
            return

        def after(choice) -> None:
            if choice == "shutdown":
                _tmux.set_status_left("")
                _tmux.kill_server()
                self.exit()
            elif choice == "background":
                # Persist-by-default: detaching leaves the server running; the
                # next /open reattaches. (Equivalent to an abrupt close now.)
                _tmux.set_status_left("")
                _tmux.detach_client()
            # None → cancel: stay in the explorer.
        self.push_screen(QuitScreen(running), after)

    def action_cycle_view(self) -> None:
        self._view_mode = (self._view_mode + 1) % 3
        self._populate()

    @staticmethod
    def _node_key(project: str, segments: "list[str]") -> str:
        # `project` is the repo root. \x00 cannot appear in a path or folder
        # segment, so it is a safe separator for "<root>\x00<seg/seg>" keys.
        return project if not segments else \
            project + "\x00" + "/".join(segments)

    def _should_expand(self, key: str) -> bool:
        """A node renders expanded unless we're collapsed and it isn't one of
        the keys the user drilled into."""
        return not self._collapse_mode or key in self._expanded

    def action_toggle_collapse(self) -> None:
        self._collapse_mode = not self._collapse_mode
        if self._collapse_mode:
            self._expanded.clear()  # collapse everything to project roots
        self._populate()

    def _usage_marker(self) -> str:
        return os.path.join(self._claude_dir(), ".session-explorer.usage-bar")

    def _usage_enabled(self) -> bool:
        return os.path.exists(self._usage_marker())

    def action_toggle_usage(self) -> None:
        if not self._tmux_enabled:
            return
        if self._usage_enabled():
            try:
                os.remove(self._usage_marker())
            except OSError:
                pass
            self._stop_usage()
            self.notify("Usage bar off")
        else:
            with open(self._usage_marker(), "a"):
                pass
            self._start_usage()
            self.notify("Usage bar on — checking…")

    def _start_usage(self) -> None:
        # Enabling fires one probe immediately so the bar appears within seconds;
        # toggling g off then on is therefore the manual "check now" path.
        self._refresh_usage()
        if self._usage_timer is None:
            self._usage_timer = self.set_interval(
                USAGE_POLL_INTERVAL, self._refresh_usage)

    def _stop_usage(self) -> None:
        if self._usage_timer is not None:
            self._usage_timer.stop()
            self._usage_timer = None
        _tmux.set_status_left("")

    @work(thread=True, exclusive=True, group="usage")
    def _refresh_usage(self) -> None:
        """Off-thread: scrape /usage (spawns a hidden claude, ~seconds), then push
        the rendered bar on the UI thread. `exclusive` so a slow scrape can't
        stack across the 5-min interval."""
        info = _usage.scrape_usage(self._claude_dir())
        self.call_from_thread(self._apply_usage, info)

    def _apply_usage(self, info) -> None:
        if info is not None:
            _tmux.set_status_left(_usage.render_bar(info))
        # On a miss we leave the previous bar in place (transient blip) rather
        # than blanking it; an explicit `g` off clears it.

    def _poll_live(self) -> None:
        """Refresh live-session state from the registry (called on a timer).

        Liveness changes update glyphs (or repopulate on a visibility change);
        then, regardless, live sessions' metadata is refreshed off-thread so
        first_prompt / msgs / tokens fill in and tick as the agent works.
        """
        from . import live as _live
        prev_live = set(self._live_states)
        try:
            new_states = _live.poll(self._live_path())
        except Exception:
            return  # never let the indicator break the UI
        # Which live sessions are *our* tmux windows (accessible) vs elsewhere.
        new_ours: set = set()
        if self._tmux_enabled:
            try:
                # Include the docked session: it's a pane, not a window, but it
                # is one of *ours* (you can F9 into it), so the glyph must show
                # accessible (●), not peek-only (○).
                new_ours = set(self._running_sids())
            except Exception:
                new_ours = self._our_windows  # keep last good on a tmux hiccup
        states_changed = new_states != self._live_states
        ours_changed = new_ours != self._our_windows
        if states_changed or ours_changed:
            old = self._live_states
            self._live_states = new_states
            self._our_windows = new_ours
            if states_changed and self._visibility_changed(old, new_states):
                # A pending select (just-created session not yet visible) wins
                # over preserving the old cursor, so the jump isn't clobbered
                # when the new row first surfaces on this very poll.
                sid = self._pending_select_sid or self._selected_sid()
                self._populate()
                self._restore_cursor_to_sid(sid)
            else:
                self._relabel_live_rows()
        ended = prev_live - set(new_states)
        if ended:
            self._maybe_offer_worktree_cleanup(ended)
        # Always refresh live metadata (stats change even when liveness doesn't).
        if self._live_states:
            self._refresh_live_metadata()
        # Keep the Queues pane current on the same cadence (cheap when hidden).
        if self._queue_visible:
            self._render_queues()
        self._detect_out_of_lease()

    def _detect_out_of_lease(self) -> None:
        """Compare root-dir snapshots between polls; toast on a change while the
        resource is in NEITHER valid exclusive-or state — i.e. no lease holder
        AND no live root session (which legitimately owns root, spec §5). Weak
        signal (spec §6); excludes the protect baseline + globs."""
        from . import queue_detect as _qd, queue_view as _qv
        try:
            rows = _qv.snapshot(self._queue_config_path(), self._queues_root(),
                                self._live_path())
        except Exception:
            return
        from . import queue_config as _qc
        for r in rows:
            if r["kind"] != "root-dir":
                continue
            res = _qc.get_resource(self._queue_config_path(), r["project_id"],
                                   r["resource"]) or {}
            path = res.get("path")
            if not path:
                continue
            exclude = set(p.lstrip("/") for p in res.get("sync", {}).get("protect", []))
            exclude |= {".git"}
            # Optional generated/served-path exclusions (spec §6). Schema-reserved:
            # read if present, not yet editable in the v1 form.
            exclude |= set(p.lstrip("/") for p in res.get("detect_exclude", []))
            snap = _qd.top_level_snapshot(path, exclude=exclude)
            prev = self._detect_snaps.get(r["id"])
            self._detect_snaps[r["id"]] = snap
            if prev is None:
                continue
            # A live root session is a legitimate exclusive-or holder, so its
            # edits are NOT out-of-lease (spec §5) — treat it as "held" too.
            held = r["holder"] is not None or r["live_root_block"] is not None
            if _qd.changed(prev, snap):
                if not held and r["id"] not in self._detect_warned:
                    self._detect_warned.add(r["id"])
                    self.notify(f"⚠ possible out-of-lease access on "
                                f"{_basename(r['project'])}/{r['resource']}",
                                severity="warning")
            else:
                # Stable poll → re-arm so the NEXT distinct change warns again
                # (instead of one warning sticking forever while idle).
                self._detect_warned.discard(r["id"])
            if held:
                self._detect_warned.discard(r["id"])  # re-arm once a lease runs too

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
        """True if a live-set change alters which rows are visible.

        Mode 1 (active only) is purely liveness-driven, so any flip matters.
        Mode 2 (all) always shows every session, so liveness never changes
        membership. Mode 0 only conditionally shows *unnamed* live sessions, so
        only an unnamed flip matters (a named session is always present)."""
        if self._view_mode == 2:
            return False
        if self._view_mode == 1:
            return set(old) != set(new)
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
            leaf.set_label(_row_label(sid, data, depth, glyph, data.get("worktree_state")))

    def _set_worktree_state(self, sid: str, state: "str | None") -> None:
        """Update one row's cached worktree state and repaint its glyph in place.

        Lets resuming a dead worktree (which recreates it on disk) flip the
        indicator red→green immediately, instead of staying stale until the next
        full rescan. The cached state survives subsequent live-metadata refreshes
        (they re-inject it), so the green sticks."""
        node = self._row_nodes.get(sid)
        if not node or not node[0].data:
            return
        leaf, depth = node
        leaf.data["worktree_state"] = state
        glyph = _glyph(self._live_states.get(sid), self._spinner_frame,
                       self._ours_flag(sid))
        leaf.set_label(_row_label(sid, leaf.data, depth, glyph, state))

    def _apply_worktree_removal(self, sid: str, path: str, size: str) -> None:
        """Remove the worktree directory and reflect the outcome in the UI.
        Shared by the manual 'w' action and the on-exit cleanup prompt."""
        result = worktree.remove(path)
        if result == "removed":
            self._set_worktree_state(sid, "dead")
            self._wt_size_cache.pop(sid, None)  # was X bytes; drop so the
            # preview stops showing a stale reclaim figure.
            self.notify(f"Worktree removed — {size} reclaimed.")
        elif result == "dirty":
            self.notify("Worktree has uncommitted changes — kept.",
                        severity="warning")
        else:
            self.notify("Could not remove the worktree (see "
                        "~/.claude/session-explorer.log).", severity="warning")

    def _maybe_offer_worktree_cleanup(self, ended: "set[str]") -> None:
        """When the docked session just stopped and its worktree is clean, offer
        to reclaim the directory — once per sid (tracked in _offered_cleanup).
        Dirty or non-worktree sessions are silently left alone. Cancel is
        permanent for this session; the user can always retry later with 'w'."""
        sid = self._docked_sid
        if sid is None or sid not in ended or sid in self._offered_cleanup:
            return
        node = self._row_nodes.get(sid)
        data = node[0].data or {} if node else {}
        path = data.get("project_path")
        if not path or worktree.MARKER not in path or not worktree.removable(path):
            return
        self._offered_cleanup.add(sid)
        name = data.get("name_cached") or sid[:8]
        size = self._wt_size_cache.get(sid) or worktree.size(path)

        def after(ok: bool) -> None:
            if ok:
                self._apply_worktree_removal(sid, path, size)

        self.push_screen(ConfirmScreen(
            f"Session '{name}' ended. Remove its worktree to free {size}?\n"
            f"{path}\n(The branch and transcript are kept; resume rebuilds it.)"),
            after)

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
                leaf.data = {"sid": sid, **data[sid],
                             "worktree_state": (leaf.data or {}).get("worktree_state")}
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
                                             self._ours_flag(sid)),
                                      (leaf.data or {}).get("worktree_state")))

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
        from . import worktree as _wt
        if _wt.MARKER in (data.get("project_path") or ""):
            if sid not in self._wt_size_cache:
                self._wt_size_cache[sid] = _wt.size(data.get("project_path"))
            data = {**data, "worktree_size": self._wt_size_cache[sid]}
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
        self._schedule_dock_sync()

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        if not self._collapse_mode:
            return
        # Track user-opened nodes so they survive a _populate() rebuild.
        # Session leaf nodes have data={"sid": ...} (no "project" key) —
        # the guard below ensures only project/folder nodes are tracked.
        data = getattr(event.node, "data", None) or {}
        if "project" in data:
            self._expanded.add(self._node_key(data["project"], data.get("segments") or []))

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed) -> None:
        if not self._collapse_mode:
            return
        data = getattr(event.node, "data", None) or {}
        if "project" in data:
            self._expanded.discard(self._node_key(data["project"], data.get("segments") or []))


_WORKTREE_MARKER = "/.claude/worktrees/"


def _dead_worktree_repo(project_path: "str | None") -> "str | None":
    """If `project_path` is a git-worktree path that needs recreating — its dir
    is gone, or a prior failed resume left it empty — and the parent repo still
    exists, return that repo root; else None. Cheap (one isdir + one listdir on
    the dead path), so the TUI can decide whether to offer recreating the
    worktree before resume."""
    if not project_path or _WORKTREE_MARKER not in project_path:
        return None
    if os.path.isdir(project_path) and os.listdir(project_path):
        return None  # populated → a live worktree, nothing to recreate
    root = project_path.split(_WORKTREE_MARKER, 1)[0]
    return root if os.path.isdir(root) else None


def _worktree_state(project_path: "str | None") -> "str | None":
    """Classify a session's working dir for the worktree indicator column.

    Returns None for a root checkout (no worktree marker), "live" for a populated
    git worktree, "dead" for one whose directory was removed — or left empty by a
    prior failed resume (same "dead" verdict `_dead_worktree_repo` uses, so the
    indicator and the resume prompt agree). Cheap (one isdir + one listdir);
    callers cache the result so the spinner/poll re-renders never hit the FS."""
    if not project_path or _WORKTREE_MARKER not in project_path:
        return None
    return "live" if os.path.isdir(project_path) and os.listdir(project_path) else "dead"


def _recreate_worktree(project_path: str, root: str) -> bool:
    """Recreate a deleted git worktree at `project_path` under repo `root`.

    `claude -w <name>` files the worktree under `.claude/worktrees/<name>` on a
    branch named `worktree-<name>`. Reattach to that branch if it still exists
    (preserving the work); otherwise create it fresh from HEAD. A prior failed
    resume may have left an empty dir at the path — drop it, and prune any stale
    worktree registration, so `git worktree add` doesn't refuse. Returns True iff
    git created the worktree."""
    leaf = project_path.split(_WORKTREE_MARKER, 1)[1].strip("/")
    if not leaf:
        return False
    if os.path.isdir(project_path):
        try:
            os.rmdir(project_path)  # only succeeds if empty
        except OSError:
            return False  # populated: not ours to clobber

    def git(*args: str) -> int:
        return subprocess.run(
            ["git", "-C", root, *args], capture_output=True, text=True
        ).returncode

    branch = f"worktree-{leaf}"
    git("worktree", "prune")
    have_branch = git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}") == 0
    add = ["worktree", "add", project_path, branch] if have_branch \
        else ["worktree", "add", "-b", branch, project_path]
    return git(*add) == 0


def _resolve_resume_cwd(project_path: "str | None") -> "str | None":
    """Directory to chdir into before `claude --resume`.

    `claude --resume` is scoped to the exact cwd that recorded the session, so to
    resume a session whose git worktree was deleted we recreate that worktree
    (via `git worktree add`) — the only way claude can locate the transcript
    filed under the worktree's project key, and it restores a real working tree.
    The TUI confirms this side effect first (see action_resume /
    _dead_worktree_repo). Returns a usable directory, or None when there's
    nothing to chdir into (caller leaves cwd alone)."""
    if not project_path:
        return None
    if os.path.isdir(project_path) and os.listdir(project_path):
        return project_path
    root = _dead_worktree_repo(project_path)
    if root:
        if _recreate_worktree(project_path, root):
            return project_path
        # git couldn't recreate it — fall back to a bare dir so claude can still
        # locate the transcript filed under this worktree's project key.
        try:
            os.makedirs(project_path, exist_ok=True)
            return project_path
        except OSError:
            return None
    # An existing-but-empty non-worktree dir is still a valid cwd.
    return project_path if os.path.isdir(project_path) else None


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


def _derive_project_cwd(sessions: dict, project: str) -> "str | None":
    """Launch cwd for a new session in `project` (a repo root): the project_path
    of its most-recently-active session, with any git-worktree suffix stripped
    back to the repo root so `claude -w` branches from the real repository. None
    when the project has no session with a usable path."""
    best = None
    best_key = ""
    for s in sessions.values():
        if session_root(s) != project:
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
    `-w`; otherwise `-w <name>`. An empty `name` omits `-n`, starting an unnamed
    (temporary) session that stays hidden by default and is reaped by `--gc`."""
    argv = ["claude", "--session-id", sid]
    if name:
        argv += ["-n", name]
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
