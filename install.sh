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
ss = hooks.setdefault("SessionStart", [])

hook_cmd = os.path.join(repo, "hooks", "session-start.sh")
# Idempotent: remove any prior session-explorer hook entry
ss = [h for h in ss if not (isinstance(h, dict) and (
    "session-explorer" in str(h.get("command", "")) or
    "session-start.sh" in str(h.get("command", ""))
))]
ss.append({"matchers": [], "command": hook_cmd})
hooks["SessionStart"] = ss

with open(settings_path, "w") as f:
    json.dump(data, f, indent=2)

print(f"Updated {settings_path}: registered SessionStart hook -> {hook_cmd}")
PY

chmod +x "${REPO_DIR}/hooks/session-start.sh" "${REPO_DIR}/bin/session-explorer"

echo
echo "Install complete. Start a new Claude session; run /session-explorer:open to open the explorer."
echo "On first open you'll be asked whether to let session-explorer manage retention."
