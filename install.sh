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
pretool_cmd = os.path.join(repo, "hooks", "pre-tool-use.sh")

# Idempotent: a hook entry is "ours" if its command points at one of our scripts.
_MARKERS = ("session-explorer", "session-start.sh", "session-live.sh",
            "pre-tool-use.sh")
# Concrete hook-script basenames. Used when pruning sub-hooks INSIDE a matcher
# group, where the broad "session-explorer" substring in _MARKERS could over-match
# a user hook whose path merely contains "session-explorer". Our own nested hooks
# are always one of these scripts, so narrowing loses no coverage.
_HOOK_SCRIPTS = ("session-start.sh", "session-live.sh", "pre-tool-use.sh")

def _cmd_is_ours(cmd):           # flat top-level entries: our dedicated entries
    return any(m in str(cmd) for m in _MARKERS)

def _sub_is_ours(cmd):           # nested sub-hooks: narrow, never over-match users
    return any(m in str(cmd) for m in _HOOK_SCRIPTS)

def _strip_ours(evt):
    """Drop our hook entries, preserving user hooks. Flat entries: drop if the
    command is ours. Nested matcher-group entries: prune only our nested
    command-hooks (matched by concrete script name, not the broad marker) and keep
    the group if any user hooks remain (so a shared Bash group never loses the
    user's hook). Fully-ours groups are dropped."""
    vals = hooks.get(evt)
    if not isinstance(vals, list):
        return []
    out = []
    for h in vals:
        if not isinstance(h, dict):
            out.append(h)
            continue
        if "hooks" in h:
            subs = h.get("hooks") or []
            kept = [s for s in subs
                    if not (isinstance(s, dict) and _sub_is_ours(s.get("command", "")))]
            if kept:
                out.append(dict(h, hooks=kept) if len(kept) != len(subs) else h)
            # else: group emptied of all our hooks -> drop the group
        elif not _cmd_is_ours(h.get("command", "")):
            out.append(h)
    return out

# Lifecycle event set is mirrored in bin/_pkg/uninstall.py (_HOOK_EVENTS) and
# .claude-plugin/plugin.json; keep all three in sync.
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

# PreToolUse root guard (leased-ground spec). Use the documented nested
# matcher-group form (matching plugin.json) so the guard actually fires on
# plain installs. Matcher covers every write-capable tool the guard decides on.
hooks["PreToolUse"] = _strip_ours("PreToolUse") + [
    {"matcher": "Bash|Edit|Write|NotebookEdit",
     "hooks": [{"type": "command", "command": pretool_cmd}]}]

with open(settings_path, "w") as f:
    json.dump(data, f, indent=2)

print(f"Updated {settings_path}: registered SessionStart + live-session + "
      "PreToolUse guard hooks")
PY

chmod +x "${REPO_DIR}/hooks/session-start.sh" "${REPO_DIR}/hooks/session-live.sh" "${REPO_DIR}/hooks/pre-tool-use.sh" "${REPO_DIR}/bin/session-explorer"

echo
echo "Install complete. Start a new Claude session; run /session-explorer:open to open the explorer."
echo "On first open you'll be asked whether to let session-explorer manage retention."
