"""Persisted TUI toggles (session-explorer-ui.json).

Single-writer (the explorer process), so a plain atomic temp-file-rename write
is enough — no `.lock` sidecar. Mirrors the default-on-corruption shape of the
other stores. v1 holds one flag: queue_pane_visible (spec §9).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict

_DEFAULT: Dict[str, Any] = {"version": 1, "queue_pane_visible": False, "retention_days": 30}


def default_path_for(index_path: str) -> str:
    d = os.path.dirname(os.path.abspath(index_path)) or "."
    return os.path.join(d, "session-explorer-ui.json")


def load(path: str) -> dict:
    if not os.path.exists(path):
        return dict(_DEFAULT)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT)
    if not isinstance(data, dict):
        return dict(_DEFAULT)
    merged = dict(_DEFAULT)
    merged.update(data)
    return merged


def save(path: str, data: dict) -> None:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-ui-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def set_queue_pane_visible(path: str, visible: bool) -> None:
    data = load(path)
    data["queue_pane_visible"] = bool(visible)
    save(path, data)


def get_retention_days(path: str) -> int:
    try:
        return int(load(path).get("retention_days", 30))
    except (TypeError, ValueError):
        return 30


def set_retention_days(path: str, days: int) -> None:
    data = load(path)
    data["retention_days"] = int(days)
    save(path, data)
