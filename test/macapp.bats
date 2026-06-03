#!/usr/bin/env bats
# End-to-end build test for `install-app`. macOS-only (skipped elsewhere); uses
# --dest into a tmp dir and --no-dock so it never touches ~/Applications or the
# real Dock.

setup() {
  REPO="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  TMP="$(mktemp -d)"
  if [ "$(uname)" != "Darwin" ]; then skip "macOS-only"; fi
}

teardown() {
  [ -n "$TMP" ] && rm -rf "$TMP" 2>/dev/null || true
}

@test "install-app builds a complete bundle" {
  run "$REPO/bin/session-explorer" install-app --dest "$TMP" --no-dock
  [ "$status" -eq 0 ]

  APP="$TMP/Session Explorer.app"
  LAUNCH="$APP/Contents/MacOS/session-explorer-launch"
  [ -x "$LAUNCH" ]
  [ -f "$APP/Contents/Resources/icon.icns" ]

  # PATH repair + resolver fallbacks present in the launcher.
  grep -q 'export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"' "$LAUNCH"
  grep -q '\$HOME/.local/bin/session-explorer' "$LAUNCH"

  # Regression guard for the Automator icon trap.
  grep -q "CFBundleIconFile" "$APP/Contents/Info.plist"
  ! grep -q "CFBundleIconName" "$APP/Contents/Info.plist"

  # Valid icns.
  run sips -g format "$APP/Contents/Resources/icon.icns"
  [[ "$output" == *"format: icns"* ]]
}

@test "install-app is idempotent" {
  "$REPO/bin/session-explorer" install-app --dest "$TMP" --no-dock
  "$REPO/bin/session-explorer" install-app --dest "$TMP" --no-dock
  run bash -c "ls -d '$TMP'/*.app | wc -l | tr -d ' '"
  [ "$output" -eq 1 ]
}
