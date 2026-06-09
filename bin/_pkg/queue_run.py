"""Single-process lease lifecycle (spec §3/§4/§5).

One process runs: take ticket -> wait turn -> [root-dir exclusive-or +
sync-source/baseline guards] -> health (warn) -> acquire -> wait_for -> run the
command in run_in -> release hook -> release ticket (strictly last, in finally).
Exit code is the child's; a pre-command refusal uses REFUSAL_EXIT. SIGINT/SIGTERM
forward to the child, then the finally releases the ticket. Crash/SIGKILL is
covered by queue_store's flock auto-release + reaping.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from . import exclusive, probes, qsync
from . import project_id as _pid
from . import queue_store

REFUSAL_EXIT = 70   # distinct from any wrapped-command failure
_RELEASE_TIMEOUT = 60


class _Interrupted(Exception):
    """Raised from the signal handler when SIGINT/SIGTERM arrives while we are
    WAITING (no child running yet), so the finally releases the ticket. The spec
    requires Ctrl-C to drop the ticket and leave the line even mid-wait."""


def queue_dir(queues_root: str, project_id: str, resource_id: str) -> str:
    return os.path.join(queues_root, project_id, resource_id)


def _wait_for_root_free(live_path: str, root: str, ticket, qdir: str, *,
                        poll_interval: float = 0.5,
                        timeout: Optional[float] = None) -> str:
    """Block (holding our FIFO ticket) while a live root session works in root.
    Returns 'free' once clear, 'timeout', or 'cancelled:<reason>'. Raises
    _Interrupted on signal (time.sleep is interrupted by the handler)."""
    waited = 0.0
    announced = False
    while True:
        reason = queue_store.cancelled_reason(qdir, ticket.number, ticket.sid)
        if reason is not None or not os.path.exists(ticket.path):
            return f"cancelled:{reason or 'cancelled'}"
        hit = exclusive.live_root_session(live_path, root)
        if hit is None:
            return "free"
        if not announced:
            print(f"queue-run: waiting — root held by live session "
                  f"{hit['name']!r}", file=sys.stderr)
            announced = True
        if timeout is not None and waited >= timeout:
            return "timeout"
        time.sleep(poll_interval)
        waited += poll_interval


def _refuse(msg: str) -> int:
    print(f"queue-run: {msg}", file=sys.stderr)
    return REFUSAL_EXIT


def _run_shell(command: str, cwd: Optional[str], timeout: Optional[float],
               env: Optional[dict] = None) -> int:
    run_env = {**os.environ, **env} if env else None
    return subprocess.run(command, shell=True, cwd=cwd, timeout=timeout,
                          env=run_env).returncode


def _hook_env(*, src: str, root: str, qdir: str) -> dict:
    """Env exported to command_acquire/command_release (overlay helper contract)."""
    return {"SE_QUEUE_WORKTREE": src, "SE_QUEUE_ROOT": root,
            "SE_QUEUE_STATE_DIR": qdir}


def _do_acquire(resource: dict, *, src: str, root: str, qdir: str) -> Optional[str]:
    """Run the acquire strategy. Returns a refusal message, or None on success."""
    strategy = resource.get("acquire", "none")
    if strategy == "none":
        return None
    if strategy == "command":
        cmd = resource.get("command_acquire")
        env = _hook_env(src=src, root=root, qdir=qdir)
        if cmd and _run_shell(cmd, cwd=root, timeout=None, env=env) != 0:
            return "acquire command failed"
        return None
    if strategy == "sync":
        sync = resource.get("sync", {})
        exclude = sync.get("exclude", ["/.git"])
        protect = sync.get("protect", list(qsync.DEFAULT_PROTECT))
        allow_delete = resource.get("allow_delete", [])
        first_transition = not qsync.in_sandbox(qdir)
        # First sandbox transition: classification gate (spec §2/§5.3).
        if first_transition:
            guard = exclusive.transition_guard(root)
            if guard:
                return guard
            try:
                would_delete = qsync.dry_run_deletions(src, root, exclude=exclude,
                                                       protect=protect)
            except qsync.SyncDryRunError as e:
                # Fail closed: never assume "no deletions" when the dry-run that
                # gates the destructive --delete could not be completed.
                return f"could not verify the sandbox reset is safe: {e}"
            unresolved = qsync.unclassified(root, would_delete, protect=protect,
                                            allow_delete=allow_delete)
            if unresolved:
                listing = "\n  ".join(unresolved)
                return ("the sandbox reset would delete unclassified root files; "
                        "add each to this resource's `protect` (precious) or "
                        "`allow_delete` (regenerable) list in the queue config, "
                        f"then retry:\n  {listing}")
        cmd = qsync.rsync_command(src, root, exclude=exclude, protect=protect,
                                  dry_run=False)
        if subprocess.run(cmd).returncode != 0:
            return "rsync acquire failed"
        # The marker means "baseline settled" -> write it only AFTER a
        # successful real sync, so a failed rsync doesn't skip the gate next run.
        if first_transition:
            qsync.mark_sandbox(qdir)
        return None
    return f"unknown acquire strategy {strategy!r}"


def _do_release(resource: dict, *, root: str, src: str, qdir: str) -> bool:
    """Run the release hook (time-bounded). Returns True on success/none."""
    if resource.get("release") != "command":
        return True
    cmd = resource.get("command_release")
    if not cmd:
        return True
    try:
        return _run_shell(cmd, cwd=root, timeout=_RELEASE_TIMEOUT,
                          env=_hook_env(src=src, root=root, qdir=qdir)) == 0
    except subprocess.TimeoutExpired:
        return False


def _record_release_failure(qdir: str, sid: str, msg: str) -> None:
    hist = os.path.join(qdir, "history")
    os.makedirs(hist, exist_ok=True)
    path = os.path.join(hist, f"release-fail-{sid}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sid": sid, "error": msg}, f)
    except OSError:
        pass


def run_lease(*, config_path: str, queues_root: str, live_path: str,
              project_id: str, resource_id: str, command: List[str], cwd: str,
              sid: str, pid: int, timeout: Optional[float] = None) -> int:
    from . import queue_config as qc

    resource = qc.get_resource(config_path, project_id, resource_id)
    if resource is None:
        return _refuse(f"no resource {resource_id!r} configured for this project")

    kind = resource.get("kind")
    run_in = resource.get("run_in", "worktree")
    root = resource.get("path") or _pid.main_root(cwd) or cwd
    src = _pid.toplevel(cwd) or cwd     # the holder's worktree (sync source)
    work_dir = root if run_in == "root" else cwd

    # root-dir sync must have a worktree source, never rsync root over itself.
    if kind == "root-dir" and resource.get("acquire") == "sync":
        if _pid.is_root_cwd(cwd, root):
            return _refuse("root-dir sync must run from a worktree, not root "
                           "itself (a root cwd is the exclusive-or holder)")

    qdir = queue_dir(queues_root, project_id, resource_id)
    display = qc.load(config_path).get("projects", {}).get(project_id, {}) \
        .get("display_path", project_id)
    label = f"{os.path.basename(display.rstrip('/')) or display}/{resource_id}"
    now_iso = datetime.now(timezone.utc).isoformat()

    ticket = queue_store.take_ticket(qdir, sid=sid, cwd=cwd, command=command,
                                     pid=pid, label=label, now_iso=now_iso)

    # A mutable holder so the signal handler always sees the *current* child.
    child_holder: List[Optional[subprocess.Popen]] = [None]

    def _handler(signum, _frame):
        ch = child_holder[0]
        if ch is not None and ch.poll() is None:
            ch.send_signal(signum)          # a command is running: forward to it
        else:
            raise _Interrupted(signum)      # still waiting: abort -> finally releases

    old_int = signal.signal(signal.SIGINT, _handler)
    old_term = signal.signal(signal.SIGTERM, _handler)

    start = time.monotonic()

    def _remaining() -> Optional[float]:
        if timeout is None:
            return None
        return max(0.0, timeout - (time.monotonic() - start))

    # `result` is set on every post-acquire path and returned AFTER finally, so
    # the release hook (in finally) can still bump it for release_required. Note:
    # pre-acquire paths `return` directly (acquired is False -> finally is a
    # no-op for release), which is safe.
    result = REFUSAL_EXIT
    acquired = False
    try:
        outcome = queue_store.wait_for_turn(qdir, ticket, timeout=_remaining())
        if outcome.startswith("cancelled:"):
            return _refuse(outcome.split(":", 1)[1])
        if outcome == "timeout":
            return _refuse("timed out waiting for the resource")

        # Exclusive-or (root-dir only): WAIT holding our FIFO place while a live
        # root session works in root; proceed once it clears (spec §5/§4) —
        # never fail and lose the line.
        if kind == "root-dir":
            root_outcome = _wait_for_root_free(live_path, root, ticket, qdir,
                                               timeout=_remaining())
            if root_outcome.startswith("cancelled:"):
                return _refuse(root_outcome.split(":", 1)[1])
            if root_outcome == "timeout":
                return _refuse("timed out waiting for the live root session to end")

        # Health: detect + warn, never block.
        ok, detail = probes.health_check(resource.get("health"))
        if not ok:
            print(f"queue-run: warning: resource appears down: {detail}",
                  file=sys.stderr)

        refusal = _do_acquire(resource, src=src, root=root, qdir=qdir)
        if refusal:
            return _refuse(refusal)        # acquire failed: nothing to release
        acquired = True

        if not probes.wait_for(resource.get("wait_for")):
            print("queue-run: resource did not become ready before timeout",
                  file=sys.stderr)
            result = REFUSAL_EXIT          # set, don't return: release must run
        else:
            try:
                child = subprocess.Popen(command, cwd=work_dir)
            except OSError as e:
                # Missing executable / bad cwd: a controlled exit, not a
                # traceback. acquired stays True so the finally still releases.
                print(f"queue-run: failed to start command: {e}", file=sys.stderr)
                result = REFUSAL_EXIT
            else:
                child_holder[0] = child
                try:
                    child.wait()
                    result = child.returncode
                finally:
                    # Clear so a signal after completion (but before handler
                    # restoration) can't forward to / mis-detect an exited child.
                    child_holder[0] = None
    except _Interrupted:
        print("queue-run: interrupted — releasing ticket", file=sys.stderr)
        result = REFUSAL_EXIT
    finally:
        # The release phase must be UNINTERRUPTIBLE: a signal here must not raise
        # (_Interrupted from our handler, or KeyboardInterrupt from the default
        # one) and skip ticket release. Ignore SIGINT/SIGTERM for the duration,
        # then restore the originals. ticket.release() sits in its own innermost
        # finally so it runs even if _do_release raises unexpectedly.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            # Release hook runs whenever acquire SUCCEEDED, on any exit path
            # after acquire (normal completion, readiness refusal, Popen
            # failure, interrupt) — before the ticket is released (spec §4).
            if acquired:
                err = None
                try:
                    ok = _do_release(resource, root=root, src=src, qdir=qdir)
                except Exception as e:   # a buggy release hook must not strand the queue
                    ok = False
                    err = f"release hook raised: {e}"
                if not ok:
                    _record_release_failure(
                        qdir, sid, err or "release hook failed/timed out")
                    print("queue-run: warning: release hook failed", file=sys.stderr)
                    if resource.get("release_required") and result == 0:
                        result = 1
        finally:
            try:
                ticket.release()   # absolutely last, unavoidable
            finally:
                # Restore handlers even if release somehow raised, so the
                # process never escapes with SIGINT/SIGTERM left at SIG_IGN.
                signal.signal(signal.SIGINT, old_int)
                signal.signal(signal.SIGTERM, old_term)
    return result
