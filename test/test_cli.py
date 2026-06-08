"""CLI smoke tests for the entry shim."""

import json
import os
import subprocess
import shutil

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_REPO_ROOT, "bin", "session-explorer")

_FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_version_flag_prints_version():
    from _pkg import __version__
    result = subprocess.run([_BIN, "--version"], capture_output=True, text=True)
    assert result.returncode == 0
    assert f"session-explorer {__version__}" in result.stdout


def test_help_when_no_args():
    result = subprocess.run([_BIN], capture_output=True, text=True)
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()


def test_index_record_via_cli(tmp_path, monkeypatch):
    transcript = tmp_path / "01ABC.jsonl"
    shutil.copy(os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = tmp_path / "index.json"

    monkeypatch.setenv("SESSION_EXPLORER_INDEX", str(idx_path))
    result = subprocess.run(
        [_BIN, "index", "--record", "01ABC", str(transcript), "/Users/jl/proj/foo"],
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_INDEX": str(idx_path)},
    )
    assert result.returncode == 0, result.stderr

    import json
    data = json.loads(idx_path.read_text())
    assert "01ABC" in data["sessions"]
    assert data["sessions"]["01ABC"]["name_cached"] == "planning-sprint14-custom"  # custom-title wins


def test_index_refresh_via_cli(tmp_path):
    transcript = tmp_path / "01ABC.jsonl"
    shutil.copy(os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = tmp_path / "index.json"
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(idx_path)}

    subprocess.run(
        [_BIN, "index", "--record", "01ABC", str(transcript), "/Users/jl/proj/foo"],
        check=True, env=env,
    )
    result = subprocess.run([_BIN, "index", "--refresh"], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr


def test_list_groups_by_project_and_folder(tmp_path):
    transcript = tmp_path / "01ABC.jsonl"
    shutil.copy(os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = tmp_path / "index.json"
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(idx_path)}
    subprocess.run(
        [_BIN, "index", "--record", "01ABC", str(transcript), "/Users/jl/proj/foo"],
        check=True, env=env,
    )
    result = subprocess.run([_BIN, "list"], capture_output=True, text=True, env=env)
    assert result.returncode == 0
    out = result.stdout
    assert "foo" in out                                     # project label
    # named.jsonl's custom-title is "planning-sprint14-custom" → root row (no /).
    assert "planning-sprint14-custom" in out
    # End-to-end token stat is plumbed through fmt_tokens to the row.
    assert "15K" in out or "15.2K" in out or "15234" in out


def test_list_no_sessions(tmp_path):
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(tmp_path / "absent.json")}
    result = subprocess.run([_BIN, "list"], capture_output=True, text=True, env=env)
    assert result.returncode == 0
    assert "no sessions" in result.stdout.lower()


def test_list_renders_slash_path_as_nested(tmp_path):
    """A session with a /-bearing name renders under its folder path in the list."""
    transcript = tmp_path / "02XYZ.jsonl"
    transcript.write_text(
        '{"type":"user","sessionId":"02XYZ","cwd":"/u/p/foo",'
        '"timestamp":"2026-05-27T10:00:00Z",'
        '"message":{"role":"user","content":"plan"}}\n'
        '{"type":"custom-title","customTitle":"planning/sprint14","sessionId":"02XYZ"}\n'
    )
    idx_path = tmp_path / "index.json"
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(idx_path)}
    subprocess.run(
        [_BIN, "index", "--record", "02XYZ", str(transcript), "/u/p/foo"],
        check=True, env=env,
    )
    result = subprocess.run([_BIN, "list"], capture_output=True, text=True, env=env)
    assert result.returncode == 0
    out = result.stdout
    assert "foo" in out
    # Folder header printed as a path, session indented under it.
    assert "planning/" in out
    assert "sprint14" in out
    # Folder header must precede the session row.
    assert out.index("planning/") < out.index("sprint14")


def test_launch_invokes_osascript_on_mac(tmp_path, monkeypatch):
    """Smoke test: `session-explorer launch` should attempt to spawn a new terminal."""
    # We run the binary in a subprocess where we can intercept by setting
    # SESSION_EXPLORER_DRY_RUN=1, which makes launcher.launch print the would-be
    # command and exit 0 without actually shelling out.
    # Redirect HOME so any tmux conf / persist-flag writes land in tmp_path, not ~/.claude.
    env = {**os.environ, "SESSION_EXPLORER_DRY_RUN": "1", "HOME": str(tmp_path)}
    result = subprocess.run([_BIN, "launch"], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "session-explorer" in result.stdout
    assert "tui" in result.stdout  # the would-be terminal runs `... tui`


def _write_gc_index(tmp_path):
    """Index with one old, unnamed session backed by an idle JSONL.

    last_active_at is 45 days back and the JSONL mtime is an hour old (>60s),
    so the session is eligible and not mistaken for a live one. Both are keyed
    to the real wall clock because the CLI uses the real `now`.
    """
    import json
    import time
    from datetime import datetime, timedelta, timezone
    jsonl = tmp_path / "old.jsonl"
    jsonl.write_text('{"type":"user"}\n')
    past = time.time() - 3600
    os.utime(jsonl, (past, past))
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"version": 2, "sessions": {
        "sid": {"name_cached": None, "last_active_at": old, "transcript_path": str(jsonl)}}}))
    return idx, jsonl


def test_index_gc_deletes_old_unnamed(tmp_path):
    import json
    idx, jsonl = _write_gc_index(tmp_path)
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(idx)}
    result = subprocess.run([_BIN, "index", "--gc"], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "Removed 1" in result.stdout
    assert not jsonl.exists()
    assert "sid" not in json.loads(idx.read_text())["sessions"]


def test_index_gc_dry_run_changes_nothing(tmp_path):
    import json
    idx, jsonl = _write_gc_index(tmp_path)
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(idx)}
    result = subprocess.run([_BIN, "index", "--gc", "--dry-run"], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout.lower()
    assert jsonl.exists()
    assert "sid" in json.loads(idx.read_text())["sessions"]


def test_cli_live_records_event(tmp_path, monkeypatch):
    import json as _json
    from _pkg import cli as _cli

    live_path = tmp_path / "session-explorer-live.json"
    monkeypatch.setenv("SESSION_EXPLORER_LIVE", str(live_path))
    rc = _cli.main(["live", "--event", "SessionStart", "--sid", "abc",
                    "--transcript", "/t/abc.jsonl", "--cwd", "/repo", "--pid", "5"])
    assert rc == 0
    data = _json.loads(live_path.read_text())
    assert data["sessions"]["abc"]["state"] == "idle"
    assert data["sessions"]["abc"]["pid"] == 5


def test_cli_live_never_errors_on_bad_input(tmp_path, monkeypatch):
    from _pkg import cli as _cli

    monkeypatch.setenv("SESSION_EXPLORER_LIVE", str(tmp_path / "live.json"))
    # Missing --sid still must not crash the hook caller.
    rc = _cli.main(["live", "--event", "Stop", "--sid", ""])
    assert rc == 0


def test_launch_wraps_in_tmux_when_available(tmp_path, monkeypatch):
    from _pkg import cli, tmux, launcher
    # Redirect HOME so the generated config lands in the test's tmp dir, never
    # the real ~/.claude.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(tmux, "available", lambda which=None: True)
    monkeypatch.setattr(tmux, "detected_version", lambda: (3, 4))
    monkeypatch.setattr(tmux, "meets_floor", lambda v: True)
    captured = {}
    monkeypatch.setattr(launcher, "launch",
                        lambda cmd: captured.setdefault("cmd", cmd) or 0)
    cli._cmd_launch()
    assert "tmux -L session-explorer" in captured["cmd"]


def test_launch_plain_when_tmux_absent(monkeypatch):
    from _pkg import cli, tmux, launcher
    monkeypatch.setattr(tmux, "available", lambda which=None: False)
    captured = {}
    monkeypatch.setattr(launcher, "launch",
                        lambda cmd: captured.setdefault("cmd", cmd) or 0)
    cli._cmd_launch()
    assert "tmux" not in captured["cmd"]
    assert captured["cmd"].startswith("exec ")


def test_launch_writes_config_without_kill_hook():
    import _pkg.tmux as tmux
    conf = tmux.build_config()
    assert "client-detached" not in conf
    assert "kill-server" not in conf


import json as _json
import subprocess as _sp


def _git(cwd, *args):
    _sp.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


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


def _qenv(tmp_path):
    return {**os.environ,
            "SESSION_EXPLORER_INDEX": str(tmp_path / "index.json"),
            "SESSION_EXPLORER_QUEUE_CONFIG": str(tmp_path / "qc.json"),
            "SESSION_EXPLORER_QUEUES_ROOT": str(tmp_path / "queues"),
            "SESSION_EXPLORER_LIVE": str(tmp_path / "live.json")}


def _seed_resource(tmp_path, env):
    """Use the config store directly to declare a trivial 'none' resource."""
    from _pkg import project_id as pid_mod, queue_config as qc
    root = _repo(tmp_path)
    pid = pid_mod.project_id(str(root))
    qc.add_resource(env["SESSION_EXPLORER_QUEUE_CONFIG"], project_id=pid,
                    display_path=str(root), resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    return root, pid


def test_queue_run_executes_command_from_cwd(tmp_path):
    env = _qenv(tmp_path)
    root, _pid = _seed_resource(tmp_path, env)
    marker = tmp_path / "ran"
    r = _sp.run([_BIN, "queue-run", "--resource", "db", "--",
                 "sh", "-c", f"touch {marker}"],
                cwd=str(root), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert marker.exists()


def test_queue_run_unknown_resource_uses_refusal_code(tmp_path):
    env = _qenv(tmp_path)
    root = _repo(tmp_path)
    r = _sp.run([_BIN, "queue-run", "--resource", "nope", "--", "true"],
                cwd=str(root), env=env, capture_output=True, text=True)
    from _pkg.queue_run import REFUSAL_EXIT
    assert r.returncode == REFUSAL_EXIT


def test_queue_status_json_lists_configured_resource(tmp_path):
    env = _qenv(tmp_path)
    root, pid = _seed_resource(tmp_path, env)
    r = _sp.run([_BIN, "queue-status", "--json"], env=env,
                capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = _json.loads(r.stdout)
    ids = [row["id"] for row in data]
    assert f"{pid}/db" in ids


def test_queue_cancel_reports_no_waiter(tmp_path):
    env = _qenv(tmp_path)
    root, pid = _seed_resource(tmp_path, env)
    # Nothing waiting -> cancel is a clean no-op with a clear message.
    r = _sp.run([_BIN, "queue-cancel", "--resource", "db", "--sid", "ghost"],
                cwd=str(root), env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "no waiting ticket" in (r.stdout + r.stderr).lower()


def _git_repo_with_root(tmp_path):
    """git-init a repo and write a queue config opting it in with a 'root' resource.

    Relies on the suite's conftest already putting bin/ on sys.path (the same way
    test_queue_awareness.py does `from _pkg import ...`)."""
    from _pkg import project_id, queue_config
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    cfg = tmp_path / "queue-config.json"
    pid = project_id.project_id(str(repo))
    queue_config.add_resource(
        str(cfg), project_id=pid, display_path=str(repo), resource_id="root",
        resource={"kind": "root-dir", "path": str(repo),
                  "guard": [{"exe": "docker", "sub": ["compose", "up"]}],
                  "run_in": "root", "acquire": "sync", "release": "none",
                  "sync": {"delete": True, "exclude": ["/.git"],
                           "protect": ["/.git"]}})
    return repo, cfg


def test_queue_context_emits_additional_context_when_opted_in(tmp_path):
    repo, cfg = _git_repo_with_root(tmp_path)
    result = subprocess.run(
        [_BIN, "queue-context", "--cwd", str(repo)],
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    assert "docker compose up" in out["additionalContext"]
    assert "queue-run" in out["additionalContext"]


def test_queue_context_silent_when_not_opted_in(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    cfg = tmp_path / "queue-config.json"
    result = subprocess.run(
        [_BIN, "queue-context", "--cwd", str(repo)],
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def _run_guard(cmd_obj, cfg, repo):
    import json as _json
    payload = _json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": cmd_obj},
        "cwd": str(repo),
    })
    return subprocess.run(
        [_BIN, "queue-guard"], input=payload, capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})


def test_queue_guard_denies_guarded_command(tmp_path):
    repo, cfg = _git_repo_with_root(tmp_path)
    result = _run_guard("docker compose up -d", cfg, repo)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert "queue-run --resource root --" in out["permissionDecisionReason"]


def test_queue_guard_allows_unguarded_command(tmp_path):
    repo, cfg = _git_repo_with_root(tmp_path)
    result = _run_guard("docker ps", cfg, repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_queue_guard_ignores_non_bash_tool(tmp_path):
    repo, cfg = _git_repo_with_root(tmp_path)
    import json as _json
    payload = _json.dumps({"tool_name": "Read",
                           "tool_input": {"file_path": "/x"}, "cwd": str(repo)})
    result = subprocess.run(
        [_BIN, "queue-guard"], input=payload, capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_queue_guard_fails_open_on_garbage_stdin(tmp_path):
    _, cfg = _git_repo_with_root(tmp_path)
    result = subprocess.run(
        [_BIN, "queue-guard"], input="not json at all",
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_queue_guard_fails_open_when_cwd_missing(tmp_path):
    # Payload-schema drift: no cwd. Must NOT guess via os.getcwd() (which could be
    # another opted-in project) -> allow silently.
    repo, cfg = _git_repo_with_root(tmp_path)
    import json as _json
    payload = _json.dumps({"tool_name": "Bash",
                           "tool_input": {"command": "docker compose up -d"}})
    result = subprocess.run(
        [_BIN, "queue-guard"], input=payload, capture_output=True, text=True,
        cwd=str(repo),  # hook cwd happens to be an opted-in repo; must be ignored
        env={**os.environ, "SESSION_EXPLORER_QUEUE_CONFIG": str(cfg)})
    assert result.returncode == 0
    assert result.stdout.strip() == ""
