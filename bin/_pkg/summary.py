"""Session-summary sidecar store + transcript digest + consent markers.

Schema: {"version": 1, "summaries": {sid: {text, generated_at, msg_count, model}}}

Concurrency mirrors folder_store.py: read under LOCK_SH; every mutate takes
LOCK_EX on a sibling .lock file plus a temp-file + atomic rename. No Textual and
no subprocess here — the TUI worker and gc both import this module.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from typing import Callable, Optional

from . import jsonl as _jsonl

MAX_DIGEST_CHARS = 48000


def default_path_for(index_path: str) -> str:
    d = os.path.dirname(os.path.abspath(index_path)) or "."
    return os.path.join(d, "session-explorer-summaries.json")


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"version": 1, "summaries": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "summaries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("summaries"), dict):
        return {"version": 1, "summaries": {}}
    return data


def save(path: str, data: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-summaries-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def mutate(path: str, fn: Callable[[dict], dict]) -> dict:
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)) or ".", exist_ok=True)
    with open(lock_path, "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            data = load(path)
            data = fn(data)
            save(path, data)
            return data
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def get(path: str, sid: str) -> Optional[dict]:
    return load(path).get("summaries", {}).get(sid)


def set(path: str, sid: str, entry: dict) -> None:
    def fn(data: dict) -> dict:
        data.setdefault("summaries", {})[sid] = entry
        return data
    mutate(path, fn)


def remove(path: str, sid: str) -> None:
    def fn(data: dict) -> dict:
        data.get("summaries", {}).pop(sid, None)
        return data
    mutate(path, fn)


def is_stale(entry: dict, current_msg_count: int) -> bool:
    return current_msg_count > int(entry.get("msg_count") or 0)


def build_digest(transcript_path: str, *, max_chars: int = MAX_DIGEST_CHARS) -> str:
    """Distill a JSONL transcript into readable text: user text turns and
    assistant text blocks only. Drops tool results, snapshots, thinking, and
    non-message line types. Over `max_chars`, keep head + tail with a middle
    elision (start frames intent, end frames outcome)."""
    parts: list[str] = []
    for msg in _jsonl._iter_messages(transcript_path):
        t = msg.get("type")
        if t == "user":
            content = msg.get("message", {}).get("content")
            text = None
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text")
                        break
            if text:
                parts.append("USER: " + text.strip())
        elif t == "assistant":
            content = msg.get("message", {}).get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                        parts.append("ASSISTANT: " + item["text"].strip())
    digest = "\n\n".join(parts)
    if len(digest) <= max_chars:
        return digest
    half = max_chars // 2
    return digest[:half] + "\n\n…\n\n" + digest[-half:]


def auto_marker(claude_dir: str) -> str:
    return os.path.join(claude_dir, ".session-explorer.summaries-auto")


def auto_enabled(claude_dir: str) -> bool:
    return os.path.exists(auto_marker(claude_dir))


def set_auto(claude_dir: str, on: bool) -> None:
    os.makedirs(claude_dir, exist_ok=True)
    m = auto_marker(claude_dir)
    if on:
        open(m, "a").close()
    elif os.path.exists(m):
        os.unlink(m)


def prompted_marker(claude_dir: str) -> str:
    return os.path.join(claude_dir, ".session-explorer.summaries-prompted")


def prompted(claude_dir: str) -> bool:
    return os.path.exists(prompted_marker(claude_dir))


def mark_prompted(claude_dir: str) -> None:
    os.makedirs(claude_dir, exist_ok=True)
    open(prompted_marker(claude_dir), "a").close()
