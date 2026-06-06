import os
import subprocess
from datetime import datetime, timezone

from _pkg import exclusive, live


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _repo(tmp_path):
    r = tmp_path / "main"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("x")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-qm", "init")
    return r


T0 = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)


def test_no_live_session_means_sandbox_free(tmp_path):
    root = _repo(tmp_path)
    lp = str(tmp_path / "live.json")
    assert exclusive.live_root_session(lp, str(root), now=T0) is None


def test_live_root_session_detected(tmp_path):
    root = _repo(tmp_path)
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1",
                      cwd=str(root), pid=os.getpid(), now=T0)
    hit = exclusive.live_root_session(lp, str(root), now=T0)
    assert hit is not None and hit["sid"] == "s1"


def test_subdir_session_counts_as_root(tmp_path):
    root = _repo(tmp_path)
    sub = root / "src"
    sub.mkdir()
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s2",
                      cwd=str(sub), pid=os.getpid(), now=T0)
    assert exclusive.live_root_session(lp, str(root), now=T0) is not None


def test_worktree_session_does_not_count_as_root(tmp_path):
    root = _repo(tmp_path)
    wt = tmp_path / "feat"
    _git(root, "worktree", "add", "-q", str(wt))
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="w1",
                      cwd=str(wt), pid=os.getpid(), now=T0)
    assert exclusive.live_root_session(lp, str(root), now=T0) is None


def test_transition_guard_blocks_dirty_root(tmp_path):
    root = _repo(tmp_path)
    (root / "f.txt").write_text("modified")  # uncommitted change
    assert exclusive.transition_guard(str(root)) is not None


def test_transition_guard_passes_clean_root(tmp_path):
    root = _repo(tmp_path)
    assert exclusive.transition_guard(str(root)) is None
