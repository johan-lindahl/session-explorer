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
