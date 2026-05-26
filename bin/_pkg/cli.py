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


def _fmt_tokens(n: int) -> str:
    if n >= 10000:
        return f"~{n // 1000}K"
    return f"~{n}"


def _fmt_age(iso: str | None) -> str:
    if not iso:
        return "—"
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    delta = datetime.now(timezone.utc) - ts
    if delta.days >= 1:
        return f"{delta.days}d"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h"
    return f"{delta.seconds // 60}m"


def _split_folder(name: str | None) -> tuple[str, str]:
    """First-dash split. ('', name) when no dash; ('', '') when no name."""
    if not name:
        return ("", "")
    if "-" not in name:
        return ("", name)
    folder, _, display = name.partition("-")
    return (folder, display)


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
        folder, _ = _split_folder(s.get("name_cached"))
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
                _, display = _split_folder(s.get("name_cached"))
                display = display or sid[:8]
                age = _fmt_age(s.get("last_active_at"))
                tokens = _fmt_tokens(s.get("tokens_estimate", 0))
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
    # launch lands in later task
    print(f"(not implemented) cmd={args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
