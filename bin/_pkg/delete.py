"""Delete a session: removes the JSONL, the index entry, its summary, and —
for a worktree session — the worktree directory + branch (safe, merged-only)."""

from __future__ import annotations

import os

from . import index as _index


def delete_session(index_path: str, session_id: str):
    """Returns the worktree purge outcome (see worktree.purge) or None when the
    session had no worktree."""
    captured: dict = {}

    def mutator(data: dict) -> dict:
        entry = data.get("sessions", {}).pop(session_id, None)
        if entry:
            captured["entry"] = entry
            transcript = entry.get("transcript_path")
            if transcript and os.path.exists(transcript):
                try:
                    os.unlink(transcript)
                except OSError:
                    pass
        return data

    _index.mutate(index_path, mutator)

    from . import summary as _summary
    _summary.remove(_summary.default_path_for(index_path), session_id)

    entry = captured.get("entry") or {}
    path = entry.get("project_path") or ""
    from . import worktree as _worktree
    if _worktree.MARKER in path:
        return _worktree.purge(path)
    return None
