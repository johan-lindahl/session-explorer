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
