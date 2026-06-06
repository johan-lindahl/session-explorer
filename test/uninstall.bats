#!/usr/bin/env bats
# Shell-level tests for uninstall.sh — restores cleanupPeriodDays and tears down
# session-explorer's settings. Runs install.sh first to set up state.

setup() {
  REPO="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  TMP="$(mktemp -d)"
  export HOME="$TMP"
}

teardown() {
  # Tolerant: a detached gc child may still be writing under $HOME=$TMP.
  [ -n "$TMP" ] && rm -rf "$TMP" 2>/dev/null || true
}

settings_get() {
  python3 -c "import json,sys; print(json.load(open('$HOME/.claude/settings.json')).get(sys.argv[1]))" "$1"
}

@test "uninstall restores the original cleanupPeriodDays" {
  mkdir -p "$HOME/.claude"
  echo '{"cleanupPeriodDays": 14}' > "$HOME/.claude/settings.json"
  bash "$REPO/install.sh"
  # Simulate the user opting into retention (the TUI prompt's effect).
  python3 - "$HOME/.claude/settings.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p)); d["cleanupPeriodDays"] = 36500; json.dump(d, open(p, "w"))
PY
  echo "14" > "$HOME/.claude/.session-explorer.backup"
  [ "$(settings_get cleanupPeriodDays)" = "36500" ]

  run bash "$REPO/uninstall.sh"
  [ "$status" -eq 0 ]
  [ "$(settings_get cleanupPeriodDays)" = "14" ]
}

@test "uninstall removes the SessionStart hook entry" {
  bash "$REPO/install.sh"
  run bash "$REPO/uninstall.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
ss = d.get('hooks', {}).get('SessionStart', [])
print(sum(1 for h in ss if 'session-start.sh' in str(h.get('command',''))))
"
  [ "$output" = "0" ]
}

@test "uninstall removes the live-session lifecycle hooks" {
  bash "$REPO/install.sh"
  run bash "$REPO/uninstall.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
h = d.get('hooks', {})
# No session-live.sh references should remain anywhere.
def refs(evt):
    out = 0
    for entry in h.get(evt, []):
        out += str(entry.get('command','')).count('session-live.sh')
        for x in entry.get('hooks', []):
            out += str(x.get('command','')).count('session-live.sh')
    return out
total = sum(refs(e) for e in ('SessionStart','UserPromptSubmit','Stop','Notification','SessionEnd'))
# The four standalone events should be gone entirely (empty or absent).
empties = all(not h.get(e) for e in ('UserPromptSubmit','Stop','Notification','SessionEnd'))
print(total, empties)
"
  [ "$output" = "0 True" ]
}

@test "uninstall keeps unrelated user hooks intact" {
  bash "$REPO/install.sh"
  # A user-owned hook the plugin must never touch.
  python3 - "$HOME/.claude/settings.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d.setdefault("hooks", {}).setdefault("Stop", []).append(
    {"matchers": [], "command": "/usr/bin/my-own-hook.sh"})
json.dump(d, open(p, "w"))
PY
  run bash "$REPO/uninstall.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
stop = d.get('hooks', {}).get('Stop', [])
assert any('my-own-hook.sh' in str(h.get('command','')) for h in stop), stop
assert not any('session-live.sh' in str(h.get('command','')) for h in stop), stop
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "uninstall keeps the index by default and --purge deletes it" {
  bash "$REPO/install.sh"
  # Fabricate an index + folder store.
  printf '{"version":2,"sessions":{}}' > "$HOME/.claude/session-explorer-index.json"
  printf '{"version":1,"projects":{}}' > "$HOME/.claude/session-explorer-folders.json"

  bash "$REPO/uninstall.sh"
  [ -f "$HOME/.claude/session-explorer-index.json" ]   # kept by default

  bash "$REPO/uninstall.sh" --purge
  [ ! -f "$HOME/.claude/session-explorer-index.json" ] # purged
  [ ! -f "$HOME/.claude/session-explorer-folders.json" ]
}

@test "uninstall strips the PreToolUse guard (flat or nested)" {
  bash "$REPO/install.sh"
  bash "$REPO/uninstall.sh"
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
pt = d.get('hooks', {}).get('PreToolUse', [])
cmds = []
for h in pt:
    if h.get('command'):
        cmds.append(h['command'])
    for sub in h.get('hooks', []) or []:
        if sub.get('command'):
            cmds.append(sub['command'])
assert not any('pre-tool-use.sh' in c for c in cmds), pt
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "uninstall preserves a shared-group user hook (even one whose path contains 'session-explorer')" {
  mkdir -p "$HOME/.claude"
  python3 -c "
import json, os
json.dump({'hooks': {'PreToolUse': [
    {'matcher': 'Bash', 'hooks': [
        {'type': 'command', 'command': '/opt/session-explorer-helper/audit.sh'},
        {'type': 'command', 'command': '$REPO/hooks/pre-tool-use.sh'}]}]}},
    open(os.path.expanduser('~/.claude/settings.json'), 'w'))
"
  bash "$REPO/uninstall.sh"
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
pt = d.get('hooks', {}).get('PreToolUse', [])
cmds = [s.get('command','') for h in pt for s in h.get('hooks', []) or []]
assert any('audit.sh' in c for c in cmds), pt   # user hook preserved despite 'session-explorer' in path
assert not any('pre-tool-use.sh' in c for c in cmds), pt   # ours removed
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}
