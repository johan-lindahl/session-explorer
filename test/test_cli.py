"""CLI smoke tests for the entry shim."""

import os
import subprocess
import shutil

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_REPO_ROOT, "bin", "session-explorer")

_FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_version_flag_prints_version():
    result = subprocess.run([_BIN, "--version"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "session-explorer 0.6.0" in result.stdout


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


def test_launch_invokes_osascript_on_mac(monkeypatch):
    """Smoke test: `session-explorer launch` should attempt to spawn a new terminal."""
    # We run the binary in a subprocess where we can intercept by setting
    # SESSION_EXPLORER_DRY_RUN=1, which makes launcher.launch print the would-be
    # command and exit 0 without actually shelling out.
    env = {**os.environ, "SESSION_EXPLORER_DRY_RUN": "1"}
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
