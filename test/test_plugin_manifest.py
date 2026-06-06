"""Validate the marketplace plugin manifest wiring (.claude-plugin/plugin.json)."""
import json
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _manifest():
    with open(os.path.join(_REPO, ".claude-plugin", "plugin.json")) as f:
        return json.load(f)


def test_manifest_is_valid_json_and_keeps_sessionstart():
    m = _manifest()
    assert m["name"] == "session-explorer"
    assert "SessionStart" in m["hooks"]


def test_manifest_registers_pretooluse_bash_guard():
    pt = _manifest()["hooks"]["PreToolUse"]
    grp = next(h for h in pt if h.get("matcher") == "Bash")
    cmds = [s.get("command", "") for s in grp["hooks"]]
    assert any("pre-tool-use.sh" in c for c in cmds), pt
