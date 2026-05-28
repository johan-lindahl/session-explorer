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
        "CLAUDE_PLUGIN_ROOT": str(_REPO_ROOT),  # so the hook can find bin/session-explorer
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


def test_hook_records_session_via_cli(tmp_path):
    """The hook should call session-explorer index --record after first-run setup.

    The stub JSONL has only an ai-title (no custom-title), so name_cached
    must be None — ai-title alone does not count as a user-assigned name.
    """
    # Create a stub JSONL the hook will index
    stub_jsonl = tmp_path / "stub.jsonl"
    stub_jsonl.write_text(
        '{"type":"ai-title","aiTitle":"work-sprint","sessionId":"01HOOK","timestamp":"2026-05-26T10:00:00Z"}\n'
        '{"type":"user","sessionId":"01HOOK","timestamp":"2026-05-26T10:00:01Z",'
        '"message":{"role":"user","content":"hi"}}\n'
    )

    payload = '{"session_id":"01HOOK","transcript_path":"' + str(stub_jsonl) + '","cwd":"' + str(tmp_path) + '"}'
    proc = _run_hook(tmp_path, stdin=payload)
    assert proc.returncode == 0

    # The hook should have created the index file with the session recorded
    index_path = tmp_path / ".claude" / "session-explorer-index.json"
    assert index_path.exists(), f"Index file not created. Hook stderr: {proc.stderr}"

    import json
    data = json.loads(index_path.read_text())
    assert "01HOOK" in data["sessions"]
    assert data["sessions"]["01HOOK"]["name_cached"] is None


def test_hook_writes_active_session_pointer(tmp_path):
    """SPEC §Hooks step 4: hook writes session_id to ~/.claude/.session-explorer.current."""
    proc = _run_hook(tmp_path, stdin='{"session_id":"01CUR","transcript_path":"/tmp/x","cwd":"/tmp"}')
    assert proc.returncode == 0
    pointer = tmp_path / ".claude" / ".session-explorer.current"
    assert pointer.exists()
    assert pointer.read_text() == "01CUR"


def test_hook_finds_cli_via_local_bin_when_plugin_dir_missing(tmp_path):
    """When CLAUDE_PLUGIN_ROOT is unset, hook should resolve CLI via ~/.local/bin/session-explorer."""
    # Symlink the repo's binary into the tmp HOME's ~/.local/bin
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "session-explorer").symlink_to(_REPO_ROOT / "bin" / "session-explorer")

    stub_jsonl = tmp_path / "stub.jsonl"
    stub_jsonl.write_text(
        '{"type":"ai-title","aiTitle":"local-bin-test","sessionId":"01LB","timestamp":"2026-05-26T10:00:00Z"}\n'
    )
    payload = '{"session_id":"01LB","transcript_path":"' + str(stub_jsonl) + '","cwd":"' + str(tmp_path) + '"}'

    # Run hook WITHOUT CLAUDE_PLUGIN_ROOT
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    env.update({"HOME": str(tmp_path)})
    proc = subprocess.run(
        ["bash", str(_HOOK)],
        input=payload, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0

    index_path = tmp_path / ".claude" / "session-explorer-index.json"
    assert index_path.exists(), f"Index file not created via ~/.local/bin path. stderr: {proc.stderr}"
    data = json.loads(index_path.read_text())
    assert "01LB" in data["sessions"]


# --- Retention GC auto-trigger (throttled once/24h, detached) ---

import time


def _wait_until(predicate, timeout=5.0, interval=0.05):
    """Poll until predicate() is true (gc is launched detached by the hook)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _log_has_removed(home):
    log = home / ".claude" / "session-explorer.log"
    return log.exists() and "Removed" in log.read_text()


def test_gc_fires_on_first_run_and_writes_stamp(tmp_path):
    """No stamp yet -> hook stamps and launches `index --gc` (detached)."""
    proc = _run_hook(tmp_path, stdin='{"session_id":"g1","transcript_path":"/tmp/g.jsonl","cwd":"/tmp"}')
    assert proc.returncode == 0, proc.stderr
    stamp = tmp_path / ".claude" / ".session-explorer.gc"
    assert stamp.exists()
    # gc runs in the background; its output lands in the log.
    assert _wait_until(lambda: _log_has_removed(tmp_path)), "gc never ran"


def test_gc_throttled_within_24h(tmp_path):
    """A stamp younger than 24h suppresses gc and is left untouched."""
    claude = tmp_path / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    stamp = claude / ".session-explorer.gc"
    stamp.write_text("")
    recent = time.time() - 3600  # 1h ago
    os.utime(stamp, (recent, recent))

    proc = _run_hook(tmp_path, stdin='{"session_id":"g2","transcript_path":"/tmp/g.jsonl","cwd":"/tmp"}')
    assert proc.returncode == 0, proc.stderr
    # Stamp not refreshed.
    assert abs(stamp.stat().st_mtime - recent) < 2
    # gc did not run.
    time.sleep(0.4)
    assert not _log_has_removed(tmp_path)


def test_gc_fires_again_after_24h(tmp_path):
    """A stamp older than 24h lets gc fire again and refreshes the stamp."""
    claude = tmp_path / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    stamp = claude / ".session-explorer.gc"
    stamp.write_text("")
    long_ago = time.time() - 25 * 3600
    os.utime(stamp, (long_ago, long_ago))

    proc = _run_hook(tmp_path, stdin='{"session_id":"g3","transcript_path":"/tmp/g.jsonl","cwd":"/tmp"}')
    assert proc.returncode == 0, proc.stderr
    assert stamp.stat().st_mtime > long_ago + 60  # refreshed
    assert _wait_until(lambda: _log_has_removed(tmp_path)), "gc never ran after 24h"
