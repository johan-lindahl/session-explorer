"""Daemon-less FIFO queue core: one ticket file per participant.

The queue *is* the set of ticket files in a directory. Ordering comes from a
monotonic number baked into each filename; the holder is the lowest-numbered
ticket whose owner process is still alive. Liveness is proven by flock: the
owner holds LOCK_EX on its own ticket for its whole life, so a prober that can
grab LOCK_EX|LOCK_NB knows the owner died (the kernel drops the lock on exit,
including SIGKILL) -> immune to PID reuse, survives crashes.

Publication ordering (under the queue '.lock'): allocate number -> write temp
ticket -> acquire LOCK_EX on it -> atomic rename into the visible dir. A ticket
is therefore visible only after it already holds its lifetime lock, so a
prober's LOCK_NB can never catch it in an unlocked gap and falsely reap it.

Concurrency mirrors the other stores: flock(LOCK_EX) on '<qdir>/.lock' guards
allocate / publish / reap / cancel.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_TICKET_PREFIX = "t"
_NUM_WIDTH = 10


def ticket_name(number: int, sid: str) -> str:
    """Lexically-sortable ticket filename (zero-padded number)."""
    safe_sid = "".join(c for c in sid if c.isalnum() or c in "-_") or "anon"
    return f"{_TICKET_PREFIX}{number:0{_NUM_WIDTH}d}-{safe_sid}.json"


def _parse_number(name: str) -> Optional[int]:
    if not (name.startswith(_TICKET_PREFIX) and name.endswith(".json")):
        return None
    core = name[len(_TICKET_PREFIX):-len(".json")]
    num_str = core.split("-", 1)[0]
    try:
        return int(num_str)
    except ValueError:
        return None


def _lock_path(qdir: str) -> str:
    return os.path.join(qdir, ".lock")


def _ticket_files(qdir: str) -> List[Tuple[int, str]]:
    """(number, abspath) for every ticket file, sorted by number."""
    out: List[Tuple[int, str]] = []
    try:
        names = os.listdir(qdir)
    except FileNotFoundError:
        return out
    for name in names:
        num = _parse_number(name)
        if num is not None:
            out.append((num, os.path.join(qdir, name)))
    out.sort(key=lambda t: t[0])
    return out


def _probe_alive(ticket_path: str) -> bool:
    """True iff some process still holds the ticket's lifetime flock."""
    try:
        f = open(ticket_path, "r")
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # we grabbed it -> owner is dead
        return False
    except (BlockingIOError, OSError):
        return True
    finally:
        f.close()


def _next_number(qdir: str) -> int:
    files = _ticket_files(qdir)
    return (files[-1][0] + 1) if files else 1


@dataclass
class Ticket:
    number: int
    path: str
    sid: str
    _lock_fd: object  # kept open for the ticket's lifetime

    def release(self) -> None:
        """Drop the lifetime lock and remove the ticket file. Best-effort and
        idempotent — never raises (a racing reaper, an already-gone file, or a
        permission error must not propagate out of cleanup)."""
        try:
            os.unlink(self.path)
        except OSError:
            pass  # already gone / racing reaper / permission — nothing to do
        try:
            self._lock_fd.close()  # releases the flock
        except Exception:
            pass


def take_ticket(qdir: str, *, sid: str, cwd: str, command, pid: int,
                label: str, now_iso: str) -> Ticket:
    """Allocate, lock, and publish a ticket. The returned Ticket holds the
    lifetime lock; call .release() (always in a finally) when done."""
    os.makedirs(qdir, exist_ok=True)
    with open(_lock_path(qdir), "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            number = _next_number(qdir)
            payload = {"number": number, "sid": sid, "cwd": cwd,
                       "command": command, "pid": pid, "label": label,
                       "created": now_iso}
            fd, tmp = tempfile.mkstemp(prefix=".t-", suffix=".tmp", dir=qdir)
            with os.fdopen(fd, "w", encoding="utf-8") as wf:
                json.dump(payload, wf)
            # Re-open and grab the LIFETIME lock BEFORE publishing.
            lock_fd = open(tmp, "r+")
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            final = os.path.join(qdir, ticket_name(number, sid))
            os.replace(tmp, final)   # publish; lock_fd follows the inode
            return Ticket(number=number, path=final, sid=sid, _lock_fd=lock_fd)
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def _reap_and_scan(qdir: str) -> List[Tuple[int, str]]:
    """Under the queue lock: unlink dead tickets, return live (number, path)."""
    os.makedirs(qdir, exist_ok=True)
    live: List[Tuple[int, str]] = []
    with open(_lock_path(qdir), "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            for number, path in _ticket_files(qdir):
                if _probe_alive(path):
                    live.append((number, path))
                else:
                    try:
                        os.unlink(path)
                    except FileNotFoundError:
                        pass
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
    return live


def holder(qdir: str) -> Optional[int]:
    """Lowest-numbered live ticket number, reaping dead ones. None if empty."""
    live = _reap_and_scan(qdir)
    return live[0][0] if live else None


def position(qdir: str, my_number: int) -> Tuple[int, int]:
    """(place, total) among live tickets; place is 1-based by number."""
    live = _reap_and_scan(qdir)
    total = len(live)
    place = sum(1 for n, _ in live if n <= my_number)
    return place, total


def _read_ticket(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def list_tickets(qdir: str) -> List[dict]:
    """Live ticket payloads, sorted by number (for queue-status / the pane)."""
    rows: List[dict] = []
    for _, path in _reap_and_scan(qdir):
        data = _read_ticket(path)
        if data is not None:
            rows.append(data)
    return rows
