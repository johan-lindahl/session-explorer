"""Argparse skeleton for the session-explorer CLI."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from . import index as _index


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "index":
        return _cmd_index(args)
    # list/launch land in later tasks
    print(f"(not implemented) cmd={args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
