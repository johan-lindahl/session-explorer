"""Display-ready snapshot of all configured shared-resource queues.

Pure assembler for the Queues pane (spec §6/§9): reads the Phase-1 stores
(queue_config / queue_store) plus the live registry, and returns one row per
configured resource across every opted-in project. The TUI renders these rows
verbatim — no Textual import here so the logic is unit-tested in isolation
(mirrors tree_model.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from . import exclusive as _exclusive
from . import index as _index
from . import queue_config as _qc
from . import queue_run as _qr
from . import queue_store as _qs


def fmt_elapsed(seconds: float) -> str:
    """'M:SS' (or 'H:MM:SS' past an hour) — matches the pane mockups (0:42)."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _elapsed(created: Optional[str], now: datetime) -> str:
    dt = _parse_iso(created)
    if dt is None:
        return "0:00"
    return fmt_elapsed((now - dt).total_seconds())


def _session_names(index_path: Optional[str]) -> dict:
    """sid -> cached session name, for the holder/waiter display. Best-effort:
    a missing/unreadable index just yields no names (callers fall back)."""
    if not index_path:
        return {}
    try:
        data = _index.load(index_path)
    except Exception:
        return {}
    return {sid: (s.get("name_cached") or None)
            for sid, s in data.get("sessions", {}).items()}


def _holder_name(sid: str, names: dict) -> str:
    """The human-readable session name for a ticket; a short sid when the
    session is unnamed or absent from the index. NEVER the project/resource
    label — that's already the row identity and identical for every ticket."""
    return names.get(sid) or sid[:8]


def snapshot(config_path: str, queues_root: str, live_path: str, *,
             index_path: Optional[str] = None,
             now: Optional[datetime] = None) -> List[dict]:
    """One row per configured resource. Each row:
      {id, project_id, project, resource, kind,
       holder: {sid,name,label,elapsed}|None,
       waiting: [{sid,name,label,pos}],   # pos = "N of M" among waiters
       live_root_block: {sid,cwd,name}|None,   # root-dir only
       active: bool}                 # holder/waiters/block present

    `name` is the holding session's title (resolved via `index_path`); `label`
    is the legacy project/resource string kept for any other consumer.
    """
    now = now or datetime.now(timezone.utc)
    names = _session_names(index_path)
    rows: List[dict] = []
    for pid, proj in _qc.all_projects(config_path).items():
        display = proj.get("display_path", pid)
        for rid, res in proj.get("resources", {}).items():
            qdir = _qr.queue_dir(queues_root, pid, rid)
            tickets = _qs.list_tickets(qdir)
            holder = None
            waiting = []
            if tickets:
                h = tickets[0]
                holder = {"sid": h["sid"], "name": _holder_name(h["sid"], names),
                          "label": h.get("label", h["sid"]),
                          "elapsed": _elapsed(h.get("created"), now)}
                waiters = tickets[1:]
                total = len(waiters)
                for i, t in enumerate(waiters, start=1):
                    waiting.append({"sid": t["sid"],
                                    "name": _holder_name(t["sid"], names),
                                    "label": t.get("label", t["sid"]),
                                    "pos": f"{i} of {total}"})
            block = None
            if res.get("kind") == "root-dir" and res.get("path"):
                block = _exclusive.live_root_session(live_path, res["path"], now=now)
            rows.append({
                "id": f"{pid}/{rid}",
                "project_id": pid,
                "project": display,
                "resource": rid,
                "kind": res.get("kind"),
                "holder": holder,
                "waiting": waiting,
                "live_root_block": block,
                "active": bool(holder or waiting or block),
            })
    return rows
