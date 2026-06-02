# Design: Subscription usage bar in the tmux status line

**Date:** 2026-06-02
**Status:** Approved (brainstorming) — pending spec review, then implementation plan

## Summary

Show a small progress bar of Claude subscription **usage** (0–100% of the current
5-hour session limit) plus its reset time in the `session-explorer` tmux status
line — the same number you see from Claude Code's `/usage` command. The bar lives
in the currently-empty `status-left`; the existing `F9 ⇄ switch · F12 ⤢ full`
hint stays on `status-right`. It refreshes every 5 minutes.

Target rendering:

```
 [████░░░░░░░░░░] 18% ↺1:29am          F9 ⇄ switch · F12 ⤢ full
```

## Why this is non-trivial: the data source

There is **no sanctioned local or API path** to the subscription usage percentage:

- No local file caches the `/usage` percentage or a structured reset timestamp
  (searched `~/.claude/`, `~/.claude.json`, `stats-cache.json`, statsig, and the
  JSONL transcripts). The only on-disk reset time is inside a 429 **error** event
  in a transcript — text-only, and only *after* you've been throttled.
- There is **no `claude usage` CLI subcommand** (open feature requests:
  anthropics/claude-code #33978, #50518).
- The live percentage exists only in Anthropic's `anthropic-ratelimit-unified-*`
  **HTTP response headers**, which Claude Code does not persist.
- The Claude Code OAuth token (`sk-ant-oat01-*`) is **rejected by the public
  Messages API** ("OAuth authentication is currently not supported"). Community
  tools that replicate the call by spoofing the Claude Code client are flagged as
  **risking account bans**, and the token expires ~every 8h.

**Decision:** rather than replicate the API call (ban risk, token-refresh
complexity — rejected), we **scrape the official client**: drive a real `claude`
instance to run `/usage` and read the rendered panel via tmux `capture-pane`.
This uses the official client (lowest ban risk, no token juggling) and reuses the
project's existing tmux capture machinery.

## Decisions (resolved during brainstorming)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Data source | Scrape official `claude` `/usage` via tmux | No sanctioned API path; lowest ban risk |
| Refresh cadence | Spawn fresh every **5 min** | Usage moves slowly; ~3-5s startup cost is negligible at 5-min spacing; nothing to keep alive |
| Display location | `status-left` (currently empty) | Keeps the F9/F12 hint untouched on `status-right` |
| Display format | Mini text bar `[████░░░░░░] 18% ↺1:29am` | Matches the `/usage` visual; fits one status line |
| Which bucket | **Session (5-hour) only** | Matches the screenshot; most relevant to "can I keep working now"; fits the width |
| Enablement | **Opt-in, off by default**, TUI toggle key + marker file | Consistent with the project's consent culture (tmux/retention are opt-in); it periodically launches `claude` |

## Architecture

Three isolated units; only the first holds logic worth unit-testing.

### 1. `bin/_pkg/usage.py` — pure core (no I/O)
- `parse_usage(captured_text: str) -> UsageInfo | None`
  - Regex `(\d+)%\s*used` for the percentage and the reset time
    (`resets <h:mm><am/pm> (<tz>)`) out of the captured panel text.
  - Returns `None` on any miss. **Never raises.**
  - `UsageInfo` carries `percent: int`, `reset_label: str` (e.g. `1:29am`),
    optionally `tz`.
- `render_bar(info: UsageInfo, width: int) -> str`
  - Build the `[████░░░░░░] 18% ↺1:29am` string, including tmux `#[fg=…]` markup.
    Fill cells = `round(percent/100 * cells)`. (Color thresholds optional; default
    single color to match the screenshot — keep it simple for v1.)
- Fully unit-testable against saved sample captures — **no `claude` needed in
  tests.** This is the testable seam.

### 2. `bin/_pkg/tmux.py` — orchestration argv builders
Follows the existing `build_*` → `_call`/`_capture` pattern:
- `build_usage_window(cwd)` — `new-window -d -n <probe> -c <cwd> claude` on the
  `-L session-explorer` server.
- `build_send_keys(window, keys)` — wraps `send-keys`.
- `build_capture(window)` — wraps `capture-pane -p`.
- `build_kill_window(window)` — backstop teardown.
- `set_status_left(text)` / config: `set -g status-left <text>` and
  `set -g status-left-length <N>`.

### 3. `bin/_pkg/tui.py` — scheduler
- `set_interval(300, self._refresh_usage)` (only when usage bar is enabled **and**
  `SESSION_EXPLORER_TMUX=1`).
- The probe runs in a **Textual thread worker** (`run_worker(..., thread=True)`)
  so the bounded waits/sleeps never block the event loop.
- On success, push the rendered string via `set -g status-left`. On failure,
  leave the prior value or clear it; log and move on.

## Scrape flow (every 5 min)

1. **Spawn** `claude` detached in a hidden window on the `-L session-explorer`
   server, in a **fixed probe cwd** `~/.claude/.session-explorer-probe/`, with env
   `SESSION_EXPLORER_PROBE=1`.
2. **Trust prompt handling** (the probe cwd is new):
   - Preferred: **pre-trust** the dir by adding it to `~/.claude.json`'s
     trusted-projects list (verified safe in M0). Then no prompt appears.
   - Fallback: detect *"Do you trust the files in this folder?"* in the captured
     pane and `send-keys` the accept selection.
3. **Wait for ready** — poll `capture-pane` until the input prompt is present,
   bounded timeout (e.g. ≤15s).
4. **Send** `send-keys '/usage' Enter`.
5. **Wait for panel** — poll `capture-pane` until `% used` text appears, bounded
   timeout.
6. **Capture** `capture-pane -p` → text → `parse_usage`.
7. **Exit cleanly** — `send-keys '/exit' Enter` (with `Ctrl-C`/`q` fallbacks) to
   let claude shut down, then `kill-window` as a backstop so nothing lingers.
8. **Clean up litter** — delete the probe transcripts (see below).
9. **Update bar** — `render_bar` → `set -g status-left`.

## Transcript litter — mitigation

A throwaway `claude` writes a JSONL transcript every run (~288/day naively).
Contained by:
- **Fixed probe cwd** → all probe transcripts land in one predictable project
  folder under `~/.claude/projects/`.
- **Delete that folder's transcripts** right after each capture.
- **`SessionStart` hook skips recording** when `SESSION_EXPLORER_PROBE=1` is set,
  so probe sessions never enter the index, live registry, or tree.

(The persistent-session alternative would reuse one transcript and avoid the
startup cost, at the price of lifecycle/cleanup complexity that conflicts with the
project's clean-shutdown rules. Spawn-fresh + cleanup was chosen; the litter is
fully contained.)

## Failure handling — degrade silently, never block

Not logged in / `claude` missing / trust prompt unresolved / parse miss / any
timeout → leave `status-left` at its prior value (or clear it), log to
`~/.claude/session-explorer.log`, return clean. The worker swallows all
exceptions. This mirrors the project's "hooks never block startup" contract.

## Enablement & teardown

- **Off by default.** A marker file `~/.claude/.session-explorer.usage-bar`
  signals "enabled" (persists across launches). Nothing polls while off and
  `status-left` stays empty.
- A single new **TUI toggle key** flips it (respecting the project's
  no-case-variant-keybindings rule — one distinct key, e.g. `g` for "gauge",
  subject to availability check against existing bindings).
- **Enable (`g`):** write the marker, **fire one probe immediately** so the bar
  appears within a few seconds, then start the 5-min `set_interval`.
- **Disable (`g` again):** remove the marker, cancel the interval, clear
  `status-left`.
- **Force-refresh is implicit:** because enabling always does an immediate probe,
  toggling `g` off-then-on is the manual "check now" path — no separate refresh
  key is needed.
- On quit, clear `status-left` so no stale bar remains.
- Feature is inert when not tmux-hosted.

## Scope boundaries (YAGNI)

- Session (5-hour) bucket only — no weekly/Opus/Sonnet breakdown in v1.
- No color-threshold gradient required for v1 (single color); may add later.
- No configurable cadence/format in v1 (5 min, fixed format).
- No in-bar interactivity.

## Testing

- **Unit:** `parse_usage` (multiple real capture fixtures incl. malformed/empty →
  `None`) and `render_bar` (fill math, width clamping, markup) in
  `test/test_usage.py`. No `claude` dependency.
- **Manual (M0 + integration):** the spawn/capture/exit orchestration is thin and
  environment-dependent; verified manually on a logged-in machine.

## Milestones

- **M0 — Feasibility gate (do first).** Manual throwaway probe: spawn `claude` in
  the probe cwd, handle the trust prompt, run `/usage`, `capture-pane`, and
  confirm the panel renders as cleanly-parseable text with the session % and reset
  time. Confirm `/exit` + `kill-window` leaves nothing behind. **If this fails or
  needs interactive navigation we can't script, revisit the approach before
  building further.**
- **M1 — Pure core.** `usage.py` + unit tests (TDD).
- **M2 — Orchestration.** `tmux.py` builders + the `tui.py` thread-worker
  scheduler; litter cleanup; hook `SESSION_EXPLORER_PROBE` skip.
- **M3 — Enablement.** Toggle key + marker file + teardown.
- **M4 — Docs.** Update `SPEC.md` (authoritative) and README; bump version.

## SPEC.md alignment

Per `CLAUDE.md`, `SPEC.md` is authoritative. This feature **adds** to it (new
status-line usage segment, probe mechanism, `SESSION_EXPLORER_PROBE` hook gate,
new opt-in marker). `SPEC.md` must be updated in the same change as the
implementation (M4), not left to diverge.

## Open risks

1. **M0 is genuinely gating** — if `/usage` requires interactive navigation or
   doesn't render capturable text, the whole approach needs rethinking.
2. **Anthropic could change `/usage`'s output format**, breaking the parser. The
   `parse_usage → None` path degrades to "no bar" rather than a crash, and the
   parser is isolated/easy to update.
3. **Per-run startup cost** (~3-5s of a hidden `claude`) every 5 min — acceptable,
   off the UI thread, but noted.
