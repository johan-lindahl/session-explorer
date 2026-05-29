#!/usr/bin/env bash
# Lifecycle dispatcher for session-explorer's live-session registry.
# Reads JSON on stdin: {hook_event_name, session_id, transcript_path, cwd, ...}
# Records the event via the CLI, fully non-blocking. Never blocks; exits 0.
# Fires on every turn (Stop/UserPromptSubmit) as well as session start/end, so
# the only synchronous cost is one python JSON parse; the CLI write is detached.

set -u
CLAUDE_DIR="${HOME}/.claude"
LOG="${CLAUDE_DIR}/session-explorer.log"
mkdir -p "${CLAUDE_DIR}" 2>/dev/null || true
log() { echo "[$(date -u +%FT%TZ)] $*" >> "${LOG}" 2>/dev/null || true; }

PAYLOAD="$(cat 2>/dev/null || true)"

# Resolve the CLI (same strategy as session-start.sh).
CLI=""
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -x "${CLAUDE_PLUGIN_ROOT}/bin/session-explorer" ]; then
  CLI="${CLAUDE_PLUGIN_ROOT}/bin/session-explorer"
elif [ -x "${HOME}/.local/bin/session-explorer" ]; then
  CLI="${HOME}/.local/bin/session-explorer"
else
  CLI="$(command -v session-explorer 2>/dev/null || echo "")"
fi
[ -n "${CLI}" ] && [ -x "${CLI}" ] || { log "warn: session-live: CLI not found"; exit 0; }
[ -n "${PAYLOAD}" ] || exit 0

read -r EVENT SID TPATH CWD < <(printf '%s' "${PAYLOAD}" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('hook_event_name',''), d.get('session_id',''),
          d.get('transcript_path',''), d.get('cwd',''))
except Exception:
    print('', '', '', '')
" 2>/dev/null)

[ -n "${EVENT}" ] && [ -n "${SID}" ] || exit 0

# Run detached so a turn never waits on the registry write. $PPID is the Claude
# process (validated in the PID-capture spike); recorded only on SessionStart.
ARGS=(live --event "${EVENT}" --sid "${SID}")
[ -n "${TPATH}" ] && ARGS+=(--transcript "${TPATH}")
[ -n "${CWD}" ] && ARGS+=(--cwd "${CWD}")
if [ "${EVENT}" = "SessionStart" ]; then
  ARGS+=(--pid "${PPID}")
fi
( "${CLI}" "${ARGS[@]}" >>"${LOG}" 2>&1 ) >/dev/null 2>&1 &

exit 0
