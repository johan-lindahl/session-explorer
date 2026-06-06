# Shared-singleton lease engine — design

**Date:** 2026-06-05 (revised 2026-06-06)
**Status:** Design approved (revised, deepened); pending spec review → implementation plan.

## Problem

When working with multiple git worktrees (often one per parallel AI agent),
some commands can only run against a **singleton resource** that the worktrees
share — and worktrees isolate *files*, not *runtime*. As one survey puts it:
"worktrees are strong at code isolation, but they do not solve runtime isolation
by themselves" — leaving ports, databases, caches, secrets, simulators and
test state as uncontrolled collision points.

The motivating case is a heavy Docker stack (db + web + redis + …) that
bind-mounts the repository root and serves those files, with browser tests
(Puppeteer/Playwright) hitting the served view on **well-known ports** (8080,
etc.) baked into the harness. But the same shape recurs whenever an action must
use a shared singleton:

- A bind-mounted dev stack on fixed ports (two worktrees both want `:3000`,
  `:5432`, `:8080` → `EADDRINUSE`).
- A browser e2e suite that must hit the app at a **fixed URL/port**.
- An iOS `xcodebuild` whose `build.db`, code-signing/keychain state, and
  simulator are global singletons (`build.db is locked`).
- An integration suite against a **single local database**, where one agent's
  migrations "instantly wipe out the DB state the other relies on."
- A build/test whose credentials live solely in a root `.env`.
- A **single physical device** (HIL rig, one emulator) or a **license seat**
  pool, where the resource is physically singular.

In every case, agents "hijack" the shared resource to run, and concurrent
access produces a **shared-mutable-state race**: overwriting each other's
files, wiping state the other is still using, or simply colliding on a port or
a lock.

### Why isolation doesn't always work

For *many* of these, the industry default is **isolation**, and it is the right
answer when available: dynamic ports, `--project-name`, per-worktree DB volumes,
per-job `-derivedDataPath`, `ThreadLocal` drivers, tools like worktree-compose.
**Reach for this engine only when isolation is genuinely ruled out**, because:

- the resource is **heavy/slow to boot**, so cloning per worktree is too costly;
- it is **tied to a fixed path** (bind-mounted) or **well-known ports** baked
  into the harness;
- or it is **physically singular** (one device, one simulator, N license seats).

This is the **serialize-the-singleton** problem, the deliberate complement to
the isolate-the-resource problem. Each template below states *why isolation is
ruled out* so the engine is not misapplied where isolation would be cleaner.

## Core model — a generic resource-lease engine

The engine is **not** a docker-test queue. It is:

> A class of commands must use a shared singleton resource; serialize access and
> optionally move files / wait for readiness around each lease.

`queue-run` is a generic **lease-around-a-command** runner. Crucially, **the
shared resource is not always the repository root** — it may be a directory, a
fixed path, a port/service, a physical device, or an abstract name. So the first
parameter of any resource is its **`kind`**, and most other behavior is
conditional on it.

### Resource kinds

| `kind` | Example | sync applies? | exclusive-or-root policy? | typical `run_in` |
|---|---|---|---|---|
| `root-dir` | bind-mounted repo root | yes | **yes** | `root` |
| `path` | a non-root shared dir/artifact cache | yes | no | `root`/`worktree` |
| `port` / `service` | a single local DB / dev server | no (usually) | no | `worktree` |
| `device` | one simulator / HIL rig | no | no | `worktree` |
| `name` | abstract singleton / license seat | no | no | `worktree` |

Two behaviors that earlier drafts treated as universal are **`kind`-specific**:

- **`sync`** (rsync worktree↔root) only makes sense for `root-dir` / `path`.
- **The exclusive-or-with-live-root-session policy (§5)** only applies to
  `kind: root-dir`. A simulator, DB, or seat has no "live root session" analog —
  it is a plain named lease.

Keystone insight (unchanged, applies to `root-dir`):

> Replace "copy in → run → delete my files" with **"acquire-hook → run → (no
> cleanup)."** For the `sync` strategy, on acquire the holder `rsync`s its
> worktree *over* root, blowing away whatever the previous holder left. The
> *next* holder's acquire is the reset, so there is nothing to clean up. The
> "cleanup wipes my competitor's files" bug disappears by construction.

A warm singleton (the Docker stack) never reboots: each holder syncs files into
the bind-mounted directory, signals/waits for readiness, runs, releases. Boot
cost is paid once.

## Components

### 1. Queue core — daemon-less FIFO + flock crash-reaping

- Store: `~/.claude/session-explorer-queues/<resource>/`, one **ticket file**
  per participant (monotonic ticket number, session id, cwd, command, PID,
  heartbeat mtime). Mirrors the existing `flock` + sidecar-`.lock` +
  temp-file-rename pattern used by `index.py` / `folder_store.py` / `live.py`.
- **Holder = the lowest ticket number whose process is alive.** Ordering comes
  from the number; the queue *is* the set of ticket files — no daemon remembers
  order.
- **Crash-reaping via flock-on-own-ticket.** Each `queue-run` process holds
  `LOCK_EX` on *its own* ticket file for its whole life. A waiter tests the
  holder's liveness by attempting `LOCK_EX | LOCK_NB` on the holder's ticket: if
  grabbable, the holder is dead (the kernel released the lock on exit, including
  `SIGKILL`) → the waiter reaps the stale file and advances. Survives `SIGKILL`,
  immune to PID-reuse. PID + heartbeat mtime are recorded for **display only**,
  never for the reap decision.
- No central "holder lock": ticket numbers are unique, so exactly one ticket is
  ever the lowest-live one, so exactly one process concludes it is the holder.
  No tie, no TOCTOU.
- **Capacity (deferred — design room).** v1 forces a single holder. The queue is
  designed so a future `capacity: N` semaphore (holder = the N lowest live
  tickets) is a clean extension, valid only for `acquire: none` resources
  (license seats). `sync`/`root-dir` resources are inherently capacity 1.
- Concurrency: `flock` + temp-file-rename guard the ticket counter and store
  writes — the same primitive the index and folder stores already use.
- **Generalized to named resources** of any `kind`; the pane shows all active
  queues.

### 2. Per-project config — declarative, per-user, never committed

Lives per-user in `~/.claude/` keyed by project path (consistent with the
index/folder stores), **not** committed to the repo — invisible to teammates who
don't use the explorer or don't work in parallel worktrees.

A project opts in and declares one or more **resources**. Each resource carries
the parameter model below.

#### Parameter model (v1 core vs deferred)

| Param | Purpose | Status |
|---|---|---|
| `kind` | `root-dir`/`path`/`port`/`device`/`name` — governs sync + exclusive-or applicability | **core** |
| `resource` | the resource's name/key (and, for `root-dir`/`path`, its filesystem path; auto-derived from `index._project_label`/`project_path` for `root-dir`) | **core** |
| `opted-in` | per-project activation flag | **core** |
| `guard` | command patterns that must hold the lease (user-declared; irreducibly project-specific) | **core** |
| `run_in` | working directory for the command — `root` or `worktree` | **core** |
| `acquire` / `release` | lifecycle hooks; each = a **strategy**: `sync` \| `none` \| `command` (arbitrary shell) | **core** |
| `sync{delete,exclude,protect}` | the `sync` strategy's knobs (see below) | **core** |
| `health` | command/probe answering "is the resource up?" — v1 **detects + warns**, does not auto-start | **core** |
| `wait_for` | readiness probe (port/URL/command) + timeout, run after acquire, before the command — kills the #1 flake source | **core** |
| `ensure` | command to **auto-start** a down resource | *deferred* (v1: warn, user starts it) |
| `reload` | post-acquire signal so a warm server picks up synced files (restart/HUP/touch) | *deferred* (most servers serve off disk live) |
| `env` | resource coordinates (port/path/DB URL) exported into the command's environment | *deferred* (hardcode for now) |
| `capacity` | N-seat semaphore (only valid with `acquire: none`) | *deferred* (v1 forces 1) |

The deferred params are **schema-reserved**: adding them later needs no
breaking change.

#### The `sync` strategy knobs

- `delete: true` — required for the "next acquire is the reset" property.
- `exclude: [".git"]` — paths not copied *in* from the worktree.
- **`protect: []`** — root-only paths that must never be overwritten or deleted
  (`.env`, local secrets, large root-only binaries). This is the `--delete`
  safety valve: without it, `--delete` would destroy root-only files that don't
  exist in the worktree. The dry-run test (§6) surfaces exactly which files a
  missing `protect` entry would delete, *before* any destructive run.

### 3. CLI spine

One command runs the whole lease lifecycle in a **single process** so release is
guaranteed on exit/crash:

```
session-explorer queue-run --resource <r> -- <command>
```

Plus:

- `session-explorer queue-status` — JSON (for the pane) + human-readable.
- `session-explorer queue-cancel` — drop a waiting ticket.

### 4. Lease lifecycle

The order is load-bearing:

> take ticket → wait turn → **[if `kind: root-dir`: exclusive-or check]** →
> **`health` check (warn if down)** → run **`acquire`** hook → **`wait_for`**
> readiness → run `<command>` in `run_in` → run **`release`** hook → **release
> ticket (strictly last)**

- **`acquire` strategies:** `sync` (`rsync -a --delete --exclude .git/
  <worktree>/ <root>/`, honoring `protect`); `none` (serialize only — no file
  movement); `command` (arbitrary shell, e.g. a DB reset). A worktree session's
  `sync` always re-syncs its own files, so it never depends on root's prior
  contents.
- **`release` is race-free by ordering** — all file movement (e.g. syncing build
  artifacts back to the holder's own worktree) happens *inside* the exclusive
  window; releasing the ticket is strictly last. It reads root (exclusively owned
  at that instant) and writes the holder's own worktree (private) → zero
  contention.

### 5. The "root is exclusive-or" policy (only `kind: root-dir`)

Root is **either** a live working session **or** the lease sandbox — never both.
This is what makes a destructive `sync` acquire safe. (Does **not** apply to
`port`/`device`/`name` resources, which are plain named leases.)

1. **Live root session present → sandbox locked out.** If any live session has
   `cwd == shared-root`, `queue-run` from a worktree **blocks** (no acquire-hook
   runs); the pane shows *"root held by live session ‹name›."* Root's files stay
   untouched. (A live root session is an implicit exclusive holder.) Liveness
   uses the same detection `--gc` relies on: flock on the JSONL / mtime within
   60s.
2. **No live root session → sandbox free.** The first worktree holder takes it;
   the acquire hook proceeds. From here root belongs to lease holders.
3. **Transition guard.** Flipping from "a root session existed" to sandbox mode,
   if root has **uncommitted git changes**, `queue-run` refuses ("root has
   uncommitted changes the sandbox would overwrite — stash/commit first") rather
   than silently clobbering. A *clean* root proceeds without nagging.
4. **Prevention layer.** In a queue-enabled project the new-session dialog
   **defaults the worktree checkbox ON** and warns if the user tries to create a
   plain root session ("this project uses the root queue — root is a shared
   sandbox; create a worktree instead").

**Why both prevention and enforcement:** we cannot hard-forbid root sessions — a
user can run `claude` in root entirely outside the explorer. The dialog
(prevention) makes root-working rare; the exclusive-or rule (enforcement) keeps
it *safe* when it happens anyway. Worst case is a worktree agent waiting
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
  (policy §5.4).
- A key in the **queue pane** — view / modify / test an existing config.

The screen is **template-first** (templates *are* the strategy library — see
§7), opening on a picker, then ~5–7 editable fields scaled to `kind`. The
**expanded** `acquire`/`release` command is shown (not hidden); raw-shell editing
is an *Advanced* reveal. Keep it to **one screen**, not a multi-step wizard —
consistent with the project's minimalism.

**Test panel** (where the destructive-`sync` risk is de-risked):

- **Guard-match tester** — type a command, see *queued / runs-free*. Zero side
  effects.
- **Acquire dry-run** — runs the `sync` strategy as `rsync --dry-run`, showing
  adds / overwrites / **deletes** (highlighted) plus the exclusive-or check. A
  missing `protect` entry is caught *before* any destructive run.
- **Health probe** — runs the `health` check so the user sees "resource up/down"
  while configuring.
- **Honest limitation:** dry-run is fully safe only for `sync`. A *custom* shell
  `acquire`/`command` can be validated for parse + guard-match but cannot be
  safely simulated; the panel says so plainly.

**Queue pane:**

- Toggleable region (~bottom 20% of the tree), live on the existing ~2s refresh
  loop. Per resource: current holder (+ elapsed) and the waiting line in order,
  surfacing **position ("2 of 3")**.
- **Detection flag:** if a `root-dir` resource is touched (mtime change) while
  **no ticket is held**, the pane flags *"out-of-lease access"* — converting
  silent bypass into a visible, actionable signal.
- Toggle key chosen to avoid case-variant collisions with existing actions.

### 7. Template library (grounded in common pain)

Templates pre-fill the parameter model for documented real-world cases. Each
states **why isolation is ruled out** (else isolate instead). The picker offers
these plus `Custom / blank`.

| Template | Documented pain | Isolation ruled out because | `kind` · `acquire` · `run_in` · `release` | guard / protect defaults |
|---|---|---|---|---|
| **Bind-mounted stack, well-known ports** | worktrees fight over `:3000/:5432/:8080`; stack serves off one bind-mounted path | heavy/slow boot; ports baked in; one fixed serve path | `root-dir` · `sync` · `root` · none; `health`+`wait_for` on the stack | guard `docker\|compose\|up`; protect `[]` |
| **Browser e2e vs fixed-URL app** | Playwright/Cypress `reuseExistingServer`+fixed `baseURL` go flaky/`EADDRINUSE` across parallel runs | app-under-test must answer at a known URL/port | `root-dir` · `sync` · `root` · none; `wait_for` the URL | guard `playwright\|cypress\|e2e`; protect `[]` |
| **iOS `xcodebuild` from root** | concurrent builds hit `build.db is locked`; keychain/signing/simulator are global singletons | shared `~/Library/Developer`, signing session, one simulator | `device`/`name` · `none` · `worktree` · *(optional build product back)* | guard `xcodebuild`; no sync |
| **Single shared database** | parallel migrations "instantly wipe out the DB state the other relies on" | one DB instance on a fixed socket/port; per-agent clones not worth it | `port` · `command` (db reset) · `worktree` · none | guard migrate/test cmds; (no protect; `env` deferred) |
| **Root-only credentials / `.env`** | an "over-helpful `.env`" / root-only secrets poison or are absent in worktrees | secrets exist only at root by policy | `root-dir` · `sync` (`protect:['.env']`) · `root` · none | guard build+test cmds; **protect `['.env']`** |
| **Single device / HIL / license seat** | can't run >1 session on one emulator/device; HIL needs an explicit device lock; paid tools have N seats | the resource is *physically* singular (capacity N) | `device`/`name` · `none` · `worktree` · none; (`capacity` deferred) | guard the device/tool cmd; no sync |

The last row proves the engine's generality: **resource ≠ root** — a pure lease
with `acquire: none`, `run_in: worktree` serializes a device or seat with no
file-sync and no exclusive-or-root policy at all.

### 8. Agent awareness & enforcement — v1 = command-guard + detection

No hook can *guarantee* compliance (agents reach the resource through wrappers
the hook can't see into — `make`, `npm run`, shell scripts). v1 relies on
**awareness + the exclusive-or safety net + detection**, with a best-effort
command-guard nudge:

- **`SessionStart` `additionalContext` injection** for opted-in projects (the
  strongest, cheapest lever — unconditional, lands before the agent's first
  action): tells every agent the resource is shared and **already warm**, to
  **never build its own stack** (it collides on the well-known ports), and to run
  guarded commands via `queue-run`.
- **`PreToolUse` Bash hook**, registered once at install, that **no-ops unless
  the current repo is opted in**. For opted-in repos it matches the declared
  `guard` patterns and **denies + redirects**: "re-run as `queue-run -- …`."
  Denying (rather than silently wrapping) keeps the lease lifecycle inside the
  one `queue-run` process. Fails open, never blocks startup.
- **Skill + `CLAUDE.md` snippet** for graceful cooperation — don't busy-spin,
  report queue position, understand sync-overwrite semantics, never boot a
  competing stack.

**Deferred — root-write-guard.** A stronger hook denying worktree Bash that
writes under shared-root without a lease was considered and deferred. Accepted
residual: wrapper commands (`make`/`npm run`) slip past command-matching — but
they surface as **out-of-lease flags** in the pane (§6), so bypass is *visible*,
not silent. The honest ceiling is "bypass is rare and visible," never "bypass is
impossible."

## Build order (independently shippable)

1. **Queue core + CLI** (`queue-run`/`queue-status`/`queue-cancel`) + the
   `kind` model + `sync`/`none`/`command` strategies (with `protect`) +
   `health`/`wait_for` — usable immediately with only `CLAUDE.md` guidance.
2. **TUI** — template-first setup/test dialog + queue pane + detection flag.
3. **Awareness / enforcement** — `SessionStart` injection + command-guard hook +
   skill.

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

- **Parameters:** `ensure` (auto-start), `reload`, `env` injection, `capacity`>1
  — schema-reserved; implement when a real template needs them.
- **Root-write-guard hook** (§8) — only if command-guard + detection proves
  insufficient in practice.
- Exact toggle keybinding for the pane (pick during implementation, avoiding
  case-variant collisions).
- Whether `queue-status` should expose per-resource history/metrics beyond the
  current snapshot (not needed for v1).
- `SPEC.md` is the authoritative source for this project; update it in the same
  change set when implementing, rather than letting code and spec diverge.

## Sources (grounding for §7)

- Penligent — *Git Worktrees Need Runtime Isolation for Parallel AI Agent
  Development* (resource collision categories).
- GPT Frontier — *Preventing Database and Port Collisions with Concurrent AI
  Agents*.
- worktree-compose (isolate-first tooling; explicit shared-vs-isolated control
  plane).
- Playwright docs — *Web server* / *Parallelism* (`reuseExistingServer`, fixed
  port flakiness).
- *Fixing "build.db is locked" in Xcode* + *Parallel xcodebuild on Cloud Mac CI*
  (DerivedData/signing/simulator singletons).
- DeFlaky — *Parallel Test Execution Causing Flaky Tests* (shared-DB races).
- Appium java-client #1451 (single-device parallel limits); Concourse CI —
  *Hardware in the loop testing* (device locking/queuing).
