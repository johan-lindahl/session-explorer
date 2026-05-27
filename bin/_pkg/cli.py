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
from .tree_model import split_folder


def _index_path() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_INDEX")
    if env_override:
        return env_override
    return os.path.expanduser("~/.claude/session-explorer-index.json")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="session-explorer")
    p.add_argument("--version", action="version", version=f"session-explorer {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="List all known sessions (text output).")
    sub.add_parser("launch", help="Launch the explorer in a new terminal window.")

    index_p = sub.add_parser("index", help="Index management.")
    index_p.add_argument("--record", nargs=3, metavar=("SESSION_ID", "TRANSCRIPT_PATH", "CWD"))
    index_p.add_argument("--refresh", action="store_true")
    return p


def _cmd_list() -> int:
    data = _index.load(_index_path())
    sessions = data.get("sessions", {})
    if not sessions:
        print("No sessions recorded yet.")
        return 0

    # Group by project_label, then by folder.
    by_project: dict[str, dict[str, list[tuple[str, dict]]]] = {}
    for sid, s in sessions.items():
        proj = s.get("project_label", "(unknown)")
        folder, _ = split_folder(s.get("name_cached"))
        by_project.setdefault(proj, {}).setdefault(folder or "(no folder)", []).append((sid, s))

    for proj in sorted(by_project):
        folders = by_project[proj]
        total = sum(len(v) for v in folders.values())
        print(f"\n{proj} ({total})")
        for folder in sorted(folders):
            if folder != "(no folder)":
                print(f"  {folder}/")
            indent = "    " if folder != "(no folder)" else "  "
            for sid, s in sorted(folders[folder], key=lambda x: x[1].get("last_active_at", ""), reverse=True):
                _, display = split_folder(s.get("name_cached"))
                display = display or sid[:8]
                age = fmt_age(s.get("last_active_at"))
                tokens = fmt_tokens(s.get("tokens_estimate", 0))
                pct = s.get("tokens_window_pct", 0)
                msgs = s.get("message_count", 0)
                prompt = (s.get("first_prompt") or "").replace("\n", " ")[:40]
                print(f"{indent}{display:<24} {age:>4}  {tokens:>6} ({pct:>3}%)  {msgs:>4} msgs   {prompt}")
    return 0


def _cmd_index(args) -> int:
    path = _index_path()
    if args.record:
        sid, transcript, cwd = args.record
        _index.record_session(path, session_id=sid, transcript_path=transcript, cwd=cwd)
        return 0
    if args.refresh:
        _index.refresh_all(path)
        return 0
    print("index: pass --record SID TRANSCRIPT CWD or --refresh", file=sys.stderr)
    return 2


def _cmd_launch() -> int:
    here = os.path.dirname(os.path.realpath(__file__))
    # bin/_pkg/cli.py → bin/session-explorer
    bin_path = os.path.normpath(os.path.join(here, "..", "session-explorer"))
    target = f"{shlex.quote(bin_path)} list; echo; echo Press Enter to close; read"
    return _launcher.launch(target)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "index":
        return _cmd_index(args)
    if args.cmd == "list":
        return _cmd_list()
    if args.cmd == "launch":
        return _cmd_launch()
    # Safety net for any unknown subcommand
    print(f"(not implemented) cmd={args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
