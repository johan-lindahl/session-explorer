# Worktree Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the explorer reclaim idle git-worktree directories — via a manual key, an on-exit prompt (the everyday path), and `--gc` pruning — without ever risking uncommitted work.

**Architecture:** A new `bin/_pkg/worktree.py` module owns the git/filesystem primitives (`remove`, `removable`, `size`), shared by the TUI (`tui.py`) and the retention GC (`gc.py`) so neither duplicates git-shelling and `gc.py` never imports Textual. Removal is non-destructive: `git worktree remove` runs **without `--force`** (git refuses dirty/untracked trees) and never deletes the branch, so a removed worktree is just a "dead" one that the existing `_recreate_worktree` rebuilds on resume.

**Tech Stack:** Python 3.11+, Textual (vendored), pytest + pytest-asyncio, `git` CLI via `subprocess`.

---

## File Structure

- **Create `bin/_pkg/worktree.py`** — pure-ish git/FS helpers: `remove(project_path) -> str`, `removable(project_path) -> bool`, `size(project_path) -> str`, plus `MARKER` and `root_of()`. One responsibility: worktree directory operations. No Textual import, so `gc.py` and `cli.py` can use it.
- **Modify `bin/_pkg/tui.py`** — preview-pane size line, `_offered_cleanup` set + the `_poll_live` on-exit prompt, the `w` manual action + binding, `_wt_size_cache`, help-text key + legend.
- **Modify `bin/_pkg/gc.py`** — `collect_worktrees()` pruning pass.
- **Modify `bin/_pkg/cli.py`** — call `collect_worktrees()` when `--gc` runs; new `--worktree-idle-days` is NOT added (constant default, YAGNI).
- **Create `test/test_worktree.py`** — unit + round-trip tests for the new module.
- **Modify `test/test_tui.py`** — preview size, on-exit prompt, manual `w` action tests.
- **Modify `test/test_gc.py`** — `collect_worktrees` tests.
- **Docs:** `SPEC.md`, `CLAUDE.md`, `CHANGELOG.md`, `bin/_pkg/__init__.py`, `plugin.json`.

---

## Task 1: Worktree primitive module

**Files:**
- Create: `bin/_pkg/worktree.py`
- Test: `test/test_worktree.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_worktree.py
import os
import subprocess as sp

from _pkg import worktree


def _init_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    sp.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "--allow-empty", "-m", "init"], cwd=path, check=True)


def _make_worktree(repo):
    """Create repo + a real worktree at <repo>/.claude/worktrees/feat on branch
    worktree-feat. Returns the worktree path."""
    _init_git_repo(repo)
    wt = str(repo / ".claude" / "worktrees" / "feat")
    sp.run(["git", "-C", str(repo), "worktree", "add", "-b", "worktree-feat", wt],
           check=True, capture_output=True)
    return wt


def test_root_of_and_marker(tmp_path):
    p = str(tmp_path / "repo" / ".claude" / "worktrees" / "x")
    assert worktree.root_of(p) == str(tmp_path / "repo")
    assert worktree.root_of(str(tmp_path / "plain")) is None


def test_remove_clean_worktree_keeps_branch(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    assert worktree.remove(wt) == "removed"
    assert not os.path.isdir(wt)                      # directory gone
    branches = sp.run(["git", "-C", str(repo), "branch", "--list", "worktree-feat"],
                      capture_output=True, text=True).stdout
    assert "worktree-feat" in branches                # branch (work) preserved


def test_remove_dirty_worktree_is_refused(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    open(os.path.join(wt, "dirty.txt"), "w").write("uncommitted")  # untracked file
    assert worktree.remove(wt) == "dirty"
    assert os.path.isdir(wt)                           # nothing removed


def test_removable_true_for_clean_false_for_dirty(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    assert worktree.removable(wt) is True
    open(os.path.join(wt, "u.txt"), "w").write("x")
    assert worktree.removable(wt) is False


def test_removable_false_when_dir_missing(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    assert worktree.removable(str(repo / ".claude" / "worktrees" / "gone")) is False


def test_size_returns_human_string(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    s = worktree.size(wt)
    assert s and s[0].isdigit()                        # e.g. "12K", "4.0K"


def test_remove_then_recreate_round_trip(tmp_path):
    """Removal is reversible: after remove(), the recreate path restores a real
    working tree on the same branch."""
    from _pkg.tui import _recreate_worktree
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    assert worktree.remove(wt) == "removed"
    assert _recreate_worktree(wt, str(repo)) is True
    assert os.path.exists(os.path.join(wt, ".git"))
    out = sp.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                 capture_output=True, text=True).stdout
    assert "worktree-feat" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_worktree.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.worktree'`.

- [ ] **Step 3: Implement `bin/_pkg/worktree.py`**

```python
"""Git-worktree directory operations, shared by the TUI and retention GC.

Removal is deliberately non-destructive: `git worktree remove` runs WITHOUT
--force, so git refuses any tree with uncommitted or untracked changes, and the
branch `worktree-<name>` is always kept. A removed worktree is therefore just a
"dead" worktree that `tui._recreate_worktree` rebuilds on resume.

No Textual import here on purpose — `gc.py` and `cli.py` import this module.
"""

from __future__ import annotations

import os
import subprocess

MARKER = "/.claude/worktrees/"


def root_of(project_path: "str | None") -> "str | None":
    """The parent repo root for a worktree path, or None if it isn't one."""
    if not project_path or MARKER not in project_path:
        return None
    return project_path.split(MARKER, 1)[0]


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args],
                          capture_output=True, text=True)


def removable(project_path: "str | None") -> bool:
    """True iff the directory exists and is clean (no modified or untracked
    files) — i.e. `git worktree remove` would succeed without --force."""
    if not project_path or not os.path.isdir(project_path):
        return False
    r = subprocess.run(["git", "-C", project_path, "status", "--porcelain"],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == ""


def remove(project_path: "str | None") -> str:
    """Remove a worktree's working directory, keeping its branch.

    Returns "removed" on success, "dirty" if git refused (uncommitted/untracked
    work — never forced), or "failed" for anything else. Callers must ensure the
    session is not live."""
    root = root_of(project_path)
    if not root or not os.path.isdir(root) or not os.path.isdir(project_path):
        return "failed"
    rc = _git(root, "worktree", "remove", project_path).returncode
    if rc == 0:
        _git(root, "worktree", "prune")
        return "removed"
    # git refused. Dirty if the dir survives and is no longer clean.
    if os.path.isdir(project_path) and not removable(project_path):
        return "dirty"
    return "failed"


def size(project_path: "str | None") -> str:
    """Human-readable on-disk size (e.g. "12M"), or "" if unavailable."""
    if not project_path or not os.path.isdir(project_path):
        return ""
    try:
        out = subprocess.run(["du", "-sh", project_path],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout:
            return out.stdout.split("\t", 1)[0].strip()
    except OSError:
        pass
    return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_worktree.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/worktree.py test/test_worktree.py
git commit -m "feat(worktree): non-destructive remove/removable/size primitives"
```

---

## Task 2: Worktree size in the preview pane

**Files:**
- Modify: `bin/_pkg/tui.py` (`_preview_text` ~137, `_refresh_preview` ~1818, `__init__` ~626)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to test/test_tui.py
def test_preview_text_shows_worktree_size_when_present():
    from _pkg.tui import _preview_text
    s = {"sid": "abc12345", "project_path": "/r/.claude/worktrees/f",
         "worktree_size": "42M", "name_cached": "feat"}
    assert "Worktree" in _preview_text(s)
    assert "42M" in _preview_text(s)


def test_preview_text_hides_worktree_size_for_root_session():
    from _pkg.tui import _preview_text
    s = {"sid": "abc12345", "project_path": "/r/proj", "name_cached": "x"}
    assert "Worktree" not in _preview_text(s)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py -k worktree_size -q`
Expected: FAIL — `assert "Worktree" in ...` fails (field not rendered yet).

- [ ] **Step 3: Add the `Worktree` field to `_preview_text`**

In `bin/_pkg/tui.py`, inside `_preview_text`, add a conditional field after the
`Path` line. Replace the `lines = [...]` list construction so it inserts the
worktree size only when present:

```python
    lines = [
        f"[b]{headline}[/]",
        "",
        field("Project", s.get("project_label") or "(unknown)"),
        field("Path", s.get("project_path") or "(unknown)"),
    ]
    if s.get("worktree_size"):
        lines.append(field("Worktree", f"{s['worktree_size']} on disk"))
    lines += [
        field("Folder", "/".join(segments) or "(none)"),
        field("Branch", s.get("branch") or "(none)"),
        field("Active", fmt_age(s.get("last_active_at"))),
        field("Created", (s.get("created_at") or "")[:10] or "—"),
        field("Messages", str(s.get("message_count", 0))),
        field("Context", context),
        field("Model", s.get("model") or "(unknown)"),
        field("Session", sid or "—"),
        "",
        "[b]Notes[/]",
        s.get("notes") or "(no notes)",
        "",
        "[b]First prompt[/]",
        s.get("first_prompt") or "(no first prompt recorded)",
        "",
        "[b]Transcript[/]",
        s.get("transcript_path") or "(unknown path)",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py -k worktree_size -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Inject the cached size in `_refresh_preview`**

Add a size cache in `__init__` (near the other live-state fields, ~line 645):

```python
        self._wt_size_cache: dict[str, str] = {}   # sid -> human size, lazy
        self._offered_cleanup: set[str] = set()     # sids already asked to clean
```

Then in `_refresh_preview`, before `self._preview.update(_preview_text(data))`
(the stopped-session branch ~line 1830), populate the size lazily for worktree
rows:

```python
        from . import worktree as _wt
        if _wt.MARKER in (data.get("project_path") or ""):
            if sid not in self._wt_size_cache:
                self._wt_size_cache[sid] = _wt.size(data.get("project_path"))
            data = {**data, "worktree_size": self._wt_size_cache[sid]}
        self._preview.update(_preview_text(data))
```

(Computing `du` only when a worktree row is previewed, and caching per-sid, keeps
the 2s preview-refresh timer cheap — same discipline as `worktree_state`.)

- [ ] **Step 6: Run the full tui suite to confirm no regressions**

Run: `python3 -m pytest test/test_tui.py -q`
Expected: PASS (all green).

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): show cached worktree disk size in the preview pane"
```

---

## Task 3: On-exit cleanup prompt (the everyday path)

**Files:**
- Modify: `bin/_pkg/tui.py` (`_poll_live` ~1585)
- Test: `test/test_tui.py`

This is the most-used trigger: when the docked worktree session exits clean, the
explorer offers to reclaim its directory — once.

- [ ] **Step 1: Write the failing test**

```python
# add to test/test_tui.py
async def test_docked_worktree_exit_offers_cleanup_once(tmp_path, monkeypatch):
    """When the docked session stops and its worktree is clean, _poll_live offers
    cleanup exactly once; confirming removes it and flips the glyph to dead."""
    import json
    from textual.screen import ModalScreen
    from _pkg import tui as tuimod
    from _pkg import live as livemod
    from _pkg.tui import SessionExplorerApp

    wt = str(tmp_path / "repo" / ".claude" / "worktrees" / "feat")
    idx = str(tmp_path / "i.json")
    json.dump({"version": 2, "sessions": {"s1": {
        "project_label": "repo", "project_path": wt, "name_cached": "feat",
        "last_active_at": "2026-06-01T10:00:00Z", "tokens_estimate": 1,
        "tokens_window_pct": 0, "message_count": 1, "first_prompt": "x"}}}, open(idx, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")

    removed = []
    monkeypatch.setattr(tuimod.worktree, "removable", lambda p: True)
    monkeypatch.setattr(tuimod.worktree, "remove",
                        lambda p: removed.append(p) or "removed")

    app = SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._docked_sid = "s1"
        app._live_states = {"s1": "idle"}            # was live
        # _poll_live does `from . import live as _live`, so patch the live module
        # itself: poll now reports the session as stopped.
        monkeypatch.setattr(livemod, "poll", lambda _p: {})
        app._poll_live(); await pilot.pause()
        assert isinstance(app.screen, ModalScreen)   # cleanup offered
        assert "s1" in app._offered_cleanup
        app.screen.dismiss(True); await pilot.pause()
        assert removed == [wt]                        # confirmed -> removed
        # A second poll must NOT re-offer (guarded by _offered_cleanup).
        app._poll_live(); await pilot.pause()
        assert not isinstance(app.screen, ModalScreen)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest test/test_tui.py -k docked_worktree_exit -q`
Expected: FAIL — `app._offered_cleanup` empty / no modal pushed.

- [ ] **Step 3: Add the import alias and the exit-detection hook**

At the top of `tui.py`, alongside the other `from . import` lines (~line 23),
add:

```python
from . import worktree
```

In `_poll_live`, capture the previously-live set at the very top of the method
(before `new_states` is computed) and call a new handler after the state update.
Replace the opening of `_poll_live`:

```python
    def _poll_live(self) -> None:
        """..."""  # keep existing docstring
        from . import live as _live
        prev_live = set(self._live_states)
        try:
            new_states = _live.poll(self._live_path())
        except Exception:
            return  # never let the indicator break the UI
```

Then, just before the final `if self._live_states:` metadata block (~line 1622),
add:

```python
        ended = prev_live - set(new_states)
        if ended:
            self._maybe_offer_worktree_cleanup(ended)
```

- [ ] **Step 4: Implement the handler**

Add this method next to `_set_worktree_state` (~line 1689):

```python
    def _maybe_offer_worktree_cleanup(self, ended: "set[str]") -> None:
        """When the docked session just stopped and its worktree is clean, offer
        to reclaim the directory — once per sid (tracked in _offered_cleanup).
        Dirty or non-worktree sessions are silently left alone."""
        sid = self._docked_sid
        if sid is None or sid not in ended or sid in self._offered_cleanup:
            return
        node = self._row_nodes.get(sid)
        path = (node[0].data or {}).get("project_path") if node else None
        if not path or worktree.MARKER not in path or not worktree.removable(path):
            return
        self._offered_cleanup.add(sid)
        size = self._wt_size_cache.get(sid) or worktree.size(path)

        def after(ok: bool) -> None:
            if not ok:
                return
            result = worktree.remove(path)
            if result == "removed":
                self._set_worktree_state(sid, "dead")
                self._wt_size_cache.pop(sid, None)
                self.notify(f"Worktree removed — {size} reclaimed.")
            elif result == "dirty":
                self.notify("Worktree has uncommitted changes — kept.",
                            severity="warning")
            else:
                self.notify("Could not remove the worktree (see "
                            "~/.claude/session-explorer.log).", severity="warning")

        self.push_screen(ConfirmScreen(
            f"Session '{sid[:8]}' ended. Remove its worktree to free {size}?\n"
            f"{path}\n(The branch and transcript are kept; resume rebuilds it.)"),
            after)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest test/test_tui.py -k docked_worktree_exit -q`
Expected: PASS (1 passed).

- [ ] **Step 6: Run the full tui + live suites for regressions**

Run: `python3 -m pytest test/test_tui.py test/test_tui_live.py -q`
Expected: PASS (all green).

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): offer worktree cleanup when a docked session exits clean"
```

---

## Task 4: Manual `w` action + help text

**Files:**
- Modify: `bin/_pkg/tui.py` (`BINDINGS` ~582, help text ~252/280, new action)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to test/test_tui.py
async def test_w_removes_clean_stopped_worktree(tmp_path, monkeypatch):
    import json
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    wt = str(tmp_path / "repo" / ".claude" / "worktrees" / "feat")
    idx = str(tmp_path / "i.json")
    json.dump({"version": 2, "sessions": {"s1": {
        "project_label": "repo", "project_path": wt, "name_cached": "feat",
        "last_active_at": "2026-06-01T10:00:00Z", "tokens_estimate": 1,
        "tokens_window_pct": 0, "message_count": 1, "first_prompt": "x",
        "worktree_state": "live"}}}, open(idx, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    removed = []
    monkeypatch.setattr(tuimod.worktree, "size", lambda p: "9M")
    monkeypatch.setattr(tuimod.worktree, "remove",
                        lambda p: removed.append(p) or "removed")
    app = SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        leaf = _find(app._tree.root, "feat")
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("w"); await pilot.pause()    # confirm dialog
        app.screen.dismiss(True); await pilot.pause()
        assert removed == [wt]


async def test_w_refuses_live_worktree(tmp_path, monkeypatch):
    import json
    from _pkg import tui as tuimod
    from _pkg.tui import SessionExplorerApp
    wt = str(tmp_path / "repo" / ".claude" / "worktrees" / "feat")
    idx = str(tmp_path / "i.json")
    json.dump({"version": 2, "sessions": {"s1": {
        "project_label": "repo", "project_path": wt, "name_cached": "feat",
        "last_active_at": "2026-06-01T10:00:00Z", "tokens_estimate": 1,
        "tokens_window_pct": 0, "message_count": 1, "first_prompt": "x",
        "worktree_state": "live"}}}, open(idx, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    called = []
    monkeypatch.setattr(tuimod.worktree, "remove", lambda p: called.append(p))
    app = SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._live_states = {"s1": "idle"}              # session is live
        leaf = _find(app._tree.root, "feat")
        app._tree.select_node(leaf); app._tree.cursor_line = leaf.line
        await pilot.pause()
        await pilot.press("w"); await pilot.pause()
        assert called == []                            # refused, never removed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_tui.py -k "removes_clean_stopped_worktree or refuses_live_worktree" -q`
Expected: FAIL — `w` is unbound (no action), nothing removed.

- [ ] **Step 3: Add the binding**

In the main `BINDINGS` list (~line 588, after the `d` delete binding), add:

```python
        Binding("w", "remove_worktree", "Remove worktree"),
```

- [ ] **Step 4: Implement the action**

Add next to `action_delete` (~line 1396):

```python
    def action_remove_worktree(self) -> None:
        """Reclaim the selected session's worktree directory (keeps branch +
        transcript; resume rebuilds it). Refuses live sessions; git refuses dirty
        ones."""
        node = self._tree.cursor_node
        data = node.data if (node and node.data) else {}
        sid = data.get("sid")
        path = data.get("project_path") or ""
        if not sid or worktree.MARKER not in path:
            self.bell()
            return
        running = set(self._running_sids()) if self._tmux_enabled else set()
        if sid in self._live_states or sid in running or sid == self._docked_sid:
            self.notify("Stop the session before removing its worktree.",
                        severity="warning")
            return
        if not os.path.isdir(path):
            self.notify("Worktree directory is already gone.", severity="warning")
            return
        size = self._wt_size_cache.get(sid) or worktree.size(path)

        def after(ok: bool) -> None:
            if not ok:
                return
            result = worktree.remove(path)
            if result == "removed":
                self._set_worktree_state(sid, "dead")
                self._wt_size_cache.pop(sid, None)
                self.notify(f"Worktree removed — {size} reclaimed.")
            elif result == "dirty":
                self.notify("Worktree has uncommitted changes — kept.",
                            severity="warning")
            else:
                self.notify("Could not remove the worktree (see "
                            "~/.claude/session-explorer.log).", severity="warning")

        self.push_screen(ConfirmScreen(
            f"Remove this worktree to free {size}?\n{path}\n"
            f"(The branch and transcript are kept; resume rebuilds it.)"), after)
```

Note: `_running_sids` already exists (used by `_poll_live`); it returns our tmux
window sids.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_tui.py -k "removes_clean_stopped_worktree or refuses_live_worktree" -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Add the help-screen entries**

In the `_help_text()` body, add a key row after the `d` row (~line 281):

```python
        key("w", "Remove the selected worktree's directory (branch + transcript kept)"),
```

And extend the Worktrees legend paragraph (~line 255) by appending one line after
the "Updated on rescan" line:

```python
        "Press [b]w[/] to reclaim a stopped worktree's directory — resume rebuilds it.",
```

- [ ] **Step 7: Run the full tui suite**

Run: `python3 -m pytest test/test_tui.py -q`
Expected: PASS (all green).

- [ ] **Step 8: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): add 'w' to remove a stopped worktree's directory"
```

---

## Task 5: `--gc` worktree pruning

**Files:**
- Modify: `bin/_pkg/gc.py` (new `collect_worktrees`), `bin/_pkg/cli.py` (~line 142)
- Test: `test/test_gc.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to test/test_gc.py
import os
import subprocess as sp
from datetime import datetime, timezone


def _git_repo_with_worktree(tmp_path, leaf="feat"):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    sp.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, check=True)
    wt = str(repo / ".claude" / "worktrees" / leaf)
    sp.run(["git", "-C", str(repo), "worktree", "add", "-b",
            f"worktree-{leaf}", wt], check=True, capture_output=True)
    return repo, wt


def _wt_index(tmp_path, sessions):
    import json
    idx = str(tmp_path / "i.json")
    json.dump({"version": 2, "sessions": sessions}, open(idx, "w"))
    return idx


def test_collect_worktrees_prunes_idle_clean(tmp_path):
    from _pkg.gc import collect_worktrees
    repo, wt = _git_repo_with_worktree(tmp_path)
    old = make_old_mtime(wt)  # see helper below; sets dir mtime 30d ago
    idx = _wt_index(tmp_path, {"s1": {
        "project_path": wt, "name_cached": "feat",
        "transcript_path": str(tmp_path / "t.jsonl")}})
    result = collect_worktrees(idx, idle_days=14)
    assert wt in result["removed_worktrees"]
    assert not os.path.isdir(wt)


def test_collect_worktrees_skips_dirty(tmp_path):
    from _pkg.gc import collect_worktrees
    repo, wt = _git_repo_with_worktree(tmp_path)
    open(os.path.join(wt, "u.txt"), "w").write("x")  # untracked -> dirty
    make_old_mtime(wt)  # backdate AFTER writing the file (writing bumps mtime)
    idx = _wt_index(tmp_path, {"s1": {"project_path": wt, "name_cached": "feat",
                                      "transcript_path": str(tmp_path / "t.jsonl")}})
    result = collect_worktrees(idx, idle_days=14)
    assert wt not in result["removed_worktrees"]
    assert result["skipped_dirty"] == 1
    assert os.path.isdir(wt)


def test_collect_worktrees_skips_fresh(tmp_path):
    from _pkg.gc import collect_worktrees
    repo, wt = _git_repo_with_worktree(tmp_path)  # mtime = now (fresh)
    idx = _wt_index(tmp_path, {"s1": {"project_path": wt, "name_cached": "feat",
                                      "transcript_path": str(tmp_path / "t.jsonl")}})
    result = collect_worktrees(idx, idle_days=14)
    assert wt not in result["removed_worktrees"]
    assert os.path.isdir(wt)


def test_collect_worktrees_dry_run_removes_nothing(tmp_path):
    from _pkg.gc import collect_worktrees
    repo, wt = _git_repo_with_worktree(tmp_path)
    make_old_mtime(wt)
    idx = _wt_index(tmp_path, {"s1": {"project_path": wt, "name_cached": "feat",
                                      "transcript_path": str(tmp_path / "t.jsonl")}})
    result = collect_worktrees(idx, idle_days=14, dry_run=True)
    assert wt in result["removed_worktrees"]
    assert os.path.isdir(wt)            # dry-run: still on disk


def make_old_mtime(path, days=30):
    """Backdate a directory's mtime so the idle check treats it as stale."""
    past = datetime.now(timezone.utc).timestamp() - days * 86400
    os.utime(path, (past, past))
    return past
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_gc.py -k collect_worktrees -q`
Expected: FAIL — `ImportError: cannot import name 'collect_worktrees'`.

- [ ] **Step 3: Implement `collect_worktrees` in `gc.py`**

Add to `bin/_pkg/gc.py` (it already imports `os`, `fcntl`, `datetime`):

```python
from . import worktree as _worktree

_WORKTREE_IDLE_DAYS = 14   # idle threshold for --gc worktree pruning


def collect_worktrees(index_path: str, *, idle_days: int = _WORKTREE_IDLE_DAYS,
                      dry_run: bool = False,
                      now: "datetime | None" = None) -> dict:
    """Reclaim idle, clean worktree directories (keeping branch + transcript;
    resume rebuilds them). Skips live sessions and any dir newer than the idle
    threshold; git refuses dirty trees (counted in skipped_dirty).

    Returns {"removed_worktrees": [...], "skipped_dirty": int,
             "skipped_live": int, "dry_run": bool}. Does NOT mutate the index —
    the transcript and the row stay; only the working directory is freed."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - idle_days * 86400

    removed: list[str] = []
    skipped_dirty = 0
    skipped_live = 0

    data = _index.load(index_path)
    for entry in data.get("sessions", {}).values():
        path = entry.get("project_path") or ""
        if _worktree.MARKER not in path or not os.path.isdir(path):
            continue
        transcript = entry.get("transcript_path")
        if transcript and os.path.exists(transcript) and _is_live(transcript, now):
            skipped_live += 1
            continue
        try:
            if os.path.getmtime(path) >= cutoff:
                continue   # too fresh
        except OSError:
            continue
        if not _worktree.removable(path):
            skipped_dirty += 1
            continue
        if dry_run:
            removed.append(path)
            continue
        if _worktree.remove(path) == "removed":
            removed.append(path)
        else:
            skipped_dirty += 1
    return {"removed_worktrees": removed, "skipped_dirty": skipped_dirty,
            "skipped_live": skipped_live, "dry_run": dry_run}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_gc.py -k collect_worktrees -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Wire it into the CLI**

In `bin/_pkg/cli.py`, in the `if args.gc:` block (~line 142), after the existing
`collect_garbage` call and its result print, add the worktree pass:

```python
        wt_result = _gc.collect_worktrees(path, dry_run=args.dry_run)
        if wt_result["removed_worktrees"]:
            verb = "would remove" if args.dry_run else "removed"
            print(f"worktrees {verb}: {len(wt_result['removed_worktrees'])}"
                  f" (skipped {wt_result['skipped_dirty']} dirty,"
                  f" {wt_result['skipped_live']} live)")
```

- [ ] **Step 6: Run the full gc + cli suites**

Run: `python3 -m pytest test/test_gc.py test/test_cli.py -q`
Expected: PASS (all green).

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/gc.py bin/_pkg/cli.py test/test_gc.py
git commit -m "feat(gc): prune idle, clean worktree directories on --gc"
```

---

## Task 6: Docs, SPEC, and release

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md`, `CHANGELOG.md`, `bin/_pkg/__init__.py`, `plugin.json`

- [ ] **Step 1: Update `SPEC.md`**

Add to the TUI key list the `w` binding, and add a "Worktree cleanup" subsection
documenting: removal is `git worktree remove` **without `--force`** (git refuses
dirty/untracked — the safety floor), the branch is never deleted, removal is
reversible via `_recreate_worktree` (resume rebuilds), the three triggers
(manual `w`, docked-exit prompt, `--gc` pruning at the 14-day idle threshold),
and the `cleanupPeriodDays = 36500` interaction (why native worktree auto-cleanup
is disabled and the explorer must own it). Note `collect_worktrees` does not
mutate the index — only the working directory is freed.

- [ ] **Step 2: Update `CLAUDE.md` load-bearing decisions**

Add a bullet under "Load-bearing design decisions":

```markdown
- **Worktree cleanup is non-destructive and explorer-owned.** Removal uses
  `git worktree remove` *without* `--force` (git refuses dirty/untracked trees)
  and never deletes the `worktree-<name>` branch, so a removed worktree is just a
  "dead" one that `_recreate_worktree` rebuilds on resume. The explorer owns this
  because Claude only offers native cleanup from the `-w`-creating process, and
  our retention `cleanupPeriodDays = 36500` disables Claude's age-based sweep.
  `--gc` (`gc.collect_worktrees`) may reclaim directories of *kept* sessions —
  the transcript and branch survive, so it's safe. Worktree git/FS primitives
  live in `bin/_pkg/worktree.py`; never reintroduce `--force`.
```

- [ ] **Step 3: Run the cutting-a-release skill**

Follow `.claude/skills/cutting-a-release/SKILL.md` exactly: bump
`bin/_pkg/__init__.py` `__version__` and `plugin.json` `version` (minor bump —
this is a feature: e.g. `1.11.4` → `1.12.0`), update the README/SPEC status lines
and the help-screen keybindings (the `w` key was added in Task 4 — confirm it's
reflected), add a `CHANGELOG.md` section for the new version describing the three
cleanup triggers, then create the GitHub release.

- [ ] **Step 4: Run the whole suite before release**

Run: `python3 -m pytest test/ -q && bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: PASS (all green).

- [ ] **Step 5: Commit + release**

Per the skill — commit the version bump and changelog, then `gh release create vX.Y.Z`.

---

## Self-Review notes

- **Spec coverage:** §1 primitive → Task 1; §2 manual `w` → Task 4; §3 disk-size
  visibility → Task 2; §4 on-exit prompt → Task 3 (sequenced early per the user's
  "most-used" priority); §5 `--gc` pruning → Task 5; §6 SPEC/docs → Task 6;
  §7 tests → distributed across every task (TDD). The spec's `_remove_worktree`
  name is realised as `worktree.remove()` in the shared module (the on-exit and
  manual handlers call it) — a deliberate decomposition refinement so `gc.py`
  needn't import Textual; noted here so it isn't read as a gap.
- **Idle threshold:** module constant `_WORKTREE_IDLE_DAYS = 14` in `gc.py`,
  matching the spec; no new CLI flag (YAGNI).
- **Type consistency:** `worktree.remove()` returns `"removed" | "dirty" |
  "failed"` everywhere; `collect_worktrees()` returns the documented dict in both
  `dry_run` and live paths; `_offered_cleanup` / `_wt_size_cache` are declared
  once in `__init__` (Task 2 Step 5) and used in Tasks 3 and 4.
```
