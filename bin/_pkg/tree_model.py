"""Pure tree-building from a loaded index. No I/O, no Textual.

Layout:
  tree: dict[project_label, dict[folder_label, list[(sid, session_dict)]]]

Folder labels:
  ""           — ungrouped (session has a name but no dash)
  "(unnamed)"  — session has no name_cached at all (only when include_unnamed=True)
  "(unfiled)"  — synthetic project bucket holding pre-created empty folders
  any other    — first-dash folder prefix (legacy split_folder), or a
                 `/`-separated path segment (new split_path; replaces
                 split_folder when all callers migrate)
"""

from __future__ import annotations

from typing import Dict, List, Tuple

ProjectsTree = Dict[str, Dict[str, List[Tuple[str, dict]]]]


def split_folder(name: "str | None") -> Tuple[str, str]:
    """First-dash split. ('', name) when no dash; ('', '') when no name."""
    if not name:
        return ("", "")
    if "-" not in name:
        return ("", name)
    folder, _, display = name.partition("-")
    return (folder, display)


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


def build_tree(index_data: dict, include_unnamed: bool = True) -> ProjectsTree:
    tree: ProjectsTree = {}
    for sid, s in index_data.get("sessions", {}).items():
        name = s.get("name_cached")
        if not name and not include_unnamed:
            continue
        project = s.get("project_label") or "(unknown)"
        if not name:
            folder = "(unnamed)"
        else:
            folder, _ = split_folder(name)
        tree.setdefault(project, {}).setdefault(folder, []).append((sid, s))

    # Sort each folder's sessions by last_active_at desc.
    for project in tree:
        for folder in tree[project]:
            tree[project][folder].sort(
                key=lambda x: x[1].get("last_active_at", ""), reverse=True
            )

    # Empty folders live under a synthetic "(unfiled)" project bucket.
    empty_folders = index_data.get("folders") or []
    if empty_folders:
        tree.setdefault("(unfiled)", {})
        for f in empty_folders:
            tree["(unfiled)"].setdefault(f, [])

    return tree


def _empty_node() -> dict:
    return {"_sessions": [], "_folders": {}}


def _walk_to(node: dict, segments: List[str]) -> dict:
    """Walk into the nested tree, creating empty folder nodes as needed."""
    for seg in segments:
        node = node["_folders"].setdefault(seg, _empty_node())
    return node


def build_nested_tree(index_data: dict, folder_store_data: dict,
                      include_unnamed: bool = False) -> Dict[str, dict]:
    """Nested project → folder → folder ... → sessions, the form the TUI renders.

    Each node is {"_sessions": [(sid, s)], "_folders": {seg: node, ...}}.

    Unnamed sessions: hidden by default. When include_unnamed=True they appear
    under a synthetic "(unnamed)" folder per project (preserves prior UX).
    """
    out: Dict[str, dict] = {}

    # 1. Place each session into its project + folder path.
    for sid, s in index_data.get("sessions", {}).items():
        name = s.get("name_cached")
        if not name and not include_unnamed:
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
