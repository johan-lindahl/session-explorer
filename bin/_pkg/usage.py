"""Subscription usage bar: scrape Claude Code's /usage panel and render a small
status-line bar. Pure parse/render logic is unit-tested; the timing-dependent
scrape coordinator at the bottom is thin and verified manually (see the
2026-06-02 usage-bar plan, M0)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# A clock time like "1:29am" / "12:00pm". Anchored to am/pm so the weekly bucket's
# date-style reset ("Jun 9, 12:00pm") still matches as a time, and so plain
# numbers in the bar can't be mistaken for a reset.
_TIME = re.compile(r"(\d{1,2}:\d{2}\s*[apAP][mM])")
_PERCENT = re.compile(r"(\d{1,3})\s*%\s*used", re.IGNORECASE)


@dataclass
class UsageInfo:
    percent: int
    reset_label: str


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _session_region(text: str) -> str:
    """Text from the 'current session' line onward, so the FIRST percent/reset we
    grab is the session bucket and not a weekly one listed below it. Falls back to
    the whole text when no explicit session header is present."""
    m = re.search(r"current session", text, re.IGNORECASE)
    return text[m.start():] if m else text


def parse_usage(captured_text: str) -> Optional[UsageInfo]:
    """Parse the session %-used and reset time out of a captured /usage panel.
    Returns None on any miss; never raises."""
    if not captured_text:
        return None
    text = _strip_ansi(captured_text)
    region = _session_region(text)
    pm = _PERCENT.search(region)
    tm = _TIME.search(region)
    if not pm or not tm:
        return None
    percent = max(0, min(100, int(pm.group(1))))
    reset_label = re.sub(r"\s+", "", tm.group(1)).lower()
    return UsageInfo(percent=percent, reset_label=reset_label)


FILL = "█"
EMPTY = "░"
CELLS = 12


def render_bar(info: UsageInfo, cells: int = CELLS) -> str:
    """A compact ` [████░░░░] 18% ↺1:29am ` string for tmux status-left. Single
    colour (v1) so the visible length is predictable for status-left-length."""
    n = max(0, min(cells, round(info.percent / 100 * cells)))
    bar = FILL * n + EMPTY * (cells - n)
    return f" [{bar}] {info.percent}% ↺{info.reset_label}"
