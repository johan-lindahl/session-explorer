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

RENAME SERIALIZATION (verified 2026-05-27 against 389 real custom-title lines
across ~50 transcripts in ~/.claude/projects/):
  Line shape (EXACT — no envelope fields):
    {"type":"custom-title",
     "customTitle":"<user label>",
     "sessionId":"<uuid>"}
  - All three fields are REQUIRED.
  - NO envelope fields are written: no uuid, no parentUuid, no timestamp, no
    cwd, no gitBranch, no version, no userType, no isSidechain. The plan's
    anticipated envelope (uuid/parentUuid/timestamp) does NOT apply to
    custom-title lines — Claude writes a minimal three-key object.
  - 100% of 389 sampled lines had exactly the key set
    {"type","customTitle","sessionId"} with no variation.
  - sessionId on the custom-title line matches the JSONL filename's UUID.
  - Multiple custom-title lines may appear in one file (observed up to 60+).
  - User title overrides ai-title.

CORRECTION (2026-06): the original ~50-file sample concluded the customTitle
"never drifted (first==last)". That was wrong. A LIVE Claude session re-writes
its in-memory custom-title roughly every turn, so after an *external* rename
(this plugin appending a custom-title) Claude's next re-emit puts the OLD title
back as the file's LAST line. A re-survey of current transcripts found drift in
19 files, 3 with the exact rename-then-revert signature. session_name() still
implements "LAST custom-title wins" (correct for sessions only Claude renames),
but the explorer no longer trusts it blindly: index.set_name records superseded
titles as "shadows" and record_session ignores a shadowed last-title. See
all_custom_titles() and SPEC.md → "Design decisions (resolved)".
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
    """Returns the user-assigned session name, or None.

    Only the LAST `custom-title` line's `customTitle` counts as a name — that's
    what `/rename` and `claude -n` write. The `ai-title` events Claude emits
    automatically as the session evolves are intentionally NOT treated as
    names, because the spec defines "kept" as "user-explicit name", not "any
    title that happens to be in the JSONL" (CLAUDE.md, SPEC.md §Naming).
    Returns None if no custom-title line is present.
    """
    last_custom = None
    for msg in _iter_messages(path):
        if msg.get("type") == "custom-title":
            v = msg.get("customTitle")
            if v:
                last_custom = v
    return last_custom


def all_custom_titles(path: str) -> list:
    """Every `customTitle` value in the transcript, in file order (dups kept).

    A live Claude session re-writes its in-memory custom-title each turn, so a
    transcript can carry the same title many times and, after an *external*
    rename (this plugin appending a custom-title), Claude's next re-emit puts the
    OLD title back as the last line. `session_name()` (last-wins) would then read
    the stale name. The explorer uses this full history to record those prior
    titles as "shadows" so a later re-emit can't revert a rename. See
    `index.set_name` / `index.record_session`.
    """
    titles = []
    for msg in _iter_messages(path):
        if msg.get("type") == "custom-title":
            v = msg.get("customTitle")
            if v:
                titles.append(v)
    return titles


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


def latest_model(path: str) -> Optional[str]:
    """Model id from the latest assistant message that carries a real one.

    `message.model` is present on every assistant line (e.g. "claude-opus-4-8").
    Skips "<synthetic>" (injected, non-model lines). Returns None when no
    assistant message records a model. Used to pick the context-window
    denominator (see index._context_window).
    """
    last = None
    for msg in _iter_messages(path):
        if msg.get("type") == "assistant":
            model = (msg.get("message") or {}).get("model")
            if model and model != "<synthetic>":
                last = model
    return last


def last_active_at(path: str) -> Optional[str]:
    """ISO8601 timestamp of the LAST line that carries one; None if missing/empty."""
    last = None
    for msg in _iter_messages(path):
        ts = msg.get("timestamp")
        if ts:
            last = ts
    return last


def session_cwd(path: str) -> Optional[str]:
    """Return the FIRST `cwd` value found in any envelope line of the JSONL.

    Used by `index --backfill` to recover the original project directory for
    sessions whose hook-payload `cwd` was never recorded (i.e. sessions that
    pre-date the plugin install). Returns None if no line carries a cwd.
    """
    for msg in _iter_messages(path):
        v = msg.get("cwd")
        if v:
            return v
    return None
