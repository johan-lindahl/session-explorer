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
