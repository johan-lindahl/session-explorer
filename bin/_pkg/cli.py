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

    uninstall_p = sub.add_parser(
        "uninstall",
        help="Restore cleanupPeriodDays and remove session-explorer's files.")
    uninstall_p.add_argument(
        "--purge", action="store_true",
        help="Also delete the session index and folder store (names re-derive "
             "from JSONLs on reindex; notes and empty folders are lost).")
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
        flag = os.path.join(claude_dir, ".session-explorer.tmux-persist")
        conf = os.path.join(claude_dir, ".session-explorer.tmux.conf")
        with open(conf, "w") as f:
            f.write(_tmux.build_config(persist_flag_path=flag))
        # Stale persist-flag from a prior run must not suppress the next
        # abrupt-close kill; clear it on every fresh launch.
        _tmux.clear_persist_flag(flag)
        target = _launcher.wrap_in_tmux(target, config_path=conf)
    return _launcher.launch(target)


def main(argv: list[str] | None = None) -> int:
    from . import folder_store as _fs
    parser = build_parser()
    args = parser.parse_args(argv)
    # Run schema migration once per invocation (idempotent, no-op when the
    # index file doesn't exist yet, so fresh repos aren't materialised here).
    idx_path = _index_path()
    try:
        _index.migrate_to_v2(idx_path, _fs.default_path_for(idx_path))
    except Exception as e:
        # Never block the CLI on migration; the next invocation retries. But
        # don't swallow the diagnostic — append to the same log the hook uses
        # so post-mortems can see what happened.
        try:
            log = os.path.expanduser("~/.claude/session-explorer.log")
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"warn: migrate_to_v2 failed: {e}\n")
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
    if args.cmd == "tui":
        from .tui import run
        return run()
    print(f"(not implemented) cmd={args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
