"""Live full-text search over session transcripts (spec 2026-07-01).

Textual-free so it unit-tests without a UI. Reads JSONL bodies at search time —
no sidecar, no index. Extraction mirrors summary.build_digest: user/assistant
text only, tool/thinking/system dropped.
"""
import os
import re

from . import jsonl as _jsonl

SNIPPET_WIDTH = 64              # context chars around a match (fits one line
                               # after the ~10-char role indent in the panel)
MAX_SNIPPETS_PER_SESSION = 5   # cap snippets shown per session

_WS = re.compile(r"\s+")


def iter_text_messages(path):
    """Yield (role, text) for user/assistant messages. Skips tool_use,
    tool_result, thinking, snapshots, and system lines."""
    for msg in _jsonl._iter_messages(path):
        t = msg.get("type")
        if t not in ("user", "assistant"):
            continue
        content = (msg.get("message") or {}).get("content")
        if t == "user" and isinstance(content, str):
            if content.strip():
                yield ("user", content)
            continue
        if isinstance(content, list):
            for item in content:
                if (isinstance(item, dict) and item.get("type") == "text"
                        and item.get("text")):
                    yield (t, item["text"])


def _window(norm, start, end, width=SNIPPET_WIDTH):
    """Return (snippet, rel_start, rel_end): a ~width-char window of `norm`
    centred on [start:end], '…' where clipped, match offset within the snippet."""
    pad = max(0, (width - (end - start)) // 2)
    a = max(0, start - pad)
    b = min(len(norm), end + pad)
    prefix = "…" if a > 0 else ""
    suffix = "…" if b < len(norm) else ""
    snippet = prefix + norm[a:b] + suffix
    rs = len(prefix) + (start - a)
    return snippet, rs, rs + (end - start)


def search_transcript(path, needle):
    """Case-insensitive substring search over user/assistant text. Returns one
    hit per matching message (first match): {role, snippet, match_start,
    match_end}."""
    needle_l = (needle or "").lower()
    if not needle_l:
        return []
    hits = []
    for role, text in iter_text_messages(path):
        norm = _WS.sub(" ", text).strip()
        idx = norm.lower().find(needle_l)
        if idx == -1:
            continue
        snippet, rs, re_ = _window(norm, idx, idx + len(needle_l))
        hits.append({"role": role, "snippet": snippet,
                     "match_start": rs, "match_end": re_})
    return hits


def search_project(rows, needle, *, include_unnamed,
                   max_snippets=MAX_SNIPPETS_PER_SESSION, progress=None):
    """Search every session in one project. `rows` is (sid, s) pairs. Returns
    result dicts for sessions with >=1 hit, newest first."""
    candidates = []
    for sid, s in rows:
        if not include_unnamed and not s.get("name_cached"):
            continue
        path = s.get("transcript_path")
        if not path or not os.path.exists(path):
            continue
        candidates.append((sid, s, path))
    total = len(candidates)
    results = []
    for i, (sid, s, path) in enumerate(candidates, 1):
        hits = search_transcript(path, needle)
        if hits:
            results.append({
                "sid": sid,
                "name": s.get("name_cached") or "(unnamed)",
                "last_active_at": s.get("last_active_at") or "",
                "hit_count": len(hits),
                "snippets": hits[:max_snippets],
                "overflow": max(0, len(hits) - max_snippets),
            })
        if progress is not None:
            progress(i, total)
    results.sort(key=lambda r: r["last_active_at"], reverse=True)
    return results


def _highlight(snippet, start, end):
    from rich.markup import escape
    return (escape(snippet[:start]) + "[reverse]" + escape(snippet[start:end])
            + "[/reverse]" + escape(snippet[end:]))


def format_session(result, needle):
    """Rich markup for one session result, in three visual tiers so the card
    scans cleanly: a title line (accent ▌ marker + bold name), a dim metadata
    line (hit count · date), then the snippet lines (speaker-coloured role label
    + normal-weight text with the match reverse-highlighted)."""
    from rich.markup import escape
    hits = result["hit_count"]
    plural = "hit" if hits == 1 else "hits"
    when = (result.get("last_active_at") or "")[:10]
    # Tier 1 — title. Tier 2 — metadata on its own line (never wraps mid-phrase).
    lines = [f"[yellow]▌[/yellow] [b]{escape(result['name'])}[/b]"]
    meta = f"{hits} {plural}"
    if when:
        meta += f" · {when}"
    lines.append(f"  [dim]{meta}[/dim]")
    # Tier 3 — content. Role labels are colour-coded and padded so snippets align.
    for h in result["snippets"]:
        if h["role"] == "user":
            role = "[green]you   [/green]"
        else:
            role = "[cyan]claude[/cyan]"
        body = _highlight(h["snippet"], h["match_start"], h["match_end"])
        lines.append(f"  {role}  {body}")
    if result["overflow"]:
        lines.append(f"  [dim]+{result['overflow']} more[/dim]")
    return "\n".join(lines)


def empty_state(needle, project_label, searched, include_unnamed):
    from rich.markup import escape
    toggle = "on" if include_unnamed else "off"
    return (f"[dim]No matches for[/dim] '{escape(needle)}' [dim]in[/dim] "
            f"{escape(project_label)} [dim]({searched} sessions searched, "
            f"unnamed {toggle}).[/dim]")
