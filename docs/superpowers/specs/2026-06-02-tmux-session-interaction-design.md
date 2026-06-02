# tmux-backed session interaction & live monitoring — design

**Date:** 2026-06-02
**Status:** Approved, not yet implemented. Two spikes required before build (§9): tmux-mouse-vs-Textual-mouse, and repaint-after-flip.
**Affects:** `SPEC.md` (new feature — must be reflected there during implementation), `CLAUDE.md` (load-bearing decisions), `plugin.json`, `bin/_pkg/` (new `tmux.py`, `snapshot.py`; changes to `tui.py`, `cli.py`, `launcher.py`), generated `session-explorer.tmux.conf`, `install.sh`/`uninstall.sh`, `test/`. **Ships as one deliverable** (single branch / PR / version bump) — the build order in §10 is internal sequencing only, not a phased release.

## Goal

Let a user resume and run **multiple Claude sessions concurrently from inside the explorer**, keep them alive in the background, glance at each one's progress from the tree, and dive into any of them to interact — without the explorer ever exiting. Today, resuming *replaces* the explorer process (`tui.py:run` → `os.execvp("claude", …)`), so you can only ever be in one session and the explorer is gone while you work. This feature makes resume **non-destructive**: the explorer stays running as a tmux window and each session is a sibling window it can flip to.

This is **Model A** from the brainstorming dialogue: the explorer remains a full-screen Textual app; interaction with a session is a same-window tmux flip; monitoring is a read-only snapshot in the preview pane. (Model B — tmux split-panes with the explorer demoted to a column — was rejected: it only shows one live session at a time anyway, gives a cramped interaction surface, and discards the mature full-screen TUI.)

## Why tmux (and not an embedded terminal widget)

A live, interactive terminal rendered *inside* a Textual widget would require embedding a terminal emulator (a PTY + `pyte` + ANSI rendering + resize/focus handling) — effectively hosting a full-screen TUI (claude) inside another. That is high-effort, fragile, and a permanent maintenance burden tracking claude's UI. tmux already solves PTY hosting, background persistence, resize, and detach/reattach as a battle-tested layer. So:

- **Interaction & persistence** come from tmux (sessions are background tmux windows; flipping is native, full-fidelity rendering).
- **Live status** reuses the existing `live.py` registry — no new mechanism.
- **Progress snapshots** are read-only (`capture-pane` or transcript tail), which sidesteps the emulator problem entirely.

tmux is an **optional enhancement**, never a hard requirement (§7).

## Architecture overview

```
/open ──► launcher spawns terminal ──► tmux -L session-explorer new-session -A -s explorer
                                          │   (dedicated server, generated config)
                                          ▼
                              window 0: session-explorer tui  (Textual, stays alive)
                                          │  shells out to tmux CLI
                          ┌───────────────┼────────────────────────────┐
                          ▼               ▼                            ▼
              tmux new-window -d   tmux select-window -t <sid>   tmux capture-pane -ep -t <sid>
              (start session)      (flip in to interact)         (snapshot for preview)
                          │
                          ▼
        window <sid>: claude --resume <sid>  (background, keeps running)

  live tree dots ◄── live.py registry (existing, unchanged)
  clickable tabs / F12 ◄── generated session-explorer.tmux.conf (status bar, mouse, key)
```

Four moving parts: (1) the **tmux server** hosting the explorer + session windows, (2) the **generated tmux config** (tabs, mouse, F12, remain-on-exit), (3) **`tui.py`** issuing tmux commands and rendering snapshots, (4) the existing **`live.py`** registry driving tree dots.

## 1. Launch & process model

When tmux is available and not declined, `/open` launches the explorer inside a **dedicated tmux server**:

```
tmux -L session-explorer -f <generated.conf> new-session -A -s explorer 'exec session-explorer tui'
```

- **Dedicated server (`-L session-explorer`)** — fully isolated from the user's personal tmux server, config, and keybindings. We never read or write their `~/.tmux.conf`.
- **`-A` (attach-or-create)** — relaunching `/open` reattaches to an existing `explorer` session. Sessions left running **survive closing and reopening** the explorer (persistence across explorer restarts). This is also the reconciliation mechanism: on launch the explorer calls `tmux list-windows` to rediscover still-running session windows and reconcile them against the tree.
- The explorer is window `0`; each resumed session is a sibling window **named by its session id** (`tmux new-window -n <sid>`). The id is always known up front (we only ever *resume*), so the `session_id → window` mapping is the window name — no separate registry file needed.
- The `launcher.py` change wraps the existing `target_command` (`exec session-explorer tui`) in the tmux invocation above when `tmux.available()` is true; otherwise the command is passed through unchanged.

**No-tmux fallback:** when tmux is absent or the user declined install, behaviour is exactly today's — `tui.py:run` does `os.execvp("claude", …)` and the explorer exits. The feature is purely additive; nothing regresses without tmux.

## 2. Interaction model — Enter / space / switch

The existing bindings keep their letters; their *semantics* become non-destructive.

| Key | Cursor on stopped session | Cursor on running session |
|---|---|---|
| **Enter** | `tmux new-window -d -n <sid> -c <cwd> 'claude --resume <sid>'`; **stay in explorer** (watch it spin up in the preview) | `tmux select-window -t <sid>` — **flip in to interact** |
| **space** | static metadata preview (today) | live snapshot preview, stay in tree (§3) |

- **Context-aware Enter** — "go as far as sensible": start a stopped session in the background, enter a running one. No second key to learn for the common flow (start several, dive into one).
- **Switching back / between sessions:**
  - **Clickable status-bar tabs (primary):** the generated config enables a tmux status bar showing window tabs (`[0 explorer] [1 feat/auth ●] …`) with mouse on. Click `explorer` to return; click any session to flip in. Doubles as the at-a-glance "what's running" list.
  - **F12 (keyboard fallback, configurable):** a no-prefix root binding `bind -n F12 select-window -t explorer`. F12 is intercepted by tmux globally, so it must be a key claude never needs — Tab/Shift-Tab (autocomplete / mode cycle), Esc, arrows, and the readline Ctrl combos are all excluded for that reason. F12 is safe but on macOS needs `fn+F12`; that is acceptable because the tab bar is the primary path. The binding is user-overridable (e.g. to `Ctrl+G`).
- **cwd & worktree handling** carry over from the current `action_resume`: `tmux new-window -c <resolved cwd>` using `_resolve_resume_cwd`, and the dead-worktree `ConfirmScreen` warning fires before spawning.

## 3. Snapshot rendering (the new preview content)

When the cursor is on a **live** session, the preview pane shows a snapshot polled by a `set_interval` timer (~1 s, tunable), via a new pure helper `snapshot.py`:

- **Explorer-launched (tmux) session** → `tmux capture-pane -ep -t <sid>`. tmux maintains every background pane's screen buffer, so the current claude frame (alternate-screen content included) is captured **without flipping to it**. `-e` preserves colour as escape sequences; rendered in the preview via `rich.text.Text.from_ansi`.
- **Any other live session** (live in `live.py` but not one of our tmux windows — e.g. claude open in a separate terminal) → **transcript tail**: parse the last few JSONL events with existing `jsonl.py` into a clean activity view (latest prompt, latest assistant text, last tool call, working/idle).
- **Stopped session** → today's static metadata preview, unchanged.

Only the **selected** session is polled for a full snapshot. Tree-wide liveness uses the existing `live.py` poll — no extra per-session snapshot polling.

## 4. Live tree dots — reuse existing infrastructure

No new mechanism. A session started via `tmux new-window 'claude --resume …'` is an ordinary Claude session, so the existing `session-live.sh` hook registers it in `session-explorer-live.json` and the TUI's existing poll renders the working/idle glyph. The dots distinguish **working** (spinner) vs **idle** (`○`), which is richer than running/done and comes for free.

## 5. Lifecycle & edge cases (approved defaults)

- **Finished session** — the generated config sets `remain-on-exit on`. A session whose `claude` exits keeps its final frame; its tab is marked `exited`; dismissing the tab (or an explorer action) closes the window with `tmux kill-window`. Lets the user read the final state and `capture-pane` still works on it.
- **Already live *elsewhere*** — if the selected session is live in `live.py` but is **not** one of our tmux windows (running in another terminal), Enter must **not** spawn a duplicate `claude --resume` (two claude processes on one transcript corrupts it). It shows a warning and offers **peek-only** via transcript tail. Detection: session id present in `live.py` poll but absent from `tmux list-windows`.
- **Status bar visibility** — hidden while only window `0` (explorer) exists; shown once ≥1 session window is running, so non-resuming users see an unchanged full-screen explorer.
- **Stale tmux server** — if the explorer process died but the server persists with session windows, `/open`'s `new-session -A` reattaches and reconciliation (§1) rediscovers the windows.
- **The session that launched the explorer** already shows as live today (it ran the slash command); unchanged and harmless.

## 6. Generated tmux config

A config file generated at launch (e.g. under the plugin's runtime dir), passed via `-f`, so the dedicated server is self-contained and the user's tmux is untouched. Contents:

- Status bar on, window-tab list, mouse on (clickable tabs + flip).
- `bind -n F12 select-window -t explorer` (configurable key).
- `set remain-on-exit on`.
- Status-bar auto-hide while only the explorer window exists (§5).
- No rebinding of the user's prefix or anything outside this server.

## 7. tmux dependency — detect, optional, consented install

- **Detect** at launch: `tmux -V`, require a floor (~3.0, for `capture-pane -e`, root bindings, `remain-on-exit`, status styling). `tmux.py` owns detection, version parse, and an injected runner for tests (mirrors `launcher.py`'s `which` injection).
- **Missing/too old** → one-time consent prompt mirroring the retention pattern: `[i] install now` (via detected package manager — `brew` on macOS without sudo; `apt`/`dnf`/`pacman`/… printed for the user to run, since they need sudo), `[s] show instructions`, `[n] not now`. Record the choice with a declined-marker (cf. `.session-explorer.retention-declined`) so it is not re-nagged. Re-verify with `tmux -V` after an install attempt before enabling the feature.
- **No bundled/downloaded binary.** Vendoring a static tmux (C + libevent + ncurses, per-OS/arch, macOS quarantine/signing) is rejected — over-engineering and against the "one vendored dep" ethos. Auto-install is consented and package-manager-based only, never silent, never sudo-without-asking.
- **Declined or unavailable** → §1 fallback (plain `execvp` resume). Explorer remains fully functional; only background monitoring/interaction is disabled.

## 8. Code shape

- **`bin/_pkg/tmux.py`** (new) — thin CLI wrapper: `available()`/version, `ensure_server`, `start_session_window(sid, cwd)`, `select_window(target)`, `capture_pane(sid)`, `list_windows()`, `kill_window(sid)`, `generate_config()`. Pure logic + an injected command runner for unit tests.
- **`bin/_pkg/snapshot.py`** (new) — `snapshot(sid) -> renderable`: capture-pane path for tmux windows, transcript-tail path (reusing `jsonl.py`) otherwise. Pure; testable with fixtures.
- **`bin/_pkg/tui.py`** — context-aware `action_resume` (start-bg vs flip) that no longer exits; snapshot rendering in the preview; a `set_interval` snapshot poll for the selected live session; reconciliation on mount. The current exit-and-execvp path is retained only for the no-tmux fallback.
- **`bin/_pkg/cli.py` / `launcher.py`** — wrap the TUI launch in the tmux invocation when `tmux.available()`.
- **Generated `session-explorer.tmux.conf`** — §6.
- **`install.sh` / `uninstall.sh`** — no new hooks needed (reuses `live.py`); any teardown of generated config/markers added to uninstall.
- **`SPEC.md` + `CLAUDE.md`** — updated in the same change (new load-bearing decisions: explorer-runs-in-tmux, non-destructive resume, tmux optional + consented, snapshot is read-only).

## 9. Spikes required before build

- **tmux mouse vs Textual mouse** — both consume mouse events. tmux forwards mouse to apps requesting tracking (so clicking tree rows should still reach Textual; status-bar/tab clicks go to tmux), but drag-to-select and edge cases need validation on macOS + Linux before committing to mouse-on.
- **Repaint after flip-back** — expected low risk (tmux preserves and restores each pane's buffer, so returning shows the last frame immediately and the app re-renders on its next tick). Confirm the explorer redraws cleanly after `select-window` round-trips, including after a terminal resize while a session was focused.

## 10. Build order (internal sequencing — all ships together)

1. `tmux.py` + spikes (§9) + launch-in-tmux + reconciliation + no-tmux fallback.
2. Context-aware Enter + generated config (tabs/F12/remain-on-exit) + switching.
3. `snapshot.py` + preview snapshot + selected-session poll + already-live-elsewhere guard.
4. tmux detection + consent-install + declined-marker + status-bar auto-hide.
5. `SPEC.md`/`CLAUDE.md` updates + tests.

## 11. Testing

- `test/test_tmux.py` — `tmux.py` command construction and version-gate via injected runner (no real tmux): start-window/select/capture/list/kill argv; config generation; availability parsing.
- `test/test_snapshot.py` — capture-pane vs transcript-tail selection; transcript-tail rendering from JSONL fixtures; ANSI→`Text` for capture output.
- `test/test_tui.py` (extend) — context-aware `action_resume` dispatch (stopped→start-window, running→select-window, live-elsewhere→warn) with tmux mocked; explorer no longer exits on resume when tmux present; fallback execvp path when absent.
- `test/test_launcher.py` (extend) — tmux-wrapped vs pass-through launch command.
- bats — install/uninstall teardown of generated config + markers.
- CI already runs pytest + bats on ubuntu + macOS. Real-tmux integration is covered by the §9 spikes, not CI (CI runners lack an interactive tmux client).

## Tunables (defaults)

| Knob | Default | Notes |
|---|---|---|
| Snapshot poll | 1 s | freshness vs. capture-pane churn |
| tmux server socket | `session-explorer` | dedicated, isolated from user tmux |
| Back-to-explorer key | F12 | configurable; tab bar is primary |
| tmux version floor | ~3.0 | `capture-pane -e`, root bindings, remain-on-exit |

## Out of scope (this feature)

- Embedded interactive terminal widget inside a Textual pane (the rejected emulator approach).
- Model B tmux split-pane layout.
- Live snapshots for *all* sessions simultaneously (only the selected one is polled).
- Windows support for the tmux path (WSL inherits the Linux path; native Windows tmux is out).
- Any change to retention, `--gc`, or `cleanupPeriodDays`.
