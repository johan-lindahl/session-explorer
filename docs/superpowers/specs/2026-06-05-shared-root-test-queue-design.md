# Shared-root test queue — design

**Date:** 2026-06-05
**Status:** Design approved; pending spec review → implementation plan.

## Problem

When working with multiple git worktrees, tests often require a resource that
only exists at the **repository root**: a heavy Docker stack (db + web + redis +
…) that bind-mounts the root directory and serves those files, with browser
tests (Puppeteer) hitting the served web view on **well-known ports** (8080,
etc.) baked into the harness and dependencies.

Because the stack serves files off the bind-mounted root, agents working in
worktrees "hijack" root to test: they copy their work-in-progress source into
root, run the stack-dependent tests, then clean up. When two agents do this
concurrently the result is a mess:

- They copy WIP into the same root paths and **overwrite each other's files**.
- One agent finishes, runs cleanup, and **wipes files the other agent's tests
  are still using**.
- Config files used by tests are modified by agent A and then **overwritten by
  agent B**.

This is a **shared-mutable-state race on the root directory**.

### Why isolation doesn't work here

The mainstream "give every agent its own everything" answer (per-worktree
Docker stacks, dynamic ports, ephemeral DBs) is ruled out by the constraints:

- The stack is **heavy and slow to boot**, so cloning it per worktree is too
  expensive.
- The web server **serves files off the bind-mounted root path**, so files
  genuinely must land at that path.
- Tests use **well-known ports**, so port randomization breaks them.

This is definitively a **serialize-the-singleton** problem, not an isolate
problem.

## Solution overview

Treat the root not as a place anyone "owns" or cleans up, but as a **shared
test sandbox** that belongs to *whoever holds the lock, only while they hold
it*. A **daemon-less FIFO queue** serializes access; the shared Docker stack
stays **warm** the whole time.

Keystone insight:

> Replace "copy in → run → delete my files" with **"sync-on-acquire → run → (no
> cleanup)."** On acquire, the holder `rsync`s its worktree *over* root, blowing
> away whatever the previous holder left. There is nothing to clean up, because
> the *next* holder's sync is the reset. The "cleanup wipes my competitor's
> files" bug disappears by construction.

The stack never reboots: each holder just rsyncs files into the bind-mounted
directory and the web server picks them up off disk. Boot cost is paid once.

## Components

### 1. Queue core — daemon-less FIFO, keyed by named resource

- Store: `~/.claude/session-explorer-queues/<resource>/`, one **ticket file**
  per participant containing: monotonic ticket number, session id, cwd,
  command, timestamp.
- **Holder** = the lowest outstanding ticket number. The queue *is* the set of
  ticket files — no daemon is needed to remember order.
- Concurrency: `flock` + temp-file-rename guard the ticket counter and the held
  slot — the same primitive the index and folder stores already use.
- **Crash-safe:** the ticket file is removed and the lock auto-released when the
  holder process exits (including crash), so a dead holder never deadlocks the
  queue.
- **Generalized to named resources** (`shared-docker-stack` is just one). The
  same machinery can guard any singleton (a flaky integration DB, a device, a
  license seat). The pane shows all active queues.

### 2. Per-project config — `~/.claude/`, keyed by project path

Lives per-user in `~/.claude/` (consistent with the index/folder stores),
**not** committed to the repo. This keeps the feature invisible to teammates
who don't use the explorer or don't work in parallel worktrees.

Records:

- **opted-in** flag for the project.
- **shared-root path** — auto-derived from the worktree ↔ parent-repo mapping
  the index already computes (`index._project_label` / `project_path`).
- **guarded-command patterns** — strong defaults (commands matching
  `docker` / `compose` / `e2e` / `playwright` / `puppeteer` / the well-known
  ports). Hand-tunable; the cooperative layer covers anything the regex misses.
- **sync settings** — default `rsync` the worktree over root, excluding `.git`.

### 3. CLI spine

One command runs the whole lease lifecycle in a **single process** so release
is guaranteed on exit/crash:

```
session-explorer queue-run --resource <r> -- <test command>
```

→ take ticket → wait turn → (exclusive-or checks, see policy) → rsync
worktree→root → run command → release.

Plus:

- `session-explorer queue-status` — JSON (for the pane) + human-readable.
- `session-explorer queue-cancel` — drop a waiting ticket.

### 4. Sync semantics

On acquire: `rsync` the holder's worktree *over* root (excluding `.git`),
overwriting whatever the last holder left. **No cleanup step** — the next
holder's sync is the reset. This eliminates both the overwrite race and the
cleanup-wipes-others bug.

A worktree session always re-syncs its own files at acquire, so it never
depends on root's prior contents.

### 5. The "root is exclusive-or" policy

Root is **either** a live working session **or** the test sandbox — never both
at the same time. This is what makes the destructive rsync safe.

1. **Live root session present → sandbox is locked out.** If any live session
   has `cwd == shared-root`, `queue-run` from a worktree **blocks** (it does not
   rsync); the pane shows *"root held by live session ‹name›."* Root's files
   stay untouched. (A live root session is treated as an implicit exclusive
   holder of the resource.) Liveness uses the same detection `--gc` relies on:
   flock on the JSONL / mtime within 60s.
2. **No live root session → sandbox is free.** The first worktree holder takes
   it; rsync-over-root proceeds. From here root belongs to lease holders.
3. **Transition guard.** When flipping from "a root session existed" to sandbox
   mode, if root has **uncommitted git changes**, `queue-run` refuses with a
   clear message ("root has uncommitted changes the sandbox would overwrite —
   stash/commit first") rather than silently clobbering. A *clean* root proceeds
   without nagging.
4. **Prevention layer.** In a queue-enabled project the new-session dialog
   **defaults the worktree checkbox ON** and warns if the user tries to create a
   plain root session ("this project uses the root queue — root is a shared
   sandbox; create a worktree instead").

**Why both prevention and enforcement:** we cannot hard-forbid root sessions —
a user can run `claude` in root entirely outside the explorer. So the dialog
(prevention) makes root-working rare, and the exclusive-or rule (enforcement)
keeps it *safe* when it happens anyway. Worst case is a worktree agent waiting
(visible and actionable in the pane), never data loss.

**Accepted tradeoff — starvation:** an idle-but-live root session pauses all
parallel testing. This is correct: it is exactly the situation where clobbering
would lose work. The pane makes the reason obvious so the user can close the
root session to unblock.

### 6. TUI

- **New-session dialog:** a **"use queue to access root"** checkbox beside the
  worktree checkbox. Checking it writes the per-project config; it reflects
  existing state (pre-checked once opted in) so it doubles as the off switch.
  In a queue-enabled project the worktree checkbox defaults ON (policy #4).
- **Queue pane:** a toggleable region (bottom ~20% of the tree), live on the
  existing ~2s refresh loop. Per resource it shows the current holder (+
  elapsed) and the waiting line in order. Waiting surfaces **position
  ("2 of 3")**, not an opaque spinner. The toggle key is chosen to avoid
  case-variant collisions with existing actions.

### 7. Agent awareness — cooperative + enforced

- **Global `PreToolUse` Bash hook**, registered once at install, that
  **no-ops unless the current repo is opted in** (zero overhead / zero
  interference elsewhere). For opted-in repos it matches guarded commands and
  **denies + redirects**: "re-run as `queue-run -- …`." Denying rather than
  silently wrapping keeps the lease lifecycle inside the one `queue-run`
  process. Like all hooks here, it fails open and never blocks startup.
- **Skill + `CLAUDE.md` snippet** explaining the *why/how* so agents cooperate
  gracefully — don't busy-spin, report queue position, understand the
  sync-overwrite semantics.

## Build order (independently shippable)

1. **Queue core + CLI** — usable immediately with only `CLAUDE.md` guidance.
2. **TUI dialog checkbox + queue pane.**
3. **Awareness / hook layer.**

Each is its own spec → plan → implementation cycle.

## Constraints honored

- Minimal deps; no new runtime dependency (`rsync`/`flock` are system tools;
  Textual stays vendored).
- `flock` + temp-file-rename for every queue write (matches existing stores).
- Hooks fail open and never block startup.
- Opt-in, like retention — nothing activates until the user checks the box.
- Per-user config in `~/.claude/`; the repo is never touched.
- Don't move or rewrite native JSONLs; the queue is orthogonal to them.

## Open items deferred

- Exact toggle keybinding for the pane (pick during implementation, avoiding
  case-variant collisions).
- Whether `queue-status` should expose per-resource history/metrics beyond the
  current snapshot (not needed for v1).
- `SPEC.md` is the authoritative source for this project; update it in the same
  change set when implementing, rather than letting code and spec diverge.
