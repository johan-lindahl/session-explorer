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


# --- the queue-* allowlist ---

def test_queue_run_mentioning_root_is_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"session-explorer queue-run --resource root -- cp x {repo}/app/x"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is None


def test_queue_run_with_quoted_bash_c_body_is_allowed(tmp_path):
    # The exact shape our own deny message suggests: operators live INSIDE the
    # quoted body, which shlex keeps as one token.
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = ("session-explorer queue-run --resource root -- "
           f"bash -c 'cp a {repo}/a && cp b {repo}/b'")
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is None


def test_queue_status_is_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(
        bash_payload("session-explorer queue-status", wt), cfg, lp) is None


def test_env_prefix_on_queue_run_is_allowed(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"FOO=1 session-explorer queue-run --resource root -- ls {repo}"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is None


def test_compound_smuggle_after_queue_run_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = (f"session-explorer queue-status && cp x {repo}/x")
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None


def test_semicolon_without_spaces_smuggle_denied(tmp_path):
    # shlex.split would keep 'queue-status;cp' as one token; the
    # punctuation_chars lexer must split it and catch the compound.
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"session-explorer queue-status;cp x {repo}/x"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None


def test_command_substitution_in_queue_invocation_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"session-explorer queue-run --resource $(echo root) -- ls {repo}"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None


def test_echo_queue_run_decoy_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    cmd = f"echo session-explorer queue-run && rm {repo}/f.txt"
    assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None


# --- adversarial / corrupt-input robustness (decide must never raise) ---

def test_non_dict_tool_input_does_not_raise(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    for bad in ("rm -rf /", ["x"], 7, None):
        p = {"tool_name": "Bash", "tool_input": bad,
             "cwd": str(wt), "session_id": "S1"}
        assert root_guard.decide(p, cfg, lp) is None
        p = {"tool_name": "Edit", "tool_input": bad,
             "cwd": str(wt), "session_id": "S1"}
        assert root_guard.decide(p, cfg, lp) is None


def test_corrupt_live_registry_does_not_raise(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = tmp_path / "live.json"
    for corrupt in ('[1, 2]', '{"sessions": "oops"}', '{"sessions": {"S1": 3}}'):
        lp.write_text(corrupt)
        p = edit_payload(repo / "f.txt", wt)   # worktree cwd -> still denies
        assert root_guard.decide(p, str(cfg), str(lp)) is not None


def test_non_string_resource_path_does_not_raise(tmp_path):
    import json
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    data = json.loads(open(cfg).read())
    pid = next(iter(data["projects"]))
    data["projects"][pid]["resources"]["root"]["path"] = 1
    open(cfg, "w").write(json.dumps(data))
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(edit_payload(repo / "f.txt", wt), str(cfg), lp) is None


# --- deny-loop protection: rewrite must itself pass the allowlist ---

def test_rewrite_for_simple_and_compound_commands_is_itself_allowlisted(tmp_path):
    # The recovery instruction must never dead-loop: whatever we suggest for
    # quotable commands must pass _is_queue_invocation.
    for cmd in ("cp x /repo/y", "cp a /repo/a && cp b /repo/b",
                "make build > /repo/log"):
        suggestion = root_guard._rewrite("root", cmd)
        assert root_guard._is_queue_invocation(suggestion), suggestion


def test_rewrite_for_unquotable_commands_suggests_script_route(tmp_path):
    for cmd in ("echo `id`", "cp $(ls) /repo/", "line1\nline2"):
        suggestion = root_guard._rewrite("root", cmd)
        assert "run.sh" in suggestion
        assert "bash -c" not in suggestion


# --- own-worktree and sibling-dir path carve-outs ---

def test_absolute_path_into_own_worktree_is_not_a_root_mention(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(
        bash_payload(f"pytest {wt}/test -q", wt), cfg, lp) is None


def test_sibling_directory_is_not_a_root_mention(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    backup = tmp_path / "repo-backup"
    backup.mkdir()
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(
        bash_payload(f"ls {backup}/x", wt), cfg, lp) is None


def test_root_mention_followed_by_subpath_still_denied(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    assert root_guard.decide(
        bash_payload(f"touch {repo}/app/etc/x", wt), cfg, lp) is not None
    assert root_guard.decide(
        bash_payload(f"rm -rf {repo}", wt), cfg, lp) is not None


# --- regression: glob-safe boundary and un-climbable worktree carve-out ---

def test_glob_and_brace_after_root_are_mentions(tmp_path):
    # Regression: `rm -rf <root>*` must deny — glob chars don't extend the
    # filename, they expand to the root and its siblings.
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    for suffix in ("*", "?", "{,/x}"):
        cmd = f"rm -rf {repo}{suffix}"
        assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is not None, cmd


def test_filename_continuation_is_still_not_a_mention(tmp_path):
    repo, wt = repo_with_worktree(tmp_path)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", wt)
    for cmd in (f"ls {repo}-backup/x", f"cat {repo}.bak", f"ls {repo}_old"):
        assert root_guard.decide(bash_payload(cmd, wt), cfg, lp) is None, cmd


def test_worktree_carveout_not_climbable(tmp_path):
    # Regression: an EXTERNAL worktree session must not climb back into root
    # through the worktrees carve-out.
    repo, wt = repo_with_worktree(tmp_path)
    ext = tmp_path / "ext-wt"
    _run(["git", "worktree", "add", "-q", str(ext), "-b", "ext2"], repo)
    cfg = shared_root_config(tmp_path, repo)
    lp = register(tmp_path, "S1", ext)
    cmd = f"echo hi > {repo}/.claude/worktrees/../../app/etc/x"
    assert root_guard.decide(bash_payload(cmd, (wt := ext)), cfg, lp) is not None
    # The legit carve-out still works: absolute path INTO a worktree, no `..`.
    ok = f"pytest {repo}/.claude/worktrees/wt1/test -q"
    assert root_guard.decide(bash_payload(ok, ext), cfg, lp) is None


# --- hot-path: no git fork when no config ---

def test_no_config_means_no_git_fork(tmp_path, monkeypatch):
    # With an empty config the guard must not even resolve the project.
    calls = []
    monkeypatch.setattr(root_guard._pid, "project_id",
                        lambda cwd: calls.append(cwd) or None)
    p = {"tool_name": "Bash", "tool_input": {"command": "ls"},
         "cwd": str(tmp_path), "session_id": "S1"}
    assert root_guard.decide(p, str(tmp_path / "qc.json"),
                             str(tmp_path / "live.json")) is None
    assert calls == []
