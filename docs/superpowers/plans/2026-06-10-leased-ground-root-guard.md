# Leased Ground — Location-Enforced Shared Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the advisory command-identity guard with a fail-closed location guard (`root_guard.py`) that makes the shared installed root unreachable through Claude tool calls outside `session-explorer queue-*`, and delete the complexity that existed only to prop up advisory enforcement.

**Architecture:** A new pure module `bin/_pkg/root_guard.py` makes the deny decision from the PreToolUse payload (tool name, file path or command text, call cwd, session id) against the project's `root-dir` resource and the live-session registry. The existing hook script and `queue-guard` CLI subcommand stay as plumbing; the matcher widens to `Bash|Edit|Write|NotebookEdit`. `guard_match.py`, `queue_detect.py`, the `{exe,sub}` guard vocabulary, and the generic resource editor/template library are removed; per-project setup collapses to one "Shared installed root" dialog. The queue engine (flock FIFO, overlay in/out, exclusive-or) is untouched.

**Tech Stack:** Python 3.11+ stdlib only (`shlex`, `os`, `fcntl` via existing modules), vendored Textual for the TUI, pytest + pytest-asyncio, bats.

**Spec:** `docs/superpowers/specs/2026-06-10-leased-ground-root-guard-design.md` — read it first.
**Branch:** `leased-ground-root-guard` (already created; the spec commit is on it).

**Conventions used throughout:**
- Run Python tests as `python3 -m pytest test/<file> -q` from the repo root (`/Volumes/Projects/ClaudeSessionExplorer`). `conftest.py` puts `bin/` on the path; import the package as `from _pkg import …`.
- Inside `bin/_pkg/` modules, import siblings as `from . import x as _x` (existing style).
- Every module under `bin/_pkg/` that the hook path touches must NOT import Textual.
- Commit after every green test run, on the `leased-ground-root-guard` branch.

---

### Task 1: `root_guard.py` — aliases, location classification, Edit/Write/NotebookEdit deny

The decision module's skeleton: resolve the project's `root-dir` resource, classify where the session lives (live registry first, payload cwd as fallback), and deny structured file-tool writes into the root.

**Files:**
- Create: `bin/_pkg/root_guard.py`
- Create: `test/test_root_guard.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_root_guard.py`:

```python
"""Unit tests for the leased-ground location guard (root_guard.decide).

decide(payload, config_path, live_path) -> deny-reason str | None (allow).
Fixtures build a REAL git repo with a managed worktree at
<repo>/.claude/worktrees/wt1 (project_id needs git; the worktrees carve-out
needs the real layout).
"""
import os
import subprocess

from _pkg import live, project_id, queue_config, root_guard

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _run(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, env=_GIT_ENV,
                   capture_output=True)


def repo_with_worktree(tmp_path):
    """A committed repo plus a managed worktree under .claude/worktrees/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], repo)
    (repo / "f.txt").write_text("x")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-qm", "init"], repo)
    wt = repo / ".claude" / "worktrees" / "wt1"
    _run(["git", "worktree", "add", "-q", str(wt), "-b", "wt1"], repo)
    return repo, wt


def shared_root_config(tmp_path, repo):
    """Overlay-shaped root-dir resource named 'root' (no guard field at all)."""
    cfg = str(tmp_path / "qc.json")
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        cfg, project_id=pid, display_path=str(repo), resource_id="root",
        resource={"kind": "root-dir", "path": str(repo),
                  "run_in": "root", "acquire": "command", "release": "command",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out"})
    return cfg


def register(tmp_path, sid, cwd):
    """Record a live session whose registered cwd is `cwd`."""
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id=sid,
                      cwd=str(cwd), pid=os.getpid())
    return lp


def edit_payload(file_path, cwd, sid="S1", tool="Edit"):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {key: str(file_path)},
            "cwd": str(cwd), "session_id": sid}


# --- resolution / classification ---

def test_allows_when_project_has_no_root_resource(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = str(tmp_path / "qc.json")          # empty config
    lp = register(tmp_path, "S1", wt)
    p = edit_payload(repo / "f.txt", wt)
    assert root_guard.decide(p, cfg, lp) is None


def test_allows_when_cwd_is_not_a_repo(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", tmp_path)  # registered outside any repo
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    p = edit_payload(outside / "x.txt", outside)
    assert root_guard.decide(p, cfg, lp) is None


def test_root_session_edits_root_freely(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", repo)      # session registered IN root
    p = edit_payload(repo / "f.txt", repo)
    assert root_guard.decide(p, cfg, lp) is None


def test_root_session_in_subdir_still_counts_as_root(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    sub = repo / "app"
    sub.mkdir()
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", sub)
    p = edit_payload(repo / "f.txt", sub)
    assert root_guard.decide(p, cfg, lp) is None


# --- Edit/Write/NotebookEdit denies ---

def test_worktree_session_edit_into_root_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    p = edit_payload(repo / "f.txt", wt)
    reason = root_guard.decide(p, cfg, lp)
    assert reason is not None
    assert "queue-run --resource root" in reason
    assert "worktree" in reason.lower()


def test_worktree_session_edit_in_own_worktree_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    p = edit_payload(wt / "f.txt", wt)       # under <root>/.claude/worktrees/
    assert root_guard.decide(p, cfg, lp) is None


def test_write_and_notebookedit_also_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    for tool in ("Write", "NotebookEdit"):
        p = edit_payload(repo / "new.txt", wt, tool=tool)
        assert root_guard.decide(p, cfg, lp) is not None, tool


def test_relative_file_path_resolves_against_call_cwd(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    # call cwd is the ROOT (drifted); a relative path lands inside root.
    p = {"tool_name": "Edit", "tool_input": {"file_path": "f.txt"},
         "cwd": str(repo), "session_id": "S1"}
    assert root_guard.decide(p, cfg, lp) is not None


def test_unguarded_tool_names_are_ignored(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    p = {"tool_name": "Read", "tool_input": {"file_path": str(repo / "f.txt")},
         "cwd": str(wt), "session_id": "S1"}
    assert root_guard.decide(p, cfg, lp) is None


def test_unregistered_session_falls_back_to_payload_cwd(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = str(tmp_path / "live.json")         # empty registry, never written
    # cwd says worktree -> deny applies even with no registry entry.
    p = edit_payload(repo / "f.txt", wt, sid="UNKNOWN")
    assert root_guard.decide(p, cfg, lp) is not None


def test_symlinked_root_path_still_matches(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    link = tmp_path / "link"
    os.symlink(repo, link)
    # Config stores the SYMLINK path; the edit uses the real path.
    cfg = str(tmp_path / "qc.json")
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        cfg, project_id=pid, display_path=str(link), resource_id="root",
        resource={"kind": "root-dir", "path": str(link),
                  "run_in": "root", "acquire": "command", "release": "command",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out"})
    lp = register(tmp_path, "S1", wt)
    p = edit_payload(repo / "f.txt", wt)
    assert root_guard.decide(p, cfg, lp) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_root_guard.py -q`
Expected: FAIL — `ImportError: cannot import name 'root_guard'`.

- [ ] **Step 3: Write the implementation**

Create `bin/_pkg/root_guard.py`:

```python
"""Location-based root guard for the PreToolUse hook (leased-ground spec,
docs/superpowers/specs/2026-06-10-leased-ground-root-guard-design.md).

One invariant, replacing command-identity guarding: for a Claude session in a
worktree, the shared installed root is unreachable through tools except via
`session-explorer queue-*`. `decide()` returns a deny reason or None (allow).

Posture: the PLUMBING fails open (cli.py wraps decide() in try/except — a
broken hook must not brick every Bash call), but the SEMANTICS fail closed:
within working plumbing, a root mention is denied by default. This is the
inverse of the old guard_match contract ("deny only what parses confidently").

No Textual import; no argparse; no stdout.
"""

from __future__ import annotations

import os
import shlex

from . import live as _live
from . import project_id as _pid
from . import queue_config as _qc

GUARDED_TOOLS = ("Bash", "Edit", "Write", "NotebookEdit")


def _aliases(root: str) -> list:
    """Path spellings an agent might use for the root: the configured path,
    its realpath, and (macOS) the /private-stripped twin. Order-preserving."""
    out = []

    def add(p: str) -> None:
        p = p.rstrip("/")
        if p and p not in out:
            out.append(p)

    add(root)
    rp = os.path.realpath(root)
    add(rp)
    if rp.startswith("/private/"):
        add(rp[len("/private"):])
    return out


def _inside(path: str, root: str) -> bool:
    p = os.path.realpath(path)
    r = os.path.realpath(root)
    return p == r or p.startswith(r + os.sep)


def _wt_dir(root: str) -> str:
    return os.path.join(root, ".claude", "worktrees")


def _is_root_location(location: str, root: str) -> bool:
    """True iff `location` is inside the root proper (root or a subdir), and
    NOT inside the managed-worktrees subtree. Everything else — managed
    worktree, external `git worktree add`, unrelated dir — is non-root and the
    deny rules apply to it."""
    return _inside(location, root) and not _inside(location, _wt_dir(root))


def _root_resource(config_path: str, cwd: str):
    """(resource_id, resource) of the project's root-dir resource, or None."""
    pid = _pid.project_id(cwd)
    if not pid:
        return None
    resources = _qc.list_resources(config_path, pid)
    for rid in sorted(resources):
        res = resources[rid]
        if res.get("kind") == "root-dir" and res.get("path"):
            return rid, res
    return None


def _session_location(payload: dict, live_path: str) -> "str | None":
    """The session's home directory: the live registry's recorded cwd when the
    session is registered (authoritative — a tool call's cwd can drift), else
    the payload cwd (weaker fallback, accepted by the spec)."""
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid:
        entry = _live.load(live_path).get("sessions", {}).get(sid) or {}
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def decide(payload: dict, config_path: str, live_path: str) -> "str | None":
    """Deny reason for a PreToolUse payload, or None to allow."""
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name")
    if tool not in GUARDED_TOOLS:
        return None
    call_cwd = payload.get("cwd")
    if not isinstance(call_cwd, str) or not call_cwd:
        return None  # no trustworthy cwd -> fail open (mirrors old guard)
    location = _session_location(payload, live_path) or call_cwd
    # Resolve the project from the call cwd; if the agent has wandered outside
    # the repo (cd /tmp), fall back to the session's registered home so a
    # registered worktree session can't escape the guard by leaving the repo.
    found = _root_resource(config_path, call_cwd)
    if not found and location != call_cwd:
        found = _root_resource(config_path, location)
    if not found:
        return None
    rid, res = found
    root = res["path"]
    if _is_root_location(location, root):
        return None  # a root session owns its tree (exclusive-or covers leases)
    if tool in ("Edit", "Write", "NotebookEdit"):
        return _decide_file_tool(payload, rid, root, call_cwd)
    return _decide_bash(payload, rid, root, call_cwd, location)


def _decide_file_tool(payload: dict, rid: str, root: str,
                      call_cwd: str) -> "str | None":
    tool_input = payload.get("tool_input") or {}
    fp = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(fp, str) or not fp:
        return None
    if not os.path.isabs(fp):
        fp = os.path.join(call_cwd, fp)
    if _inside(fp, root) and not _inside(fp, _wt_dir(root)):
        return (
            f"{root} is this project's shared installed root — leased ground, "
            f"not writable from a worktree session. Edit the file in YOUR "
            f"worktree instead; your changes reach the root via the overlay "
            f"when you run: session-explorer queue-run --resource {rid} -- "
            f"<cmd>. Reads are not blocked (use the Read tool to inspect "
            f"root files).")
    return None


def _decide_bash(payload: dict, rid: str, root: str, call_cwd: str,
                 location: str) -> "str | None":
    # Placeholder until Task 2; Task 1 only needs file-tool denies.
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_root_guard.py -q`
Expected: PASS (all tests in this file).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/root_guard.py test/test_root_guard.py
git commit -m "feat(root-guard): location classification + file-tool deny"
```

---

### Task 2: `root_guard.py` — Bash rules (cd-drift, mention-deny, climb, rewrite)

**Files:**
- Modify: `bin/_pkg/root_guard.py` (replace the `_decide_bash` placeholder)
- Modify: `test/test_root_guard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_root_guard.py`:

```python
def bash_payload(cmd, cwd, sid="S1"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd},
            "cwd": str(cwd), "session_id": sid}


# --- Bash: mention = deny ---

def test_bash_mentioning_root_denied_with_rewrite(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"cp build.xml {repo}/build.xml"
    reason = root_guard.decide(bash_payload(cmd, wt), cfg, lp)
    assert reason is not None
    assert f"session-explorer queue-run --resource root -- {cmd}" in reason
    assert "Read tool" in reason


def test_bash_innocent_command_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(
        bash_payload("phpunit --testsuite unit", wt), cfg, lp) is None


def test_bash_compound_mention_suggests_bash_c_wrap(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"cp a {repo}/a && cp b {repo}/b"
    reason = root_guard.decide(bash_payload(cmd, wt), cfg, lp)
    assert reason is not None
    # Shell-operator commands must be wrapped whole so every part runs leased.
    assert "queue-run --resource root -- bash -c " in reason


def test_bash_realpath_spelling_of_symlinked_config_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    link = tmp_path / "link"
    os.symlink(repo, link)
    cfg = str(tmp_path / "qc.json")
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        cfg, project_id=pid, display_path=str(link), resource_id="root",
        resource={"kind": "root-dir", "path": str(link),
                  "run_in": "root", "acquire": "command", "release": "command",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out"})
    lp = register(tmp_path, "S1", wt)
    cmd = f"touch {os.path.realpath(str(repo))}/x"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None


# --- Bash: parent-climb from a managed worktree ---

def test_bash_climb_from_managed_worktree_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    reason = root_guard.decide(
        bash_payload("cp x ../../../somefile", wt), cfg, lp)
    assert reason is not None


def test_bash_single_parent_step_allowed(tmp_path):
    # One `..` from a managed worktree only reaches .claude/worktrees — shared
    # but harmless; the rule keys on `../..` (two-plus steps).
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(
        bash_payload("ls ../other-worktree", wt), cfg, lp) is None


def test_bash_climb_rule_not_applied_to_external_worktree(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    ext = tmp_path / "ext-wt"
    _run(["git", "worktree", "add", "-q", str(ext), "-b", "ext"], repo)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", ext)
    # Climbing from an external worktree does not lexically reach the root.
    assert root_guard.decide(
        bash_payload("cat ../../notes.txt", ext), cfg, lp) is None


# --- Bash: cd-drift ---

def test_cd_drift_into_root_denied_even_for_innocent_command(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)     # session HOME is the worktree
    reason = root_guard.decide(
        bash_payload("npm install", repo), cfg, lp)  # call cwd = root
    assert reason is not None
    assert "drifted" in reason.lower() or "working directory" in reason.lower()


def test_root_session_running_in_root_is_not_drift(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", repo)   # session HOME is the root
    assert root_guard.decide(
        bash_payload("npm install", repo), cfg, lp) is None


def test_registered_worktree_session_escaping_repo_still_guarded(tmp_path):
    # Session registered in the worktree, but the call cwd wandered to an
    # unrelated dir: project resolution falls back to the registered home.
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    outside = tmp_path / "outside"
    outside.mkdir()
    cmd = f"cp x {repo}/x"
    assert root_guard.decide(bash_payload(cmd, outside), cfg, lp) is not None
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `python3 -m pytest test/test_root_guard.py -q`
Expected: the Task-1 tests still PASS; every new Bash test FAILs (decide returns None from the placeholder).

- [ ] **Step 3: Implement `_decide_bash`**

In `bin/_pkg/root_guard.py`, replace the placeholder `_decide_bash` with:

```python
# Shell operators that would let the agent's outer shell re-split a suggested
# rewrite, running part of it outside the lease. Same table the old awareness
# module used; presence of any of these wraps the rewrite in `bash -c <quoted>`.
_SHELL_OPS = ("&&", "||", ";", "|", "&", ">", "<", "$(", "`", "\n")


def _rewrite(rid: str, command: str) -> str:
    """The exact queue-run invocation to suggest for `command`."""
    if any(op in command for op in _SHELL_OPS):
        return (f"session-explorer queue-run --resource {rid} -- "
                f"bash -c {shlex.quote(command)}")
    return f"session-explorer queue-run --resource {rid} -- {command}"


def _deny_bash_text(rid: str, root: str, command: str) -> str:
    return (
        f"{root} is this project's shared installed root — leased ground, "
        f"unreachable outside a lease. Re-run the ENTIRE command through the "
        f"queue:\n\n    {_rewrite(rid, command)}\n\n"
        f"To merely inspect root files, use the Read tool (reads are not "
        f"blocked). `session-explorer queue-status` shows who holds the lease.")


def _decide_bash(payload: dict, rid: str, root: str, call_cwd: str,
                 location: str) -> "str | None":
    # cd-drift first: a worktree session whose call cwd has wandered into the
    # root is wrong even for innocent commands (relative paths land in root),
    # and even for queue-run itself (the overlay source must be the worktree).
    if _is_root_location(call_cwd, root):
        return (
            f"Your working directory has drifted into {root}, the shared "
            f"installed root — leased ground for worktree sessions. cd back "
            f"to your own worktree; anything that must run in the root goes "
            f"through: session-explorer queue-run --resource {rid} -- <cmd>.")
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command:
        return None
    # Mention = deny. No "confident parse" requirement: a worktree session has
    # no legitimate raw root-touching Bash, ever (a lease only exists inside a
    # queue-run process), so false positives are cheap and recoverable.
    if any(alias in command for alias in _aliases(root)):
        return _deny_bash_text(rid, root, command)
    # Parent-climb: a managed worktree sits at <root>/.claude/worktrees/<n>,
    # so `../..` already reaches shared ground. External worktrees are
    # covered by the alias rule only.
    if _inside(location, _wt_dir(root)) and "../.." in command:
        return _deny_bash_text(rid, root, command)
    return None
```

(Note: Task 3 will insert the queue-* allowlist between the drift check and the mention check.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_root_guard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/root_guard.py test/test_root_guard.py
git commit -m "feat(root-guard): Bash mention/climb/cd-drift denies with queue-run rewrite"
```

---

### Task 3: `root_guard.py` — the single `queue-*` allowlist

A command that *mentions* the root is still allowed iff it is **one simple command** invoking `session-explorer queue-…`. Token-level analysis uses `shlex.shlex(punctuation_chars=True)` so `a;b` and `a&&b` split even without spaces, while a quoted `bash -c '…'` body stays one token (the suggested rewrite form must pass!).

**Files:**
- Modify: `bin/_pkg/root_guard.py`
- Modify: `test/test_root_guard.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_root_guard.py`:

```python
# --- the queue-* allowlist ---

def test_queue_run_mentioning_root_is_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"session-explorer queue-run --resource root -- cp x {repo}/app/x"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is None


def test_queue_run_with_quoted_bash_c_body_is_allowed(tmp_path):
    # The exact shape our own deny message suggests: operators live INSIDE the
    # quoted body, which shlex keeps as one token.
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = ("session-explorer queue-run --resource root -- "
           f"bash -c 'cp a {repo}/a && cp b {repo}/b'")
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is None


def test_queue_status_is_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(
        bash_payload("session-explorer queue-status", wt), cfg, lp) is None


def test_env_prefix_on_queue_run_is_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"FOO=1 session-explorer queue-run --resource root -- ls {repo}"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is None


def test_compound_smuggle_after_queue_run_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = (f"session-explorer queue-status && cp x {repo}/x")
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None


def test_semicolon_without_spaces_smuggle_denied(tmp_path):
    # shlex.split would keep 'queue-status;cp' as one token; the
    # punctuation_chars lexer must split it and catch the compound.
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"session-explorer queue-status;cp x {repo}/x"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None


def test_command_substitution_in_queue_invocation_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"session-explorer queue-run --resource $(echo root) -- ls {repo}"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None


def test_echo_queue_run_decoy_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"echo session-explorer queue-run && rm {repo}/f.txt"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None
```

- [ ] **Step 2: Run the tests to verify the allow-cases fail**

Run: `python3 -m pytest test/test_root_guard.py -q`
Expected: the four `*_is_allowed` tests FAIL (mention currently denies them); the deny-case tests already pass.

- [ ] **Step 3: Implement the allowlist**

In `bin/_pkg/root_guard.py`, add above `_decide_bash`:

```python
_PUNCT = set("&|;<>()")


def _is_queue_invocation(command: str) -> bool:
    """True iff `command` is ONE simple `session-explorer queue-*` invocation.

    Token-level: shlex with punctuation_chars splits `a;b` / `a&&b` even
    without whitespace, while quoted bodies (`bash -c '…'`) stay one token —
    so our own suggested rewrite passes and a smuggled compound does not.
    Anything unlexable or operator-bearing fails toward NOT-allowlisted (the
    cost is a false deny of an odd queue invocation, which the deny message
    makes one step to fix)."""
    if "\n" in command or "`" in command or "$(" in command:
        return False
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return False
    if any(t and set(t) <= _PUNCT for t in toks):
        return False  # any operator/redirect token -> compound command
    while toks:  # tolerate env-assignment prefixes: FOO=1 session-explorer …
        head = toks[0]
        if "=" in head and not head.startswith("-") \
                and "/" not in head.split("=", 1)[0]:
            toks = toks[1:]
            continue
        break
    return (len(toks) >= 2
            and os.path.basename(toks[0]) == "session-explorer"
            and toks[1].startswith("queue-"))
```

Then in `_decide_bash`, insert between the `command` extraction and the mention check:

```python
    if _is_queue_invocation(command):
        return None  # the one door: a single simple session-explorer queue-* call
```

- [ ] **Step 4: Run the full module suite**

Run: `python3 -m pytest test/test_root_guard.py -q`
Expected: PASS (all Task 1–3 tests).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/root_guard.py test/test_root_guard.py
git commit -m "feat(root-guard): single-simple-command queue-* allowlist"
```

---

### Task 4: Switch the `queue-guard` CLI subcommand to `root_guard`

The hook script (`hooks/pre-tool-use.sh`) is untouched — it already pipes any payload to `session-explorer queue-guard`. Only the subcommand's internals change.

**Files:**
- Modify: `bin/_pkg/cli.py:383-411` (`_cmd_queue_guard`)
- Modify: `test/test_cli.py:355-418` (the queue-guard tests)

- [ ] **Step 1: Rewrite the queue-guard tests**

In `test/test_cli.py`, replace `_run_guard` and the five `test_queue_guard_*` functions (lines 355–418) with:

```python
def _wt_repo(tmp_path):
    """Committed repo + managed worktree (root_guard needs the real layout)."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True, env=env)
    wt = repo / ".claude" / "worktrees" / "wt1"
    subprocess.run(["git", "worktree", "add", "-q", str(wt), "-b", "wt1"],
                   cwd=repo, check=True, env=env)
    cfg = tmp_path / "qc.json"
    from _pkg import project_id, queue_config
    queue_config.add_resource(
        str(cfg), project_id=project_id.project_id(str(repo)),
        display_path=str(repo), resource_id="root",
        resource={"kind": "root-dir", "path": str(repo),
                  "run_in": "root", "acquire": "command", "release": "command",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out"})
    from _pkg import live
    lp = tmp_path / "live.json"
    live.record_event(str(lp), event="SessionStart", session_id="S1",
                      cwd=str(wt), pid=os.getpid())
    return repo, wt, cfg, lp


def _run_guard_payload(payload_obj, cfg, lp):
    import json as _json
    return subprocess.run(
        [_BIN, "queue-guard"], input=_json.dumps(payload_obj),
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg),
             "SESSION_EXPLORER_LIVE": str(lp)})


def test_queue_guard_denies_bash_mentioning_root(tmp_path):
    repo, wt, cfg, lp = _wt_repo(tmp_path)
    result = _run_guard_payload(
        {"tool_name": "Bash",
         "tool_input": {"command": f"cp x {repo}/x"},
         "cwd": str(wt), "session_id": "S1"}, cfg, lp)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "queue-run --resource root --" in out["permissionDecisionReason"]


def test_queue_guard_allows_innocent_bash(tmp_path):
    repo, wt, cfg, lp = _wt_repo(tmp_path)
    result = _run_guard_payload(
        {"tool_name": "Bash", "tool_input": {"command": "phpunit -c app"},
         "cwd": str(wt), "session_id": "S1"}, cfg, lp)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_queue_guard_denies_edit_into_root(tmp_path):
    repo, wt, cfg, lp = _wt_repo(tmp_path)
    result = _run_guard_payload(
        {"tool_name": "Edit",
         "tool_input": {"file_path": str(repo / "f.txt")},
         "cwd": str(wt), "session_id": "S1"}, cfg, lp)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"


def test_queue_guard_ignores_read_tool(tmp_path):
    repo, wt, cfg, lp = _wt_repo(tmp_path)
    result = _run_guard_payload(
        {"tool_name": "Read",
         "tool_input": {"file_path": str(repo / "f.txt")},
         "cwd": str(wt), "session_id": "S1"}, cfg, lp)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_queue_guard_fails_open_on_garbage_stdin(tmp_path):
    repo, wt, cfg, lp = _wt_repo(tmp_path)
    result = subprocess.run(
        [_BIN, "queue-guard"], input="not json at all",
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg),
             "SESSION_EXPLORER_LIVE": str(lp)})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_queue_guard_fails_open_when_cwd_missing(tmp_path):
    # Payload-schema drift: no cwd. Must NOT guess via os.getcwd() -> allow.
    repo, wt, cfg, lp = _wt_repo(tmp_path)
    result = subprocess.run(
        [_BIN, "queue-guard"],
        input=json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": f"cp x {repo}/x"}}),
        capture_output=True, text=True, cwd=str(wt),
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg),
             "SESSION_EXPLORER_LIVE": str(lp)})
    assert result.returncode == 0
    assert result.stdout.strip() == ""
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `python3 -m pytest test/test_cli.py -q -k queue_guard`
Expected: `test_queue_guard_denies_bash_mentioning_root` and `test_queue_guard_denies_edit_into_root` FAIL (old code only handles Bash + guard rules); the allow/fail-open tests may already pass.

- [ ] **Step 3: Rewrite `_cmd_queue_guard`**

In `bin/_pkg/cli.py`, replace the whole `_cmd_queue_guard` function (lines 383–411) with:

```python
def _cmd_queue_guard(args) -> int:
    """Read a PreToolUse payload on stdin; deny tool calls that touch the
    shared installed root from a worktree session (root_guard, leased-ground
    spec). PLUMBING fails open: bad JSON / no config / unexpected error -> no
    output, exit 0, tool proceeds. Within working plumbing a root mention is
    denied by default — the inverse of the old advisory guard."""
    import json as _json
    try:
        raw = sys.stdin.read()
        payload = _json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        from . import root_guard as _rg
        reason = _rg.decide(payload, _queue_config_path(), _live_path())
        if reason:
            print(_json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
    except Exception:
        pass
    return 0
```

Also update the subparser help text in `build_parser()` (lines 119–122) to:

```python
    sub.add_parser(
        "queue-guard",
        help="Read a PreToolUse payload on stdin; deny tool calls that touch "
             "the shared installed root outside a lease (root_guard). "
             "Plumbing fails open.")
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest test/test_cli.py -q`
Expected: PASS (whole file — the queue-context tests are untouched until Task 6).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(cli): queue-guard delegates to root_guard (location deny)"
```

---

### Task 5: Widen the PreToolUse matcher to `Bash|Edit|Write|NotebookEdit`

Three registration surfaces must stay in sync: `plugin.json` (marketplace), `install.sh` (plain), `uninstall.py` (teardown — **no change needed**: it prunes by event name + script basename, not matcher; verify, don't edit).

**Files:**
- Modify: `.claude-plugin/plugin.json` (PreToolUse matcher)
- Modify: `install.sh:100-102`
- Modify: `test/test_plugin_manifest.py:20-21`
- Modify: `test/install.bats` (PreToolUse assertions, lines ~118-171)
- Modify: `test/hook.bats` (`optin_repo` + the Phase-3 tests, lines ~160-224)

- [ ] **Step 1: Update the manifest test**

In `test/test_plugin_manifest.py`, change line 21 from matching `"Bash"` to:

```python
    grp = next(h for h in pt if h.get("matcher") == "Bash|Edit|Write|NotebookEdit")
```

Run: `python3 -m pytest test/test_plugin_manifest.py -q` → expected FAIL (manifest still says `Bash`).

- [ ] **Step 2: Update `plugin.json` and `install.sh`**

In `.claude-plugin/plugin.json`, PreToolUse block, change `"matcher": "Bash"` to `"matcher": "Bash|Edit|Write|NotebookEdit"`.

In `install.sh` (lines 98–102), change the comment + matcher:

```python
# PreToolUse root guard (leased-ground spec). Use the documented nested
# matcher-group form (matching plugin.json) so the guard actually fires on
# plain installs. Matcher covers every write-capable tool the guard decides on.
hooks["PreToolUse"] = _strip_ours("PreToolUse") + [
    {"matcher": "Bash|Edit|Write|NotebookEdit",
     "hooks": [{"type": "command", "command": pretool_cmd}]}]
```

Run: `python3 -m pytest test/test_plugin_manifest.py -q` → expected PASS.

- [ ] **Step 3: Update `install.bats` and `hook.bats`**

`test/install.bats`: in the assertions about **our** hook's matcher (the "install registers a PreToolUse hook" test at ~line 118 and the idempotency test at ~135), replace `h.get('matcher') == 'Bash'` with `'Bash|Edit|Write|NotebookEdit'`, and rename the first test to `"install registers a PreToolUse hook (nested matcher-group, write-capable tools)"`. Do **NOT** change the seeded *user* hook in the preservation test (~line 160) — a user's own `{'matcher': 'Bash', …}` entry legitimately keeps its matcher; only assert our command is added alongside it.

`test/hook.bats`: replace the `optin_repo` helper (line 160) and the two Phase-3 payload tests (lines 206–224) with:

```bash
optin_repo() {
  # Committed repo + managed worktree + overlay-shaped root resource.
  local repo="$1"
  git init -q "$repo"
  git -C "$repo" -c user.name=t -c user.email=t@t commit -q --allow-empty -m i
  git -C "$repo" worktree add -q "$repo/.claude/worktrees/wt1" -b wt1
  python3 - "$REPO" "$repo" "$HOME/.claude/session-explorer-queue-config.json" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/bin")
from _pkg import project_id, queue_config
repo, cfg = sys.argv[2], sys.argv[3]
pid = project_id.project_id(repo)
queue_config.add_resource(
    cfg, project_id=pid, display_path=repo, resource_id="root",
    resource={"kind": "root-dir", "path": repo,
              "run_in": "root", "acquire": "command", "release": "command",
              "command_acquire": "session-explorer queue-overlay in",
              "command_release": "session-explorer queue-overlay out"})
PY
}

@test "pre-tool-use denies a Bash command that mentions the shared root" {
  mkdir -p "$HOME/.claude"
  REPO_DIR="$HOME/proj"
  optin_repo "$REPO_DIR"
  WT="$REPO_DIR/.claude/worktrees/wt1"
  PAYLOAD="{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"cp x $REPO_DIR/x\"},\"cwd\":\"$WT\",\"session_id\":\"S1\"}"
  run bash -c "printf '%s' '$PAYLOAD' | bash '$REPO/hooks/pre-tool-use.sh'"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import json,sys; d=json.load(sys.stdin); h=d['hookSpecificOutput']; assert h['permissionDecision']=='deny'; assert 'queue-run --resource root --' in h['permissionDecisionReason']"
}

@test "pre-tool-use denies an Edit into the shared root" {
  mkdir -p "$HOME/.claude"
  REPO_DIR="$HOME/proj"
  optin_repo "$REPO_DIR"
  WT="$REPO_DIR/.claude/worktrees/wt1"
  PAYLOAD="{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$REPO_DIR/app.php\"},\"cwd\":\"$WT\",\"session_id\":\"S1\"}"
  run bash -c "printf '%s' '$PAYLOAD' | bash '$REPO/hooks/pre-tool-use.sh'"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['hookSpecificOutput']['permissionDecision']=='deny'"
}

@test "pre-tool-use is silent for an innocent worktree command" {
  mkdir -p "$HOME/.claude"
  REPO_DIR="$HOME/proj"
  optin_repo "$REPO_DIR"
  WT="$REPO_DIR/.claude/worktrees/wt1"
  PAYLOAD="{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"phpunit -c app\"},\"cwd\":\"$WT\",\"session_id\":\"S1\"}"
  run bash -c "printf '%s' '$PAYLOAD' | bash '$REPO/hooks/pre-tool-use.sh'"
  [ "$status" -eq 0 ]
  [ -z "$(echo -n "$output" | tr -d '[:space:]')" ]
}
```

(These bats sessions are NOT in the live registry, so the guard classifies by the payload `cwd` — the documented fallback. If `hook.bats` has additional Phase-3 tests after line 224 referencing `docker compose`, update them the same way: cwd becomes the worktree, the command mentions/doesn't-mention `$REPO_DIR`.)

- [ ] **Step 4: Run both bats suites and the manifest test**

Run: `bats test/install.bats test/hook.bats && python3 -m pytest test/test_plugin_manifest.py -q`
Expected: PASS.

- [ ] **Step 5: Verify uninstall teardown still covers PreToolUse**

Run: `python3 -m pytest test/test_uninstall.py -q && bats test/uninstall.bats`
Expected: PASS with **no changes** — `_HOOK_EVENTS` already lists `PreToolUse` and pruning matches the script basename `pre-tool-use.sh`, not the matcher string.

- [ ] **Step 6: Commit**

```bash
git add .claude-plugin/plugin.json install.sh test/test_plugin_manifest.py test/install.bats test/hook.bats
git commit -m "feat(hooks): widen PreToolUse matcher to Bash|Edit|Write|NotebookEdit"
```

---

### Task 6: Shrink the awareness text; remove `guard_reason` from `queue_awareness`

The SessionStart context becomes a short usage hint about a wall, not a cooperation contract. `guard_reason`, `_redirect_command`, `_SHELL_OPS`, and `_guard_label` go (the rewrite logic now lives in `root_guard`). `session_context()` keeps its signature — `session-start.sh` / `queue-context` wiring is untouched.

**Files:**
- Modify: `bin/_pkg/queue_awareness.py` (rewrite — the file shrinks to ~50 lines)
- Modify: `test/test_queue_awareness.py` (rewrite)
- Modify: `test/test_cli.py:328-339` (`test_queue_context_emits_additional_context_when_opted_in` asserts old text)

- [ ] **Step 1: Rewrite the awareness tests**

Replace the body of `test/test_queue_awareness.py` with:

```python
"""Tests for the SessionStart awareness hint (queue_awareness)."""
import subprocess

from _pkg import queue_awareness as qa
from _pkg import project_id, queue_config


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def _add_root_resource(cfg_path, repo):
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        str(cfg_path), project_id=pid, display_path=str(repo),
        resource_id="root",
        resource={"kind": "root-dir", "path": str(repo),
                  "run_in": "root", "acquire": "command", "release": "command",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out"})
    return pid


def test_session_context_none_when_not_opted_in(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    assert qa.session_context(str(cfg), str(repo)) is None


def test_session_context_none_outside_git(tmp_path):
    cfg = tmp_path / "queue-config.json"
    assert qa.session_context(str(cfg), str(tmp_path)) is None


def test_session_context_is_a_short_wall_hint(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    text = qa.session_context(str(cfg), str(repo))
    assert text is not None
    assert str(repo) in text                       # names the root path
    assert "write-blocked" in text.lower()         # states the wall
    assert "queue-run --resource root --" in text  # the one door
    assert "queue-status" in text
    # The old cooperation contract is gone.
    assert "leased ground" not in text.lower()
    assert "guarded commands" not in text.lower()
    assert len(text.splitlines()) <= 6


def test_session_context_lists_non_root_resources_one_liner(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    pid = _add_root_resource(cfg, repo)
    queue_config.add_resource(
        str(cfg), project_id=pid, display_path=str(repo), resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    text = qa.session_context(str(cfg), str(repo))
    assert "db" in text


def test_session_context_for_non_root_only_project(tmp_path):
    # Back-compat: a project with only a port/device resource still gets a
    # hint (serialize via queue-run), just no root-wall paragraph.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        str(cfg), project_id=pid, display_path=str(repo), resource_id="sim",
        resource={"kind": "device", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    text = qa.session_context(str(cfg), str(repo))
    assert text is not None and "sim" in text and "queue-run" in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest test/test_queue_awareness.py -q`
Expected: FAIL (old contract text, old fixtures had `guard`).

- [ ] **Step 3: Rewrite `queue_awareness.py`**

Replace the entire file with:

```python
"""SessionStart awareness hint for shared-resource projects.

One pure entry point: `session_context(config_path, cwd)` -> the SessionStart
`additionalContext` text for an opted-in project, or None. Since the
leased-ground change, this is a USAGE HINT about an enforced wall (the
PreToolUse root guard denies root-touching tool calls), not a cooperation
contract — the old `guard_reason` command matching lives on, location-based,
in `root_guard.py`.

No argparse, no Textual, no stdout. Callers (cli.py) wrap in try/except and
fail open.
"""

from __future__ import annotations

from . import project_id as _pid
from . import queue_config as _qc


def _render_context(resources: dict) -> str:
    root_id = None
    root_res = None
    for rid in sorted(resources):
        if resources[rid].get("kind") == "root-dir":
            root_id, root_res = rid, resources[rid]
            break
    lines = []
    if root_res is not None:
        lines += [
            f"This project's installed root at {root_res.get('path')} is "
            f"shared across worktrees and write-blocked outside a lease "
            f"(tool calls that touch it are denied).",
            f"Run anything that needs the installed root (tests, builds, "
            f"installs) as: `session-explorer queue-run --resource {root_id} "
            f"-- <cmd>` — it overlays your changed files into the root, runs, "
            f"and restores them.",
            "`session-explorer queue-status` shows the current holder and "
            "queue.",
        ]
    others = [rid for rid in sorted(resources) if rid != root_id]
    if others:
        lines.append(
            "Other shared resources for this project (serialize the same "
            "way, via `session-explorer queue-run --resource <id> -- <cmd>`): "
            + ", ".join(others) + ".")
    return "\n".join(lines)


def session_context(config_path: str, cwd: str) -> "str | None":
    pid = _pid.project_id(cwd)
    if not pid:
        return None
    resources = _qc.list_resources(config_path, pid)
    if not resources:
        return None
    return _render_context(resources)
```

- [ ] **Step 4: Fix the queue-context CLI test**

In `test/test_cli.py`, `test_queue_context_emits_additional_context_when_opted_in` (line ~328): the fixture `_git_repo_with_root` (line ~310) keeps working (extra `guard`/`sync` keys are tolerated), but the assertions change. Replace lines 338–339 with:

```python
    assert "write-blocked" in out["additionalContext"].lower()
    assert "queue-run" in out["additionalContext"]
```

- [ ] **Step 5: Run the affected suites**

Run: `python3 -m pytest test/test_queue_awareness.py test/test_cli.py test/test_session_start.py -q`
Expected: PASS. (If `test_session_start.py` asserts old context phrases like "Cooperate with the lease engine", update those assertions to `"write-blocked"` the same way.)

- [ ] **Step 6: Commit**

```bash
git add bin/_pkg/queue_awareness.py test/test_queue_awareness.py test/test_cli.py test/test_session_start.py
git commit -m "feat(awareness): shrink SessionStart context to a wall-hint; drop guard_reason"
```

---

### Task 7: TUI collapse — `SharedRootScreen`, remove templates/editor/detector wiring

The generic two-level setup (ResourceListScreen → ResourceEditorScreen with 8 templates, guard editor, test panel) collapses to one dialog. The mtime detector wiring goes. The experimental constant and help text update to the new claim.

**Files:**
- Modify: `bin/_pkg/tui.py` —
  - replace `QUEUE_TEMPLATES`/`template_resource` (lines 88–153) with `SHARED_ROOT_DEFAULTS`
  - delete `parse_guard_lines`, `format_guard_lines`, `parse_wait_for`, `format_wait_for` (keep `parse_path_lines`)
  - delete `ResourceListScreen` (lines 758–851) and `ResourceEditorScreen` (lines 854–1120); add `SharedRootScreen`
  - update `action_resource_setup` (line 2018)
  - delete `_detect_snaps`/`_detect_warned` init (lines ~1310–1314), the `self._detect_out_of_lease()` call (line 2497), and the `_detect_out_of_lease` method (lines 2499–2543)
  - update `QUEUE_EXPERIMENTAL` (line 83) and `_queue_help_text()` (line 1127)
- Modify: `test/test_queue_templates.py` (rewrite), `test/test_tui_queue.py` (prune + add)

- [ ] **Step 1: Rewrite the pure-data tests**

Replace `test/test_queue_templates.py` with:

```python
from _pkg.tui import (SHARED_ROOT_DEFAULTS, QUEUE_EXPERIMENTAL,
                      parse_path_lines)


def test_shared_root_defaults_are_the_overlay_shape():
    d = SHARED_ROOT_DEFAULTS
    assert d["kind"] == "root-dir"
    assert d["acquire"] == "command"        # NOT sync — no rsync --delete
    assert d["release"] == "command"
    assert d["run_in"] == "root"
    assert d["command_acquire"] == "session-explorer queue-overlay in"
    assert d["command_release"] == "session-explorer queue-overlay out"
    assert d["release_required"] is False
    assert "guard" not in d                 # location guard replaced commands


def test_shared_root_defaults_pass_config_validation(tmp_path):
    # The dialog saves this shape verbatim (+path); it must satisfy
    # queue_config._validate, incl. the overlay in/out pairing rule.
    from _pkg import queue_config
    cfg = str(tmp_path / "qc.json")
    res = dict(SHARED_ROOT_DEFAULTS)
    res["path"] = "/repo"
    queue_config.add_resource(cfg, project_id="p1", display_path="/repo",
                              resource_id="root", resource=res)
    assert queue_config.get_resource(cfg, "p1", "root")["path"] == "/repo"


def test_parse_path_lines():
    assert parse_path_lines("/.git\n  /.env  \n\n/certs") == [
        "/.git", "/.env", "/certs"]


def test_experimental_labels():
    from _pkg.tui import _render_queue_rows, _queue_help_text
    assert "enforced for claude tool calls" in QUEUE_EXPERIMENTAL.lower()
    assert "experimental" in _render_queue_rows([]).lower()  # pane header tag
    assert QUEUE_EXPERIMENTAL in _queue_help_text()          # full caveat
```

Run: `python3 -m pytest test/test_queue_templates.py -q` → expected FAIL (no `SHARED_ROOT_DEFAULTS` yet).

- [ ] **Step 2: Replace the template block in `tui.py`**

Delete lines 88–175 (`QUEUE_TEMPLATES`, `template_resource`, `parse_guard_lines`, `format_guard_lines`) and lines for `parse_wait_for`/`format_wait_for` (~182–205), keeping `parse_path_lines`. Replace `QUEUE_EXPERIMENTAL` (line 83) and add the defaults:

```python
QUEUE_EXPERIMENTAL = ("Experimental — enforced for Claude tool calls only; it "
                      "cannot stop a non-Claude process from touching the "
                      "resource. Don't rely on it for safety.")

# The one resource shape the setup dialog writes (leased-ground spec): the
# overlay-and-restore mutex on the shared installed root. The engine still
# understands the other kinds/strategies for back-compat configs; they are
# just no longer a UI surface.
SHARED_ROOT_DEFAULTS = {
    "kind": "root-dir", "acquire": "command", "release": "command",
    "run_in": "root",
    "command_acquire": "session-explorer queue-overlay in",
    "command_release": "session-explorer queue-overlay out",
    "release_required": False,
}
```

- [ ] **Step 3: Replace the two screens with `SharedRootScreen`**

Delete `ResourceListScreen` (758–851) and `ResourceEditorScreen` (854–1120) entirely. In their place:

```python
class SharedRootScreen(_PanelScreen):
    """Single per-project setup dialog (leased-ground spec): share / stop
    sharing the installed root, with an optional protect list. Saving applies
    the overlay shape (SHARED_ROOT_DEFAULTS) — including migrating an existing
    root-dir resource of any older shape onto it, keeping its resource id.
    Returns True when the config changed."""

    RESOURCE_ID = "root"

    BINDINGS = [
        Binding("escape", "dismiss(False)", "Close"),
        Binding("ctrl+s", "save", "Share / save"),
        Binding("ctrl+d", "stop_sharing", "Stop sharing"),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def __init__(self, *, project_root: str, project_id: str,
                 config_path: str) -> None:
        super().__init__()
        self._project_root = project_root
        self._project_id = project_id
        self._config_path = config_path
        from . import project_id as _pid
        # The shared root is the repo's MAIN working tree, never the selected
        # node (which can be a worktree shown as its own project).
        self._root_path = _pid.main_root(project_root) or project_root
        self._existing_rid: "str | None" = None

    def compose(self) -> ComposeResult:
        from . import queue_config as _qc
        resources = _qc.list_resources(self._config_path, self._project_id)
        rid = next((r for r in sorted(resources)
                    if resources[r].get("kind") == "root-dir"), None)
        self._existing_rid = rid
        existing = resources.get(rid, {}) if rid else {}
        protect = "\n".join(existing.get("sync", {}).get("protect", []))
        if rid:
            status = (f"shared as '{rid}' (acquire: "
                      f"{existing.get('acquire', '?')})")
        else:
            status = "not shared"
        yield Vertical(
            Label(f"Shared installed root — {_basename(self._project_root)}",
                  classes="dialog-title"),
            Label(QUEUE_EXPERIMENTAL, classes="dialog-hint"),
            Label(f"Root:   {self._root_path}\nStatus: {status}",
                  id="sr-status"),
            Label("Tool calls that touch the root from a worktree session "
                  "are denied; work runs through "
                  "`queue-run -- <cmd>` (overlay in → run → restore).",
                  classes="dialog-hint"),
            Label("Protect — root-only paths to keep, one per line (optional)",
                  classes="dialog-hint"),
            TextArea(protect, id="sr-protect"),
            Label("", id="sr-error", classes="dialog-hint"),
            Label("ctrl-s share/save · ctrl-d stop sharing · esc close",
                  classes="dialog-hint"),
            id="panel",
        )

    def action_save(self) -> None:
        from . import queue_config as _qc
        res = dict(SHARED_ROOT_DEFAULTS)
        res["path"] = self._root_path
        protect = parse_path_lines(self.query_one("#sr-protect", TextArea).text)
        if protect:
            # Stored under sync.protect for schema continuity; the overlay
            # acquire never rsyncs, so this is data for future use + display.
            res["sync"] = {"delete": False, "exclude": [], "protect": protect}
        try:
            _qc.add_resource(
                self._config_path, project_id=self._project_id,
                display_path=self._project_root,
                resource_id=self._existing_rid or self.RESOURCE_ID,
                resource=res)
        except ValueError as e:
            self.query_one("#sr-error", Label).update(f"[red]{e}[/]")
            return
        self.dismiss(True)

    def action_stop_sharing(self) -> None:
        from . import queue_config as _qc
        rid = self._existing_rid
        if not rid:
            self.dismiss(False)
            return

        def after(ok: bool) -> None:
            if ok:
                _qc.remove_resource(self._config_path, self._project_id, rid)
                self.dismiss(True)

        self.app.push_screen(
            ConfirmScreen(f"Stop sharing the installed root ('{rid}')? "
                          "(queue config only; no files are touched)"), after)

    def action_help(self) -> None:
        # QueueHelpScreen's only remaining entry point now that the resource
        # list is gone — keep `?` reachable from the setup dialog.
        self.app.push_screen(QueueHelpScreen())
```

Update `action_resource_setup` (line 2018–2029): replace the `ResourceListScreen` push with

```python
        self.push_screen(SharedRootScreen(project_root=project, project_id=pid,
                                          config_path=self._queue_config_path()))
```

- [ ] **Step 4: Remove the detector wiring**

In `tui.py`: delete the `self._detect_snaps` / `self._detect_warned` attributes (lines ~1310–1314), the `self._detect_out_of_lease()` call at the end of `_poll_live` (line 2497), and the whole `_detect_out_of_lease` method (lines 2499–2543). Search the file for `guard_match` — the only import was inside the deleted `action_test_guard`; verify none remain: `grep -n "guard_match\|queue_detect" bin/_pkg/tui.py` must return nothing.

- [ ] **Step 5: Rewrite `_queue_help_text`**

Replace the body of `_queue_help_text()` (line 1127 onward) with:

```python
def _queue_help_text() -> str:
    return "\n".join([
        f"[b]Shared installed root — quick help[/]  [dim]— {QUEUE_EXPERIMENTAL}[/]",
        "",
        "[b]The model.[/] One project root holds the installed app; worktrees",
        "hold code changes. The root is [b]leased ground[/]: tool calls that",
        "touch it from a worktree session are denied by a PreToolUse hook.",
        "",
        "[b]The one door.[/] `session-explorer queue-run --resource <id> -- <cmd>`",
        "takes the FIFO lease, overlays your changed files into the root, runs",
        "your command there, restores the overlay, and releases — on success,",
        "failure, or interrupt. `queue-status` shows the holder and queue.",
        "",
        "[b]Limits.[/] Non-Claude processes and commands that compute the root",
        "path at runtime aren't blocked — the dirty-root refusal at the next",
        "lease is the backstop. A live Claude session working IN the root",
        "blocks worktree leases until it ends (and vice versa is fine).",
        "",
        f"Full guide: {QUEUE_GUIDE_URL}",
    ])
```

- [ ] **Step 6: Prune and extend `test/test_tui_queue.py`**

Delete these tests (they exercise deleted code): `test_resource_list_lists_configured_resources`, `test_editor_saves_a_resource`, `test_editor_saves_guard_and_protect_for_root_dir`, `test_root_dir_path_is_main_worktree_not_the_selected_worktree`, `test_root_dir_ignores_path_edits_and_saves_wait_for`, `test_editing_clears_stale_command_and_health`, `test_selecting_template_populates_release_and_health_fields`, `test_malformed_wait_for_is_refused_not_dropped`, `test_editor_guard_tester_uses_edited_guard`, `test_dry_run_refuses_when_source_equals_root`, `test_dry_run_surfaces_transition_guard_for_dirty_root`, `test_out_of_lease_toast`, `test_out_of_lease_rearms_after_stable_poll`, `test_no_toast_during_live_root_session`.

Update `test_queue_help_mentions_protect_and_guide` to assert the new help: `"leased ground"` and `QUEUE_GUIDE_URL` present.

Append (same harness style as the file's existing screen tests):

```python
@pytest.mark.asyncio
async def test_shared_root_screen_saves_overlay_shape(index_path, tmp_path,
                                                      monkeypatch):
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import SharedRootScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SharedRootScreen(project_root=str(repo), project_id=pid,
                                  config_path=qcfg)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#sr-protect", TextArea).text = "/.env\n/certs"
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "root")
    assert res["acquire"] == "command"
    assert res["command_acquire"] == "session-explorer queue-overlay in"
    assert res["command_release"] == "session-explorer queue-overlay out"
    assert res["sync"]["protect"] == ["/.env", "/certs"]
    assert "guard" not in res


@pytest.mark.asyncio
async def test_shared_root_screen_migrates_existing_root_resource(
        index_path, tmp_path, monkeypatch):
    # An old sync-shaped root resource (e.g. the misapplied bind-mounted-stack
    # template) is migrated onto the overlay shape on save, keeping its id.
    import subprocess
    from _pkg import project_id, queue_config
    from _pkg.tui import SharedRootScreen
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    pid = project_id.project_id(str(repo))
    qcfg = str(tmp_path / "qc.json")
    monkeypatch.setenv("SESSION_EXPLORER_QUEUE_CONFIG", qcfg)
    queue_config.add_resource(
        qcfg, project_id=pid, display_path=str(repo),
        resource_id="royal-magento-docker",
        resource={"kind": "root-dir", "path": str(repo), "run_in": "root",
                  "acquire": "sync", "release": "none",
                  "guard": [{"exe": "docker", "sub": ["compose", "up"]}],
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git", "/.env"]}})
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = SharedRootScreen(project_root=str(repo), project_id=pid,
                                  config_path=qcfg)
        app.push_screen(screen)
        await pilot.pause()
        screen.action_save()
        await pilot.pause()
    res = queue_config.get_resource(qcfg, pid, "royal-magento-docker")
    assert res["acquire"] == "command"          # migrated off sync
    assert res["sync"]["protect"] == ["/.git", "/.env"]  # protect carried over
    assert queue_config.get_resource(qcfg, pid, "root") is None  # id kept
```

- [ ] **Step 7: Run the TUI suites**

Run: `python3 -m pytest test/test_queue_templates.py test/test_tui_queue.py test/test_tui.py test/test_tui_live.py test/test_snapshot.py -q`
Expected: PASS. If `test_tui.py`/`test_snapshot.py` reference any deleted symbol (grep for `ResourceEditor|ResourceList|template_resource|parse_wait_for`), update those references to `SharedRootScreen`/`SHARED_ROOT_DEFAULTS` equivalents or delete the assertion.

- [ ] **Step 8: Commit**

```bash
git add bin/_pkg/tui.py test/test_queue_templates.py test/test_tui_queue.py test/test_tui.py test/test_snapshot.py
git commit -m "feat(tui): collapse setup to SharedRootScreen; drop templates, guard editor, mtime detector"
```

---

### Task 8: Delete `guard_match.py` and `queue_detect.py`

Only safe now: Task 6 removed the `queue_awareness` import, Task 7 removed both `tui.py` imports.

**Files:**
- Delete: `bin/_pkg/guard_match.py`, `bin/_pkg/queue_detect.py`
- Delete: `test/test_guard_match.py`, `test/test_queue_detect.py`

- [ ] **Step 1: Verify nothing imports them**

Run: `grep -rn "guard_match\|queue_detect" bin/ test/ hooks/ install.sh | grep -v "\.pyc"`
Expected: no output. If anything appears, fix that reference first (it is a missed seam from Tasks 6–7).

- [ ] **Step 2: Delete**

```bash
git rm bin/_pkg/guard_match.py bin/_pkg/queue_detect.py test/test_guard_match.py test/test_queue_detect.py
```

- [ ] **Step 3: Run the full Python suite**

Run: `python3 -m pytest test/ -q`
Expected: PASS, no collection errors.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: delete guard_match + queue_detect (replaced by root_guard)"
```

---### Task 9: Documentation — SPEC.md, README, queue-guide, CHANGELOG

**Files:**
- Modify: `SPEC.md` — the "Shared-resource lease engine" section: replace the §8 awareness/command-guard model with the location guard (deny rules, allowlist, fail-open plumbing / fail-closed semantics, registry-based session classification, honest limits); remove the §6 detection-flag paragraph; update the experimental claim to "enforced for Claude tool calls, advisory beyond them"; replace the TUI two-level-setup description with the SharedRootScreen dialog.
- Modify: `README.md` — the queue section's `> ⚠️ Experimental` callout text gains the new claim; the setup flow description (`q` → `s`) now describes the single dialog.
- Modify: `docs/queue-guide.md` — top banner keeps experimental; replace the guard-list/template-catalog sections with: the leased-ground model, the deny behavior + exact messages an agent sees, the `queue-run` door, and the limits (computed paths, non-Claude writers, dirty-root backstop).
- Modify: `CLAUDE.md` — update the "Phase-3 awareness/enforcement is advisory and fail-open" bullet: enforcement is now location-based and fail-closed in semantics (root_guard), plumbing still fail-open; `guard_match`/`queue_detect` no longer exist.
- Modify: `CHANGELOG.md` — add the `## 1.17.0` section (content below; do NOT bump version files yet — that is Task 10).

- [ ] **Step 1: Make the documentation edits above**

CHANGELOG entry to add:

```markdown
## 1.17.0 — Leased ground: location-enforced shared root

The shared-resource guard is inverted from advisory command-matching to a
fail-closed location rule: for a Claude session in a worktree, the shared
installed root is unreachable through tools (Bash/Edit/Write/NotebookEdit)
except via `session-explorer queue-*`. Denies carry the exact `queue-run`
rewrite. Plumbing still fails open (a broken hook never blocks tool calls);
non-Claude writers remain out of scope (dirty-root refusal is the backstop).

- New `root_guard.py` decision module behind the existing PreToolUse hook;
  matcher widened to `Bash|Edit|Write|NotebookEdit` on both install paths.
- Removed: `{exe, sub}` guard lists (`guard_match.py`), the out-of-lease
  mtime detector (`queue_detect.py`), the template library and generic
  resource editor. Per-project setup is now one "Shared installed root"
  dialog (overlay shape, protect list); existing root-dir resources migrate
  on save.
- SessionStart awareness text shrinks to a short usage hint.
- Experimental claim updated: enforced for Claude tool calls, advisory
  beyond them.
```

- [ ] **Step 2: Self-check the docs**

Run: `grep -rn "guard_match\|queue_detect\|out-of-lease access\|{exe" SPEC.md README.md docs/queue-guide.md CLAUDE.md`
Expected: no stale references to removed mechanisms (mentions inside a "what changed / history" sentence are fine; live descriptions are not).

- [ ] **Step 3: Commit**

```bash
git add SPEC.md README.md docs/queue-guide.md CLAUDE.md CHANGELOG.md
git commit -m "docs: leased-ground model in SPEC/README/queue-guide/CLAUDE.md + 1.17.0 changelog"
```

---

### Task 10: Full verification + version bump

Per the phased-delivery rule: one bump at the end, after everything is green. The GitHub release itself happens via the `cutting-a-release` skill only after Johan has tested the branch.

**Files:**
- Modify: `bin/_pkg/__init__.py` (`__version__ = "1.17.0"`)
- Modify: `.claude-plugin/plugin.json` (`"version": "1.17.0"`)

- [ ] **Step 1: Run everything**

```bash
python3 -m pytest test/ -q
bats test/install.bats test/uninstall.bats test/hook.bats
```
Expected: all PASS.

- [ ] **Step 2: Bump the version**

Set `__version__ = "1.17.0"` in `bin/_pkg/__init__.py` and `"version": "1.17.0"` in `.claude-plugin/plugin.json`. Check `README.md`/`SPEC.md` status lines for a version mention and update if present (the `cutting-a-release` skill's checklist).

- [ ] **Step 3: Commit and push the branch**

```bash
git add bin/_pkg/__init__.py .claude-plugin/plugin.json README.md SPEC.md
git commit -m "chore: bump to v1.17.0 (leased ground)"
git push -u origin leased-ground-root-guard
```

- [ ] **Step 4: Manual smoke test (report, don't release)**

Tell Johan the branch is ready and how to smoke it against the Magento project:
1. `rsync` the branch to the marketplace cache copy per the install-layout memory (the running `/open` uses the cache, not the repo).
2. In a worktree Claude session: try `Edit` on a root file and `cp x <root>/x` — both must deny with the queue-run rewrite; `session-explorer queue-run --resource <rid> -- phpunit …` must still work.
3. PR + release (`cutting-a-release`) only after his go-ahead.
