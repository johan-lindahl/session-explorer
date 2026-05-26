"""Tests for hooks/session-start.sh.

Run the hook as a subprocess with HOME redirected to a tmp dir; assert on
the resulting files in $HOME/.claude/.
"""

import json
import os
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _REPO_ROOT / "hooks" / "session-start.sh"


def _run_hook(home: Path, stdin: str = "") -> subprocess.CompletedProcess:
    """Invoke session-start.sh with HOME=tmp and the given stdin payload."""
    env = {
        **os.environ,
        "HOME": str(home),
        "CLAUDE_PLUGIN_DIR": str(_REPO_ROOT),  # so the hook can find bin/session-explorer
    }
    return subprocess.run(
        ["bash", str(_HOOK)],
        input=stdin,
        capture_output=True, text=True,
        env=env,
    )


def _seed_settings(home: Path, payload: dict) -> Path:
    """Write payload as ~/.claude/settings.json under the redirected HOME."""
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    path = claude_dir / "settings.json"
    path.write_text(json.dumps(payload))
    return path


def test_first_run_backs_up_and_sets_cleanup(tmp_path):
    settings = _seed_settings(tmp_path, {"cleanupPeriodDays": 30, "other": "stuff"})

    proc = _run_hook(tmp_path, stdin='{"session_id":"abc","transcript_path":"/tmp/x.jsonl","cwd":"/tmp"}')
    assert proc.returncode == 0, proc.stderr

    backup = tmp_path / ".claude" / ".session-explorer.backup"
    assert backup.exists()
    assert backup.read_text().strip() == "30"

    data = json.loads(settings.read_text())
    assert data["cleanupPeriodDays"] == 36500
    assert data["other"] == "stuff"  # preserves unrelated fields


def test_second_run_is_noop(tmp_path):
    _seed_settings(tmp_path, {"cleanupPeriodDays": 30})

    # First run — sets 36500, backs up 30
    _run_hook(tmp_path, stdin='{"session_id":"abc","transcript_path":"/tmp/x.jsonl","cwd":"/tmp"}')

    # Tamper: change settings.json to include a marker
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.write_text(json.dumps({"cleanupPeriodDays": 36500, "marker": "do not touch"}))

    # Second run — should not touch settings.json (backup file exists)
    proc = _run_hook(tmp_path, stdin='{"session_id":"def","transcript_path":"/tmp/y.jsonl","cwd":"/tmp"}')
    assert proc.returncode == 0

    after = json.loads(settings_path.read_text())
    assert after["marker"] == "do not touch"
    assert after["cleanupPeriodDays"] == 36500

    backup = tmp_path / ".claude" / ".session-explorer.backup"
    assert backup.read_text().strip() == "30"  # still the original prior value


def test_hook_never_exits_nonzero_on_malformed_stdin(tmp_path):
    """The hook MUST exit 0 even if its input is unparseable — never block startup."""
    proc = _run_hook(tmp_path, stdin="not json")
    assert proc.returncode == 0


def test_hook_creates_claude_dir_if_missing(tmp_path):
    """When ~/.claude doesn't exist yet, the hook should create it (idempotently)."""
    # No _seed_settings call — tmp_path is bare
    proc = _run_hook(tmp_path, stdin='{"session_id":"abc","transcript_path":"/tmp/x.jsonl","cwd":"/tmp"}')
    assert proc.returncode == 0
    assert (tmp_path / ".claude").is_dir()
