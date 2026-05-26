---
description: Open the session-explorer TUI in a new terminal window.
allowed-tools: Bash
---

Open the session-explorer in a new terminal window.

!`bash -c 'shopt -s nullglob; for p in "$HOME/.local/bin/session-explorer" "$HOME"/.claude/plugins/cache/*/session-explorer/*/bin/session-explorer; do [ -x "$p" ] && exec "$p" launch; done; echo "session-explorer binary not found" >&2; exit 1'`
