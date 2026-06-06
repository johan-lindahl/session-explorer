"""Health + readiness probes for the queue lease lifecycle (spec §2/§4).

`health_check` answers "is the resource up?" -- v1 detects and warns, never
auto-starts (the `ensure` param is deferred). `wait_for` polls a port / url /
command until ready or the timeout elapses, run after acquire and before the
wrapped command. Stdlib only: subprocess + socket (no requests/http client dep).
"""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple


def health_check(command: Optional[str], *, timeout: float = 10) -> Tuple[bool, str]:
    """Run the health shell command; up iff it exits 0. No command -> (True,
    'no health check')."""
    if not command:
        return True, "no health check configured"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "health check timed out"
    except OSError as e:
        return False, f"health check error: {e}"
    if r.returncode == 0:
        return True, "up"
    return False, (r.stderr.strip() or r.stdout.strip() or
                   f"health check exited {r.returncode}")


def _check_port(target: str, timeout: float) -> bool:
    host, _, port = target.partition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _check_url(target: str, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(target, timeout=timeout) as resp:
            return 200 <= resp.status < 500   # any response = the server answered
    except urllib.error.HTTPError:
        return True       # an HTTP error is still a live server
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _check_command(target: str, timeout: float) -> bool:
    try:
        return subprocess.run(target, shell=True, capture_output=True,
                              timeout=timeout).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def wait_for(spec: Optional[dict], *, poll_interval: float = 0.5) -> bool:
    """Poll `spec` (a {type, target, timeout} dict) until ready or timeout.
    `type` is 'port' | 'url' | 'command'. None/empty spec -> ready immediately."""
    if not spec:
        return True
    kind = spec.get("type")
    target = spec.get("target", "")
    deadline = float(spec.get("timeout", 60))
    per_try = min(poll_interval * 4, 5.0)  # cap a single probe's own timeout
    checker = {"port": _check_port, "url": _check_url,
               "command": _check_command}.get(kind)
    if checker is None:
        return True  # unknown probe type: don't block (fail open)
    waited = 0.0
    while True:
        if checker(target, per_try):
            return True
        if waited >= deadline:
            return False
        time.sleep(poll_interval)
        waited += poll_interval
