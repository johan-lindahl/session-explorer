#!/usr/bin/env bash
# PreToolUse hook for session-explorer (leased-ground root guard).
#
# Reads a PreToolUse payload on stdin: {tool_name, tool_input, cwd, session_id,
# ...} for Bash/Edit/Write/NotebookEdit. Delegates to the CLI's queue-guard,
# which (via root_guard) denies tool calls that touch the shared installed root
# from a worktree session, with a queue-run redirect. The PLUMBING here fails
# OPEN — no CLI, bad payload, or a crashed guard emits nothing and exits 0, so
# a broken hook never bricks tool calls — while the guard's own matching is
# deny-by-default on root mentions (semantics fail closed).

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
