"""Per-project shared-resource declarations for the queue engine.

Schema (v1):
  {"version": 1,
   "projects": {
     "<project-id>": {
       "display_path": "/abs/repo/path",          # human-readable, never a key
       "resources": {
         "<resource-id>": {
           "kind": "root-dir"|"path"|"port"|"service"|"device"|"name",
           "path": "/abs/path",                    # data only; for root-dir/path
           "guard": [{"exe": "docker", "sub": ["compose", "up"]}],  # legacy, ignored (root_guard replaced command matching)
           "run_in": "root"|"worktree",
           "acquire": "sync"|"none"|"command",
           "release": "none"|"command",
           "command_acquire": "<shell>",           # when acquire == command
           "command_release": "<shell>",           # when release == command
           "release_required": false,
           "sync": {"delete": true, "exclude": ["/.git"],
                    "protect": ["/.git", "/.env", "/.env.*"]},
           "allow_delete": [],                      # classified regenerable paths
           "health": "<shell>",                     # optional
           "wait_for": {"type": "url"|"port"|"command",
                        "target": "...", "timeout": 60}   # optional
         }
       }
     }
   }}

Keyed by `project_id.project_id(cwd)` — NOT a raw cwd path. Concurrency mirrors
`folder_store`: read under LOCK_SH (via `load`), mutate under LOCK_EX on a
sidecar `.lock` + temp-file-rename.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from typing import Any, Callable, Dict, List

VALID_KINDS = {"root-dir", "path", "port", "service", "device", "name"}
_RESOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DEFAULT: Dict[str, Any] = {"version": 1, "projects": {}}


def default_path_for(index_path: str) -> str:
    d = os.path.dirname(os.path.abspath(index_path)) or "."
    return os.path.join(d, "session-explorer-queue-config.json")


def valid_resource_id(rid: str) -> bool:
    """Slug usable as an on-disk queue key: lowercase, no slash/dot/`..`."""
    return bool(rid) and bool(_RESOURCE_ID_RE.match(rid))


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {"version": 1, "projects": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "projects": {}}


def save(path: str, data: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-qc-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def mutate(path: str, fn: Callable[[dict], dict]) -> dict:
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)) or ".", exist_ok=True)
    with open(lock_path, "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            data = fn(load(path))
            save(path, data)
            return data
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def _validate(resource_id: str, resource: dict) -> None:
    if not valid_resource_id(resource_id):
        raise ValueError(
            f"invalid resource id {resource_id!r}: must match [a-z0-9][a-z0-9-]*")
    kind = resource.get("kind")
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind {kind!r}: one of {sorted(VALID_KINDS)}")
    if resource.get("run_in") not in ("root", "worktree"):
        raise ValueError("run_in must be 'root' or 'worktree'")
    acquire = resource.get("acquire")
    if acquire not in ("sync", "none", "command"):
        raise ValueError("acquire must be 'sync', 'none' or 'command'")
    release = resource.get("release")
    if release not in ("none", "command"):
        raise ValueError("release must be 'none' or 'command'")
    # v1: sync only applies to root-dir (spec §2).
    if acquire == "sync" and kind != "root-dir":
        raise ValueError("acquire 'sync' is only valid for kind 'root-dir' in v1")
    # Strategy invariants: a command strategy needs its command; a root-dir
    # resource needs the path it bind-mounts / syncs to.
    if acquire == "command" and not resource.get("command_acquire"):
        raise ValueError("acquire 'command' requires a 'command_acquire' shell string")
    if release == "command" and not resource.get("command_release"):
        raise ValueError("release 'command' requires a 'command_release' shell string")
    # An apply-only overlay (queue-overlay in with no matching out) copies the
    # worktree's changed files into root and never restores them, leaking them
    # into the shared install. Require the restoring half whenever the in-half
    # is wired.
    if "queue-overlay in" in (resource.get("command_acquire") or "") \
            and "queue-overlay out" not in (resource.get("command_release") or ""):
        raise ValueError(
            "'queue-overlay in' acquire requires a matching 'queue-overlay out' "
            "release (release='command', command_release='session-explorer "
            "queue-overlay out') — otherwise the overlay is never restored")
    if kind == "root-dir" and not resource.get("path"):
        raise ValueError("kind 'root-dir' requires a 'path'")


def add_resource(path: str, *, project_id: str, display_path: str,
                 resource_id: str, resource: dict) -> None:
    """Add/replace one resource under a project. Validates before writing."""
    _validate(resource_id, resource)

    def m(data: dict) -> dict:
        projects = data.setdefault("projects", {})
        proj = projects.setdefault(project_id, {"display_path": display_path,
                                                "resources": {}})
        proj["display_path"] = display_path
        proj.setdefault("resources", {})[resource_id] = resource
        data.setdefault("version", 1)
        return data

    mutate(path, m)


def remove_resource(path: str, project_id: str, resource_id: str) -> None:
    def m(data: dict) -> dict:
        proj = data.get("projects", {}).get(project_id)
        if proj:
            proj.get("resources", {}).pop(resource_id, None)
            if not proj.get("resources"):
                data["projects"].pop(project_id, None)  # opt out when empty
        return data

    mutate(path, m)


def get_resource(path: str, project_id: str, resource_id: str) -> "dict | None":
    return load(path).get("projects", {}).get(project_id, {}) \
        .get("resources", {}).get(resource_id)


def list_resources(path: str, project_id: str) -> Dict[str, dict]:
    return dict(load(path).get("projects", {}).get(project_id, {})
                .get("resources", {}))


def is_opted_in(path: str, project_id: str) -> bool:
    return bool(list_resources(path, project_id))


def all_projects(path: str) -> Dict[str, dict]:
    return dict(load(path).get("projects", {}))
