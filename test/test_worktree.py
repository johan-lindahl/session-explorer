import os
import subprocess as sp

from _pkg import worktree


def _init_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    sp.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "--allow-empty", "-m", "init"], cwd=path, check=True)


def _make_worktree(repo):
    """Create repo + a real worktree at <repo>/.claude/worktrees/feat on branch
    worktree-feat. Returns the worktree path."""
    _init_git_repo(repo)
    wt = str(repo / ".claude" / "worktrees" / "feat")
    sp.run(["git", "-C", str(repo), "worktree", "add", "-b", "worktree-feat", wt],
           check=True, capture_output=True)
    return wt


def test_root_of_and_marker(tmp_path):
    p = str(tmp_path / "repo" / ".claude" / "worktrees" / "x")
    assert worktree.root_of(p) == str(tmp_path / "repo")
    assert worktree.root_of(str(tmp_path / "plain")) is None
    assert worktree.root_of(None) is None
    assert worktree.root_of("") is None


def test_remove_clean_worktree_keeps_branch(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    assert worktree.remove(wt) == "removed"
    assert not os.path.isdir(wt)                      # directory gone
    branches = sp.run(["git", "-C", str(repo), "branch", "--list", "worktree-feat"],
                      capture_output=True, text=True).stdout
    assert "worktree-feat" in branches                # branch (work) preserved


def test_remove_dirty_worktree_is_refused(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    with open(os.path.join(wt, "dirty.txt"), "w") as f:  # untracked file
        f.write("uncommitted")
    assert worktree.remove(wt) == "dirty"
    assert os.path.isdir(wt)                           # nothing removed


def test_removable_true_for_clean_false_for_dirty(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    assert worktree.removable(wt) is True
    with open(os.path.join(wt, "u.txt"), "w") as f:
        f.write("x")
    assert worktree.removable(wt) is False


def test_removable_false_when_dir_missing(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    assert worktree.removable(str(repo / ".claude" / "worktrees" / "gone")) is False


def test_size_returns_human_string(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    s = worktree.size(wt)
    assert s and s[0].isdigit()                        # e.g. "12K", "4.0K"


def test_remove_then_recreate_round_trip(tmp_path):
    """Removal is reversible: after remove(), the recreate path restores a real
    working tree on the same branch."""
    from _pkg.tui import _recreate_worktree
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    assert worktree.remove(wt) == "removed"
    assert _recreate_worktree(wt, str(repo)) is True
    assert os.path.exists(os.path.join(wt, ".git"))
    out = sp.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                 capture_output=True, text=True).stdout
    assert "worktree-feat" in out


def _commit_in_worktree(wt):
    """Add a unique commit on the worktree's branch so `git branch -d` refuses."""
    with open(os.path.join(wt, "new.txt"), "w") as f:
        f.write("work")
    sp.run(["git", "-C", wt, "add", "."], check=True, capture_output=True)
    sp.run(["git", "-C", wt, "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-qm", "wip"], check=True, capture_output=True)


def test_purge_clean_merged_removes_dir_and_branch(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    assert worktree.purge(wt) == "removed"
    assert not os.path.isdir(wt)
    branches = sp.run(["git", "-C", str(repo), "branch", "--list", "worktree-feat"],
                      capture_output=True, text=True).stdout
    assert "worktree-feat" not in branches            # branch deleted (was merged)


def test_purge_unmerged_branch_kept(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    _commit_in_worktree(wt)                            # branch now has a unique commit
    assert worktree.purge(wt) == "removed_branch_kept"
    assert not os.path.isdir(wt)
    branches = sp.run(["git", "-C", str(repo), "branch", "--list", "worktree-feat"],
                      capture_output=True, text=True).stdout
    assert "worktree-feat" in branches                # branch kept (unmerged work)


def test_purge_dirty_leaves_everything(tmp_path):
    repo = tmp_path / "repo"
    wt = _make_worktree(repo)
    with open(os.path.join(wt, "dirty.txt"), "w") as f:
        f.write("uncommitted")
    assert worktree.purge(wt) == "dirty"
    assert os.path.isdir(wt)


def test_purge_non_worktree_path_is_error(tmp_path):
    assert worktree.purge(str(tmp_path)) == "error"
