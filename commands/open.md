---
description: Open the session-explorer TUI in a new terminal window.
allowed-tools: Bash
---

!`bash -c 'shopt -s nullglob; for p in "$HOME/.local/bin/session-explorer" "$HOME"/.claude/plugins/cache/*/session-explorer/*/bin/session-explorer; do [ -x "$p" ] && exec "$p" launch; done; echo "session-explorer binary not found" >&2; exit 1'`

The session-explorer TUI has already been launched in a new Terminal window
by the shell command above. **Do not run the launcher again.** Reply with one
short sentence confirming it opened (or report the error message above if the
binary was not found).
