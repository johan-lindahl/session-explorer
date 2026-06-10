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


def _aliases(root: str) -> "list[str]":
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
    if not _qc.load(config_path).get("projects"):
        return None
    pid = _pid.project_id(cwd)
    if not pid:
        return None
    resources = _qc.list_resources(config_path, pid)
    for rid in sorted(resources):
        res = resources[rid]
        if res.get("kind") == "root-dir" and isinstance(res.get("path"), str) \
                and res.get("path"):
            return rid, res
    return None


def _session_location(payload: dict, live_path: str) -> "str | None":
    """The session's home directory: the live registry's recorded cwd when the
    session is registered (authoritative — a tool call's cwd can drift), else
    the payload cwd (weaker fallback, accepted by the spec)."""
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid:
        data = _live.load(live_path)
        sessions = data.get("sessions") if isinstance(data, dict) else None
        entry = sessions.get(sid) if isinstance(sessions, dict) else None
        # NB: deliberately no liveness (_alive) filter here — sids are UUIDs
        # and SessionStart re-records on resume, so a stale entry's cwd is
        # still the best available signal; a wrong guess only flips WHICH
        # deny rule applies, never allows a root write.
        cwd = entry.get("cwd") if isinstance(entry, dict) else None
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
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
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


# Shell operators that would let the agent's outer shell re-split a suggested
# rewrite, running part of it outside the lease. Same table the old awareness
# module used; presence of any of these wraps the rewrite in `bash -c <quoted>`.
_SHELL_OPS = ("&&", "||", ";", "|", "&", ">", "<", "$(", "`", "\n")

# Characters the allowlist raw-rejects even inside quotes (it cannot tell
# quoted from unquoted once shlex strips quotes). A bash -c rewrite of a
# command containing them would itself be denied — a dead loop — so for
# those we suggest the script-file route instead.
_UNQUOTABLE = ("$(", "`", "\n")

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


def _rewrite(rid: str, command: str) -> str:
    """The exact queue-run invocation to suggest for `command` — guaranteed
    to survive _is_queue_invocation, or a script-file fallback when it can't."""
    if any(ch in command for ch in _UNQUOTABLE):
        return (f"(your command contains a newline/backtick/$( — put it in a "
                f"script inside your worktree, e.g. run.sh, then:) "
                f"session-explorer queue-run --resource {rid} -- bash run.sh")
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


def _mentions_root(command: str, aliases: "list[str]") -> bool:
    """True iff the command references the root OUTSIDE the managed-worktrees
    subtree. An alias occurrence followed by `/.claude/worktrees/` is worktree
    ground, not a root mention; an occurrence followed by a path character
    (e.g. `<root>-backup`) is a different path entirely."""
    for alias in aliases:
        start = 0
        while True:
            i = command.find(alias, start)
            if i == -1:
                break
            rest = command[i + len(alias):]
            if not rest.startswith("/.claude/worktrees/") and \
                    (rest == "" or rest[0] in "/ \t'\";)&|<>\n"):
                return True
            start = i + 1
    return False


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
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return None
    if _is_queue_invocation(command):
        return None  # the one door: a single simple session-explorer queue-* call
    # Mention = deny. No "confident parse" requirement: a worktree session has
    # no legitimate raw root-touching Bash, ever (a lease only exists inside a
    # queue-run process), so false positives are cheap and recoverable.
    if _mentions_root(command, _aliases(root)):
        return _deny_bash_text(rid, root, command)
    # Parent-climb: a managed worktree sits at <root>/.claude/worktrees/<n>,
    # so `../..` already reaches shared ground. External worktrees are
    # covered by the alias rule only.
    if _inside(location, _wt_dir(root)) and "../.." in command:
        return _deny_bash_text(rid, root, command)
    return None
