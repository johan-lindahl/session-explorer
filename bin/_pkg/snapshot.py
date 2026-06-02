"""Read-only progress snapshot for a session's preview pane.

Two sources (spec §3): an explorer-launched tmux window is captured live via
tmux capture-pane (handled at the call site, ANSI); any other live session is
summarised here by tailing its JSONL transcript into a compact activity view.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from . import jsonl as _jsonl


def _line_for(msg: dict) -> Optional[List[str]]:
    """Return list of activity lines (typically 0-2) for this message."""
    lines = []
    t = msg.get("type")
    content = msg.get("message", {}).get("content")
    if t == "user":
        if isinstance(content, str):
            return [f"you: {content.strip()[:80]}"]
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    return [f"you: {item.get('text', '').strip()[:80]}"]
        return []
    if t == "assistant" and isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text", "").strip():
                lines.append(f"claude: {item['text'].strip()[:80]}")
            if item.get("type") == "tool_use":
                lines.append(f"tool: {item.get('name', '?')}")
    return lines


def transcript_tail(path: str, limit: int = 12) -> str:
    """Last `limit` human-meaningful activity lines from the JSONL, or ''."""
    lines: List[str] = []
    for msg in _jsonl._iter_messages(path):
        msg_lines = _line_for(msg)
        lines.extend(msg_lines)
    return "\n".join(lines[-limit:])
