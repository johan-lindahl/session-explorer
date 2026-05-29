#!/usr/bin/env bats
# Shell-level tests for hooks/session-start.sh — first-run setup + the throttled
# gc auto-trigger. pytest also covers this; these assert the same contract in a
# pure-bash harness (the form the hook actually runs in).

setup() {
  REPO="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  TMP="$(mktemp -d)"
  export HOME="$TMP"
  export CLAUDE_PLUGIN_ROOT="$REPO"   # so the hook resolves the real CLI
  PAYLOAD='{"session_id":"01HOOK","transcript_path":"/tmp/x.jsonl","cwd":"/tmp"}'
}

teardown() {
  # Tolerant: the hook's detached gc child may still be writing under $HOME=$TMP.
  [ -n "$TMP" ] && rm -rf "$TMP" 2>/dev/null || true
}

run_hook() {  # run_hook  (reads $PAYLOAD on stdin)
  printf '%s' "$PAYLOAD" | bash "$REPO/hooks/session-start.sh"
}

@test "hook exits 0 and never blocks startup" {
  run run_hook
  [ "$status" -eq 0 ]
}

@test "hook never modifies settings.json (retention is opt-in)" {
  mkdir -p "$HOME/.claude"
  echo '{"cleanupPeriodDays": 21}' > "$HOME/.claude/settings.json"
  run run_hook
  [ "$status" -eq 0 ]
  [ ! -f "$HOME/.claude/.session-explorer.backup" ]
  run python3 -c "import json; print(json.load(open('$HOME/.claude/settings.json'))['cleanupPeriodDays'])"
  [ "$output" = "21" ]   # untouched
}

@test "hook writes the active-session pointer" {
  run run_hook
  [ "$status" -eq 0 ]
  [ "$(cat "$HOME/.claude/.session-explorer.current")" = "01HOOK" ]
}

@test "hook does not gc until retention is enabled" {
  run run_hook
  [ "$status" -eq 0 ]
  [ ! -f "$HOME/.claude/.session-explorer.gc" ]
}

@test "hook fires gc on first run and stamps the throttle file" {
  mkdir -p "$HOME/.claude"
  echo "30" > "$HOME/.claude/.session-explorer.backup"   # retention opted in
  run run_hook
  [ "$status" -eq 0 ]
  [ -f "$HOME/.claude/.session-explorer.gc" ]
  # gc is detached; poll the log for its output (generous for cold CI runners).
  for _ in $(seq 1 200); do
    grep -q "Removed" "$HOME/.claude/session-explorer.log" 2>/dev/null && break
    sleep 0.05
  done
  grep -q "Removed" "$HOME/.claude/session-explorer.log"
}

@test "hook throttles gc within 24h (recent stamp left untouched)" {
  mkdir -p "$HOME/.claude"
  echo "30" > "$HOME/.claude/.session-explorer.backup"   # retention opted in
  stamp="$HOME/.claude/.session-explorer.gc"
  : > "$stamp"
  # Backdate 1 hour and read mtime via python3 (guaranteed present) so the test
  # doesn't depend on BSD-vs-GNU `date`/`stat` flag differences — the macOS-only
  # `date -v` / `stat -f` form silently misbehaved on the Linux CI runner.
  python3 -c "import os,time; t=time.time()-3600; os.utime('$stamp',(t,t))"
  before="$(python3 -c "import os; print(int(os.path.getmtime('$stamp')))")"
  run run_hook
  [ "$status" -eq 0 ]
  after="$(python3 -c "import os; print(int(os.path.getmtime('$stamp')))")"
  [ "$before" = "$after" ]   # not refreshed -> gc did not fire
  sleep 0.3
  ! grep -q "Removed" "$HOME/.claude/session-explorer.log" 2>/dev/null
}

# --- hooks/session-live.sh: lifecycle dispatcher for the live-session registry ---

@test "session-live.sh forwards SessionStart with pid to the CLI" {
  STUB="$BATS_TEST_TMPDIR/cli-args"
  cat > "$BATS_TEST_TMPDIR/session-explorer" <<EOF
#!/usr/bin/env bash
echo "\$@" >> "$STUB"
EOF
  chmod +x "$BATS_TEST_TMPDIR/session-explorer"
  export PATH="$BATS_TEST_TMPDIR:$PATH"
  # Don't let the marketplace CLI resolution win over the PATH stub.
  unset CLAUDE_PLUGIN_ROOT

  echo '{"hook_event_name":"SessionStart","session_id":"s9","transcript_path":"/t/s9.jsonl","cwd":"/repo"}' \
    | "$REPO/hooks/session-live.sh"
  # The CLI call is backgrounded; give it a moment to write.
  sleep 0.5

  run cat "$STUB"
  [[ "$output" == *"live --event SessionStart"* ]]
  [[ "$output" == *"--sid s9"* ]]
  [[ "$output" == *"--pid "* ]]
}

@test "session-live.sh exits 0 even with empty stdin" {
  run bash -c "printf '' | bash '$REPO/hooks/session-live.sh'"
  [ "$status" -eq 0 ]
}
