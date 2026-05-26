"""Parse Claude Code session JSONL transcripts.

Field paths are based on the Anthropic Messages API response format embedded
inside Claude Code transcripts. Task 4 (inspect-local-jsonl.py) verifies them
against a real local file; adjust here if reality diverges from the fixtures.
"""

import json
from typing import Optional


def _iter_messages(path: str):
    """Yield decoded JSON objects, silently skipping malformed lines."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return


def message_count(path: str) -> int:
    return sum(1 for _ in _iter_messages(path))


def first_user_prompt(path: str) -> Optional[str]:
    for msg in _iter_messages(path):
        if msg.get("type") == "user":
            content = msg.get("message", {}).get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list) and content:
                # User messages can occasionally be array-shaped (tool results).
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return item.get("text")
    return None
