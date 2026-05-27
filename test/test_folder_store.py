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


def test_add_idempotent(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning")
    folder_store.add(path, "p1", "planning")
    assert folder_store.load(path)["projects"]["p1"] == ["planning"]


def test_add_multiple_projects_isolated(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning")
    folder_store.add(path, "p2", "bugfix")
    data = folder_store.load(path)
    assert data["projects"]["p1"] == ["planning"]
    assert data["projects"]["p2"] == ["bugfix"]


def test_remove_existing(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning")
    folder_store.add(path, "p1", "bugfix")
    folder_store.remove(path, "p1", "planning")
    assert folder_store.load(path)["projects"]["p1"] == ["bugfix"]


def test_remove_absent_is_noop(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning")
    folder_store.remove(path, "p1", "ghost")
    folder_store.remove(path, "no-such-project", "anything")
    assert folder_store.load(path)["projects"]["p1"] == ["planning"]


def test_list_paths_returns_sorted_copy(tmp_path):
    path = str(tmp_path / "f.json")
    folder_store.add(path, "p1", "planning/sprint14")
    folder_store.add(path, "p1", "bugfix")
    folder_store.add(path, "p1", "planning")
    paths = folder_store.list_paths(path, "p1")
    assert paths == ["bugfix", "planning", "planning/sprint14"]
    # mutating the result must not affect storage
    paths.append("evil")
    assert folder_store.list_paths(path, "p1") == ["bugfix", "planning", "planning/sprint14"]


def test_list_paths_missing_project_returns_empty(tmp_path):
    path = str(tmp_path / "f.json")
    assert folder_store.list_paths(path, "p1") == []
