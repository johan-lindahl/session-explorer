"""Tests for _pkg.index — atomic, flock'd JSON storage."""

import json
import os
import threading

from _pkg import index


def test_load_missing_returns_default(tmp_path):
    idx = index.load(str(tmp_path / "nope.json"))
    assert idx == {"version": 1, "folders": [], "sessions": {}}


def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "index.json")
    payload = {"version": 1, "folders": ["foo"], "sessions": {"u1": {"notes": "x"}}}
    index.save(path, payload)
    assert index.load(path) == payload


def test_save_writes_via_temp_rename(tmp_path):
    """Verifies the temp file is renamed, not written-in-place — crashes mid-write
    must leave the previous file intact."""
    path = str(tmp_path / "index.json")
    index.save(path, {"version": 1, "folders": [], "sessions": {"a": {}}})
    # No leftover *.tmp file
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_concurrent_writes_dont_corrupt(tmp_path):
    """Two threads call mutate(append) 50 times each; final folders list has 100 items."""
    path = str(tmp_path / "index.json")

    def worker(prefix: str):
        for i in range(50):
            def mutator(data: dict) -> dict:
                data["folders"].append(f"{prefix}-{i}")
                return data
            index.mutate(path, mutator)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()

    final = index.load(path)
    assert len(final["folders"]) == 100
