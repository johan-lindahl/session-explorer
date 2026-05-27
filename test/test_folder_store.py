"""Tests for _pkg.folder_store — atomic per-project folder path storage."""

import json
import os
import threading

from _pkg import folder_store


def test_load_missing_returns_default(tmp_path):
    assert folder_store.load(str(tmp_path / "absent.json")) == {
        "version": 1, "projects": {}
    }


def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "folders.json")
    payload = {"version": 1, "projects": {"acme-api": ["planning"]}}
    folder_store.save(path, payload)
    assert folder_store.load(path) == payload


def test_save_writes_via_temp_rename(tmp_path):
    path = str(tmp_path / "folders.json")
    folder_store.save(path, {"version": 1, "projects": {"x": []}})
    assert list(tmp_path.glob("*.tmp")) == []


def test_concurrent_writes_dont_corrupt(tmp_path):
    path = str(tmp_path / "folders.json")

    def worker(project: str):
        for i in range(50):
            folder_store.add(path, project, f"f{i}")

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    data = folder_store.load(path)
    assert len(data["projects"]["a"]) == 50
    assert len(data["projects"]["b"]) == 50


def test_default_path_for_index_sibling(tmp_path):
    idx = str(tmp_path / "session-explorer-index.json")
    expected = str(tmp_path / "session-explorer-folders.json")
    assert folder_store.default_path_for(idx) == expected
