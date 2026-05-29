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


def _empty_node() -> dict:
    return {"_sessions": [], "_folders": {}}


def _walk_to(node: dict, segments: List[str]) -> dict:
    """Walk into the nested tree, creating empty folder nodes as needed."""
    for seg in segments:
        node = node["_folders"].setdefault(seg, _empty_node())
    return node


def build_nested_tree(index_data: dict, folder_store_data: dict,
                      include_unnamed: bool = False,
                      live_ids: "set[str] | None" = None) -> Dict[str, dict]:
    """Nested project → folder → folder ... → sessions, the form the TUI renders.

    Each node is {"_sessions": [(sid, s)], "_folders": {seg: node, ...}}.

    Unnamed sessions: hidden by default. When include_unnamed=True they appear
    under a synthetic "(unnamed)" folder per project (preserves prior UX).

    Live sessions (`live_ids`) are always placed, even when unnamed and
    `include_unnamed` is False — a live unnamed session is surfaced under the
    synthetic "(unnamed)" folder.
    """
    live_ids = live_ids or set()
    out: Dict[str, dict] = {}

    # 1. Place each session into its project + folder path.
    for sid, s in index_data.get("sessions", {}).items():
        name = s.get("name_cached")
        if not name and not include_unnamed and sid not in live_ids:
            continue
        project = s.get("project_label") or "(unknown)"
        proj_node = out.setdefault(project, _empty_node())
        if not name:
            target = proj_node["_folders"].setdefault("(unnamed)", _empty_node())
        else:
            segments, _ = split_path(name)
            target = _walk_to(proj_node, segments)
        target["_sessions"].append((sid, s))

    # 2. Lay in stored folder paths (may create empty folder nodes).
    for project, paths in (folder_store_data.get("projects") or {}).items():
        proj_node = out.setdefault(project, _empty_node())
        for path_str in paths or []:
            segs = [seg for seg in path_str.split("/") if seg.strip()]
            _walk_to(proj_node, segs)

    # 3. Sort every _sessions list newest-first.
    def sort_node(node: dict):
        node["_sessions"].sort(key=lambda x: x[1].get("last_active_at", ""), reverse=True)
        for child in node["_folders"].values():
            sort_node(child)
    for proj in out.values():
        sort_node(proj)

    return out
