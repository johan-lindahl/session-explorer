"""Folder expiry rules (SPEC: "Folder expiry rules (`t`)"). Pure, Textual-free.

A rule sits on a folder path (or "" = the project node) inside the folder
store's ``rules`` map, keyed by project root:

    {"expire_days": N}   delete sessions idle for more than N days
    {"keep": true}       keep forever (overrides an inherited expiry)

A session's rule is the nearest one walking from its folder up to the project
node; no rule anywhere means "keep forever" (the pre-rules behaviour).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .tree_model import session_root, split_path

WARN_SECONDS = 48 * 3600     # last-warning window: AGE cell renders dim-red


def project_rules(folder_store_data: dict, project: str) -> Dict[str, dict]:
    rules = (folder_store_data.get("rules") or {}).get(project) or {}
    return {k: v for k, v in rules.items() if valid_rule(v)}


def valid_rule(rule) -> bool:
    if not isinstance(rule, dict):
        return False
    if rule.get("keep") is True:
        return True
    d = rule.get("expire_days")
    return isinstance(d, int) and not isinstance(d, bool) and d >= 1


def resolve(rules: Dict[str, dict], segments: List[str]) -> Tuple[Optional[dict], Optional[str]]:
    """(rule, source_folder) for a folder path; source "" is the project node.
    (None, None) when nothing applies."""
    for i in range(len(segments), -1, -1):
        key = "/".join(segments[:i])
        if key in rules:
            return rules[key], key
    return None, None


def idle_reference(entry: dict) -> Optional[datetime]:
    """When the session was last active: the later of last_active_at and the
    transcript's mtime (guards against an index restored with stale values)."""
    best = None
    la = entry.get("last_active_at")
    if la:
        try:
            best = datetime.fromisoformat(la.replace("Z", "+00:00"))
            if best.tzinfo is None:
                best = best.replace(tzinfo=timezone.utc)
        except ValueError:
            best = None
    tp = entry.get("transcript_path")
    if tp:
        try:
            m = datetime.fromtimestamp(os.path.getmtime(tp), tz=timezone.utc)
            if best is None or m > best:
                best = m
        except OSError:
            pass
    return best


def status(entry: dict, rules: Dict[str, dict], now: datetime) -> dict:
    """Expiry status of one session:
    {"kind": "none"|"keep"|"expires", "source": folder|None,
     "days": N|None, "remaining": seconds|None}
    Unnamed sessions are always "none" (they have no folder; unnamed
    retention governs them)."""
    name = entry.get("name_cached")
    if not name:
        return {"kind": "none", "source": None, "days": None, "remaining": None}
    segments, _ = split_path(name)
    rule, source = resolve(rules, segments)
    if rule is None:
        return {"kind": "none", "source": None, "days": None, "remaining": None}
    if rule.get("keep"):
        return {"kind": "keep", "source": source, "days": None, "remaining": None}
    days = rule["expire_days"]
    ref = idle_reference(entry)
    remaining = None
    if ref is not None:
        remaining = ref.timestamp() + days * 86400 - now.timestamp()
    return {"kind": "expires", "source": source, "days": days, "remaining": remaining}


def is_expired(st: dict) -> bool:
    return st["kind"] == "expires" and st["remaining"] is not None and st["remaining"] <= 0


def source_label(source: Optional[str], project_label: str = "") -> str:
    if source is None:
        return ""
    return f"{source}/" if source else (project_label or "project")


def rule_label(rule: Optional[dict]) -> str:
    if not rule:
        return "keep forever (no rule)"
    if rule.get("keep"):
        return "keep forever"
    return f"delete after {rule['expire_days']}d idle"


def badge(rule: Optional[dict]) -> str:
    """Short folder-row badge for a rule set directly on that folder."""
    if not rule:
        return ""
    return "∞" if rule.get("keep") else f"⏱{rule['expire_days']}d"


def fmt_remaining(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    if seconds <= 0:
        return "now"
    days = int(seconds // 86400)
    if days >= 1:
        return f"{days}d"
    hours = int(seconds // 3600)
    return f"{hours}h" if hours >= 1 else "<1h"


def preview_line(st: dict, project_label: str = "") -> str:
    """Plain text for the preview's Expires field (caller escapes)."""
    src = source_label(st.get("source"), project_label)
    if st["kind"] == "none":
        return "never (no rule)"
    if st["kind"] == "keep":
        return f"never ({src})"
    if st.get("skip_reason"):
        return f"expired — kept: {st['skip_reason']}"
    when = fmt_remaining(st["remaining"])
    head = "on next pass" if when == "now" else f"in {when}"
    return f"{head} ({src} · {st['days']}d idle)"


def expired_sessions(index_data: dict, folder_store_data: dict, now: datetime,
                     project: Optional[str] = None) -> List[Tuple[str, dict, dict]]:
    """[(sid, entry, status)] for every named session past its rule's expiry
    (optionally restricted to one project root). No liveness/dirt checks —
    callers apply those."""
    out = []
    cache: Dict[str, Dict[str, dict]] = {}
    for sid, entry in index_data.get("sessions", {}).items():
        root = session_root(entry)
        if project is not None and root != project:
            continue
        rules = cache.get(root)
        if rules is None:
            rules = cache[root] = project_rules(folder_store_data, root)
        if not rules:
            continue
        st = status(entry, rules, now)
        if is_expired(st):
            out.append((sid, entry, st))
    return out
