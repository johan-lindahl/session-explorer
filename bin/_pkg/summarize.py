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
import tempfile

SUMMARY_MODEL = "claude-haiku-4-5"
SUMMARY_TIMEOUT = 90.0

PROMPT = (
    "Summarise what this past Claude Code session was about and what was "
    "accomplished, in 3-5 sentences or short bullet points. Do NOT continue or "
    "reply to the conversation — only describe it. Be concrete. No preamble, no "
    "heading — just the summary."
)


class SummaryError(Exception):
    """Raised when the summariser subprocess cannot produce a summary."""


def _build_prompt(digest: str) -> str:
    """Wrap the transcript in explicit markers and repeat the instruction at both
    ends. A long transcript of USER:/ASSISTANT: turns otherwise reads as a live
    conversation the model *continues* ("Ready for your next task…"), and a
    single top-of-prompt instruction gets buried before the model reaches the
    end. Bracketing + a bottom restatement + "do not continue" fixes both."""
    return (f"{PROMPT}\n\n<<<TRANSCRIPT_START>>>\n{digest}\n<<<TRANSCRIPT_END>>>"
            f"\n\n{PROMPT}")


def run(digest: str, *, model: str = SUMMARY_MODEL, timeout: float = SUMMARY_TIMEOUT) -> str:
    """Summarise `digest` via `claude -p`.

    The whole instruction+transcript goes in the `-p` argument — piping it on
    stdin is silently ignored by the CLI (it answers the `-p` arg and never sees
    the pipe). We also run in a throwaway empty cwd so Claude doesn't load the
    current project's CLAUDE.md and summarise *that* instead of the transcript,
    and close stdin so the CLI doesn't wait ~3s for pipe input that never comes.
    """
    claude = shutil.which("claude")
    if not claude:
        raise SummaryError("claude CLI not found on PATH")

    env = dict(os.environ)
    env["SESSION_EXPLORER_SUMMARIZER"] = "1"
    env["SESSION_EXPLORER_PROBE"] = "1"

    argv = [claude, "-p", _build_prompt(digest), "--model", model]
    workdir = tempfile.mkdtemp(prefix="se-summarize-")
    try:
        try:
            proc = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env, cwd=workdir,
            )
        except OSError as e:
            raise SummaryError(f"failed to launch claude: {e}") from e

        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            raise SummaryError("summariser timed out") from e
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if proc.returncode != 0:
        raise SummaryError(f"claude exited {proc.returncode}: {(err or '').strip()[:200]}")
    text = (out or "").strip()
    if not text:
        raise SummaryError("summariser returned empty output")
    return text
