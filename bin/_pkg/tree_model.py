"""Pure tree-building from a loaded index. No I/O, no Textual.

Layout produced by build_nested_tree:
  dict[project_label, node]

Each node is: {"_sessions": [(sid, session_dict), ...], "_folders": {seg: node, ...}}

Session names use `/`-separated paths: the last segment is the display name and
all preceding segments form the folder hierarchy. Dashes are ordinary characters
— they carry no special meaning.

Special folder labels:
  "(unnamed)"  — synthetic folder holding sessions with no name_cached
                 (only present when include_unnamed=True)
  "(unfiled)"  — project bucket carrying stored empty folders with no session
"""

from __future__ import annotations

from typing import Dict, List, Tuple

_WORKTREE_MARKER = "/.claude/worktrees/"


def session_root(s: dict) -> str:
    """The stable grouping identity for a session: its repo root path.

    Strips any ``/.claude/worktrees/<name>`` suffix so a repo's worktrees group
    with the repo. Falls back to the cached label (then ``(unknown)``) when the
    session has no path — the case exercised by older entries and unit tests.
    """
    cwd = s.get("project_path")
    if cwd:
        if _WORKTREE_MARKER in cwd:
            cwd = cwd.split(_WORKTREE_MARKER, 1)[0]
        return cwd.rstrip("/") or cwd
    return s.get("project_label") or "(unknown)"


def split_path(name: "str | None") -> Tuple[List[str], str]:
    """Split a session name on `/` into folder segments + display name.

    The last non-empty segment is the display name; everything before it is the
    folder path. Empty segments (from `foo//bar`, leading/trailing `/`, or
    whitespace-only segments) are dropped. Returns ([], "") when there's no
    usable content.
    """
    if not name:
        return ([], "")
    segments = [seg.strip() for seg in name.split("/")]
    segments = [seg for seg in segments if seg]
    if not segments:
        return ([], "")
    return (segments[:-1], segments[-1])


def replace_folder_prefix(
    name: "str | None", old_segments: List[str], new_segments: List[str]
) -> "str | None":
    """Rewrite a session `name` when it lives under the folder `old_segments`.

    Folder membership is compared segment-wise (not as a raw string prefix), so
    folder ["team","planning"] never captures a session named
    "team/planning-extra/x" — "planning" and "planning-extra" are distinct
    segments. A session whose folder path *equals* `old_segments` (the session
    sits directly in the folder, its leaf being the display name) is included.

    Returns the rewritten name with `old_segments` swapped for `new_segments`
    and the display name + any deeper sub-segments preserved, or None when the
    session is not under the folder.
    """
    segments, display = split_path(name)
    n = len(old_segments)
    if len(segments) < n or segments[:n] != list(old_segments):
        return None
    rebuilt = list(new_segments) + segments[n:] + [display]
    return "/".join(rebuilt)


def _basename(root: str) -> str:
    """The repo's own folder name — the last path segment of its root."""
    return root.rstrip("/").split("/")[-1] or root


def disambiguate(roots) -> Dict[str, str]:
    """Map each repo root path to a display label.

    A root whose basename is unique gets that bare basename (e.g. ``magento2``).
    When several distinct roots share a basename — the duplicate-repo case —
    each gets the *minimal* ancestor path that tells it apart: the immediate
    parent when that suffices (``acme/magento2``), or the nearest differing
    ancestor with skipped levels collapsed to ``…`` (``work/…/magento2``).
    Labels are unique across the returned map. A root with no path separator
    has no ancestor to borrow, so it keeps its bare value even under collision.
    """
    by_base: Dict[str, List[str]] = {}
    for r in roots:
        by_base.setdefault(_basename(r), []).append(r)

    out: Dict[str, str] = {}
    for base, group in by_base.items():
        if len(group) == 1:
            out[group[0]] = base
            continue
        for r in group:
            out[r] = _unique_label(r, group)

    # The `ancestor/…/base` form can, in rare deep layouts, still produce two
    # equal labels (same furthest ancestor + basename, differing only in a
    # collapsed middle). Fall back to the full root path for any that collide so
    # the displayed label is always unique. (Node identity is the root either
    # way, so this only affects display.)
    seen: Dict[str, int] = {}
    for label in out.values():
        seen[label] = seen.get(label, 0) + 1
    for r, label in list(out.items()):
        if seen[label] > 1:
            out[r] = r
    return out


def _unique_label(root: str, group: List[str]) -> str:
    """Shortest ``ancestor[/…]/basename`` label distinguishing `root` in `group`."""
    segs = root.rstrip("/").split("/")
    base = segs[-1] or root
    # Walk up: j = number of ancestor segments included beyond the basename.
    for j in range(1, len(segs)):
        suffix = segs[-(j + 1):]
        if all(o is root or o.rstrip("/").split("/")[-(j + 1):] != suffix
               for o in group):
            ancestor = segs[-(j + 1)]
            sep = "/" if j == 1 else "/…/"
            return f"{ancestor}{sep}{base}"
    # No ancestor disambiguates (root too shallow / identical paths): use the
    # whole path so the label is at least unique.
    return root


def _empty_node() -> dict:
    return {"_sessions": [], "_folders": {}}


def _walk_to(node: dict, segments: List[str]) -> dict:
    """Walk into the nested tree, creating empty folder nodes as needed."""
    for seg in segments:
        node = node["_folders"].setdefault(seg, _empty_node())
    return node


def build_nested_tree(index_data: dict, folder_store_data: dict,
                      include_unnamed: bool = False,
                      live_ids: "set[str] | None" = None,
                      live_only: bool = False) -> Dict[str, dict]:
    """Nested project → folder → folder ... → sessions, the form the TUI renders.

    Each node is {"_sessions": [(sid, s)], "_folders": {seg: node, ...}}.

    Unnamed sessions: hidden by default. When include_unnamed=True they appear
    under a synthetic "(unnamed)" folder per project (preserves prior UX).

    Live sessions (`live_ids`) are always placed, even when unnamed and
    `include_unnamed` is False — a live unnamed session is surfaced under the
    synthetic "(unnamed)" folder.

    When `live_only` is True, only sessions whose sid is in `live_ids` are
    placed (named or unnamed); `include_unnamed` is ignored. This backs the
    TUI's "Active only" view mode.
    """
    live_ids = live_ids or set()
    out: Dict[str, dict] = {}

    # Nodes are keyed by repo *root* (the stable identity), not the display
    # label — two distinct repos that share a basename (e.g. several `magento2`
    # checkouts) must not collapse into one node. The disambiguated label is
    # computed once below and attached as `_label`.

    # 1. Place each session into its project (root) + folder path.
    for sid, s in index_data.get("sessions", {}).items():
        name = s.get("name_cached")
        if live_only:
            if sid not in live_ids:
                continue
        elif not name and not include_unnamed and sid not in live_ids:
            continue
        root = session_root(s)
        proj_node = out.setdefault(root, _empty_node())
        if not name:
            target = proj_node["_folders"].setdefault("(unnamed)", _empty_node())
        else:
            segments, _ = split_path(name)
            target = _walk_to(proj_node, segments)
        target["_sessions"].append((sid, s))

    # 2. Lay in stored folder paths (may create empty folder nodes). The folder
    # store is keyed by root too (see index.migrate_folder_store_keys) — BUT a
    # stale pre-root-keying hook can re-add *basename* keys after migration.
    # Such a key must not become its own ghost project, and must not make the
    # real repo's basename look contested (which would spuriously prefix it):
    # fold its paths into every session root sharing that basename instead.
    # Matching considers ALL session roots, not just the visible ones — folder
    # ownership doesn't depend on the current view filter.
    all_roots = {session_root(s) for s in index_data.get("sessions", {}).values()}
    for key, paths in (folder_store_data.get("projects") or {}).items():
        if key in all_roots or "/" in key:
            targets = [key]                 # a proper root (or path) key
        else:
            targets = [r for r in all_roots if _basename(r) == key] or [key]
        for root in targets:
            proj_node = out.setdefault(root, _empty_node())
            for path_str in paths or []:
                segs = [seg for seg in path_str.split("/") if seg.strip()]
                _walk_to(proj_node, segs)

    # 3. Attach a display label per root (bare basename, disambiguated only on
    # collision).
    labels = disambiguate(out.keys())
    for root, node in out.items():
        node["_label"] = labels.get(root, _basename(root))

    # 4. Sort every _sessions list newest-first.
    def sort_node(node: dict):
        node["_sessions"].sort(key=lambda x: x[1].get("last_active_at", ""), reverse=True)
        for child in node["_folders"].values():
            sort_node(child)
    for proj in out.values():
        sort_node(proj)

    return out
