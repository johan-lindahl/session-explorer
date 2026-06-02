"""Subscription usage bar: scrape Claude Code's /usage panel and render a small
status-line bar. Pure parse/render logic is unit-tested; the timing-dependent
scrape coordinator at the bottom is thin and verified manually (see the
2026-06-02 usage-bar plan, M0)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# A clock time like "1:29am" / "12:00pm". Anchored to am/pm so the weekly bucket's
# date-style reset ("Jun 9, 12:00pm") still matches as a time, and so plain
# numbers in the bar can't be mistaken for a reset.
_TIME = re.compile(r"(\d{1,2}:\d{2}\s*[apAP][mM])")
_PERCENT = re.compile(r"(?<![0-9\-])(\d{1,3})\s*%\s*used", re.IGNORECASE)


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
    """A compact ` [████░░░░] 18% ↺1:29am` string for tmux status-left. Single
    colour (v1) so the visible length is predictable for status-left-length."""
    n = max(0, min(cells, round(info.percent / 100 * cells)))
    bar = FILL * n + EMPTY * (cells - n)
    return f" [{bar}] {info.percent}% ↺{info.reset_label}"


# --- coordinator helpers (called by the scrape coordinator in a later task) ---

PROBE_DIRNAME = ".session-explorer-probe"


def probe_cwd(claude_dir: str) -> str:
    """Fixed cwd for the throwaway probe claude, so all probe transcripts land in
    one predictable project folder we can clean up afterward."""
    return os.path.join(claude_dir, PROBE_DIRNAME)


def has_usage_panel(text: str) -> bool:
    return bool(_PERCENT.search(_strip_ansi(text or "")))


def looks_like_trust_prompt(text: str) -> bool:
    return "trust the files in this folder" in (text or "").lower()


import glob
import time

from . import tmux as _tmux

READY_TIMEOUT = 20.0   # seconds to wait for claude's prompt / trust dialog
PANEL_TIMEOUT = 10.0   # seconds to wait for the /usage panel to render
POLL_STEP = 0.5


def _wait_for(target: str, predicate, timeout: float,
              *, on_trust=None) -> bool:
    """Poll capture-pane until `predicate(text)` is true or timeout. If a trust
    prompt appears meanwhile, call `on_trust` once to dismiss it."""
    deadline = time.monotonic() + timeout
    trusted = False
    while time.monotonic() < deadline:
        text = _tmux.capture_plain(target)
        if not trusted and on_trust is not None and looks_like_trust_prompt(text):
            on_trust()
            trusted = True
            time.sleep(POLL_STEP)
            continue
        if predicate(text):
            return True
        time.sleep(POLL_STEP)
    return False


def cleanup_probe_transcripts(claude_dir: str) -> None:
    """Delete the JSONLs the throwaway probe claude wrote. Globbing by the probe
    dirname is robust to however Claude mangles the cwd into a project folder."""
    # Claude mangles the probe cwd into a project-folder name with non-alphanumerics
    # (incl. the leading dot) turned to dashes, e.g. "...--session-explorer-probe".
    # Match on the dot-less stem so the glob actually hits that folder.
    pattern = os.path.join(
        claude_dir, "projects", "*session-explorer-probe*", "*.jsonl")
    for path in glob.glob(pattern):
        try:
            os.remove(path)
        except OSError:
            pass


def scrape_usage(claude_dir: str, window: str = None) -> Optional[UsageInfo]:
    """Spawn a hidden probe claude, run /usage, capture+parse the panel, then tear
    everything down. Returns None on any failure; never raises."""
    window = window or _tmux.PROBE_WINDOW
    cwd = probe_cwd(claude_dir)
    info: Optional[UsageInfo] = None
    try:
        os.makedirs(cwd, exist_ok=True)
        # Clear any stale probe window from a previously crashed run; a duplicate
        # same-named window makes send-keys/capture/kill ambiguous and would leak
        # an orphaned claude process.
        try:
            _tmux.kill_window(window)
        except Exception:
            pass
        if _tmux.start_probe_window(cwd, window) != 0:
            return None
        # Wait for the input prompt; dismiss the first-run trust dialog if shown.
        ready = _wait_for(
            window,
            lambda t: not looks_like_trust_prompt(t) and len(t.strip()) > 0,
            READY_TIMEOUT,
            on_trust=lambda: _tmux.send_keys(window, "Enter"),
        )
        if not ready:
            return None
        _tmux.send_keys(window, "/usage", "Enter")
        if _wait_for(window, has_usage_panel, PANEL_TIMEOUT):
            info = parse_usage(_tmux.capture_plain(window))
        return info
    except Exception:
        return None
    finally:
        # /usage opens a modal Settings screen ("Esc to cancel"), so dismiss it
        # with Escape rather than typing /exit at a prompt; kill-window is the
        # hard backstop that terminates the throwaway claude either way.
        try:
            _tmux.send_keys(window, "Escape")
            time.sleep(0.2)
        except Exception:
            pass
        try:
            _tmux.kill_window(window)
        except Exception:
            pass
        cleanup_probe_transcripts(claude_dir)
