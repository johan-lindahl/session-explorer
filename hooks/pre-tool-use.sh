#!/usr/bin/env bash
# PreToolUse hook for session-explorer (Phase 3, spec section 8).
#
# Reads a PreToolUse payload on stdin: {tool_name, tool_input:{command}, cwd, ...}.
# For a guarded Bash command in an opted-in project, delegates to the CLI, which
# prints a `permissionDecision: deny` + redirect to queue-run. Fails OPEN: on any
# error (no CLI, bad payload, parse ambiguity) it emits nothing and exits 0, so
# the tool call proceeds. A false deny is worse than a missed guard.

set -u

# Probe sessions must leave no trace and never deny a tool call.
if [ "${SESSION_EXPLORER_PROBE:-}" = "1" ]; then exit 0; fi

CLAUDE_DIR="${HOME}/.claude"
LOG="${CLAUDE_DIR}/session-explorer.log"
# Ensure the log dir exists before any 2>>"${LOG}" redirection so the hook stays
# truly silent/fail-open even on a fresh box (mirrors session-start.sh).
mkdir -p "${CLAUDE_DIR}" 2>/dev/null || true

PAYLOAD="$(cat 2>/dev/null || true)"

# --- Resolve the CLI (same order as session-start.sh) ---
CLI=""
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -x "${CLAUDE_PLUGIN_ROOT}/bin/session-explorer" ]; then
  CLI="${CLAUDE_PLUGIN_ROOT}/bin/session-explorer"
elif [ -x "${HOME}/.local/bin/session-explorer" ]; then
  CLI="${HOME}/.local/bin/session-explorer"
else
  CLI="$(command -v session-explorer 2>/dev/null || echo "")"
fi

# No CLI or no payload -> fail open (let the tool run).
if [ -z "${CLI}" ] || [ ! -x "${CLI}" ] || [ -z "${PAYLOAD}" ]; then
  exit 0
fi

printf '%s' "${PAYLOAD}" | "${CLI}" queue-guard 2>>"${LOG}" || true
exit 0
