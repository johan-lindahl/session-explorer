# Launch-failure Recovery & Persist-by-default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make worktree-launch failures visible and recoverable, and make tmux-hosted sessions survive every explorer exit except an explicit "shut down all".

**Architecture:** Part 2 reverses "Option C" by deleting the `client-detached` kill hook and the persist-flag machinery, leaning on the existing `new-session -A` reattach. Part 1 redirects a new session's stderr to a per-sid file, checks liveness ~1.5 s after launch, and on death surfaces+logs the captured error and stamps the stub; a transcript-less stub is started fresh (`--session-id`) instead of resumed.

**Tech Stack:** Python 3.11+, vendored Textual, pytest/pytest-asyncio, a dedicated `-L session-explorer` tmux server. Tests: `python3 -m pytest test/ -q`.

Design: `docs/superpowers/specs/2026-06-08-launch-failure-recovery-and-persist-design.md`.

---

## File Structure

- `bin/_pkg/tmux.py` — drop `client-detached` hook + persist helpers (Part 2); add `err_path` redirect to `build_new_session_window`/`start_new_session_window` (Part 1A).
- `bin/_pkg/cli.py` — stop creating/clearing the persist flag (Part 2).
- `bin/_pkg/uninstall.py` — keep `.session-explorer.tmux-persist` only as leftover-cleanup (Part 2).
- `bin/_pkg/index.py` — `set_launch_error` + clear it in `record_session` (Part 1B).
- `bin/_pkg/tui.py` — `action_quit` background branch (Part 2); module-level launch helpers, `_do_new_session` liveness wiring, `_check_launch`, `action_resume` stub routing + `_start_stub_fresh`, preview `last_launch_error` line (Part 1).
- `SPEC.md` / `CLAUDE.md` / `README.md` / `CHANGELOG.md` / `__init__.py` / `.claude-plugin/plugin.json` — docs + release (final task).

---

## PART 2 — Persist by default

### Task 1: Drop the kill hook and persist-flag helpers from tmux.py

**Files:**
- Modify: `bin/_pkg/tmux.py` (`build_config` ~169-208; persist helpers ~211-225)
- Test: `test/test_tmux.py`

- [ ] **Step 1: Update the build_config tests to the new contract**

In `test/test_tmux.py`, replace `test_build_config_contains_core_settings`, `test_build_config_respects_custom_keys`, and delete `test_persist_flag_set_clear_check`:

```python
def test_build_config_contains_core_settings():
    conf = tmux.build_config()
    assert "set -g mouse on" in conf
    assert "set -g status on" in conf
    assert "remain-on-exit" not in conf
    assert "bind -n F9 select-pane -t :.+" in conf
    assert "bind -n F12 resize-pane -Z" in conf
    assert 'window-status-format ""' in conf
    assert 'window-status-current-format ""' in conf
    assert "F9 ⇄ switch · F12 ⤢ full" in conf
    # Persist-by-default: detaching the client must NOT kill the server.
    assert "client-detached" not in conf
    assert "kill-server" not in conf


def test_build_config_respects_custom_keys():
    conf = tmux.build_config(switch_key="C-g", zoom_key="C-f")
    assert "bind -n C-g select-pane -t :.+" in conf
    assert "bind -n C-f resize-pane -Z" in conf
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: FAIL — `build_config()` still requires `persist_flag_path`; persist-helper test still references removed names later.

- [ ] **Step 3: Rewrite build_config and remove the persist helpers**

In `bin/_pkg/tmux.py`, replace the `build_config` signature/body so it drops `persist_flag_path` and the `detach_hook`:

```python
def build_config(*, switch_key: str = "F9",
                 zoom_key: str = "F12", socket: str = SOCKET) -> str:
    """tmux config for the dedicated server. Self-contained; never touches the
    user's ~/.tmux.conf. The split-pane layout: the explorer is the left pane
    and the active claude session docks as a right pane. `switch_key` flips
    focus; `zoom_key` toggles fullscreen.

    Persist-by-default: there is NO client-detached hook. Detaching the client
    by any means (red-button/Cmd-W, crash, or the deliberate `x → b`) leaves the
    server — background sessions and the detached explorer — running. Only an
    explicit `x → s` ("shut down all") calls kill-server. The next `/open`
    reattaches via `new-session -A`.
    """
    hint = (f"#[fg=black,bg=green] {switch_key} ⇄ switch "
            f"· {zoom_key} ⤢ full #[default]")
    return "\n".join([
        "set -g mouse on",
        "set -g status on",
        'set -g status-left ""',
        "set -g status-left-length 40",
        'set -g window-status-format ""',
        'set -g window-status-current-format ""',
        f'set -g status-right "{hint}"',
        "set -g status-right-length 40",
        f"bind -n {switch_key} select-pane -t :.+",
        f"bind -n {zoom_key} resize-pane -Z",
        "",
    ])
```

Delete the three persist-flag helpers entirely (the `# --- persist-flag helpers ---` block: `set_persist_flag`, `clear_persist_flag`, `persist_flag_set`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): persist by default — drop client-detached kill hook + persist flag

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Update persist-flag callers (cli.py, tui.py, uninstall.py)

**Files:**
- Modify: `bin/_pkg/cli.py:272-282` (config gen), `bin/_pkg/tui.py:2239-2244` (`action_quit` background), `bin/_pkg/uninstall.py`
- Test: `test/test_cli.py` (delete `test_launch_clears_stale_persist_flag`)

- [ ] **Step 1: Update/delete the cli persist test**

In `test/test_cli.py`, delete `test_launch_clears_stale_persist_flag` (it asserts removed behavior). Add a replacement that the generated config has no kill hook:

```python
def test_launch_writes_config_without_kill_hook(tmp_path, monkeypatch):
    import _pkg.tmux as tmux
    conf = tmux.build_config()
    assert "client-detached" not in conf
    assert "kill-server" not in conf
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test/test_cli.py -q`
Expected: FAIL — `test_launch_clears_stale_persist_flag` references removed `persist` behavior / `clear_persist_flag`.

- [ ] **Step 3: Update cli.py config generation**

In `bin/_pkg/cli.py`, replace the tmux config block (currently ~272-282) with:

```python
    if _tmux.available() and _tmux.meets_floor(_tmux.detected_version()):
        claude_dir = os.path.expanduser("~/.claude")
        os.makedirs(claude_dir, exist_ok=True)   # may not exist yet (CI / first run)
        conf = os.path.join(claude_dir, ".session-explorer.tmux.conf")
        with open(conf, "w") as f:
            f.write(_tmux.build_config())
        target = _launcher.wrap_in_tmux(target, config_path=conf)
```

- [ ] **Step 4: Update tui.py action_quit background branch**

In `bin/_pkg/tui.py`, replace the `background` branch of `action_quit` (currently ~2239-2244):

```python
            elif choice == "background":
                # Persist-by-default: detaching leaves the server running; the
                # next /open reattaches. (Equivalent to an abrupt close now.)
                _tmux.set_status_left("")
                _tmux.detach_client()
```

- [ ] **Step 5: Update uninstall.py comment (keep the marker for leftover cleanup)**

In `bin/_pkg/uninstall.py`, keep `".session-explorer.tmux-persist"` in the markers tuple (it cleans stale flag files left by pre-1.15 installs) but retag the comment:

```python
    # tmux interaction-layer artifacts.
    ".session-explorer.tmux.conf",
    ".session-explorer.tmux-persist",  # legacy (pre-1.15): persist mechanism removed; clean leftovers
    ".session-explorer.tmux-declined",
```

- [ ] **Step 6: Run the suites to verify pass**

Run: `python3 -m pytest test/test_cli.py test/test_uninstall.py test/test_tui.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/cli.py bin/_pkg/tui.py bin/_pkg/uninstall.py test/test_cli.py
git commit -m "feat: persist-by-default — stop creating the persist flag; x→b just detaches

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## PART 1 — Launch-failure visibility + stub recovery

### Task 3: Capture new-session stderr via an err_path redirect

**Files:**
- Modify: `bin/_pkg/tmux.py` (`build_new_session_window` ~54-76, `start_new_session_window` ~251-258)
- Test: `test/test_tmux.py`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_tmux.py`:

```python
def test_build_new_session_window_redirects_stderr_when_err_path():
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature",
                                         err_path="/tmp/se-launch-sid-9.err")
    assert argv[-1] == (
        "exec claude --session-id sid-9 -n feature 2>/tmp/se-launch-sid-9.err")


def test_build_new_session_window_no_redirect_without_err_path():
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature")
    assert argv[-1] == "exec claude --session-id sid-9 -n feature"
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test/test_tmux.py -k redirect -q`
Expected: FAIL — `build_new_session_window()` got an unexpected keyword `err_path`.

- [ ] **Step 3: Add err_path to the builder and the start wrapper**

In `bin/_pkg/tmux.py`, change `build_new_session_window` to accept `err_path` and append a (quoted-target) redirect AFTER `shlex.join` (operators must stay unquoted):

```python
def build_new_session_window(sid: str, cwd: str, name: str,
                             worktree: "str | None" = None,
                             err_path: "str | None" = None) -> List[str]:
    """... (existing docstring) ...

    When `err_path` is given, claude's stderr is redirected to that file so a
    startup failure (e.g. `git worktree add` collision under `-w`) is captured
    even though the window closes when claude exits. The redirect is appended
    after shlex.join so the `2>` operator is not quoted; the path is quoted.
    """
    inner = ["exec", "claude", "--session-id", sid]
    if name:
        inner += ["-n", name]
    if worktree is not None:
        inner.append("-w")
        if worktree:
            inner.append(worktree)
    cmd = shlex.join(inner)
    if err_path:
        cmd += f" 2>{shlex.quote(err_path)}"
    return build_base() + [
        "new-window", "-d", "-n", sid, "-c", cwd, cmd]
```

And thread it through `start_new_session_window`:

```python
def start_new_session_window(sid: str, cwd: str, name: str,
                             worktree: "str | None" = None,
                             label: "str | None" = None,
                             err_path: "str | None" = None) -> int:
    """Start a fresh session window; see build_new_session_window for the
    worktree tri-state and the err_path stderr-capture semantics."""
    rc = _call(build_new_session_window(sid, cwd, name, worktree, err_path))
    if label:
        _call(build_set_label(sid, label))
    return rc
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_tmux.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tmux.py test/test_tmux.py
git commit -m "feat(tmux): capture new-session stderr via err_path redirect

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: index.set_launch_error + clear on successful record

**Files:**
- Modify: `bin/_pkg/index.py` (add `set_launch_error`; clear in `record_session`)
- Test: `test/test_index.py`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_index.py`:

```python
def test_set_launch_error_stamps_row(tmp_path):
    idx_path = str(tmp_path / "index.json")
    index.seed_new_session(idx_path, "S9", "feature/x", "/Users/jl/proj/foo")
    index.set_launch_error(idx_path, "S9", "Error creating worktree: already exists")
    row = index.load(idx_path)["sessions"]["S9"]
    assert row["last_launch_error"] == "Error creating worktree: already exists"


def test_record_session_clears_launch_error(tmp_path):
    idx_path = str(tmp_path / "index.json")
    index.seed_new_session(idx_path, "S9", "feature/x", str(tmp_path))
    index.set_launch_error(idx_path, "S9", "boom")
    transcript = tmp_path / "S9.jsonl"
    transcript.write_text('{"type":"custom-title","customTitle":"feature/x","sessionId":"S9"}\n')
    index.record_session(idx_path, "S9", str(transcript), str(tmp_path), skip_git=True)
    row = index.load(idx_path)["sessions"]["S9"]
    assert "last_launch_error" not in row
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test/test_index.py -k launch_error -q`
Expected: FAIL — `index.set_launch_error` does not exist.

- [ ] **Step 3: Implement set_launch_error and the clear**

In `bin/_pkg/index.py`, add (near `seed_new_session`):

```python
def set_launch_error(index_path: str, session_id: str, error: str) -> dict:
    """Record why a session's launch failed (e.g. `claude -w` could not create
    its worktree). Shown in the preview so an unopenable stub explains itself;
    cleared by record_session once a transcript appears (a successful start)."""
    def mutator(data: dict) -> dict:
        row = data["sessions"].get(session_id)
        if row is not None:
            row["last_launch_error"] = error
        return data
    return mutate(index_path, mutator)
```

In `record_session`, the transcript now exists → the prior failure is stale. After building `new_entry` (just before `data["sessions"][session_id] = new_entry`), drop the field:

```python
        new_entry.pop("last_launch_error", None)
        data["sessions"][session_id] = new_entry
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_index.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/index.py test/test_index.py
git commit -m "feat(index): set_launch_error; clear it once a transcript appears

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Preview renders last_launch_error

**Files:**
- Modify: `bin/_pkg/tui.py` (preview builder, the `field(...)` block ~300-305)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Find the preview builder name first: `grep -n "def .*preview_text\|def _preview_body\|def preview_lines" bin/_pkg/tui.py` (the function containing the `field("Session", ...)` line). Call it `_preview_text` below. Add to `test/test_tui.py`:

```python
def test_preview_shows_last_launch_error():
    from _pkg.tui import _preview_text
    s = {"sid": "S9", "name_cached": "feature/x", "project_path": "/p",
         "last_launch_error": "Error creating worktree: already exists"}
    text = _preview_text(s)
    assert "Launch" in text
    assert "Error creating worktree: already exists" in text


def test_preview_no_launch_line_when_clean():
    from _pkg.tui import _preview_text
    s = {"sid": "S9", "name_cached": "feature/x", "project_path": "/p"}
    assert "failed:" not in _preview_text(s)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test/test_tui.py -k launch_error -q`
Expected: FAIL — no "Launch" line in the preview.

- [ ] **Step 3: Add the conditional block**

In the preview builder, immediately after the `field("Session", sid or "—")` line, insert:

```python
    ]
    if s.get("last_launch_error"):
        lines += ["", "[b]Launch[/]", "failed: " + s["last_launch_error"]]
    lines += [
        "",
        "[b]Notes[/]",
```

(i.e. close the existing `lines = [...]`/`lines += [...]` list after the `Session` field, append the conditional block, then continue with the `Notes` block. Keep the existing fields intact — only split the list to inject the conditional.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_tui.py -k launch_error -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): preview shows last_launch_error so a broken stub explains itself

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: action_resume starts a transcript-less stub fresh

**Files:**
- Modify: `bin/_pkg/tui.py` (`action_resume` ~1673; add `_start_stub_fresh`)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_tui.py` (async; mirrors `test_tui_queue.py` style). It seeds a transcript-less stub, captures `_do_new_session` args, and asserts Enter routes there reusing the sid:

```python
@pytest.mark.asyncio
async def test_resume_on_stub_starts_fresh(tmp_path):
    from _pkg import index
    from _pkg.tui import SessionExplorerApp
    idx = str(tmp_path / "index.json")
    index.seed_new_session(idx, "S9", "feature/x", str(tmp_path))  # no transcript_path
    app = SessionExplorerApp(index_path=idx)
    captured = {}
    async with app.run_test() as pilot:
        await pilot.pause()
        app._tmux_enabled = True
        app._do_new_session = lambda sid, cwd, name, wt, label: captured.update(
            sid=sid, name=name, wt=wt)
        # Put the cursor on the stub row.
        node = next(n for n in app._tree.walk_nodes()
                    if n.data and n.data.get("sid") == "S9")
        app._tree.select_node(node); app._tree.cursor_node = node
        await pilot.pause()
        app.action_resume()
        await pilot.pause()
    assert captured.get("sid") == "S9"          # reuses the stub's id
    assert captured.get("name") == "feature/x"  # not claude --resume
    assert captured.get("wt") is None           # tmp_path is not a shared-resource root
```

If `walk_nodes`/`select_node`/`cursor_node` differ from the codebase, adapt to the same helpers other `test_tui.py` cursor tests use (grep `cursor_node` in `test/test_tui*.py`).

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test/test_tui.py -k resume_on_stub -q`
Expected: FAIL — `action_resume` calls `_exit_to_resume`/`_dock` (resume path), so `_do_new_session` is never invoked.

- [ ] **Step 3: Add the stub branch + _start_stub_fresh**

In `bin/_pkg/tui.py`, at the top of `action_resume` (right after resolving `sid`), add the stub short-circuit:

```python
    def action_resume(self) -> None:
        node = self._tree.cursor_node
        if not node or not node.data or "sid" not in node.data:
            self.bell()
            return
        data = node.data
        sid = data["sid"]
        # A transcript-less stub has no conversation to --resume (a new session
        # whose first turn never happened, e.g. `claude -w` failed at startup).
        # Start it fresh, reusing its seeded id + name, instead.
        if not data.get("transcript_path"):
            self._start_stub_fresh(sid, data)
            return
        project_path = data.get("project_path")
        # ... existing body unchanged from here ...
```

Add the helper (near `_do_new_session`):

```python
    def _start_stub_fresh(self, sid: str, data: dict) -> None:
        """Launch a transcript-less stub as a fresh session, reusing its sid and
        name. Worktree defaults exactly as `c` does for the project: a
        shared-resource root → `-w <slug>`, else no worktree."""
        from . import project_id as _pid, queue_config as _qc
        name = data.get("name_cached") or ""
        cwd = data.get("project_path") or os.path.expanduser("~")
        pid = _pid.project_id(cwd)
        root_is_shared = bool(pid and any(
            r.get("kind") == "root-dir"
            for r in _qc.list_resources(self._queue_config_path(), pid).values()))
        slug = worktree_slug(name)
        worktree = slug if (root_is_shared and slug) else None
        _, display = split_path(name)
        label = display or sid[:8]
        if not self._tmux_enabled:
            self._new_session_argv = _new_session_argv(sid, name, worktree)
            self._new_session_cwd = cwd
            self.exit()
            return
        self._do_new_session(sid, cwd, name, worktree, label)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test/test_tui.py -k resume_on_stub -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): Enter on a transcript-less stub starts it fresh (not --resume)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Detect launch death, surface + log + stamp the stub

**Files:**
- Modify: `bin/_pkg/tui.py` (module-level helpers; `_do_new_session` ~2022; add `_check_launch`; add `LAUNCH_CHECK_DELAY` constant)
- Test: `test/test_tui.py`

- [ ] **Step 1: Write the failing tests for the pure helpers**

Add to `test/test_tui.py`:

```python
def test_launch_err_path_is_sid_specific():
    from _pkg.tui import _launch_err_path
    p = _launch_err_path("abc-123")
    assert "abc-123" in p and p.endswith(".err")


def test_summarize_launch_error_prefers_worktree_line():
    from _pkg.tui import _summarize_launch_error
    raw = ("Preparing worktree (new branch 'worktree-x')\n"
           "Error creating worktree: worktree \"x\" already exists\n")
    out = _summarize_launch_error(raw)
    assert "already exists" in out
    assert out.startswith("Error creating worktree")


def test_summarize_launch_error_blank_returns_empty():
    from _pkg.tui import _summarize_launch_error
    assert _summarize_launch_error("") == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test/test_tui.py -k "launch_err_path or summarize_launch_error" -q`
Expected: FAIL — helpers undefined.

- [ ] **Step 3: Add the module-level helpers + constant**

In `bin/_pkg/tui.py` (module level, near other constants/helpers):

```python
import tempfile

LAUNCH_CHECK_DELAY = 1.5  # seconds after a new-session launch to verify it stuck


def _launch_err_path(sid: str) -> str:
    """Per-sid temp file capturing a new session's startup stderr."""
    return os.path.join(tempfile.gettempdir(), f"session-explorer-launch-{sid}.err")


def _summarize_launch_error(raw: str) -> str:
    """One-line summary of captured startup stderr for a toast/preview. Prefers
    the line that names the failure ('Error creating worktree…'); else the first
    non-empty line. Truncated. Blank in → blank out."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    chosen = next((ln for ln in lines if "Error creating worktree" in ln), None)
    chosen = chosen or next((ln for ln in lines if "worktree" in ln.lower()
                             or ln.lower().startswith("error")), None)
    chosen = chosen or lines[0]
    return chosen[:200]


def _log_line(msg: str) -> None:
    """Best-effort append to ~/.claude/session-explorer.log. Never raises."""
    try:
        from datetime import datetime, timezone
        log = os.path.expanduser("~/.claude/session-explorer.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n")
    except Exception:
        pass
```

- [ ] **Step 4: Run the helper tests to verify pass**

Run: `python3 -m pytest test/test_tui.py -k "launch_err_path or summarize_launch_error" -q`
Expected: PASS.

- [ ] **Step 5: Write the failing test for _check_launch (dead path)**

Add to `test/test_tui.py`:

```python
@pytest.mark.asyncio
async def test_check_launch_dead_window_surfaces_and_stamps(tmp_path, monkeypatch):
    from _pkg import index, tui as tuimod
    from _pkg.tui import SessionExplorerApp, _launch_err_path
    idx = str(tmp_path / "index.json")
    index.seed_new_session(idx, "S9", "feature/x", str(tmp_path))
    err = _launch_err_path("S9")
    with open(err, "w") as f:
        f.write("Error creating worktree: worktree \"x\" already exists\n")
    monkeypatch.setattr(tuimod._tmux, "session_windows", lambda: [])  # dead
    app = SessionExplorerApp(index_path=idx)
    notes = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app._docked_sid = None
        app._live_states = {}
        app.notify = lambda *a, **k: notes.append((a, k))
        app._check_launch("S9", err, "feature/x")
        await pilot.pause()
    row = index.load(idx)["sessions"]["S9"]
    assert "already exists" in row["last_launch_error"]
    assert notes, "expected a warning toast"
    assert not os.path.exists(err)  # errfile cleaned up
```

- [ ] **Step 6: Run to verify failure**

Run: `python3 -m pytest test/test_tui.py -k check_launch_dead -q`
Expected: FAIL — `_check_launch` undefined.

- [ ] **Step 7: Add _check_launch and wire it into _do_new_session**

In `bin/_pkg/tui.py`, add the method:

```python
    def _check_launch(self, sid: str, err_path: str, name: str) -> None:
        """~1.5 s after a new-session launch, verify it actually stuck. If the
        window died at startup (claude exited — e.g. `claude -w` failed), read
        the captured stderr, surface it, log it, and stamp the stub so the row
        explains itself. Alive → just clean up the errfile."""
        alive = (sid in _tmux.session_windows()
                 or sid == self._docked_sid
                 or sid in self._live_states)
        if not alive:
            raw = ""
            try:
                with open(err_path, encoding="utf-8", errors="replace") as f:
                    raw = f.read()
            except OSError:
                pass
            msg = _summarize_launch_error(raw) or "Session failed to start."
            _log_line(f"launch failed sid={sid} name={name!r}: {raw!r}")
            self.notify(f"Couldn’t start “{name or sid[:8]}”: {msg}",
                        severity="warning", timeout=10)
            from . import index as _index
            _index.set_launch_error(self._index_path, sid, msg)
            self._populate()
        try:
            os.remove(err_path)
        except OSError:
            pass
```

Update `_do_new_session` to pass `err_path` and schedule the check:

```python
    def _do_new_session(self, sid: str, cwd: str, name: str,
                        worktree: "str | None", label: "str | None") -> None:
        """Start a fresh claude session as a background window and dock it as
        the right pane, swapping out whatever was docked. A short-delay liveness
        check surfaces a startup failure (e.g. `claude -w` could not create its
        worktree) instead of letting it vanish into a closed window."""
        self._undock_current()
        err_path = _launch_err_path(sid)
        _tmux.start_new_session_window(sid, cwd, name, worktree, label,
                                       err_path=err_path)
        self._join_docked(sid)
        self._populate()
        self._poll_live()
        self.set_timer(LAUNCH_CHECK_DELAY,
                       lambda: self._check_launch(sid, err_path, name))
```

- [ ] **Step 8: Run to verify pass**

Run: `python3 -m pytest test/test_tui.py -k check_launch_dead -q`
Expected: PASS.

- [ ] **Step 9: Run the full suite**

Run: `python3 -m pytest test/ -q`
Expected: PASS (all). If a pre-existing test asserts the old `_do_new_session` signature/behavior, adapt it to the new signature.

- [ ] **Step 10: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "feat(tui): detect new-session launch death; surface + log + stamp the stub

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Docs, spec, changelog, release (v1.15.0)

**Files:**
- Modify: `SPEC.md` (Option C decision; new launch-recovery decision), `CLAUDE.md` (Option C bullet), `README.md` (status/keys if changed), `CHANGELOG.md`, `bin/_pkg/__init__.py`, `.claude-plugin/plugin.json`

- [ ] **Step 1: Rewrite the "Option C" decision**

In `SPEC.md` and the `CLAUDE.md` "Abrupt window-close…" bullet, replace the Option-C text with the persist-by-default rule:

> **Sessions persist by default.** The dedicated tmux server has no
> `client-detached` kill hook, so detaching the client by any means
> (red-button/Cmd-W, crash, or the deliberate `x → b`) leaves the server and
> all background sessions running; the next `/open` reattaches via
> `new-session -A`. Only an explicit `x → s` ("shut down all") kills the
> server. The persist-flag mechanism was removed.

- [ ] **Step 2: Add the launch-recovery decision to SPEC.md**

Add a load-bearing bullet near the new-session / worktree decisions:

> **A new session's startup failure is captured, not swallowed.** New-session
> windows redirect claude's stderr to a per-sid errfile; ~1.5 s after launch
> the explorer verifies the session stuck, and on death surfaces+logs the
> captured error and stamps the row's `last_launch_error` (shown in the
> preview). A transcript-less stub is **started fresh** (`--session-id`) on
> Enter, never `--resume`d.

- [ ] **Step 3: Add a CHANGELOG entry**

Add a `## v1.15.0` section to `CHANGELOG.md`:

```markdown
## v1.15.0

- Sessions now persist across every explorer exit except an explicit
  "shut down all" (`x → s`). Closing the window (red-button/Cmd-W) or a crash
  no longer kills your running sessions — the next `/open` reattaches.
- New-session launch failures (e.g. `claude -w` unable to create its worktree)
  are surfaced and logged instead of vanishing into a closed pane.
- A named session whose first turn never happened (no transcript) now starts
  fresh on Enter instead of refusing to open; if its last launch failed, the
  preview shows why.
```

- [ ] **Step 4: Bump the version (follow the cutting-a-release skill)**

Invoke the `cutting-a-release` skill and follow it as the authoritative checklist: bump `bin/_pkg/__init__.py` and `.claude-plugin/plugin.json` to `1.15.0`, update README/SPEC status lines (and help-screen keybindings only if they changed — they did not here), confirm the CHANGELOG section, then create the release.

- [ ] **Step 5: Final verification**

Run: `python3 -m pytest test/ -q`
Then: `bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: all green.

- [ ] **Step 6: Commit + push + PR**

```bash
git add -A
git commit -m "docs+release: persist-by-default & launch recovery — v1.15.0

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin launch-failure-recovery-and-persist
gh pr create --base main --title "Persist-by-default & worktree-launch recovery (v1.15.0)" --body "<summary + test evidence>"
```

---

## Self-Review

**Spec coverage:**
- 1A capture → Task 3 (err_path) + Task 7 (liveness/surface/log). ✓
- 1B stub-fresh → Task 6; failed-row stamp → Task 4 (index) + Task 5 (preview) + Task 7 (set on death). ✓
- Part 2 persist → Task 1 (hook/helpers) + Task 2 (callers); spec rewrite → Task 8. ✓
- Testing section of spec → tests in Tasks 1,3,4,5,6,7; bats in Task 8. ✓

**Type/name consistency:** `err_path` param name is consistent across `build_new_session_window`/`start_new_session_window`/`_launch_err_path`/`_do_new_session`/`_check_launch`. `last_launch_error` key consistent across `index.set_launch_error`, `record_session` clear, preview, `_check_launch`. `_summarize_launch_error`/`_log_line`/`LAUNCH_CHECK_DELAY` referenced only where defined. ✓

**Placeholders:** `_preview_text` in Task 5 is a real lookup (grep instruction given) — resolve it to the actual function name before writing the test. No other placeholders.
