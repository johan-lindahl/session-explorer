# Active-session indicator — design

**Date:** 2026-05-29
**Status:** Implemented on branch feat/active-session-indicator (PID-capture spike + manual smoke test pending).
**Affects:** `SPEC.md` (new feature — must be reflected there during implementation), `plugin.json`, `install.sh`/`uninstall.sh`, `hooks/`, `bin/_pkg/` (new `live.py`, `cli.py`, `tui.py`, `tree_model.py`), `test/`

## Goal

Show, inside the explorer TUI, which Claude Code sessions are **currently live** on the
machine — so a user running 2–3 agents at once can glance at the tree and see which are
**actively working** (animated) versus **open but idle**, with everything else shown as
inactive. Liveness must scale to multiple concurrent sessions, not just "the last one
started".

## Why the obvious approaches don't work

Investigated and rejected as the primary signal:

- **`~/.claude/.session-explorer.current`** — the existing hook writes only the *last*
  started session id here. Single-valued; cannot represent 2–3 concurrent sessions.
- **`flock` on the JSONL** (`gc.py:_is_live`) — empirically dead for real sessions.
  Tested against a live transcript: `lsof` shows no open handle and a non-blocking
  `LOCK_EX` is acquired immediately. Claude Code opens → appends → closes; it holds no
  persistent lock. So `flock` cannot detect an open-but-idle session.
- **JSONL `mtime`** — reliably detects *actively writing*, but an open-but-idle session
  (waiting for user input) has no recent write and is indistinguishable from a closed one.
- **The Claude/Anthropic API** — inference-only (Messages/Batches/Files/Models + org-level
  usage billing). It has no knowledge of local CLI sessions and no "list active sessions"
  endpoint. Liveness must come from a local signal.

**Conclusion:** reliable open-but-idle detection requires Claude Code **lifecycle hooks**
maintaining a live-session registry, with a **crash-safe** death signal because
`SessionEnd` is unreliable (SIGKILL / terminal-close / crash bypass it).

## Architecture overview

```
Claude Code lifecycle hooks ──► hooks/session-live.sh ──► session-explorer live --event …
                                                              │ (flock + temp-rename)
                                                              ▼
                                          ~/.claude/session-explorer-live.json  (registry)
                                                              ▲
                                                              │ read every ~2s + kill -0 pid
                                       TUI refresh timer ─────┘ ──► per-row glyph (spinner/○/none)
```

Three moving parts: (1) a volatile **registry** file, (2) **hooks** that maintain it,
(3) the **TUI** reading it on a timer and animating working rows.

## 1. Live-session registry (new sidecar file)

New file `~/.claude/session-explorer-live.json`, written with the **same flock +
temp-file-rename atomic pattern** as the index (reuse `index.py`'s `load`/`save`/`mutate`
helpers — do not reinvent). Schema:

```json
{
  "version": 1,
  "sessions": {
    "<session_id>": {
      "state": "working",            // "working" | "idle"
      "pid": 12345,                   // Claude process pid captured at SessionStart
      "last_seen": "2026-05-29T07:08:00Z",
      "transcript_path": "/Users/.../<uuid>.jsonl",
      "cwd": "/Volumes/Projects/ClaudeSessionExplorer"
    }
  }
}
```

Properties:

- **Separate from index and folder store.** Volatile runtime state only. Never merged into
  the index; never read by retention / `--gc`. The index keeps its existing fields
  unchanged (no new persistent `active`/`live` field — same spirit as "kept is implicit").
- Lives at a path derived the same way as the index path (sibling in `~/.claude/`).

## 2. Hooks (event-driven state machine)

One new dispatcher script `hooks/session-live.sh`. It reads the JSON payload on stdin,
extracts `hook_event_name`, `session_id`, `transcript_path`, `cwd`, and (for SessionStart)
the parent pid, then calls `session-explorer live --event <name> --sid <id> [...]`. The CLI
does the flock'd registry mutate (reuse `index.mutate`).

| Hook event | Matcher | Registry action |
|---|---|---|
| `SessionStart` | — | upsert entry; `state=idle`; record `pid` (§4); `last_seen=now` |
| `UserPromptSubmit` | — | `state=working`; `last_seen=now` |
| `Stop` | — | `state=idle`; `last_seen=now` |
| `Notification` | `idle_prompt` | `state=idle`; `last_seen=now` |
| `SessionEnd` | — | remove entry (best-effort) |

- State stays `working` for the **whole turn** (between `UserPromptSubmit` and `Stop`), so a
  long-running tool call with no JSONL writes is still correctly "working".
- Hook updates must be **non-blocking** — run async (if Claude Code's `async` hook option is
  available) or background the work — so they never add latency to a turn. Failures are
  logged to `~/.claude/session-explorer.log` and exit 0, matching the existing hook's
  "never block startup" discipline.
- The existing `SessionStart` registration (index `--record`, retention GC) is preserved.
  Whether the live-registry SessionStart action is folded into the existing
  `session-start.sh` or added as a second SessionStart command is an implementation detail
  (Claude Code allows multiple commands per event).

## 3. TUI rendering + refresh timer

Today the TUI is static (refreshes only on F5). Add two `set_interval` timers:

- **Animation tick (~200 ms, tunable):** advances the braille spinner frame for every row
  currently in `working` state and rewrites *only those rows'* labels in place. Requires a
  `session_id → TreeNode` map built during `_populate()`.
- **Registry poll (~2 s, tunable):** re-reads `session-explorer-live.json`, runs
  death-detection (§4), recomputes each session's state (working / idle / inactive), and
  re-renders only the rows whose state changed. **Exception:** when a transition changes a
  row's *visibility* — an unnamed session going live (must appear) or a surfaced live
  unnamed session dying (must disappear) — the poll triggers a full `_populate()` rebuild
  rather than an in-place label rewrite, since tree membership changed (see §6).

Neither timer re-reads JSONLs or reindexes — that stays on F5. When no sessions are live the
animation tick is effectively a cheap no-op.

**Glyph column** (matches the approved visual): animated braille spinner (frames
`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`, green) for **working**; steady dim `○` for **open but idle**; nothing for
inactive. The glyph is a leading column in `_row_label()` and must not disturb the existing
stat-column alignment (the name field width already adapts to tree depth).

## 4. Death detection — PID liveness + TTL backstop

`SessionEnd` cannot be trusted (SIGKILL / terminal-close / crash bypass it), so removal is
best-effort only. Ground truth for "still alive":

- At `SessionStart`, the hook records the **Claude process pid** (the hook's parent process)
  in the entry.
- On each registry poll, an entry is **alive iff `os.kill(pid, 0)` succeeds**. This catches
  crash / kill / terminal-close that `SessionEnd` misses, and keeps an idle session shown for
  as long as its process lives.
- **TTL backstop:** if `kill -0` *succeeds* but `last_seen` is older than a generous TTL
  (default **24h**, tunable), prune anyway — guards against PID-reuse zombies.
- Dead entries are pruned from the registry during the poll, under flock. A stale registry
  left over from a reboot self-heals on the first poll.

### Validation risk (resolve first during implementation)

PID capture assumes `hooks/session-live.sh`'s parent process *is* the Claude process. If
implementation reveals the hook runs under a transient wrapper shell (so the recorded pid
dies immediately), fall back to **TTL-only** death detection. This must be verified
empirically (inspect `$PPID` / process tree from inside a real hook invocation) **before**
committing to the PID path. Document the outcome in `SPEC.md`.

## 5. Install / settings registration

- **Marketplace path:** add the four new events (`UserPromptSubmit`, `Stop`, `Notification`
  with `idle_prompt` matcher, `SessionEnd`) to `plugin.json`'s `hooks` block — auto-registered.
- **Plain `install.sh` path:** `install.sh` already registers the SessionStart hook in
  `settings.json`; extend it to register the four new events. `uninstall.sh` removes them.
- This is **independent of retention** — it does not touch `cleanupPeriodDays`, the backup
  file, or the opt-in flow. Hook *registration* has always been install.sh's job; only
  `cleanupPeriodDays` is the review-sensitive bit, and it is untouched here.

## 6. Edge cases

- **Live but unnamed session — surfaced as an exception to the hide rule.** Unnamed sessions
  are hidden by default, but an actively-running agent is exactly what the user wants to see.
  **Decision:** any session that is currently **live (working or idle) is shown regardless of
  the unnamed filter** — `build_nested_tree()`'s unnamed-filter gains an "OR is-live" escape
  hatch. The subtitle shows the active count, e.g. `● 2 active`. When a live session ends and
  it is unnamed, it reverts to hidden on the next poll (handled by the visibility-change
  re-populate in §3). This is orthogonal to the `u` toggle: pressing `u` still reveals *all*
  unnamed sessions; the exception only forces *live* unnamed ones to always be visible.
  Liveness is "shown", not "named/kept" — it never writes a name and never affects retention.
- **Stale registry after reboot/crash.** First poll prunes dead pids; self-heals.
- **The session that launched the TUI** shows as `working` (it ran the slash command).
  Expected and harmless.
- **Registry references a session not yet in the index.** SessionStart records both the index
  and the registry, so the row exists; if not, the poll ignores unknown sids.

## 7. Testing

- `test/test_live.py` (pytest): registry `mutate` atomicity; each event's state transition;
  `kill -0` alive vs dead pruning; TTL backstop; registry/index isolation (live writes never
  touch the index).
- `test/hook.bats`: extend for `session-live.sh` dispatch across the events (correct CLI
  invocation per `hook_event_name`).
- TUI unit test: `session_id → state → glyph` mapping and spinner-frame advance (pure
  function; no real timer).
- CI (`.github/workflows/ci.yml`) already runs both suites on ubuntu + macOS — new tests ride
  along. Note: `os.kill(pid, 0)` semantics differ on Windows (out of scope; M5 territory).

## Tunables (defaults)

| Knob | Default | Notes |
|---|---|---|
| Spinner tick | 200 ms | animation smoothness vs. CPU |
| Registry poll | 2 s | freshness vs. flock churn |
| Death TTL backstop | 24 h | guards PID reuse; PID check does the real work |
| Spinner frames | `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` | braille |

## Out of scope (v1 of this feature)

- Windows `kill -0` equivalent (defer to M5 Linux/Windows work).
- Surfacing per-session activity history / timeline.
- Any change to retention, `--gc`, or `cleanupPeriodDays`.
