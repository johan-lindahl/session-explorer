"""Delete a session: removes the JSONL file and the index entry."""

from __future__ import annotations

import os

from . import index as _index


def delete_session(index_path: str, session_id: str) -> None:
    def mutator(data: dict) -> dict:
        entry = data.get("sessions", {}).pop(session_id, None)
        if entry:
            transcript = entry.get("transcript_path")
            if transcript and os.path.exists(transcript):
                try:
                    os.unlink(transcript)
                except OSError:
                    pass
        return data
    _index.mutate(index_path, mutator)
