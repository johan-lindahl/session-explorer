"""Summarise a transcript digest by shelling out to the Claude Code CLI.

Runs `claude -p` headless with the digest piped on stdin. Spawned with
SESSION_EXPLORER_SUMMARIZER=1 and SESSION_EXPLORER_PROBE=1 so our own
SessionStart hook bails at its early-exit guard — the summariser session records
no index row, current pointer, or GC. It uses no tools, so the pre-tool-use hook
never fires. No Textual import.
"""

from __future__ import annotations

import os
import shutil
import subprocess

SUMMARY_MODEL = "claude-haiku-4-5"
SUMMARY_TIMEOUT = 90.0

PROMPT = (
    "You are summarising a Claude Code session transcript for a session browser. "
    "In 3-5 sentences or short bullet points, say what the session was about and "
    "what was accomplished. Be concrete. No preamble, no heading — just the summary."
)


class SummaryError(Exception):
    """Raised when the summariser subprocess cannot produce a summary."""


def run(digest: str, *, model: str = SUMMARY_MODEL, timeout: float = SUMMARY_TIMEOUT) -> str:
    claude = shutil.which("claude")
    if not claude:
        raise SummaryError("claude CLI not found on PATH")

    env = dict(os.environ)
    env["SESSION_EXPLORER_SUMMARIZER"] = "1"
    env["SESSION_EXPLORER_PROBE"] = "1"

    argv = [claude, "-p", PROMPT, "--model", model]
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env,
        )
    except OSError as e:
        raise SummaryError(f"failed to launch claude: {e}") from e

    try:
        out, err = proc.communicate(input=digest, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        proc.kill()
        raise SummaryError("summariser timed out") from e

    if proc.returncode != 0:
        raise SummaryError(f"claude exited {proc.returncode}: {(err or '').strip()[:200]}")
    text = (out or "").strip()
    if not text:
        raise SummaryError("summariser returned empty output")
    return text
