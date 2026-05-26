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
    assert "session-explorer 0.1.0" in result.stdout


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
