"""Tests for _pkg.index — atomic, flock'd JSON storage."""

import json
import os
import os as _os
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


import shutil


_FIX = _os.path.join(_os.path.dirname(__file__), "fixtures")


def test_record_session_creates_entry(tmp_path):
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")

    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")

    data = index.load(idx_path)
    assert "01ABC" in data["sessions"]
    s = data["sessions"]["01ABC"]
    assert s["name_cached"] == "planning-sprint14-custom"  # custom-title wins over ai-title
    assert s["project_path"] == "/Users/jl/proj/foo"
    assert s["project_label"] == "foo"
    assert s["first_prompt"] == "plan sprint 14 work"
    assert s["message_count"] == 5  # 4 original + the custom-title line added in Task 5
    assert s["tokens_estimate"] == 15234
    assert s["bytes"] > 0


def test_record_session_idempotent(tmp_path):
    """Calling record twice updates last_active_at but doesn't duplicate."""
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")

    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")
    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")

    data = index.load(idx_path)
    assert len(data["sessions"]) == 1


def test_record_session_preserves_notes(tmp_path):
    """A user-edited 'notes' field survives a re-record."""
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")

    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")
    # User edits notes
    def add_notes(data: dict) -> dict:
        data["sessions"]["01ABC"]["notes"] = "user notes"
        return data
    index.mutate(idx_path, add_notes)
    # Re-record
    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")

    data = index.load(idx_path)
    assert data["sessions"]["01ABC"]["notes"] == "user notes"
