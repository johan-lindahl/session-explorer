# Session Summaries + Settings Screen + Delete-Worktree Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-session summaries (generated with `claude -p`, shown in a relocated bottom preview pane), a consolidated Settings screen, and worktree cleanup when a session is permanently deleted.

**Architecture:** Two new Textual-free modules — `summary.py` (sidecar store + digest + markers) and `summarize.py` (`claude -p` runner) — plus new primitives `worktree.purge`, `retention.disable`, and `ui_state.retention_days`. The TUI wires an auto-on-exit worker + a `u` action, relocates the preview pane from the right into the tree column, and adds a `SettingsScreen`. `delete_session` and `collect_garbage` cascade into `worktree.purge` and drop the summary entry.

**Tech Stack:** Python 3.11+, vendored Textual (no new deps), `git`, the `claude` CLI, `fcntl` flock + temp-file-rename stores, pytest + pytest-asyncio.

## Global Constraints

- **No new dependencies.** Only the vendored Textual under `bin/_pkg/_vendor/`. Standard library otherwise.
- **Python 3.11+**; CI runs 3.11–3.13 on ubuntu + macos.
- **All store writes use flock + temp-file-rename** (mirror `folder_store.py` / `index.py`).
- **Never `git ... --force`** and never destroy unmerged work: `git worktree remove` (no `--force`) and `git branch -d` (safe, merged-only) only.
- **`@work` workers must be guarded** — wrap the body in try/except → `_log_line`, matching `_live_meta_tick` (an unguarded worker exception exits the whole app).
- **`worktree.remove()` is unchanged** — the `w`/gc reclaim paths keep the branch. Only the new `worktree.purge()` (permanent delete) safe-deletes the branch.
- **`SPEC.md` is authoritative** — update it (and `CLAUDE.md`) in the same change (Task 14).
- **One PR, one version bump** — bump to **1.18.0** (minor) only in Task 14, at the very end, via the `cutting-a-release` skill.
- **Run tests with** `python3 -m pytest test/ -q`. Never run the full suite against a live tmux server (subprocess tests kill it) — CI/clean env only.

---

## File Structure

- **Create** `bin/_pkg/summary.py` — summaries sidecar store, `build_digest`, staleness, consent markers. Pure, no Textual, no subprocess.
- **Create** `bin/_pkg/summarize.py` — `claude -p` runner. No Textual.
- **Create** `test/test_summary.py`, `test/test_summarize.py`.
- **Modify** `bin/_pkg/worktree.py` — add `purge()`.
- **Modify** `bin/_pkg/retention.py` — add `disable()`.
- **Modify** `bin/_pkg/ui_state.py` — add `retention_days` field + `set_retention_days`/`get_retention_days`.
- **Modify** `bin/_pkg/delete.py` — cascade to summary + worktree, return the worktree outcome.
- **Modify** `bin/_pkg/gc.py` — read `retention_days` from ui_state, purge deleted stubs' worktrees, drop their summaries.
- **Modify** `hooks/session-start.sh` — bail on `SESSION_EXPLORER_SUMMARIZER=1` too.
- **Modify** `bin/_pkg/uninstall.py` — teardown the summaries file + markers.
- **Modify** `bin/_pkg/tui.py` — bottom preview relocation, `u` action + auto-on-exit worker, `SettingsScreen` + `,`, first-run prompt, summary merge into rows, delete confirm/report.
- **Modify** `test/conftest.py` — add `.session-explorer.summaries-prompted` to the `index_path` fixture.
- **Modify** existing test files: `test_worktree.py`, `test_retention.py`, `test_ui_state.py`, `test_delete.py`, `test_gc.py`, `test_uninstall.py`, `test_tui.py`.
- **Modify** docs: `SPEC.md`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `bin/_pkg/__init__.py`, `.claude-plugin/plugin.json`.

---

## Phase 1 — Stores + primitives

### Task 1: `summary.py` — sidecar store, markers, digest, staleness

**Files:**
- Create: `bin/_pkg/summary.py`
- Test: `test/test_summary.py`

**Interfaces:**
- Produces:
  - `default_path_for(index_path: str) -> str`
  - `load(path) -> dict` / `save(path, data) -> None` / `mutate(path, fn) -> dict`
  - `get(path, sid) -> dict | None` / `set(path, sid, entry: dict) -> None` / `remove(path, sid) -> None`
  - `build_digest(transcript_path, *, max_chars: int = 48000) -> str`
  - `is_stale(entry: dict, current_msg_count: int) -> bool`
  - `auto_marker(claude_dir) -> str` / `auto_enabled(claude_dir) -> bool` / `set_auto(claude_dir, on: bool) -> None`
  - `prompted(claude_dir) -> bool` / `mark_prompted(claude_dir) -> None`
  - Constant `MAX_DIGEST_CHARS = 48000`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_summary.py
import json
import os

from _pkg import summary


def test_set_get_remove_roundtrip(tmp_path):
    p = str(tmp_path / "sum.json")
    assert summary.get(p, "sid-1") is None
    summary.set(p, "sid-1", {"text": "did stuff", "generated_at": "2026-07-01T00:00:00Z",
                             "msg_count": 20, "model": "claude-haiku-4-5"})
    got = summary.get(p, "sid-1")
    assert got["text"] == "did stuff" and got["msg_count"] == 20
    summary.remove(p, "sid-1")
    assert summary.get(p, "sid-1") is None


def test_remove_missing_is_noop(tmp_path):
    p = str(tmp_path / "sum.json")
    summary.remove(p, "nope")  # must not raise


def test_load_corrupt_returns_default(tmp_path):
    p = str(tmp_path / "sum.json")
    open(p, "w").write("{not json")
    assert summary.load(p) == {"version": 1, "summaries": {}}


def test_default_path_is_sibling_of_index(tmp_path):
    idx = str(tmp_path / "se-index.json")
    assert summary.default_path_for(idx) == str(tmp_path / "session-explorer-summaries.json")


def test_build_digest_keeps_user_and_assistant_text_drops_tool_noise(tmp_path):
    t = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "message": {"content": "please refactor auth"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Sure, doing it."}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "BIG NOISY OUTPUT"}]}},
        {"type": "file-history-snapshot", "snapshot": "x" * 500},
    ]
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    d = summary.build_digest(str(t))
    assert "please refactor auth" in d
    assert "Sure, doing it." in d
    assert "BIG NOISY OUTPUT" not in d
    assert "file-history-snapshot" not in d


def test_build_digest_elides_when_too_long(tmp_path):
    t = tmp_path / "t.jsonl"
    many = [{"type": "user", "message": {"content": f"line {i} " + "x" * 200}} for i in range(2000)]
    t.write_text("\n".join(json.dumps(x) for x in many) + "\n")
    d = summary.build_digest(str(t), max_chars=1000)
    assert len(d) <= 1200  # cap + elision marker slack
    assert "…" in d  # middle elided


def test_is_stale(tmp_path):
    entry = {"msg_count": 20}
    assert summary.is_stale(entry, 25) is True
    assert summary.is_stale(entry, 20) is False


def test_auto_marker_toggle(tmp_path):
    cd = str(tmp_path)
    assert summary.auto_enabled(cd) is False
    summary.set_auto(cd, True)
    assert summary.auto_enabled(cd) is True
    summary.set_auto(cd, False)
    assert summary.auto_enabled(cd) is False


def test_prompted_marker(tmp_path):
    cd = str(tmp_path)
    assert summary.prompted(cd) is False
    summary.mark_prompted(cd)
    assert summary.prompted(cd) is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_summary.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named '_pkg.summary'`).

- [ ] **Step 3: Implement `summary.py`**

```python
# bin/_pkg/summary.py
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
_DEFAULT = {"version": 1, "summaries": {}}


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


def _claude_dir_join(claude_dir: str, name: str) -> str:
    return os.path.join(claude_dir, name)


def auto_marker(claude_dir: str) -> str:
    return _claude_dir_join(claude_dir, ".session-explorer.summaries-auto")


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
    return _claude_dir_join(claude_dir, ".session-explorer.summaries-prompted")


def prompted(claude_dir: str) -> bool:
    return os.path.exists(prompted_marker(claude_dir))


def mark_prompted(claude_dir: str) -> None:
    os.makedirs(claude_dir, exist_ok=True)
    open(prompted_marker(claude_dir), "a").close()
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_summary.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/summary.py test/test_summary.py
git commit -m "feat: summary sidecar store, digest builder, consent markers"
```

---

### Task 2: `summarize.py` — the `claude -p` runner

**Files:**
- Create: `bin/_pkg/summarize.py`
- Test: `test/test_summarize.py`

**Interfaces:**
- Produces:
  - `class SummaryError(Exception)`
  - `run(digest: str, *, model: str = SUMMARY_MODEL, timeout: float = SUMMARY_TIMEOUT) -> str`
  - Constants `SUMMARY_MODEL = "claude-haiku-4-5"`, `SUMMARY_TIMEOUT = 90.0`, `PROMPT`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_summarize.py
import subprocess

import pytest

from _pkg import summarize


def test_run_returns_trimmed_stdout(monkeypatch):
    captured = {}

    class FakeProc:
        def __init__(self, *a, **k):
            captured["args"] = a[0]
            captured["env"] = k.get("env")
            captured["input"] = k.get("input")
        def communicate(self, timeout=None):
            return ("  a short summary\n", "")
        @property
        def returncode(self):
            return 0
        def kill(self):
            pass

    monkeypatch.setattr(summarize.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: "/usr/bin/claude")
    out = summarize.run("USER: hi", model="claude-haiku-4-5")
    assert out == "a short summary"
    # guard env is set so our SessionStart hook leaves no trace
    assert captured["env"]["SESSION_EXPLORER_SUMMARIZER"] == "1"
    assert captured["env"]["SESSION_EXPLORER_PROBE"] == "1"
    # digest is piped on stdin, model flag present
    assert "USER: hi" in captured["input"]
    assert "claude-haiku-4-5" in captured["args"]


def test_run_raises_when_claude_missing(monkeypatch):
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: None)
    with pytest.raises(summarize.SummaryError):
        summarize.run("x")


def test_run_raises_on_nonzero(monkeypatch):
    class FakeProc:
        def __init__(self, *a, **k): pass
        def communicate(self, timeout=None): return ("", "boom")
        @property
        def returncode(self): return 1
        def kill(self): pass
    monkeypatch.setattr(summarize.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: "/usr/bin/claude")
    with pytest.raises(summarize.SummaryError):
        summarize.run("x")


def test_run_raises_on_timeout(monkeypatch):
    class FakeProc:
        def __init__(self, *a, **k): pass
        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)
        @property
        def returncode(self): return None
        def kill(self): pass
    monkeypatch.setattr(summarize.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: "/usr/bin/claude")
    with pytest.raises(summarize.SummaryError):
        summarize.run("x", timeout=0.01)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_summarize.py -q`
Expected: FAIL (no module `_pkg.summarize`).

- [ ] **Step 3: Implement `summarize.py`**

```python
# bin/_pkg/summarize.py
"""Summarise a transcript digest by shelling out to the Claude Code CLI.

Runs `claude -p` headless with the digest piped on stdin. Spawned with
SESSION_EXPLORER_SUMMARIZER=1 and SESSION_EXPLORER_PROBE=1 so our own
SessionStart hook bails at its early-exit guard — the summariser session records
no index row, current pointer, or GC. It uses no tools, so the pre-tool-use hook
never fires. No Textual import.
"""

from __future__ import annotations

import os
import shutil
import subprocess

SUMMARY_MODEL = "claude-haiku-4-5"
SUMMARY_TIMEOUT = 90.0

PROMPT = (
    "You are summarising a Claude Code session transcript for a session browser. "
    "In 3-5 sentences or short bullet points, say what the session was about and "
    "what was accomplished. Be concrete. No preamble, no heading — just the summary."
)


class SummaryError(Exception):
    """Raised when the summariser subprocess cannot produce a summary."""


def run(digest: str, *, model: str = SUMMARY_MODEL, timeout: float = SUMMARY_TIMEOUT) -> str:
    claude = shutil.which("claude")
    if not claude:
        raise SummaryError("claude CLI not found on PATH")

    env = dict(os.environ)
    env["SESSION_EXPLORER_SUMMARIZER"] = "1"
    env["SESSION_EXPLORER_PROBE"] = "1"

    argv = [claude, "-p", PROMPT, "--model", model]
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env,
        )
    except OSError as e:
        raise SummaryError(f"failed to launch claude: {e}") from e

    try:
        out, err = proc.communicate(input=digest, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        raise SummaryError("summariser timed out") from e

    if proc.returncode != 0:
        raise SummaryError(f"claude exited {proc.returncode}: {(err or '').strip()[:200]}")
    text = (out or "").strip()
    if not text:
        raise SummaryError("summariser returned empty output")
    return text
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_summarize.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/summarize.py test/test_summarize.py
git commit -m "feat: claude -p summariser runner with hook env guard"
```

---

### Task 3: `worktree.purge()` — safe dir + merged-only branch delete

**Files:**
- Modify: `bin/_pkg/worktree.py`
- Test: `test/test_worktree.py`

**Interfaces:**
- Consumes: `worktree.MARKER`, `worktree.root_of`, `worktree._git`, `worktree.removable`.
- Produces: `purge(project_path: str | None) -> str` returning `"removed"` | `"removed_branch_kept"` | `"dirty"` | `"error"`.

- [ ] **Step 1: Write the failing tests** (use a real throwaway git repo + worktree)

```python
# add to test/test_worktree.py
import os
import subprocess

from _pkg import worktree


def _init_repo_with_worktree(tmp_path, dirty=False, extra_commit=False):
    root = tmp_path / "repo"
    root.mkdir()
    def g(*a): subprocess.run(["git", "-C", str(root), *a], check=True,
                              capture_output=True, text=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (root / "f.txt").write_text("hi")
    g("add", "."); g("commit", "-qm", "init")
    wt = root / ".claude" / "worktrees" / "feat"
    wt.parent.mkdir(parents=True)
    g("worktree", "add", "-b", "worktree-feat", str(wt))
    if extra_commit:
        (wt / "new.txt").write_text("work")
        subprocess.run(["git", "-C", str(wt), "add", "."], check=True)
        subprocess.run(["git", "-C", str(wt), "commit", "-qm", "wip"], check=True)
    if dirty:
        (wt / "dirty.txt").write_text("uncommitted")
    return str(wt)


def test_purge_clean_merged_removes_dir_and_branch(tmp_path):
    wt = _init_repo_with_worktree(tmp_path)
    assert worktree.purge(wt) == "removed"
    assert not os.path.isdir(wt)


def test_purge_unmerged_branch_kept(tmp_path):
    wt = _init_repo_with_worktree(tmp_path, extra_commit=True)
    # clean tree, but branch has a commit not on main → git branch -d refuses
    assert worktree.purge(wt) == "removed_branch_kept"
    assert not os.path.isdir(wt)


def test_purge_dirty_leaves_everything(tmp_path):
    wt = _init_repo_with_worktree(tmp_path, dirty=True)
    assert worktree.purge(wt) == "dirty"
    assert os.path.isdir(wt)


def test_purge_non_worktree_path_is_error(tmp_path):
    assert worktree.purge(str(tmp_path)) == "error"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_worktree.py -q -k purge`
Expected: FAIL (`AttributeError: module '_pkg.worktree' has no attribute 'purge'`).

- [ ] **Step 3: Implement `purge` (append to `worktree.py`)**

```python
def purge(project_path: "str | None") -> str:
    """Permanent-delete cleanup: remove the working directory (no --force) AND
    safe-delete the branch `worktree-<leaf>` (git branch -d — refuses unmerged).

    Unlike remove(), this deletes the branch, because a permanently-deleted
    session has nothing left to rebuild. Returns:
      "removed"             dir gone (or absent) + branch deleted
      "removed_branch_kept" dir gone (or absent) but branch unmerged/absent
      "dirty"               git refused the dir (uncommitted/untracked work) — nothing changed
      "error"               not a worktree path / repo root missing
    """
    root = root_of(project_path)
    if not root or not os.path.isdir(root):
        return "error"
    if os.path.isdir(project_path):
        rc = _git(root, "worktree", "remove", project_path).returncode
        if rc != 0:
            if os.path.isdir(project_path) and not removable(project_path):
                return "dirty"
            return "error"
        _git(root, "worktree", "prune")
    leaf = project_path.split(MARKER, 1)[1].strip("/")
    if not leaf:
        return "removed_branch_kept"
    branch = f"worktree-{leaf}"
    rc = _git(root, "branch", "-d", branch).returncode
    return "removed" if rc == 0 else "removed_branch_kept"
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_worktree.py -q`
Expected: PASS (all, incl. the 4 new).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/worktree.py test/test_worktree.py
git commit -m "feat: worktree.purge — remove dir + safe (merged-only) branch delete"
```

---

### Task 4: `retention.disable()` + `ui_state.retention_days`

**Files:**
- Modify: `bin/_pkg/retention.py`, `bin/_pkg/ui_state.py`
- Test: `test/test_retention.py`, `test/test_ui_state.py`

**Interfaces:**
- Produces:
  - `retention.disable(claude_dir: str) -> None` — restore `cleanupPeriodDays` from backup, remove the backup file.
  - `ui_state.get_retention_days(path) -> int` (default 30), `ui_state.set_retention_days(path, days: int) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# add to test/test_retention.py
import json
from _pkg import retention


def test_disable_restores_prior_and_removes_backup(tmp_path):
    cd = str(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 45}))
    retention.enable(cd)  # backs up 45, sets 36500
    assert json.load(open(tmp_path / "settings.json"))["cleanupPeriodDays"] == 36500
    retention.disable(cd)
    assert json.load(open(tmp_path / "settings.json"))["cleanupPeriodDays"] == 45
    assert not retention.is_enabled(cd)  # backup gone


def test_disable_without_backup_is_noop(tmp_path):
    retention.disable(str(tmp_path))  # must not raise
```

```python
# add to test/test_ui_state.py
from _pkg import ui_state


def test_retention_days_default_and_roundtrip(tmp_path):
    p = str(tmp_path / "ui.json")
    assert ui_state.get_retention_days(p) == 30
    ui_state.set_retention_days(p, 7)
    assert ui_state.get_retention_days(p) == 7
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_retention.py test/test_ui_state.py -q -k "disable or retention_days"`
Expected: FAIL (attributes missing).

- [ ] **Step 3: Implement**

Append to `retention.py`:

```python
def disable(claude_dir: str) -> None:
    """Turn retention back off: restore the backed-up cleanupPeriodDays and drop
    the backup file (which is the 'enabled' signal). No-op when not enabled.
    Review-sensitive: this rewrites settings.json."""
    bp = backup_path(claude_dir)
    if not os.path.exists(bp):
        return
    try:
        prior = int(open(bp, encoding="utf-8").read().strip())
    except (ValueError, OSError):
        prior = _DEFAULT_PRIOR
    sp = _settings_path(claude_dir)
    try:
        with open(sp, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    data["cleanupPeriodDays"] = prior
    os.makedirs(claude_dir, exist_ok=True)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.unlink(bp)
```

Add to `ui_state.py`: extend `_DEFAULT` and add accessors.

```python
_DEFAULT: Dict[str, Any] = {"version": 1, "queue_pane_visible": False, "retention_days": 30}
```

```python
def get_retention_days(path: str) -> int:
    try:
        return int(load(path).get("retention_days", 30))
    except (TypeError, ValueError):
        return 30


def set_retention_days(path: str, days: int) -> None:
    data = load(path)
    data["retention_days"] = int(days)
    save(path, data)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_retention.py test/test_ui_state.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/retention.py bin/_pkg/ui_state.py test/test_retention.py test/test_ui_state.py
git commit -m "feat: retention.disable + ui_state.retention_days"
```

---

### Task 5: Hook env guard for the summariser

**Files:**
- Modify: `hooks/session-start.sh:12`
- Test: `test/hook.bats`

**Interfaces:** none (shell). The summariser sets `SESSION_EXPLORER_SUMMARIZER=1` (Task 2); the hook must bail on it just like `SESSION_EXPLORER_PROBE=1`.

- [ ] **Step 1: Add a failing bats test**

```bash
# add to test/hook.bats
@test "summariser sessions leave no trace" {
  run env SESSION_EXPLORER_SUMMARIZER=1 bash "${HOOK}" <<< '{"session_id":"s","transcript_path":"/x","cwd":"/y","source":"startup"}'
  [ "$status" -eq 0 ]
  [ ! -f "${CLAUDE_DIR}/session-explorer-index.json" ]
}
```

(Use the same `HOOK`/`CLAUDE_DIR` setup the existing hook.bats `setup()` defines — mirror an existing probe test in that file for the exact variable names.)

- [ ] **Step 2: Run to verify it fails**

Run: `bats test/hook.bats -f "summariser"`
Expected: FAIL (index file gets written because the guard isn't there yet).

- [ ] **Step 3: Edit the guard line**

In `hooks/session-start.sh`, change:

```bash
if [ "${SESSION_EXPLORER_PROBE:-}" = "1" ]; then exit 0; fi
```
to:
```bash
if [ "${SESSION_EXPLORER_PROBE:-}" = "1" ] || [ "${SESSION_EXPLORER_SUMMARIZER:-}" = "1" ]; then exit 0; fi
```

- [ ] **Step 4: Run to verify pass**

Run: `bats test/hook.bats`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/session-start.sh test/hook.bats
git commit -m "feat: SessionStart hook bails for summariser sessions"
```

---

## Phase 2 — Delete cascades

### Task 6: `delete_session` cascades to summary + worktree

**Files:**
- Modify: `bin/_pkg/delete.py`
- Test: `test/test_delete.py`

**Interfaces:**
- Consumes: `summary.remove`, `summary.default_path_for`, `worktree.MARKER`, `worktree.purge`.
- Produces: `delete_session(index_path, session_id) -> str | None` — the worktree purge outcome (`"removed"`/`"removed_branch_kept"`/`"dirty"`/`"error"`) or `None` when the session had no worktree.

- [ ] **Step 1: Write the failing tests**

```python
# add to test/test_delete.py
import json
import os

from _pkg import delete as _delete
from _pkg import summary as _summary


def _write_index(tmp_path, entry):
    p = str(tmp_path / "se-index.json")
    json.dump({"version": 1, "sessions": {"sid-1": entry}}, open(p, "w"))
    return p


def test_delete_drops_summary_entry(tmp_path):
    idx = _write_index(tmp_path, {"project_path": "/tmp/x"})
    sp = _summary.default_path_for(idx)
    _summary.set(sp, "sid-1", {"text": "s", "msg_count": 5})
    _delete.delete_session(idx, "sid-1")
    assert _summary.get(sp, "sid-1") is None


def test_delete_returns_none_without_worktree(tmp_path):
    idx = _write_index(tmp_path, {"project_path": "/tmp/plain"})
    assert _delete.delete_session(idx, "sid-1") is None


def test_delete_purges_worktree(tmp_path, monkeypatch):
    wt = "/repo/.claude/worktrees/feat"
    idx = _write_index(tmp_path, {"project_path": wt})
    calls = {}
    monkeypatch.setattr("_pkg.worktree.purge", lambda p: calls.setdefault("p", p) or "removed")
    assert _delete.delete_session(idx, "sid-1") == "removed"
    assert calls["p"] == wt
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_delete.py -q`
Expected: FAIL (return value / summary drop not implemented).

- [ ] **Step 3: Rewrite `delete.py`**

```python
"""Delete a session: removes the JSONL, the index entry, its summary, and —
for a worktree session — the worktree directory + branch (safe, merged-only)."""

from __future__ import annotations

import os

from . import index as _index


def delete_session(index_path: str, session_id: str):
    """Returns the worktree purge outcome (see worktree.purge) or None when the
    session had no worktree."""
    captured: dict = {}

    def mutator(data: dict) -> dict:
        entry = data.get("sessions", {}).pop(session_id, None)
        if entry:
            captured["entry"] = entry
            transcript = entry.get("transcript_path")
            if transcript and os.path.exists(transcript):
                try:
                    os.unlink(transcript)
                except OSError:
                    pass
        return data

    _index.mutate(index_path, mutator)

    from . import summary as _summary
    _summary.remove(_summary.default_path_for(index_path), session_id)

    entry = captured.get("entry") or {}
    path = entry.get("project_path") or ""
    from . import worktree as _worktree
    if _worktree.MARKER in path:
        return _worktree.purge(path)
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_delete.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/delete.py test/test_delete.py
git commit -m "feat: delete_session cascades to summary + worktree purge"
```

---

### Task 7: `collect_garbage` — configurable period, worktree + summary cascade

**Files:**
- Modify: `bin/_pkg/gc.py`
- Test: `test/test_gc.py`

**Interfaces:**
- Consumes: `ui_state.default_path_for`, `ui_state.get_retention_days`, `worktree.MARKER`, `worktree.purge`, `summary.default_path_for`, `summary.remove`.
- Produces: `collect_garbage(index_path, *, retention_days: int | None = None, dry_run=False, now=None) -> dict` — same dict as before plus `"removed_worktrees": [path, ...]`. When `retention_days` is None it resolves from `ui_state`.

- [ ] **Step 1: Write the failing tests**

```python
# add to test/test_gc.py
import json
import os
from datetime import datetime, timezone

from _pkg import gc as _gc
from _pkg import ui_state as _ui


def _old_stub(tmp_path, project_path):
    p = str(tmp_path / "se-index.json")
    json.dump({"version": 1, "sessions": {"stub": {
        "name_cached": None, "project_path": project_path,
        "last_active_at": "2020-01-01T00:00:00Z",
    }}}, open(p, "w"))
    return p


def test_gc_reads_retention_days_from_ui_state(tmp_path):
    idx = _old_stub(tmp_path, "/tmp/plain")
    _ui.set_retention_days(_ui.default_path_for(idx), 5)
    now = datetime(2020, 1, 4, tzinfo=timezone.utc)  # only 3 days old < 5 → kept
    res = _gc.collect_garbage(idx, now=now)
    assert res["removed"] == []


def test_gc_purges_worktree_of_deleted_stub(tmp_path, monkeypatch):
    wt = "/repo/.claude/worktrees/feat"
    idx = _old_stub(tmp_path, wt)
    seen = {}
    monkeypatch.setattr("_pkg.worktree.purge", lambda p: seen.setdefault("p", p) or "removed")
    res = _gc.collect_garbage(idx)  # default period 30, stub is ancient
    assert res["removed"] == ["stub"]
    assert seen["p"] == wt
    assert wt in res["removed_worktrees"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_gc.py -q -k "retention_days or purges_worktree"`
Expected: FAIL.

- [ ] **Step 3: Edit `gc.py`**

Change the signature and resolve the period; collect worktree paths in the mutator, purge + drop summaries after `mutate`:

```python
def collect_garbage(index_path: str, *, retention_days: "int | None" = None,
                    dry_run: bool = False,
                    now: "datetime | None" = None) -> dict:
    from . import ui_state as _ui
    if retention_days is None:
        retention_days = _ui.get_retention_days(_ui.default_path_for(index_path))
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - retention_days * 86400

    removed: list[str] = []
    removed_worktrees: list[str] = []
    wt_paths: dict[str, str] = {}   # sid -> worktree path (collected in the mutator)
    skipped_live = 0
```

In the `dry_run` branch, add `removed_worktrees` to the returned dict (empty list is fine for dry-run; leave real purging out of dry-run):

```python
        return {"removed": removed, "skipped_live": skipped_live,
                "removed_worktrees": [], "dry_run": True}
```

In the `mutator`, when a row is dropped, record its worktree path before dropping:

```python
            if exists:
                try:
                    os.unlink(transcript)
                except OSError:
                    pass
            path = entry.get("project_path") or ""
            if _worktree.MARKER in path:
                wt_paths[sid] = path
            removed.append(sid)
```

After `_index.mutate(index_path, mutator)` and before the return, purge worktrees and drop summaries (outside the index lock):

```python
    _index.mutate(index_path, mutator)

    from . import summary as _summary
    sp = _summary.default_path_for(index_path)
    for sid in removed:
        _summary.remove(sp, sid)
        wt = wt_paths.get(sid)
        if wt and _worktree.purge(wt) in ("removed", "removed_branch_kept"):
            removed_worktrees.append(wt)

    return {"removed": removed, "skipped_live": skipped_live,
            "removed_worktrees": removed_worktrees, "dry_run": False}
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_gc.py -q`
Expected: PASS (existing gc tests still pass — the extra `removed_worktrees` key is additive).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/gc.py test/test_gc.py
git commit -m "feat: gc reads configurable retention period, purges deleted stubs' worktrees + summaries"
```

---

## Phase 3 — Summaries wiring (TUI)

### Task 8: Auto-on-exit summariser worker

**Files:**
- Modify: `bin/_pkg/tui.py` (constants; `_poll_live` at ~2150; new `_maybe_summarize` + guarded worker)
- Test: `test/test_tui.py`

**Interfaces:**
- Consumes: `summary.auto_enabled`, `summary.build_digest`, `summary.set`, `summary.default_path_for`, `summarize.run`, `_index.load`.
- Produces: `SessionExplorerApp._maybe_summarize(ended: set[str]) -> None`; constant `SUMMARY_MIN_MSGS = 8`.

- [ ] **Step 1: Write the failing test**

```python
# add to test/test_tui.py
async def test_auto_summary_on_exit(index_path, tmp_path, monkeypatch):
    import json
    from _pkg import summary as _summary
    # Named session, enough messages, with a transcript on disk.
    data = json.load(open(index_path))
    t = tmp_path / "t.jsonl"
    t.write_text('{"type":"user","message":{"content":"hi there"}}\n')
    data["sessions"]["sid-1"]["transcript_path"] = str(t)
    data["sessions"]["sid-1"]["message_count"] = 20
    json.dump(data, open(index_path, "w"))
    _summary.set_auto(str(tmp_path), True)
    monkeypatch.setattr("_pkg.summarize.run", lambda digest, **k: "AUTO SUMMARY")

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._docked_sid = "sid-1"
        app._maybe_summarize({"sid-1"})
        await pilot.pause()
        await pilot.pause()
    sp = _summary.default_path_for(index_path)
    assert _summary.get(sp, "sid-1")["text"] == "AUTO SUMMARY"


async def test_auto_summary_skipped_when_disabled(index_path, tmp_path, monkeypatch):
    import json
    from _pkg import summary as _summary
    data = json.load(open(index_path))
    t = tmp_path / "t.jsonl"; t.write_text('{"type":"user","message":{"content":"hi"}}\n')
    data["sessions"]["sid-1"]["transcript_path"] = str(t)
    data["sessions"]["sid-1"]["message_count"] = 20
    json.dump(data, open(index_path, "w"))
    # auto NOT enabled
    monkeypatch.setattr("_pkg.summarize.run", lambda *a, **k: "NOPE")
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._docked_sid = "sid-1"
        app._maybe_summarize({"sid-1"})
        await pilot.pause()
    assert _summary.get(_summary.default_path_for(index_path), "sid-1") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_tui.py -q -k auto_summary`
Expected: FAIL (`_maybe_summarize` missing).

- [ ] **Step 3: Implement**

Add near the other module constants in `tui.py` (e.g. by `SNAPSHOT_POLL_INTERVAL`):

```python
SUMMARY_MIN_MSGS = 8  # skip auto-summaries for sessions shorter than this
```

In `_poll_live`, right after the existing cleanup call:

```python
        ended = prev_live - set(new_states)
        if ended:
            self._maybe_offer_worktree_cleanup(ended)
            self._maybe_summarize(ended)
```

Add the method + guarded worker (model on `_refresh_live_metadata`/`_live_meta_tick`):

```python
    def _maybe_summarize(self, ended: "set[str]") -> None:
        """Auto-summarise the docked session when it just stopped, if the user
        enabled auto-summaries and the session is named + long enough."""
        from . import summary as _summary
        if not _summary.auto_enabled(self._claude_dir()):
            return
        sid = self._docked_sid
        if not sid or sid not in ended:
            return
        try:
            entry = _index.load(self._index_path).get("sessions", {}).get(sid) or {}
        except Exception:
            return
        if not entry.get("name_cached"):
            return
        if int(entry.get("message_count") or 0) < SUMMARY_MIN_MSGS:
            return
        tp = entry.get("transcript_path")
        if not tp or not os.path.exists(tp):
            return
        self._summarize_worker(sid, tp, entry.get("message_count") or 0)

    @work(thread=True, group="summarize")
    def _summarize_worker(self, sid: str, transcript_path: str, msg_count: int) -> None:
        self._summarize_tick(sid, transcript_path, msg_count)

    def _summarize_tick(self, sid: str, transcript_path: str, msg_count: int) -> None:
        """Guarded worker body: build digest → claude -p → store → refresh.
        @work defaults to exit_on_error=True, so a failure here must log + skip,
        never take the app down (see the live-meta worker)."""
        from . import summary as _summary
        from . import summarize as _summarize
        try:
            digest = _summary.build_digest(transcript_path)
            if not digest.strip():
                return
            text = _summarize.run(digest)
            from datetime import datetime, timezone
            _summary.set(_summary.default_path_for(self._index_path), sid, {
                "text": text,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "msg_count": int(msg_count),
                "model": _summarize.SUMMARY_MODEL,
            })
            self.call_from_thread(self._refresh_preview)
        except Exception:
            import traceback
            _log_line("summary generation failed (skipped):\n" + traceback.format_exc())
```

*(Note: `datetime.now` is fine here — this is runtime app code, not a Workflow script.)*

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_tui.py -q -k auto_summary`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat: auto-summarise docked session on exit (guarded worker)"
```

---

### Task 9: `u` — Update (regenerate) the selected session's summary

**Files:**
- Modify: `bin/_pkg/tui.py` (BINDINGS ~770; `check_action` ~866; new `action_update_summary`)
- Test: `test/test_tui.py`

**Interfaces:**
- Consumes: the worker from Task 8; `self._live_states`, `self._running_sids`, `self._docked_sid`.
- Produces: `action_update_summary(self) -> None`; binding `u`.

- [ ] **Step 1: Write the failing test**

```python
# add to test/test_tui.py
async def test_u_summarises_selected_session(index_path, tmp_path, monkeypatch):
    import json
    from _pkg import summary as _summary
    data = json.load(open(index_path))
    t = tmp_path / "t.jsonl"; t.write_text('{"type":"user","message":{"content":"hi"}}\n')
    data["sessions"]["sid-1"]["transcript_path"] = str(t)
    data["sessions"]["sid-1"]["message_count"] = 3  # below threshold, but u bypasses it
    json.dump(data, open(index_path, "w"))
    monkeypatch.setattr("_pkg.summarize.run", lambda *a, **k: "MANUAL SUMMARY")
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "down", "down")  # to sid-1 leaf
        await pilot.press("u")
        await pilot.pause(); await pilot.pause()
    assert _summary.get(_summary.default_path_for(index_path), "sid-1")["text"] == "MANUAL SUMMARY"


async def test_u_refuses_live_session(index_path, tmp_path, monkeypatch):
    monkeypatch.setattr("_pkg.summarize.run", lambda *a, **k: "SHOULD NOT RUN")
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._live_states = {"sid-1": "idle"}
        await pilot.press("down", "down", "down")
        await pilot.press("u")
        await pilot.pause()
    from _pkg import summary as _summary
    assert _summary.get(_summary.default_path_for(index_path), "sid-1") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_tui.py -q -k "u_summarises or u_refuses"`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `BINDINGS`:

```python
        Binding("u", "update_summary", "Summarise"),
```

Add `"update_summary"` to the `check_action` modal-guard tuple (line ~866, the long `if action in (...)`).

Add the action:

```python
    def action_update_summary(self) -> None:
        node = self._tree.cursor_node
        data = node.data if (node and node.data) else {}
        sid = data.get("sid")
        if not sid:
            self.bell()
            return
        running = set(self._running_sids()) if self._tmux_enabled else set()
        if sid in self._live_states or sid in running or sid == self._docked_sid:
            self.notify("Stop the session before summarising it.", severity="warning")
            return
        tp = data.get("transcript_path")
        if not tp or not os.path.exists(tp):
            self.notify("No transcript on disk to summarise yet.", severity="warning")
            return
        self.notify("Summarising…")
        self._summarize_worker(sid, tp, data.get("message_count") or 0)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_tui.py -q -k "u_summarises or u_refuses"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat: 'u' updates the selected session's summary on demand"
```

---

## Phase 4 — UI

### Task 10: Relocate preview to the bottom + Summary section + filter merge

**Files:**
- Modify: `bin/_pkg/tui.py` (`compose` ~908; `CSS` ~758; `_preview_text` ~222; `_populate` — the summary merge)
- Test: `test/test_tui.py`

**Interfaces:**
- Consumes: `summary.load`, `summary.default_path_for`, `summary.is_stale`.
- Produces: preview rendered inside `#treepane`; `_preview_text` shows a `Summary` block; `_populate` merges `s["summary"]` + `s["summary_msg_count"]`.

- [ ] **Step 1: Write the failing tests**

```python
# add to test/test_tui.py
async def test_preview_is_in_treepane_not_horizontal(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # preview must be a descendant of the treepane vertical
        tp = app.query_one("#treepane")
        assert app._preview in tp.walk_children()


async def test_preview_shows_summary(index_path, tmp_path):
    from _pkg import summary as _summary
    _summary.set(_summary.default_path_for(index_path), "sid-1",
                 {"text": "Refactored auth.", "msg_count": 18, "model": "x",
                  "generated_at": "2026-07-01T00:00:00Z"})
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down", "down", "down")  # sid-1
        await pilot.press("space")                 # open preview
        await pilot.pause()
        assert "Refactored auth." in str(app._preview.render())
```

```python
# add to test/test_format or test_tui: pure _preview_text check
def test_preview_text_has_summary_block():
    from _pkg.tui import _preview_text
    out = _preview_text({"sid": "s", "name_cached": "x", "summary": "did things",
                         "message_count": 5, "summary_msg_count": 5})
    assert "Summary" in out and "did things" in out


def test_preview_text_marks_stale_summary():
    from _pkg.tui import _preview_text
    out = _preview_text({"sid": "s", "name_cached": "x", "summary": "old",
                         "message_count": 30, "summary_msg_count": 10})
    assert "may be stale" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_tui.py -q -k "preview_is_in_treepane or preview_shows_summary or preview_text_has_summary or stale_summary"`
Expected: FAIL.

- [ ] **Step 3: Implement**

`compose` — move `self._preview` into the treepane Vertical (drop the wrapping `Horizontal`):

```python
        yield Vertical(self._colheader, self._tree, self._preview,
                       self._queues, self._empty, id="treepane")
```
(Remove the old `yield Horizontal(Vertical(...), self._preview)`; `Horizontal` may now be an unused import — leave it, other screens use `Vertical` only; verify no other `Horizontal` use before removing the import.)

`CSS` — change the `#preview` rule:

```python
    #preview { height: auto; max-height: 40%; padding: 0 1; border-top: solid $accent; }
```

`_preview_text` — insert a Summary block. Replace the `Notes`→`First prompt` tail:

```python
    lines += [
        "",
        "[b]Notes[/]",
        s.get("notes") or "(no notes)",
        "",
        _summary_header(s),
        s.get("summary") or "(no summary — press u to generate)",
        "",
        "[b]First prompt[/]",
        s.get("first_prompt") or "(no first prompt recorded)",
        "",
        "[b]Transcript[/]",
        s.get("transcript_path") or "(unknown path)",
    ]
    return "\n".join(lines)
```

Add the helper above `_preview_text`:

```python
def _summary_header(s: dict) -> str:
    from . import summary as _summary
    if s.get("summary") and _summary.is_stale(
            {"msg_count": s.get("summary_msg_count") or 0}, s.get("message_count") or 0):
        return "[b]Summary (may be stale)[/]"
    return "[b]Summary[/]"
```

`_populate` — after loading the index sessions and before building rows, merge summaries into each `s` dict. Find where `_populate` iterates sessions and enrich the per-session dict it attaches to nodes. Add near the top of `_populate` (after `data = _index.load(...)`):

```python
        from . import summary as _summary
        _sums = _summary.load(_summary.default_path_for(self._index_path)).get("summaries", {})
```
Then when composing each session's `data`/`s` dict for its row (where the node data is built), add:
```python
            _se = _sums.get(sid)
            if _se:
                s = {**s, "summary": _se.get("text"),
                     "summary_msg_count": _se.get("msg_count")}
```
(Attach the enriched `s` to the leaf's `data`, exactly where the existing code sets `node.data`. `_matches` already searches `s.get("summary")`, so filtering now works.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_tui.py -q`
Expected: PASS (incl. existing preview/toggle tests — verify none asserted the old right-side layout).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat: relocate preview to bottom pane, add Summary block + filter merge"
```

---

### Task 11: Settings screen (`,`)

**Files:**
- Modify: `bin/_pkg/tui.py` (new `SettingsScreen`; `BINDINGS`; `check_action`; `action_settings`; row helpers `_settings_rows`/`_settings_activate`)
- Test: `test/test_tui.py`

**Interfaces:**
- Consumes: `summary.auto_enabled`/`set_auto`; `retention.is_enabled`/`enable`/`disable`; `ui_state.get_retention_days`/`set_retention_days`; `_usage_enabled`/`_start_usage`/`_stop_usage`; `_queue_visible`/`action_toggle_queues`; `_tmux.available`.
- Produces: `SettingsScreen(_PanelScreen)`; `SessionExplorerApp._settings_rows() -> list[tuple[str, str]]`; `_settings_activate(row_id: str) -> None`; binding `,` → `action_settings`.

- [ ] **Step 1: Write the failing tests**

```python
# add to test/test_tui.py
async def test_settings_toggles_auto_summary(index_path, tmp_path):
    from _pkg import summary as _summary
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert _summary.auto_enabled(str(tmp_path)) is False
        app._settings_activate("auto_summary")
        assert _summary.auto_enabled(str(tmp_path)) is True
        app._settings_activate("auto_summary")
        assert _summary.auto_enabled(str(tmp_path)) is False


async def test_settings_toggles_retention(index_path, tmp_path):
    import json
    (tmp_path / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 30}))
    from _pkg import retention
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._settings_activate("retention")
        assert retention.is_enabled(str(tmp_path)) is True
        app._settings_activate("retention")
        assert retention.is_enabled(str(tmp_path)) is False


async def test_settings_rows_reflect_state(index_path, tmp_path):
    from _pkg import summary as _summary
    _summary.set_auto(str(tmp_path), True)
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        rows = dict(app._settings_rows())
        assert "[x]" in rows["auto_summary"]


async def test_comma_opens_settings_screen(index_path):
    from _pkg.tui import SessionExplorerApp, SettingsScreen
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(",")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_tui.py -q -k settings or comma`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `BINDINGS`:

```python
        Binding("comma", "settings", "Settings"),
```

Add `"settings"` to the `check_action` modal-guard tuple.

Add the row helpers on the app (state → labels, and activation). Place near `action_toggle_usage`:

```python
    def _settings_rows(self) -> "list[tuple[str, str]]":
        """(row_id, label) for the Settings screen, reflecting current state."""
        from . import summary as _summary
        from . import retention
        from . import ui_state as _ui
        cd = self._claude_dir()
        def box(on): return "[x]" if on else "[ ]"
        ret_on = retention.is_enabled(cd)
        days = _ui.get_retention_days(self._ui_path())
        rows = [
            ("auto_summary", f"{box(_summary.auto_enabled(cd))} Auto-summarise sessions on exit"),
            ("retention", f"{box(ret_on)} Auto-delete unnamed sessions"),
            ("retention_days", f"      after {days} days" + ("" if ret_on else "  (enable above first)")),
            ("usage", f"{box(self._usage_enabled())} Usage bar"),
            ("queues", f"{box(self._queue_visible)} Queues pane"),
        ]
        from . import tmux as _tmux
        if _tmux.available() or self._tmux_enabled:
            rows.append(("tmux", "    tmux hosting: on"))
        else:
            rows.append(("tmux", "    tmux hosting: not set up  — Enter to set up"))
        return rows

    def _settings_activate(self, row_id: str) -> None:
        from . import summary as _summary
        from . import retention
        from . import ui_state as _ui
        cd = self._claude_dir()
        if row_id == "auto_summary":
            _summary.set_auto(cd, not _summary.auto_enabled(cd))
        elif row_id == "retention":
            if retention.is_enabled(cd):
                retention.disable(cd)
            else:
                retention.enable(cd)
        elif row_id == "retention_days":
            def after(val: str) -> None:
                try:
                    n = int(val.strip())
                except (ValueError, AttributeError):
                    return
                if n > 0:
                    _ui.set_retention_days(self._ui_path(), n)
            self.push_screen(RenameScreen(str(_ui.get_retention_days(self._ui_path())),
                                          title="Auto-delete after how many days?"), after)
        elif row_id == "usage":
            self.action_toggle_usage()
        elif row_id == "queues":
            self.action_toggle_queues()
        elif row_id == "tmux":
            from . import tmux as _tmux
            if not (_tmux.available() or self._tmux_enabled):
                if os.path.exists(self._tmux_decline_marker()):
                    os.unlink(self._tmux_decline_marker())
                self._maybe_offer_tmux()

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen())
```

Add the screen class (near `QuitScreen`), modelled on it but re-rendering an `OptionList` after each toggle:

```python
class SettingsScreen(_PanelScreen):
    """Persisted-preferences screen. ↑/↓ move · Enter/Space toggle · Esc close.
    Rows and their activation live on the app (_settings_rows/_settings_activate)
    so they're unit-testable; this screen is a thin re-rendering shell."""

    BINDINGS = [
        Binding("escape", "dismiss()", "Close"),
        Binding("enter", "toggle", "Toggle", show=False),
        Binding("space", "toggle", "Toggle", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Settings", classes="dialog-title"),
            OptionList(id="settings-list"),
            Label("↑↓ move · enter/space toggle · esc close", classes="dialog-hint"),
            id="panel",
        )

    def on_mount(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        ol = self.query_one("#settings-list", OptionList)
        highlighted = ol.highlighted
        ol.clear_options()
        for rid, label in self.app._settings_rows():
            ol.add_option(Option(label, id=rid))
        if highlighted is not None and highlighted < ol.option_count:
            ol.highlighted = highlighted

    def action_toggle(self) -> None:
        ol = self.query_one("#settings-list", OptionList)
        if ol.highlighted is None:
            return
        rid = ol.get_option_at_index(ol.highlighted).id
        self.app._settings_activate(rid)
        self._rebuild()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.app._settings_activate(event.option.id)
        self._rebuild()
```

(`OptionList`, `Option`, `Label`, `Binding`, `Vertical` are already imported for the other screens — verify at the top of `tui.py`.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_tui.py -q -k "settings or comma"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat: Settings screen (auto-summary, retention+period, usage, queues, tmux)"
```

---

### Task 12: First-run summaries prompt + reveal the pane

**Files:**
- Modify: `bin/_pkg/tui.py` (`on_mount` / `_maybe_open_help` chain); `test/conftest.py` (fixture marker)
- Test: `test/test_tui.py`

**Interfaces:**
- Consumes: `summary.prompted`/`mark_prompted`/`set_auto`; `_index.load`.
- Produces: `SessionExplorerApp._maybe_prompt_summaries() -> None`, called at the end of the first-run onboarding chain.

- [ ] **Step 1: Update the shared fixture so existing TUI tests don't get the prompt**

In `test/conftest.py`, `index_path` fixture, add alongside the other markers:

```python
    (tmp_path / ".session-explorer.summaries-prompted").write_text("")
```

- [ ] **Step 2: Write the failing tests**

```python
# add to test/test_tui.py
async def test_first_run_summaries_prompt_shows_and_enables(tmp_path):
    import json
    from _pkg import summary as _summary
    # Build an index WITHOUT the summaries-prompted marker, with a named session.
    p = str(tmp_path / "se-index.json")
    json.dump({"version": 1, "sessions": {"sid-1": {
        "project_label": "demo", "project_path": "/tmp/demo",
        "name_cached": "planning/x", "message_count": 10,
    }}}, open(p, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    from _pkg.tui import SessionExplorerApp, ConfirmScreen
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)  # the summaries prompt
        await pilot.press("y")
        await pilot.pause()
    assert _summary.auto_enabled(str(tmp_path)) is True
    assert _summary.prompted(str(tmp_path)) is True


async def test_first_run_summaries_prompt_skipped_when_no_named_session(tmp_path):
    import json
    from _pkg import summary as _summary
    p = str(tmp_path / "se-index.json")
    json.dump({"version": 1, "sessions": {}}, open(p, "w"))
    (tmp_path / ".session-explorer.help-seen").write_text("")
    (tmp_path / ".session-explorer.retention-declined").write_text("")
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=p)
    async with app.run_test() as pilot:
        await pilot.pause()
    assert _summary.prompted(str(tmp_path)) is False  # not shown → not marked
```

- [ ] **Step 3: Run to verify they fail**

Run: `python3 -m pytest test/test_tui.py -q -k first_run_summaries`
Expected: FAIL.

- [ ] **Step 4: Implement**

In `_maybe_open_help` (which already runs after retention and calls `_maybe_offer_tmux`), append a call at the end:

```python
    def _maybe_open_help(self) -> None:
        self._maybe_offer_tmux()
        if not os.path.exists(self._help_marker_path()):
            self._mark_help_seen()
            self.action_help()
        self._maybe_prompt_summaries()
```

Add the method:

```python
    def _maybe_prompt_summaries(self) -> None:
        """One-time discoverability nudge: introduce summaries + offer auto-on-exit,
        then reveal the preview pane once. Only when there's ≥1 named session so a
        brand-new empty install isn't nagged."""
        from . import summary as _summary
        cd = self._claude_dir()
        if _summary.prompted(cd):
            return
        try:
            sessions = _index.load(self._index_path).get("sessions", {})
        except Exception:
            return
        if not any(s.get("name_cached") for s in sessions.values()):
            return

        def after(ok: bool) -> None:
            _summary.set_auto(cd, bool(ok))
            _summary.mark_prompted(cd)
            self._preview.display = True   # reveal so the user sees where summaries live
            self._refresh_preview()

        self.push_screen(ConfirmScreen(
            "session-explorer can summarise what each session was about — shown in the "
            "details pane (Space).",
            detail="Auto-summarise sessions when you leave them? "
                   "(You can also press u to summarise the selected one anytime.)"),
            after)
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m pytest test/test_tui.py -q`
Expected: PASS (all TUI tests; the fixture marker keeps the older tests prompt-free).

- [ ] **Step 6: Commit**

```bash
git add bin/_pkg/tui.py test/conftest.py test/test_tui.py
git commit -m "feat: one-time first-run summaries prompt + reveal preview pane"
```

---

## Phase 5 — Teardown, docs, release

### Task 13: Uninstall teardown

**Files:**
- Modify: `bin/_pkg/uninstall.py` (`_OPERATIONAL_SIDECARS`, `_DATA_FILES`)
- Test: `test/test_uninstall.py`

**Interfaces:** none new — extends the existing sidecar/data lists.

- [ ] **Step 1: Write the failing test**

```python
# add to test/test_uninstall.py
def test_teardown_removes_summary_artifacts(tmp_path):
    from _pkg import uninstall
    cd = tmp_path
    (cd / ".session-explorer.summaries-auto").write_text("")
    (cd / ".session-explorer.summaries-prompted").write_text("")
    (cd / "session-explorer-summaries.json").write_text("{}")
    uninstall.teardown(claude_dir=str(cd), purge_data=True)
    assert not (cd / ".session-explorer.summaries-auto").exists()
    assert not (cd / ".session-explorer.summaries-prompted").exists()
    assert not (cd / "session-explorer-summaries.json").exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest test/test_uninstall.py -q -k summary_artifacts`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add the two markers to `_OPERATIONAL_SIDECARS`:

```python
    ".session-explorer.summaries-auto",
    ".session-explorer.summaries-prompted",
```

Add the sidecar (+ its lock) to `_DATA_FILES`:

```python
    "session-explorer-summaries.json",
    "session-explorer-summaries.json.lock",
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_uninstall.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/uninstall.py test/test_uninstall.py
git commit -m "feat: uninstall tears down summary sidecar + markers"
```

---

### Task 14: Docs + release (1.18.0)

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md`, `README.md`, help text (`bin/_pkg/tui.py` `_help_text`), `CHANGELOG.md`, `bin/_pkg/__init__.py`, `.claude-plugin/plugin.json`

**This task follows the `cutting-a-release` skill — invoke it and use it as the authoritative checklist.**

- [ ] **Step 1: Full suite green first**

Run: `python3 -m pytest test/ -q` (clean env — no live tmux server) and `bats test/hook.bats test/uninstall.bats test/install.bats`
Expected: all PASS.

- [ ] **Step 2: `_help_text` — add the new keys + concepts**

Add to the keybindings list and a short "Summaries" paragraph:

```python
        key("u", "Summarise (or refresh) the selected session"),
        key(",", "Settings (auto-summaries, retention, usage, queues, tmux)"),
```
Plus one line under a Summaries heading: *"Named sessions can carry an AI summary of what they were about — auto on exit (opt-in) or on demand with u; shown in the details pane (Space)."*

- [ ] **Step 3: `SPEC.md`** — add a "Session summaries" section and a "Settings screen" section (mirror the design doc §2–§6), add the two markers + the summaries sidecar to the install-layout file list, and amend the worktree invariant: *"the branch is kept on `remove()`/gc reclaim but safe-deleted (`git branch -d`, merged-only) on permanent session delete via `worktree.purge`."*

- [ ] **Step 4: `CLAUDE.md`** — add load-bearing notes: summaries sidecar + `SESSION_EXPLORER_SUMMARIZER` hook guard; auto-summary is a *toggle*, not a permanent decision; `worktree.purge` vs `worktree.remove`; the Settings screen is the home for persisted prefs; `_summarize_worker` must stay guarded (same rule as `_live_meta_tick`).

- [ ] **Step 5: `README.md`** — document `u`, `,`, the bottom preview pane, the Settings screen, and the summaries feature (opt-in, uses `claude -p`).

- [ ] **Step 6: `CHANGELOG.md`** — new `## 1.18.0` section summarising: session summaries (auto/on-demand), Settings screen, retention re-toggle + configurable period, delete-cascades-to-worktree.

- [ ] **Step 7: Version bump** — `bin/_pkg/__init__.py` `__version__ = "1.18.0"` and `.claude-plugin/plugin.json` `"version": "1.18.0"`.

- [ ] **Step 8: Commit + verify**

```bash
git add -A
git commit -m "docs: session summaries + settings screen; bump to 1.18.0"
python3 -m pytest test/ -q
```
Expected: PASS. (The GitHub release/tag is cut per the `cutting-a-release` skill after the PR merges.)

---

## Self-Review

**Spec coverage** (each spec section → task):
- §1 summariser (`claude -p`, Haiku, hook guard) → Tasks 2, 5.
- §2 store, digest, staleness, markers → Task 1.
- §3 auto-on-exit, `u`, first-run discoverability → Tasks 8, 9, 12.
- §4 bottom preview, Summary section, filter merge → Task 10.
- §5 Settings screen (auto-summary, retention+period, usage, queues, tmux) → Tasks 4, 11.
- §6 delete cascade (`worktree.purge`, `delete_session`, `collect_garbage`) → Tasks 3, 6, 7.
- Data-model files / uninstall teardown → Tasks 1, 4, 13.
- Docs + release → Task 14.

**Placeholder scan:** no TBD/TODO; every code step has real code; commands have expected output.

**Type consistency:** `worktree.purge` returns the same 4 strings in Task 3, 6, 7. `delete_session` returns `str | None` (Task 6) consumed by the delete-report step. `_summarize_worker(sid, transcript_path, msg_count)` signature identical in Tasks 8 and 9. `_settings_rows`/`_settings_activate` names match between Task 11's app methods and the `SettingsScreen`. `collect_garbage` adds `removed_worktrees` consistently (Task 7).

**Note carried to execution:** in Task 10, verify no existing test asserts the old right-side `Horizontal(preview)` layout, and confirm `Horizontal` is still used elsewhere before touching its import. In Task 11, confirm `OptionList`/`Option` are imported at the top of `tui.py` (they are, via `MoveScreen`).
