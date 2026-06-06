from _pkg import queue_detect


def test_snapshot_lists_top_level_entries(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "sub").mkdir()
    snap = queue_detect.top_level_snapshot(str(tmp_path), exclude={".git"})
    assert "a.txt" in snap and "sub" in snap


def test_snapshot_excludes_protected(tmp_path):
    (tmp_path / ".env").write_text("secret")
    (tmp_path / "a.txt").write_text("1")
    snap = queue_detect.top_level_snapshot(str(tmp_path), exclude={".env"})
    assert ".env" not in snap and "a.txt" in snap


def test_snapshot_excludes_glob_protect_pattern(tmp_path):
    # A protect pattern like '.env.*' must exclude .env.local (Finding 3).
    (tmp_path / ".env.local").write_text("secret")
    (tmp_path / ".env.prod").write_text("secret")
    (tmp_path / "a.txt").write_text("1")
    snap = queue_detect.top_level_snapshot(str(tmp_path), exclude={".env.*"})
    assert ".env.local" not in snap and ".env.prod" not in snap
    assert "a.txt" in snap


def test_changed_detects_new_entry(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    before = queue_detect.top_level_snapshot(str(tmp_path), exclude=set())
    (tmp_path / "b.txt").write_text("2")
    after = queue_detect.top_level_snapshot(str(tmp_path), exclude=set())
    assert queue_detect.changed(before, after) is True


def test_changed_false_when_identical(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    s = queue_detect.top_level_snapshot(str(tmp_path), exclude=set())
    assert queue_detect.changed(s, dict(s)) is False
