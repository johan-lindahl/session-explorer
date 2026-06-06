# Shared-resource queue — Phase 3 (awareness & enforcement) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every agent in an opted-in project *aware* the project shares singleton resources, and *nudge* guarded Bash commands toward `queue-run`, via a `SessionStart` `additionalContext` injection and a new fail-open `PreToolUse` Bash hook — reusing the Phase-1 guard/config/identity primitives.

**Architecture:** All decision logic lives in one new pure module, `bin/_pkg/queue_awareness.py`, which reuses `project_id`, `queue_config`, and `guard_match`. Two thin CLI subcommands expose it: `queue-context` (prints SessionStart `additionalContext` JSON for an opted-in project) and `queue-guard` (reads a `PreToolUse` payload on stdin, prints a `deny`+redirect for guarded commands). The already-registered `hooks/session-start.sh` gains a branch calling `queue-context`; a new thin `hooks/pre-tool-use.sh` pipes stdin to `queue-guard`. Both hooks fail open and never block. The `PreToolUse` hook is *new wiring*, so it is mirrored across `plugin.json` (marketplace), `install.sh` (plain), and `uninstall.py` (teardown). Cooperative guidance ships as the injected text plus a copy-paste `CLAUDE.md` snippet in `docs/queue-guide.md`.

**Tech Stack:** Python 3.11+ (stdlib only — `argparse`, `json`, `shlex`, `os`), Bash hooks, pytest + pytest-asyncio, bats. No new runtime dependency.

---

## Spec reference

Implements **§8 (Agent awareness & enforcement — v1 = command-guard + detection)** of `docs/superpowers/specs/2026-06-05-shared-root-test-queue-design.md`, plus its "Install surfaces" subsection. Phases 1 (queue core + CLI) and 2 (TUI) are already shipped (v1.13.0). This is the third independently-shippable build-order item.

**Load-bearing constraints carried from the spec:**

- **Fail open, always.** A false deny is worse than a missed guard. Any parse/IO/internal error in either hook → tool proceeds, session starts. (`guard_match.matches` already fails open; the CLI handlers wrap everything in `try/except` → exit 0, no output.)
- **No new manifest event for SessionStart.** The `additionalContext` branch lives inside the *already-registered* `hooks/session-start.sh` — no new registration.
- **`PreToolUse` is new wiring** — register in `plugin.json` **and** `install.sh`, tear down in `uninstall.py`, and add to the `cutting-a-release` checklist so it can't land marketplace-only.
- **Guard matching is `{exe, sub}` on parsed argv, never substring regex** — reuse `guard_match.matches`, do not reimplement.
- **Opted-in iff ≥1 resource** — `queue_config.is_opted_in` / `list_resources`.
- **Accepted v1 blind spot:** wrappers that hide a guarded command (`make`, `npm run`) slip past. Mitigation is the awareness injection + cooperative text, not the hook. Don't try to defeat wrappers.

## File structure

| File | New/Modify | Responsibility |
|---|---|---|
| `bin/_pkg/queue_awareness.py` | **Create** | Pure logic: `session_context()` (additionalContext text) + `guard_reason()` (redirect text). Reuses `project_id`/`queue_config`/`guard_match`. No I/O beyond config read, no Textual, no argparse. |
| `bin/_pkg/cli.py` | Modify | Add `queue-context` + `queue-guard` subparsers, `_cmd_queue_context`/`_cmd_queue_guard` handlers, and dispatch lines. |
| `hooks/session-start.sh` | Modify | Route `index --record` stdout to the log; add a final branch that prints `queue-context` output (the hook's only stdout). |
| `hooks/pre-tool-use.sh` | **Create** | Thin wrapper: resolve CLI, pipe stdin → `queue-guard`. Fails open, exits 0. |
| `.claude-plugin/plugin.json` | Modify | Register `PreToolUse` (matcher `Bash`) → `pre-tool-use.sh`. |
| `install.sh` | Modify | Register `PreToolUse` in settings.json; add `pre-tool-use.sh` to markers + chmod. |
| `bin/_pkg/uninstall.py` | Modify | Add `pre-tool-use.sh` to `_HOOK_MARKERS`, `PreToolUse` to `_HOOK_EVENTS`. |
| `docs/queue-guide.md` | Modify | Append "Cooperating as an agent" section + copy-paste `CLAUDE.md` snippet. |
| `SPEC.md`, `CLAUDE.md` | Modify | Mark §8 implemented; record the new hook as a load-bearing decision. |
| `.claude/skills/cutting-a-release/SKILL.md` | Modify | Add the `PreToolUse` mirror + queue-guide cooperative snippet to the checklist. |
| `test/test_queue_awareness.py` | **Create** | Unit tests for `session_context`/`guard_reason`. |
| `test/test_cli.py` | Modify | Subprocess tests for `queue-context`/`queue-guard`. |
| `test/hook.bats` | Modify | `pre-tool-use.sh` exit-0 + deny-JSON tests. |
| `test/install.bats`, `test/uninstall.bats` | Modify | `PreToolUse` register/teardown assertions (plain-install `settings.json`). |
| `test/test_plugin_manifest.py` | **Create** | Pure-JSON validation of the marketplace `plugin.json` `PreToolUse` wiring. |

---

## Task 1: `queue_awareness.py` — pure decision logic

**Files:**
- Create: `bin/_pkg/queue_awareness.py`
- Test: `test/test_queue_awareness.py`

This module is the single source of truth for *what text* the SessionStart injection and the PreToolUse redirect say. It reuses `project_id.project_id`, `queue_config.list_resources`, and `guard_match.matches`. Pure (config read only), no argparse, no Textual.

- [ ] **Step 1: Write the failing test**

Create `test/test_queue_awareness.py`:

```python
"""Unit tests for the Phase-3 awareness/guard logic (queue_awareness)."""
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
        resource={
            "kind": "root-dir", "path": str(repo),
            "guard": [{"exe": "docker", "sub": ["compose", "up"]}],
            "run_in": "root", "acquire": "sync", "release": "none",
            "sync": {"delete": True, "exclude": ["/.git"], "protect": ["/.git"]},
        })
    return pid


def test_session_context_none_when_not_opted_in(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    assert qa.session_context(str(cfg), str(repo)) is None


def test_session_context_none_outside_git(tmp_path):
    cfg = tmp_path / "queue-config.json"
    assert qa.session_context(str(cfg), str(tmp_path)) is None


def test_session_context_lists_resources_and_cooperation(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    text = qa.session_context(str(cfg), str(repo))
    assert text is not None
    assert "root" in text and "root-dir" in text
    assert "docker compose up" in text            # rendered guard label
    assert "queue-run" in text                     # the lever
    assert "queue-status" in text


def test_guard_reason_fires_on_guarded_command(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "docker compose up -d", str(repo))
    assert reason is not None
    assert "queue-run --resource root --" in reason
    assert "docker compose up -d" in reason


def test_guard_reason_silent_on_unguarded_command(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    assert qa.guard_reason(str(cfg), "docker ps", str(repo)) is None
    assert qa.guard_reason(str(cfg), "npm run setup", str(repo)) is None


def test_guard_reason_skips_already_wrapped_queue_run(tmp_path):
    # A properly-wrapped command is a single argv segment whose exe is
    # 'session-explorer' (docker sits after --), so the parsed-argv matcher
    # naturally does not fire - no substring check, no deny-loop.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    cmd = "session-explorer queue-run --resource root -- docker compose up"
    assert qa.guard_reason(str(cfg), cmd, str(repo)) is None


def test_guard_reason_no_substring_bypass(tmp_path):
    # 'queue-run' appears as a literal arg, but the second segment really does run
    # docker directly - parsed-argv matching must still fire (no substring escape).
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "echo queue-run && docker compose up -d",
                             str(repo))
    assert reason is not None
    assert "queue-run --resource root --" in reason


def test_guard_reason_wraps_compound_command_in_bash_c(tmp_path):
    # A compound command must be wrapped whole in `bash -c <quoted>` so the
    # operator runs INSIDE the lease. The broken form would re-embed the raw text
    # after `--`, letting the agent's outer shell split off the trailing segment.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "cd app && docker compose up -d", str(repo))
    assert reason is not None
    assert "-- bash -c " in reason
    # the whole compound is a single quoted arg, not split after `--`
    assert "-- cd app &&" not in reason
    assert "'cd app && docker compose up -d'" in reason


def test_guard_reason_wraps_newline_separated_command(tmp_path):
    # A newline is a shell command separator too: shlex folds it into one matchable
    # segment, but the agent's shell would split it. The whole thing must be wrapped
    # in bash -c so the trailing line can't run outside the lease.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "docker compose up -d\necho done", str(repo))
    assert reason is not None
    assert "-- bash -c " in reason
    assert "-- docker compose up -d\necho done" not in reason


def test_guard_reason_simple_command_unwrapped(tmp_path):
    # A single simple command (no shell operators) is suggested verbatim - no
    # noisy bash -c wrapper.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "docker compose up -d", str(repo))
    assert "queue-run --resource root -- docker compose up -d" in reason
    assert "bash -c" not in reason


def test_guard_reason_fails_open_on_guard_match_blind_spots(tmp_path):
    # guard_match intentionally returns NO match for command substitution,
    # backticks, heredocs, no-space operators, and wrapper bodies (bash -c). Those
    # commands are ALLOWED (fail open), never denied-and-wrapped — guard_reason
    # never even reaches _redirect_command for them. The SessionStart awareness
    # injection (not this hook) is the backstop for these inherited blind spots.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    for cmd in [
        "docker compose up $(echo -d)",     # command substitution
        "docker compose up `echo -d`",       # backticks
        "bash -c 'docker compose up -d'",    # wrapper body hides the command
        "docker compose up&&echo done",      # no-space operator: not lexed
    ]:
        assert qa.guard_reason(str(cfg), cmd, str(repo)) is None, cmd


def test_guard_reason_none_when_not_opted_in(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    assert qa.guard_reason(str(cfg), "docker compose up", str(repo)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_queue_awareness.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.queue_awareness'`.

- [ ] **Step 3: Write minimal implementation**

Create `bin/_pkg/queue_awareness.py`:

```python
"""Phase-3 awareness/guard text (spec section 8).

Two pure entry points, both reusing the Phase-1 primitives so guard semantics
stay single-sourced:

- `session_context(config_path, cwd)` -> the SessionStart `additionalContext`
  text for an opted-in project, or None (not a git repo / not opted in).
- `guard_reason(config_path, command, cwd)` -> a redirect message for a guarded
  Bash command, or None (allow it through).

No argparse, no Textual, no stdout. Callers (cli.py) wrap these in try/except
and fail open: a false deny is worse than a missed guard (spec section 8).
"""

from __future__ import annotations

import shlex

from . import guard_match as _gm
from . import project_id as _pid
from . import queue_config as _qc


def _guard_label(resource: dict) -> str:
    """Render a resource's {exe, sub} rules as 'docker compose up, cypress run'."""
    parts = []
    for rule in resource.get("guard") or []:
        toks = [rule.get("exe", "")] + list(rule.get("sub") or [])
        label = " ".join(t for t in toks if t)
        if label:
            parts.append(label)
    return ", ".join(parts)


def _render_context(resources: dict) -> str:
    lines = [
        "This project shares one or more singleton resources across its git "
        "worktrees, coordinated by session-explorer. Other Claude sessions may "
        "be using them right now.",
        "",
        "Declared shared resources:",
    ]
    for rid in sorted(resources):
        res = resources[rid]
        kind = res.get("kind", "?")
        guard = _guard_label(res)
        suffix = f" - guarded commands: {guard}" if guard else ""
        lines.append(f"  - {rid} ({kind}){suffix}")
    lines += [
        "",
        "Cooperate with the lease engine:",
        "  - Never start your own copy of a shared stack / server / database. It "
        "is already running and warm; a second copy collides on its fixed ports "
        "and paths.",
        "  - Run guarded commands through a lease: "
        "`session-explorer queue-run --resource <name> -- <command>`.",
        "  - If a resource is busy, queue-run waits in FIFO order. Don't busy-spin, "
        "force it, or work around it - report your queue position and wait.",
        "  - A `sync` lease overwrites the shared root with your worktree's files "
        "on acquire. Expect that; keep secrets / local-only files out of tracked "
        "paths.",
        "  - Inspect state anytime with `session-explorer queue-status`.",
    ]
    return "\n".join(lines)


def session_context(config_path: str, cwd: str) -> "str | None":
    pid = _pid.project_id(cwd)
    if not pid:
        return None
    resources = _qc.list_resources(config_path, pid)
    if not resources:
        return None
    return _render_context(resources)


# Shell control operators/separators that would let the agent's OUTER shell
# re-split a command after `--`, running part of it outside the lease. Includes a
# newline: shlex tokenizes `docker compose up\necho done` into one matchable
# segment, but the agent's shell treats the newline as a command separator, so an
# unwrapped redirect would run `echo done` outside the lease. Presence of any of
# these means we wrap the whole command in `bash -c <quoted>` so every separator
# runs INSIDE the lease. Erring toward wrapping is always safe (it only affects
# how the suggestion reads), so a crude substring scan is fine here.
# NOTE: in practice guard_match already declines to match commands containing
# `$(`, backticks, or heredocs (it fails open, so guard_reason returns None and
# this function is never reached for them). The `$(`/backtick entries here are
# therefore defensive belt-and-suspenders, not the live path.
_SHELL_OPS = ("&&", "||", ";", "|", "&", ">", "<", "$(", "`", "\n")


def _redirect_command(rid: str, command: str) -> str:
    """The exact `queue-run` invocation to suggest for `command`.

    A bare `queue-run --resource R -- <command>` only round-trips when the agent's
    shell won't re-split it. `cd app && docker compose up` re-embedded raw would
    run ONLY `cd app` under the lease and `docker compose up` outside it. So any
    command carrying a shell operator is wrapped whole in `bash -c <quoted>`,
    which keeps every operator inside the single leased process."""
    if any(op in command for op in _SHELL_OPS):
        return (f"session-explorer queue-run --resource {rid} -- "
                f"bash -c {shlex.quote(command)}")
    return f"session-explorer queue-run --resource {rid} -- {command}"


def guard_reason(config_path: str, command: str, cwd: str) -> "str | None":
    """Redirect text for a guarded command, or None to allow it through.

    An already-wrapped `session-explorer queue-run --resource R -- <guarded cmd>`
    is skipped for free by the parsed-argv matcher, with no substring check: the
    guarded executable sits after `--`, so it is never a segment-leading token, and
    `guard_match.matches` keys only on each simple command's leading exe + sub.
    A NAIVE `"queue-run" in command` substring check would be both redundant and a
    bypass (`echo queue-run && docker compose up` would slip through), so it is
    deliberately absent - matching stays purely on parsed argv (spec sections 2
    and 8)."""
    if not command:
        return None
    pid = _pid.project_id(cwd)
    if not pid:
        return None
    resources = _qc.list_resources(config_path, pid)
    for rid in sorted(resources):
        rules = resources[rid].get("guard") or []
        if _gm.matches(command, rules):
            return (
                f"This command uses '{rid}', a shared singleton resource for this "
                f"project that must be held under a lease so parallel worktrees "
                f"don't collide. Re-run it through queue-run:\n\n"
                f"    {_redirect_command(rid, command)}\n\n"
                f"queue-run takes the lease (waiting in FIFO order if it's busy), "
                f"runs your command, then releases it. Check "
                f"`session-explorer queue-status` for who holds it now."
            )
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_queue_awareness.py -q`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/queue_awareness.py test/test_queue_awareness.py
git commit -m "feat(queue): add queue_awareness - SessionStart context + guard-redirect text"
```

---

## Task 2: `queue-context` CLI subcommand

**Files:**
- Modify: `bin/_pkg/cli.py` (subparser after `queue-cancel` ~line 110; handler after `_cmd_queue_cancel` ~line 350; **early** dispatch right after `parse_args`, before the migration block ~line 356)
- Test: `test/test_cli.py`

Exposes `session_context` to the SessionStart hook as a fully-formed `hookSpecificOutput` JSON line. Fails open: any error → no output, exit 0.

- [ ] **Step 1: Write the failing test**

Add to `test/test_cli.py`. The file currently imports only `os`, `subprocess`, and `shutil` (test/test_cli.py:3), but the new tests call `json.loads(...)`, so **add `import json`** at the top first. (`_BIN` is the existing bin-path constant.)

```python
def _git_repo_with_root(tmp_path):
    """git-init a repo and write a queue config opting it in with a 'root' resource.

    Relies on the suite's conftest already putting bin/ on sys.path (the same way
    test_queue_awareness.py does `from _pkg import ...`)."""
    from _pkg import project_id, queue_config
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    cfg = tmp_path / "queue-config.json"
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        str(cfg), project_id=pid, display_path=str(repo), resource_id="root",
        resource={"kind": "root-dir", "path": str(repo),
                  "guard": [{"exe": "docker", "sub": ["compose", "up"]}],
                  "run_in": "root", "acquire": "sync", "release": "none",
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git"]}})
    return repo, cfg


def test_queue_context_emits_additional_context_when_opted_in(tmp_path):
    repo, cfg = _git_repo_with_root(tmp_path)
    result = subprocess.run(
        [_BIN, "queue-context", "--cwd", str(repo)],
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    assert "docker compose up" in out["additionalContext"]
    assert "queue-run" in out["additionalContext"]


def test_queue_context_silent_when_not_opted_in(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    cfg = tmp_path / "queue-config.json"
    result = subprocess.run(
        [_BIN, "queue-context", "--cwd", str(repo)],
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_cli.py -k queue_context -q`
Expected: FAIL — non-zero exit / `(not implemented) cmd=queue-context`.

- [ ] **Step 3: Add the subparser**

In `bin/_pkg/cli.py`, in `build_parser()`, immediately **after** the `queue-cancel` block (after the `qcancel.add_argument("--reason", ...)` line, before `uninstall_p = ...`):

```python
    qctx = sub.add_parser(
        "queue-context",
        help="Print SessionStart additionalContext for an opted-in project "
             "(used by the SessionStart hook). Silent + fail-open otherwise.")
    qctx.add_argument("--cwd", required=True,
                      help="Session cwd used to resolve the project.")
```

- [ ] **Step 4: Add the handler**

In `bin/_pkg/cli.py`, **after** `_cmd_queue_cancel` (after its `return 1` / closing, before `def main(`):

```python
def _cmd_queue_context(args) -> int:
    """Emit a SessionStart additionalContext JSON line for opted-in projects.
    Fails open: any error -> no output, exit 0 (never disrupt session start)."""
    import json as _json
    try:
        from . import queue_awareness as _qa
        text = _qa.session_context(_queue_config_path(), args.cwd)
        if text:
            print(_json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": text}}))
    except Exception:
        pass
    return 0
```

- [ ] **Step 5: Add an EARLY dispatch (before the global migrations)**

The hook subcommands run on the critical path — `queue-context` fires at every SessionStart, and `queue-guard` (Task 3) at **every PreToolUse Bash call across all sessions**. `main()` runs `_index.migrate_to_v2(...)` + `migrate_folder_store_keys(...)` *before* its normal dispatch (cli.py:357–375); those touch the index/folder stores the hooks don't need. So dispatch the hook subcommands **before** that migration block to keep them cheap and honor the "never block / cheap hook" constraint.

In `main()`, immediately **after** `args = parser.parse_args(argv)` and **before** `idx_path = _index_path()` / the `try: _index.migrate_to_v2(...)` block, insert:

```python
    # Hook subcommands are on the critical path (SessionStart, and every
    # PreToolUse Bash call). Keep them cheap: dispatch before the global index /
    # folder migrations below, which they don't need, so a Bash tool call never
    # pays migration overhead just to evaluate the guard.
    if args.cmd == "queue-context":
        return _cmd_queue_context(args)
```

Leave the existing later dispatch lines (`queue-run`/`queue-status`/`queue-cancel`/`tui`/…) untouched; do **not** add a second `queue-context` line further down.

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest test/test_cli.py -k queue_context -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(cli): add queue-context subcommand (SessionStart additionalContext)"
```

---

## Task 3: `queue-guard` CLI subcommand

**Files:**
- Modify: `bin/_pkg/cli.py` (subparser after `queue-context`; handler after `_cmd_queue_context`; dispatch in the early hook-dispatch block from Task 2, before the migration block)
- Test: `test/test_cli.py`

Reads a `PreToolUse` payload on stdin; if the Bash command matches a guard rule for the payload's `cwd` project, prints a `permissionDecision: deny` + redirect. Fails open on anything else.

- [ ] **Step 1: Write the failing test**

Add to `test/test_cli.py` (reuses `_git_repo_with_root` from Task 2):

```python
def _run_guard(cmd_obj, cfg, repo):
    import json as _json
    payload = _json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": cmd_obj},
        "cwd": str(repo),
    })
    return subprocess.run(
        [_BIN, "queue-guard"], input=payload, capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})


def test_queue_guard_denies_guarded_command(tmp_path):
    repo, cfg = _git_repo_with_root(tmp_path)
    result = _run_guard("docker compose up -d", cfg, repo)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "queue-run --resource root --" in out["permissionDecisionReason"]


def test_queue_guard_allows_unguarded_command(tmp_path):
    repo, cfg = _git_repo_with_root(tmp_path)
    result = _run_guard("docker ps", cfg, repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_queue_guard_ignores_non_bash_tool(tmp_path):
    repo, cfg = _git_repo_with_root(tmp_path)
    import json as _json
    payload = _json.dumps({"tool_name": "Read",
                           "tool_input": {"file_path": "/x"}, "cwd": str(repo)})
    result = subprocess.run(
        [_BIN, "queue-guard"], input=payload, capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_queue_guard_fails_open_on_garbage_stdin(tmp_path):
    _, cfg = _git_repo_with_root(tmp_path)
    result = subprocess.run(
        [_BIN, "queue-guard"], input="not json at all",
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_queue_guard_fails_open_when_cwd_missing(tmp_path):
    # Payload-schema drift: no cwd. Must NOT guess via os.getcwd() (which could be
    # another opted-in project) -> allow silently.
    repo, cfg = _git_repo_with_root(tmp_path)
    import json as _json
    payload = _json.dumps({"tool_name": "Bash",
                           "tool_input": {"command": "docker compose up -d"}})
    result = subprocess.run(
        [_BIN, "queue-guard"], input=payload, capture_output=True, text=True,
        cwd=str(repo),  # hook cwd happens to be an opted-in repo; must be ignored
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0
    assert result.stdout.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_cli.py -k queue_guard -q`
Expected: FAIL — `(not implemented) cmd=queue-guard`.

- [ ] **Step 3: Add the subparser**

In `build_parser()`, immediately **after** the `queue-context` block:

```python
    sub.add_parser(
        "queue-guard",
        help="Read a PreToolUse payload on stdin; emit a deny+redirect for "
             "guarded Bash commands (used by the PreToolUse hook). Fails open.")
```

- [ ] **Step 4: Add the handler**

After `_cmd_queue_context`:

```python
def _cmd_queue_guard(args) -> int:
    """Read a PreToolUse payload on stdin; deny+redirect a guarded Bash command.
    Fails open: any error (bad JSON, no config, parse ambiguity) -> no output,
    exit 0, tool proceeds. A false deny is worse than a missed guard (spec
    section 8)."""
    import json as _json
    try:
        raw = sys.stdin.read()
        payload = _json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
            return 0
        command = (payload.get("tool_input") or {}).get("command") or ""
        # Resolve strictly from the payload's cwd. Do NOT fall back to
        # os.getcwd(): the hook process's cwd is set by Claude Code (plugin /
        # install context), so guessing it could deny against the WRONG opted-in
        # project. No trustworthy cwd -> fail open (allow).
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            return 0
        from . import queue_awareness as _qa
        reason = _qa.guard_reason(_queue_config_path(), command, cwd)
        if reason:
            print(_json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason}}))
    except Exception:
        pass
    return 0
```

- [ ] **Step 5: Add the dispatch line to the early hook-dispatch block**

In `main()`, add `queue-guard` to the **early** hook-dispatch block introduced in Task 2 (the one before the migration block), right after the `queue-context` line:

```python
    if args.cmd == "queue-guard":
        return _cmd_queue_guard(args)
```

So the early block reads `queue-context` then `queue-guard`, both returning before the global migrations run.

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest test/test_cli.py -k queue_guard -q`
Expected: PASS (5 passed).

- [ ] **Step 7: Run the full Python suite (no regressions)**

Run: `python3 -m pytest test/ -q`
Expected: PASS (all green).

- [ ] **Step 8: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "feat(cli): add queue-guard subcommand (PreToolUse deny+redirect)"
```

---

## Task 4: Wire SessionStart `additionalContext` into `hooks/session-start.sh`

**Files:**
- Modify: `hooks/session-start.sh` (line 67 `index --record`; new branch before `exit 0` at line 86)
- Test: `test/hook.bats`

The hook must emit *only* the `queue-context` JSON to stdout (so it's valid `additionalContext`). The existing `index --record` currently leaves stdout un-redirected; route it to the log, then add the `queue-context` branch as the last thing before `exit 0`.

- [ ] **Step 1: Write the failing test**

Add to `test/hook.bats` (mirrors the existing `setup()` / `run_hook` helpers — `REPO`, `CLAUDE_PLUGIN_ROOT`, `HOME` are already exported there). Add a helper to opt a repo in, and two tests:

```bash
# --- Phase 3: SessionStart additionalContext ---

# Opt a git repo into the queue with a guarded 'root' resource.
optin_repo() {
  local repo="$1"
  git init -q "$repo"
  python3 - "$REPO" "$repo" "$HOME/.claude/session-explorer-queue-config.json" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/bin")
from _pkg import project_id, queue_config
repo, cfg = sys.argv[2], sys.argv[3]
pid = project_id.project_id(repo)
queue_config.add_resource(
    cfg, project_id=pid, display_path=repo, resource_id="root",
    resource={"kind": "root-dir", "path": repo,
              "guard": [{"exe": "docker", "sub": ["compose", "up"]}],
              "run_in": "root", "acquire": "sync", "release": "none",
              "sync": {"delete": True, "exclude": ["/.git"], "protect": ["/.git"]}})
PY
}

@test "session-start emits additionalContext for an opted-in project" {
  mkdir -p "$HOME/.claude"
  REPO_DIR="$HOME/proj"
  optin_repo "$REPO_DIR"
  PAYLOAD="{\"session_id\":\"01CTX\",\"transcript_path\":\"$HOME/01CTX.jsonl\",\"cwd\":\"$REPO_DIR\"}"
  run bash -c "printf '%s' '$PAYLOAD' | bash '$REPO/hooks/session-start.sh'"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['hookSpecificOutput']['hookEventName']=='SessionStart'; assert 'queue-run' in d['hookSpecificOutput']['additionalContext']"
}

@test "session-start emits nothing on stdout for a non-opted-in project" {
  mkdir -p "$HOME/.claude"
  REPO_DIR="$HOME/plain"
  git init -q "$REPO_DIR"
  PAYLOAD="{\"session_id\":\"01PLN\",\"transcript_path\":\"$HOME/01PLN.jsonl\",\"cwd\":\"$REPO_DIR\"}"
  run bash -c "printf '%s' '$PAYLOAD' | bash '$REPO/hooks/session-start.sh'"
  [ "$status" -eq 0 ]
  [ -z "$(echo -n "$output" | tr -d '[:space:]')" ]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats test/hook.bats -f "additionalContext"`
Expected: FAIL — the opted-in test sees empty stdout (no branch yet).

- [ ] **Step 3: Route `index --record` stdout to the log**

In `hooks/session-start.sh`, change line 67 from:

```bash
    "${CLI}" index --record "${SID}" "${TPATH}" "${CWD}" 2>>"${LOG}" || log "warn: index --record failed for ${SID}"
```

to:

```bash
    "${CLI}" index --record "${SID}" "${TPATH}" "${CWD}" >>"${LOG}" 2>&1 || log "warn: index --record failed for ${SID}"
```

- [ ] **Step 4: Add the additionalContext branch**

In `hooks/session-start.sh`, immediately **before** the final `exit 0` (line 86), add:

```bash
# --- Shared-resource awareness (Phase 3, spec section 8) ---
# For opted-in projects, inject SessionStart additionalContext telling the agent
# the resource is shared + warm and to use queue-run. queue-context prints the
# hookSpecificOutput JSON (or nothing) and fails open; this is the ONLY thing the
# hook writes to stdout (index --record above is routed to the log).
if [ -n "${CLI}" ] && [ -x "${CLI}" ] && [ -n "${CWD}" ]; then
  "${CLI}" queue-context --cwd "${CWD}" 2>>"${LOG}" || true
fi

```

- [ ] **Step 5: Run test to verify it passes**

Run: `bats test/hook.bats -f "additionalContext|non-opted-in"`
Expected: PASS.

- [ ] **Step 6: Run the full hook suite (no regressions)**

Run: `bats test/hook.bats`
Expected: PASS (existing pointer/gc tests still green).

- [ ] **Step 7: Commit**

```bash
git add hooks/session-start.sh test/hook.bats
git commit -m "feat(hook): inject shared-resource awareness into SessionStart additionalContext"
```

---

## Task 5: New `hooks/pre-tool-use.sh` wrapper

**Files:**
- Create: `hooks/pre-tool-use.sh`
- Test: `test/hook.bats`

A thin Bash wrapper mirroring `session-start.sh`'s CLI-resolution block, piping stdin to `queue-guard`. No registration in this task (Task 6 wires it).

- [ ] **Step 1: Write the failing test**

Add to `test/hook.bats` (reuses the `optin_repo` helper from Task 4):

```bash
# --- Phase 3: PreToolUse command-guard ---

@test "pre-tool-use exits 0 and is silent on empty stdin" {
  run bash -c "printf '' | bash '$REPO/hooks/pre-tool-use.sh'"
  [ "$status" -eq 0 ]
  [ -z "$(echo -n "$output" | tr -d '[:space:]')" ]
}

@test "pre-tool-use denies a guarded Bash command in an opted-in project" {
  mkdir -p "$HOME/.claude"
  REPO_DIR="$HOME/proj"
  optin_repo "$REPO_DIR"
  PAYLOAD="{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"docker compose up -d\"},\"cwd\":\"$REPO_DIR\"}"
  run bash -c "printf '%s' '$PAYLOAD' | bash '$REPO/hooks/pre-tool-use.sh'"
  [ "$status" -eq 0 ]
  echo "$output" | python3 -c "import json,sys; d=json.load(sys.stdin); h=d['hookSpecificOutput']; assert h['permissionDecision']=='deny'; assert 'queue-run --resource root --' in h['permissionDecisionReason']"
}

@test "pre-tool-use is silent for an unguarded command" {
  mkdir -p "$HOME/.claude"
  REPO_DIR="$HOME/proj"
  optin_repo "$REPO_DIR"
  PAYLOAD="{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"docker ps\"},\"cwd\":\"$REPO_DIR\"}"
  run bash -c "printf '%s' '$PAYLOAD' | bash '$REPO/hooks/pre-tool-use.sh'"
  [ "$status" -eq 0 ]
  [ -z "$(echo -n "$output" | tr -d '[:space:]')" ]
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bats test/hook.bats -f "pre-tool-use"`
Expected: FAIL — `hooks/pre-tool-use.sh` does not exist.

- [ ] **Step 3: Write the hook**

Create `hooks/pre-tool-use.sh`:

```bash
#!/usr/bin/env bash
# PreToolUse hook for session-explorer (Phase 3, spec section 8).
#
# Reads a PreToolUse payload on stdin: {tool_name, tool_input:{command}, cwd, ...}.
# For a guarded Bash command in an opted-in project, delegates to the CLI, which
# prints a `permissionDecision: deny` + redirect to queue-run. Fails OPEN: on any
# error (no CLI, bad payload, parse ambiguity) it emits nothing and exits 0, so
# the tool call proceeds. A false deny is worse than a missed guard.

set -u

# Probe sessions must leave no trace and never deny a tool call.
if [ "${SESSION_EXPLORER_PROBE:-}" = "1" ]; then exit 0; fi

CLAUDE_DIR="${HOME}/.claude"
LOG="${CLAUDE_DIR}/session-explorer.log"
# Ensure the log dir exists before any 2>>"${LOG}" redirection so the hook stays
# truly silent/fail-open even on a fresh box (mirrors session-start.sh).
mkdir -p "${CLAUDE_DIR}" 2>/dev/null || true

PAYLOAD="$(cat 2>/dev/null || true)"

# --- Resolve the CLI (same order as session-start.sh) ---
CLI=""
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -x "${CLAUDE_PLUGIN_ROOT}/bin/session-explorer" ]; then
  CLI="${CLAUDE_PLUGIN_ROOT}/bin/session-explorer"
elif [ -x "${HOME}/.local/bin/session-explorer" ]; then
  CLI="${HOME}/.local/bin/session-explorer"
else
  CLI="$(command -v session-explorer 2>/dev/null || echo "")"
fi

# No CLI or no payload -> fail open (let the tool run).
if [ -z "${CLI}" ] || [ ! -x "${CLI}" ] || [ -z "${PAYLOAD}" ]; then
  exit 0
fi

printf '%s' "${PAYLOAD}" | "${CLI}" queue-guard 2>>"${LOG}" || true
exit 0
```

- [ ] **Step 4: Make it executable**

Run: `chmod +x hooks/pre-tool-use.sh`

- [ ] **Step 5: Run test to verify it passes**

Run: `bats test/hook.bats -f "pre-tool-use"`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add hooks/pre-tool-use.sh test/hook.bats
git commit -m "feat(hook): add pre-tool-use.sh command-guard wrapper (fails open)"
```

---

## Task 6: Register `PreToolUse` across plugin.json, install.sh, uninstall.py

**Files:**
- Modify: `.claude-plugin/plugin.json` (after the `SessionEnd` block, line 73)
- Modify: `install.sh` (`_is_ours` ~line 41; `_MARKERS` line 39; new registration after line 69; chmod line 77)
- Modify: `bin/_pkg/uninstall.py` (`_is_our_hook` ~line 40; `_HOOK_MARKERS` line 15; `_HOOK_EVENTS` line 18)
- Test: `test/install.bats`, `test/uninstall.bats`

This is the *new wiring* the spec flags must be mirrored on all paths.

> **Hook-shape decision (review fix).** The current Claude Code docs (https://code.claude.com/docs/en/hooks) describe `settings.json` hooks **only** in the nested *matcher-group* form — `{"matcher": "Bash", "hooks": [{"type": "command", "command": ...}]}` — and document no legacy flat shape. The pre-existing `install.sh` writes a **flat** `{"matchers": [], "command": ...}` shape for the other events; whether current Claude Code still fires that is unverified (the marketplace `plugin.json`, which the primary install path uses, already uses the correct nested form, so the main path works regardless). To guarantee the new guard actually fires on plain installs, **register the new `PreToolUse` hook in `install.sh` using the documented nested form** (matching `plugin.json`), not the flat form. That requires `install.sh`'s `_strip_ours` and `uninstall.py`'s teardown to **prune at the nested command level** — removing only our command-hooks (matched by **concrete script name**, never the broad `session-explorer` substring, so a user hook whose path merely contains "session-explorer" survives) and keeping a matcher group if any user hooks remain — so a Bash group a user shares with us never loses the user's hook (below). **Out of scope for Phase 3:** migrating the existing flat-form hooks — flagged here as a suspected pre-existing bug to verify separately (a `/hooks` check + log inspection on a plain install), not fixed in this change.

- [ ] **Step 1: Write the failing tests**

Add to `test/install.bats`:

```bash
@test "install registers a PreToolUse hook (nested matcher-group, Bash)" {
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
pt = d['hooks']['PreToolUse']
# Documented nested shape: a matcher group scoped to Bash with nested command.
grp = next(h for h in pt if h.get('matcher') == 'Bash')
cmds = [s.get('command','') for s in grp.get('hooks', [])]
assert any('pre-tool-use.sh' in c for c in cmds), pt
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "install is idempotent — one PreToolUse guard command after two runs" {
  bash "$REPO/install.sh"
  bash "$REPO/install.sh"
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
# Count guard commands in BOTH flat and nested shapes.
cmds = []
for h in d['hooks']['PreToolUse']:
    if h.get('command'):
        cmds.append(h['command'])
    for sub in h.get('hooks', []) or []:
        if sub.get('command'):
            cmds.append(sub['command'])
print(sum(1 for c in cmds if 'pre-tool-use.sh' in c))
"
  [ "$output" = "1" ]
}

@test "install preserves a shared-group user hook (even one whose path contains 'session-explorer')" {
  mkdir -p "$HOME/.claude"
  # The user hook path deliberately contains 'session-explorer' to prove nested
  # pruning matches on concrete script names, not the broad marker.
  python3 -c "
import json, os
json.dump({'hooks': {'PreToolUse': [
    {'matcher': 'Bash', 'hooks': [
        {'type': 'command', 'command': '/opt/session-explorer-helper/audit.sh'},
        {'type': 'command', 'command': '$REPO/hooks/pre-tool-use.sh'}]}]}},
    open(os.path.expanduser('~/.claude/settings.json'), 'w'))
"
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
cmds = [s.get('command','') for h in d['hooks']['PreToolUse'] for s in h.get('hooks', []) or []]
assert any('audit.sh' in c for c in cmds), d   # user hook preserved despite 'session-explorer' in path
assert sum('pre-tool-use.sh' in c for c in cmds) == 1, d   # exactly one of ours
print('ok')
"
  [ "$output" = "ok" ]
}
```

Add to `test/uninstall.bats`:

```bash
@test "uninstall strips the PreToolUse guard (flat or nested)" {
  bash "$REPO/install.sh"
  bash "$REPO/uninstall.sh" || session-explorer uninstall || true
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
pt = d.get('hooks', {}).get('PreToolUse', [])
cmds = []
for h in pt:
    if h.get('command'):
        cmds.append(h['command'])
    for sub in h.get('hooks', []) or []:
        if sub.get('command'):
            cmds.append(sub['command'])
assert not any('pre-tool-use.sh' in c for c in cmds), pt
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "uninstall preserves a shared-group user hook (even one whose path contains 'session-explorer')" {
  mkdir -p "$HOME/.claude"
  python3 -c "
import json, os
json.dump({'hooks': {'PreToolUse': [
    {'matcher': 'Bash', 'hooks': [
        {'type': 'command', 'command': '/opt/session-explorer-helper/audit.sh'},
        {'type': 'command', 'command': '$REPO/hooks/pre-tool-use.sh'}]}]}},
    open(os.path.expanduser('~/.claude/settings.json'), 'w'))
"
  bash "$REPO/uninstall.sh" || session-explorer uninstall || true
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
pt = d.get('hooks', {}).get('PreToolUse', [])
cmds = [s.get('command','') for h in pt for s in h.get('hooks', []) or []]
assert any('audit.sh' in c for c in cmds), pt   # user hook preserved despite 'session-explorer' in path
assert not any('pre-tool-use.sh' in c for c in cmds), pt   # ours removed
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}
```

> Note: match the exact `uninstall.sh`/`session-explorer uninstall` invocation already used by the other tests in `test/uninstall.bats` — adjust the second line of the test to mirror them rather than the placeholder above.

Also create `test/test_plugin_manifest.py` — a pure-JSON check of the *marketplace* wiring (the bats tests only cover the plain-install `settings.json`, so a `plugin.json` syntax/matcher regression would otherwise only surface at release review):

```python
"""Validate the marketplace plugin manifest wiring (.claude-plugin/plugin.json)."""
import json
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _manifest():
    with open(os.path.join(_REPO, ".claude-plugin", "plugin.json")) as f:
        return json.load(f)


def test_manifest_is_valid_json_and_keeps_sessionstart():
    m = _manifest()
    assert m["name"] == "session-explorer"
    assert "SessionStart" in m["hooks"]


def test_manifest_registers_pretooluse_bash_guard():
    pt = _manifest()["hooks"]["PreToolUse"]
    grp = next(h for h in pt if h.get("matcher") == "Bash")
    cmds = [s.get("command", "") for s in grp["hooks"]]
    assert any("pre-tool-use.sh" in c for c in cmds), pt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bats test/install.bats -f "PreToolUse" ; bats test/uninstall.bats -f "PreToolUse" ; python3 -m pytest test/test_plugin_manifest.py -q`
Expected: FAIL — bats: `KeyError: 'PreToolUse'`; pytest: `KeyError: 'PreToolUse'` in `test_manifest_registers_pretooluse_bash_guard` (the valid-JSON test already passes).

- [ ] **Step 3: Register in `plugin.json`**

In `.claude-plugin/plugin.json`, add a `PreToolUse` entry. Change the end of the `SessionEnd` block (lines 65–74) so a new key follows it — insert after the `SessionEnd` array's closing `]` (line 74), before the final `}` of `hooks`:

```json
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/session-live.sh"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}\"/hooks/pre-tool-use.sh"
          }
        ]
      }
    ]
```

(Note the comma added after the `SessionEnd` array.)

- [ ] **Step 4: Register in `install.sh`**

In `install.sh`, update `_MARKERS` (line 39) to include the new script, and add a **narrow** `_HOOK_SCRIPTS` set used only when pruning inside a possibly-shared matcher group:

```python
_MARKERS = ("session-explorer", "session-start.sh", "session-live.sh",
            "pre-tool-use.sh")
# Concrete hook-script basenames. Used when pruning sub-hooks INSIDE a matcher
# group, where the broad "session-explorer" substring in _MARKERS could over-match
# a user hook whose path merely contains "session-explorer". Our own nested hooks
# are always one of these scripts, so narrowing loses no coverage.
_HOOK_SCRIPTS = ("session-start.sh", "session-live.sh", "pre-tool-use.sh")
```

Replace `_is_ours`/`_strip_ours` (lines 41–50) with command-level + **nested-pruning** versions, so re-install is idempotent for the nested `PreToolUse` entry **and** a mixed matcher group never loses a user's hook. (`_strip_ours` keeps its name and signature, so the existing call sites for the flat events stay correct.)

```python
def _cmd_is_ours(cmd):           # flat top-level entries: our dedicated entries
    return any(m in str(cmd) for m in _MARKERS)

def _sub_is_ours(cmd):           # nested sub-hooks: narrow, never over-match users
    return any(m in str(cmd) for m in _HOOK_SCRIPTS)

def _strip_ours(evt):
    """Drop our hook entries, preserving user hooks. Flat entries: drop if the
    command is ours. Nested matcher-group entries: prune only our nested
    command-hooks (matched by concrete script name, not the broad marker) and keep
    the group if any user hooks remain (so a shared Bash group never loses the
    user's hook). Fully-ours groups are dropped."""
    vals = hooks.get(evt)
    if not isinstance(vals, list):
        return []
    out = []
    for h in vals:
        if not isinstance(h, dict):
            out.append(h)
            continue
        if "hooks" in h:
            subs = h.get("hooks") or []
            kept = [s for s in subs
                    if not (isinstance(s, dict) and _sub_is_ours(s.get("command", "")))]
            if kept:
                out.append(dict(h, hooks=kept) if len(kept) != len(subs) else h)
            # else: group emptied of all our hooks -> drop the group
        elif not _cmd_is_ours(h.get("command", "")):
            out.append(h)
    return out
```

Add a `pretool_cmd` definition next to the others (after line 36 `live_cmd = ...`):

```python
pretool_cmd = os.path.join(repo, "hooks", "pre-tool-use.sh")
```

Add the registration after the `SessionEnd` block (after line 69), before the `with open(settings_path, "w")`. Use the **documented nested matcher-group form** (matching `plugin.json`), not the flat form, so the guard actually fires:

```python
hooks["PreToolUse"] = _strip_ours("PreToolUse") + [
    {"matcher": "Bash",
     "hooks": [{"type": "command", "command": pretool_cmd}]}]
```

Update the chmod line (77) to make the new hook executable:

```bash
chmod +x "${REPO_DIR}/hooks/session-start.sh" "${REPO_DIR}/hooks/session-live.sh" "${REPO_DIR}/hooks/pre-tool-use.sh" "${REPO_DIR}/bin/session-explorer"
```

- [ ] **Step 5: Register teardown in `uninstall.py`**

In `bin/_pkg/uninstall.py`, update `_HOOK_MARKERS` (line 15):

```python
_HOOK_MARKERS = ("session-explorer", "session-start.sh", "session-live.sh",
                 "pre-tool-use.sh")
```

and `_HOOK_EVENTS` (lines 18–19), and add a **narrow** `_HOOK_SCRIPTS` set for nested pruning:

```python
_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "Notification",
                "SessionEnd", "PreToolUse")
# Concrete hook-script basenames — used when pruning sub-hooks inside a possibly
# shared matcher group, where the broad "session-explorer" substring in
# _HOOK_MARKERS could over-match a user hook whose path merely contains it. Our
# own nested hooks are always one of these scripts.
_HOOK_SCRIPTS = ("session-start.sh", "session-live.sh", "pre-tool-use.sh")
```

Replace `_is_our_hook` (lines 40–43) with command-level + **nested-pruning** helpers so the nested `PreToolUse` entry is stripped on teardown **without** dropping a user hook that shares its matcher group:

```python
def _cmd_is_ours(cmd: object) -> bool:          # flat top-level entries
    return any(m in str(cmd) for m in _HOOK_MARKERS)


def _sub_is_ours(cmd: object) -> bool:          # nested sub-hooks (narrow)
    return any(m in str(cmd) for m in _HOOK_SCRIPTS)


def _prune_our_hooks(entries: list) -> "tuple[list, bool]":
    """Return (kept_entries, changed). Drops our flat entries and our nested
    command-hooks (nested matched by concrete script name, not the broad marker)
    while preserving user hooks that share a matcher group; a group emptied of all
    our hooks is dropped."""
    kept: list = []
    changed = False
    for h in entries:
        if not isinstance(h, dict):
            kept.append(h)
            continue
        if "hooks" in h:
            subs = h.get("hooks") or []
            kept_subs = [s for s in subs
                         if not (isinstance(s, dict) and _sub_is_ours(s.get("command", "")))]
            if len(kept_subs) != len(subs):
                changed = True
            if kept_subs:
                kept.append(dict(h, hooks=kept_subs) if len(kept_subs) != len(subs) else h)
        elif _cmd_is_ours(h.get("command", "")):
            changed = True
        else:
            kept.append(h)
    return kept, changed
```

Then update the teardown loop (lines 82–92) to use it:

```python
            for evt in _HOOK_EVENTS:
                entries = hooks.get(evt)
                if not isinstance(entries, list):
                    continue
                kept, changed = _prune_our_hooks(entries)
                if changed:
                    actions.append(f"removed {evt} hook entry")
                if kept:
                    hooks[evt] = kept
                elif evt in hooks:
                    del hooks[evt]
```

> If any existing test imports `_is_our_hook` directly, retarget it at `_prune_our_hooks` (or at `teardown`'s observable result on `settings.json`); the round here removes `_is_our_hook`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `bats test/install.bats ; bats test/uninstall.bats ; python3 -m pytest test/test_plugin_manifest.py -q`
Expected: PASS (new + existing tests green; manifest test 2 passed).

- [ ] **Step 7: Run the full pytest suite (uninstall.py is covered there too)**

Run: `python3 -m pytest test/ -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add .claude-plugin/plugin.json install.sh bin/_pkg/uninstall.py test/install.bats test/uninstall.bats test/test_plugin_manifest.py
git commit -m "feat(install): register PreToolUse command-guard hook on all paths"
```

---

## Task 7: Cooperative guidance + docs (queue-guide, SPEC, CLAUDE.md, release checklist)

**Files:**
- Modify: `docs/queue-guide.md` (append a section)
- Modify: `SPEC.md` (mark §8 implemented)
- Modify: `CLAUDE.md` (add the load-bearing hook decision)
- Modify: `.claude/skills/cutting-a-release/SKILL.md` (checklist)

No code; documentation that single-sources the §8 cooperative contract and keeps the release checklist honest.

- [ ] **Step 1: Append the cooperative section to `docs/queue-guide.md`**

Find the file's end (`grep -n "^## " docs/queue-guide.md` to see its section list) and append:

```markdown
## Cooperating as an agent

If you are a Claude session working in a project that shares singleton resources,
the SessionStart hook already told you which resources exist. To cooperate:

- **Never boot your own copy of a shared stack / server / database.** It is
  already running and warm; a second copy collides on its fixed ports and paths.
- **Run guarded commands through a lease:**
  `session-explorer queue-run --resource <name> -- <command>`. queue-run takes
  the lease (waiting in FIFO order if it's busy), runs the command, and releases.
- **Don't busy-spin or force a busy resource.** Report your queue position
  (`session-explorer queue-status`) and wait.
- **Expect a `sync` lease to overwrite the shared root** with your worktree's
  files on acquire. Keep secrets / local-only files out of tracked paths.

### CLAUDE.md snippet (copy into an opted-in project)

```markdown
## Shared resources

This repo shares singleton resources across worktrees via session-explorer.
- Do NOT start your own stack/server/db — it is already warm and will collide.
- Run guarded commands as: `session-explorer queue-run --resource <name> -- <cmd>`.
- Check who holds a resource with `session-explorer queue-status`.
```
```

- [ ] **Step 2: Mark §8 implemented in `SPEC.md`**

`grep -n "Phase 3\|Awareness\|PreToolUse\|additionalContext\|Build order" SPEC.md` to find the build-order / milestone area, then update the status line(s) per the `cutting-a-release` skill (handled in Task 8) and add a short note that §8 is shipped. Add a paragraph where the awareness/enforcement layer is described:

```markdown
**Phase 3 (shipped):** awareness + command-guard. The SessionStart hook injects
`additionalContext` for opted-in projects (via `session-explorer queue-context`);
a new `PreToolUse` Bash hook (`hooks/pre-tool-use.sh` → `queue-guard`) denies a
guarded command and redirects it to `queue-run`. Both fail open. Decision text
and guard matching are single-sourced in `bin/_pkg/queue_awareness.py` (reusing
`guard_match`). Accepted v1 blind spot: wrappers (`make`/`npm`) that hide a
guarded command are not caught — mitigated by the awareness injection, not the
hook.
```

(Place it adjacent to the existing "Shared-resource lease engine" section; match surrounding heading depth.)

- [ ] **Step 3: Add the load-bearing decision to `CLAUDE.md`**

In `CLAUDE.md`, under "Load-bearing design decisions", append after the existing shared-resource queue bullet:

```markdown
- **Phase-3 awareness/enforcement is advisory and fail-open.** The SessionStart
  `additionalContext` branch (inside the already-registered `session-start.sh`,
  via `queue-context`) and the new `PreToolUse` Bash hook (`hooks/pre-tool-use.sh`
  → `queue-guard`) only *nudge* — both emit nothing and exit 0 on any error, a
  false deny being worse than a missed guard. Decision text + guard matching live
  in `bin/_pkg/queue_awareness.py`, reusing `guard_match.matches` (never a
  substring regex). The `PreToolUse` hook is new wiring: registered in
  `.claude-plugin/plugin.json` **and** `install.sh`, torn down in `uninstall.py`
  (`_HOOK_MARKERS`/`_HOOK_EVENTS`) — keep all three in sync. Wrappers that hide a
  guarded command (`make`/`npm`) are an accepted blind spot.
```

- [ ] **Step 4: Update the `cutting-a-release` checklist**

In `.claude/skills/cutting-a-release/SKILL.md`, add a checklist item under "## Checklist" (after the `docs/queue-guide.md` item, ~line 89):

```markdown
- [ ] `PreToolUse` hook mirrored across `.claude-plugin/plugin.json`, `install.sh`
      (markers + registration + chmod), and `uninstall.py` (`_HOOK_MARKERS`/
      `_HOOK_EVENTS`) — never marketplace-only
- [ ] `docs/queue-guide.md` "Cooperating as an agent" snippet kept in sync with
      the SessionStart injection text in `queue_awareness.py`
```

- [ ] **Step 5: Commit**

```bash
git add docs/queue-guide.md SPEC.md CLAUDE.md .claude/skills/cutting-a-release/SKILL.md
git commit -m "docs: Phase-3 cooperative guidance + spec/CLAUDE/release-checklist updates"
```

---

## Task 8: Version bump + release

**Files:** per the `cutting-a-release` skill.

Phase 3 is independently shippable (the spec's build order: "Each is its own spec → plan → implementation cycle"; Phases 1 & 2 each shipped their own version). Cut a **minor** bump (this is a feature).

- [ ] **Step 1: Run the full suite one last time**

Run: `python3 -m pytest test/ -q && bats test/install.bats test/uninstall.bats test/hook.bats`
Expected: all PASS.

- [ ] **Step 2: Invoke the release skill**

Use the `cutting-a-release` skill. It is authoritative for the exact file set. For this release specifically:
- Bump `bin/_pkg/__init__.py` `__version__` and `.claude-plugin/plugin.json` `"version"` (must match) — minor bump from `1.13.0` → `1.14.0` (verify current with `grep -n version bin/_pkg/__init__.py .claude-plugin/plugin.json`).
- Update `README.md` + `SPEC.md` status lines.
- Add a `CHANGELOG.md` section (newest on top) describing: SessionStart shared-resource awareness injection; PreToolUse command-guard hook redirecting guarded commands to `queue-run`; cooperative guide snippet.
- The help screen (`tui.py` `_help_text()`) needs **no** keybinding change (Phase 3 adds no keys) — do not edit it.
- Confirm `docs/queue-guide.md` is in sync (Task 7 handled it).

- [ ] **Step 3: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to open the PR (one PR for all of Phase 3) and, after merge + green CI, cut the GitHub release per Phase 2 of the release skill.

---

## Self-review notes

- **Spec §8 coverage:** SessionStart `additionalContext` injection → Tasks 1,2,4. `PreToolUse` Bash hook reuses `guard_match` (parsed-argv `{exe,sub}`, fail-open) → Tasks 1,3,5: it denies+redirects **only confidently-parsed guarded simple commands**. Command substitution `$(...)`, backticks, heredocs, no-space operators, and wrapper bodies (`bash -c`, `make`/`npm` targets) are **not matched by `guard_match`**, so they pass through un-denied — an accepted v1 blind spot whose backstop is the SessionStart awareness injection, **not** this hook (these are *not* "denied-and-wrapped"). Locked by `test_guard_reason_fails_open_on_guard_match_blind_spots`. Install surfaces mirrored (plugin.json + install.sh + uninstall) → Task 6. Skill + CLAUDE.md snippet → Task 7. Deferred root-write-guard is explicitly *not* built (honored).
- **Fail-open posture:** every new entry point wraps logic in `try/except` → exit 0, and both hooks resolve-or-bail without ever exiting non-zero. Verified by `test_queue_guard_fails_open_on_garbage_stdin` and `pre-tool-use exits 0 ... on empty stdin`.
- **Symbol consistency:** `session_context(config_path, cwd)`, `guard_reason(config_path, command, cwd)`, `_queue_config_path()`, `queue_config.list_resources`, `project_id.project_id`, `guard_match.matches` — all match the names verified in the existing code.
- **No new keys / no TUI change** — Phase 3 is hooks + CLI + docs only; the `q`/`x`/`s` keymap from Phase 2 is untouched, so the help screen needs no edit.
- **PreToolUse payload contract (external dependency, verified):** the current contract delivers top-level `session_id`, `cwd`, `tool_name`, and `tool_input` (Bash command at `tool_input.command`); see https://code.claude.com/docs/en/hooks. `_cmd_queue_guard` degrades to fail-open under any drift: a missing/changed `tool_name` (≠ `"Bash"`) returns 0 silently, and a missing/non-string `cwd` **also returns 0 silently** — it deliberately does **not** fall back to `os.getcwd()`, since the hook process's cwd is set by Claude Code (plugin/install context) and guessing it could deny against the wrong opted-in project. Worst case of a contract change is a *missed* nudge, never a false deny. Re-confirm the field shape if Claude Code's hook schema is revised before shipping.
- **No substring matching anywhere (review fix):** `guard_reason` relies solely on `guard_match.matches` parsed-argv semantics; the prior `"queue-run" in command` shortcut was removed (it was redundant and an `echo queue-run && docker compose up` bypass). Covered by `test_guard_reason_no_substring_bypass` + `test_guard_reason_skips_already_wrapped_queue_run`.
