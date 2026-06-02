# tmux Subscription Usage Bar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a small `[████░░░░░░] 18% ↺1:29am` progress bar of the current 5-hour subscription usage in the session-explorer tmux status line, refreshed every 5 minutes, toggled on/off with the `g` key (off by default).

**Architecture:** Because the usage % exists only in Anthropic's live response headers (no local file, no `claude usage` subcommand), we **scrape the official client**: a Textual thread-worker periodically spawns a throwaway `claude` in a hidden tmux window on the dedicated `-L session-explorer` server, drives `/usage` via `send-keys`, reads the rendered panel with `capture-pane`, parses out `% used` + reset time, exits and kills the window, cleans up the probe transcript, and pushes the rendered bar into `status-left` via `tmux set`. Pure parse/render logic lives in a new `usage.py` and is unit-tested against captured fixtures; the timing-dependent orchestration is thin and verified manually.

**Tech Stack:** Python 3.11+, vendored Textual (`@work(thread=True)` workers, `set_interval`), tmux ≥3.1 CLI (subprocess), bash hooks. Tests: pytest (`test/`), bats (`test/hook.bats`).

---

## Background for the implementer (read once)

- **The dedicated tmux server** is reached via `tmux -L session-explorer …`. All argv is built by pure `build_*` functions in `bin/_pkg/tmux.py` and run through thin `_call`/`_capture` wrappers at the bottom of that file. Follow that split exactly: new argv → a `build_*` function (unit-tested) + a thin wrapper.
- **The TUI** is `bin/_pkg/tui.py`. It already runs periodic work via `self.set_interval(seconds, method)` (see `on_mount`, line ~700) and off-loads blocking work with `@work(thread=True, exclusive=True, group=…)` workers that marshal results back with `self.call_from_thread(...)` (see `_refresh_live_metadata`, line ~1537). Mirror that pattern — never block the UI thread.
- **`SESSION_EXPLORER_TMUX=1`** is set by the launcher only when the explorer is tmux-hosted; `self._tmux_enabled` (line ~597) reflects it. The usage bar is inert when this is false.
- **Hooks** (`hooks/session-start.sh`, `hooks/session-live.sh`) fire for *every* claude start, including our probe. They inherit the probe process's environment, so an env var set on the probe window lets them bail out. They must "never block; exit 0".
- **`_claude_dir()`** (tui.py line ~663) returns the dir holding the index (normally `~/.claude`). Probe/marker paths hang off it.
- **Run tests** with `python3 -m pytest test/ -q` (Textual is vendored; nothing to install). Bats: `bats test/hook.bats`.

## File Structure

| File | Create/Modify | Responsibility |
|------|---------------|----------------|
| `bin/_pkg/usage.py` | **Create** | Pure parse (`parse_usage`) + render (`render_bar`) + readiness predicates + the thin `scrape_usage` coordinator and transcript cleanup. |
| `test/fixtures/usage_panel.txt` | **Create** (in M0) | Real captured `/usage` panel text, the fixture the parser is tested against. |
| `test/test_usage.py` | **Create** | Unit tests for `parse_usage`, `render_bar`, predicates. |
| `bin/_pkg/tmux.py` | **Modify** | Add `build_probe_window`, `build_send_keys`, `build_capture_plain`, `build_set_status_left` builders + thin wrappers; add `status-left-length` to `build_config`. |
| `test/test_tmux.py` | **Modify** | Assert the new argv builders + the config line. |
| `hooks/session-start.sh` | **Modify** | Bail out early when `SESSION_EXPLORER_PROBE=1`. |
| `hooks/session-live.sh` | **Modify** | Same probe bail-out. |
| `test/hook.bats` | **Modify** | Assert the probe-skip behaviour. |
| `bin/_pkg/tui.py` | **Modify** | `g` binding, marker file, start/stop/refresh wiring, on_mount autostart, check_action gate, quit clear. |
| `SPEC.md` | **Modify** | Document the usage-bar segment, probe mechanism, `SESSION_EXPLORER_PROBE` gate, opt-in marker. |
| `README.md` | **Modify** | Mention the `g` usage bar. |
| `.claude-plugin/plugin.json`, `bin/_pkg/__init__.py` | **Modify** | Version bump to 1.8.0. |

---

## Task 0 (M0): Feasibility probe — capture a real `/usage` panel

> **STATUS: SATISFIED (2026-06-02).** A real `/usage` capture confirmed the
> approach: `/usage` opens directly on the Usage tab and renders inline as plain
> text (no navigation), the `Current session` / `NN% used` / `Resets H:MMam (TZ)`
> lines are present, and a fresh probe claude reports the true account 5-hour
> window. Dismissal is `Escape` (modal Settings screen). The fixture
> `test/fixtures/usage_panel.txt` is already saved. Steps below are retained as
> the reproduction record; you may skip straight to Task 1.

**This is a manual investigation gate, not automated. Everything downstream parses what this produces. If `/usage` needs interactive navigation we can't script, STOP and report back before building further.**

**Files:**
- Create: `test/fixtures/usage_panel.txt`

- [ ] **Step 1: Confirm tmux + a logged-in claude are available**

Run:
```bash
tmux -V && command -v claude && echo "ok"
```
Expected: a tmux version ≥ 3.1 and a claude path. (You must be logged into a Pro/Max subscription for `/usage` to show data.)

- [ ] **Step 2: Spawn a throwaway claude in a hidden probe window and drive `/usage` by hand**

Run, one block, pausing where noted:
```bash
mkdir -p "$HOME/.claude/.session-explorer-probe"
tmux -L session-explorer new-window -d -n se-usage-probe \
  -c "$HOME/.claude/.session-explorer-probe" 'SESSION_EXPLORER_PROBE=1 exec claude'
sleep 6
# If a "Do you trust the files in this folder?" prompt is up, accept the default:
tmux -L session-explorer capture-pane -p -t se-usage-probe | tail -20
```
If you see a trust prompt in that output, run:
```bash
tmux -L session-explorer send-keys -t se-usage-probe Enter
sleep 3
```
Then drive `/usage`:
```bash
tmux -L session-explorer send-keys -t se-usage-probe "/usage" Enter
sleep 4
tmux -L session-explorer capture-pane -p -t se-usage-probe
```

- [ ] **Step 3: Record findings**

Confirm the capture contains a session percentage (`NN% used` or similar) and a reset time (`Resets H:MMam (TZ)`). Note in the commit message:
- the exact wording/layout of the session line and reset line,
- whether a trust prompt appeared and which key dismissed it,
- whether `/usage` rendered inline (good) or required arrow-key navigation (bad → STOP).

- [ ] **Step 4: Save the capture as the test fixture**

Save the **session-relevant** portion of the capture (and a plausible weekly section if present) to `test/fixtures/usage_panel.txt`. It should look approximately like:
```
Current session
████░░░░░░░░░░░░░░░░ 18% used
Resets 1:29am (Europe/Stockholm)

Current week (all models)
██████░░░░░░░░░░░░░░ 31% used
Resets Jun 9, 12:00pm
```
**If the real wording differs, save the REAL text** — Task 1's regex is written against this fixture, so it must be authentic.

- [ ] **Step 5: Tear down and clean up**

```bash
tmux -L session-explorer send-keys -t se-usage-probe "/exit" Enter
sleep 1
tmux -L session-explorer kill-window -t se-usage-probe 2>/dev/null
rm -f "$HOME/.claude/projects/"*session-explorer-probe*/*.jsonl
tmux -L session-explorer list-windows
```
Expected: `se-usage-probe` is gone; no probe transcripts remain.

- [ ] **Step 6: Commit the fixture**

```bash
git add test/fixtures/usage_panel.txt
git commit -m "test(usage): capture real /usage panel as parser fixture (M0)"
```

---

## Task 1: `parse_usage` — pull % + reset from captured text

**Files:**
- Create: `bin/_pkg/usage.py`
- Test: `test/test_usage.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_usage.py`:
```python
import os
from _pkg import usage

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "usage_panel.txt")


def _panel() -> str:
    with open(FIX, encoding="utf-8") as f:
        return f.read()


def test_parse_usage_reads_session_percent_and_reset():
    info = usage.parse_usage(_panel())
    assert info is not None
    assert info.percent == 23                 # the SESSION bucket (first/anchored)
    assert info.reset_label == "1:29am"


def test_parse_usage_strips_ansi_escapes():
    raw = "Current session\n\x1b[34m███\x1b[0m 42% used\nResets 9:05pm (UTC)\n"
    info = usage.parse_usage(raw)
    assert info.percent == 42
    assert info.reset_label == "9:05pm"


def test_parse_usage_returns_none_when_no_percent():
    assert usage.parse_usage("just some unrelated text") is None


def test_parse_usage_returns_none_on_empty():
    assert usage.parse_usage("") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest test/test_usage.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.usage'`.

- [ ] **Step 3: Write the minimal implementation**

Create `bin/_pkg/usage.py`:
```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest test/test_usage.py -q`
Expected: PASS (4 passed). If `test_parse_usage_reads_session_percent_and_reset` fails because your real M0 fixture has different numbers, update the asserted `percent`/`reset_label` to match the fixture.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/usage.py test/test_usage.py
git commit -m "feat(usage): parse session %-used and reset time from /usage capture"
```

---

## Task 2: `render_bar` — build the status-line string

**Files:**
- Modify: `bin/_pkg/usage.py`
- Test: `test/test_usage.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_usage.py`:
```python
def test_render_bar_fills_proportionally():
    info = usage.UsageInfo(percent=50, reset_label="1:29am")
    # 50% of 12 cells = 6 filled
    assert usage.render_bar(info, cells=12) == " [██████░░░░░░] 50% ↺1:29am"


def test_render_bar_zero_and_full():
    assert usage.render_bar(usage.UsageInfo(0, "9:00am"), cells=10) == \
        " [░░░░░░░░░░] 0% ↺9:00am"
    assert usage.render_bar(usage.UsageInfo(100, "9:00am"), cells=10) == \
        " [██████████] 100% ↺9:00am"


def test_render_bar_clamps_rounding():
    # 99% of 10 cells rounds to 10 filled but must not exceed cells
    s = usage.render_bar(usage.UsageInfo(99, "9:00am"), cells=10)
    assert s.count("█") <= 10
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_usage.py -q`
Expected: FAIL — `AttributeError: module '_pkg.usage' has no attribute 'render_bar'`.

- [ ] **Step 3: Implement**

Append to `bin/_pkg/usage.py` (below `parse_usage`):
```python
FILL = "█"
EMPTY = "░"
CELLS = 12


def render_bar(info: UsageInfo, cells: int = CELLS) -> str:
    """A compact ` [████░░░░] 18% ↺1:29am ` string for tmux status-left. Single
    colour (v1) so the visible length is predictable for status-left-length."""
    n = max(0, min(cells, round(info.percent / 100 * cells)))
    bar = FILL * n + EMPTY * (cells - n)
    return f" [{bar}] {info.percent}% ↺{info.reset_label}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest test/test_usage.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/usage.py test/test_usage.py
git commit -m "feat(usage): render the status-line progress bar"
```

---

## Task 3: Readiness predicates + probe paths

**Files:**
- Modify: `bin/_pkg/usage.py`
- Test: `test/test_usage.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_usage.py`:
```python
def test_has_usage_panel_detects_percent_line():
    assert usage.has_usage_panel("blah 18% used blah") is True
    assert usage.has_usage_panel("welcome to claude") is False


def test_looks_like_trust_prompt():
    assert usage.looks_like_trust_prompt(
        "Do you trust the files in this folder?") is True
    assert usage.looks_like_trust_prompt("normal prompt") is False


def test_probe_cwd_under_claude_dir():
    assert usage.probe_cwd("/home/x/.claude") == \
        "/home/x/.claude/.session-explorer-probe"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_usage.py -q`
Expected: FAIL — `AttributeError: ... 'has_usage_panel'`.

- [ ] **Step 3: Implement**

Append to `bin/_pkg/usage.py`:
```python
import os

PROBE_DIRNAME = ".session-explorer-probe"


def probe_cwd(claude_dir: str) -> str:
    """Fixed cwd for the throwaway probe claude, so all probe transcripts land in
    one predictable project folder we can clean up afterward."""
    return os.path.join(claude_dir, PROBE_DIRNAME)


def has_usage_panel(text: str) -> bool:
    return bool(_PERCENT.search(_strip_ansi(text or "")))


def looks_like_trust_prompt(text: str) -> bool:
    return "trust the files in this folder" in (text or "").lower()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest test/test_usage.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/usage.py test/test_usage.py
git commit -m "feat(usage): readiness predicates and probe cwd helper"
```

---

## Task 4: tmux argv builders + status-left-length

**Files:**
- Modify: `bin/_pkg/tmux.py`
- Test: `test/test_tmux.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_tmux.py`:
```python
def test_build_probe_window_sets_env_and_cwd():
    argv = tmux.build_probe_window("/home/x/.claude/.session-explorer-probe")
    assert argv == [
        "tmux", "-L", "session-explorer", "new-window", "-d",
        "-n", "se-usage-probe", "-c", "/home/x/.claude/.session-explorer-probe",
        "SESSION_EXPLORER_PROBE=1 exec claude",
    ]


def test_build_send_keys_passes_keys_through():
    assert tmux.build_send_keys("se-usage-probe", "/usage", "Enter") == [
        "tmux", "-L", "session-explorer", "send-keys", "-t", "se-usage-probe",
        "/usage", "Enter",
    ]


def test_build_capture_plain_has_no_escapes_flag():
    assert tmux.build_capture_plain("se-usage-probe") == [
        "tmux", "-L", "session-explorer", "capture-pane", "-p",
        "-t", "se-usage-probe",
    ]


def test_build_set_status_left():
    assert tmux.build_set_status_left(" [██] 1% ↺1am") == [
        "tmux", "-L", "session-explorer", "set-option", "-g",
        "status-left", " [██] 1% ↺1am",
    ]


def test_build_config_sets_status_left_length():
    cfg = tmux.build_config(persist_flag_path="/tmp/flag")
    assert "set -g status-left-length 40" in cfg
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: FAIL — `AttributeError: module '_pkg.tmux' has no attribute 'build_probe_window'`.

- [ ] **Step 3: Implement the builders**

In `bin/_pkg/tmux.py`, add a constant beside the others near the top (after `DOCK_PCT = 65` on line ~21):
```python
PROBE_WINDOW = "se-usage-probe"  # hidden window for the usage-bar scrape
```

Add these builders next to the other `build_*` functions (e.g. after `build_capture`, line ~120):
```python
def build_probe_window(cwd: str, window: str = PROBE_WINDOW) -> List[str]:
    """Detached hidden window running a throwaway claude for the /usage scrape.
    SESSION_EXPLORER_PROBE=1 is set on the claude process so the SessionStart /
    lifecycle hooks bail out and leave no index/registry trace."""
    return build_base() + [
        "new-window", "-d", "-n", window, "-c", cwd,
        "SESSION_EXPLORER_PROBE=1 exec claude"]


def build_send_keys(target: str, *keys: str) -> List[str]:
    return build_base() + ["send-keys", "-t", target, *keys]


def build_capture_plain(target: str) -> List[str]:
    """Plain (no -e) capture: easier to regex than colour-escaped output."""
    return build_base() + ["capture-pane", "-p", "-t", target]


def build_set_status_left(text: str) -> List[str]:
    return build_base() + ["set-option", "-g", "status-left", text]
```

Add the `status-left-length` line in `build_config` — change the existing `'set -g status-left ""',` line (line ~162) so the list reads:
```python
        'set -g status-left ""',
        "set -g status-left-length 40",
```

- [ ] **Step 4: Add the thin wrappers**

At the bottom of `bin/_pkg/tmux.py` (after `capture_pane`, line ~273), add:
```python
def start_probe_window(cwd: str, window: str = PROBE_WINDOW) -> int:
    return _call(build_probe_window(cwd, window))


def send_keys(target: str, *keys: str) -> int:
    return _call(build_send_keys(target, *keys))


def capture_plain(target: str) -> str:
    return _capture(build_capture_plain(target))


def set_status_left(text: str) -> int:
    return _call(build_set_status_left(text))
```

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: PASS (all tmux tests, including the 5 new ones).

- [ ] **Step 6: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): probe-window/send-keys/status-left builders + status-left-length"
```

---

## Task 5: `scrape_usage` coordinator + transcript cleanup

**Files:**
- Modify: `bin/_pkg/usage.py`

This is timing-dependent orchestration (subprocess + sleeps), so — like tmux.py's thin wrappers — it is **not unit-tested**; it is exercised manually in M0/integration. Write the full code; do not leave placeholders.

- [ ] **Step 1: Implement the coordinator**

Append to `bin/_pkg/usage.py`:
```python
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
    pattern = os.path.join(
        claude_dir, "projects", "*" + PROBE_DIRNAME + "*", "*.jsonl")
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
```

- [ ] **Step 2: Verify the module imports cleanly and existing tests still pass**

Run: `python3 -m pytest test/test_usage.py -q && python3 -c "from bin._pkg import usage" 2>/dev/null || python3 -c "import sys; sys.path.insert(0,'bin'); import _pkg.usage"`
Expected: usage tests still PASS (10 passed); the import line prints nothing and exits 0.

- [ ] **Step 3: Commit**

```bash
git add bin/_pkg/usage.py
git commit -m "feat(usage): scrape coordinator (spawn→/usage→capture→teardown→cleanup)"
```

---

## Task 6: Probe hooks bail-out

**Files:**
- Modify: `hooks/session-start.sh`, `hooks/session-live.sh`
- Test: `test/hook.bats`

- [ ] **Step 1: Write the failing bats test**

Append to `test/hook.bats` (follow the existing helper style in that file — set `HOME` to a temp dir, pipe a JSON payload to the hook):
```bash
@test "session-start: probe sessions leave no trace" {
  export SESSION_EXPLORER_PROBE=1
  run bash -c 'echo "{\"session_id\":\"p1\",\"transcript_path\":\"/t/p1.jsonl\",\"cwd\":\"/c\"}" | "'"$BATS_TEST_DIRNAME"'/../hooks/session-start.sh"'
  [ "$status" -eq 0 ]
  [ ! -f "$HOME/.claude/.session-explorer.current" ]
  unset SESSION_EXPLORER_PROBE
}
```
(If the existing tests in `hook.bats` use a `setup()` that exports `HOME` to `$BATS_TMPDIR`, rely on it rather than re-declaring. Match the file's conventions.)

- [ ] **Step 2: Run to verify it fails**

Run: `bats test/hook.bats`
Expected: the new test FAILS — `.session-explorer.current` IS written because the gate doesn't exist yet.

- [ ] **Step 3: Add the gate to `session-start.sh`**

In `hooks/session-start.sh`, immediately after `set -u` (line 8), insert:
```bash

# Usage-bar probe sessions (see usage.py / SESSION_EXPLORER_PROBE) must leave no
# trace: no current-pointer, no index record, no GC. Bail out before any work.
if [ "${SESSION_EXPLORER_PROBE:-}" = "1" ]; then exit 0; fi
```

- [ ] **Step 4: Add the same gate to `session-live.sh`**

In `hooks/session-live.sh`, immediately after `set -u` (line 8), insert:
```bash

# Skip the usage-bar probe entirely (no live-registry churn).
if [ "${SESSION_EXPLORER_PROBE:-}" = "1" ]; then exit 0; fi
```

- [ ] **Step 5: Run to verify it passes**

Run: `bats test/hook.bats`
Expected: PASS (all, including the new probe test).

- [ ] **Step 6: Commit**

```bash
git add hooks/session-start.sh hooks/session-live.sh test/hook.bats
git commit -m "feat(hooks): bail out for SESSION_EXPLORER_PROBE usage-probe sessions"
```

---

## Task 7: Wire the `g` toggle into the TUI

**Files:**
- Modify: `bin/_pkg/tui.py`

No unit test here (Textual app wiring; the pure logic it calls is already tested). Verify by import + a manual smoke check.

- [ ] **Step 1: Add the import and interval constant**

Near the top imports of `bin/_pkg/tui.py` (the `from . import tmux as _tmux` block, line ~23), add:
```python
from . import usage as _usage
```
Near `LIVE_POLL_INTERVAL = 2.0` (line ~46), add:
```python
USAGE_POLL_INTERVAL = 300.0  # seconds between usage-bar refreshes (5 min)
```

- [ ] **Step 2: Add the binding**

In the main app `BINDINGS` list (line ~549), add after the `toggle_unnamed` binding (line ~557):
```python
        Binding("g", "toggle_usage", "Usage bar"),
```

- [ ] **Step 3: Initialise timer state**

In `__init__`, beside `self._sync_timer = None` (line ~607), add:
```python
        # Usage-bar refresh timer (5-min interval); None when the bar is off.
        self._usage_timer = None
```

- [ ] **Step 4: Gate the action in `check_action`**

In `check_action` (line ~609): add `"toggle_usage"` to the modal-guard tuple on line ~613, and add a tmux gate. The method becomes:
```python
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("resume", "rename", "move", "new_folder", "new_session", "delete", "notes", "preview", "close_preview", "filter", "toggle_unnamed", "toggle_usage", "rescan", "help", "expand_node", "collapse_node", "quit") and isinstance(self.screen, ModalScreen):
            return False
        # The usage bar only exists in the tmux-hosted layout.
        if action == "toggle_usage" and not self._tmux_enabled:
            return False
        if action == "quit" and getattr(self, "_filter", None) is not None and self._filter.has_focus:
            return False
        return True
```

- [ ] **Step 5: Add the marker helpers + start/stop/refresh methods**

Add these methods to the app class (e.g. just after `action_toggle_unnamed`, line ~1411):
```python
    def _usage_marker(self) -> str:
        return os.path.join(self._claude_dir(), ".session-explorer.usage-bar")

    def _usage_enabled(self) -> bool:
        return os.path.exists(self._usage_marker())

    def action_toggle_usage(self) -> None:
        if not self._tmux_enabled:
            return
        if self._usage_enabled():
            try:
                os.remove(self._usage_marker())
            except OSError:
                pass
            self._stop_usage()
            self.notify("Usage bar off")
        else:
            with open(self._usage_marker(), "a"):
                pass
            self._start_usage()
            self.notify("Usage bar on — checking…")

    def _start_usage(self) -> None:
        # Enabling fires one probe immediately so the bar appears within seconds;
        # toggling g off then on is therefore the manual "check now" path.
        self._refresh_usage()
        if self._usage_timer is None:
            self._usage_timer = self.set_interval(
                USAGE_POLL_INTERVAL, self._refresh_usage)

    def _stop_usage(self) -> None:
        if self._usage_timer is not None:
            self._usage_timer.stop()
            self._usage_timer = None
        _tmux.set_status_left("")

    @work(thread=True, exclusive=True, group="usage")
    def _refresh_usage(self) -> None:
        """Off-thread: scrape /usage (spawns a hidden claude, ~seconds), then push
        the rendered bar on the UI thread. `exclusive` so a slow scrape can't
        stack across the 5-min interval."""
        info = _usage.scrape_usage(self._claude_dir())
        self.call_from_thread(self._apply_usage, info)

    def _apply_usage(self, info) -> None:
        if info is not None:
            _tmux.set_status_left(_usage.render_bar(info))
        # On a miss we leave the previous bar in place (transient blip) rather
        # than blanking it; an explicit `g` off clears it.
```

- [ ] **Step 6: Autostart on mount when the marker is present**

In `on_mount` (line ~670), after the three `set_interval(...)` calls (line ~703), add:
```python
        # Usage bar: restore it if the user left it enabled, but only in the
        # tmux-hosted layout (it has nowhere to render otherwise).
        if self._tmux_enabled and self._usage_enabled():
            self._start_usage()
```

- [ ] **Step 7: Clear the bar on quit**

In `action_quit` (line ~1390), add as the first statement inside the method:
```python
        if self._tmux_enabled:
            _tmux.set_status_left("")
```

- [ ] **Step 8: Smoke-check the import and full test suite**

Run:
```bash
python3 -c "import sys; sys.path.insert(0,'bin'); import _pkg.tui" && python3 -m pytest test/ -q
```
Expected: import prints nothing (exit 0); the whole pytest suite PASSES.

- [ ] **Step 9: Manual smoke test (tmux-hosted)**

In a real terminal: `/session-explorer:open` (or the dev launcher) so the explorer is tmux-hosted. Press `g`. Expected within ~10s: the bar appears in the bottom-left status line; the F9/F12 hint stays bottom-right. Press `g` again: the bar clears. Then:
```bash
ls "$HOME/.claude/projects/"*session-explorer-probe*/ 2>/dev/null   # should be empty/absent
tmux -L session-explorer list-windows                               # no se-usage-probe lingering
```

- [ ] **Step 10: Commit**

```bash
git add bin/_pkg/tui.py
git commit -m "feat(tui): g toggles the subscription usage bar (off by default)"
```

---

## Task 8: Docs + version bump

**Files:**
- Modify: `SPEC.md`, `README.md`, `.claude-plugin/plugin.json`, `bin/_pkg/__init__.py`

- [ ] **Step 1: Update `SPEC.md` (authoritative)**

Add a subsection (under the TUI / status-line area) documenting:
- The usage bar lives in `status-left`; the F9/F12 hint stays in `status-right`.
- It is **off by default**, toggled by `g`, persisted by the marker file `~/.claude/.session-explorer.usage-bar`. Enabling fires an immediate probe + a 5-min interval; toggling off-then-on is the manual refresh.
- Data source: there is no local cache / `claude usage` subcommand, so the bar is produced by **scraping the official client** — a hidden probe `claude` on the dedicated tmux server runs `/usage`; the panel is `capture-pane`d and parsed (`usage.parse_usage`). Probe sessions carry `SESSION_EXPLORER_PROBE=1` so both hooks bail out, and their transcripts are deleted after each scrape (`cleanup_probe_transcripts`).
- It is inert when not tmux-hosted. Failures degrade silently (bar unchanged), never block the UI.

Add a "Resolved design decisions" line noting session (5-hour) bucket only; the API-replication route was rejected for ban risk.

- [ ] **Step 2: Update `README.md`**

Add `g` to the keybindings list / feature blurb: "`g` — toggle a live subscription-usage bar (5-hour limit) in the tmux status line."

- [ ] **Step 3: Bump the version to 1.8.0**

In `.claude-plugin/plugin.json` change `"version": "1.7.0"` → `"version": "1.8.0"`.
In `bin/_pkg/__init__.py` change `__version__ = "1.7.0"` → `__version__ = "1.8.0"`.

- [ ] **Step 4: Run the full suite once more**

Run: `python3 -m pytest test/ -q && bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add SPEC.md README.md .claude-plugin/plugin.json bin/_pkg/__init__.py
git commit -m "docs: document the usage bar; bump to v1.8.0"
```

---

## Self-review notes (already reconciled against the spec)

- **Data-source / scrape mechanism** → Tasks 0, 5. **5-min cadence** → `USAGE_POLL_INTERVAL` (Task 7). **status-left placement + mini-bar format** → Tasks 2, 4, 7. **Session (5-hour) bucket only** → `_session_region` anchors on "current session" (Task 1). **Opt-in + `g` toggle + immediate-probe-on-enable + off/on force-refresh** → Task 7. **Transcript litter mitigation (fixed cwd + delete + hook gate)** → `probe_cwd`/`cleanup_probe_transcripts` (Tasks 3, 5) + Task 6. **Silent failure** → `scrape_usage` returns None / `_apply_usage` leaves the bar (Tasks 5, 7). **Clean exit (`/exit` + kill-window)** → Task 5 `finally`. **Trust-prompt handling** → `_wait_for(on_trust=…)` (Task 5), proven in M0 (Task 0). **Feasibility gate** → Task 0.
- **Name consistency:** `PROBE_WINDOW`/`probe window` (tmux.py), `PROBE_DIRNAME`/`probe_cwd` (usage.py), `SESSION_EXPLORER_PROBE` (hooks), `_usage_marker`/`_usage_timer`/`_refresh_usage`/`scrape_usage`/`parse_usage`/`render_bar` used identically across tasks.
- **Open risk carried from the spec:** Task 0 is genuinely gating — if `/usage` needs interactive navigation that `send-keys` can't drive, stop and revisit before Task 1.
```
