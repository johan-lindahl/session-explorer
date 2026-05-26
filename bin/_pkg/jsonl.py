"""Parse Claude Code session JSONL transcripts.

VERIFIED FIELD PATHS (Task 4 — inspected real ~/.claude/projects/ transcripts,
Claude Code v2.1.x, 2026-05-26):

LINE TYPES observed across 506 real JSONL files (sorted by frequency):
  assistant          — Claude response; carries message.* and usage
  user               — Human turn (or tool-result turn)
  last-prompt        — Metadata: last prompt leaf UUID
  permission-mode    — Metadata: current permission mode
  ai-title           — AI-generated session title; field: aiTitle
  file-history-snapshot — Snapshot of open files
  attachment         — Hook result / context attachment
  worktree-state     — Worktree metadata
  system/turn_duration — Timing/count per turn; fields: durationMs, messageCount
  queue-operation    — Queue state
  agent-name         — Sub-agent name; field: agentName
  custom-title       — User-set session title (rename); field: customTitle
  system/away_summary — Summary written during away period
  bridge-session     — Session bridge metadata
  system/local_command — Local shell command log
  system/api_error   — API error record
  system/bridge_status — Bridge health
  system/scheduled_task_fire — Scheduled task trigger
  pr-link            — Pull-request URL
  system/compact_boundary — Compaction marker

NO system/init LINE TYPE EXISTS in real files. The hypothetical system/init
with sessionName was a wrong assumption from Task 3.

SESSION NAME (Task 5 target):
  - AI-generated: type="ai-title", field: aiTitle  (appears many times per
    session as the model refines it; the LAST occurrence is current)
  - User-set rename: type="custom-title", field: customTitle
    (takes precedence over ai-title when present)
  - No system/init.sessionName exists.

TOKEN USAGE (confirmed on every assistant line):
  message.usage.input_tokens
  message.usage.cache_creation_input_tokens   — may be 0
  message.usage.cache_read_input_tokens       — may be 0
  message.usage.output_tokens
  message.usage.cache_creation.ephemeral_1h_input_tokens
  message.usage.cache_creation.ephemeral_5m_input_tokens
  (also server_tool_use.web_search_requests, etc.)

MODEL ID (confirmed per-message):
  message.model  — on every assistant line, e.g. "claude-sonnet-4-6"

USER CONTENT SHAPE:
  - First user message: message.content is typically a plain string
  - Subsequent user turns (tool results): message.content is a list of dicts
    with type="tool_result" or type="text"

RENAME SERIALIZATION:
  type="custom-title", field: customTitle (string), sessionId (string).
  No "rename" event type observed. User title overrides ai-title.
  Example shape (no real content):
    {"type":"custom-title","customTitle":"<user label>","sessionId":"<uuid>"}
"""

import json
import os
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
                # Tool-result turns have list content; look for a text item.
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return item.get("text")
    return None


def session_name(path: str) -> Optional[str]:
    """Returns the Claude-assigned session name, or None.

    Precedence: the LAST `custom-title` line's `customTitle` wins (user rename).
    Falls back to the LAST `ai-title` line's `aiTitle` (AI-generated, refined
    repeatedly during the session). Returns None if neither line type is present.
    """
    last_custom = None
    last_ai = None
    for msg in _iter_messages(path):
        t = msg.get("type")
        if t == "custom-title":
            v = msg.get("customTitle")
            if v:
                last_custom = v
        elif t == "ai-title":
            v = msg.get("aiTitle")
            if v:
                last_ai = v
    return last_custom or last_ai


def tokens_estimate(path: str) -> int:
    """Approximate context size.

    Returns `cache_read_input_tokens` from the latest assistant message
    (accurate per the spec's "Why not sum input_tokens" note). Falls back to
    `os.path.getsize(path) // 4` when no assistant message has been written yet.
    Zero for missing/empty files.
    """
    last_cache_read = None
    for msg in _iter_messages(path):
        if msg.get("type") == "assistant":
            usage = msg.get("message", {}).get("usage") or {}
            val = usage.get("cache_read_input_tokens")
            if val:
                last_cache_read = val
    if last_cache_read is not None:
        return int(last_cache_read)
    try:
        return os.path.getsize(path) // 4
    except FileNotFoundError:
        return 0


def last_active_at(path: str) -> Optional[str]:
    """ISO8601 timestamp of the LAST line that carries one; None if missing/empty."""
    last = None
    for msg in _iter_messages(path):
        ts = msg.get("timestamp")
        if ts:
            last = ts
    return last
