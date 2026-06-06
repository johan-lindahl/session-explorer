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
