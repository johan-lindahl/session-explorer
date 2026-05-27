---
description: Open the session-explorer TUI in a new terminal window.
allowed-tools: Bash
---

!`bash -c 'INSTALLED="$HOME/.claude/plugins/installed_plugins.json"; CLI=$(python3 -c "import json, sys, os; fp = sys.argv[1]; data = json.load(open(fp)) if os.path.exists(fp) else {}; e = data.get(\"plugins\", {}).get(\"session-explorer@session-explorer\", []); ip = e[0].get(\"installPath\", \"\") if e else \"\"; ip and print(ip + \"/bin/session-explorer\")" "$INSTALLED" 2>/dev/null); [ -x "$CLI" ] || CLI="$HOME/.local/bin/session-explorer"; [ -x "$CLI" ] || CLI=$(command -v session-explorer 2>/dev/null); if [ -n "$CLI" ] && [ -x "$CLI" ]; then exec "$CLI" launch; fi; echo "session-explorer binary not found" >&2; exit 1'`

The session-explorer TUI has already been launched in a new Terminal window
by the shell command above. **Do not run the launcher again.** Reply with one
short sentence confirming it opened (or report the error message above if the
binary was not found).
