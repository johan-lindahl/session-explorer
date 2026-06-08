# Launch-failure recovery & persist-by-default — design

Date: 2026-06-08
Status: approved (brainstorming) → ready for implementation plan

## Problem

Two related gaps surfaced from a real incident: a named session
(`35cc68e3…`, "user-story/43821-Platform_PageCache …") that **refused to
open**. Investigation established:

1. It was a **worktree** session (`c` with the worktree box on — magento-os'
   root is a shared resource, so the box defaults on and the worktree name is
   the slug of the session name). The launch was `claude … -w <slug>`.
2. `claude -w <slug>` **failed at startup** because `git worktree add` hit a
   collision (a stray `.claude/worktrees/<slug>` directory, or a transient
   `.git` lock during heavy worktree churn). Reproduced directly:
   `Error creating worktree: … already exists … pass a different --worktree name`.
3. Because the window runs `exec claude …`, Claude exiting **closes the tmux
   window**. The dock (`join-pane -s <sid>`) then finds no window → **no pane
   appears** ("the right panel never opened"). Whether you get "no pane" or
   "pane then vanishes" depends on timing → **arbitrary**.
4. The only residue is the **seeded index stub** (`seed_new_session` wrote a
   row with `name_cached` + repo-root `project_path`, **no `transcript_path`**).
   Pressing Enter on it runs `claude --resume=<sid>`, which can't work (no
   conversation) → the row is **unopenable**.

Ruled out (with evidence): the `c` feature itself, bad cwd/PATH, the name's
special characters, the auto-generated worktree **slug** (clean, git-valid),
slow Claude start, SessionStart/queue hooks, and version skew. The failure is
specifically `claude -w` exiting on a `git worktree add` error, in a path that
**logs nothing** — a total observability blind spot.

Separately, the user wants tmux-hosted sessions to **survive explorer
termination** so the next `/open` resumes where they left off.

## Goals

- **Visibility:** a launch/dock failure is never silent — capture Claude's
  startup stderr, surface it, and log it.
- **Recovery:** an orphan transcript-less stub is openable again (Enter starts
  it fresh), and a failed launch leaves a self-explaining row, not a mystery.
- **Persistence:** sessions survive *all* exits except an explicit "shut down
  all"; the next `/open` reattaches.

Non-goals (deliberately deferred): pre-flight worktree-collision detection /
auto-clean (the "level 3" guard). If a retry hits the same stray-dir collision,
the surfaced error tells the user to clean it manually.

## Part 1 — Worktree-launch failure: visibility + recovery

### 1A. Capture the failure

- **Stderr capture.** `build_new_session_window` redirects the new session's
  stderr to a per-sid temp file: `exec claude … 2> <errfile>`. `exec` is kept
  (closing the window still kills Claude directly). The redirect captures the
  `git worktree add` error, which prints to stderr **before** Claude's TUI
  starts.
  - *Validation during implementation:* confirm the redirect does not disturb
    Claude's interactive UI (its TUI is on stdout/the tty). If it does, fall
    back to a non-`exec` wrapper that captures the exit code without redirecting
    stderr, accepting a generic message instead of Claude's exact text.
- **Liveness check.** After `_do_new_session` starts + docks, a single one-shot
  timer (~1.5 s) checks whether the session is actually alive: present in
  `session_windows()`, or the current docked pane, or the live registry. If
  **none**, the window died at startup.
- **Surface + log.** On death: read `<errfile>`, `notify(...)` the captured
  message (severity warning), and append a line to `session-explorer.log` with
  the `new-window`/`join-pane` return codes and the captured stderr. Clean up
  `<errfile>`.

### 1B. Recover the orphan stub

- **Enter on a stub starts it fresh.** `action_resume` treats an entry with no
  `transcript_path` as a stub: instead of the resume/dock path
  (`claude --resume`), it routes to the new-session **start** path
  (`_do_new_session`) reusing the stub's `sid` (via `--session-id`) and
  `name_cached`. The worktree is defaulted exactly as `c` does for the project
  (shared-resource root → worktree on, slug from the name; otherwise off). No
  dialog — one keypress.
- **Failed launch leaves a self-explaining row.** On a detected launch failure
  (1A), the stub is **kept** (it's a named session the user intended and it is
  now retryable) and **stamped** with the reason on the index row
  (`last_launch_error`). The preview pane renders `Last launch failed: <error>`
  so a non-opening row explains itself. (Chosen over auto-delete, which would
  discard the typed name.)

### Components touched

- `bin/_pkg/tmux.py`: `build_new_session_window` stderr redirect; helper for
  the errfile path.
- `bin/_pkg/tui.py`: `_do_new_session` (errfile wiring + liveness timer +
  surface/log), `action_resume` (stub → start-fresh), preview pane
  (`Last launch failed` line).
- `bin/_pkg/index.py`: `last_launch_error` set on launch failure, cleared once
  the session starts successfully (a transcript appears).
- A small logging helper to `session-explorer.log` for the launch/dock path.

## Part 2 — Persist by default (reverse "Option C")

- **Remove the kill hook.** Drop the `client-detached` hook from
  `tmux.build_config` (`tmux.py:179-182`). Detaching the client by any means —
  red-button/Cmd-W, crash, or `x → b` — leaves the `-L session-explorer` server
  (background sessions **and** the detached explorer TUI) running. The reattach
  path is unchanged (`new-session -A -s explorer` in `launcher.py`).
- **Remove the now-dead persist-flag machinery:** `set_persist_flag`,
  `clear_persist_flag`, `persist_flag_set` (`tmux.py`), the flag file and its
  generation in `cli.py:275-281`, and the `.session-explorer.tmux-persist`
  marker in `uninstall.py`. `action_quit`'s `background` branch becomes a plain
  `detach_client()`.
- **Only explicit shutdown kills.** `x → s` ("shut down all") still calls
  `kill_server()` — unchanged. `x → b` and abrupt close are now equivalent
  (both persist).
- **One explorer TUI only.** The `-A` attach guarantees a single `explorer`
  session, so we never spawn a duplicate TUI (avoids the known
  orphan-TUI-clobbers-index issue). Verified by test.
- **Spec.** Rewrite the "Option C" load-bearing decision in `SPEC.md` and
  `CLAUDE.md` to "persist by default; only an explicit shut-down kills," in the
  same change.

### Accepted trade-off

Background sessions can accumulate. Accepted by the user over the safety-net
alternative; discoverability already exists (live indicators in the tree; `x`
lists what's running).

## Testing

- **1A:** unit-test the liveness check + surface/log given a dead vs. alive
  window (tmux interactions stubbed); a focused test that a window running a
  command which exits is absent from `session_windows()` shortly after.
- **1B:** `action_resume` on a transcript-less entry invokes the start path
  with the stub's sid + name (not `--resume`); `last_launch_error` round-trips
  through the index and renders in the preview.
- **Part 2:** `build_config` no longer emits a `client-detached` kill hook;
  `action_quit` `background` path no longer touches a persist flag; `x → s`
  still kills; uninstall no longer references the persist marker.
- Full suite green: `python3 -m pytest test/ -q` and the bats shell tests.

## Delivery

Single PR, TDD throughout. Version bump to **1.15.0** (minor — user-facing
behavior change) at the end, via the `cutting-a-release` skill: bump
`__init__.py` + `plugin.json`, update README/SPEC status lines and the
help-screen keybindings if they change, add a `CHANGELOG.md` section, then
`gh release create v1.15.0`. Per the user's phased-delivery preference: build
both parts, then one PR + one bump.
