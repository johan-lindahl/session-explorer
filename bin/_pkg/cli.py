"""Argparse skeleton for the session-explorer CLI."""

from __future__ import annotations

import argparse
import os
import shlex
import sys

from . import __version__
from . import index as _index
from . import launcher as _launcher
from .format import fmt_age, fmt_tokens
from .tree_model import build_nested_tree, split_path


def _index_path() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_INDEX")
    if env_override:
        return env_override
    return os.path.expanduser("~/.claude/session-explorer-index.json")


def _live_path() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_LIVE")
    if env_override:
        return env_override
    from . import live as _live
    return _live.default_path_for(_index_path())


def _queue_config_path() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_QUEUE_CONFIG")
    if env_override:
        return env_override
    from . import queue_config as _qc
    return _qc.default_path_for(_index_path())


def _queues_root() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_QUEUES_ROOT")
    if env_override:
        return env_override
    return os.path.join(os.path.dirname(_index_path()), "session-explorer-queues")


def _resolve_project(args) -> "tuple[str, str] | None":
    """Resolve (project_id, resource_id) from --resource + cwd/--project.
    Accepts a fully-qualified '<project-id>/<resource-id>' --resource too."""
    from . import project_id as _pid
    res = args.resource
    if "/" in res:
        pid, rid = res.split("/", 1)
        return pid, rid
    cwd = getattr(args, "project", None) or os.getcwd()
    pid = _pid.project_id(cwd)
    if pid is None:
        return None
    return pid, res


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="session-explorer")
    p.add_argument("--version", action="version", version=f"session-explorer {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="List all known sessions (text output).")
    sub.add_parser("launch", help="Launch the explorer in a new terminal window.")
    sub.add_parser("tui", help="Run the Textual TUI in-place (used by `launch`).")

    index_p = sub.add_parser("index", help="Index management.")
    index_p.add_argument("--record", nargs=3, metavar=("SESSION_ID", "TRANSCRIPT_PATH", "CWD"))
    index_p.add_argument("--refresh", action="store_true")
    index_p.add_argument("--backfill", action="store_true",
                         help="Scan ~/.claude/projects/ and index any session not yet tracked.")
    index_p.add_argument("--gc", action="store_true",
                         help="Delete old unnamed sessions (retention cleanup).")
    index_p.add_argument("--dry-run", action="store_true",
                         help="With --gc: report what would be removed, delete nothing.")
    index_p.add_argument("--retention-days", type=int, default=30, metavar="N",
                         help="With --gc: delete unnamed sessions idle longer than N days (default 30).")

    live_p = sub.add_parser("live", help="Record a session lifecycle event (used by hooks).")
    live_p.add_argument("--event", required=True,
                        help="Hook event name (SessionStart/UserPromptSubmit/Stop/Notification/SessionEnd).")
    live_p.add_argument("--sid", required=True, help="Session id.")
    live_p.add_argument("--transcript", default=None)
    live_p.add_argument("--cwd", default=None)
    live_p.add_argument("--pid", type=int, default=None)

    qr = sub.add_parser("queue-run",
                        help="Run a command under a shared-resource lease.")
    qr.add_argument("--resource", required=True,
                    help="Resource id, or fully-qualified <project-id>/<resource-id>.")
    qr.add_argument("--project", default=None,
                    help="Repo root to resolve the resource against (default: cwd).")
    qr.add_argument("--timeout", type=float, default=None,
                    help="Max seconds to wait for the lease before giving up.")
    qr.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- then the command to run.")

    qstat = sub.add_parser("queue-status",
                           help="Show active shared-resource queues.")
    qstat.add_argument("--json", action="store_true", help="Emit JSON.")

    qcancel = sub.add_parser("queue-cancel",
                             help="Cancel a waiting ticket on a resource.")
    qcancel.add_argument("--resource", required=True)
    qcancel.add_argument("--project", default=None)
    qcancel.add_argument("--sid", required=True, help="Session id of the waiter.")
    qcancel.add_argument("--reason", default="cancelled by user")

    qctx = sub.add_parser(
        "queue-context",
        help="Print SessionStart additionalContext for an opted-in project "
             "(used by the SessionStart hook). Silent + fail-open otherwise.")
    qctx.add_argument("--cwd", required=True,
                      help="Session cwd used to resolve the project.")

    sub.add_parser(
        "queue-guard",
        help="Read a PreToolUse payload on stdin; emit a deny+redirect for "
             "guarded Bash commands (used by the PreToolUse hook). Fails open.")

    qov = sub.add_parser(
        "queue-overlay",
        help="Engine-invoked overlay helper (in|out) for the shared installed "
             "app root template. Reads SE_QUEUE_WORKTREE/ROOT/STATE_DIR env.")
    qov.add_argument("direction", choices=["in", "out"])

    uninstall_p = sub.add_parser(
        "uninstall",
        help="Restore cleanupPeriodDays and remove session-explorer's files.")
    uninstall_p.add_argument(
        "--purge", action="store_true",
        help="Also delete the session index and folder store (names re-derive "
             "from JSONLs on reindex; notes and empty folders are lost).")

    app_p = sub.add_parser(
        "install-app",
        help="(macOS) Create a Dock launcher app in ~/Applications.")
    app_p.add_argument("--dest", default="~/Applications",
                       help="Parent directory for the .app (default ~/Applications).")
    app_p.add_argument("--name", default="Session Explorer",
                       help="App display name (default 'Session Explorer').")
    app_p.add_argument("--no-dock", action="store_true",
                       help="Create the app but do not pin it to the Dock.")
    return p


def _cmd_list() -> int:
    from . import folder_store as _fs
    idx_path = _index_path()
    data = _index.load(idx_path)
    fs_data = _fs.load(_fs.default_path_for(idx_path))
    if not data.get("sessions") and not fs_data.get("projects"):
        print("No sessions recorded yet.")
        return 0

    tree = build_nested_tree(data, fs_data, include_unnamed=True)

    def total(node):
        return len(node["_sessions"]) + sum(total(c) for c in node["_folders"].values())

    for proj in sorted(tree):
        node = tree[proj]
        print(f"\n{proj} ({total(node)})")
        # Root-level sessions first.
        for sid, s in node["_sessions"]:
            _print_session_row(sid, s, indent="  ")
        # Then folders, recursively, with path prefix.
        _print_subtree(node["_folders"], prefix="")
    return 0


def _print_session_row(sid, s, indent: str) -> None:
    _, display = split_path(s.get("name_cached"))
    display = display or sid[:8]
    age = fmt_age(s.get("last_active_at"))
    tokens = fmt_tokens(s.get("tokens_estimate", 0))
    pct = s.get("tokens_window_pct", 0)
    msgs = s.get("message_count", 0)
    prompt = (s.get("first_prompt") or "").replace("\n", " ")[:40]
    print(f"{indent}{display:<24} {age:>4}  {tokens:>6} ({pct:>3}%)  {msgs:>4} msgs   {prompt}")


def _print_subtree(folders: dict, prefix: str) -> None:
    for name in sorted(folders):
        child = folders[name]
        path = f"{prefix}{name}"
        if not child["_sessions"] and not child["_folders"]:
            print(f"  {path}/  (empty)")
            continue
        print(f"  {path}/")
        for sid, s in child["_sessions"]:
            _print_session_row(sid, s, indent="    ")
        _print_subtree(child["_folders"], prefix=path + "/")


def _cmd_index(args) -> int:
    path = _index_path()
    if args.record:
        sid, transcript, cwd = args.record
        _index.record_session(path, session_id=sid, transcript_path=transcript, cwd=cwd)
        return 0
    if args.refresh:
        _index.refresh_all(path)
        return 0
    if args.backfill:
        added = _index.backfill(path)
        print(f"Backfilled {added} session(s) from ~/.claude/projects/")
        return 0
    if args.gc:
        from _pkg import gc as _gc
        result = _gc.collect_garbage(
            path, retention_days=args.retention_days, dry_run=args.dry_run)
        n = len(result["removed"])
        live = result["skipped_live"]
        suffix = f" ({live} live session(s) skipped)" if live else ""
        if args.dry_run:
            print(f"[dry-run] Would remove {n} old unnamed session(s){suffix}")
        else:
            print(f"Removed {n} old unnamed session(s){suffix}")
        wt_result = _gc.collect_worktrees(path, dry_run=args.dry_run)
        if wt_result["removed_worktrees"]:
            wn = len(wt_result["removed_worktrees"])
            wt_suffix = (f" (skipped {wt_result['skipped_dirty']} dirty,"
                         f" {wt_result['skipped_live']} live)")
            if args.dry_run:
                print(f"[dry-run] Would remove {wn} idle worktree(s){wt_suffix}")
            else:
                print(f"Removed {wn} idle worktree(s){wt_suffix}")
        return 0
    print("index: pass --record SID TRANSCRIPT CWD, --refresh, --backfill, or --gc", file=sys.stderr)
    return 2


def _cmd_live(args) -> int:
    # Hooks call this; it must never raise (would surface as a hook failure).
    if not args.sid:
        return 0
    try:
        from . import live as _live
        _live.record_event(_live_path(), event=args.event, session_id=args.sid,
                            transcript_path=args.transcript, cwd=args.cwd, pid=args.pid)
    except Exception as e:
        try:
            log = os.path.expanduser("~/.claude/session-explorer.log")
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"warn: live --event {args.event} failed: {e}\n")
        except Exception:
            pass
    return 0


def _cmd_uninstall(args) -> int:
    from . import uninstall as _uninstall
    claude_dir = os.path.expanduser("~/.claude")
    actions = _uninstall.teardown(claude_dir=claude_dir, purge_data=args.purge)
    if actions:
        print("session-explorer uninstall:")
        for a in actions:
            print(f"  - {a}")
    else:
        print("session-explorer uninstall: nothing to undo (not installed?).")
    print("\nTo remove the plugin itself, run in Claude Code:")
    print("  /plugin uninstall session-explorer")
    return 0


def _cmd_launch() -> int:
    import shlex as _shlex
    from . import tmux as _tmux
    here = os.path.dirname(os.path.realpath(__file__))
    # bin/_pkg/cli.py → bin/session-explorer
    bin_path = os.path.normpath(os.path.join(here, "..", "session-explorer"))
    # `exec` so closing the TUI closes the spawned terminal window cleanly.
    target = f"exec {_shlex.quote(bin_path)} tui"
    if _tmux.available() and _tmux.meets_floor(_tmux.detected_version()):
        claude_dir = os.path.expanduser("~/.claude")
        os.makedirs(claude_dir, exist_ok=True)   # may not exist yet (CI / first run)
        conf = os.path.join(claude_dir, ".session-explorer.tmux.conf")
        with open(conf, "w") as f:
            f.write(_tmux.build_config())
        target = _launcher.wrap_in_tmux(target, config_path=conf)
    return _launcher.launch(target)


def _cmd_install_app(args) -> int:
    from . import macapp
    return macapp.install_app(dest=args.dest, name=args.name,
                              pin_dock=not args.no_dock)


def _cmd_queue_run(args) -> int:
    from . import queue_run as _qr
    resolved = _resolve_project(args)
    if resolved is None:
        print("queue-run: cwd is not inside a git repo / opted-in project",
              file=sys.stderr)
        return _qr.REFUSAL_EXIT
    project_id, resource_id = resolved
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("queue-run: no command given (use: queue-run --resource R -- CMD)",
              file=sys.stderr)
        return _qr.REFUSAL_EXIT
    import uuid
    sid = os.environ.get("CLAUDE_SESSION_ID") or f"cli-{uuid.uuid4().hex[:8]}"
    return _qr.run_lease(
        config_path=_queue_config_path(), queues_root=_queues_root(),
        live_path=_live_path(), project_id=project_id, resource_id=resource_id,
        command=command, cwd=os.getcwd(), sid=sid, pid=os.getpid(),
        timeout=args.timeout)


def _cmd_queue_status(args) -> int:
    import json as _json
    from . import queue_config as _qc
    from . import queue_run as _qr
    from . import queue_store as _qs
    cfg = _queue_config_path()
    rows = []
    for pid, proj in _qc.all_projects(cfg).items():
        for rid in proj.get("resources", {}):
            qdir = _qr.queue_dir(_queues_root(), pid, rid)
            tickets = _qs.list_tickets(qdir)
            holder = tickets[0] if tickets else None
            rows.append({
                "id": f"{pid}/{rid}",
                "project": proj.get("display_path", pid),
                "resource": rid,
                "holder": holder["sid"] if holder else None,
                "waiting": [t["sid"] for t in tickets[1:]],
            })
    if args.json:
        print(_json.dumps(rows))
        return 0
    if not rows:
        print("No shared resources configured.")
        return 0
    for row in rows:
        state = (f"holder: {row['holder']}" if row["holder"] else "free")
        wait = (f"  waiting: {', '.join(row['waiting'])}" if row["waiting"] else "")
        print(f"{row['id']:<40} {state}{wait}")
    return 0


def _cmd_queue_cancel(args) -> int:
    from . import queue_run as _qr
    from . import queue_store as _qs
    resolved = _resolve_project(args)
    if resolved is None:
        print("queue-cancel: could not resolve project", file=sys.stderr)
        return 2
    pid, rid = resolved
    qdir = _qr.queue_dir(_queues_root(), pid, rid)
    if _qs.cancel(qdir, sid=args.sid, reason=args.reason):
        print(f"Cancelled waiting ticket for {args.sid} on {pid}/{rid}.")
        return 0
    print(f"queue-cancel: no waiting ticket for {args.sid} on {pid}/{rid} "
          f"(it may be the running holder or already gone).", file=sys.stderr)
    return 1


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


def _cmd_queue_overlay(args) -> int:
    """Overlay (`in`) or restore (`out`) the shared installed app root. Reads
    the SE_QUEUE_* env the engine exports. `in` refuses on a dirty root so the
    engine treats acquire as failed; `out` returns nonzero if any path could not
    be restored, so the engine records a release failure."""
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
        manifest = _ov.apply_overlay(worktree, root, state_dir)
        if not manifest:
            # A legitimate success only when the branch genuinely has no delta;
            # otherwise a visible breadcrumb (the overlay is a no-op, root is
            # unchanged) instead of a silent "acquired".
            print("queue-overlay: nothing to overlay — the worktree has no "
                  "changes relative to its fork point with root", file=sys.stderr)
        return 0
    failed = _ov.restore_overlay(root, state_dir)
    if failed:
        print(f"queue-overlay: {len(failed)} path(s) not restored: "
              f"{', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    from . import folder_store as _fs
    parser = build_parser()
    args = parser.parse_args(argv)
    # Hook subcommands are on the critical path (SessionStart, and every
    # PreToolUse Bash call). Keep them cheap: dispatch before the global index /
    # folder migrations below, which they don't need, so a Bash tool call never
    # pays migration overhead just to evaluate the guard.
    if args.cmd == "queue-context":
        return _cmd_queue_context(args)
    if args.cmd == "queue-guard":
        return _cmd_queue_guard(args)
    # Run schema migration once per invocation (idempotent, no-op when the
    # index file doesn't exist yet, so fresh repos aren't materialised here).
    idx_path = _index_path()
    try:
        _index.migrate_to_v2(idx_path, _fs.default_path_for(idx_path))
        # Re-key the folder store from repo basename to repo root (so same-named
        # repos stop merging). Idempotent + version-gated; runs after v2 so the
        # index it reads sessions from is up to date.
        _index.migrate_folder_store_keys(idx_path, _fs.default_path_for(idx_path))
    except Exception as e:
        # Never block the CLI on migration; the next invocation retries. But
        # don't swallow the diagnostic — append to the same log the hook uses
        # so post-mortems can see what happened.
        try:
            log = os.path.expanduser("~/.claude/session-explorer.log")
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"warn: migration failed: {e}\n")
        except Exception:
            pass
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "index":
        return _cmd_index(args)
    if args.cmd == "live":
        return _cmd_live(args)
    if args.cmd == "uninstall":
        return _cmd_uninstall(args)
    if args.cmd == "list":
        return _cmd_list()
    if args.cmd == "launch":
        return _cmd_launch()
    if args.cmd == "install-app":
        return _cmd_install_app(args)
    if args.cmd == "queue-run":
        return _cmd_queue_run(args)
    if args.cmd == "queue-status":
        return _cmd_queue_status(args)
    if args.cmd == "queue-cancel":
        return _cmd_queue_cancel(args)
    if args.cmd == "queue-overlay":
        return _cmd_queue_overlay(args)
    if args.cmd == "tui":
        from .tui import run
        return run()
    print(f"(not implemented) cmd={args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
