from _pkg import qsync


def test_filters_anchor_and_dedupe():
    f = qsync.build_filters(exclude=["/.git", "node_modules"],
                            protect=["/.git", "/.env"])
    # exclude + protect unioned, each rendered as an anchored exclude filter
    assert "--filter=exclude /.git" in f
    assert "--filter=exclude /node_modules" in f
    assert "--filter=exclude /.env" in f
    # /.git appears once despite being in both lists
    assert f.count("--filter=exclude /.git") == 1


def test_rsync_command_shape():
    cmd = qsync.rsync_command("/wt", "/root", exclude=["/.git"], protect=["/.env"],
                              dry_run=False)
    assert cmd[0] == "rsync"
    assert "-a" in cmd and "--delete" in cmd
    assert "--delete-excluded" not in cmd     # never; excluded must survive
    assert cmd[-2] == "/wt/" and cmd[-1] == "/root/"   # trailing slashes


def test_dry_run_adds_itemize_flags():
    cmd = qsync.rsync_command("/wt", "/root", exclude=[], protect=[], dry_run=True)
    assert "-n" in cmd and "-i" in cmd


def test_trailing_slashes_normalized():
    cmd = qsync.rsync_command("/wt/", "/root/", exclude=[], protect=[], dry_run=False)
    assert cmd[-2] == "/wt/" and cmd[-1] == "/root/"


import os
import subprocess

import pytest


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _repo(tmp_path, name):
    r = tmp_path / name
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    return r


def test_parse_deletions_from_itemized_output():
    out = (
        "*deleting   stale.txt\n"
        ">f+++++++++ new.txt\n"
        "*deleting   cache/blob\n"
        "cd+++++++++ dir/\n"
    )
    assert qsync.parse_deletions(out) == ["stale.txt", "cache/blob"]


def test_classify_separates_tracked_from_untracked(tmp_path):
    root = _repo(tmp_path, "root")
    (root / "tracked.txt").write_text("a")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "c")
    (root / "untracked.txt").write_text("b")
    (root / ".gitignore").write_text("ignored.txt\n")
    (root / "ignored.txt").write_text("c")
    would_delete = ["tracked.txt", "untracked.txt", "ignored.txt"]
    needs = qsync.classify_candidates(str(root), would_delete)
    # tracked is auto-allowed (legitimate branch diff); the rest need a decision
    assert set(needs) == {"untracked.txt", "ignored.txt"}


def test_unclassified_excludes_auto_protect_and_already_classified(tmp_path):
    root = _repo(tmp_path, "root")
    (root / ".env").write_text("SECRET=1")
    (root / "build").mkdir()
    (root / "build" / "out").write_text("x")
    (root / "certs").mkdir()
    (root / "certs" / "key.pem").write_text("k")
    would_delete = [".env", "build/out", "certs/key.pem"]
    unresolved = qsync.unclassified(
        str(root), would_delete,
        protect=["/certs"], allow_delete=["/build"])
    # .env -> auto-protected; build -> allow_delete; certs -> protect; none left
    assert unresolved == []
    # Now drop the classifications: build/out + certs/key.pem must surface.
    unresolved2 = qsync.unclassified(str(root), would_delete,
                                     protect=[], allow_delete=[])
    assert set(unresolved2) == {"build/out", "certs/key.pem"}


def test_sandbox_marker_roundtrip(tmp_path):
    qdir = str(tmp_path / "q")
    assert qsync.in_sandbox(qdir) is False
    qsync.mark_sandbox(qdir)
    assert qsync.in_sandbox(qdir) is True


def test_dry_run_fails_closed_on_rsync_error(tmp_path):
    # A non-existent source makes rsync exit non-zero; we must RAISE, not
    # return [] (which would silently bypass the delete-classification gate).
    with pytest.raises(qsync.SyncDryRunError):
        qsync.dry_run_deletions(str(tmp_path / "does-not-exist"),
                                str(tmp_path), exclude=[], protect=[])
