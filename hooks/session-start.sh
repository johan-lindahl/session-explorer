#!/usr/bin/env bash
# SessionStart hook for session-explorer.
# Idempotent first-run setup + delegates to CLI for index recording.
#
# Reads JSON on stdin: {session_id, transcript_path, cwd, source}
# Never blocks startup; logs failures and exits 0.

set -u

CLAUDE_DIR="${HOME}/.claude"
LOG="${CLAUDE_DIR}/session-explorer.log"
SETTINGS="${CLAUDE_DIR}/settings.json"
BACKUP="${CLAUDE_DIR}/.session-explorer.backup"

mkdir -p "${CLAUDE_DIR}" 2>/dev/null || true

log() { echo "[$(date -u +%FT%TZ)] $*" >> "${LOG}" 2>/dev/null || true; }

# Read stdin (best-effort)
PAYLOAD="$(cat 2>/dev/null || true)"

# --- First-run setup: neutralise native cleanup ---
if [ ! -f "${BACKUP}" ]; then
  if [ -f "${SETTINGS}" ]; then
    # Extract current cleanupPeriodDays (default 30 if unset).
    PRIOR="$(python3 -c "
import json
try:
    with open('${SETTINGS}') as f:
        d = json.load(f)
    print(d.get('cleanupPeriodDays', 30))
except Exception:
    print(30)
" 2>/dev/null || echo 30)"
    echo "${PRIOR}" > "${BACKUP}"

    # Set cleanupPeriodDays = 36500 in settings.json
    python3 -c "
import json
with open('${SETTINGS}') as f:
    d = json.load(f)
d['cleanupPeriodDays'] = 36500
with open('${SETTINGS}', 'w') as f:
    json.dump(d, f, indent=2)
" 2>>"${LOG}" || log "warn: failed to update cleanupPeriodDays"
    log "first-run: backed up cleanupPeriodDays=${PRIOR}, set to 36500"
  else
    echo 30 > "${BACKUP}"
    echo '{"cleanupPeriodDays": 36500}' > "${SETTINGS}"
    log "first-run: created settings.json with cleanupPeriodDays=36500"
  fi
fi

# --- Resolve the CLI ---
# Marketplace install: CLAUDE_PLUGIN_ROOT is set by Claude Code; CLI lives at $CLAUDE_PLUGIN_ROOT/bin/session-explorer.
# Plain install.sh: the binary is symlinked into ~/.local/bin/session-explorer.
# Either way, also fall back to PATH lookup.
CLI=""
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -x "${CLAUDE_PLUGIN_ROOT}/bin/session-explorer" ]; then
  CLI="${CLAUDE_PLUGIN_ROOT}/bin/session-explorer"
elif [ -x "${HOME}/.local/bin/session-explorer" ]; then
  CLI="${HOME}/.local/bin/session-explorer"
else
  CLI="$(command -v session-explorer 2>/dev/null || echo "")"
fi

# Parse session_id, transcript_path, cwd from PAYLOAD using python3 (via stdin to avoid quoting bugs).
SID=""
TPATH=""
CWD=""
if [ -n "${PAYLOAD}" ]; then
  read -r SID TPATH CWD < <(printf '%s' "${PAYLOAD}" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('session_id',''), d.get('transcript_path',''), d.get('cwd',''))
except Exception:
    print('', '', '')
" 2>/dev/null)
fi

# --- Write the active-session pointer (per SPEC §Hooks step 4) ---
if [ -n "${SID}" ]; then
  printf '%s' "${SID}" > "${CLAUDE_DIR}/.session-explorer.current" 2>/dev/null || true
fi

# --- Record the session into the index ---
if [ -n "${CLI}" ] && [ -x "${CLI}" ]; then
  if [ -n "${SID}" ] && [ -n "${TPATH}" ] && [ -n "${CWD}" ]; then
    "${CLI}" index --record "${SID}" "${TPATH}" "${CWD}" 2>>"${LOG}" || log "warn: index --record failed for ${SID}"
  fi
else
  log "warn: session-explorer CLI not found; CLAUDE_PLUGIN_ROOT=${CLAUDE_PLUGIN_ROOT:-(unset)}; ~/.local/bin checked; PATH checked"
fi

exit 0
