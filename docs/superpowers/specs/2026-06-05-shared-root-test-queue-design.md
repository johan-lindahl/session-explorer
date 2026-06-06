# Shared-root lease engine — design

**Date:** 2026-06-05 (revised 2026-06-06)
**Status:** Design approved (revised); pending spec review → implementation plan.

## Problem

When working with multiple git worktrees, some commands can only run from the
**repository root**, against a singleton resource that lives there. The
motivating case is a heavy Docker stack (db + web + redis + …) that bind-mounts
the root directory and serves those files, with browser tests (Puppeteer)
hitting the served web view on **well-known ports** (8080, etc.) baked into the
harness.

But it is **not only that case**. The same shape recurs whenever an action must
execute *in* root:

- An iOS integration suite that can only run via `xcodebuild test` from root.
- A Windows/C# build that depends on large binary files that exist only at root.
- Any build/test whose credentials live solely in a root `.env`.

Because the resource serves or reads files off root, agents working in worktrees
"hijack" root to run: they copy work-in-progress into root, run the
root-dependent command, then clean up. When two agents do this concurrently the
result is a mess:

- They copy WIP into the same root paths and **overwrite each other's files**.
- One agent finishes, runs cleanup, and **wipes files the other agent is still
  using**.
- Config/credential files modified by agent A are **overwritten by agent B**.

This is a **shared-mutable-state race on the root directory**.

### Why isolation doesn't work here

The mainstream "give every agent its own everything" answer (per-worktree
stacks, dynamic ports, ephemeral DBs) is ruled out by the constraints:

- The resource is **heavy/slow to boot** or **tied to root** (bind-mounted path,
  well-known ports, root-only binaries/credentials), so cloning it per worktree
  is too expensive or simply impossible.
- Files genuinely must land at the root path.

This is definitively a **serialize-the-singleton** problem, not an isolate
problem.

## Core reframing — a generic root-lease engine

The thing being built is **not** a docker-test queue. It is:

> A class of commands must run *in* a shared singleton directory; serialize
> access and optionally move files in/out around each lease.

Docker/e2e is one instance; iOS, C#, and `.env` builds are others. Nothing in
the core knows about Docker or testing. `queue-run` is a generic
**lease-around-a-command** runner. "Sync" is **not** a built-in behavior — it is
a configurable **acquire/release hook**, one strategy among others (including
"none").

Keystone insight (still the heart of it):

> Replace "copy in → run → delete my files" with **"acquire-hook → run → (no
> cleanup)."** For the `sync` strategy, on acquire the holder `rsync`s its
> worktree *over* root, blowing away whatever the previous holder left. There is
> nothing to clean up, because the *next* holder's acquire is the reset. The
> "cleanup wipes my competitor's files" bug disappears by construction.

A warm singleton (e.g. the Docker stack) never reboots: each holder just syncs
files into the bind-mounted directory and the server picks them up off disk.
Boot cost is paid once.

## Components

### 1. Queue core — daemon-less FIFO + flock crash-reaping

- Store: `~/.claude/session-explorer-queues/<resource>/`, one **ticket file**
  per participant containing: monotonic ticket number, session id, cwd, command,
  PID, heartbeat mtime. Mirrors the existing `flock` + sidecar-`.lock` +
  temp-file-rename pattern used by `index.py` / `folder_store.py` / `live.py`.
- **Holder = the lowest ticket number whose process is alive.** Ordering comes
  from the number; the queue *is* the set of ticket files — no daemon remembers
  order.
- **Crash-reaping via flock-on-own-ticket.** Each `queue-run` process holds
  `LOCK_EX` on *its own* ticket file for its whole life. A waiter tests the
  current holder's liveness by attempting `LOCK_EX | LOCK_NB` on the holder's
  ticket: if grabbable, the holder is dead (the kernel released the lock on exit,
  including `SIGKILL`) → the waiter reaps the stale file and advances. This
  survives `SIGKILL` and is immune to the PID-reuse race. PID + heartbeat mtime
  are recorded for **display only** (pane status), never for the reap decision.
- No central "holder lock" is needed: ticket numbers are unique, so exactly one
  ticket is ever the lowest-live one, so exactly one process concludes it is the
  holder. No tie, no TOCTOU.
- Concurrency: `flock` + temp-file-rename guard the ticket counter and store
  writes — the same primitive the index and folder stores already use.
- **Generalized to named resources.** `root` (the shared directory) is one
  resource; the same machinery guards any singleton (a flaky integration DB, a
  device, a license seat). The pane shows all active queues.

### 2. Per-project config — declarative, per-user, never committed

Lives per-user in `~/.claude/` keyed by project path (consistent with the
index/folder stores), **not** committed to the repo. This keeps the feature
invisible to teammates who don't use the explorer or don't work in parallel
worktrees.

Per resource, records:

- **`resource`** — e.g. `root`.
- **opted-in** flag for the project.
- **shared-root path** — auto-derived from the worktree ↔ parent-repo mapping
  the index already computes (`index._project_label` / `project_path`).
- **`guard`** — user-declared command patterns that must hold the lease (e.g.
  `xcodebuild test`, `dotnet build`, `docker compose`, `make integration`).
  Irreducibly project-specific: only the human knows which commands need root.
- **`run_in`** — working directory for the command (default `root`).
- **`acquire` / `release`** — each is a **named strategy** (`sync` | `none`)
  **or** an arbitrary shell command (advanced escape hatch).

The **`sync` strategy** knobs:

- `delete: true` — required for the "next acquire is the reset" property.
- `exclude: [".git"]` — paths not copied *in* from the worktree.
- **`protect: []`** — root-only paths that must never be overwritten or deleted
  (e.g. `.env`, local secrets, large root-only binaries). This is the `--delete`
  safety valve: without it, `--delete` would destroy root-only files that don't
  exist in the worktree.

Templates (see §6) *are* this strategy library with sensible per-domain
defaults.

### 3. CLI spine

One command runs the whole lease lifecycle in a **single process** so release is
guaranteed on exit/crash:

```
session-explorer queue-run --resource <r> -- <command>
```

→ take ticket → wait turn → exclusive-or check (§5) → run `acquire` hook → run
`<command>` in `run_in` → run `release` hook → **release ticket (strictly
last)**.

Plus:

- `session-explorer queue-status` — JSON (for the pane) + human-readable.
- `session-explorer queue-cancel` — drop a waiting ticket.

### 4. Lease lifecycle & sync semantics

The lifecycle order is load-bearing:

> take ticket → wait turn → exclusive-or check → **acquire hook** → run command
> → **release hook** → release ticket (strictly last)

- **Acquire hook** (for `sync`): `rsync -a --delete --exclude .git/ <worktree>/
  <root>/`, honoring `protect`. Overwrites whatever the last holder left; **no
  cleanup step** — the next holder's acquire is the reset. A worktree session
  always re-syncs its own files on acquire, so it never depends on root's prior
  contents.
- **Release hook** (optional, e.g. C# binaries): syncs build artifacts from root
  back into the holder's own worktree. **Race-free by ordering** — it runs
  inside the exclusive window, reading root (exclusively owned at that instant)
  and writing the holder's own worktree (private). Safe *as long as releasing
  the ticket is strictly last*.
- `acquire: none` covers cases that only need exclusive execution from root
  (e.g. a root `.env` already present, no WIP to sync in).

### 5. The "root is exclusive-or" policy

Root is **either** a live working session **or** the lease sandbox — never both.
This is what makes a destructive acquire hook safe. (Domain-independent: holds
identically for docker/iOS/C#/`.env`.)

1. **Live root session present → sandbox locked out.** If any live session has
   `cwd == shared-root`, `queue-run` from a worktree **blocks** (no acquire-hook
   runs); the pane shows *"root held by live session ‹name›."* Root's files stay
   untouched. (A live root session is an implicit exclusive holder.) Liveness
   uses the same detection `--gc` relies on: flock on the JSONL / mtime within
   60s.
2. **No live root session → sandbox free.** The first worktree holder takes it;
   the acquire hook proceeds. From here root belongs to lease holders.
3. **Transition guard.** Flipping from "a root session existed" to sandbox mode,
   if root has **uncommitted git changes**, `queue-run` refuses with a clear
   message ("root has uncommitted changes the sandbox would overwrite —
   stash/commit first") rather than silently clobbering. A *clean* root proceeds
   without nagging.
4. **Prevention layer.** In a queue-enabled project the new-session dialog
   **defaults the worktree checkbox ON** and warns if the user tries to create a
   plain root session ("this project uses the root queue — root is a shared
   sandbox; create a worktree instead").

**Why both prevention and enforcement:** we cannot hard-forbid root sessions — a
user can run `claude` in root entirely outside the explorer. So the dialog
(prevention) makes root-working rare, and the exclusive-or rule (enforcement)
keeps it *safe* when it happens anyway. Worst case is a worktree agent waiting
(visible and actionable in the pane), never data loss.

**Accepted tradeoff — starvation:** an idle-but-live root session pauses all
parallel work on that resource. This is correct: it is exactly the situation
where clobbering would lose work. The pane makes the reason obvious so the user
can close the root session to unblock.

### 6. TUI — setup/test dialog + queue pane

**One config screen, two entry points** (don't build two things):

- The new-session dialog's **"use queue to access root"** checkbox — first-time
  setup; reflects existing state (pre-checked once opted in) so it doubles as the
  off switch. In a queue-enabled project the worktree checkbox defaults ON
  (policy #4).
- A key in the **queue pane** — view / modify / test an existing config.

The screen is **template-first**:

- Opens on a **template picker**: `Docker + browser`, `iOS xcodebuild`,
  `C# root-binaries`, `.env in root`, `Custom / blank`. Templates *are* the
  named-strategy library, pre-filling `guard` / `acquire` / `release` /
  `run_in`. The `.env in root` template ships as `acquire: sync` with
  `protect: [".env"]` (or `acquire: none`).
- Then ~5 editable fields. The **expanded** `acquire`/`release` command is shown
  (e.g. the full `rsync … --delete` line), not hidden. Raw-shell editing is an
  *Advanced* reveal — casual users never hand-write shell.
- Keep it to **one screen** (picker + fields + test panel), not a multi-step
  wizard — consistent with the project's minimalism.

**Test panel** (this is where the destructive-rsync risk is de-risked):

- **Guard-match tester** — type a command, see *queued / runs-free*. Zero side
  effects.
- **Acquire dry-run** — runs the `sync` strategy as `rsync --dry-run`, showing
  adds / overwrites / **deletes** (deletes highlighted) plus the exclusive-or
  check. A missing `protect` entry is caught *before* any destructive run.
- **Honest limitation:** dry-run is fully safe only for the `sync` strategy. A
  *custom* shell `acquire` can be validated for parse + guard-match but cannot be
  safely simulated; the panel says so plainly.

**Queue pane:**

- Toggleable region (~bottom 20% of the tree), live on the existing ~2s refresh
  loop. Per resource: current holder (+ elapsed) and the waiting line in order.
  Waiting surfaces **position ("2 of 3")**, not an opaque spinner.
- **Detection flag:** if root is touched (mtime change) while **no ticket is
  held**, the pane flags *"out-of-lease access."* This converts silent bypass
  into a visible, actionable signal.
- Toggle key chosen to avoid case-variant collisions with existing actions.

### 7. Agent awareness & enforcement — v1 = command-guard + detection

No hook can *guarantee* compliance (agents reach root through wrappers the hook
can't see into — `make`, `npm run`, shell scripts). v1 therefore relies on
**awareness + the exclusive-or safety net + detection**, with a best-effort
command-guard nudge:

- **`SessionStart` `additionalContext` injection** for opted-in projects (the
  strongest, cheapest lever — unconditional, lands before the agent's first
  action): tells every agent the root resource is shared and **always warm**, to
  **never build its own stack** (it collides on the well-known ports), and to run
  guarded commands via `queue-run`.
- **`PreToolUse` Bash hook**, registered once at install, that **no-ops unless
  the current repo is opted in** (zero overhead/interference elsewhere). For
  opted-in repos it matches the declared `guard` patterns and **denies +
  redirects**: "re-run as `queue-run -- …`." Denying (rather than silently
  wrapping) keeps the lease lifecycle inside the one `queue-run` process. Fails
  open, never blocks startup.
- **Skill + `CLAUDE.md` snippet** explaining the why/how so agents cooperate
  gracefully — don't busy-spin, report queue position, understand the
  sync-overwrite semantics, never boot a competing stack.

**Deferred — root-write-guard.** A stronger hook that denies worktree Bash
writing under shared-root without a lease was considered and deferred. Accepted
residual: wrapper commands (`make` / `npm run`) that reach root internally slip
past command-matching — but they surface as **out-of-lease flags** in the pane
(§6), so bypass is *visible*, not silent. The honest ceiling of the design is
"bypass is rare and visible," never "bypass is impossible."

## Build order (independently shippable)

1. **Queue core + CLI** (`queue-run` / `queue-status` / `queue-cancel`) + the
   `sync` strategy with `protect` — usable immediately with only `CLAUDE.md`
   guidance.
2. **TUI** — setup/test dialog + queue pane + detection flag.
3. **Awareness / enforcement** — `SessionStart` injection + command-guard hook +
   skill.

Each is its own spec → plan → implementation cycle.

## Constraints honored

- Minimal deps; no new runtime dependency (`rsync` / `flock` are system tools;
  Textual stays vendored).
- `flock` + temp-file-rename for every queue write (matches existing stores).
- Hooks fail open and never block startup.
- Opt-in, like retention — nothing activates until the user checks the box.
- Per-user config in `~/.claude/`; the repo is never touched.
- Don't move or rewrite native JSONLs; the queue is orthogonal to them.

## Open items deferred

- **Root-write-guard hook** (§7) — only if command-guard + detection proves
  insufficient in practice.
- Exact toggle keybinding for the pane (pick during implementation, avoiding
  case-variant collisions).
- Whether `queue-status` should expose per-resource history/metrics beyond the
  current snapshot (not needed for v1).
- `SPEC.md` is the authoritative source for this project; update it in the same
  change set when implementing, rather than letting code and spec diverge.
