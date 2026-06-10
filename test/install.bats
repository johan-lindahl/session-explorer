#!/usr/bin/env bats
# Shell-level tests for install.sh — the plain (non-marketplace) install path.
# pytest covers the Python internals; this exercises the bash script end to end
# with HOME redirected to a throwaway dir.

setup() {
  REPO="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  TMP="$(mktemp -d)"
  export HOME="$TMP"
}

teardown() {
  # Tolerant: a detached gc child may still be writing under $HOME=$TMP.
  [ -n "$TMP" ] && rm -rf "$TMP" 2>/dev/null || true
}

settings_get() {  # settings_get <key>
  python3 -c "import json,sys; print(json.load(open('$HOME/.claude/settings.json')).get(sys.argv[1]))" "$1"
}

@test "install creates the binary symlink into ~/.local/bin" {
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  [ -L "$HOME/.local/bin/session-explorer" ]
  [ "$(readlink "$HOME/.local/bin/session-explorer")" = "$REPO/bin/session-explorer" ]
}

@test "install does NOT touch cleanupPeriodDays (retention is opt-in)" {
  mkdir -p "$HOME/.claude"
  echo '{"cleanupPeriodDays": 14}' > "$HOME/.claude/settings.json"
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  [ "$(settings_get cleanupPeriodDays)" = "14" ]   # left as-is
  [ ! -f "$HOME/.claude/.session-explorer.backup" ]
}

@test "install registers a SessionStart hook pointing at session-start.sh" {
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
ss = d['hooks']['SessionStart']
assert any('session-start.sh' in str(h.get('command','')) for h in ss), ss
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "install registers the live-session hooks in settings.json" {
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  run python3 -c "import json,os;d=json.load(open(os.environ['HOME']+'/.claude/settings.json'));h=d.get('hooks',{});print('Stop' in h, 'UserPromptSubmit' in h, 'SessionEnd' in h, 'Notification' in h)"
  [[ "$output" == "True True True True" ]]
}

@test "install keeps both SessionStart commands (start + live)" {
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
ss = d['hooks']['SessionStart']
cmds = ' '.join(str(h.get('command','')) for h in ss)
assert 'session-start.sh' in cmds, ss
assert 'session-live.sh' in cmds, ss
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "install Notification hook carries the idle_prompt matcher" {
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
n = d['hooks']['Notification']
assert any('idle_prompt' in str(h.get('matcher','')) or 'idle_prompt' in str(h.get('matchers','')) for h in n), n
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "install is idempotent — second run keeps exactly one hook entry" {
  mkdir -p "$HOME/.claude"
  echo '{"cleanupPeriodDays": 14}' > "$HOME/.claude/settings.json"
  bash "$REPO/install.sh"
  bash "$REPO/install.sh"
  # Exactly one session-explorer SessionStart hook entry.
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
n = sum(1 for h in d['hooks']['SessionStart'] if 'session-start.sh' in str(h.get('command','')))
print(n)
"
  [ "$output" = "1" ]
}

@test "install is idempotent — live hooks aren't duplicated on re-run" {
  bash "$REPO/install.sh"
  bash "$REPO/install.sh"
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
h = d['hooks']
def count(evt):
    return sum(c.count('session-live.sh') for entry in h.get(evt, [])
               for c in [str(x.get('command','')) for x in entry.get('hooks', [])] or [str(entry.get('command',''))])
print(count('SessionStart'), count('UserPromptSubmit'), count('Stop'), count('Notification'), count('SessionEnd'))
"
  [ "$output" = "1 1 1 1 1" ]
}

@test "install registers a PreToolUse hook (nested matcher-group, write-capable tools)" {
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
pt = d['hooks']['PreToolUse']
# Documented nested shape: a matcher group scoped to all write-capable tools.
grp = next(h for h in pt if h.get('matcher') == 'Bash|Edit|Write|NotebookEdit')
cmds = [s.get('command','') for s in grp.get('hooks', [])]
assert any('pre-tool-use.sh' in c for c in cmds), pt
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

@test "install is idempotent — one PreToolUse guard command after two runs" {
  bash "$REPO/install.sh"
  bash "$REPO/install.sh"
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
# Count guard commands in BOTH flat and nested shapes.
cmds = []
for h in d['hooks']['PreToolUse']:
    if h.get('command'):
        cmds.append(h['command'])
    for sub in h.get('hooks', []) or []:
        if sub.get('command'):
            cmds.append(sub['command'])
print(sum(1 for c in cmds if 'pre-tool-use.sh' in c))
"
  [ "$output" = "1" ]
}

@test "install preserves a shared-group user hook (even one whose path contains 'session-explorer')" {
  mkdir -p "$HOME/.claude"
  # The user hook path deliberately contains 'session-explorer' to prove nested
  # pruning matches on concrete script names, not the broad marker.
  python3 -c "
import json, os
json.dump({'hooks': {'PreToolUse': [
    {'matcher': 'Bash', 'hooks': [
        {'type': 'command', 'command': '/opt/session-explorer-helper/audit.sh'},
        {'type': 'command', 'command': '$REPO/hooks/pre-tool-use.sh'}]}]}},
    open(os.path.expanduser('~/.claude/settings.json'), 'w'))
"
  run bash "$REPO/install.sh"
  [ "$status" -eq 0 ]
  run python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
cmds = [s.get('command','') for h in d['hooks']['PreToolUse'] for s in h.get('hooks', []) or []]
assert any('audit.sh' in c for c in cmds), d   # user hook preserved despite 'session-explorer' in path
assert sum('pre-tool-use.sh' in c for c in cmds) == 1, d   # exactly one of ours
print('ok')
"
  [ "$output" = "ok" ]
}
