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

# --- Index recording lands in Task 14 ---

exit 0
