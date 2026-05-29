#!/usr/bin/env bash
# session-explorer plain install (non-marketplace).
# Idempotent: re-running is safe.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
LOCAL_BIN="${HOME}/.local/bin"
SETTINGS="${CLAUDE_DIR}/settings.json"
BACKUP="${CLAUDE_DIR}/.session-explorer.backup"

mkdir -p "${CLAUDE_DIR}" "${LOCAL_BIN}"

# --- Symlink the binary ---
ln -sf "${REPO_DIR}/bin/session-explorer" "${LOCAL_BIN}/session-explorer"
echo "Linked: ${LOCAL_BIN}/session-explorer -> ${REPO_DIR}/bin/session-explorer"

# --- Register the SessionStart hook (does NOT touch cleanupPeriodDays) ---
# Retention is opt-in: the TUI asks on first launch before neutralising
# cleanupPeriodDays. install.sh only wires up the binary + hook.
python3 - "$REPO_DIR" <<'PY'
import json, os, sys

repo = sys.argv[1]
settings_path = os.path.expanduser("~/.claude/settings.json")
try:
    with open(settings_path) as f:
        data = json.load(f)
except FileNotFoundError:
    data = {}

hooks = data.setdefault("hooks", {})

start_cmd = os.path.join(repo, "hooks", "session-start.sh")
live_cmd = os.path.join(repo, "hooks", "session-live.sh")

# Idempotent: a hook entry is "ours" if its command points at one of our scripts.
_MARKERS = ("session-explorer", "session-start.sh", "session-live.sh")

def _is_ours(h):
    return isinstance(h, dict) and any(
        m in str(h.get("command", "")) for m in _MARKERS
    )

def _strip_ours(evt):
    return [h for h in hooks.get(evt, []) if not _is_ours(h)]

# SessionStart: keep any user hooks, re-add session-start.sh + session-live.sh.
ss = _strip_ours("SessionStart")
ss.append({"matchers": [], "command": start_cmd})
ss.append({"matchers": [], "command": live_cmd})
hooks["SessionStart"] = ss

# The live dispatcher on the other lifecycle events. Notification carries the
# idle_prompt matcher; the rest match everything.
hooks["UserPromptSubmit"] = _strip_ours("UserPromptSubmit") + [
    {"matchers": [], "command": live_cmd}]
hooks["Stop"] = _strip_ours("Stop") + [
    {"matchers": [], "command": live_cmd}]
hooks["Notification"] = _strip_ours("Notification") + [
    {"matchers": ["idle_prompt"], "command": live_cmd}]
hooks["SessionEnd"] = _strip_ours("SessionEnd") + [
    {"matchers": [], "command": live_cmd}]

with open(settings_path, "w") as f:
    json.dump(data, f, indent=2)

print(f"Updated {settings_path}: registered SessionStart + live-session hooks")
PY

chmod +x "${REPO_DIR}/hooks/session-start.sh" "${REPO_DIR}/bin/session-explorer"

echo
echo "Install complete. Start a new Claude session; run /session-explorer:open to open the explorer."
echo "On first open you'll be asked whether to let session-explorer manage retention."
