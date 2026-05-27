"""Pure-Python display formatters shared by the CLI text mode and the TUI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def fmt_tokens(n: int) -> str:
    if n >= 10_000:
        return f"~{n // 1000}K"
    return f"~{n}"


def fmt_age(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    delta = datetime.now(timezone.utc) - ts
    if delta.days >= 1:
        return f"{delta.days}d"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h"
    return f"{delta.seconds // 60}m"


def fmt_pct(pct: int) -> str:
    return f"({pct}%)"
