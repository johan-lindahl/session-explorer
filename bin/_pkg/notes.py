"""Set a session's notes field. Thin wrapper around index.mutate()."""

from __future__ import annotations

from . import index as _index


def set_notes(index_path: str, session_id: str, notes: str) -> None:
    def mutator(data: dict) -> dict:
        s = data.setdefault("sessions", {}).setdefault(session_id, {})
        s["notes"] = notes
        return data
    _index.mutate(index_path, mutator)
