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


def test_refresh_all_recomputes_caches(tmp_path):
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")
    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")

    # Simulate stale caches
    def stale(data: dict) -> dict:
        data["sessions"]["01ABC"]["message_count"] = 0
        data["sessions"]["01ABC"]["tokens_estimate"] = 0
        return data
    index.mutate(idx_path, stale)

    index.refresh_all(idx_path)

    data = index.load(idx_path)
    assert data["sessions"]["01ABC"]["message_count"] == 5  # named.jsonl now has 5 lines
    assert data["sessions"]["01ABC"]["tokens_estimate"] == 15234


def test_refresh_all_drops_missing_jsonl(tmp_path):
    """If a session's JSONL no longer exists, refresh drops it from the index."""
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")
    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/x")

    _os.unlink(transcript)
    index.refresh_all(idx_path)

    data = index.load(idx_path)
    assert "01ABC" not in data["sessions"]


def test_backfill_adds_untracked_sessions(tmp_path):
    """backfill() scans projects_root and records every JSONL not yet in the index."""
    projects = tmp_path / "projects"
    proj_a = projects / "-Users-jl-proj-foo"
    proj_a.mkdir(parents=True)
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), proj_a / "AAA.jsonl")
    shutil.copy(_os.path.join(_FIX, "unnamed.jsonl"), proj_a / "BBB.jsonl")

    idx_path = str(tmp_path / "index.json")
    added = index.backfill(idx_path, projects_root=str(projects))
    assert added == 2

    data = index.load(idx_path)
    assert set(data["sessions"].keys()) == {"AAA", "BBB"}
    # The named one recovers its cwd from the JSONL envelope.
    assert data["sessions"]["AAA"]["project_path"] == "/Users/jl/proj/foo"
    assert data["sessions"]["AAA"]["name_cached"] == "planning-sprint14-custom"


def test_backfill_skips_existing(tmp_path):
    projects = tmp_path / "projects"
    proj_a = projects / "-x"
    proj_a.mkdir(parents=True)
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), proj_a / "AAA.jsonl")

    idx_path = str(tmp_path / "index.json")
    # Pre-seed AAA with a user-edited notes field.
    index.record_session(idx_path, session_id="AAA",
                         transcript_path=str(proj_a / "AAA.jsonl"),
                         cwd="/seeded/path")
    def add_notes(d: dict) -> dict:
        d["sessions"]["AAA"]["notes"] = "pre-existing"
        return d
    index.mutate(idx_path, add_notes)

    added = index.backfill(idx_path, projects_root=str(projects))
    assert added == 0
    # Existing entry untouched — notes preserved, project_path not clobbered.
    data = index.load(idx_path)
    assert data["sessions"]["AAA"]["notes"] == "pre-existing"
    assert data["sessions"]["AAA"]["project_path"] == "/seeded/path"


def test_backfill_handles_missing_projects_root(tmp_path):
    idx_path = str(tmp_path / "index.json")
    assert index.backfill(idx_path, projects_root=str(tmp_path / "nonexistent")) == 0


def test_project_label_plain_path():
    assert index._project_label("/Users/you/code/acme-app") == "acme-app"
    assert index._project_label("/Users/you/code/acme-app/") == "acme-app"


def test_project_label_collapses_worktree_to_parent():
    """Worktree sessions group under the parent repo, not the worktree leaf."""
    wt = "/Users/you/code/acme-app/.claude/worktrees/feature-login"
    assert index._project_label(wt) == "acme-app"
    # A multi-segment repo path still resolves to the repo's own basename.
    wt2 = "/Users/you/code/AcmeCorp/acme-api/.claude/worktrees/bugfix-cart"
    assert index._project_label(wt2) == "acme-api"


def test_record_session_worktree_label(tmp_path):
    transcript = str(tmp_path / "WT.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")
    cwd = "/Users/you/code/acme-app/.claude/worktrees/feature-login"
    index.record_session(idx_path, session_id="WT", transcript_path=transcript, cwd=cwd)
    s = index.load(idx_path)["sessions"]["WT"]
    # Label collapses to the parent repo; project_path keeps the worktree so
    # resume chdir's into the right working tree.
    assert s["project_label"] == "acme-app"
    assert s["project_path"] == cwd


def test_migrate_to_v2_moves_legacy_folders(tmp_path):
    from _pkg import folder_store
    idx_path = str(tmp_path / "index.json")
    fs_path = str(tmp_path / "folders.json")
    # Pre-existing v1 index with folders[].
    index.save(idx_path, {
        "version": 1,
        "folders": ["audits/q1", "planning"],
        "sessions": {},
    })
    index.migrate_to_v2(idx_path, fs_path)

    new_idx = index.load(idx_path)
    assert new_idx["version"] == 2
    assert "folders" not in new_idx
    assert new_idx["sessions"] == {}

    fs_data = folder_store.load(fs_path)
    assert fs_data["projects"]["(unfiled)"] == ["audits/q1", "planning"] or \
           sorted(fs_data["projects"]["(unfiled)"]) == ["audits/q1", "planning"]


def test_migrate_to_v2_is_idempotent(tmp_path):
    from _pkg import folder_store
    idx_path = str(tmp_path / "index.json")
    fs_path = str(tmp_path / "folders.json")
    index.save(idx_path, {"version": 1, "folders": ["a"], "sessions": {}})
    index.migrate_to_v2(idx_path, fs_path)
    # Second call is a no-op.
    index.migrate_to_v2(idx_path, fs_path)
    assert index.load(idx_path)["version"] == 2
    assert folder_store.load(fs_path)["projects"]["(unfiled)"] == ["a"]


def test_migrate_to_v2_v1_no_folders_field(tmp_path):
    """A v1 index with no folders[] key still bumps to v2 without touching the store."""
    from _pkg import folder_store
    idx_path = str(tmp_path / "index.json")
    fs_path = str(tmp_path / "folders.json")
    index.save(idx_path, {"version": 1, "sessions": {}})
    index.migrate_to_v2(idx_path, fs_path)
    assert index.load(idx_path)["version"] == 2
    # Folder store file not created when nothing to migrate.
    import os as _os
    assert not _os.path.exists(fs_path)
