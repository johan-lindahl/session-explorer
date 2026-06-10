"""Tests for the SessionStart awareness hint (queue_awareness)."""
import subprocess

from _pkg import queue_awareness as qa
from _pkg import project_id, queue_config


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def _add_root_resource(cfg_path, repo):
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        str(cfg_path), project_id=pid, display_path=str(repo),
        resource_id="root",
        resource={"kind": "root-dir", "path": str(repo),
                  "run_in": "root", "acquire": "command", "release": "command",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out"})
    return pid


def test_session_context_none_when_not_opted_in(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    assert qa.session_context(str(cfg), str(repo)) is None


def test_session_context_none_outside_git(tmp_path):
    cfg = tmp_path / "queue-config.json"
    assert qa.session_context(str(cfg), str(tmp_path)) is None


def test_session_context_is_a_short_wall_hint(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    text = qa.session_context(str(cfg), str(repo))
    assert text is not None
    assert str(repo) in text                       # names the root path
    assert "write-blocked" in text.lower()         # states the wall
    assert "queue-run --resource root --" in text  # the one door
    assert "queue-status" in text
    # The old cooperation contract is gone.
    assert "leased ground" not in text.lower()
    assert "guarded commands" not in text.lower()
    assert len(text.splitlines()) <= 6


def test_session_context_lists_non_root_resources_one_liner(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    pid = _add_root_resource(cfg, repo)
    queue_config.add_resource(
        str(cfg), project_id=pid, display_path=str(repo), resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    text = qa.session_context(str(cfg), str(repo))
    assert "db" in text


def test_session_context_for_non_root_only_project(tmp_path):
    # Back-compat: a project with only a port/device resource still gets a
    # hint (serialize via queue-run), just no root-wall paragraph.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        str(cfg), project_id=pid, display_path=str(repo), resource_id="sim",
        resource={"kind": "device", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    text = qa.session_context(str(cfg), str(repo))
    assert text is not None and "sim" in text and "queue-run" in text
