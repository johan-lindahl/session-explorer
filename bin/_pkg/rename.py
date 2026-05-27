"""Append a Claude-compatible custom-title event to a session JSONL.

The line shape was verified in Task 2 against 389 real custom-title lines
in ~/.claude/projects/: Claude writes EXACTLY three keys — type, customTitle,
sessionId — with no envelope (no uuid, parentUuid, timestamp). We mirror that
minimal shape so Claude's own picker treats the rename as native.

Uses an exclusive flock on the target file to avoid interleaving with a live
Claude write.
"""

from __future__ import annotations

import fcntl
import json
import os


def append_custom_title(transcript_path: str, session_id: str, new_name: str) -> None:
    event = {
        "type": "custom-title",
        "customTitle": new_name,
        "sessionId": session_id,
    }
    line = json.dumps(event, ensure_ascii=False) + "\n"
    # O_APPEND + flock guarantees the line lands atomically at end-of-file.
    fd = os.open(transcript_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
