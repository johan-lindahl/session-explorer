"""Unit tests for the Phase-3 awareness/guard logic (queue_awareness)."""
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
        resource={
            "kind": "root-dir", "path": str(repo),
            "guard": [{"exe": "docker", "sub": ["compose", "up"]}],
            "run_in": "root", "acquire": "sync", "release": "none",
            "sync": {"delete": True, "exclude": ["/.git"], "protect": ["/.git"]},
        })
    return pid


def test_session_context_none_when_not_opted_in(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    assert qa.session_context(str(cfg), str(repo)) is None


def test_session_context_none_outside_git(tmp_path):
    cfg = tmp_path / "queue-config.json"
    assert qa.session_context(str(cfg), str(tmp_path)) is None


def test_session_context_lists_resources_and_cooperation(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    text = qa.session_context(str(cfg), str(repo))
    assert text is not None
    assert "root" in text and "root-dir" in text
    assert "docker compose up" in text            # rendered guard label
    assert "queue-run" in text                     # the lever
    assert "queue-status" in text


def test_guard_reason_fires_on_guarded_command(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "docker compose up -d", str(repo))
    assert reason is not None
    assert "queue-run --resource root --" in reason
    assert "docker compose up -d" in reason


def test_guard_reason_silent_on_unguarded_command(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    assert qa.guard_reason(str(cfg), "docker ps", str(repo)) is None
    assert qa.guard_reason(str(cfg), "npm run setup", str(repo)) is None


def test_guard_reason_skips_already_wrapped_queue_run(tmp_path):
    # A properly-wrapped command is a single argv segment whose exe is
    # 'session-explorer' (docker sits after --), so the parsed-argv matcher
    # naturally does not fire - no substring check, no deny-loop.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    cmd = "session-explorer queue-run --resource root -- docker compose up"
    assert qa.guard_reason(str(cfg), cmd, str(repo)) is None


def test_guard_reason_no_substring_bypass(tmp_path):
    # 'queue-run' appears as a literal arg, but the second segment really does run
    # docker directly - parsed-argv matching must still fire (no substring escape).
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "echo queue-run && docker compose up -d",
                             str(repo))
    assert reason is not None
    assert "queue-run --resource root --" in reason


def test_guard_reason_wraps_compound_command_in_bash_c(tmp_path):
    # A compound command must be wrapped whole in `bash -c <quoted>` so the
    # operator runs INSIDE the lease. The broken form would re-embed the raw text
    # after `--`, letting the agent's outer shell split off the trailing segment.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "cd app && docker compose up -d", str(repo))
    assert reason is not None
    assert "-- bash -c " in reason
    # the whole compound is a single quoted arg, not split after `--`
    assert "-- cd app &&" not in reason
    assert "'cd app && docker compose up -d'" in reason


def test_guard_reason_wraps_newline_separated_command(tmp_path):
    # A newline is a shell command separator too: shlex folds it into one matchable
    # segment, but the agent's shell would split it. The whole thing must be wrapped
    # in bash -c so the trailing line can't run outside the lease.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "docker compose up -d\necho done", str(repo))
    assert reason is not None
    assert "-- bash -c " in reason
    assert "-- docker compose up -d\necho done" not in reason


def test_guard_reason_simple_command_unwrapped(tmp_path):
    # A single simple command (no shell operators) is suggested verbatim - no
    # noisy bash -c wrapper.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    reason = qa.guard_reason(str(cfg), "docker compose up -d", str(repo))
    assert "queue-run --resource root -- docker compose up -d" in reason
    assert "bash -c" not in reason


def test_guard_reason_fails_open_on_guard_match_blind_spots(tmp_path):
    # guard_match intentionally returns NO match for command substitution,
    # backticks, heredocs, no-space operators, and wrapper bodies (bash -c). Those
    # commands are ALLOWED (fail open), never denied-and-wrapped — guard_reason
    # never even reaches _redirect_command for them. The SessionStart awareness
    # injection (not this hook) is the backstop for these inherited blind spots.
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    _add_root_resource(cfg, repo)
    for cmd in [
        "docker compose up $(echo -d)",     # command substitution
        "docker compose up `echo -d`",       # backticks
        "bash -c 'docker compose up -d'",    # wrapper body hides the command
        "docker compose up&&echo done",      # no-space operator: not lexed
    ]:
        assert qa.guard_reason(str(cfg), cmd, str(repo)) is None, cmd


def test_guard_reason_none_when_not_opted_in(tmp_path):
    repo = _git_repo(tmp_path)
    cfg = tmp_path / "queue-config.json"
    assert qa.guard_reason(str(cfg), "docker compose up", str(repo)) is None


def test_context_mentions_overlay_and_experimental(tmp_path):
    from _pkg import project_id as _pid, queue_config as qc, queue_awareness as qa_local
    root = tmp_path / "main"; root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    cfg = str(tmp_path / "qc.json")
    pid = _pid.project_id(str(root))
    qc.add_resource(cfg, project_id=pid, display_path=str(root),
                    resource_id="ov",
                    resource={"kind": "root-dir", "path": str(root),
                              "run_in": "root", "acquire": "command",
                              "release": "command",
                              "command_acquire": "session-explorer queue-overlay in",
                              "command_release": "session-explorer queue-overlay out"})
    text = qa_local.session_context(cfg, str(root))
    assert text is not None
    assert "experimental" in text.lower()
    assert "queue-run" in text
    assert "git restore" in text or "hand-roll" in text
