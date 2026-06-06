#!/usr/bin/env bash
# SessionStart hook for session-explorer.
# Idempotent first-run setup + delegates to CLI for index recording.
#
# Reads JSON on stdin: {session_id, transcript_path, cwd, source}
# Never blocks startup; logs failures and exits 0.

set -u

# Usage-bar probe sessions (see usage.py / SESSION_EXPLORER_PROBE) must leave no
# trace: no current-pointer, no index record, no GC. Bail out before any work.
if [ "${SESSION_EXPLORER_PROBE:-}" = "1" ]; then exit 0; fi

CLAUDE_DIR="${HOME}/.claude"
LOG="${CLAUDE_DIR}/session-explorer.log"
SETTINGS="${CLAUDE_DIR}/settings.json"
BACKUP="${CLAUDE_DIR}/.session-explorer.backup"

mkdir -p "${CLAUDE_DIR}" 2>/dev/null || true

log() { echo "[$(date -u +%FT%TZ)] $*" >> "${LOG}" 2>/dev/null || true; }

# Read stdin (best-effort)
PAYLOAD="$(cat 2>/dev/null || true)"

# NOTE: the hook never modifies settings.json. Neutralising cleanupPeriodDays is
# opt-in and handled by the TUI's first-launch prompt (see tui.on_mount /
# _pkg/retention.py), which writes ${BACKUP} when the user agrees. Retention GC
# below only runs once that opt-in has happened.

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
    "${CLI}" index --record "${SID}" "${TPATH}" "${CWD}" >>"${LOG}" 2>&1 || log "warn: index --record failed for ${SID}"
  fi

  # --- Retention GC: only when the user opted in (backup present), at most once
  #     per 24h, fully detached so startup never waits. ---
  # ${BACKUP} exists iff retention was enabled via the TUI prompt; until then
  # native cleanup is in charge and the plugin must not delete anything. Throttle
  # via a stamp file; stamp BEFORE launching so a slow/failed gc can't re-fire.
  GC_STAMP="${CLAUDE_DIR}/.session-explorer.gc"
  if [ -f "${BACKUP}" ] && { [ ! -f "${GC_STAMP}" ] || [ -n "$(find "${GC_STAMP}" -mmin +1440 2>/dev/null)" ]; }; then
    : > "${GC_STAMP}" 2>/dev/null || true
    # Redirect the gc child's fds away from the hook's stdout/stderr so the
    # caller doesn't block waiting on an inherited pipe; background the subshell.
    ( "${CLI}" index --gc >>"${LOG}" 2>&1 ) >/dev/null 2>&1 &
  fi
else
  log "warn: session-explorer CLI not found; CLAUDE_PLUGIN_ROOT=${CLAUDE_PLUGIN_ROOT:-(unset)}; ~/.local/bin checked; PATH checked"
fi

# --- Shared-resource awareness (Phase 3, spec section 8) ---
# For opted-in projects, inject SessionStart additionalContext telling the agent
# the resource is shared + warm and to use queue-run. queue-context prints the
# hookSpecificOutput JSON (or nothing) and fails open; this is the ONLY thing the
# hook writes to stdout (index --record above is routed to the log).
if [ -n "${CLI}" ] && [ -x "${CLI}" ] && [ -n "${CWD}" ]; then
  "${CLI}" queue-context --cwd "${CWD}" 2>>"${LOG}" || true
fi

exit 0
