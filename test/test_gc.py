import os
import tempfile
from datetime import datetime, timedelta, timezone

from _pkg import index as _index
from _pkg import gc as _gc


def _iso(dt):
    return dt.isoformat()


def _setup(sessions):
    """Create a tmp index with the given sessions dict and back each entry with
    a real JSONL on disk (unless transcript_path is left out)."""
    fd, idx = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    created_files = []
    for sid, entry in sessions.items():
        if entry.get("_make_jsonl", True):
            fd, jsonl = tempfile.mkstemp(suffix=".jsonl")
            os.close(fd)
            with open(jsonl, "w") as f:
                f.write('{"type":"user"}\n')
            entry["transcript_path"] = jsonl
            created_files.append(jsonl)
        entry.pop("_make_jsonl", None)
    _index.save(idx, {"version": 2, "sessions": sessions})
    return idx, created_files


def _cleanup(idx, files):
    for p in [idx, idx + ".lock", *files]:
        if os.path.exists(p):
            os.unlink(p)


NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


def _age_file(path, seconds):
    """Set a file's mtime to `seconds` before NOW.

    Must be relative to NOW (the clock injected into collect_garbage), not the
    real wall clock — otherwise a file 'aged' a few hours back ends up with an
    mtime LATER than NOW, so now - mtime goes negative and trips the <60s
    live-session guard, masking the deletion under test.
    """
    past = NOW.timestamp() - seconds
    os.utime(path, (past, past))


def test_old_unnamed_session_is_deleted():
    old = _iso(NOW - timedelta(days=45))
    idx, files = _setup({"sid": {"name_cached": None, "last_active_at": old}})
    try:
        # Make the JSONL look idle (not a live session).
        _age_file(files[0], 3600)
        result = _gc.collect_garbage(idx, now=NOW)
        assert result["removed"] == ["sid"]
        assert not os.path.exists(files[0])
        assert "sid" not in _index.load(idx)["sessions"]
    finally:
        _cleanup(idx, files)


def test_named_old_session_is_kept():
    old = _iso(NOW - timedelta(days=45))
    idx, files = _setup({"sid": {"name_cached": "team/keepme", "last_active_at": old}})
    try:
        _age_file(files[0], 3600)
        result = _gc.collect_garbage(idx, now=NOW)
        assert result["removed"] == []
        assert os.path.exists(files[0])
        assert "sid" in _index.load(idx)["sessions"]
    finally:
        _cleanup(idx, files)


def test_unnamed_recent_session_is_kept():
    recent = _iso(NOW - timedelta(days=5))
    idx, files = _setup({"sid": {"name_cached": None, "last_active_at": recent}})
    try:
        _age_file(files[0], 3600)
        result = _gc.collect_garbage(idx, now=NOW)
        assert result["removed"] == []
        assert os.path.exists(files[0])
    finally:
        _cleanup(idx, files)


def test_unnamed_old_but_recently_modified_jsonl_is_kept():
    """Live-session guard: JSONL mtime within 60s means a session may be active."""
    old = _iso(NOW - timedelta(days=45))
    idx, files = _setup({"sid": {"name_cached": None, "last_active_at": old}})
    try:
        _age_file(files[0], 5)  # modified 5s ago -> looks live
        result = _gc.collect_garbage(idx, now=NOW)
        assert result["removed"] == []
        assert result["skipped_live"] == 1
        assert os.path.exists(files[0])
    finally:
        _cleanup(idx, files)


def test_unnamed_old_but_flocked_jsonl_is_kept():
    """Live-session guard: an active flock on the JSONL means skip."""
    import fcntl
    old = _iso(NOW - timedelta(days=45))
    idx, files = _setup({"sid": {"name_cached": None, "last_active_at": old}})
    try:
        _age_file(files[0], 3600)  # idle by mtime...
        holder = open(files[0], "r")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)  # ...but locked
        try:
            result = _gc.collect_garbage(idx, now=NOW)
            assert result["removed"] == []
            assert result["skipped_live"] == 1
            assert os.path.exists(files[0])
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()
    finally:
        _cleanup(idx, files)


def test_dry_run_deletes_nothing():
    old = _iso(NOW - timedelta(days=45))
    idx, files = _setup({"sid": {"name_cached": None, "last_active_at": old}})
    try:
        _age_file(files[0], 3600)
        result = _gc.collect_garbage(idx, now=NOW, dry_run=True)
        assert result["removed"] == ["sid"]
        assert result["dry_run"] is True
        assert os.path.exists(files[0])  # untouched
        assert "sid" in _index.load(idx)["sessions"]  # untouched
    finally:
        _cleanup(idx, files)


def test_retention_days_override():
    age = _iso(NOW - timedelta(days=10))
    idx, files = _setup({"sid": {"name_cached": None, "last_active_at": age}})
    try:
        _age_file(files[0], 3600)
        # 10 days old, retention 7 -> eligible.
        result = _gc.collect_garbage(idx, now=NOW, retention_days=7)
        assert result["removed"] == ["sid"]
    finally:
        _cleanup(idx, files)


def test_missing_last_active_at_falls_back_to_mtime():
    """No last_active_at field: use the JSONL mtime to judge age."""
    idx, files = _setup({"sid": {"name_cached": None}})  # no last_active_at
    try:
        _age_file(files[0], 45 * 86400)  # 45 days old by mtime
        result = _gc.collect_garbage(idx, now=NOW)
        assert result["removed"] == ["sid"]
        assert not os.path.exists(files[0])
    finally:
        _cleanup(idx, files)


def test_missing_jsonl_row_is_pruned():
    old = _iso(NOW - timedelta(days=45))
    idx, files = _setup({"sid": {"name_cached": None, "last_active_at": old}})
    try:
        os.unlink(files[0])  # JSONL gone out-of-band
        result = _gc.collect_garbage(idx, now=NOW)
        assert result["removed"] == ["sid"]
        assert "sid" not in _index.load(idx)["sessions"]
    finally:
        _cleanup(idx, files)
