"""Unit tests for the leased-ground location guard (root_guard.decide).

decide(payload, config_path, live_path) -> deny-reason str | None (allow).
Fixtures build a REAL git repo with a managed worktree at
<repo>/.claude/worktrees/wt1 (project_id needs git; the worktrees carve-out
needs the real layout).
"""
import os
import subprocess

from _pkg import live, project_id, queue_config, root_guard

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _run(cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, env=_GIT_ENV,
                   capture_output=True)


def repo_with_worktree(tmp_path):
    """A committed repo plus a managed worktree under .claude/worktrees/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q"], repo)
    (repo / "f.txt").write_text("x")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-qm", "init"], repo)
    wt = repo / ".claude" / "worktrees" / "wt1"
    _run(["git", "worktree", "add", "-q", str(wt), "-b", "wt1"], repo)
    return repo, wt


def shared_root_config(tmp_path, repo):
    """Overlay-shaped root-dir resource named 'root' (no guard field at all)."""
    cfg = str(tmp_path / "qc.json")
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        cfg, project_id=pid, display_path=str(repo), resource_id="root",
        resource={"kind": "root-dir", "path": str(repo),
                  "run_in": "root", "acquire": "command", "release": "command",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out"})
    return cfg


def register(tmp_path, sid, cwd):
    """Record a live session whose registered cwd is `cwd`."""
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id=sid,
                      cwd=str(cwd), pid=os.getpid())
    return lp


def edit_payload(file_path, cwd, sid="S1", tool="Edit"):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {key: str(file_path)},
            "cwd": str(cwd), "session_id": sid}


# --- resolution / classification ---

def test_allows_when_project_has_no_root_resource(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = str(tmp_path / "qc.json")          # empty config
    lp = register(tmp_path, "S1", wt)
    p = edit_payload(repo / "f.txt", wt)
    assert root_guard.decide(p, cfg, lp) is None


def test_allows_when_cwd_is_not_a_repo(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", tmp_path)  # registered outside any repo
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    p = edit_payload(outside / "x.txt", outside)
    assert root_guard.decide(p, cfg, lp) is None


def test_root_session_edits_root_freely(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", repo)      # session registered IN root
    p = edit_payload(repo / "f.txt", repo)
    assert root_guard.decide(p, cfg, lp) is None


def test_root_session_in_subdir_still_counts_as_root(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    sub = repo / "app"
    sub.mkdir()
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", sub)
    p = edit_payload(repo / "f.txt", sub)
    assert root_guard.decide(p, cfg, lp) is None


# --- Edit/Write/NotebookEdit denies ---

def test_worktree_session_edit_into_root_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    p = edit_payload(repo / "f.txt", wt)
    reason = root_guard.decide(p, cfg, lp)
    assert reason is not None
    assert "queue-run --resource root" in reason
    assert "worktree" in reason.lower()


def test_worktree_session_edit_in_own_worktree_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    p = edit_payload(wt / "f.txt", wt)       # under <root>/.claude/worktrees/
    assert root_guard.decide(p, cfg, lp) is None


def test_write_and_notebookedit_also_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    for tool in ("Write", "NotebookEdit"):
        p = edit_payload(repo / "new.txt", wt, tool=tool)
        assert root_guard.decide(p, cfg, lp) is not None, tool


def test_relative_file_path_resolves_against_call_cwd(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    # call cwd is the ROOT (drifted); a relative path lands inside root.
    p = {"tool_name": "Edit", "tool_input": {"file_path": "f.txt"},
         "cwd": str(repo), "session_id": "S1"}
    assert root_guard.decide(p, cfg, lp) is not None


def test_unguarded_tool_names_are_ignored(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    p = {"tool_name": "Read", "tool_input": {"file_path": str(repo / "f.txt")},
         "cwd": str(wt), "session_id": "S1"}
    assert root_guard.decide(p, cfg, lp) is None


def test_unregistered_session_falls_back_to_payload_cwd(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = str(tmp_path / "live.json")         # empty registry, never written
    # cwd says worktree -> deny applies even with no registry entry.
    p = edit_payload(repo / "f.txt", wt, sid="UNKNOWN")
    assert root_guard.decide(p, cfg, lp) is not None


def test_symlinked_root_path_still_matches(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    link = tmp_path / "link"
    os.symlink(repo, link)
    # Config stores the SYMLINK path; the edit uses the real path.
    cfg = str(tmp_path / "qc.json")
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        cfg, project_id=pid, display_path=str(link), resource_id="root",
        resource={"kind": "root-dir", "path": str(link),
                  "run_in": "root", "acquire": "command", "release": "command",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out"})
    lp = register(tmp_path, "S1", wt)
    p = edit_payload(repo / "f.txt", wt)
    assert root_guard.decide(p, cfg, lp) is not None


def bash_payload(cmd, cwd, sid="S1"):
    return {"tool_name": "Bash", "tool_input": {"command": cmd},
            "cwd": str(cwd), "session_id": sid}


# --- Bash: mention = deny ---

def test_bash_mentioning_root_denied_with_rewrite(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"cp build.xml {repo}/build.xml"
    reason = root_guard.decide(bash_payload(cmd, wt), cfg, lp)
    assert reason is not None
    assert f"session-explorer queue-run --resource root -- {cmd}" in reason
    assert "Read tool" in reason


def test_bash_innocent_command_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(
        bash_payload("phpunit --testsuite unit", wt), cfg, lp) is None


def test_bash_compound_mention_suggests_bash_c_wrap(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"cp a {repo}/a && cp b {repo}/b"
    reason = root_guard.decide(bash_payload(cmd, wt), cfg, lp)
    assert reason is not None
    # Shell-operator commands must be wrapped whole so every part runs leased.
    assert "queue-run --resource root -- bash -c " in reason


def test_bash_realpath_spelling_of_symlinked_config_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    link = tmp_path / "link"
    os.symlink(repo, link)
    cfg = str(tmp_path / "qc.json")
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        cfg, project_id=pid, display_path=str(link), resource_id="root",
        resource={"kind": "root-dir", "path": str(link),
                  "run_in": "root", "acquire": "command", "release": "command",
                  "command_acquire": "session-explorer queue-overlay in",
                  "command_release": "session-explorer queue-overlay out"})
    lp = register(tmp_path, "S1", wt)
    cmd = f"touch {os.path.realpath(str(repo))}/x"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None


# --- Bash: parent-climb from a managed worktree ---

def test_bash_climb_from_managed_worktree_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    reason = root_guard.decide(
        bash_payload("cp x ../../../somefile", wt), cfg, lp)
    assert reason is not None


def test_bash_single_parent_step_allowed(tmp_path):
    # One `..` from a managed worktree only reaches .claude/worktrees — shared
    # but harmless; the rule keys on `../..` (two-plus steps).
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(
        bash_payload("ls ../other-worktree", wt), cfg, lp) is None


def test_bash_climb_rule_not_applied_to_external_worktree(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    ext = tmp_path / "ext-wt"
    _run(["git", "worktree", "add", "-q", str(ext), "-b", "ext"], repo)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", ext)
    # Climbing from an external worktree does not lexically reach the root.
    assert root_guard.decide(
        bash_payload("cat ../../notes.txt", ext), cfg, lp) is None


# --- Bash: cd-drift ---

def test_cd_drift_into_root_denied_even_for_innocent_command(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)     # session HOME is the worktree
    reason = root_guard.decide(
        bash_payload("npm install", repo), cfg, lp)  # call cwd = root
    assert reason is not None
    assert "drifted" in reason.lower() or "working directory" in reason.lower()


def test_root_session_running_in_root_is_not_drift(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", repo)   # session HOME is the root
    assert root_guard.decide(
        bash_payload("npm install", repo), cfg, lp) is None


def test_registered_worktree_session_escaping_repo_still_guarded(tmp_path):
    # Session registered in the worktree, but the call cwd wandered to an
    # unrelated dir: project resolution falls back to the registered home.
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    outside = tmp_path / "outside"
    outside.mkdir()
    cmd = f"cp x {repo}/x"
    assert root_guard.decide(bash_payload(cmd, outside), cfg, lp) is not None
