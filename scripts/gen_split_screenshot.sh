#!/usr/bin/env bash
# Dev-only setup for the README split-pane screenshot (docs/images/split.png).
#
# The other shots (tree/live/preview/help) are pure-headless SVG exports — see
# gen_screenshots.py. The split shot can't be: its right pane is a REAL claude
# session, so it has to be a genuine tmux split captured from a terminal. This
# script builds that split with FABRICATED explorer data (no leak) and a fresh
# throwaway claude in a scratch project; you type a made-up prompt and capture.
#
# Usage:
#   scripts/gen_split_screenshot.sh            # build the layout, print steps
# Then, in a terminal:
#   tmux -L session-explorer attach -t main    # size the window, frame it
#   <type a short made-up prompt in the right (claude) pane, let it answer>
#   screencapture -o -w docs/images/split.png  # click the window  (or -R region)
#   tmux -L session-explorer kill-server        # tear down when done
#
# Requires: tmux >= 3.1 and the `claude` CLI on PATH. Fabricated data only —
# the explorer tree is sample sessions, the claude pane is a brand-new session
# in /tmp, so nothing from your real history is shown.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${TMPDIR:-/tmp}/se-shot-split"
SOCK="session-explorer"
BIN="$REPO/bin/session-explorer"

rm -rf "$WORK"; mkdir -p "$WORK/acme-api"

# Fabricated explorer index (sample sessions across two projects).
cat > "$WORK/index.json" <<'JSON'
{"version": 2, "sessions": {
  "acme-api/a-auth": {"project_label": "acme-api", "project_path": "/Users/jl/acme-api", "name_cached": "auth/refresh-tokens", "branch": "main", "last_active_at": "2026-06-02T09:55:00+00:00", "created_at": "2026-05-20T00:00:00+00:00", "tokens_estimate": 48000, "tokens_window_pct": 24, "message_count": 36, "first_prompt": "Add refresh-token rotation to the auth service", "notes": ""},
  "acme-api/a-bug": {"project_label": "acme-api", "project_path": "/Users/jl/acme-api", "name_cached": "fix/null-deref", "branch": "main", "last_active_at": "2026-06-02T07:30:00+00:00", "created_at": "2026-05-22T00:00:00+00:00", "tokens_estimate": 12000, "tokens_window_pct": 6, "message_count": 9, "first_prompt": "Investigate the null deref in the parser", "notes": ""},
  "webapp/w-feat": {"project_label": "webapp", "project_path": "/Users/jl/webapp", "name_cached": "feature/live-cart", "branch": "main", "last_active_at": "2026-06-02T09:40:00+00:00", "created_at": "2026-05-28T00:00:00+00:00", "tokens_estimate": 91000, "tokens_window_pct": 45, "message_count": 58, "first_prompt": "Build the live cart total component", "notes": "review before merge"},
  "webapp/w-idle": {"project_label": "webapp", "project_path": "/Users/jl/webapp", "name_cached": "spike/pricing", "branch": "main", "last_active_at": "2026-06-02T08:10:00+00:00", "created_at": "2026-05-27T00:00:00+00:00", "tokens_estimate": 33000, "tokens_window_pct": 16, "message_count": 21, "first_prompt": "Prototype the new pricing tiers", "notes": ""}
}}
JSON

# Suppress the first-run help + retention modals so the tree mounts clean.
: > "$WORK/.session-explorer.help-seen"
: > "$WORK/.session-explorer.retention-declined"
# A scratch source file so the throwaway claude session has something to edit.
printf 'export function refreshTokens() {\n  // rotate and persist refresh tokens\n}\n' > "$WORK/acme-api/auth.ts"

# Real tmux config (F9 switch / F12 fullscreen / status-line hints).
python3 - "$WORK" <<'PY'
import sys; sys.path.insert(0, __import__("os").path.join("bin"))
from _pkg import tmux
work = sys.argv[1]
open(f"{work}/tmux.conf", "w").write(
    tmux.build_config())
PY

tmux -L "$SOCK" kill-server 2>/dev/null || true
tmux -L "$SOCK" -f "$WORK/tmux.conf" new-session -d -s main -n explorer -x 220 -y 50 \
  "env SESSION_EXPLORER_TMUX=1 SESSION_EXPLORER_INDEX=$WORK/index.json SESSION_EXPLORER_TMUX_NO_OFFER=1 $BIN tui"
sleep 2
tmux -L "$SOCK" split-window -h -t main:explorer.0 -l 65% -c "$WORK/acme-api" "claude"

cat <<EOF

Split layout is up on the '$SOCK' tmux server. Next:
  1) tmux -L $SOCK attach -t main      # in a terminal; size/frame the window
  2) accept claude's trust prompt, type a short made-up prompt, let it answer
  3) screencapture -o -w "$REPO/docs/images/split.png"   # click the window
  4) tmux -L $SOCK kill-server          # tear down

Note: the throwaway claude session is recorded in your *real* index/projects
(its env didn't inherit SESSION_EXPLORER_INDEX). Remove it afterwards:
  rm -rf ~/.claude/projects/*se-shot-split*  &&  rm -rf "$WORK"
EOF
