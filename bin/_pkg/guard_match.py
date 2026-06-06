"""Guard matching: does a shell command invoke a guarded executable+subcommand?

Pure and conservative (spec §2/§8): lex the command with `shlex`, split on
**whitespace-delimited** operators (&& || ; | &), take each simple command's
executable basename plus leading subcommand tokens, and match against {exe, sub?}
rules. NEVER a substring regex ('up' must not match 'cleanup').

CONTRACT / KNOWN LIMITS (do not overclaim — this is reused by the Phase-3 hook):
this is NOT a full shell parser. It fails **open** (returns no match) on anything
it cannot confidently lex: command substitution `$(…)`/backticks, heredocs,
unbalanced quotes, and operators written WITHOUT surrounding whitespace
(`a&&b`). Redirections (`>`, `2>&1`) and wrapper bodies (`bash -c "…"`,
`make`/`npm` targets that hide the command) are likewise not seen. For the TUI
tester a missed match is harmless (the user sees "runs free"); the Phase-3 deny
hook must keep the same fail-open posture (a false deny is worse than a missed
guard — §8) and may later harden this with a real parser. Tests assert only the
confidently-lexable cases plus the fail-open boundary.
"""

from __future__ import annotations

import os
import shlex
from typing import List


def _segments(command: str) -> List[List[str]]:
    """Split on &&/||/;/| into simple commands; return token lists. Returns []
    if the text cannot be confidently lexed (unbalanced quotes, etc.)."""
    if any(m in command for m in ("$(", "`", "<<")):
        return []  # command substitution / heredoc — refuse to guess
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    segs: List[List[str]] = []
    cur: List[str] = []
    for tok in tokens:
        if tok in ("&&", "||", ";", "|", "&"):
            if cur:
                segs.append(cur); cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


_PREFIXES = {"env", "command", "nohup", "time"}


def _strip_prefixes(seg: List[str]) -> List[str]:
    out = list(seg)
    # leading `cd DIR` is dropped by the && split already; drop VAR=val + wrappers.
    while out:
        head = out[0]
        if "=" in head and not head.startswith("-") and "/" not in head.split("=", 1)[0]:
            out = out[1:]; continue
        if head in _PREFIXES:
            out = out[1:]; continue
        break
    return out


def matches(command: str, rules: List[dict]) -> bool:
    """True iff any simple-command segment matches any {exe, sub?} rule."""
    if not rules:
        return False
    for seg in _segments(command):
        seg = _strip_prefixes(seg)
        if not seg:
            continue
        exe = os.path.basename(seg[0])
        rest = seg[1:]
        for rule in rules:
            if exe != rule.get("exe"):
                continue
            sub = rule.get("sub") or []
            if rest[:len(sub)] == sub:
                return True
    return False
