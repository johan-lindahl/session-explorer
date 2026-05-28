# TUI rescan key + empty-state message — design

**Date:** 2026-05-28
**Status:** Approved, pre-implementation

## Problem

On a fresh machine the explorer opens empty. Two compounding causes:

1. **Pre-install sessions are never auto-imported.** `index.backfill()` scans
   `~/.claude/projects/`, but nothing calls it automatically — not `install.sh`,
   not the plugin manifest, not the `SessionStart` hook, not the TUI. It is
   reachable only by manually running `session-explorer index --backfill`.
   So a new user sees none of their history. (SPEC §"index --backfill" already
   notes pre-install sessions are invisible without it.)
2. **Even after import, every session is unnamed** and unnamed sessions are
   hidden by default, so the *default* view stays empty until the user presses
   `u`. A colleague hit exactly this: opened the explorer, saw nothing, pressed
   `u`, still nothing — because the index itself was empty.

## Non-goals / constraints preserved

- **No SessionStart/startup changes.** Backfill stays out of the hook path, so
  the "hooks never block startup" rule is untouched. Index-building is
  TUI-driven and user-triggered.
- **"Unnamed hidden by default" stays.** We do not change the default
  visibility rule; we make the empty moment self-explanatory instead.
- **Single entry point.** No new slash command. The rescan lives inside the TUI.
- **JSONL is authoritative.** Reindex re-reads names/metadata from the JSONL and
  preserves user-only fields (notes); it never rewrites transcripts.

## Design

### 1. `index.reindex(index_path) -> dict`

New function in `bin/_pkg/index.py`. Runs `refresh_all()` **then** `backfill()`,
in that order so each session is touched exactly once:

- `refresh_all` recomputes cached fields for already-tracked sessions and prunes
  entries whose JSONL was deleted.
- `backfill` then adds any session under `~/.claude/projects/` not yet tracked.

On an empty index, `refresh_all` is a no-op and `backfill` records everything.
Returns `{"added": int, "total": int}`.

**Safety:** non-destructive. `record_session` merges `{**existing, ...}`, so
`notes` survive; names survive because `name_cached` is re-read from the
`custom-title` event in the JSONL. Empty user-created folders live in the
separate folder store, which reindex doesn't touch. The only removals are
index entries whose JSONL no longer exists (intended pruning).

### 2. Empty-state message — pure `_empty_state_text(...)`

A pure, unit-testable function in `bin/_pkg/tui.py` returning the message string
(or `None` when rows are visible). Branch order:

1. `visible > 0` → `None` (tree renders normally; no message).
2. filter active → "No sessions match the current filter. Press Esc to clear."
3. `unnamed_hidden > 0` → "N unnamed session(s) hidden. Press u to show them,
   then r to name one."
4. index empty & not yet scanned → "No sessions indexed yet. Press R to scan
   ~/.claude/projects/ for your sessions."
5. index empty & already scanned → "No sessions found under ~/.claude/projects/."

Rendered in a `Static` (`#empty-state`) inside the tree pane, shown only when the
message is non-`None`; the `Tree` and column header are hidden in that case.
App-level bindings (`u`, `R`, `q`, `h`) still fire while the message shows.

### 3. Rescan key `R`

- `Binding("R", "rescan", "Rescan")` on the app (`r` stays rename; `R` is
  shift-r).
- `action_rescan` sets a `scanning ~/.claude/projects/…` subtitle and launches a
  Textual `@work(thread=True, exclusive=True)` worker that calls
  `index.reindex`, then repaints via `self.call_from_thread(self._populate)`.
  Running in a worker keeps the UI responsive (reindex shells out to `git` per
  session, so it can take seconds on a large history).
- Added to the `check_action` modal-guard so it can't fire over a modal.
- A `_scanned` flag flips after the first rescan so the empty-state message can
  switch from "Press R to scan" to "No sessions found".

### 4. Docs

- `_help_text` gains an `R` row.
- **SPEC.md** keybinding table gains `R`; the backfill paragraph notes the TUI
  rescan as the user-facing trigger; the unnamed/visibility paragraph notes the
  empty-state hint.

## Testing

- **pytest `test_index.py`:** `reindex` adds untracked sessions, prunes entries
  whose JSONL is gone, and preserves `notes` + custom-title names across a run.
- **pytest `test_tui.py`:** `_empty_state_text` returns the correct branch for
  each of the five states above.

## Flow for the reported bug

Colleague opens the explorer → sees "Press R to scan" → presses `R` → sees
"N unnamed sessions hidden — press u" → presses `u` → his history appears.
