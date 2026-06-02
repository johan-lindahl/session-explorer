# Split-pane explorer + claude — design

**Status:** Proposed (experimental, to be evaluated on a dev branch)
**Date:** 2026-06-02
**Supersedes (on this branch):** the windows-flipping interaction model in
`2026-06-02-tmux-session-interaction-design.md` and SPEC.md §"tmux interaction layer".

## Motivation

Today the explorer is tmux **window 0** and each resumed session is its own
**sibling window**; you flip between them with F12 / clickable status-bar tabs.
That model is built for *breadth* — monitoring many background sessions. This
design trades breadth for *focus*: explorer and one live claude session visible
**side by side** in a single window, no flipping. You see the tree and the
running session at once, and can zoom claude fullscreen when you want the room.

This is an **experiment**. The goal of the dev branch is to live in the new
model and judge whether it's better than windows-flipping. On this branch the
split view **fully replaces** the windows-flipping UX (it is not an opt-in
toggle).

## Core model: a docking layer over the windows substrate

The windows engine is **kept as the substrate** — only the *view* changes. This
is the load-bearing decision that makes the feature low-risk: liveness glyphs,
`capture-pane` snapshots, and `list-windows` reconciliation keep working
unchanged.

- **Window 0 (`__EXPLORER__`) becomes a two-pane horizontal split:** explorer in
  the **left** pane, the active claude session in the **right** pane.
- **Inactive sessions keep running as background windows** (today's
  `new-window -d`). They are never shown to the user, but they are alive — which
  is exactly what "the others keep running, just not displayed" requires. All
  existing window-keyed machinery (snapshots, liveness, reconciliation) operates
  on them as before.
- **Dock** a session → `join-pane -h` its window into window 0 on the right. The
  source window (a single pane) is consumed; the session is now the right pane.
- **Swap / undock** → `break-pane -d` the current right (claude) pane back into
  its own background window (named by its sid, so reconciliation still finds it),
  then `join-pane` the newly selected session in.
- **No session docked → the explorer pane fills the full width** automatically
  (a one-pane window). When a docked claude **exits**, its pane closes and the
  explorer reclaims the width with no extra logic — this is the "go back to one
  pane for the explorer" behavior, for free (`remain-on-exit` stays off).

### Pane targeting

After a dock, pane 0 is the explorer (our Textual TUI) and pane 1 is claude.
Undock must target the **claude** pane specifically, never the explorer pane.
Track the docked pane by its **pane id** (captured from `join-pane`'s output, or
selected via `{right-of}` / "the pane not running the explorer") rather than a
bare index, so the explorer is never accidentally broken out.

### Focus on dock

Docking a session **focuses the claude pane** (`select-pane` right), so Enter
lands you ready to type — preserving today's "Enter always lands you *in* the
session" contract.

## Interaction & keys

| Key / action | Behavior |
|---|---|
| **Enter** on a session row | Dock it into the right pane **and focus it**. Entering a *different* session breaks the current one out to a background window and docks the new one. |
| **F9** | Toggle focus between explorer and claude (`select-pane`). Because window 0 only ever has two panes, this is an unambiguous flip. |
| **Mouse click** on a pane | Focuses that pane (mouse is already on). |
| **F12** | Zoom the focused pane fullscreen and back (`resize-pane -Z`) — the "tree on/off, claude fullscreen" toggle. |
| **Space** | Unchanged: live snapshot / metadata preview. Still useful for peeking *background* (undocked) sessions without docking them. |

- **F9 is a configurable root binding** (`bind -n F9 select-pane -t :.+`), in the
  same family as the existing F12 root binding — proven reliable, and chosen
  because function keys collide with neither the explorer (a tree, not a text
  editor) nor claude (which needs Ctrl/Alt+arrows for word motion, ruling those
  out as global root bindings). Cmd/Super is unusable: terminals never pass it to
  tmux, and Linux has no Cmd at all.
- **F12 is reassigned** from today's "back to explorer" to "zoom focused pane."

## Status bar & footer hints

- **Drop the window-tab list** from the tmux status bar. Sessions are no longer
  user-facing windows, so tabs are noise; the **explorer tree is the only
  session switcher**.
- **tmux status line** (kept, one line) shows `F9 ⇄ switch · F12 ⤢ fullscreen`
  hints, optionally with the docked session's label. Keeping this line matters
  for the **zoomed** case: when claude is fullscreen the Textual footer is
  hidden, so the tmux line is the only surviving place for the "F12 to exit
  fullscreen" hint.
- **Textual footer** advertises **F9** and **F12** the same way the help text
  advertises keys today (per user request). Visible on the left whenever the
  split is shown, regardless of which pane is focused.
- The old `F12 → explorer` status-right hint and `bind -n F12 select-window`
  are removed.

## What is explicitly untouched

- **No-tmux fallback** — `os.execvp("claude", …)`; the split feature requires
  tmux and is purely additive on top of the no-tmux path.
- **`c` new-session flow** — still spawns the session, then **docks** it instead
  of `select-window`-ing to its window.
- **Liveness polling, snapshots, `Space` preview** — unchanged; they operate on
  background windows exactly as before.
- **Quit / persist Option-C logic** — unchanged. `kill-server` (or persist via
  the flag + detach) still tears down both the docked pane and all background
  windows.
- **"Already live in another terminal" guard** — unchanged: refuse a second
  `claude --resume` on the same JSONL, offer peek-only.

## Open tuning knob (non-blocking)

**Default split ratio:** start claude at ~60–65% width, explorer ~35–40%
(`join-pane -h -p <pct>`), drag-resizable via mouse. Easy to retune while living
with it on the branch.

## Risk to exercise on the branch

`join-pane`/`break-pane` **re-parent a live claude process** on every dock/swap.
claude and Textual both handle the resulting SIGWINCH resize cleanly, so this is
expected to be fine — but **rapid dock/undock churn is the novel path** and is
the specific thing the branch needs to stress-test (re-parent under load,
mid-render, while claude is mid-tool-call). If churn proves flaky, fallbacks to
consider: debounce swaps, or keep the previous claude as a hidden pane in a
spare window rather than breaking it out each time.

## Code touch-points

- **`bin/_pkg/tmux.py`** — new `build_*` argv builders + thin wrappers for
  `join-pane`, `break-pane`, `select-pane`, `resize-pane -Z`; rework
  `build_config()` (F9 binding, F12 reassigned to zoom, status-bar redesign,
  drop window-tab list + old back hint).
- **`bin/_pkg/tui.py`** — `action_resume` (and the `c` new-session path) dock via
  join/break instead of `select_window`; track the docked pane id; update footer
  hints and help overlay (F9/F12). The "select existing window" branch becomes a
  "dock existing background window" branch.
- **`SPEC.md`** — update the "tmux interaction layer" section to describe the
  split-pane model (per the CLAUDE.md rule: spec and code change together).
- **Tests** — pure `build_*` builders unit-tested; dock/undock sequencing covered
  by mocked-tmux TUI tests; the re-parent churn exercised manually on the branch.
