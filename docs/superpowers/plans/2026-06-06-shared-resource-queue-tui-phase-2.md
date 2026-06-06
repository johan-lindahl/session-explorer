# Shared-Resource Queue — TUI (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Textual TUI surface for the shared-resource lease engine — a global read-only **Queues pane** (`q`), per-project **resource setup/editor dialogs** with a destructive-sync **test panel**, **new-session auto-slug** + worktree-default-on, a best-effort **out-of-lease detection toast**, and the **`docs/queue-guide.md`** user guide — on top of the Phase 1 CLI/core already shipped in v1.12.1.

**Architecture:** Phase 1 (`queue_config.py`, `queue_store.py`, `queue_run.py`, `qsync.py`, `exclusive.py`, `probes.py`, `project_id.py`) is the in-process data layer. Phase 2 reads it **directly from `tui.py`** (the same way the TUI already reads `live.poll()` and `index.load()`), not by shelling out to `queue-status`. Three new **pure modules** — `ui_state.py` (pane-visibility persistence), `queue_view.py` (display-ready snapshot assembler), `queue_detect.py` (root-dir change detector) — hold all the logic that must be unit-tested without Textual, mirroring the existing `tree_model.py` / `live.py` split. The TUI adds a content-gated `Static` pane plus `_PanelScreen` modals following the established dialog pattern, all driven off the existing `set_interval(LIVE_POLL_INTERVAL, …)` 2-second loop.

**Tech Stack:** Python 3.11–3.13 stdlib (`fcntl`, `json`, `tempfile`, `subprocess`), vendored Textual, `rsync` + `git` (already required). Tests: `pytest` + `pytest-asyncio` (Textual `Pilot` for the TUI), per the existing `test/` layout.

---

## File Structure

**New files:**
- `bin/_pkg/ui_state.py` — persisted UI toggles (`session-explorer-ui.json`), starting with `queue_pane_visible`. Pure load/save, atomic temp-file-rename like `folder_store.py`.
- `bin/_pkg/queue_view.py` — `snapshot(...)` assembles a display-ready list of resource statuses from config + store + live (holder + elapsed, waiting + positions, root-dir live-session block). Pure; no Textual import.
- `bin/_pkg/queue_detect.py` — `top_level_snapshot(path)` + `diff(...)` for the best-effort root-dir out-of-lease detector. Pure.
- `docs/queue-guide.md` — the offline-first user guide (when-NOT-to-use first, then `--delete`/`protect` rules, then template catalog).
- Tests: `test/test_ui_state.py`, `test/test_queue_view.py`, `test/test_queue_detect.py`, `test/test_tui_queue.py` (Pilot-driven pane + dialogs), and additions to `test/test_tui.py` for the slug helper.

**Modified files:**
- `bin/_pkg/tui.py` — keymap (`q`→Queues, `x`→Exit), Queues pane widget + rendering, `ResourceListScreen` / `ResourceEditorScreen` / `ResourceEditorTestPanel`, new-session auto-slug + worktree-default-on, detection toast, help text.
- `SPEC.md` — add a "Queues pane (Phase 2)" section; bump the status/milestone lines at release.
- `.claude/skills/cutting-a-release/SKILL.md` — add `docs/queue-guide.md` to the checklist.
- `bin/_pkg/__init__.py` + `.claude-plugin/plugin.json` — version bump (release task).
- `CHANGELOG.md` + `README.md` — release task.

**Established patterns this plan follows (verified in the codebase):**
- Persistence: `load()` returns a default dict on missing/corrupt; `save()` writes `tempfile.mkstemp` → `os.replace`; `mutate()` takes `flock(LOCK_EX)` on a `.lock` sidecar. (`queue_config.py:59-98`, `folder_store.py`.)
- Dialogs: subclass `_PanelScreen(ModalScreen)`; `compose()` yields `Vertical(Label(title, classes="dialog-title"), …widgets…, Label(hint, classes="dialog-hint"), id="panel")`; each keeps its own `Binding("escape", "dismiss(<value>)", …)`. (`tui.py:305-527`.)
- Polling: `self.set_interval(LIVE_POLL_INTERVAL, self._poll_live)` in `on_mount` (`tui.py:769`); off-thread refresh via `@work(thread=True, exclusive=True, group=…)` + `call_from_thread`.
- Toasts: `self.notify(msg, severity="warning")` (`tui.py:1485`).
- Modal-suppression of app bindings: `check_action()` returns `False` for app actions while a `ModalScreen` is up (`tui.py:669-687`).

---

## Task 1: `ui_state.py` — pane-visibility persistence

**Files:**
- Create: `bin/_pkg/ui_state.py`
- Test: `test/test_ui_state.py`

The spec (§9) persists pane visibility as a single global boolean in `~/.claude/session-explorer-ui.json` → `{"queue_pane_visible": true}`. Single-writer (the TUI), so a plain atomic write — no `.lock` sidecar needed — but follow the same default-on-corruption shape as the other stores.

- [ ] **Step 1: Write the failing test**

```python
# test/test_ui_state.py
import json
import os

from _pkg import ui_state


def test_default_path_is_sibling_of_index(tmp_path):
    idx = str(tmp_path / "session-explorer-index.json")
    assert ui_state.default_path_for(idx) == str(tmp_path / "session-explorer-ui.json")


def test_load_missing_returns_default(tmp_path):
    p = str(tmp_path / "session-explorer-ui.json")
    assert ui_state.load(p) == {"version": 1, "queue_pane_visible": False}


def test_load_corrupt_returns_default(tmp_path):
    p = tmp_path / "session-explorer-ui.json"
    p.write_text("{not json")
    assert ui_state.load(str(p)) == {"version": 1, "queue_pane_visible": False}


def test_set_and_load_roundtrip(tmp_path):
    p = str(tmp_path / "session-explorer-ui.json")
    ui_state.set_queue_pane_visible(p, True)
    assert ui_state.load(p)["queue_pane_visible"] is True
    ui_state.set_queue_pane_visible(p, False)
    assert ui_state.load(p)["queue_pane_visible"] is False


def test_set_preserves_unknown_keys(tmp_path):
    p = tmp_path / "session-explorer-ui.json"
    p.write_text(json.dumps({"version": 1, "queue_pane_visible": False, "future": 7}))
    ui_state.set_queue_pane_visible(str(p), True)
    data = json.loads(p.read_text())
    assert data["future"] == 7 and data["queue_pane_visible"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_ui_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.ui_state'`

- [ ] **Step 3: Write the implementation**

```python
# bin/_pkg/ui_state.py
"""Persisted TUI toggles (session-explorer-ui.json).

Single-writer (the explorer process), so a plain atomic temp-file-rename write
is enough — no `.lock` sidecar. Mirrors the default-on-corruption shape of the
other stores. v1 holds one flag: queue_pane_visible (spec §9).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict

_DEFAULT: Dict[str, Any] = {"version": 1, "queue_pane_visible": False}


def default_path_for(index_path: str) -> str:
    d = os.path.dirname(os.path.abspath(index_path)) or "."
    return os.path.join(d, "session-explorer-ui.json")


def load(path: str) -> dict:
    if not os.path.exists(path):
        return dict(_DEFAULT)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT)
    if not isinstance(data, dict):
        return dict(_DEFAULT)
    merged = dict(_DEFAULT)
    merged.update(data)
    return merged


def save(path: str, data: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-ui-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def set_queue_pane_visible(path: str, visible: bool) -> None:
    data = load(path)
    data["queue_pane_visible"] = bool(visible)
    save(path, data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_ui_state.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/ui_state.py test/test_ui_state.py
git commit -m "feat(queue-tui): ui_state store for queue-pane visibility"
```

---

## Task 2: `queue_view.py` — display-ready snapshot assembler

**Files:**
- Create: `bin/_pkg/queue_view.py`
- Test: `test/test_queue_view.py`

The pane needs more than `queue-status --json` emits: holder **elapsed** time, per-waiter **position** ("1 of 2"), the resource **kind**, and — for `root-dir` — the **live-session block** (`exclusive.live_root_session`). This module assembles it all in one pure function so the pane is a thin renderer. It reads via the Phase 1 APIs: `queue_config.all_projects`, `queue_run.queue_dir`, `queue_store.list_tickets`, `exclusive.live_root_session`.

Ticket payloads carry `{"number","sid","cwd","command","pid","label","created"}` (ISO-8601, see `queue_store.take_ticket`). `list_tickets` returns live tickets sorted by number; the first is the holder.

- [ ] **Step 1: Write the failing test**

```python
# test/test_queue_view.py
from datetime import datetime, timezone

from _pkg import queue_config, queue_run, queue_store, queue_view


def _now():
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)


def _seed_ticket(qdir, *, sid, created, label):
    # take_ticket allocates the ticket number itself (monotonic per qdir), so we
    # don't pass one — sequential calls deterministically get 1, 2, 3 … in order.
    # The returned Ticket holds the lifetime flock; the caller must release() it.
    return queue_store.take_ticket(
        qdir, sid=sid, cwd="/x", command=["test"], pid=1,
        label=label, now_iso=created)


def test_fmt_elapsed():
    assert queue_view.fmt_elapsed(0) == "0:00"
    assert queue_view.fmt_elapsed(42) == "0:42"
    assert queue_view.fmt_elapsed(83) == "1:23"
    assert queue_view.fmt_elapsed(3725) == "1:02:05"


def test_snapshot_free_resource(tmp_path):
    cfg = str(tmp_path / "qc.json")
    queues = str(tmp_path / "queues")
    queue_config.add_resource(
        cfg, project_id="abc123", display_path="/repo/Gym",
        resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    rows = queue_view.snapshot(cfg, queues, str(tmp_path / "live.json"),
                               now=_now())
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "abc123/db"
    assert r["project"] == "/repo/Gym"
    assert r["resource"] == "db"
    assert r["kind"] == "port"
    assert r["holder"] is None
    assert r["waiting"] == []
    assert r["live_root_block"] is None
    assert r["active"] is False


def test_snapshot_holder_and_waiters(tmp_path):
    cfg = str(tmp_path / "qc.json")
    queues = str(tmp_path / "queues")
    queue_config.add_resource(
        cfg, project_id="abc123", display_path="/repo/Gym",
        resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    qdir = queue_run.queue_dir(queues, "abc123", "db")
    held = []
    held.append(_seed_ticket(qdir, sid="feat-auth",
                             created="2026-06-06T11:59:18+00:00", label="Gym/db"))
    held.append(_seed_ticket(qdir, sid="bugfix",
                             created="2026-06-06T11:59:50+00:00", label="Gym/db"))
    held.append(_seed_ticket(qdir, sid="ui",
                             created="2026-06-06T11:59:55+00:00", label="Gym/db"))
    try:
        rows = queue_view.snapshot(cfg, queues, str(tmp_path / "live.json"),
                                   now=_now())
    finally:
        for t in held:
            t.release()
    r = rows[0]
    assert r["holder"]["sid"] == "feat-auth"
    assert r["holder"]["elapsed"] == "0:42"
    assert [w["sid"] for w in r["waiting"]] == ["bugfix", "ui"]
    assert r["waiting"][0]["pos"] == "1 of 2"
    assert r["waiting"][1]["pos"] == "2 of 2"
    assert r["active"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_queue_view.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.queue_view'`

- [ ] **Step 3: Write the implementation**

```python
# bin/_pkg/queue_view.py
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


def snapshot(config_path: str, queues_root: str, live_path: str, *,
             now: Optional[datetime] = None) -> List[dict]:
    """One row per configured resource. Each row:
      {id, project_id, project, resource, kind,
       holder: {sid,label,elapsed}|None,
       waiting: [{sid,label,pos}],   # pos = "N of M" among waiters
       live_root_block: {sid,cwd,name}|None,   # root-dir only
       active: bool}                 # holder/waiters/block present
    """
    now = now or datetime.now(timezone.utc)
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
                holder = {"sid": h["sid"], "label": h.get("label", h["sid"]),
                          "elapsed": _elapsed(h.get("created"), now)}
                waiters = tickets[1:]
                total = len(waiters)
                for i, t in enumerate(waiters, start=1):
                    waiting.append({"sid": t["sid"],
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_queue_view.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_view.py test/test_queue_view.py
git commit -m "feat(queue-tui): queue_view snapshot assembler for the pane"
```

---

## Task 3: worktree slug helper (pure)

**Files:**
- Modify: `bin/_pkg/tui.py` (add module-level `worktree_slug` near `split_path`)
- Test: `test/test_tui.py` (add slug cases)

The spec §9 fixes the slug rules exactly: lowercase; spaces/underscores/dots→`-`; drop non-`[a-z0-9-]`; collapse/trim dashes; derived from the display portion **after the last `/`**; blank name → blank.

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui.py  (append)
from _pkg.tui import worktree_slug


def test_worktree_slug_basic():
    assert worktree_slug("Sprint 14 Auth") == "sprint-14-auth"


def test_worktree_slug_uses_display_portion_after_last_slash():
    assert worktree_slug("team/planning/Sprint 14") == "sprint-14"


def test_worktree_slug_strips_punctuation_and_collapses_dashes():
    assert worktree_slug("Feature: __Foo.Bar__!!") == "feature-foo-bar"


def test_worktree_slug_blank_is_blank():
    assert worktree_slug("") == ""
    assert worktree_slug("   ") == ""
    assert worktree_slug("team/") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui.py::test_worktree_slug_basic -q`
Expected: FAIL — `ImportError: cannot import name 'worktree_slug'`

- [ ] **Step 3: Write the implementation**

Add near the top-level helpers in `tui.py` (e.g. just after `split_path`'s definition — find it with `grep -n "def split_path" bin/_pkg/tui.py`):

```python
def worktree_slug(name: str) -> str:
    """Slug a session name into a git-worktree name (spec §9).

    Uses the display portion after the last '/'; lowercases; turns spaces,
    underscores and dots into '-'; drops anything outside [a-z0-9-]; collapses
    and trims dashes. Blank in → blank out (so a temporary unnamed session
    leaves the worktree name empty, i.e. a bare `-w`).
    """
    import re
    display = name.rsplit("/", 1)[-1].strip().lower()
    display = re.sub(r"[ _.]+", "-", display)
    display = re.sub(r"[^a-z0-9-]+", "", display)
    display = re.sub(r"-{2,}", "-", display).strip("-")
    return display
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -q -k worktree_slug`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(queue-tui): worktree_slug helper for new-session auto-slug"
```

---

## Task 4: Keymap — `q`→Queues, `x`→Exit (+ check_action + help text)

**Files:**
- Modify: `bin/_pkg/tui.py:588-612` (BINDINGS), `:669-687` (check_action), `:227-301` (`_help_text`)
- Test: `test/test_tui_queue.py` (new file)

Per spec §9: `q` toggles the Queues pane (reassigning quit), `x` is global Exit. The existing `action_quit` (`tui.py:1541`) is kept and simply re-bound to `x`. `action_toggle_queues` is added empty here (full behavior lands in Task 5/6) so the binding wiring is testable now.

- [ ] **Step 1: Move the shared `index_path` fixture into `conftest.py`**

The `index_path` fixture currently lives **locally** in `test/test_tui.py:6-40`, so it is not visible to a new test module. Move it into `test/conftest.py` (where it becomes session-wide) and delete the copy from `test_tui.py`. Cut the entire `@pytest.fixture def index_path(...)` block (the `import json`/`import os`/`import pytest` at the top of `test_tui.py` stay if other code uses them; `json`/`os`/`pytest` are needed by the fixture, so add them to `conftest.py`). Append to `test/conftest.py`:

```python
import json

import pytest


@pytest.fixture
def index_path(tmp_path):
    """Per-test index in an isolated directory (shared across TUI test modules).

    The folder store is derived as a sibling of the index, so co-locating the
    index inside the unique tmp_path keeps the folder store test-isolated too.
    """
    path = str(tmp_path / "se-index.json")
    json.dump({
        "version": 1, "folders": [],
        "sessions": {
            "sid-1": {
                "project_label": "demo",
                "project_path": "/tmp/demo-project",
                "name_cached": "planning/sprint14",
                "last_active_at": "2026-05-27T10:00:00Z",
                "tokens_estimate": 12345,
                "tokens_window_pct": 6,
                "message_count": 18,
                "first_prompt": "hello",
            }
        }
    }, open(path, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    yield path
```

Then delete the duplicate fixture from `test/test_tui.py` (lines 6-40). Leave `test_tui_quit` **unchanged for now** — at this point `q` still quits, so it stays valid; it is renamed alongside the binding change in Step 4 (doing it here would assert against a binding that doesn't exist yet).

Run `python3 -m pytest test/test_tui.py -q` and confirm it still passes — this verifies the fixture now resolves from `conftest.py` (and `test_tui_quit` pressing `q` still genuinely quits, so the run is a real check, not a trivial one).

- [ ] **Step 2: Write the failing test**

```python
# test/test_tui_queue.py
import os

import pytest

# Import _pkg.tui BEFORE textual: _pkg/__init__ appends the vendored Textual
# (bin/_pkg/_vendor) to sys.path, so `textual` is only importable once _pkg has
# been imported. conftest adds bin/ but not _vendor, so a bare
# `from textual.widgets import ...` at module top would fail on a clean env with
# no site-packages Textual. Order matters here.
from _pkg.tui import SessionExplorerApp
from textual.widgets import Checkbox, Input, Label, TextArea


def _binding_keys(action):
    return {b.key for b in SessionExplorerApp.BINDINGS if b.action == action}


def test_q_bound_to_toggle_queues_not_quit():
    assert "q" in _binding_keys("toggle_queues")
    assert "q" not in _binding_keys("quit")


def test_x_bound_to_quit():
    assert "x" in _binding_keys("quit")


@pytest.mark.asyncio
async def test_x_exits_app(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
    # run_test context exiting cleanly is the assertion; no hang.
```

(The `index_path` fixture now resolves from `test/conftest.py` after Step 1, and isolates the index + folder store + silences help/retention modals.)

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui_queue.py -q`
Expected: FAIL — `q` still maps to `quit`.

- [ ] **Step 4: Edit the BINDINGS list (and rename the now-stale quit test)**

In `tui.py:605`, replace the quit binding:

```python
        Binding("q", "toggle_queues", "Queues"),
        Binding("x", "quit", "Exit"),
```

Now that `q` no longer quits, update the stale test in `test/test_tui.py` in the same step — rename `test_tui_quit` (`test_tui.py:52-58`) to `test_tui_exit` and press `x`:

```python
async def test_tui_exit(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.press("x")     # x is the exit key after the Phase-2 rebind
        await pilot.pause()
    # Reaching here without timeout means exit worked.
```

- [ ] **Step 5: Update `check_action` (`tui.py:669-687`)**

The big tuple of app actions suppressed while a modal is up must include `toggle_queues` and drop the now-removed `q→quit` special-casing. Replace the two `if` bodies that mention `"quit"`:

```python
        if action in ("resume", "rename", "move", "new_folder", "new_session", "delete", "notes", "preview", "close_preview", "filter", "cycle_view", "toggle_collapse", "toggle_usage", "rescan", "help", "expand_node", "collapse_node", "quit", "toggle_queues", "resource_setup") and isinstance(self.screen, ModalScreen):
            return False
        # While the filter Input is focused, never let `q`/`x` fire the global
        # Queues-toggle or Exit bindings — the keystrokes belong in the text.
        if action in ("quit", "toggle_queues") and getattr(self, "_filter", None) is not None and self._filter.has_focus:
            return False
```

- [ ] **Step 6: Add the (temporary) `action_toggle_queues` stub**

Just after `action_help` (`tui.py:1525`), add:

```python
    def action_toggle_queues(self) -> None:
        # Full behavior added in the Queues-pane task; stub keeps the binding live.
        pass
```

- [ ] **Step 7: Update the help text (`_help_text`, `tui.py:296`)**

Replace the single `key("q", "Quit")` line with:

```python
        key("q", "Toggle the Queues pane (shared-resource leases)"),
        key("x", "Exit"),
```

And update the closing help line `tui.py:298` if it names `q` for dismissing help — it refers to the HelpScreen's own `q` (still valid inside the modal), so leave it.

- [ ] **Step 8: Run tests**

Run: `python3 -m pytest test/test_tui_queue.py test/test_tui.py -q`
Expected: PASS (existing TUI tests unaffected; new keymap tests pass)

- [ ] **Step 9: Commit**

```bash
git add bin/_pkg/tui.py test/conftest.py test/test_tui.py test/test_tui_queue.py
git commit -m "feat(queue-tui): rebind q→Queues, x→Exit; help + check_action"
```

---

## Task 5: Queues pane widget + content-gated visibility + persistence

**Files:**
- Modify: `bin/_pkg/tui.py` — `compose` (`:711-729`), CSS (`:577-586`), `__init__` (`:614-668`), `on_mount` (`:738-775`), `action_toggle_queues`
- Test: `test/test_tui_queue.py`

The pane is a `Static` (`id="queues"`) in the left column under the tree, hidden by default. `q` toggles it and persists the flag via `ui_state`. **Content-gating** (§9, exact): with the flag on, the pane takes space only when there is something to show — **≥1 *active* queue anywhere, OR the currently-selected project has configured resources**. A persisted `true` with an idle, unrelated configured resource must NOT show a pane (zero-footprint). An explicit `q` press with nothing to show still surfaces a one-line activation hint *this session* (so the keypress isn't a no-op), but that force is not persisted. `queue_view.snapshot()` returns *all* configured resources each with an `active` flag and `project_id`; the gating decision (active-anywhere vs selected-project-configured) lives in the TUI, not the view module.

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui_queue.py  (append)
from _pkg import queue_config, ui_state


@pytest.mark.asyncio
async def test_queue_pane_hidden_by_default(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#queues").display is False


@pytest.mark.asyncio
async def test_q_with_no_resources_shows_hint_then_persists_off_render(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        pane = app.query_one("#queues")
        assert pane.display is True
        assert "Set up" in str(pane.render()) or "shared resources" in str(pane.render()).lower()


@pytest.mark.asyncio
async def test_q_toggle_persists_flag(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    ui_path = ui_state.default_path_for(index_path)
    assert ui_state.load(ui_path)["queue_pane_visible"] is True


@pytest.mark.asyncio
async def test_persisted_visible_with_only_unrelated_idle_renders_nothing(
        index_path, tmp_path, monkeypatch):
    # Zero-footprint regression (spec §9): a persisted queue_pane_visible=true,
    # whose ONLY configured resource is idle AND belongs to an unrelated project
    # (not the selected one), must render NOTHING on launch — never the row.
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    monkeypatch.setenv("SESSION_EXPLORER_QUEUES_ROOT", str(tmp_path / "queues"))
    queue_config.add_resource(
        qcfg, project_id="zzz999", display_path="/repo/Other", resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    # The fixture's session project '/tmp/demo-project' is not a git repo, so its
    # project_id is None — it can never match the unrelated 'zzz999' resource.
    ui_state.set_queue_pane_visible(ui_state.default_path_for(index_path), True)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#queues").display is False
```

(`queue_config` is imported at the top of `test_tui_queue.py` alongside `ui_state` from the earlier Task-5 step.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_tui_queue.py -q -k "pane or hint or persists or unrelated_idle"`
Expected: FAIL — no `#queues` widget.

- [ ] **Step 3: Add the widget to `compose` (`tui.py:721-724`)**

Insert the pane into the left `Vertical` (so it sits under the tree, not beside the preview):

```python
        self._queues = Static("", id="queues")
        self._queues.display = False
        yield Horizontal(
            Vertical(self._colheader, self._tree, self._queues, self._empty, id="treepane"),
            self._preview,
        )
```

- [ ] **Step 4: Add CSS (`tui.py:586`, inside the `CSS = """ … """` block)**

```python
    #queues { height: auto; max-height: 40%; padding: 0 1; border-top: solid $accent; }
```

- [ ] **Step 5: Add state to `__init__` (`tui.py:668`, end of `__init__`)**

```python
        # Queues pane (shared-resource leases). Visibility persists globally;
        # _queue_hint_forced shows a one-line activation hint after an explicit
        # `q` press even when nothing is configured (cleared on next toggle-off).
        self._queue_visible: bool = False
        self._queue_hint_forced: bool = False
```

- [ ] **Step 6: Restore persisted visibility in `on_mount` (after `tui.py:740` `self._populate()`)**

```python
        from . import ui_state as _ui
        self._queue_visible = bool(_ui.load(self._ui_path()).get("queue_pane_visible"))
        self._render_queues()  # content-gated; renders nothing if empty
```

And add the path + config helpers near `_live_path` (`tui.py:734`):

```python
    def _ui_path(self) -> str:
        from . import ui_state as _ui
        return _ui.default_path_for(self._index_path)

    def _queue_config_path(self) -> str:
        from . import queue_config as _qc
        return os.environ.get("SESSION_EXPLORER_QUEUE_CONFIG") or _qc.default_path_for(self._index_path)

    def _queues_root(self) -> str:
        return os.environ.get("SESSION_EXPLORER_QUEUES_ROOT") or os.path.join(self._claude_dir(), "session-explorer-queues")
```

- [ ] **Step 7: Implement `action_toggle_queues` and `_render_queues`**

Replace the stub from Task 4 with:

```python
    def _gating_rows(self) -> list:
        """The rows that justify showing the pane AND are rendered in it (spec
        §9): every *active* queue across all projects, plus all resources of the
        currently-selected project (so its idle ones are visible). An idle,
        unrelated resource is in neither set, so it never opens the pane and is
        never rendered."""
        from . import project_id as _pid, queue_view as _qv
        try:
            rows = _qv.snapshot(self._queue_config_path(), self._queues_root(),
                                self._live_path())
        except Exception:
            rows = []
        sel_proj, _ = self._project_and_prefix_for_cursor()
        sel_pid = _pid.project_id(sel_proj) if sel_proj else None
        return [r for r in rows
                if r["active"] or (sel_pid is not None and r["project_id"] == sel_pid)]

    def action_toggle_queues(self) -> None:
        from . import ui_state as _ui
        self._queue_visible = not self._queue_visible
        _ui.set_queue_pane_visible(self._ui_path(), self._queue_visible)
        # Force the one-line hint ONLY when turning the pane on with nothing to
        # show — so the keypress isn't a silent no-op on an unconfigured project.
        # Never force when there is real content (else the pane stays stuck open
        # as a "hint" if that content later disappears this session).
        self._queue_hint_forced = self._queue_visible and not self._gating_rows()
        self._render_queues()

    def _render_queues(self) -> None:
        """Content-gated render (spec §9). The gating set and the rendered set are
        the SAME filtered rows, so the pane never shows an unrelated idle resource
        — when that set is empty it shows the activation hint (or nothing, if the
        hint wasn't forced this session)."""
        gating = self._gating_rows()
        show = self._queue_visible and (bool(gating) or self._queue_hint_forced)
        self._queues.display = show
        if not show:
            return
        if not gating:
            self._queues.update(
                "[b]Queues[/]  ·  this project is not using shared resources\n"
                "[dim]Select a project and press [b]s[/] to set up · "
                "guide: docs/queue-guide.md[/]")
            return
        self._queues.update(_render_queue_rows(gating))
```

- [ ] **Step 8: Add the pure row renderer (module-level, near `_help_text`)**

```python
def _render_queue_rows(rows: list) -> str:
    """Render queue_view.snapshot() rows as pane markup (spec §9 mockup)."""
    lines = ["[b]Queues[/]"]
    for r in rows:
        name = f"{_basename(r['project'])} / {r['resource']}"
        if r["live_root_block"]:
            who = r["live_root_block"].get("name", "?")
            lines.append(f"  {name:<26}⛔ held by live session ‹{who}›")
            continue
        if r["holder"]:
            h = r["holder"]
            lines.append(f"  {name:<26}● holder: {h['label']} ({h['elapsed']})")
            if r["waiting"]:
                waits = " · ".join(f"{w['label']} ({w['pos']})" for w in r["waiting"])
                lines.append(f"  {'':<26}waiting: {waits}")
        else:
            lines.append(f"  {name:<26}○ free")
    return "\n".join(lines)


def _basename(path: str) -> str:
    return os.path.basename(path.rstrip("/")) or path
```

- [ ] **Step 9: Run tests**

Run: `python3 -m pytest test/test_tui_queue.py -q`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_queue.py
git commit -m "feat(queue-tui): content-gated Queues pane + persisted toggle"
```

---

## Task 6: Live the Queues pane on the 2s refresh loop

**Files:**
- Modify: `bin/_pkg/tui.py` — `on_mount` (`:769`), `_poll_live` (`:1637-1680`)
- Test: `test/test_tui_queue.py`

The pane must update as holders/waiters change (spec §6: "live on the existing ~2s refresh loop"). Snapshot assembly touches the filesystem (reaping dead tickets via flock probes), so call `_render_queues` from `_poll_live` only when the pane is visible — cheap and bounded.

- [ ] **Step 1: Write the failing test**

This test must prove the **`_poll_live` hook specifically** — not just that `action_toggle_queues` renders. So it shows the pane *before* any ticket exists (the holder must NOT already be on screen), then creates the ticket, then calls `_poll_live()` and asserts the holder appeared. Without the Step 3 hook this stays on the activation hint and the assertion fails.

```python
# test/test_tui_queue.py  (append)
@pytest.mark.asyncio
async def test_poll_live_refreshes_the_pane(index_path, tmp_path, monkeypatch):
    from _pkg import queue_config, queue_run, queue_store
    qcfg = str(tmp_path / "qc.json")
    queues = str(tmp_path / "queues")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    monkeypatch.setenv("SESSION_EXPLORER_QUEUES_ROOT", queues)
    queue_config.add_resource(
        qcfg, project_id="abc123", display_path="/repo/Gym", resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")              # show pane FIRST, no ticket yet
        await pilot.pause()
        # Pane is up but the resource is idle and unselected → no holder shown.
        assert "holder:" not in str(app.query_one("#queues").render())
        # Now a holder appears AFTER the pane is shown; only _poll_live can
        # surface it (action_toggle_queues already ran).
        qdir = queue_run.queue_dir(queues, "abc123", "db")
        ticket = queue_store.take_ticket(qdir, sid="feat-auth", cwd="/x",
                                         command=["t"], pid=1, label="Gym/db",
                                         now_iso="2026-06-06T11:00:00+00:00")
        try:
            app._poll_live()
            await pilot.pause()
            assert "holder: Gym/db" in str(app.query_one("#queues").render())
        finally:
            ticket.release()
```

(Note: the first assertion relies on `abc123` not being the selected project — the `index_path` fixture's session is project `demo`, which has no `project_id` collision with the synthetic `abc123`, so the idle `db` is neither active nor selected and stays hidden until it gains a holder.)

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_tui_queue.py -q -k poll_live_refreshes`
Expected: FAIL — the holder never appears because `_poll_live` doesn't refresh the pane yet (assertion on `holder: Gym/db` fails).

- [ ] **Step 3: Hook `_render_queues` into `_poll_live`**

At the end of `_poll_live` (`tui.py:1680`, after the live-metadata refresh block), add:

```python
        # Keep the Queues pane current on the same cadence (cheap when hidden).
        if self._queue_visible:
            self._render_queues()
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest test/test_tui_queue.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_queue.py
git commit -m "feat(queue-tui): refresh Queues pane on the 2s poll loop"
```

---

## Task 7: `s` → per-project Resource List dialog (list / remove)

**Files:**
- Modify: `bin/_pkg/tui.py` — add `ResourceListScreen(_PanelScreen)`, `action_resource_setup`, BINDINGS, check_action gating
- Test: `test/test_tui_queue.py`

Per §6: with a project (or a session under it) selected, `s` opens *that project's* resource list, titled with the project name. `s` is a hidden global binding (not in the footer; §9 keeps only `q` as the added footer key), gated by `check_action` to require a selected project. The list shows configured resources and supports `a` (add — Task 8), `e` (edit — Task 8), `Del` (remove, confirmed), `?` (help — Task 10). This task wires the screen + list + remove; add/edit push the editor stub.

`project_id` for the selected project comes from `project_id.project_id(project_root)`, where `project_root` is the tree node's `data["project"]` (the repo root path). Resources are stored under that id.

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui_queue.py  (append)
@pytest.mark.asyncio
async def test_s_disabled_without_project_selection(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # No project node selected (empty tree) → resource_setup is disabled.
        assert app.check_action("resource_setup", ()) is False


@pytest.mark.asyncio
async def test_resource_list_lists_configured_resources(index_path, tmp_path, monkeypatch):
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceListScreen
    # A real git repo so project_id resolves.
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    queue_config.add_resource(
        qcfg, project_id=pid, display_path=str(repo), resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    screen = ResourceListScreen(project_root=str(repo), project_id=pid,
                                config_path=qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(screen)
        await pilot.pause()
        body = str(screen.query_one("#reslist").render() if hasattr(
            screen.query_one("#reslist"), "render") else screen.query_one("#reslist"))
        # The OptionList contains the resource id.
        assert any("db" in str(o.prompt) for o in screen.query_one("#reslist").options)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_tui_queue.py -q -k "resource_list or s_disabled"`
Expected: FAIL — no `ResourceListScreen` / `resource_setup` action.

- [ ] **Step 3: Add the `s` binding (after the `q`/`x` bindings, `tui.py:606`)**

```python
        Binding("s", "resource_setup", "Shared resources", show=False),
```

- [ ] **Step 4: Gate `s` in `check_action` (add before the final `return True`)**

```python
        # `s` (shared-resource setup) is only meaningful with a project selected.
        if action == "resource_setup":
            proj, _ = self._project_and_prefix_for_cursor()
            return proj is not None
```

- [ ] **Step 5: Add `action_resource_setup` (near `action_new_session`, `tui.py:1342`)**

```python
    def action_resource_setup(self) -> None:
        from . import project_id as _pid
        project, _ = self._project_and_prefix_for_cursor()
        if not project:
            self.bell(); return
        pid = _pid.project_id(project)
        if pid is None:
            self.notify("This project is not a git repository — shared resources "
                        "need a repo.", severity="warning")
            return
        self.push_screen(ResourceListScreen(project_root=project, project_id=pid,
                                             config_path=self._queue_config_path()))
```

- [ ] **Step 6: Add `ResourceListScreen` (after `NotesScreen`, `tui.py:528`)**

```python
class ResourceListScreen(_PanelScreen):
    """Per-project shared-resource list (spec §6). a add · e edit · Del remove ·
    ? help · esc close. The destructive editor + test panel live in
    ResourceEditorScreen."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Close"),
        Binding("a", "add", "Add", show=False),
        Binding("e", "edit", "Edit", show=False),
        Binding("delete", "remove", "Remove", show=False),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def __init__(self, *, project_root: str, project_id: str, config_path: str) -> None:
        super().__init__()
        self._project_root = project_root
        self._project_id = project_id
        self._config_path = config_path

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"Shared resources — {_basename(self._project_root)}",
                  classes="dialog-title"),
            OptionList(id="reslist"),
            Label("a add · e edit · Del remove · ? help · esc close",
                  classes="dialog-hint"),
            id="panel",
        )

    def on_mount(self) -> None:
        self._reload()

    def _reload(self) -> None:
        from . import queue_config as _qc
        ol = self.query_one("#reslist", OptionList)
        ol.clear_options()
        resources = _qc.list_resources(self._config_path, self._project_id)
        if not resources:
            ol.add_option(Option("(no resources yet — press a to add)", id=None))
            return
        for rid, res in sorted(resources.items()):
            label = (f"{rid:<14} {res.get('kind',''):<9} "
                     f"acquire:{res.get('acquire','')}  run_in:{res.get('run_in','')}")
            ol.add_option(Option(label, id=rid))

    def action_add(self) -> None:
        def after(saved):
            if saved:
                self._reload()
        self.app.push_screen(
            ResourceEditorScreen(project_root=self._project_root,
                                 project_id=self._project_id,
                                 config_path=self._config_path,
                                 resource_id=None),
            after)

    def action_edit(self) -> None:
        rid = self._selected_rid()
        if not rid:
            return
        def after(saved):
            if saved:
                self._reload()
        self.app.push_screen(
            ResourceEditorScreen(project_root=self._project_root,
                                 project_id=self._project_id,
                                 config_path=self._config_path,
                                 resource_id=rid),
            after)

    def action_remove(self) -> None:
        from . import queue_config as _qc
        rid = self._selected_rid()
        if not rid:
            return
        def after(ok: bool) -> None:
            if ok:
                _qc.remove_resource(self._config_path, self._project_id, rid)
                self._reload()
        self.app.push_screen(
            ConfirmScreen(f"Remove shared resource '{rid}'? (queue config only; "
                          "no files are touched)"), after)

    def action_help(self) -> None:
        self.app.push_screen(QueueHelpScreen())

    def _selected_rid(self) -> "str | None":
        ol = self.query_one("#reslist", OptionList)
        idx = ol.highlighted
        if idx is None:
            return None
        opt = ol.get_option_at_index(idx)
        return opt.id
```

(`QueueHelpScreen` and `ResourceEditorScreen` are defined in Tasks 10 and 8; add a minimal placeholder `ResourceEditorScreen`/`QueueHelpScreen` now so the module imports — they get filled in those tasks. Insert directly below:)

```python
class ResourceEditorScreen(_PanelScreen):
    """Filled in the editor task; placeholder keeps the module importable."""
    BINDINGS = [Binding("escape", "dismiss(False)", "Cancel")]

    def __init__(self, *, project_root, project_id, config_path, resource_id) -> None:
        super().__init__()
        self._project_root = project_root
        self._project_id = project_id
        self._config_path = config_path
        self._resource_id = resource_id

    def compose(self) -> ComposeResult:
        yield Vertical(Label("Resource editor (TODO)", classes="dialog-title"),
                       Label("esc cancel", classes="dialog-hint"), id="panel")


class QueueHelpScreen(_PanelScreen):
    """Filled in the help task; placeholder."""
    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        yield Vertical(Label("Queue help (TODO)", classes="dialog-title"),
                       Label("esc close", classes="dialog-hint"), id="panel")
```

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest test/test_tui_queue.py -q -k "resource_list or s_disabled"`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_queue.py
git commit -m "feat(queue-tui): per-project resource list dialog (s) with remove"
```

---

## Task 8: Resource editor — template picker, kind-reflow form, save

**Files:**
- Modify: `bin/_pkg/tui.py` — replace the `ResourceEditorScreen` placeholder; add module-level `QUEUE_TEMPLATES`, `template_resource`, and the pure form helpers `parse_guard_lines`/`format_guard_lines`/`parse_path_lines`/`parse_wait_for`/`format_wait_for`
- Test: `test/test_tui_queue.py`, `test/test_queue_templates.py` (new — pure template + form-helper data)

The editor is template-first and **reflows per `kind`** (spec §6): a `device`/`port`/`name` resource hides the `path` + `protect` inputs and (via the template) uses `run_in: worktree`; a `root-dir` shows `protect` and a **read-only** canonical `path`. It exposes the v1-core fields directly — **`guard`** (one `exe sub…` rule per line), **`protect`** (root-only paths, one per line, sync resources), acquire/release commands, `release_required`, `health`, and **`wait_for`** readiness (`'<url|port|command> <target>'` + a timeout). Two spec-faithful rules: the **root-dir `path` is always `project_id.main_root(project_root)`** — never the editable input (the field is disabled for root-dir; only a `path`-kind path is editable) — and the form is **authoritative on save**, so clearing a command/health/`wait_for` field removes the stale value (and reverts a now-empty command strategy to `none`). On Save it validates via `queue_config.add_resource` (which enforces the §2 invariants — `sync` only on `root-dir`, `command` needs its shell string) and dismisses `True`.

First, factor the template catalog into pure data so it is unit-tested without Textual.

- [ ] **Step 1: Write the failing test (templates)**

```python
# test/test_queue_templates.py
from _pkg.tui import QUEUE_TEMPLATES, template_resource


def test_templates_cover_documented_cases():
    keys = {t["key"] for t in QUEUE_TEMPLATES}
    assert {"bind-mounted-stack", "browser-e2e", "ios-sim",
            "shared-db", "root-env", "device-seat", "custom"} <= keys


def test_template_resource_root_dir_has_sync_and_protect():
    res = template_resource("root-env", path="/repo")
    assert res["kind"] == "root-dir"
    assert res["acquire"] == "sync"
    assert res["run_in"] == "root"
    assert "/.env" in res["sync"]["protect"]


def test_template_resource_device_forces_worktree_no_sync():
    res = template_resource("ios-sim", path="")
    assert res["kind"] in ("device", "name")
    assert res["run_in"] == "worktree"
    assert res["acquire"] == "none"
    assert "sync" not in res


def test_custom_template_is_blank_none_strategy():
    res = template_resource("custom", path="")
    assert res["acquire"] == "none"
    assert res["run_in"] == "worktree"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_queue_templates.py -q`
Expected: FAIL — no `QUEUE_TEMPLATES`.

- [ ] **Step 3: Add the template catalog (module-level in `tui.py`, near `worktree_slug`)**

```python
# Spec §7 template catalog. `defaults` is merged into a resource dict; the editor
# overlays user edits. Kept as pure data so it is unit-tested without Textual.
QUEUE_TEMPLATES = [
    {"key": "bind-mounted-stack", "title": "Bind-mounted stack, well-known ports",
     "defaults": {"kind": "root-dir", "acquire": "sync", "release": "none",
                  "run_in": "root",
                  "guard": [{"exe": "docker", "sub": ["compose", "up"]},
                            {"exe": "docker", "sub": ["compose", "run"]}],
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env", "/.env.*"]},
                  "wait_for": {"type": "url", "target": "http://localhost:8080",
                               "timeout": 120}}},
    {"key": "browser-e2e", "title": "Browser e2e vs fixed-URL app",
     "defaults": {"kind": "root-dir", "acquire": "sync", "release": "none",
                  "run_in": "root",
                  "guard": [{"exe": "playwright", "sub": ["test"]},
                            {"exe": "cypress", "sub": ["run"]}],
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env", "/.env.*"]},
                  "wait_for": {"type": "url", "target": "http://localhost:3000",
                               "timeout": 120}}},
    {"key": "ios-sim", "title": "iOS simulator / xcodebuild",
     "defaults": {"kind": "device", "acquire": "none", "release": "none",
                  "run_in": "worktree",
                  "guard": [{"exe": "xcodebuild", "sub": ["test"]}]}},
    # acquire defaults to "none" (valid as-saved); filling the editor's acquire
    # field promotes it to "command" with the user's DB-reset shell. Shipping
    # acquire="command" with an empty command_acquire would fail queue_config
    # validation on save (queue_config.py:121-122).
    {"key": "shared-db", "title": "Single shared database",
     "defaults": {"kind": "port", "acquire": "none", "release": "none",
                  "run_in": "worktree",
                  "guard": [{"exe": "npm", "sub": ["run", "migrate"]}]}},
    {"key": "root-env", "title": "Root-only credentials / .env",
     "defaults": {"kind": "root-dir", "acquire": "sync", "release": "none",
                  "run_in": "root", "guard": [],
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env", "/.env.*"]}}},
    {"key": "device-seat", "title": "Single device / HIL / license seat",
     "defaults": {"kind": "name", "acquire": "none", "release": "none",
                  "run_in": "worktree", "guard": []}},
    {"key": "custom", "title": "Custom / blank",
     "defaults": {"kind": "name", "acquire": "none", "release": "none",
                  "run_in": "worktree", "guard": []}},
]


def template_resource(key: str, *, path: str) -> dict:
    """Build a fresh resource dict from a template key. Pure."""
    import copy
    tpl = next((t for t in QUEUE_TEMPLATES if t["key"] == key), None)
    if tpl is None:
        tpl = next(t for t in QUEUE_TEMPLATES if t["key"] == "custom")
    res = copy.deepcopy(tpl["defaults"])
    if res.get("kind") in ("root-dir", "path") and path:
        res["path"] = path
    return res


# --- Editor form <-> resource-dict conversions (pure, unit-tested) ---

def parse_guard_lines(text: str) -> list:
    """Each non-empty line 'exe sub1 sub2' -> {'exe': exe, 'sub': [sub1, ...]}.
    Blank/whitespace-only lines are dropped. So 'docker compose up' becomes
    {'exe': 'docker', 'sub': ['compose', 'up']}. Pure."""
    rules = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        rules.append({"exe": parts[0], "sub": parts[1:]})
    return rules


def format_guard_lines(rules: list) -> str:
    """Inverse of parse_guard_lines, to pre-fill the guard editor."""
    return "\n".join(" ".join([r.get("exe", "")] + list(r.get("sub", [])))
                     for r in (rules or []))


def parse_path_lines(text: str) -> list:
    """One path per line; blanks dropped, whitespace trimmed (for protect)."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def parse_wait_for(text: str, timeout_text: str) -> "dict | None":
    """'<url|port|command> <target>' + a timeout string -> a wait_for spec, or
    None when empty/invalid (so a cleared field removes the spec). Pure."""
    parts = text.split(None, 1)
    if len(parts) < 2:
        return None
    wtype, target = parts[0], parts[1].strip()
    if wtype not in ("url", "port", "command") or not target:
        return None
    try:
        timeout = float(timeout_text.strip()) if timeout_text.strip() else 60.0
    except ValueError:
        timeout = 60.0
    return {"type": wtype, "target": target, "timeout": timeout}


def format_wait_for(spec: dict) -> tuple:
    """Inverse of parse_wait_for: (‘type target’, ‘timeout’) for pre-filling."""
    if not spec:
        return ("", "")
    line = f"{spec.get('type', '')} {spec.get('target', '')}".strip()
    t = spec.get("timeout")
    return (line, "" if t is None else str(t))
```

- [ ] **Step 4: Run the template test**

Add unit tests for the form conversions to `test/test_queue_templates.py`:

```python
# test/test_queue_templates.py  (append)
from _pkg.tui import (parse_guard_lines, format_guard_lines, parse_path_lines,
                      parse_wait_for, format_wait_for)


def test_parse_guard_lines():
    rules = parse_guard_lines("docker compose up\nplaywright test\n\n  \n")
    assert rules == [{"exe": "docker", "sub": ["compose", "up"]},
                     {"exe": "playwright", "sub": ["test"]}]


def test_guard_lines_roundtrip():
    rules = [{"exe": "docker", "sub": ["compose", "up"]}, {"exe": "xcodebuild", "sub": ["test"]}]
    assert parse_guard_lines(format_guard_lines(rules)) == rules


def test_parse_path_lines():
    assert parse_path_lines("/.git\n  /.env  \n\n/certs") == ["/.git", "/.env", "/certs"]


def test_parse_wait_for():
    assert parse_wait_for("url http://localhost:8080", "120") == {
        "type": "url", "target": "http://localhost:8080", "timeout": 120.0}
    assert parse_wait_for("port localhost:5432", "") == {
        "type": "port", "target": "localhost:5432", "timeout": 60.0}
    assert parse_wait_for("", "30") is None          # empty → no spec
    assert parse_wait_for("bogus x", "10") is None    # unknown type → no spec


def test_wait_for_roundtrip():
    spec = {"type": "url", "target": "http://localhost:8080", "timeout": 120.0}
    line, t = format_wait_for(spec)
    assert parse_wait_for(line, t) == spec
    assert format_wait_for(None) == ("", "")
```

Run: `python3 -m pytest test/test_queue_templates.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Write the failing editor test**

```python
# test/test_tui_queue.py  (append)
@pytest.mark.asyncio
async def test_editor_saves_a_resource(index_path, tmp_path, monkeypatch):
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "ios-sim"
        screen._template_key = "ios-sim"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "ios-sim")
    assert res is not None and res["kind"] == "device" and res["run_in"] == "worktree"


@pytest.mark.asyncio
async def test_editor_saves_guard_and_protect_for_root_dir(index_path, tmp_path, monkeypatch):
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "root"
        screen._template_key = "root-env"                # root-dir · sync
        screen.query_one("#res-guard", TextArea).text = "docker compose up"
        screen.query_one("#res-protect", TextArea).text = "/.git\n/.env\n/certs"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "root")
    assert res["kind"] == "root-dir"
    # A custom guard and a custom protect entry both round-trip into the save.
    assert {"exe": "docker", "sub": ["compose", "up"]} in res["guard"]
    assert "/certs" in res["sync"]["protect"]


@pytest.mark.asyncio
async def test_root_dir_path_is_main_worktree_not_the_selected_worktree(
        index_path, tmp_path, monkeypatch):
    # Finding-1 regression: standing on an arbitrary `git worktree add` node, the
    # saved root-dir path must be the repo's MAIN working tree (spec §1), not the
    # worktree we happened to select.
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "x", "-q"],
                   check=True, env=env)
    wt = tmp_path / "repo-feat"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", str(wt), "-b", "feat"],
                   check=True, env=env)
    pid = project_id.project_id(str(wt))   # same id as repo (git-common-dir)
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(wt), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "root"
        screen._template_key = "root-env"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "root")
    assert res["path"] == project_id.main_root(str(repo))
    assert res["path"] != str(wt)


@pytest.mark.asyncio
async def test_root_dir_ignores_path_edits_and_saves_wait_for(
        index_path, tmp_path, monkeypatch):
    # Finding 1: a typed path is ignored for root-dir (always canonical).
    # Finding 2: wait_for is editable and round-trips into the save.
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "root"
        screen._template_key = "root-env"
        screen.query_one("#res-path", Input).value = "/totally/wrong"   # tampered
        screen.query_one("#res-wait", Input).value = "url http://localhost:8080"
        screen.query_one("#res-wait-timeout", Input).value = "90"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "root")
    assert res["path"] == project_id.main_root(str(repo))     # tamper ignored
    assert res["wait_for"] == {"type": "url",
                               "target": "http://localhost:8080", "timeout": 90.0}


@pytest.mark.asyncio
async def test_editing_clears_stale_command_and_health(index_path, tmp_path, monkeypatch):
    # Finding 3: clearing a field in the editor removes the stale value (and
    # reverts a now-empty command strategy to 'none'), not leaves the old one.
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    queue_config.add_resource(
        qcfg, project_id=pid, display_path=str(repo), resource_id="db",
        resource={"kind": "port", "run_in": "worktree", "acquire": "command",
                  "release": "none", "command_acquire": "reset-db",
                  "health": "pg_isready"})
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id="db")
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-acq", Input).value = ""
        screen.query_one("#res-health", Input).value = ""
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "db")
    assert "command_acquire" not in res
    assert res["acquire"] == "none"         # reverted from 'command'
    assert "health" not in res


@pytest.mark.asyncio
async def test_malformed_wait_for_is_refused_not_dropped(index_path, tmp_path, monkeypatch):
    # Finding (polish): a non-empty but invalid readiness field must block the
    # save with an error, not silently behave like "no readiness check".
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-id", Input).value = "root"
        screen._template_key = "root-env"
        screen.query_one("#res-wait", Input).value = "urls http://localhost:8080"  # typo
        screen.action_save()
        await pilot.pause()
        assert "readiness" in str(screen.query_one("#res-error", Label).render()).lower()
    # Save was refused → nothing persisted.
    assert queue_config.get_resource(qcfg, pid, "root") is None
```

- [ ] **Step 6: Run to verify it fails**

Run: `python3 -m pytest test/test_tui_queue.py -q -k "editor_saves or guard_and_protect or main_worktree or ignores_path or clears_stale or malformed_wait"`
Expected: FAIL — placeholder editor has no `#res-id` / `#res-guard` / `action_save`.

- [ ] **Step 7: Replace the `ResourceEditorScreen` placeholder**

```python
class ResourceEditorScreen(_PanelScreen):
    """Template-first resource editor that reflows per kind (spec §6). Saves via
    queue_config.add_resource (which enforces the §2 invariants). Returns True on
    a successful save, False on cancel. The destructive test panel is mounted by
    the test-panel task."""

    BINDINGS = [
        Binding("escape", "dismiss(False)", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, *, project_root, project_id, config_path, resource_id) -> None:
        super().__init__()
        self._project_root = project_root
        self._project_id = project_id
        self._config_path = config_path
        self._resource_id = resource_id          # None == add
        self._template_key = "custom"
        from . import project_id as _pid
        # A root-dir resource's path is the repo's MAIN working tree, never the
        # selected tree node — which can be an arbitrary `git worktree add`
        # shown as its own project (spec §1). Derive it via the git-common-dir
        # helper; fall back to the node path if git can't resolve it.
        self._root_path = _pid.main_root(project_root) or project_root
        self._existing = None
        self._kind = "name"

    def compose(self) -> ComposeResult:
        from . import queue_config as _qc
        existing = (_qc.get_resource(self._config_path, self._project_id,
                                     self._resource_id) if self._resource_id else None)
        self._existing = existing
        if existing:
            self._template_key = "custom"   # edit: seed fields from the stored shape
        base = existing or template_resource("custom", path=self._root_path)
        self._kind = base.get("kind", "name")
        title = ("Edit resource" if existing else "Add resource") + \
                f" — {_basename(self._project_root)}"
        opts = [Option(t["title"], id=t["key"]) for t in QUEUE_TEMPLATES]
        yield Vertical(
            Label(title, classes="dialog-title"),
            Label("Template", classes="dialog-hint"),
            OptionList(*opts, id="res-template"),
            Input(value=self._resource_id or "", placeholder="resource id (e.g. ios-sim)",
                  id="res-id", disabled=bool(self._resource_id)),
            Label(f"kind: {self._kind}", id="res-kind", classes="dialog-hint"),
            Input(value=base.get("path", self._root_path),
                  placeholder="path (path-kind editable; root-dir is auto-derived)",
                  id="res-path"),
            Label("Guard — one 'exe sub…' rule per line (empty = unguarded)",
                  classes="dialog-hint"),
            TextArea(format_guard_lines(base.get("guard", [])), id="res-guard"),
            Label("Protect — root-only paths to keep, one per line (root-dir/path)",
                  id="res-protect-label", classes="dialog-hint"),
            TextArea("\n".join(base.get("sync", {}).get("protect", [])), id="res-protect"),
            Input(value=base.get("command_acquire", ""),
                  placeholder="acquire command (when acquire=command)", id="res-acq"),
            Input(value=base.get("command_release", ""),
                  placeholder="release command (optional)", id="res-rel"),
            Checkbox("release required (fail the run if release fails)",
                     value=bool(base.get("release_required")), id="res-req"),
            Input(value=base.get("health", ""),
                  placeholder="health check command (optional)", id="res-health"),
            Input(value=format_wait_for(base.get("wait_for"))[0],
                  placeholder="readiness: '<url|port|command> <target>' (optional)",
                  id="res-wait"),
            Input(value=format_wait_for(base.get("wait_for"))[1],
                  placeholder="readiness timeout seconds (default 60)", id="res-wait-timeout"),
            Label("", id="res-error", classes="dialog-hint"),
            Label("ctrl-s save · ctrl-t guard · ctrl-r dry-run · ctrl-h health · esc cancel",
                  classes="dialog-hint"),
            id="panel",
        )

    def on_mount(self) -> None:
        self._reflow(self._kind)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "res-template":
            return
        self._template_key = event.option.id or "custom"
        res = template_resource(self._template_key, path=self._root_path)
        self._kind = res["kind"]
        self.query_one("#res-kind", Label).update(f"kind: {res['kind']}")
        # Repopulate the form from the chosen template's defaults.
        self.query_one("#res-guard", TextArea).text = format_guard_lines(res.get("guard", []))
        self.query_one("#res-protect", TextArea).text = "\n".join(
            res.get("sync", {}).get("protect", []))
        self.query_one("#res-acq", Input).value = res.get("command_acquire", "")
        wline, wt = format_wait_for(res.get("wait_for"))
        self.query_one("#res-wait", Input).value = wline
        self.query_one("#res-wait-timeout", Input).value = wt
        self._reflow(res["kind"])

    def _reflow(self, kind: str) -> None:
        """Hide the path + protect inputs for non-file kinds (spec §6 reflow). A
        root-dir path is auto-derived (spec §1), so its input is shown read-only
        as the canonical main-worktree path; only a `path`-kind path is editable."""
        is_file = kind in ("root-dir", "path")
        path_input = self.query_one("#res-path", Input)
        path_input.display = is_file
        path_input.disabled = (kind == "root-dir")
        if kind == "root-dir":
            path_input.value = self._root_path     # canonical, not user-editable
        self.query_one("#res-protect", TextArea).display = is_file
        self.query_one("#res-protect-label", Label).display = is_file

    def _build_resource(self) -> dict:
        if self._existing is not None and self._template_key == "custom":
            res = dict(self._existing)
        else:
            res = template_resource(self._template_key, path=self._root_path)
        kind = res.get("kind")
        # root-dir path is ALWAYS the canonical main worktree (spec §1) — never
        # the editable input. Only a `path`-kind path is taken from the form.
        if kind == "root-dir":
            res["path"] = self._root_path
        elif kind == "path":
            p = self.query_one("#res-path", Input).value.strip()
            if p:
                res["path"] = p
        # The guard form is authoritative — an empty form means unguarded.
        res["guard"] = parse_guard_lines(self.query_one("#res-guard", TextArea).text)
        # Protect only has meaning when syncing; fold the form into the sync dict.
        if res.get("acquire") == "sync":
            sync = res.setdefault("sync", {"delete": True, "exclude": ["/.git"]})
            sync["protect"] = parse_path_lines(self.query_one("#res-protect", TextArea).text)
        # Command/health/wait_for: the form is authoritative, so a CLEARED field
        # removes the stale value (and reverts a now-empty command strategy to
        # 'none') rather than silently keeping the old one (Finding 3).
        acq = self.query_one("#res-acq", Input).value.strip()
        if acq:
            res["command_acquire"] = acq
            res["acquire"] = "command"
        else:
            res.pop("command_acquire", None)
            if res.get("acquire") == "command":
                res["acquire"] = "none"
        rel = self.query_one("#res-rel", Input).value.strip()
        if rel:
            res["command_release"] = rel
            res["release"] = "command"
        else:
            res.pop("command_release", None)
            if res.get("release") == "command":
                res["release"] = "none"
        res["release_required"] = self.query_one("#res-req", Checkbox).value
        health = self.query_one("#res-health", Input).value.strip()
        if health:
            res["health"] = health
        else:
            res.pop("health", None)
        wf = parse_wait_for(self.query_one("#res-wait", Input).value,
                            self.query_one("#res-wait-timeout", Input).value)
        if wf:
            res["wait_for"] = wf
        else:
            res.pop("wait_for", None)
        return res

    def action_save(self) -> None:
        from . import queue_config as _qc
        rid = self.query_one("#res-id", Input).value.strip()
        # A non-empty but unparseable readiness field is a typo, not "no
        # readiness" — refuse rather than silently drop it. (Empty still clears,
        # handled in _build_resource.) Kept here, not in _build_resource, so the
        # test-panel actions that also build the resource never raise on a typo.
        wait_text = self.query_one("#res-wait", Input).value.strip()
        if wait_text and parse_wait_for(
                self.query_one("#res-wait", Input).value,
                self.query_one("#res-wait-timeout", Input).value) is None:
            self.query_one("#res-error", Label).update(
                "[red]readiness must be '<url|port|command> <target>'[/]")
            return
        try:
            res = self._build_resource()
            _qc.add_resource(self._config_path, project_id=self._project_id,
                             display_path=self._project_root, resource_id=rid,
                             resource=res)
        except ValueError as e:
            self.query_one("#res-error", Label).update(f"[red]{e}[/]")
            return
        self.dismiss(True)
```

- [ ] **Step 8: Run tests**

Run: `python3 -m pytest test/test_tui_queue.py test/test_queue_templates.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_queue.py test/test_queue_templates.py
git commit -m "feat(queue-tui): resource editor — guard/protect editing, main-root path"
```

---

## Task 9: Editor test panel — guard tester, rsync dry-run, health probe

**Files:**
- Modify: `bin/_pkg/tui.py` — extend `ResourceEditorScreen` with the test panel
- New pure helper: `bin/_pkg/guard_match.py` (parse argv + match `{exe,sub}` rules)
- Test: `test/test_guard_match.py`, `test/test_tui_queue.py`

The test panel (§6) de-risks the destructive `sync`: a **guard-match tester** (type a command → *queued / runs-free*, no side effects), an **acquire dry-run** (`qsync.dry_run_deletions`, highlighting deletes), and a **health probe** (`probes.health_check`). Guard matching is pure (`{exe, sub}` over parsed argv, never substring) — factor it out so it is shared with the Phase 3 hook later and unit-tested now.

- [ ] **Step 1: Write the failing guard-match test**

```python
# test/test_guard_match.py
from _pkg import guard_match


RULES = [{"exe": "docker", "sub": ["compose", "up"]},
         {"exe": "playwright", "sub": ["test"]}]


def test_matches_exact_subcommand():
    assert guard_match.matches("docker compose up -d", RULES) is True


def test_basename_of_absolute_exe():
    assert guard_match.matches("/usr/local/bin/docker compose up", RULES) is True


def test_does_not_match_other_subcommand():
    assert guard_match.matches("docker ps", RULES) is False


def test_up_must_not_match_cleanup():
    assert guard_match.matches("npm run cleanup", RULES) is False


def test_strips_leading_cd_and_env_assignment():
    assert guard_match.matches("cd /x && FOO=1 docker compose up", RULES) is True


def test_unparseable_returns_false_fail_open():
    # command substitution / shell body it cannot confidently parse → no match.
    assert guard_match.matches("bash -c 'docker compose up'", RULES) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_guard_match.py -q`
Expected: FAIL — no `_pkg.guard_match`.

- [ ] **Step 3: Implement `guard_match.py`**

```python
# bin/_pkg/guard_match.py
"""Guard matching: does a shell command invoke a guarded executable+subcommand?

Pure and conservative (spec §2/§8): lex the command with `shlex`, split on
**whitespace-delimited** operators (&& || ; | &), take each simple command's
executable basename plus leading subcommand tokens, and match against {exe, sub?}
rules. NEVER a substring regex ('up' must not match 'cleanup').

CONTRACT / KNOWN LIMITS (do not overclaim — this is reused by the Phase-3 hook):
this is NOT a full shell parser. It fails **open** (returns no match) on anything
it cannot confidently lex: command substitution `$(…)`/backticks, heredocs,
unbalanced quotes, and operators written WITHOUT surrounding whitespace
(`a&&b`). Redirections (`>`, `2>&1`) and wrapper bodies (`bash -c "…"`,
`make`/`npm` targets that hide the command) are likewise not seen. For the TUI
tester a missed match is harmless (the user sees "runs free"); the Phase-3 deny
hook must keep the same fail-open posture (a false deny is worse than a missed
guard — §8) and may later harden this with a real parser. Tests assert only the
confidently-lexable cases plus the fail-open boundary.
"""

from __future__ import annotations

import os
import shlex
from typing import List


def _segments(command: str) -> List[List[str]]:
    """Split on &&/||/;/| into simple commands; return token lists. Returns []
    if the text cannot be confidently lexed (unbalanced quotes, etc.)."""
    if any(m in command for m in ("$(", "`", "<<")):
        return []  # command substitution / heredoc — refuse to guess
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    segs: List[List[str]] = []
    cur: List[str] = []
    for tok in tokens:
        if tok in ("&&", "||", ";", "|", "&"):
            if cur:
                segs.append(cur); cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


_PREFIXES = {"env", "command", "nohup", "time"}


def _strip_prefixes(seg: List[str]) -> List[str]:
    out = list(seg)
    # leading `cd DIR` is dropped by the && split already; drop VAR=val + wrappers.
    while out:
        head = out[0]
        if "=" in head and not head.startswith("-") and "/" not in head.split("=", 1)[0]:
            out = out[1:]; continue
        if head in _PREFIXES:
            out = out[1:]; continue
        break
    return out


def matches(command: str, rules: List[dict]) -> bool:
    """True iff any simple-command segment matches any {exe, sub?} rule."""
    if not rules:
        return False
    for seg in _segments(command):
        seg = _strip_prefixes(seg)
        if not seg:
            continue
        exe = os.path.basename(seg[0])
        rest = seg[1:]
        for rule in rules:
            if exe != rule.get("exe"):
                continue
            sub = rule.get("sub") or []
            if rest[:len(sub)] == sub:
                return True
    return False
```

- [ ] **Step 4: Run the guard-match test**

Run: `python3 -m pytest test/test_guard_match.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Write the failing test-panel test**

```python
# test/test_tui_queue.py  (append)
@pytest.mark.asyncio
async def test_editor_guard_tester_uses_edited_guard(index_path, tmp_path, monkeypatch):
    # The tester must reflect the CURRENT form (Finding 4), so set the guard via
    # the form, not just a template key, and confirm it's the matched rule set.
    import subprocess
    from _pkg import project_id
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#res-guard", TextArea).text = "docker compose up"
        screen.query_one("#test-cmd", Input).value = "docker compose up -d"
        screen.action_test_guard()
        await pilot.pause()
        assert "QUEUED" in str(screen.query_one("#test-out", Label).render()).upper()
        screen.query_one("#test-cmd", Input).value = "docker ps"
        screen.action_test_guard()
        await pilot.pause()
        assert "FREE" in str(screen.query_one("#test-out", Label).render()).upper()


@pytest.mark.asyncio
async def test_dry_run_refuses_when_source_equals_root(index_path, tmp_path, monkeypatch):
    # Finding 3: standing on the main root, source == dest, so a naive dry-run
    # would report "no deletions" (false safety). The panel must refuse instead.
    import subprocess
    from _pkg import project_id
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # project_root is the main root; the editor derives path = main_root too,
        # so source == dest.
        screen = ResourceEditorScreen(project_root=str(repo), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen._template_key = "root-env"     # root-dir · sync
        screen.action_dry_run()
        await pilot.pause()
        out = str(screen.query_one("#test-out", Label).render()).lower()
        assert "worktree source" in out
        assert "no deletions" not in out


@pytest.mark.asyncio
async def test_dry_run_surfaces_transition_guard_for_dirty_root(
        index_path, tmp_path, monkeypatch):
    # Finding 1: the dry-run must show the exclusive-or check, not just deletes.
    # From a worktree source over a DIRTY main root, it surfaces the uncommitted-
    # changes refusal that the real acquire would hit.
    import subprocess
    from _pkg import project_id
    from _pkg.tui import ResourceEditorScreen
    repo = tmp_path / "repo"; repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("v1")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init", "-q"],
                   check=True, env=env)
    wt = tmp_path / "repo-feat"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", str(wt), "-b", "feat"],
                   check=True, env=env)
    (repo / "tracked.txt").write_text("dirty")     # uncommitted change in root
    pid = project_id.project_id(str(wt))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Source = the worktree (distinct from the main-root dest).
        screen = ResourceEditorScreen(project_root=str(wt), project_id=pid,
                                      config_path=qcfg, resource_id=None)
        app.push_screen(screen)
        await pilot.pause()
        screen._template_key = "root-env"
        screen.action_dry_run()
        await pilot.pause()
        out = str(screen.query_one("#test-out", Label).render()).lower()
        assert "uncommitted changes" in out
```

- [ ] **Step 6: Run to verify it fails**

Run: `python3 -m pytest test/test_tui_queue.py -q -k "guard_tester or dry_run_refuses or surfaces_transition_guard"`
Expected: FAIL — no `#test-cmd` / `action_test_guard` / `action_dry_run`.

- [ ] **Step 7: Extend `ResourceEditorScreen.compose` (before the final hint Label) and add actions**

Add to the `Vertical(...)` in `compose`, just above the `Label("ctrl-s save…")`:

```python
            Label("Test panel", classes="dialog-title"),
            Input(placeholder="command to test against the guard", id="test-cmd"),
            Label("", id="test-out", classes="dialog-hint"),
            Label("Dry-run is safe only for sync and needs a worktree source "
                  "distinct from root; custom shells can't be simulated.",
                  classes="dialog-hint"),
```

Add the binding (in the editor's `BINDINGS`):

```python
        Binding("ctrl+t", "test_guard", "Test guard"),
```

Add the action methods:

```python
    def action_test_guard(self) -> None:
        from . import guard_match as _gm
        # Build from the CURRENT form (edits + existing saved guard), not the
        # bare template — otherwise an edited or existing guard isn't tested.
        rules = self._build_resource().get("guard") or []
        cmd = self.query_one("#test-cmd", Input).value.strip()
        if not cmd:
            return
        if _gm.matches(cmd, rules):
            self.query_one("#test-out", Label).update("[yellow]→ QUEUED (guarded)[/]")
        else:
            self.query_one("#test-out", Label).update("[green]→ RUNS FREE (unguarded)[/]")

    def action_dry_run(self) -> None:
        """rsync --dry-run preview for sync resources: deletions PLUS the
        exclusive-or check (spec §6) — a live root session and/or a dirty root
        that would block/refuse the real acquire, surfaced before the user trusts
        the preview."""
        from . import qsync as _qs, exclusive as _ex
        res = self._build_resource()
        if res.get("acquire") != "sync":
            self.query_one("#test-out", Label).update(
                "[dim]dry-run only applies to acquire=sync[/]")
            return
        # A real lease syncs from a WORKTREE over root. If the panel's source
        # (the selected node) is the root itself, rsync would diff root against
        # root and report "no deletions" — false safety. Refuse rather than lie.
        src = self._project_root
        if os.path.realpath(src) == os.path.realpath(res["path"]):
            self.query_one("#test-out", Label).update(
                "[yellow]dry-run needs a worktree source distinct from root — "
                "open this panel from a worktree session to preview deletions[/]")
            return
        lines = []
        # Exclusive-or check (spec §6): these would block/refuse the real acquire.
        # NB: _live_path() lives on the App, not this modal screen — use self.app.
        block = _ex.live_root_session(self.app._live_path(), res["path"])
        if block:
            lines.append(f"[red]⛔ root held by live session ‹{block.get('name')}› "
                         f"— acquire would block[/]")
        tg = _ex.transition_guard(res["path"])
        if tg:
            lines.append(f"[yellow]{tg}[/]")
        sync = res.get("sync", {})
        try:
            dels = _qs.dry_run_deletions(src, res["path"],
                                         exclude=sync.get("exclude", []),
                                         protect=sync.get("protect", []))
        except _qs.SyncDryRunError as e:
            lines.append(f"[red]dry-run failed: {e}[/]")
            self.query_one("#test-out", Label).update("\n".join(lines))
            return
        if dels:
            shown = ", ".join(dels[:6]) + (" …" if len(dels) > 6 else "")
            lines.append(f"[red]would DELETE {len(dels)}: {shown}[/]")
        else:
            lines.append("[green]no deletions[/]")
        self.query_one("#test-out", Label).update("\n".join(lines))

    def action_health(self) -> None:
        from . import probes as _p
        cmd = self.query_one("#res-health", Input).value.strip() or None
        ok, detail = _p.health_check(cmd)
        sev = "[green]" if ok else "[red]"
        self.query_one("#test-out", Label).update(f"{sev}health: {detail}[/]")
```

Add the `ctrl+r` (dry-run) and `ctrl+h` (health) bindings alongside `ctrl+t`:

```python
        Binding("ctrl+r", "dry_run", "Dry-run"),
        Binding("ctrl+h", "health", "Health"),
```

- [ ] **Step 8: Run tests**

Run: `python3 -m pytest test/test_tui_queue.py test/test_guard_match.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add bin/_pkg/tui.py bin/_pkg/guard_match.py test/test_guard_match.py test/test_tui_queue.py
git commit -m "feat(queue-tui): editor test panel (guard tester, dry-run, health)"
```

---

## Task 10: In-dialog `?` help + guide link

**Files:**
- Modify: `bin/_pkg/tui.py` — replace the `QueueHelpScreen` placeholder
- Test: `test/test_tui_queue.py`

§6 requires concise **offline** guidance (works on a remote box: no browser dependency) and a link to the guide shown as a **plain, copyable URL** (never relying on OSC-8 terminal hyperlinks), **plus** a best-effort click affordance for mouse users. The plain `https://…` GitHub URL is the guaranteed copy path; a `[link=…]` markup wrapper is the best-effort click (its absence in e.g. Terminal.app degrades to the plain URL still being visible). Lead with *when NOT to use this* and the `--delete`/`protect` rule. The repo URL matches `_help_text` (`tui.py:301`): `https://github.com/johan-lindahl/session-explorer`.

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui_queue.py  (append)
@pytest.mark.asyncio
async def test_queue_help_mentions_protect_and_guide(index_path):
    from _pkg.tui import QueueHelpScreen, _queue_help_text, QUEUE_GUIDE_URL
    text = _queue_help_text()
    assert "protect" in text.lower()
    assert "isolate" in text.lower()
    # The full, copyable GitHub URL must be present as plain text (not only a
    # repo-relative path and not hidden behind an OSC-8-only hyperlink).
    assert QUEUE_GUIDE_URL in text
    assert QUEUE_GUIDE_URL.startswith("https://github.com/")
    assert QUEUE_GUIDE_URL.endswith("/docs/queue-guide.md")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_tui_queue.py -q -k queue_help_mentions`
Expected: FAIL — no `_queue_help_text`.

- [ ] **Step 3: Replace the `QueueHelpScreen` placeholder + add the text helper**

```python
QUEUE_GUIDE_URL = ("https://github.com/johan-lindahl/session-explorer"
                   "/blob/main/docs/queue-guide.md")


def _queue_help_text() -> str:
    return "\n".join([
        "[b]Shared resources — quick help[/]",
        "",
        "[b]Isolate first.[/] If you can give each worktree its own port, DB, or",
        "derived-data dir, do that instead — this engine is for singletons that",
        "genuinely can't be isolated (one bind-mounted root, one simulator, one DB).",
        "",
        "[b]sync is destructive.[/] A root-dir 'sync' acquire runs",
        "[b]rsync --delete[/] from your worktree over the shared root — it blows",
        "away whatever the previous holder left. [b]protect[/] lists root-only",
        "paths to keep untouched (secrets, certs); [b].git/.env[/] are protected",
        "by default. Use the [b]dry-run[/] test (ctrl-r) to see deletions first.",
        "",
        "[b]Guards[/] are matched on the parsed command (exe + subcommand), never",
        "as substrings. Test a command with ctrl-t before relying on it.",
        "",
        # Plain, copyable URL (always visible) wrapped in a best-effort click
        # link — terminals without OSC-8 still show the bare URL to copy.
        "Full guide (opens / copyable):",
        f"  [link={QUEUE_GUIDE_URL}]{QUEUE_GUIDE_URL}[/link]",
    ])


class QueueHelpScreen(_PanelScreen):
    """Offline shared-resource help. esc closes."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(_queue_help_text()),
            Label("esc close", classes="dialog-hint"),
            id="panel",
        )
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest test/test_tui_queue.py -q -k queue_help`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_queue.py
git commit -m "feat(queue-tui): offline in-dialog queue help (?)"
```

---

## Task 11: New-session auto-slug + worktree-default-on / warn-on-plain-root

**Files:**
- Modify: `bin/_pkg/tui.py` — `NewSessionScreen` (`:407-455`), `action_new_session` (`:1342-1383`)
- Test: `test/test_tui_queue.py`

§9: checking *Create git worktree* auto-fills the worktree name with `worktree_slug(name)`; typing in the name field keeps it in sync until the user manually edits the worktree field. §5.4: when the project has a `root-dir` resource, the checkbox **defaults ON** and submitting a *plain root* session warns. `action_new_session` decides `root_is_shared` by checking the project's config for any `root-dir` kind.

- [ ] **Step 1: Write the failing test**

```python
# test/test_tui_queue.py  (append)
@pytest.mark.asyncio
async def test_new_session_autoslug_syncs_until_manual_edit(index_path):
    from _pkg.tui import NewSessionScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = NewSessionScreen("proj", name_prefix="", cwd="/x",
                                  root_is_shared=False)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#ns-wt", Checkbox).value = True
        await pilot.pause()
        name = screen.query_one("#ns-name", Input)
        name.value = "Sprint 14 Auth"
        screen.on_input_changed(Input.Changed(name, "Sprint 14 Auth"))
        assert screen.query_one("#ns-wtname", Input).value == "sprint-14-auth"


@pytest.mark.asyncio
async def test_new_session_defaults_worktree_on_for_root_dir_project(index_path):
    from _pkg.tui import NewSessionScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = NewSessionScreen("proj", name_prefix="", cwd="/x",
                                  root_is_shared=True)
        app.push_screen(screen)
        await pilot.pause()
        assert screen.query_one("#ns-wt", Checkbox).value is True


@pytest.mark.asyncio
async def test_manual_worktree_edit_persists_even_when_value_equals_slug(index_path):
    # Finding 4: a user edit to the worktree field stops auto-sync even when the
    # typed value happens to equal worktree_slug(name) — focus, not value, is the
    # signal, so retyping the same slug still counts as manual.
    from _pkg.tui import NewSessionScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = NewSessionScreen("proj", name_prefix="", cwd="/x", root_is_shared=True)
        app.push_screen(screen)
        await pilot.pause()
        name = screen.query_one("#ns-name", Input)
        wt = screen.query_one("#ns-wtname", Input)
        name.focus()
        await pilot.pause()
        name.value = "auth"
        screen.on_input_changed(Input.Changed(name, "auth"))
        assert wt.value == "auth"                 # auto-filled (name focused)
        # User focuses the worktree field and retypes the SAME value → manual.
        wt.focus()
        await pilot.pause()
        screen.on_input_changed(Input.Changed(wt, "auth"))
        # A later name change must NOT overwrite the manual worktree name.
        name.value = "auth two"
        screen.on_input_changed(Input.Changed(name, "auth two"))
        assert screen.query_one("#ns-wtname", Input).value == "auth"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_tui_queue.py -q -k "autoslug or defaults_worktree or manual_worktree_edit"`
Expected: FAIL — `NewSessionScreen` has no `root_is_shared` / `on_input_changed`.

- [ ] **Step 3: Modify `NewSessionScreen`**

Update `__init__` and `compose`, and add change handlers:

```python
    def __init__(self, project: str, name_prefix: str = "", cwd: str = "",
                 *, root_is_shared: bool = False) -> None:
        super().__init__()
        self._project = project
        self._name_prefix = name_prefix
        self._cwd = cwd
        self._root_is_shared = root_is_shared
        self._wt_manual = False  # set once the user edits the worktree field

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New session in '{self._project}' (use / to nest)",
                  classes="dialog-title"),
            Input(value=self._name_prefix, placeholder="session name", id="ns-name"),
            Input(value=self._cwd, placeholder="working directory", id="ns-cwd"),
            Checkbox("Create git worktree (-w)", value=self._root_is_shared, id="ns-wt"),
            Input(value=(worktree_slug(self._name_prefix) if self._root_is_shared else ""),
                  placeholder="worktree name (optional)", id="ns-wtname",
                  disabled=not self._root_is_shared),
            Label("enter create · esc cancel", classes="dialog-hint"),
            id="panel",
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "ns-name" and not self._wt_manual:
            if self.query_one("#ns-wt", Checkbox).value:
                # Programmatic auto-fill: happens while #ns-name has focus, so
                # the resulting #ns-wtname Changed below sees an unfocused field
                # and is NOT treated as a manual edit.
                self.query_one("#ns-wtname", Input).value = worktree_slug(event.value)
        elif event.input.id == "ns-wtname":
            # A *user* edit requires #ns-wtname to be focused (the user is typing
            # in it); our auto-fill writes it while #ns-name is focused. Using
            # focus — not value comparison — correctly handles a user retyping the
            # SAME slug, which a value check (value == slug(name)) would miss.
            if event.input.has_focus:
                self._wt_manual = True
```

(Keep the existing `on_checkbox_changed` enabling/disabling `#ns-wtname`, and also auto-fill the slug when the box is ticked:)

```python
    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "ns-wt":
            wt = self.query_one("#ns-wtname", Input)
            wt.disabled = not event.value
            if event.value and not self._wt_manual and not wt.value:
                wt.value = worktree_slug(self.query_one("#ns-name", Input).value)
```

- [ ] **Step 4: Pass `root_is_shared` from `action_new_session`**

In `action_new_session` (`tui.py:1383`), compute the flag and pass it; and add the plain-root warning in `after`:

```python
    def action_new_session(self) -> None:
        from . import project_id as _pid, queue_config as _qc
        project, prefix = self._project_and_prefix_for_cursor()
        if not project:
            self.bell(); return
        sessions = _index.load(self._index_path).get("sessions", {})
        default_cwd = _derive_project_cwd(sessions, project) or os.path.expanduser("~")
        pid = _pid.project_id(project)
        root_is_shared = bool(pid and any(
            r.get("kind") == "root-dir"
            for r in _qc.list_resources(self._queue_config_path(), pid).values()))

        def after(result: "dict | None") -> None:
            if not result:
                return
            if root_is_shared and not result["worktree"]:
                def confirmed(ok: bool) -> None:
                    if ok:
                        self._finish_new_session(project, result)
                self.push_screen(
                    ConfirmScreen("This project's root is a shared sandbox; a "
                                  "plain root session can be clobbered by a lease. "
                                  "Create it anyway?"),
                    confirmed)
                return
            self._finish_new_session(project, result)

        self.push_screen(
            NewSessionScreen(project, prefix, default_cwd,
                             root_is_shared=root_is_shared),
            after)
```

Move the body that builds/starts the session (the current lines `tui.py:1352-1381`) into a new `_finish_new_session(self, project, result)` method verbatim (it already reads `result["name"]`, `result["cwd"]`, `result["worktree"]`, `result["worktree_name"]`).

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest test/test_tui_queue.py test/test_tui.py -q`
Expected: PASS (new-session behavior preserved; slug + default-on covered)

- [ ] **Step 6: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_queue.py
git commit -m "feat(queue-tui): new-session auto-slug + worktree-default-on for shared root"
```

---

## Task 12: `queue_detect.py` — best-effort out-of-lease toast

**Files:**
- Create: `bin/_pkg/queue_detect.py`
- Test: `test/test_queue_detect.py`
- Modify: `bin/_pkg/tui.py` — `__init__`, `_poll_live`

§6/§9: a **best-effort, debounced** heuristic. Snapshot the `root-dir` resource's **top-level entry set + mtimes**; when it changes while the resource is in **neither valid exclusive-or state** — no lease holder **and** no live root session (a live root session legitimately owns root, spec §5, so its edits are *not* out-of-lease) — raise a transient toast. Honest limits (stated in the toast/help, not overclaimed): catches creates/deletes/renames, **misses in-place content writes**. The exclusion set is the **`protect` baseline + `.git` + an optional per-resource `detect_exclude` list**, matched as **globs** (so `.env.*` excludes `.env.local`); `detect_exclude` is the "known generated/served paths" source — schema-reserved, read if present, v1 doesn't surface it in the editor. This is a weak signal, never enforcement.

- [ ] **Step 1: Write the failing test**

```python
# test/test_queue_detect.py
from _pkg import queue_detect


def test_snapshot_lists_top_level_entries(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "sub").mkdir()
    snap = queue_detect.top_level_snapshot(str(tmp_path), exclude={".git"})
    assert "a.txt" in snap and "sub" in snap


def test_snapshot_excludes_protected(tmp_path):
    (tmp_path / ".env").write_text("secret")
    (tmp_path / "a.txt").write_text("1")
    snap = queue_detect.top_level_snapshot(str(tmp_path), exclude={".env"})
    assert ".env" not in snap and "a.txt" in snap


def test_snapshot_excludes_glob_protect_pattern(tmp_path):
    # A protect pattern like '.env.*' must exclude .env.local (Finding 3).
    (tmp_path / ".env.local").write_text("secret")
    (tmp_path / ".env.prod").write_text("secret")
    (tmp_path / "a.txt").write_text("1")
    snap = queue_detect.top_level_snapshot(str(tmp_path), exclude={".env.*"})
    assert ".env.local" not in snap and ".env.prod" not in snap
    assert "a.txt" in snap


def test_changed_detects_new_entry(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    before = queue_detect.top_level_snapshot(str(tmp_path), exclude=set())
    (tmp_path / "b.txt").write_text("2")
    after = queue_detect.top_level_snapshot(str(tmp_path), exclude=set())
    assert queue_detect.changed(before, after) is True


def test_changed_false_when_identical(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    s = queue_detect.top_level_snapshot(str(tmp_path), exclude=set())
    assert queue_detect.changed(s, dict(s)) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_queue_detect.py -q`
Expected: FAIL — no `_pkg.queue_detect`.

- [ ] **Step 3: Implement `queue_detect.py`**

```python
# bin/_pkg/queue_detect.py
"""Best-effort out-of-lease change detector for root-dir resources (spec §6/§9).

Snapshots the top-level entry set + mtimes of the shared root and compares
between polls. A change with no ticket held is a *weak* signal of out-of-lease
access — debounced and surfaced as a transient toast, never as enforcement.
Catches creates/deletes/renames; MISSES in-place content writes. Excludes the
protect baseline and any caller-supplied generated paths.

`exclude` entries are **glob patterns matched against the top-level entry name**
(via `fnmatch`), so a protect pattern like `.env.*` correctly excludes
`.env.local` — matching how the sync `protect` baseline is anchored. (Callers
strip the protect entries' leading `/` before passing them in.)
"""

from __future__ import annotations

import fnmatch
import os
from typing import Dict, Set


def _excluded(name: str, exclude: Set[str]) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in exclude)


def top_level_snapshot(path: str, *, exclude: Set[str]) -> Dict[str, float]:
    """{entry_name: mtime} for the immediate children of `path`, dropping any
    whose name matches an `exclude` glob (e.g. '.git', '.env', '.env.*')."""
    out: Dict[str, float] = {}
    try:
        with os.scandir(path) as it:
            for entry in it:
                if _excluded(entry.name, exclude):
                    continue
                try:
                    out[entry.name] = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    out[entry.name] = 0.0
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return {}
    return out


def changed(before: Dict[str, float], after: Dict[str, float]) -> bool:
    """True iff the entry set or any mtime differs (creates/deletes/renames)."""
    return before != after
```

- [ ] **Step 4: Run the pure test**

Run: `python3 -m pytest test/test_queue_detect.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Wire it into the TUI poll loop**

Add state in `__init__` (after the queue-pane state):

```python
        # Best-effort out-of-lease detector: per-resource last top-level snapshot
        # and a debounce set. A change-burst toasts once; the set re-arms after a
        # stable (unchanged) poll, so a *later* distinct change toasts again.
        self._detect_snaps: dict[str, dict] = {}
        self._detect_warned: set[str] = set()
```

Add a method and call it from `_poll_live` (after the `_render_queues` hook):

```python
    def _detect_out_of_lease(self) -> None:
        """Compare root-dir snapshots between polls; toast on a change while the
        resource is in NEITHER valid exclusive-or state — i.e. no lease holder
        AND no live root session (which legitimately owns root, spec §5). Weak
        signal (spec §6); excludes the protect baseline + globs."""
        from . import queue_detect as _qd, queue_view as _qv
        try:
            rows = _qv.snapshot(self._queue_config_path(), self._queues_root(),
                                self._live_path())
        except Exception:
            return
        from . import queue_config as _qc
        for r in rows:
            if r["kind"] != "root-dir":
                continue
            res = _qc.get_resource(self._queue_config_path(), r["project_id"],
                                   r["resource"]) or {}
            path = res.get("path")
            if not path:
                continue
            exclude = set(p.lstrip("/") for p in res.get("sync", {}).get("protect", []))
            exclude |= {".git"}
            # Optional generated/served-path exclusions (spec §6). Schema-reserved:
            # read if present, not yet editable in the v1 form.
            exclude |= set(p.lstrip("/") for p in res.get("detect_exclude", []))
            snap = _qd.top_level_snapshot(path, exclude=exclude)
            prev = self._detect_snaps.get(r["id"])
            self._detect_snaps[r["id"]] = snap
            if prev is None:
                continue
            # A live root session is a legitimate exclusive-or holder, so its
            # edits are NOT out-of-lease (spec §5) — treat it as "held" too.
            held = r["holder"] is not None or r["live_root_block"] is not None
            if _qd.changed(prev, snap):
                if not held and r["id"] not in self._detect_warned:
                    self._detect_warned.add(r["id"])
                    self.notify(f"⚠ possible out-of-lease access on "
                                f"{_basename(r['project'])}/{r['resource']}",
                                severity="warning")
            else:
                # Stable poll → re-arm so the NEXT distinct change warns again
                # (instead of one warning sticking forever while idle).
                self._detect_warned.discard(r["id"])
            if held:
                self._detect_warned.discard(r["id"])  # re-arm once a lease runs too
```

And in `_poll_live` (after `_render_queues`):

```python
        self._detect_out_of_lease()
```

- [ ] **Step 6: Write a Pilot test for the toast**

```python
# test/test_tui_queue.py  (append)
@pytest.mark.asyncio
async def test_out_of_lease_toast(index_path, tmp_path, monkeypatch):
    import subprocess
    from _pkg import project_id, queue_config
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    monkeypatch.setenv("SESSION_EXPLORER_QUEUES_ROOT", str(tmp_path / "queues"))
    queue_config.add_resource(
        qcfg, project_id=pid, display_path=str(repo), resource_id="root",
        resource={"kind": "root-dir", "path": str(repo), "run_in": "root",
                  "acquire": "sync", "release": "none",
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env", "/.env.*"]}})
    app = SessionExplorerApp(index_path=index_path)
    notices = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app.notify = lambda msg, **k: notices.append(msg)  # capture toasts
        app._detect_out_of_lease()                     # seed baseline
        (repo / "newfile.txt").write_text("x")          # out-of-lease change
        app._detect_out_of_lease()
        await pilot.pause()
    assert any("out-of-lease" in m for m in notices)


@pytest.mark.asyncio
async def test_out_of_lease_rearms_after_stable_poll(index_path, tmp_path, monkeypatch):
    # Finding 5: two distinct idle changes, with a stable poll between, must
    # produce TWO warnings — the debounce re-arms, it doesn't latch forever.
    import subprocess
    from _pkg import project_id, queue_config
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    monkeypatch.setenv("SESSION_EXPLORER_QUEUES_ROOT", str(tmp_path / "queues"))
    queue_config.add_resource(
        qcfg, project_id=pid, display_path=str(repo), resource_id="root",
        resource={"kind": "root-dir", "path": str(repo), "run_in": "root",
                  "acquire": "sync", "release": "none",
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env", "/.env.*"]}})
    app = SessionExplorerApp(index_path=index_path)
    notices = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app.notify = lambda msg, **k: notices.append(msg)
        app._detect_out_of_lease()              # seed baseline
        (repo / "f1.txt").write_text("x")        # change 1
        app._detect_out_of_lease()              # warn #1
        app._detect_out_of_lease()              # stable poll → re-arm
        (repo / "f2.txt").write_text("y")        # change 2
        app._detect_out_of_lease()              # warn #2
        await pilot.pause()
    assert len([m for m in notices if "out-of-lease" in m]) == 2


@pytest.mark.asyncio
async def test_no_toast_during_live_root_session(index_path, tmp_path, monkeypatch):
    # Finding 2: a live session working IN root is a valid exclusive-or holder
    # (spec §5), so its edits must NOT toast "out-of-lease".
    import subprocess
    from _pkg import project_id, queue_config, live
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    live_path = str(tmp_path / "live.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    monkeypatch.setenv("SESSION_EXPLORER_QUEUES_ROOT", str(tmp_path / "queues"))
    monkeypatch.setenv("SESSION_EXPLORER_LIVE", live_path)
    queue_config.add_resource(
        qcfg, project_id=pid, display_path=str(repo), resource_id="root",
        resource={"kind": "root-dir", "path": str(repo), "run_in": "root",
                  "acquire": "sync", "release": "none",
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env", "/.env.*"]}})
    # A live session whose cwd is the main root (an exclusive-or holder).
    live.record_event(live_path, event="SessionStart", session_id="rootsess",
                      cwd=str(repo), pid=os.getpid())
    app = SessionExplorerApp(index_path=index_path)
    notices = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app.notify = lambda msg, **k: notices.append(msg)
        app._detect_out_of_lease()              # seed
        (repo / "newfile.txt").write_text("x")   # change made under the live session
        app._detect_out_of_lease()
        await pilot.pause()
    assert not any("out-of-lease" in m for m in notices)
```

- [ ] **Step 7: Run tests**

Run: `python3 -m pytest test/test_queue_detect.py test/test_tui_queue.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add bin/_pkg/queue_detect.py test/test_queue_detect.py bin/_pkg/tui.py test/test_tui_queue.py
git commit -m "feat(queue-tui): best-effort out-of-lease detection toast"
```

---

## Task 13: `docs/queue-guide.md`

**Files:**
- Create: `docs/queue-guide.md`

§6: leads with *when NOT to use this* and the `--delete`/`protect` rule, then the template catalog. Rendered on github.com; linked from the in-dialog help.

- [ ] **Step 1: Write the guide**

```markdown
# Shared-resource queue — user guide

> Reach for this only when isolation is genuinely impossible.

## When NOT to use this

If each worktree can get its own resource, isolate instead — it is simpler and
has no destructive step:

- **Ports:** dynamic ports / `--project-name` per worktree.
- **Databases:** a per-worktree DB volume or schema.
- **Build/derived data:** per-job `-derivedDataPath` / build dir.

Use the queue **only** when the resource is heavy/slow to boot, tied to a fixed
path (bind-mounted) or well-known ports, or physically singular (one simulator,
one device, N license seats).

## The one dangerous primitive: `sync`

A `root-dir` resource with `acquire: sync` runs, on every acquire:

    rsync -a --delete <your-worktree>/ <shared-root>/

with anchored `--filter=exclude` rules. `--delete` means the acquire **removes
whatever the previous holder left** — that is the point (the next acquire is the
reset), but it also means anything in the shared root that is not protected and
not in your worktree is deleted.

- **`protect`** lists root-only paths to keep untouched (secrets, certs,
  fixtures). `/.git`, `/.env`, `/.env.*` are protected by default.
- **`exclude`** lists worktree junk that must not be copied in (`/.git`,
  `node_modules`).
- The first time a root enters sandbox mode, `queue-run` refuses until every
  untracked/gitignored path the dry-run would delete is classified as *protect*
  or *allow-delete*.

Always preview with the editor's **dry-run** (ctrl-r) before relying on a sync
resource.

## root is exclusive-or

A `root-dir` root is **either** a live working session **or** the lease sandbox,
never both. While a live Claude session is working in root, worktree leases
block (visible in the Queues pane). Create worktree sessions, not plain root
sessions, in shared-root projects — the new-session dialog defaults to this.

## Template catalog

| Template | Use when | kind · acquire · run_in |
|---|---|---|
| Bind-mounted stack, well-known ports | heavy Docker stack on fixed ports | root-dir · sync · root |
| Browser e2e vs fixed-URL app | Playwright/Cypress at a fixed baseURL | root-dir · sync · root |
| iOS simulator / xcodebuild | one simulator, global signing/build.db | device · none · worktree |
| Single shared database | one DB on a fixed socket/port | port · none by default (command once you add a DB-reset shell) · worktree |
| Root-only credentials / .env | secrets exist only at root | root-dir · sync (protect .env) · root |
| Single device / HIL / license seat | physically singular resource | device/name · none · worktree |

## Setting up

In the explorer, select a project and press **s**. Add a resource from a
template, edit its fields, test the guard and dry-run, and save. The Queues pane
(**q**) shows live holders and waiters across every opted-in project.
```

- [ ] **Step 2: Verify it renders (sanity)**

Run: `python3 -c "import pathlib; print(pathlib.Path('docs/queue-guide.md').read_text()[:80])"`
Expected: prints the title line.

- [ ] **Step 3: Commit**

```bash
git add docs/queue-guide.md
git commit -m "docs: shared-resource queue user guide"
```

---

## Task 14: SPEC.md + cutting-a-release checklist

**Files:**
- Modify: `SPEC.md` (add a "Queues pane (Phase 2)" subsection after the Phase 1 lease-engine section, ~line 590)
- Modify: `.claude/skills/cutting-a-release/SKILL.md`

- [ ] **Step 1: Add the Phase 2 spec section**

After the Phase 1 "Shared-resource lease engine" section in `SPEC.md`, add a subsection documenting: the `q`/`x` keymap change; the content-gated Queues pane reading `queue_view.snapshot`; the per-project `s` → resource-list → editor flow with the template catalog and test panel (guard tester / rsync dry-run / health); new-session auto-slug + worktree-default-on for root-dir projects; the best-effort detection toast and its honest limits; `ui_state.py` (`session-explorer-ui.json`); and the new pure modules (`queue_view`, `queue_detect`, `guard_match`). Mirror the load-bearing-decision phrasing used elsewhere in SPEC.md. Note the pane is **read-only** (cancellation stays CLI-only).

- [ ] **Step 2: Update the release checklist**

In `.claude/skills/cutting-a-release/SKILL.md`, add `docs/queue-guide.md` to the list of files to keep current (it must not silently diverge from the editor's help link), and confirm the existing "update `key(...)` lines AND matching `Binding(...)` rows iff keys changed" item covers the new `q`/`x`/`s` bindings.

- [ ] **Step 3: Run the full suite (no regressions)**

Run: `python3 -m pytest test/ -q`
Expected: PASS (all suites green)

- [ ] **Step 4: Commit**

```bash
git add SPEC.md .claude/skills/cutting-a-release/SKILL.md
git commit -m "docs(spec): Queues pane (Phase 2); add queue-guide to release checklist"
```

---

## Task 15: Release (version bump via the cutting-a-release skill)

**Files:**
- Modify: `bin/_pkg/__init__.py` (`__version__`), `.claude-plugin/plugin.json` (`version`), `README.md`, `CHANGELOG.md`, `SPEC.md` status/milestone lines

Phase 2 is a feature → **minor** bump (1.12.1 → 1.13.0). Per the `feedback_phased_delivery` memory, the bump happens **once, at the end** of the whole phase, not per task.

- [ ] **Step 1: Invoke the release skill**

Use the `cutting-a-release` skill (`.claude/skills/cutting-a-release/SKILL.md`) — it is the authoritative checklist. It bumps `__init__.py` + `plugin.json` to `1.13.0`, updates README/SPEC status lines, the help-screen keybinding section (already done in Task 4 — verify it matches the shipped keys `q`/`x`/`s`), adds a `CHANGELOG.md` section summarizing the Queues pane + setup dialogs + auto-slug + detection toast + guide, and creates the GitHub release.

- [ ] **Step 2: Verify version sync**

Run: `python3 -c "import json,re,pathlib; v=re.search(r'\"(.*?)\"', [l for l in pathlib.Path('bin/_pkg/__init__.py').read_text().splitlines() if '__version__' in l][0]).group(1); pj=json.loads(pathlib.Path('.claude-plugin/plugin.json').read_text())['version']; print(v, pj); assert v==pj=='1.13.0'"`
Expected: prints `1.13.0 1.13.0` with no assertion error.

- [ ] **Step 3: Full suite + tag**

Run: `python3 -m pytest test/ -q`
Expected: PASS. Then follow the skill to `gh release create v1.13.0`.

---

## Self-Review

**Spec coverage (build-order item 2):**
- Queues pane → Tasks 5, 6 (render + 2s loop). ✓
- `q`/`x` keymap → Task 4. ✓
- Two-level per-project setup/test dialog → Tasks 7 (list), 8 (editor), 9 (test panel). ✓
- New-session auto-slug → Tasks 3 (helper) + 11 (wiring + worktree-default-on/warn, §5.4/§9). ✓
- Detection toast → Task 12 (§6/§9 best-effort, honest limits). ✓
- `docs/queue-guide.md` + release checklist → Tasks 13, 14. ✓
- Offline `?` help + copyable guide link (no OSC-8 reliance) → Task 10 (§6). ✓
- Content-gated persistent pane visibility (`session-explorer-ui.json`) → Tasks 1, 5 (§9). ✓
- Pane read-only / cancellation stays CLI → noted in Task 14 (§6). ✓
- Guard matching on parsed argv, never substring → Task 9 `guard_match.py` (§2/§8). ✓
- rsync dry-run preview using exact Phase-1 filters → Task 9 `qsync.dry_run_deletions` (§6). ✓
- Editable destructive-safety fields (`guard`, `sync.protect`) → Task 8 (form `parse_guard_lines`/`parse_path_lines`), tested by `test_editor_saves_guard_and_protect_for_root_dir`. ✓
- v1-core `wait_for` readiness exposed in the editor (and in the `bind-mounted`/`browser-e2e` templates); a non-empty but invalid readiness field refuses the save with an error rather than silently dropping it (empty still clears) → Task 8 (`parse_wait_for`/`format_wait_for` + `action_save` guard), tested by `test_parse_wait_for`/`test_wait_for_roundtrip` + `test_root_dir_ignores_path_edits_and_saves_wait_for` + `test_malformed_wait_for_is_refused_not_dropped`. ✓
- root-dir `path` = canonical main working tree, read-only, not the selected node → Task 8 (`project_id.main_root`, `_reflow` disables it, `_build_resource` forces it), tested by `test_root_dir_path_is_main_worktree_not_the_selected_worktree` and `test_root_dir_ignores_path_edits_and_saves_wait_for` (§1). ✓
- Form authoritative on save (clearing a field removes the stale value) → Task 8 (`pop`-on-blank), tested by `test_editing_clears_stale_command_and_health`. ✓
- Dry-run never reports false safety when source == root → Task 9 (refuse + hint), tested by `test_dry_run_refuses_when_source_equals_root`. ✓
- Dry-run shows the **exclusive-or check** (live-root block + uncommitted-root transition guard), not just deletes → Task 9 (`exclusive.live_root_session`/`transition_guard`), tested by `test_dry_run_surfaces_transition_guard_for_dirty_root` (spec §6). ✓
- Detection toast re-arms after a stable poll (not latched) → Task 12, tested by `test_out_of_lease_rearms_after_stable_poll`. ✓
- Detector treats a **live root session as a valid holder** (no false toast during legitimate exclusive-or work) → Task 12 (`held |= live_root_block`), tested by `test_no_toast_during_live_root_session` (spec §5). ✓
- Detector excludes honor **glob protect patterns** (`.env.*` excludes `.env.local`) → Task 12 (`queue_detect` fnmatch), tested by `test_snapshot_excludes_glob_protect_pattern` (spec §6). ✓
- New-session manual worktree edit stops auto-sync via **focus**, robust to retyping the same slug → Task 11, tested by `test_manual_worktree_edit_persists_even_when_value_equals_slug` (spec §9). ✓

**Type/name consistency:** `queue_view.snapshot` row keys (`id`, `project_id`, `project`, `resource`, `kind`, `holder`, `waiting`, `live_root_block`, `active`) are produced in Task 2 and consumed unchanged in Tasks 5/6/12. `worktree_slug` (Task 3) is reused in Task 11. `QUEUE_TEMPLATES`/`template_resource`/`parse_guard_lines`/`format_guard_lines`/`parse_path_lines` (Task 8) are reused in Task 9's guard tester. `_build_resource` (Task 8) is the single source for both the guard tester and the dry-run in Task 9 (so both honor edits). `_basename` (Task 5) reused in Tasks 7/8/12. `_queue_config_path`/`_queues_root`/`_ui_path` (Task 5) reused throughout. The editor derives `_root_path` once in `__init__` (Task 8) and uses it for the path default, template builds, and dry-run source comparison.

**Sequencing note:** Task 7 introduces placeholder `ResourceEditorScreen`/`QueueHelpScreen` so the module imports; Tasks 8 and 10 replace them. If executed out of order, the placeholders keep tests green.

**Placeholder scan:** no "TBD"/"add error handling"/"similar to" — every code step shows complete code. The only intentional stub is the Task-4 `action_toggle_queues` pass-body, explicitly replaced in Task 5.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-shared-resource-queue-tui-phase-2.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with batched checkpoints.

Which approach?
