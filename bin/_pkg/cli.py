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
from .tree_model import build_tree, split_folder


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
    if not data.get("sessions"):
        print("No sessions recorded yet.")
        return 0

    tree = build_tree(data)
    # The "(unfiled)" synthetic bucket holds empty user-created folders only —
    # skip it from the text listing since it has no sessions to render.
    projects = [p for p in tree if p != "(unfiled)"]

    # Map build_tree's canonical folder sentinels to the legacy display key so
    # `""` (named-but-no-dash) and `"(unnamed)"` (no name at all) collapse into
    # one header-less bucket, preserving the prior output byte-for-byte.
    _HEADERLESS = ("", "(unnamed)")

    for proj in sorted(projects):
        folders = tree[proj]
        # Merge the two header-less sentinels into one logical bucket while
        # preserving last_active_at desc order across both.
        headerless: list[tuple[str, dict]] = []
        for key in _HEADERLESS:
            headerless.extend(folders.get(key, []))
        headerless.sort(key=lambda x: x[1].get("last_active_at", ""), reverse=True)

        named_folders = sorted(f for f in folders if f not in _HEADERLESS)

        total = len(headerless) + sum(len(folders[f]) for f in named_folders)
        print(f"\n{proj} ({total})")

        # Iterate folders in the same sorted order the old code used: the
        # legacy key for headerless was "(no folder)", which sorts before any
        # real folder name beginning with a letter (parens < letters in ASCII).
        ordered: list[tuple[str, list[tuple[str, dict]], str]] = []
        ordered.append(("(no folder)", headerless, "  "))
        for f in named_folders:
            ordered.append((f, folders[f], "    "))
        ordered.sort(key=lambda x: x[0])

        for folder, entries, indent in ordered:
            if folder != "(no folder)":
                print(f"  {folder}/")
            for sid, s in entries:
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
