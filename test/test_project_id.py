import os
import subprocess

import pytest

from _pkg import project_id


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A real git repo with one commit at <tmp>/main."""
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("x")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-qm", "init")
    return root


def test_project_id_is_stable_and_hex16(repo):
    pid = project_id.project_id(str(repo))
    assert pid is not None
    assert len(pid) == 16 and all(c in "0123456789abcdef" for c in pid)
    assert project_id.project_id(str(repo)) == pid  # stable


def test_subdir_resolves_to_same_id(repo):
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    assert project_id.project_id(str(sub)) == project_id.project_id(str(repo))


def test_worktree_shares_parent_repo_id(repo):
    wt = repo.parent / "feat"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert project_id.project_id(str(wt)) == project_id.project_id(str(repo))


def test_non_repo_returns_none(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert project_id.project_id(str(plain)) is None


def test_main_root_is_main_working_tree_not_worktree(repo):
    wt = repo.parent / "feat"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert project_id.main_root(str(wt)) == os.path.realpath(str(repo))
    assert project_id.main_root(str(repo)) == os.path.realpath(str(repo))


def test_is_root_cwd_true_for_root_and_subdir_false_for_worktree(repo):
    main = project_id.main_root(str(repo))
    sub = repo / "src"
    sub.mkdir()
    wt = repo.parent / "feat"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert project_id.is_root_cwd(str(repo), main) is True
    assert project_id.is_root_cwd(str(sub), main) is True       # subdir of root
    assert project_id.is_root_cwd(str(wt), main) is False       # worktree session
