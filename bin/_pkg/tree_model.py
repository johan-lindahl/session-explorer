"""Pure tree-building from a loaded index. No I/O, no Textual.

Layout:
  tree: dict[project_label, dict[folder_label, list[(sid, session_dict)]]]

Folder labels:
  ""           — ungrouped (session has a name but no dash)
  "(unnamed)"  — session has no name_cached at all (only when include_unnamed=True)
  "(unfiled)"  — synthetic project bucket holding pre-created empty folders
  any other    — first-dash folder prefix
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
