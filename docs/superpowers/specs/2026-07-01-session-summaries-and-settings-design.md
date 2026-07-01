# Session summaries + Settings screen + delete-worktree cleanup

**Date:** 2026-07-01
**Status:** Approved, implementing
**Scope:** New modules `bin/_pkg/summary.py`, `bin/_pkg/summarize.py`; changes to
`tui.py` (bottom preview relocation, `u` action, `SettingsScreen`, delete confirm),
`delete.py`, `gc.py`, `worktree.py`, `retention.py`, `ui_state.py`, `uninstall.py`,
`hooks/session-start.sh`. New sidecar `~/.claude/session-explorer-summaries.json` and
markers `~/.claude/.session-explorer.summaries-auto` / `.summaries-prompted` (both removed
by `uninstall`). Docs: `SPEC.md`, `CLAUDE.md`, `README.md`, help screen, `CHANGELOG.md`.

## Problem

Users accumulate many named/parked sessions and can't remember what each was about. The
name (folder path + display) is the only descriptor, and the toggled right-hand preview
only shows metadata + the first prompt — neither tells you what actually *happened* in a
session.

Three concrete gaps, all confirmed against the code:

1. **No summary of a session's content exists anywhere.** `jsonl.first_user_prompt` gives
   the opening prompt; nothing summarises the work done.
2. **The right-hand preview pane collides with the docked session.** The explorer is the
   *left* tmux pane; its own layout is `Horizontal(treepane, preview)`, so when a session
   docks as the right tmux pane the preview gets squeezed into `[tree | preview] | [claude]`
   — cramped and adjacent to the very session it describes.
3. **Deleting a session orphans its worktree.** `delete_session` (`delete.py`) removes only
   the JSONL + index row; the worktree dir *and* `worktree-<slug>` branch are left behind.
   `collect_worktrees` can never reclaim them afterward because it iterates the *index*,
   which no longer has the row. Dead worktrees pile up.

Separately, persisted preferences are scattered and some are lock-out traps: **retention**
is a one-time first-run prompt with *no way to change your mind* (`retention.is_decided`
returns true forever once enabled or declined); the **usage bar** is a persisted marker
reachable only via `g`; the **queues pane** is persisted (`ui_state.queue_pane_visible`)
reachable only via `q`; **tmux** decline is likewise permanent (`_maybe_offer_tmux` never
re-offers once `.session-explorer.tmux-declined` exists). There is no single place to see
or change what's toggleable.

## Non-goals

- **No bulk backfill.** The existing backlog is summarised on demand with `u`, one session
  at a time. (Considered and dropped to bound token spend and UI complexity.)
- **No summariser model/threshold UI.** Model and message threshold are code constants for
  v1 (a checkbox screen is the wrong place for a model picker).
- **No tmux *disable*.** tmux is the backbone of live docking; the Settings row is
  status + set-up only, never a disable (avoids a footgun that silently kills docking).
- **No persisted default for view mode (`Tab`) / collapse (`z`).** They are live view states
  that reset each launch; persisting them is new scope and they're flipped constantly.
- **No in-place `/compact`** (unchanged deferral from the main spec).

## Design

### 1. Summary generation — `bin/_pkg/summarize.py`

Textual-free runner that turns a transcript digest into summary text by shelling out to the
Claude Code CLI in headless mode.

- `run(digest: str, *, model: str, timeout: float) -> str` — spawns
  `claude -p <instructions>` with `digest` piped on **stdin**, returns the trimmed stdout,
  raises on non-zero exit / timeout / missing binary.
- Spawned with env `SESSION_EXPLORER_SUMMARIZER=1` **and** `SESSION_EXPLORER_PROBE=1` set,
  so our own `SessionStart` hook bails at its existing early-exit guard (`session-start.sh`
  currently bails on `SESSION_EXPLORER_PROBE=1`; we add `SESSION_EXPLORER_SUMMARIZER` to the
  same line for clarity). The summariser call therefore records **no** index row, current
  pointer, or GC — it leaves no trace. It uses no tools, so `pre-tool-use.sh`/root-guard
  never fires.
- **Model default:** `claude-haiku-4-5` (cheap, fast; summaries don't need Opus). Named
  constant `SUMMARY_MODEL` in `summarize.py`.
- **Timeout:** `SUMMARY_TIMEOUT = 90.0` s. On timeout the subprocess is killed and `run`
  raises.
- **Prompt:** asks for a concise 3–5 sentence / bulleted summary of *what the session was
  about and what was accomplished*, no preamble.

CLI resolution mirrors the hook's (`CLAUDE_PLUGIN_ROOT/bin`, `~/.local/bin`, `PATH`); we
resolve `claude` itself via `shutil.which("claude")` and raise a clear error if absent
(surfaced as a toast, logged).

### 2. Summary store + digest — `bin/_pkg/summary.py`

Pure logic, no Textual, no subprocess.

**Sidecar:** `~/.claude/session-explorer-summaries.json`

```json
{
  "version": 1,
  "summaries": {
    "<sid>": {
      "text": "Refactored auth token refresh; fixed the 401 retry loop; added tests.",
      "generated_at": "2026-07-01T12:00:00Z",
      "msg_count": 47,
      "model": "claude-haiku-4-5"
    }
  }
}
```

- `default_path_for(index_path)` → sibling of the index (mirrors folder-store/ui-state).
- `load(path) -> dict`, default-on-corruption shape (like the other stores).
- `save(path, data)` — **flock + temp-file-rename** (two sessions can exit concurrently),
  matching the index write pattern.
- `get(path, sid) -> dict | None`, `set(path, sid, entry)`, `remove(path, sid)`.
- `build_digest(transcript_path, *, max_chars=48000) -> str` — reads the JSONL and distills
  a readable transcript: **user text turns** and **assistant text blocks** only. Drops
  `tool_result` content, `file-history-snapshot`, thinking, and non-message line types. If
  the digest exceeds `max_chars`, keep head + tail with an elision marker in the middle (the
  start frames intent, the end frames the outcome). Reuses `jsonl._iter_messages`.
- `is_stale(entry, current_msg_count) -> bool` — `current_msg_count > entry["msg_count"]`
  (the session grew since it was summarised).

**Consent markers:**

- `auto_marker(claude_dir)` → `.session-explorer.summaries-auto`; `auto_enabled(claude_dir)
  -> bool` (marker present) and `set_auto(claude_dir, on)` create/remove it.
- `prompted_marker(claude_dir)` → `.session-explorer.summaries-prompted`;
  `prompted(claude_dir) -> bool` and `mark_prompted(claude_dir)` record that the one-time
  first-run intro (§3, *First-run discoverability*) has been shown, so it isn't re-shown.

Auto-on-exit defaults **off**. It is turned on either by the one-time first-run prompt or in
the Settings screen (the permanent re-toggle) — mirroring how retention pairs a first-run
nudge with a settings re-toggle. Nothing spends tokens until the user opts in (or presses
`u`), and there is no permanent lock-out (Settings always flips it back).

### 3. Triggers

**Auto-on-exit.** `_poll_live()` already computes `ended = prev_live - set(new_states)` and
calls `_maybe_offer_worktree_cleanup(ended)`. Add `self._maybe_summarize(ended)` alongside
it. `_maybe_summarize`:

- returns immediately unless `summary.auto_enabled(claude_dir)`;
- acts only on `self._docked_sid` when it is in `ended` (the session you just left);
- requires the session to be **named** (`name_cached`) and to have **≥ `SUMMARY_MIN_MSGS`
  (=8)** JSONL messages;
- launches a **guarded `@work(thread=True)` worker** — same shape as `_live_meta_tick`
  (try/except → `_log_line` on failure, never let a worker exception exit the app, per the
  CLAUDE.md `@work` rule). The worker: `build_digest` → `summarize.run` → `summary.set` →
  `call_from_thread(self._refresh_preview)`.

**Manual `u` (Update).** `action_update_summary`:

- session leaf only (else `bell()`);
- **refuses live rows** (transcript is mid-write) with a toast;
- **bypasses** the message threshold (explicit user intent) but refuses a session with no
  transcript on disk;
- runs the same guarded worker; shows a transient "Summarising…" state in the preview for
  that sid until the worker returns.

**First-run discoverability.** Because auto-summaries default off and the summary lives in
the `Space`-toggled pane, a one-time onboarding makes the feature findable:

- Shown once, guarded by `summary.prompted`, on `on_mount` **after** the existing
  retention/tmux/help onboarding, and only when the index already holds **≥1 named session**
  (returning users with a backlog — the target user — get it; a brand-new empty install
  doesn't nag and gets it on a later launch once sessions exist).
- A `ConfirmScreen`: *"session-explorer can summarise what each session was about — shown in
  the details pane (Space). Auto-summarise sessions when you leave them? You can also press
  `u` to summarise the selected one anytime."* Yes → `summary.set_auto(on)`. Either answer →
  `summary.mark_prompted`.
- Immediately after the dialog closes, **reveal the preview pane once**
  (`_preview.display = True`) so the user sees where summaries appear; `Space` toggles it
  thereafter. First run only — subsequent launches respect the user's last toggle state.

### 4. Display — relocate preview to the bottom + Summary section

**Relocation.** In `compose()`, move `self._preview` from the right of the `Horizontal` into
the `treepane` `Vertical`, between the tree and the queues pane:

```
Vertical(colheader, tree, preview, queues, empty, id="treepane")
```

The top-level `Horizontal` wrapper is dropped (only `treepane` remains). CSS: `#preview`
loses `border-left`, gains `border-top: solid $accent` and a `max-height` cap (like
`#queues` at `40%`) so the tree keeps most of the height. `Space` still toggles it; default
hidden (unchanged).

**Summary section.** `_preview_text(s)` gains a `Summary` block (after `Notes`, before
`First prompt`): the stored text, or `(no summary — press u to generate)`. If the stored
entry is stale vs the current `message_count`, the header reads `Summary (may be stale)`.
Live sessions keep showing the live tail via `_render_live_preview` (no summary shown for a
running session).

**Filter for free.** `_matches` already searches `s.get("summary")` (`tui.py:1072`). In
`_populate`, load the summaries sidecar once and merge each entry's `text` into the
in-memory session dict as `s["summary"]` (and its `msg_count` as `s["summary_msg_count"]`
for staleness). Summaries become searchable via `/` with no on-disk index change.

### 5. Settings screen — `SettingsScreen`

New `ModalScreen` (a `_PanelScreen` subclass), bound to **`,`** (`action_settings`). Rows
navigated with `↑`/`↓`, toggled with `Enter`/`Space`, closed with `Esc`. Each row reads its
live state on open and writes through on toggle. Rows:

| Row | Backing store | Toggle behaviour |
|---|---|---|
| **Auto-summarize on exit** | `.session-explorer.summaries-auto` | `summary.set_auto`. Default off. |
| **Auto-delete unnamed sessions after `N` days** | retention markers + `ui_state.retention_days` | see below |
| **Usage bar** | `.session-explorer.usage-bar` | reuse `_usage_enabled` / start/stop; row mirrors `g`. Only shown/active in the tmux-hosted layout. |
| **Queues pane** | `ui_state.queue_pane_visible` | reuse `action_toggle_queues` state; row mirrors `q`. |
| **tmux hosting** | detection + install offer | status/set-up only (below) |

**Retention row.** Toggle + an editable integer `N` (default 30).

- Label: *"Auto-delete unnamed sessions after N days"* — explicit that **named sessions are
  never auto-deleted** (the "kept = has a name" invariant).
- **On** → `retention.enable(claude_dir)` (backs up + sets `cleanupPeriodDays=36500`, exactly
  as the first-run prompt does today) and stores `N` in `ui_state.json`.
- **Off** → new `retention.disable(claude_dir)`: restore `cleanupPeriodDays` from the backup
  and remove the backup file (the same restore `uninstall` performs). This is the first-ever
  way to turn retention back off from the UI; review-sensitive (touches `settings.json`).
- Editing `N` (only meaningful when on): a small input (reuse the `_PanelScreen`/`Input`
  pattern) writes `ui_state.retention_days`. `collect_garbage` reads it (default 30 when
  absent). `ui_state.json` stays single-writer (only the explorer writes; `--gc` reads).
- The first-run retention prompt in `on_mount` is unchanged (still the initial nudge); the
  row is the re-toggle it never had.

**tmux row.** Read-only status with one conditional action, **never a disable**:

- Installed / hosting on → *"tmux hosting: on"* (read-only).
- Not installed → *"tmux hosting: not set up"* + a **Set up** action that reuses the existing
  `_maybe_offer_tmux` install-how dialog (`tmux_install.install_command`). Choosing Set up
  also removes `.session-explorer.tmux-declined` so it's no longer suppressed. This
  re-surfaces the offer after a decline (fixing that lock-out) without ever tearing down a
  running host.

### 6. Delete cascades to the worktree

**New primitive** `worktree.purge(path) -> str` (distinct from the unchanged
`worktree.remove`): removes the working directory with `git worktree remove` (**no
`--force`**), then attempts `git branch -d worktree-<slug>` (safe delete — git refuses if
unmerged). Returns one of `"removed"` (dir gone, branch gone), `"removed_branch_kept"`
(dir gone, branch unmerged/kept), `"dirty"` (git refused the dir — uncommitted changes),
`"error"`. Slug derived from the path leaf under `.claude/worktrees/`. `worktree.remove`
stays byte-for-byte the same for the `w` action, the docked-exit offer, and
`collect_worktrees` (those keep the branch so resume can rebuild).

**Manual `d`.** `action_delete`'s confirm text mentions the worktree + reclaimable size when
the session has one. On confirm, `delete_session` runs, then (if the row had a worktree
path on disk) `worktree.purge` runs and the outcome is reported: *"Deleted; worktree removed
(freed X)."* / *"Deleted; worktree kept — uncommitted changes."* / *"Deleted; worktree
removed, branch kept — unmerged commits."* Live sessions still can't be deleted (existing
guard).

**Retention gc.** `collect_garbage` gains worktree cleanup: for each unnamed session it
deletes, if `worktree.MARKER in project_path` and the dir exists, call `worktree.purge`.
Headless — results are logged, not toasted. `worktree.py` is already Textual-free so `gc.py`
can import it (it already imports it for `collect_worktrees`).

**Delete drops the summary.** Both `delete_session` and `collect_garbage`'s stub-expiry
remove the sid's entry from the summaries sidecar.

## Data model / files

New / changed on disk under `~/.claude/`:

- `session-explorer-summaries.json` — the sidecar (§2). New.
- `.session-explorer.summaries-auto` — auto-on-exit flag. New.
- `.session-explorer.summaries-prompted` — one-time onboarding-shown flag. New.
- `session-explorer-ui.json` — gains `retention_days` (int, default 30). Changed.
- No change to `session-explorer-index.json` (summaries are merged in memory only).

**Uninstall teardown** (`uninstall.py`): remove `session-explorer-summaries.json` and the
`.session-explorer.summaries-auto` / `.summaries-prompted` markers, in addition to the
existing `cleanupPeriodDays` restore from backup.

## Keybindings

- **`u`** — Update (regenerate) the selected session's summary. New; free.
- **`,`** — open the Settings screen. New; free.
- `Space` still toggles the (now bottom) preview. Unchanged key, new location.
- No `a` key (auto-summaries lives in Settings). `g`/`q`/`z`/`Tab` unchanged.

Both new keys are added to the help screen (`_help_text`) and the README keybinding list.

## Consent & defaults

- Auto-summaries default **off**; turned on by the one-time first-run prompt (§3) or in
  Settings. Nothing spends tokens until the user opts in (or presses `u`).
- `u` and the Settings-driven summarise are explicit user actions.
- Retention behaviour is unchanged except for the new UI re-toggle and configurable period.

## Edge cases

- **`claude` binary missing / errors / times out** → worker logs to
  `~/.claude/session-explorer.log`, toasts once, never crashes (guarded worker).
- **Dangling `transcript_path`** (hook recorded a path claude hasn't created) → `build_digest`
  finds no file → skip (no summary), same defensive posture as the rename/move paths.
- **Concurrent exits** → sidecar writes are flock-guarded.
- **Session resumed after summarising, then exits again** → auto-on-exit overwrites; manual
  `u` overwrites; stale flag covers the in-between (session grew, not yet re-summarised).
- **Dirty worktree on delete** → `git worktree remove` refuses; dir kept; user told; session
  still deleted (as they asked). No data loss.
- **Unmerged worktree branch on delete** → `git branch -d` refuses; branch kept; reported.
- **Summariser session polluting the index** → prevented by the hook early-bail env guard.
- **tmux Set-up chosen while already installed** → no-op path (status shows "on"); the Set up
  action only appears when not installed.

## Testing (TDD)

Pure units (no app, no network):

- `summary.py`: digest extraction (drops tool noise; head+tail elision over `max_chars`),
  store load/save/get/set/remove, flock round-trip, `is_stale`, `auto_enabled`/`set_auto`,
  `prompted`/`mark_prompted`.
- `summarize.py`: `run` with a **mocked subprocess** (stdout captured; non-zero → raises;
  timeout → raises; env includes the guard vars; stdin carries the digest).
- `worktree.purge`: against throwaway git repos — merged branch deleted, unmerged branch
  kept (`removed_branch_kept`), dirty dir refused (`dirty`).
- `delete.py`: `delete_session` removes the summary entry; worktree cascade invoked when a
  worktree path is present (purge mocked).
- `gc.py`: `collect_garbage` reads `retention_days` from `ui_state`; purges deleted stubs'
  worktrees; drops their summary entries.
- `retention.disable` restores `cleanupPeriodDays` and removes the backup.
- `ui_state`: `retention_days` default + round-trip.

TUI tests (`test_tui*.py`, Textual `run_test`):

- Preview renders in the `treepane` `Vertical` (bottom), not the old `Horizontal`; Summary
  section text (present / absent / stale).
- `u` routing: session vs non-session (bell); live refusal toast; worker path with
  `summarize.run` patched.
- First-run summaries prompt: shown once (gated on ≥1 named session in the index), sets the
  auto flag on Yes, reveals the preview pane; not re-shown when the `summaries-prompted`
  marker is present; skipped for an empty index.
- `SettingsScreen`: opens on `,`; each row reflects and writes its backing store; retention
  row edits `N`; tmux row shows status/set-up per availability.
- `action_delete` confirm mentions the worktree; outcome reporting (purge patched).
- Filter finds a merged summary via `/`.

## Docs + release

- **`SPEC.md`**: add a "Session summaries" section and a "Settings screen" section; update
  the install-layout file list; amend the worktree invariant to note the permanent-delete
  branch exception.
- **`CLAUDE.md`**: add load-bearing notes — summaries sidecar + hook env guard; auto-summary
  is a toggle not a permanent decision; `worktree.purge` vs `worktree.remove` (branch kept
  on reclaim, safe-deleted on permanent delete); Settings screen houses persisted prefs.
- **`README.md`** + help screen: `u`, `,`, the bottom preview, the Settings screen.
- **`CHANGELOG.md`** + version bump to **1.18.0** (minor — feature), per the
  `cutting-a-release` skill. **One PR, one bump at the very end** (all phases first).

## Implementation phases (shipped together as one PR)

1. **Stores + primitives** (no UI): `summary.py`, `summarize.py`, `worktree.purge`,
   `retention.disable`, `ui_state.retention_days`, hook env guard. Full unit tests.
2. **Delete cascade**: `delete_session` + `collect_garbage` worktree purge + summary drop.
3. **Summaries wiring**: auto-on-exit worker, `u` action, digest→run→store.
4. **UI**: relocate preview to bottom, Summary section, filter merge, `SettingsScreen`.
5. **Docs + release**: SPEC/CLAUDE/README/help/CHANGELOG + 1.18.0.
