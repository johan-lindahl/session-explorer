# Overlay-and-restore Test-Queue Resource — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class "shared installed app root, overlay-and-restore" queue resource so worktree sessions can run tests in the installed Magento root through a serialized overlay that auto-restores on completion, failure, or signal (`queue-run -- phpunit`) instead of an uncoordinated hand-rolled `cp`/`git restore`.

**Architecture:** A `root-dir` resource with `acquire=command`/`release=command` wired to a shipped overlay helper (`session-explorer queue-overlay in|out`). The helper copies the worktree's changed files into root on acquire and restores exactly those paths on release; the lease engine already runs release in a `finally`, so restore runs on normal completion, child failure, and handled SIGINT/SIGTERM. **It is NOT crash-proof:** a SIGKILL or hard process crash skips the `finally`, leaving the overlay in place — the next acquire then refuses the now-dirty root (`exclusive.transition_guard`), surfacing it instead of double-overlaying. One small engine change exposes the worktree/root/state-dir to the command hooks via env vars. A new template captures the pattern with a curated PHP guard list. The whole queue subsystem is labeled experimental across the TUI and docs.

**Tech Stack:** Python 3.11+ (stdlib only — `subprocess`, `shutil`, `json`), vendored Textual for the TUI, pytest (+ pytest-asyncio), bats for shell. Spec: `docs/superpowers/specs/2026-06-08-overlay-test-queue-resource-design.md`.

---

## File structure

- **Create** `bin/_pkg/overlay.py` — pure overlay logic (`changed_files`, `apply_overlay`, `restore_overlay`) + manifest. No Textual.
- **Create** `test/test_overlay.py` — unit tests for the helper against a real git repo + worktree.
- **Modify** `bin/_pkg/queue_run.py` — `_run_shell` gains `env`; `_do_acquire` (command branch) and `_do_release` export `SE_QUEUE_*`; `_do_release` gains `src`/`qdir`.
- **Modify** `bin/_pkg/cli.py` — `queue-overlay {in,out}` subcommand + dispatch.
- **Modify** `bin/_pkg/tui.py` — new `overlay-installed-root` template; `QUEUE_EXPERIMENTAL` constant; experimental labels on the Queues pane header, activation hint, resource screens, and help text.
- **Modify** `bin/_pkg/queue_awareness.py` — overlay-aware guidance + experimental note in `_render_context`.
- **Modify** `test/test_queue_run.py`, `test/test_queue_templates.py`, `test/test_queue_awareness.py` — new coverage.
- **Modify** `README.md`, `SPEC.md`, `docs/queue-guide.md`, `CHANGELOG.md` — experimental labeling + overlay docs.
- **Modify** `bin/_pkg/__init__.py`, `.claude-plugin/plugin.json` — version bump (final task).

**Run all tests:** `python3 -m pytest test/ -q` (from repo root). Single file shown per task.

---

### Task 1: Engine exposes `SE_QUEUE_*` env to command hooks

The overlay helper needs to know its source worktree, target root, and where to write the manifest. The engine knows all three (`src`, `root`, `qdir`) but does not pass them to `command_acquire`/`command_release`. Add an `env` param to `_run_shell` and export the three vars from both command branches. `_do_release` currently lacks `src`/`qdir` — add them.

**Files:**
- Modify: `bin/_pkg/queue_run.py:70-82` (`_run_shell`, `_do_acquire` command branch), `:122-132` (`_do_release`), `:270` (release call site)
- Test: `test/test_queue_run.py`

- [ ] **Step 1: Write the failing test** — append to `test/test_queue_run.py`:

```python
def test_command_hooks_receive_all_se_queue_env(tmp_path, paths):
    """Both acquire AND release hooks see all three SE_QUEUE_* vars. Run from a
    real worktree so WORKTREE, ROOT and STATE_DIR are three distinct values — a
    bug omitting any one fails here, not silently at runtime."""
    import os as _os
    root = _repo(tmp_path)
    from _pkg import project_id as _pid
    pid = _pid.project_id(str(root))
    wt = tmp_path / "wt"
    _git(root, "worktree", "add", "-q", "-b", "feat", str(wt))
    acq, rel = tmp_path / "acq.txt", tmp_path / "rel.txt"
    dump = ("(printenv SE_QUEUE_WORKTREE; printenv SE_QUEUE_ROOT; "
            "printenv SE_QUEUE_STATE_DIR)")
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="ov",
                    resource={"kind": "root-dir", "path": _pid.main_root(str(root)),
                              "run_in": "root", "acquire": "command",
                              "release": "command",
                              "command_acquire": f"{dump} > {acq}",
                              "command_release": f"{dump} > {rel}"})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="ov",
        command=["true"], cwd=str(wt), sid="s1", pid=os.getpid())
    assert rc == 0
    qdir = queue_run.queue_dir(paths["queues_root"], pid, "ov")
    expected = [_os.path.realpath(str(wt)), _pid.main_root(str(root)), qdir]
    assert acq.read_text().splitlines() == expected   # acquire saw all three
    assert rel.read_text().splitlines() == expected   # release saw all three
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest test/test_queue_run.py::test_command_hooks_receive_all_se_queue_env -q`
Expected: FAIL (env vars unset → files empty / mismatch).

- [ ] **Step 3: Add `env` to `_run_shell`** — replace `bin/_pkg/queue_run.py:70-71`:

```python
def _run_shell(command: str, cwd: Optional[str], timeout: Optional[float],
               env: Optional[dict] = None) -> int:
    run_env = {**os.environ, **env} if env else None
    return subprocess.run(command, shell=True, cwd=cwd, timeout=timeout,
                          env=run_env).returncode
```

- [ ] **Step 4: Export env from `_do_acquire` command branch** — replace `bin/_pkg/queue_run.py:79-83`:

```python
    if strategy == "command":
        cmd = resource.get("command_acquire")
        env = _hook_env(src=src, root=root, qdir=qdir)
        if cmd and _run_shell(cmd, cwd=root, timeout=None, env=env) != 0:
            return "acquire command failed"
        return None
```

- [ ] **Step 5: Add the `_hook_env` helper and update `_do_release`** — insert `_hook_env` just above `_do_acquire` (before line 74) and replace `_do_release` (`:122-132`):

```python
def _hook_env(*, src: str, root: str, qdir: str) -> dict:
    """Env exported to command_acquire/command_release (overlay helper contract)."""
    return {"SE_QUEUE_WORKTREE": src, "SE_QUEUE_ROOT": root,
            "SE_QUEUE_STATE_DIR": qdir}


def _do_release(resource: dict, *, root: str, src: str, qdir: str) -> bool:
    """Run the release hook (time-bounded). Returns True on success/none."""
    if resource.get("release") != "command":
        return True
    cmd = resource.get("command_release")
    if not cmd:
        return True
    try:
        return _run_shell(cmd, cwd=root, timeout=_RELEASE_TIMEOUT,
                          env=_hook_env(src=src, root=root, qdir=qdir)) == 0
    except subprocess.TimeoutExpired:
        return False
```

- [ ] **Step 6: Update the `_do_release` call site** — in `run_lease`, replace `ok = _do_release(resource, root=root)` (around `:270`) with:

```python
                    ok = _do_release(resource, root=root, src=src, qdir=qdir)
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `python3 -m pytest test/test_queue_run.py -q`
Expected: PASS (all existing tests + the new one).

- [ ] **Step 8: Commit**

```bash
git add bin/_pkg/queue_run.py test/test_queue_run.py
git commit -m "feat(queue): export SE_QUEUE_* env to command acquire/release hooks"
```

---

### Task 2: The overlay helper (`overlay.py`)

Pure logic: compute the worktree's changed files, copy them into root with a manifest, and restore exactly those paths. Modified files (existed in root) restore via `git checkout`; added files (new in root) restore via `rm`.

**Files:**
- Create: `bin/_pkg/overlay.py`
- Test: `test/test_overlay.py`

- [ ] **Step 1: Write the failing test** — create `test/test_overlay.py`:

```python
import subprocess

from _pkg import overlay


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _repo_with_worktree(tmp_path):
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "src.txt").write_text("ROOT\n")
    _git(root, "add", "src.txt")
    _git(root, "commit", "-qm", "init")
    wt = tmp_path / "wt"
    _git(root, "worktree", "add", "-q", "-b", "feat", str(wt))
    return root, wt


def test_apply_then_restore_modified_and_added(tmp_path):
    root, wt = _repo_with_worktree(tmp_path)
    state = tmp_path / "state"
    # Worktree modifies an existing file and adds a new (untracked) one.
    (wt / "src.txt").write_text("WORKTREE\n")
    (wt / "new.txt").write_text("NEW\n")

    manifest = overlay.apply_overlay(str(wt), str(root), str(state))
    by_path = {m["path"]: m["status"] for m in manifest}
    assert by_path == {"src.txt": "modified", "new.txt": "added"}
    assert (root / "src.txt").read_text() == "WORKTREE\n"   # overlaid in
    assert (root / "new.txt").read_text() == "NEW\n"
    assert (state / overlay.MANIFEST_NAME).exists()

    overlay.restore_overlay(str(root), str(state))
    assert (root / "src.txt").read_text() == "ROOT\n"       # checkout-restored
    assert not (root / "new.txt").exists()                  # rm-restored
    assert not (state / overlay.MANIFEST_NAME).exists()     # manifest cleaned


def test_restore_without_manifest_is_noop(tmp_path):
    root, _ = _repo_with_worktree(tmp_path)
    overlay.restore_overlay(str(root), str(tmp_path / "empty"))  # must not raise
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest test/test_overlay.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.overlay'`.

- [ ] **Step 3: Create `bin/_pkg/overlay.py`**

```python
"""Overlay-and-restore helper for the 'shared installed app root' queue resource.

Wired as a root-dir resource's command hooks (acquire=command/release=command):

    command_acquire: session-explorer queue-overlay in
    command_release: session-explorer queue-overlay out

The engine runs both with cwd=root and exports SE_QUEUE_WORKTREE (overlay
source), SE_QUEUE_ROOT (overlay target) and SE_QUEUE_STATE_DIR (manifest dir).

`apply_overlay` copies the worktree's changed files into root and records a
manifest. `restore_overlay` undoes exactly those paths (git checkout for files
that existed in root, rm for ones the overlay created) and deletes the manifest.
restore runs in the engine's release `finally`, so it survives normal
completion, child failure, and handled SIGINT/SIGTERM — but NOT a SIGKILL/hard
crash, which skips the finally (the next overlay then refuses the dirty root).

Pure helpers take explicit paths so they unit-test without env. No Textual.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import List

MANIFEST_NAME = "overlay.manifest"


def _git_lines(cwd: str, *args: str) -> List[str]:
    out = subprocess.run(["git", "-C", cwd, *args],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def changed_files(worktree: str, root: str) -> List[str]:
    """Relpaths whose worktree version differs from root's checked-out commit,
    plus the worktree's untracked (non-ignored) files. Deduped, sorted."""
    paths = set()
    head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode == 0:
        base = head.stdout.strip()
        paths.update(_git_lines(worktree, "diff", "--name-only", base))
    paths.update(_git_lines(worktree, "ls-files", "--others", "--exclude-standard"))
    return sorted(paths)


def apply_overlay(worktree: str, root: str, state_dir: str) -> List[dict]:
    """Copy each changed file from worktree into root; write + return a manifest
    of {path, status} where status is 'modified' (existed in root) or 'added'."""
    manifest = []
    for rel in changed_files(worktree, root):
        src = os.path.join(worktree, rel)
        if not os.path.isfile(src):
            continue   # deleted / non-regular path: v1 copies-in only
        dst = os.path.join(root, rel)
        status = "modified" if os.path.exists(dst) else "added"
        os.makedirs(os.path.dirname(dst) or root, exist_ok=True)
        shutil.copy2(src, dst)
        manifest.append({"path": rel, "status": status})
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    return manifest


def restore_overlay(root: str, state_dir: str) -> None:
    """Undo a prior apply_overlay: git-checkout modified paths, rm added ones,
    then delete the manifest. Missing/corrupt manifest -> nothing to do."""
    mpath = os.path.join(state_dir, MANIFEST_NAME)
    try:
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return
    for entry in manifest:
        rel = entry.get("path")
        if not rel:
            continue
        if entry.get("status") == "added":
            try:
                os.remove(os.path.join(root, rel))
            except OSError:
                pass
        else:
            subprocess.run(["git", "-C", root, "checkout", "--", rel],
                           capture_output=True, text=True)
    try:
        os.remove(mpath)
    except OSError:
        pass
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest test/test_overlay.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/overlay.py test/test_overlay.py
git commit -m "feat(queue): overlay helper — copy changed worktree files into root, restore exactly"
```

---

### Task 3: `queue-overlay {in,out}` CLI subcommand

Wire the helper as an engine-invoked subcommand. `in` refuses (nonzero) on a dirty root so the engine treats acquire as failed and does not run the test against an unsafe root.

**Files:**
- Modify: `bin/_pkg/cli.py:119-122` (add parser after `queue-guard`), `:381` area (add `_cmd_queue_overlay`), `:420-423` (dispatch)
- Test: `test/test_cli.py` (subprocess-style; matches existing CLI coverage)

- [ ] **Step 1: Write the failing test** — append to `test/test_cli.py`:

```python
def test_queue_overlay_in_out_roundtrip(tmp_path):
    import os, subprocess
    from _pkg import overlay
    def g(cwd, *a):
        subprocess.run(["git", "-C", str(cwd), *a], check=True,
                       capture_output=True, text=True)
    root = tmp_path / "main"; root.mkdir()
    g(root, "init", "-q"); g(root, "config", "user.email", "t@t")
    g(root, "config", "user.name", "t")
    (root / "a.txt").write_text("ROOT\n"); g(root, "add", "a.txt")
    g(root, "commit", "-qm", "init")
    wt = tmp_path / "wt"; g(root, "worktree", "add", "-q", "-b", "feat", str(wt))
    (wt / "a.txt").write_text("WT\n")
    state = tmp_path / "state"
    env = {**os.environ, "SE_QUEUE_WORKTREE": str(wt),
           "SE_QUEUE_ROOT": str(root), "SE_QUEUE_STATE_DIR": str(state)}
    r = subprocess.run([_BIN, "queue-overlay", "in"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (root / "a.txt").read_text() == "WT\n"
    r = subprocess.run([_BIN, "queue-overlay", "out"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (root / "a.txt").read_text() == "ROOT\n"
```

(`_BIN = <repo>/bin/session-explorer` is already defined at the top of `test/test_cli.py`; no new import needed beyond `os`/`subprocess`, which the file already imports.)

Also add the **dirty-root refusal** test — the core safety property (without it, Task 3 would pass even if the guard were dropped):

```python
def test_queue_overlay_in_refuses_dirty_root(tmp_path):
    import os, subprocess
    from _pkg import overlay
    def g(cwd, *a):
        subprocess.run(["git", "-C", str(cwd), *a], check=True,
                       capture_output=True, text=True)
    root = tmp_path / "main"; root.mkdir()
    g(root, "init", "-q"); g(root, "config", "user.email", "t@t")
    g(root, "config", "user.name", "t")
    (root / "a.txt").write_text("ROOT\n"); g(root, "add", "a.txt")
    g(root, "commit", "-qm", "init")
    wt = tmp_path / "wt"; g(root, "worktree", "add", "-q", "-b", "feat", str(wt))
    (wt / "a.txt").write_text("WT\n")          # worktree has a change to overlay
    (root / "a.txt").write_text("DIRTY\n")     # but root is dirty -> must refuse
    state = tmp_path / "state"
    env = {**os.environ, "SE_QUEUE_WORKTREE": str(wt),
           "SE_QUEUE_ROOT": str(root), "SE_QUEUE_STATE_DIR": str(state)}
    r = subprocess.run([_BIN, "queue-overlay", "in"], env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0                                   # refused
    assert (root / "a.txt").read_text() == "DIRTY\n"          # NOT overlaid
    assert not (state / overlay.MANIFEST_NAME).exists()        # no manifest written
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest test/test_cli.py::test_queue_overlay_in_out_roundtrip -q`
Expected: FAIL (`invalid choice: 'queue-overlay'` from argparse).

- [ ] **Step 3: Add the parser** — after the `queue-guard` parser (`bin/_pkg/cli.py:122`) insert:

```python
    qov = sub.add_parser(
        "queue-overlay",
        help="Engine-invoked overlay helper (in|out) for the shared installed "
             "app root template. Reads SE_QUEUE_WORKTREE/ROOT/STATE_DIR env.")
    qov.add_argument("direction", choices=["in", "out"])
```

- [ ] **Step 4: Add the command function** — insert near the other `_cmd_queue_*` functions (e.g. after `_cmd_queue_guard`):

```python
def _cmd_queue_overlay(args) -> int:
    """Overlay (`in`) or restore (`out`) the shared installed app root. Reads
    the SE_QUEUE_* env the engine exports. `in` refuses on a dirty root so the
    engine treats acquire as failed."""
    from . import exclusive as _ex
    from . import overlay as _ov
    worktree = os.environ.get("SE_QUEUE_WORKTREE", "")
    root = os.environ.get("SE_QUEUE_ROOT", "")
    state_dir = os.environ.get("SE_QUEUE_STATE_DIR", "")
    if not root or not state_dir:
        print("queue-overlay: missing SE_QUEUE_ROOT/SE_QUEUE_STATE_DIR env",
              file=sys.stderr)
        return 1
    if args.direction == "in":
        if not worktree:
            print("queue-overlay: missing SE_QUEUE_WORKTREE env", file=sys.stderr)
            return 1
        guard = _ex.transition_guard(root)
        if guard:
            print(f"queue-overlay: refusing overlay — {guard}", file=sys.stderr)
            return 1
        _ov.apply_overlay(worktree, root, state_dir)
        return 0
    _ov.restore_overlay(root, state_dir)
    return 0
```

- [ ] **Step 5: Add dispatch** — in `main`, next to the other queue dispatches (`bin/_pkg/cli.py:420`):

```python
    if args.cmd == "queue-overlay":
        return _cmd_queue_overlay(args)
```

- [ ] **Step 6: Run both tests to verify they pass**

Run: `python3 -m pytest test/test_cli.py -k queue_overlay -q`
Expected: PASS (roundtrip + dirty-root refusal).

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(queue): queue-overlay in|out subcommand (dirty-root refusal on in)"
```

---

### Task 4: The `overlay-installed-root` template + curated guard

Add the template so the pattern is one-click and correctly modeled (mutex, no rsync), with a PHP-flavored curated guard list (phpunit/phpstan/magento di+upgrade — deliberately NOT phpcs).

**Files:**
- Modify: `bin/_pkg/tui.py:88` (insert before the `custom` template)
- Test: `test/test_queue_templates.py`

- [ ] **Step 1: Write the failing test** — append to `test/test_queue_templates.py`:

```python
def test_overlay_template_is_command_mutex_with_curated_guard():
    res = template_resource("overlay-installed-root", path="/repo")
    assert res["kind"] == "root-dir"
    assert res["acquire"] == "command"      # NOT sync — no rsync --delete
    assert res["release"] == "command"
    assert res["run_in"] == "root"
    assert res["path"] == "/repo"
    assert res["command_acquire"] == "session-explorer queue-overlay in"
    assert res["command_release"] == "session-explorer queue-overlay out"
    exes = {(r["exe"], tuple(r["sub"])) for r in res["guard"]}
    assert ("phpunit", ()) in exes
    assert ("phpstan", ()) in exes
    assert ("magento", ("setup:di:compile",)) in exes
    # phpcs is worktree-safe and must NOT be guarded through the root mutex.
    assert not any(r["exe"] in ("phpcs", "php-cs-fixer") for r in res["guard"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest test/test_queue_templates.py::test_overlay_template_is_command_mutex_with_curated_guard -q`
Expected: FAIL (template key not found → falls back to `custom`, assertions fail).

- [ ] **Step 3: Add the template** — insert into `QUEUE_TEMPLATES` immediately before the `custom` entry (`bin/_pkg/tui.py:88`):

```python
    {"key": "overlay-installed-root",
     "title": "Shared installed app root (overlay tests)",
     "defaults": {"kind": "root-dir", "acquire": "command", "release": "command",
                  "run_in": "root",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out",
                  "release_required": False,
                  "guard": [{"exe": "phpunit", "sub": []},
                            {"exe": "phpstan", "sub": []},
                            {"exe": "magento", "sub": ["setup:di:compile"]},
                            {"exe": "magento", "sub": ["setup:upgrade"]}]}},
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest test/test_queue_templates.py -q`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_queue_templates.py
git commit -m "feat(queue): overlay-installed-root template (command mutex, curated PHP guard)"
```

---

### Task 5: Overlay-aware + experimental awareness text

Sharpen the SessionStart awareness so worktree sessions route root-needing tests through `queue-run` and never hand-roll cp/restore, and state the coordination is experimental/advisory.

**Files:**
- Modify: `bin/_pkg/queue_awareness.py:35-64` (`_render_context`)
- Test: `test/test_queue_awareness.py`

- [ ] **Step 1: Write the failing test** — append to `test/test_queue_awareness.py` (mirror the file's existing fixture/setup for building a config; adjust names to match):

```python
def test_context_mentions_overlay_and_experimental(tmp_path):
    from _pkg import project_id as _pid, queue_config as qc, queue_awareness as qa
    import subprocess
    root = tmp_path / "main"; root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    cfg = str(tmp_path / "qc.json")
    pid = _pid.project_id(str(root))
    qc.add_resource(cfg, project_id=pid, display_path=str(root),
                    resource_id="ov",
                    resource={"kind": "root-dir", "path": str(root),
                              "run_in": "root", "acquire": "command",
                              "release": "command",
                              "command_acquire": "session-explorer queue-overlay in",
                              "command_release": "session-explorer queue-overlay out"})
    text = qa.session_context(cfg, str(root))
    assert text is not None
    assert "experimental" in text.lower()
    assert "queue-run" in text
    assert "git restore" in text or "hand-roll" in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest test/test_queue_awareness.py::test_context_mentions_overlay_and_experimental -q`
Expected: FAIL (assertions on "experimental"/"hand-roll" not present).

- [ ] **Step 3: Update `_render_context`** — in `bin/_pkg/queue_awareness.py`, change the opening line and add two bullets. Replace the first list element (the intro string at `:37-39`) with:

```python
        "This project shares one or more singleton resources across its git "
        "worktrees, coordinated by session-explorer. NOTE: this coordination is "
        "EXPERIMENTAL and advisory — it cannot stop an uncoordinated process, so "
        "cooperate actively. Other Claude sessions may be using them right now.",
```

And append these two bullets to the `lines += [...]` cooperative list (after the `sync` bullet, before the `queue-status` bullet at `:62`):

```python
        "  - If a resource is a shared *installed app root*, tests/QA that need "
        "the installed app (e.g. phpunit, phpstan) must run via the lease: "
        "`session-explorer queue-run --resource <name> -- <command>`. The lease "
        "overlays your worktree's changed files into the root and restores them "
        "after (on completion, failure, or interrupt).",
        "  - Do NOT hand-roll your own overlay (cp files into the root, run, "
        "`git restore`). It bypasses the queue and collides with other sessions.",
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest test/test_queue_awareness.py -q`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_awareness.py test/test_queue_awareness.py
git commit -m "feat(queue): awareness text — overlay-via-queue-run + experimental note"
```

---

### Task 6: Experimental labeling across the TUI

The canonical full caveat is one constant, `QUEUE_EXPERIMENTAL`, used verbatim on the roomy surfaces — both resource screens and the help text. The space-limited Queues pane header and activation hint can't fit a full sentence, so they carry a short `— experimental` tag instead. Tests assert the tag on the header + activation hint (mounted) and the full constant in the help text.

**Files:**
- Modify: `bin/_pkg/tui.py` — add `QUEUE_EXPERIMENTAL` constant (near `QUEUE_TEMPLATES`, ~`:48`); `_render_queue_rows` (`:434`); activation hint (`:2204`); `ResourceListScreen.compose` (`:723-731`); `ResourceEditorScreen.compose` (`:842`); `_queue_help_text` (`:1064`)
- Test: `test/test_queue_templates.py` (pure assertions: header + constant + help text); `test/test_tui_queue.py` (mounted assertion: activation hint)

- [ ] **Step 1: Write the failing pure tests** — append to `test/test_queue_templates.py`:

```python
def test_queues_header_and_help_are_labeled_experimental():
    from _pkg.tui import _render_queue_rows, _queue_help_text, QUEUE_EXPERIMENTAL
    assert "cooperative" in QUEUE_EXPERIMENTAL.lower()
    assert "experimental" in _render_queue_rows([]).lower()   # pane header tag
    assert QUEUE_EXPERIMENTAL in _queue_help_text()           # full caveat in help
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest test/test_queue_templates.py::test_queues_header_and_help_are_labeled_experimental -q`
Expected: FAIL (`ImportError: cannot import name 'QUEUE_EXPERIMENTAL'`).

- [ ] **Step 3: Add the constant** — insert above `QUEUE_TEMPLATES` (`bin/_pkg/tui.py:49`):

```python
QUEUE_EXPERIMENTAL = ("Experimental — cooperative only; it cannot stop an "
                      "uncoordinated process from touching the resource. "
                      "Don't rely on it for safety.")
```

- [ ] **Step 4: Label the Queues pane header** — replace `bin/_pkg/tui.py:434`:

```python
    lines = ["[b]Queues[/] [dim]— experimental[/]"]
```

- [ ] **Step 5: Label the activation hint** — replace the hint string at `bin/_pkg/tui.py:2204`:

```python
                "[b]Queues[/] [dim]— experimental[/]  ·  this project is not "
                "using shared resources\n"
```

- [ ] **Step 6: Banner on the resource list screen** — in `ResourceListScreen.compose` (`:724-731`), add a hint Label right after the title Label:

```python
        yield Vertical(
            Label(f"Shared resources — {_basename(self._project_root)}",
                  classes="dialog-title"),
            Label(QUEUE_EXPERIMENTAL, classes="dialog-hint"),
            OptionList(id="reslist"),
            Label("a add · e edit · Del remove · ? help · esc close",
                  classes="dialog-hint"),
            id="panel",
        )
```

- [ ] **Step 7: Banner on the resource editor screen** — in `ResourceEditorScreen.compose`, the title Label is `bin/_pkg/tui.py:842` (`Label(title, classes="dialog-title"),`) inside the `yield Vertical(` at `:841`. Insert the banner as the next line:

```python
        yield Vertical(
            Label(title, classes="dialog-title"),
            Label(QUEUE_EXPERIMENTAL, classes="dialog-hint"),
            Label("Template", classes="dialog-hint"),
            OptionList(*opts, id="res-template"),
            # ... rest of the existing Vertical body unchanged ...
```

- [ ] **Step 8: Lead the help text with the caveat** — `_queue_help_text` (`bin/_pkg/tui.py:1064`) is a single `return "\n".join([...])`. Change its first two list elements (currently `"[b]Shared resources — quick help[/]", "",`) to lead with the caveat:

```python
def _queue_help_text() -> str:
    return "\n".join([
        f"[b]Shared resources — quick help[/]  [yellow]({QUEUE_EXPERIMENTAL})[/]",
        "",
        # ... the rest of the existing list unchanged ...
```

- [ ] **Step 9: Add a mounted test for the activation hint** — append to `test/test_tui_queue.py` (mirrors its existing `index_path` fixture + `run_test` pattern; `str(pane.render())` is how that file reads pane content):

```python
@pytest.mark.asyncio
async def test_activation_hint_is_labeled_experimental(index_path):
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")          # open the pane on a no-resource project
        await pilot.pause()
        pane = app.query_one("#queues")
        assert pane.display is True
        assert "experimental" in str(pane.render()).lower()
```

> The resource list/editor screens embed `Label(QUEUE_EXPERIMENTAL, …)` directly (Steps 6–7), so they cannot drift from the constant the help-text test pins; a separate mount test for them is omitted as low-value.

- [ ] **Step 10: Run the pure + mounted tests + full TUI suite to verify nothing broke**

Run: `python3 -m pytest test/test_queue_templates.py test/test_tui_queue.py test/test_tui.py -q`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add bin/_pkg/tui.py test/test_queue_templates.py test/test_tui_queue.py
git commit -m "feat(queue): label the queue subsystem experimental across the TUI"
```

---

### Task 7: Docs + version bump

Documentation labeling and the overlay guide section, plus the version bump. Per the project's phased-delivery rule, the **release itself is cut after the user has tested** — this task only stages the bump and docs.

**Files:**
- Modify: `README.md` (queue section), `SPEC.md` ("Shared-resource lease engine" heading + a new overlay bullet), `docs/queue-guide.md` (top banner + overlay section), `CHANGELOG.md` (new section), `bin/_pkg/__init__.py` (version), `.claude-plugin/plugin.json` (version)

- [ ] **Step 1: README** — add an experimental callout to the queue section:

```markdown
> ⚠️ **Experimental.** The shared-resource queue is cooperative/advisory only —
> it cannot stop an uncoordinated process from touching a resource. Don't rely on
> it for safety.
```

- [ ] **Step 2: SPEC.md** — flag the "Shared-resource lease engine" heading as experimental and add a bullet under it describing the overlay resource:

```markdown
- **overlay-installed-root (experimental):** a `root-dir` resource with
  `acquire=command`/`release=command` wired to `session-explorer queue-overlay
  in|out`. On acquire it copies the holder worktree's changed files into root
  (refusing a dirty root via `exclusive.transition_guard`); on release the engine
  `finally` restores exactly those paths (git-checkout for modified, rm for
  added) on normal completion, child failure, and handled SIGINT/SIGTERM (a
  SIGKILL/hard crash skips the finally; the next acquire then refuses the dirty
  root). The engine exports `SE_QUEUE_WORKTREE`/`ROOT`/`STATE_DIR`
  to the command hooks. Models the "tests must run in the installed root" pattern
  without rsync. v1 copies-in only (deletes/generated artifacts not propagated)
  and remains advisory (a raw `cp`-into-root is unguardable).
```

- [ ] **Step 3: docs/queue-guide.md** — add a top banner and an overlay section:

```markdown
> ⚠️ **Experimental — cooperative only.** This system coordinates by convention.
> It cannot prevent an uncoordinated process from touching a shared resource.

## Shared installed app root (overlay tests)

When only your repo's main checkout is a fully installed app (vendor/, generated/,
DB, env) and worktrees are bare checkouts, tests must run *in* the root. Use the
"Shared installed app root (overlay tests)" template: it takes a FIFO mutex on the
root, copies your worktree's changed files in, runs your command, and restores
them after — even if the command fails. Run tests as:

    session-explorer queue-run --resource <name> -- phpunit path/to/Test.php

Guard the tools that need the root (phpunit, phpstan, `bin/magento setup:*`).
Do NOT guard phpcs / php-cs-fixer — they run fine in a bare worktree and must not
be serialized. `php bin/magento …` is a known guard blind spot (mitigated by the
awareness injection).
```

- [ ] **Step 4: CHANGELOG.md** — add a new section at the top (use the next version from Step 5):

```markdown
## vX.Y.0

- **Shared installed app root (overlay tests) — experimental.** New queue
  template + `queue-overlay in|out` helper: serialize "run tests in the installed
  root" overlays through a release/failure/signal-safe lease (not SIGKILL-proof)
  instead of hand-rolled cp/git-restore.
  Curated PHP guard (phpunit/phpstan/magento; not phpcs).
- The shared-resource queue subsystem is now clearly labeled **experimental**
  (TUI panes/dialogs/help + README/SPEC/guide).
```

- [ ] **Step 5: Version bump** — follow the `cutting-a-release` skill. Bump the **minor** version (new feature) in `bin/_pkg/__init__.py` and `.claude-plugin/plugin.json`. Read the current value first; set both to the same new `X.(Y+1).0`. Replace `vX.Y.0` in the CHANGELOG section with the chosen number.

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest test/ -q && bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add README.md SPEC.md docs/queue-guide.md CHANGELOG.md bin/_pkg/__init__.py .claude-plugin/plugin.json
git commit -m "docs(queue): overlay resource docs + experimental labeling; bump version"
```

---

## Self-review notes (addressed)

- **transition_guard granularity:** the spec §5 says "dirty on overlaid paths"; the implementation reuses `exclusive.transition_guard(root)`, which refuses on **any** uncommitted change in root. This is stricter and simpler, and safe (a clean starting root guarantees restore correctness). Acceptable v1 behavior; noted here so it is not mistaken for a bug.
- **Manifest keying:** a single `overlay.manifest` per resource queue-dir is safe because the resource is a mutex (one holder runs acquire/release at a time). A SIGKILL that skips `restore` leaks overlaid files; the next overlay's `transition_guard` then refuses on the now-dirty root, surfacing the problem rather than silently double-overlaying.
- **Guard exe is `magento`** (basename of `bin/magento`), per `guard_match.matches` using `os.path.basename`. `php bin/magento …` is an accepted blind spot, consistent with the documented make/npm wrappers.
- **Dirty-root refusal is tested** in Task 3 (`test_queue_overlay_in_refuses_dirty_root`): the safety property has a test that fails if the guard is dropped, not just the implementation.
- **Spec coverage:** §1 resource model → Task 4; §2 helper → Tasks 2–3; §3 engine env → Task 1; §4 guard+awareness → Tasks 4–5; §5 safety/limits → Tasks 2–3 (dirty-root refusal w/ test, copies-in-only) + self-review; §6 experimental → Tasks 5–7; §7 testing/docs → every task's tests + Task 7.
