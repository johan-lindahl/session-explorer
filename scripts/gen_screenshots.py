#!/usr/bin/env python3
"""Dev-only generator for the README TUI screenshots.

Drives the REAL Textual app headless with a fabricated index, exports SVGs, then
converts to PNG via Chrome headless + ImageMagick. NOT shipped in the plugin
(lives under scripts/, not bin/_pkg/), so it adds no runtime dependency.

Usage:  python3 scripts/gen_screenshots.py
Requires: Google Chrome and ImageMagick (`magick`) on this machine.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "bin"))

from _pkg.tui import SessionExplorerApp  # noqa: E402

OUT = os.path.join(REPO, "docs", "images")
WORK = tempfile.mkdtemp(prefix="se-shots-")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

SESSIONS = {
    "acme-api/a-auth": {"project_label": "acme-api", "project_path": "/Users/jl/acme-api",
        "name_cached": "auth/refresh-tokens", "branch": "main",
        "last_active_at": "2026-05-29T08:00:00+00:00", "created_at": "2026-05-20T00:00:00+00:00",
        "tokens_estimate": 48000, "tokens_window_pct": 24, "message_count": 36,
        "first_prompt": "Add refresh-token rotation to the auth service", "notes": "",
        "transcript_path": "/Users/jl/.claude/projects/acme-api/a.jsonl"},
    "acme-api/a-bug": {"project_label": "acme-api", "project_path": "/Users/jl/acme-api",
        "name_cached": "fix/null-deref", "branch": "main",
        "last_active_at": "2026-05-29T07:30:00+00:00", "created_at": "2026-05-22T00:00:00+00:00",
        "tokens_estimate": 12000, "tokens_window_pct": 6, "message_count": 9,
        "first_prompt": "Investigate the null deref in the parser", "notes": "",
        "transcript_path": "/Users/jl/.claude/projects/acme-api/b.jsonl"},
    "webapp/w-feat": {"project_label": "webapp", "project_path": "/Users/jl/webapp",
        "name_cached": "feature/live-cart", "branch": "main",
        "last_active_at": "2026-05-29T09:59:00+00:00", "created_at": "2026-05-28T00:00:00+00:00",
        "tokens_estimate": 91000, "tokens_window_pct": 45, "message_count": 58,
        "first_prompt": "Build the live cart total component", "notes": "review before merge",
        "transcript_path": "/Users/jl/.claude/projects/webapp/w.jsonl"},
    "webapp/w-idle": {"project_label": "webapp", "project_path": "/Users/jl/webapp",
        "name_cached": "spike/pricing", "branch": "main",
        "last_active_at": "2026-05-29T09:40:00+00:00", "created_at": "2026-05-27T00:00:00+00:00",
        "tokens_estimate": 33000, "tokens_window_pct": 16, "message_count": 21,
        "first_prompt": "Prototype the new pricing tiers", "notes": "",
        "transcript_path": "/Users/jl/.claude/projects/webapp/wi.jsonl"},
}
LIVE = {"webapp/w-feat": "working", "webapp/w-idle": "idle"}


def _write_index(path):
    json.dump({"version": 2, "sessions": SESSIONS}, open(path, "w"))


async def _shoot(idx_path, name, live=None, open_help=False, open_preview=False):
    app = SessionExplorerApp(index_path=idx_path)
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause()
        if live:
            app._live_states = dict(live)
            app._spinner_frame = 7  # a dense, legible braille frame (⠧)
            # Repopulate so the "● N active" subtitle is recomputed (it's set in
            # _populate, which ran on mount with an empty live set); this also
            # re-renders every row's live glyph via _row_label.
            app._populate()
            app._relabel_live_rows()
            await pilot.pause()
        if open_preview:
            # Preview is empty unless a session row (not a folder/project) is
            # highlighted; move the cursor onto a named session with notes first.
            app._restore_cursor_to_sid("webapp/w-feat")
            await pilot.pause()
            app.action_preview()
            await pilot.pause()
        if open_help:
            app.action_help()
            await pilot.pause()
        app.save_screenshot(os.path.join(WORK, f"{name}.svg"))


def _svg_to_png(name):
    svg = os.path.join(WORK, f"{name}.svg")
    chrome_png = os.path.join(WORK, f"{name}.chrome.png")
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=2", "--default-background-color=00000000",
                    "--window-size=1439,731", f"--screenshot={chrome_png}",
                    f"file://{svg}"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["magick", chrome_png, "-resize", "1600x", "-strip",
                    os.path.join(OUT, f"{name}.png")], check=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    idx = os.path.join(WORK, "index.json")
    _write_index(idx)
    # Suppress first-run modals so the app mounts straight to the tree.
    d = os.path.dirname(idx)
    open(os.path.join(d, ".session-explorer.help-seen"), "w").close()
    open(os.path.join(d, ".session-explorer.retention-declined"), "w").close()
    asyncio.run(_shoot(idx, "tree", live=LIVE))
    asyncio.run(_shoot(idx, "live", live=LIVE))
    asyncio.run(_shoot(idx, "preview", live=LIVE, open_preview=True))
    asyncio.run(_shoot(idx, "help", open_help=True))
    for name in ("tree", "live", "preview", "help"):
        _svg_to_png(name)
        print("wrote", os.path.join(OUT, f"{name}.png"))


if __name__ == "__main__":
    main()
