# Shared-singleton lease engine — design

**Date:** 2026-06-05 (revised 2026-06-06)
**Status:** Design approved (revised, deepened, with TUI visual design); pending
spec review → implementation plan.

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
the isolate-the-resource problem. Each template (§7) states *why isolation is
ruled out* so the engine is not misapplied where isolation would be cleaner.

## Core model — a generic resource-lease engine

The engine is **not** a docker-test queue, and — importantly — **the shared
resource is not always the repository root.** It is:

> A class of commands must use a shared singleton resource; serialize access and
> optionally move files / wait for readiness around each lease.

`queue-run` is a generic **lease-around-a-command** runner. A project declares a
**set of resources** (0..N), each with a **`kind`**, its own guard, its own
strategy, and its own queue. "Root" is just a resource of `kind: root-dir` — one
row among possibly several (a project can guard `root` *and* a simulator *and* a
DB port at once). The TUI vocabulary follows this: the feature is **shared
resources** (the pane is **Queues**); there is no privileged "root queue."

### Resource kinds

| `kind` | Example | sync applies? | exclusive-or-root policy? | typical `run_in` |
|---|---|---|---|---|
| `root-dir` | bind-mounted repo root | yes | **yes** | `root` |
| `path` | a non-root shared dir/artifact cache | yes *(v1: deferred — see below)* | no | `root`/`worktree` |
| `port` / `service` | a single local DB / dev server | no (usually) | no | `worktree` |
| `device` | one simulator / HIL rig | no | no | `worktree` |
| `name` | abstract singleton / license seat | no | no | `worktree` |

Two behaviors that earlier drafts treated as universal are **`kind`-specific**:

- **`sync`** (rsync worktree↔root) only makes sense for `root-dir` / `path` — and
  in **v1 is restricted to `root-dir`**. `path`+`sync`+`--delete` is equally
  destructive but the baseline/sandbox-transition safety model (§2) and the
  exclusive-or policy (§5) are framed around root; extending them to arbitrary
  shared paths is deferred (no v1 template needs it). v1 `path` resources are
  `acquire: none`/`command` only.
- **The exclusive-or-with-live-root-session policy (§5)**, the new-session
  prevention layer, and the pane's out-of-lease **detection flag** all apply
  **only to `kind: root-dir`**. A simulator, DB, or seat is a plain named lease
  with no root to protect.

Keystone insight (applies to `root-dir`):

> Replace "copy in → run → delete my files" with **"acquire-hook → run → (no
> cleanup)."** For the `sync` strategy, on acquire the holder `rsync`s its
> worktree *over* root, blowing away whatever the previous holder left. The
> *next* holder's acquire is the reset, so there is nothing to clean up. The
> "cleanup wipes my competitor's files" bug disappears by construction.

A warm singleton (the Docker stack) never reboots: each holder syncs files into
the bind-mounted directory, waits for readiness, runs, releases. Boot cost is
paid once.

## Components

### 1. Queue core — daemon-less FIFO + flock crash-reaping

- **Queue identity — keyed by project *and* resource, never resource alone.**
  Store: `~/.claude/session-explorer-queues/<project-id>/<resource>/`, where
  `<project-id>` is a stable hash of the **canonical repo root**, computed by a
  **new shared helper** (used identically by queue identity, `cwd` resolution in
  §3, and live-root matching in §5): `git rev-parse --show-toplevel` to resolve an
  arbitrary subdirectory cwd to the git top-level, `realpath` to canonicalize
  symlinks, then the existing `/.claude/worktrees/` collapse (git common-dir
  identity) so a worktree maps onto its parent repo. The current
  `index.project_root()` only string-strips the worktree marker and resolves
  neither subdirs nor symlinks, so it is **insufficient on its own** — the helper
  must be added. Resource names like `root` /
  `db` recur across projects, so keying by resource alone would collide; the
  project hash disambiguates. The human-readable `‹project› / ‹resource›` label
  shown in the pane is display metadata stored *inside* the ticket, kept separate
  from the on-disk key. One **ticket file** per participant holds: monotonic
  ticket number, session id, cwd, command, PID, heartbeat mtime, display label.
  Mirrors the existing `flock` + sidecar-`.lock` + temp-file-rename pattern
  (`index.py` / `folder_store.py` / `live.py`).
- **Holder = the lowest ticket number whose process is alive.** Ordering from
  the number; the queue *is* the set of ticket files — no daemon remembers order.
- **Crash-reaping via flock-on-own-ticket.** Each `queue-run` process holds
  `LOCK_EX` on *its own* ticket file for its whole life. A waiter tests the
  holder's liveness by attempting `LOCK_EX | LOCK_NB` on the holder's ticket: if
  grabbable, the holder is dead (kernel released the lock on exit, including
  `SIGKILL`) → the waiter reaps the stale file and advances. Survives `SIGKILL`,
  immune to PID-reuse. PID + heartbeat mtime are recorded for **display only**.
- **Ticket publication ordering (so a live ticket is never mis-reaped).** All of
  *allocate number → write temp ticket → acquire `LOCK_EX` on it → atomic rename
  into the visible queue dir* happens under an exclusive lock on the queue's
  `.lock` metadata file, and the metadata lock is released only after the rename.
  A ticket is therefore **published only after it already holds its lifetime
  lock**, so a waiter's `LOCK_NB` liveness probe can never catch a just-created
  ticket in the unlocked gap and falsely reap it as dead.
- No central "holder lock": ticket numbers are unique, so exactly one ticket is
  ever the lowest-live one → exactly one holder. No tie, no TOCTOU.
- **Capacity (deferred — design room).** v1 forces a single holder. The queue is
  designed so a future `capacity: N` semaphore (holder = the N lowest live
  tickets) is a clean extension, valid only for `acquire: none` resources
  (license seats). `sync`/`root-dir` resources are inherently capacity 1.
- Concurrency: `flock` + temp-file-rename guard every queue write — the same
  primitive the index and folder stores already use.
- **Per resource, of any `kind`;** the pane shows all active queues across
  opted-in projects.

### 2. Per-project config — declarative, per-user, never committed

Lives per-user in `~/.claude/` keyed by project path (consistent with the
index/folder stores), **not** committed to the repo — invisible to teammates who
don't use the explorer or don't work in parallel worktrees.

A project opts in by declaring **one or more resources**. "Opted in" simply means
"has ≥1 resource." Each resource carries the parameter model below.

#### Parameter model (v1 core vs deferred)

| Param | Purpose | Status |
|---|---|---|
| `kind` | `root-dir`/`path`/`port`/`device`/`name` — governs sync + exclusive-or applicability | **core** |
| `resource` | the resource's name/key (and, for `root-dir`/`path`, its filesystem path; auto-derived from `index._project_label`/`project_path` for `root-dir`) | **core** |
| `guard` | commands that must hold the lease — matched on **parsed argv** (executable basename + required subcommand tokens), not substring regex (see below) | **core** |
| `run_in` | working directory for the command — `root` or `worktree` | **core** |
| `acquire` / `release` | lifecycle hooks; each = a **strategy**: `sync` \| `none` \| `command` (arbitrary shell) | **core** |
| `sync{delete,exclude,protect}` | the `sync` strategy's knobs (see below) | **core** |
| `health` | command/probe answering "is the resource up?" — v1 **detects + warns**, does not auto-start | **core** |
| `wait_for` | readiness probe (port/URL/command) + timeout, run after acquire, before the command | **core** |
| `ensure` | command to **auto-start** a down resource | *deferred* (v1: warn, user starts it) |
| `reload` | post-acquire signal so a warm server picks up synced files | *deferred* |
| `env` | resource coordinates (port/path/DB URL) exported into the command's environment | *deferred* |
| `capacity` | N-seat semaphore (only valid with `acquire: none`) | *deferred* (v1 forces 1) |

Deferred params are **schema-reserved**: adding them later needs no breaking
change. (The project-level opt-in flag is implicit — a project is opted in iff
it has ≥1 resource.)

**Guard matching semantics.** A guard is **not** a substring regex over raw shell
text — `up` must never match `cleanup` or `npm run setup`. Each guard is a
`{exe, sub?}` rule matched against the command's **parsed argv**: the executable
**basename** (`/usr/local/bin/docker` → `docker`) plus optional required leading
**subcommand tokens**. So `{exe: docker, sub: [compose, up]}` matches
`docker compose up -d` but not `docker ps`. Wrapper commands (`make e2e`,
`npm run test:e2e`) are deliberately out of the hook's scope and are caught by
the §6 out-of-lease detection flag instead. Template defaults ship as
conservative `{exe, sub}` rules, never bare substrings.

#### The `sync` strategy knobs and exact rsync filters

`sync` is the most dangerous primitive in the design (`--delete` against root),
so its filters are specified exactly, not as `--exclude .git`.

- `delete: true` — required for the "next acquire is the reset" property. Never
  pass `--delete-excluded`; excluded/protected paths must always survive deletion.
- `exclude: ["/.git"]` — paths **not copied in** from the worktree, as
  **anchored, slash-prefixed, no-trailing-slash** patterns. `.git` is a *file* in
  a worktree (a gitdir pointer) but a *directory* in root; `/.git/` (trailing
  slash) would match only the directory and let the worktree's `.git` **file** be
  copied into root, corrupting root's repo. `/.git` matches either. Rendered as
  `--filter='exclude /.git'` (and one per extra exclude).
- **`protect: [...]`** — root-only paths to preserve **untouched** (neither
  overwritten nor deleted). Implemented as **anchored exclude filters**
  (`--filter='exclude /<path>'`), *not* rsync `protect`/`P` rules: a `P` rule
  blocks receiver-side **deletion only**, so if the worktree also contains the
  path it would still **overwrite** root's copy. `exclude` is the correct
  primitive — it removes the path from the transfer *and* (with `--delete`) from
  deletion, so root's version wins on both axes. `protect` and the `exclude` knob
  therefore share one mechanism (anchored rsync excludes) and differ only in
  intent: `exclude` drops worktree junk (`node_modules`, `/.git`) on the way
  *in*; `protect` preserves a root-only file. `/.git` appears in both for clarity.
- **Protected *baseline* vs prior-holder residue — what makes the reset safe.**
  A `--delete` acquire must remove the *previous lease holder's* files (that is
  the reset) while never touching genuine root-only/local files (`.env`, caches,
  certs). The baseline is therefore defined **purely from git**, *not* from a
  diff against the first holder's worktree: it is the set of root's
  **gitignored + untracked** paths (`git status --ignored --porcelain` /
  `git ls-files --others`), captured at the **first sandbox transition** and
  auto-added to `protect` (seeded from `/.git`, `/.env`, `/.env.*`). Deriving it
  from the worktree diff would be wrong — files a branch *intentionally deleted*,
  or files missing because the first worktree is dirty/divergent, would get
  permanently protected and root would stop matching the holder. A small
  per-resource **sandbox marker** records that the resource is in sandbox mode and
  the baseline is established. (Tracked files absent from a later holder's
  worktree are legitimately deleted by the reset — they are not in the baseline.)
- **Dry-run refusal fires *only* at that first transition, never in steady
  state.** At the first transition `queue-run` runs `rsync --dry-run`; any
  would-delete path **not** already in the baseline is a candidate root-only file
  → it **refuses** and lists them (*"these root files would be deleted — protect
  or remove them"*) until the baseline is confirmed. Once in sandbox mode,
  acquires `--delete` **freely** — prior-holder residue is *supposed* to go; only
  the protected baseline survives. This reconciles the deletion-safety gate with
  the "next acquire is the reset" model: the gate guards the **one** dangerous
  flip, not every run.

### 3. CLI spine

One command runs the whole lease lifecycle in a **single process** so release is
guaranteed on exit/crash:

```
session-explorer queue-run --resource <r> -- <command>
```

**Project resolution (resources are per-project, names repeat).** `--resource
<r>` is resolved against a project: by default the **canonical repo root derived
from `cwd`** (collapsing worktree → parent, exactly as the index does), so an
agent in a worktree resolves to its parent project's resource without extra
flags. Overrides: `--project <root>` to name the project explicitly, and a
fully-qualified `<project-id>/<resource>` id for non-cwd callers (the TUI, hooks,
`queue-status`). If `cwd` resolves to no opted-in project, `queue-run` errors
clearly rather than guessing.

Plus `session-explorer queue-status` (JSON for the pane + human-readable; emits
fully-qualified ids) and `session-explorer queue-cancel` (drop a waiting ticket).

### 4. Lease lifecycle

The order is load-bearing:

> take ticket → wait turn → **[if `kind: root-dir`: exclusive-or check]** →
> **`health` check (warn if down)** → run **`acquire`** hook → **`wait_for`**
> readiness → run `<command>` in `run_in` → run **`release`** hook → **release
> ticket (strictly last)**

- **`acquire` strategies:** `sync` (`rsync -a --delete <worktree>/ <root>/` with
  the **exact `--filter` exclude/protect rules and the runtime dry-run refusal
  specified in §2**, never a bare `--exclude .git`); `none` (serialize only);
  `command` (arbitrary shell, e.g. a DB reset). A worktree session's `sync`
  always re-syncs its own files, so it never depends on root's prior contents.
- **`release` is race-free by ordering** — all file movement (e.g. syncing build
  artifacts back to the holder's own worktree) happens *inside* the exclusive
  window; releasing the ticket is strictly last.

**Failure semantics (explicit).** The lease is always cleaned up and the
caller's intent is preserved:

- **Ticket release is in a `finally`** — the ticket file is removed (and its
  lifetime lock dropped) however `queue-run` exits: command success, command
  failure, exception, or signal.
- **Exit code is the child's.** `queue-run` exits with the wrapped command's exit
  status, so it is transparent to callers/CI. A pre-command refusal (exclusive-or
  block, dry-run refusal, resource-resolution error) uses a distinct reserved
  non-zero code so it's distinguishable from a test failure.
- **Signals (`SIGINT`/`SIGTERM`)** are trapped: forward to the child, then run
  release and exit. Crash/`SIGKILL` is still covered by flock auto-release +
  reaping (§1).
- **Release-hook failures don't strand the queue.** The `release` hook is
  **time-bounded**; if it fails or times out, `queue-run` still **releases the
  ticket** (removing it from the active queue so the next holder proceeds) and
  writes the error to a separate, short-lived record — `…/<resource>/history/`
  (last-N or TTL-pruned), **not** the now-deleted ticket — which `queue-status`
  and the pane read. A failed `release` never blocks the next holder.

**Waiting & cancellation (observable by the waiter).** A `queue-run` that hasn't
acquired yet **holds its ticket** (its FIFO place) while waiting — on an earlier
ticket *or* on a live-root block. `Ctrl-C`/`SIGINT` removes its ticket and leaves
the line (the `finally` release). Cancellation by *another* process needs a real
protocol, because merely unlinking a waiting ticket would not stop the waiter
(which still holds its open fd/flock and keeps polling): the **wait loop
re-checks its own ticket each poll**, and `queue-cancel` **writes a tombstone**
(replacing the ticket / recording a durable cancel reason in `…/history/`) that
the waiter detects on its next poll and then **exits non-zero with that reason**.
`queue-cancel` targets **waiting tickets only — a current holder cannot be
canceled this way** (aborting a running command is out of scope; the holder
releases via its own lifecycle). Head-of-line blocking is *not* a special hazard
for the live-root case: the exclusive-or rule blocks **all** worktree acquires
while a root session is live, so every waiter is stalled by the session — not by
the first ticket — and all proceed in FIFO order once it ends (the accepted
starvation tradeoff, §5). An optional `--timeout` bounds the wait before a
`queue-run` gives up and releases its ticket.

### 5. The "root is exclusive-or" policy (only `kind: root-dir`)

Root is **either** a live working session **or** the lease sandbox — never both.
This is what makes a destructive `sync` acquire safe. (Does **not** apply to
`port`/`device`/`name` resources, which are plain named leases.)

1. **Live root session present → sandbox locked out.** If a live session is
   working *in root*, `queue-run` from a worktree **blocks** (no acquire-hook
   runs); the pane shows *"root held by live session ‹name›."* Root's files stay
   untouched. (A live root session is an implicit exclusive holder.) Two details
   the earlier draft got wrong:
   - **Liveness source = the live registry** (`live.py` /
     `session-explorer-live.json`), which records each session's `cwd` + `pid`
     and is judged live by **PID liveness with a 24h TTL backstop** (`live._alive`).
     *Not* the `--gc` JSONL-flock/60s heuristic — that is a different mechanism
     for a different purpose.
   - **Canonical-root matching, not `cwd == shared-root`.** A session counts as
     "in root" when its `cwd` resolves to **this project's canonical repo root and
     is *not* inside a `.claude/worktrees/…` subtree**, computed by the shared
     canonical-root helper (§1) — which resolves a subdirectory cwd to the git
     top-level, *not* the weaker `index.project_root()` string-strip. This catches
     sessions started in a *subdirectory* of root (which exact-match would miss)
     while correctly excluding worktree sessions (the legitimate lease holders).
2. **No live root session → sandbox free.** The first worktree holder takes it;
   the acquire hook proceeds. From here root belongs to lease holders.
3. **Transition guard (two layers — git-tracked *and* ignored).** Flipping from
   "a root session existed" to sandbox mode, if root has **uncommitted git
   changes**, `queue-run` refuses ("root has uncommitted changes the sandbox
   would overwrite — stash/commit first"). But `git status` does **not** see
   ignored root-only files (`.env`, caches, certs, binaries), which `--delete`
   would still remove — so the **rsync `--dry-run` deletion refusal** (§2) is the
   second, authoritative layer: at this first transition it **captures the
   protected baseline** and refuses on any would-delete path not yet protected,
   ignored or not. Once the baseline is confirmed and the resource enters sandbox
   mode, later acquires reset freely (§2) — this guard does not re-fire. A *clean*
   root that passes both proceeds without nagging.
4. **Prevention layer (only when the project has a `root-dir` resource).** The
   new-session dialog **defaults the worktree checkbox ON** and warns if the user
   tries to create a plain root session ("this project's root is a shared
   sandbox; create a worktree instead"). A project whose resources are all
   `port`/`device`/`name` gets **no** such warning — there is no root to clobber.

**Why both prevention and enforcement:** we cannot hard-forbid root sessions — a
user can run `claude` in root entirely outside the explorer. The dialog
(prevention) makes root-working rare; the exclusive-or rule (enforcement) keeps
it *safe* when it happens anyway. Worst case is a worktree agent waiting
(visible and actionable in the pane), never data loss.

**Accepted tradeoff — starvation:** an idle-but-live root session pauses all
parallel work on that resource. This is correct: it is exactly the situation
where clobbering would lose work. The pane makes the reason obvious.

### 6. TUI — shared-resource model, access, and help

The feature's home is the **Queues pane**; configuration is a **per-project set
of resources** reached from it. Visual layout, the keymap, and the
zero-footprint tiers are specified in **§9**; this section is the behavior.

**Two scopes (the thing that confuses people):**

- **The Queues pane = a *global, read-only* view.** It lists active queues across
  *all* opted-in projects at once, each named `‹project› / ‹resource›`. It is not
  where you choose what to configure.
- **The setup dialog = *per-project*, edit scope**, keyed by repo-root path. With
  5 projects you can opt in 3 and leave 2 alone, independently.

**Access (selection-driven):** with a project (or any session under it) selected
in the tree, **`q`** opens the pane and **`s`** opens *that project's* resource
list — the dialog title names the project ("Shared resources — GymCompanion") so
it's never ambiguous. If the selection isn't under a project, `s` is unavailable
with a hint to select one. The muscle memory is **`q` → `s`**.

**Two-level setup:**

1. **Resource list** (per project): add / edit / remove resources. `a` adds one.
2. **Resource editor** (per resource): template-first form that **reflows per
   `kind`** (a `device` hides the sync knobs and forces `run_in: worktree`); the
   expanded `acquire`/`release` command is shown, with raw-shell editing behind an
   *Advanced* reveal. Includes the test panel below.

**Test panel** (where the destructive-`sync` risk is de-risked):

- **Guard-match tester** — type a command, see *queued / runs-free*. No side
  effects.
- **Acquire dry-run** — runs `sync` as `rsync --dry-run`, showing adds /
  overwrites / **deletes** (highlighted) plus the exclusive-or check. Catches a
  missing `protect` entry *before* any destructive run.
- **Health probe** — runs the `health` check so "resource up/down" shows while
  configuring.
- **Honest limitation:** dry-run is fully safe only for `sync`. A custom shell
  `acquire`/`command` is validated for parse + guard-match but cannot be
  simulated; the panel says so plainly.

**Help affordances** (the topic is dense *and* destructive, so help is
first-class):

- An in-dialog **`?`** opens concise, **offline** guidance (what `protect` does,
  what `--delete` will overwrite, isolate-first) — works on a remote box with no
  browser.
- A link to **`docs/queue-guide.md`** (Markdown in the repo, rendered on
  github.com) for the full model. Rendered with **graceful degradation**: a
  Textual click-action that opens the OS browser for mouse users, plus the plain
  URL always shown for copy — never relying on terminal-native OSC-8 hyperlinks
  (unsupported in e.g. macOS Terminal.app). The guide leads with *when NOT to use
  this* and the `--delete`/`protect` rule, then the template catalog; it is added
  to the `cutting-a-release` checklist so it can't silently diverge.

**Queues pane semantics:** live on the existing ~2s refresh loop. Per resource:
holder (+ elapsed) and the waiting line with **position ("2 of 3")**. When the
selected project has no resources, the pane shows **activation help** (how to set
up) instead of being empty. The **detection flag** is an explicitly *best-effort*
heuristic, not a guarantee: a background snapshot of the `root-dir` resource
(top-level entry set + mtimes) is compared between polls, and a change with **no
ticket held** raises a transient *"possible out-of-lease access"* toast (§9). Its
limits are stated honestly — entry/directory mtimes catch creates/deletes/renames
but **miss in-place content writes**, and warm services writing generated files
just after a lease releases can false-positive — so it is **debounced**, scoped to
the synced tree, and **excludes** known generated/served paths and the `protect`
baseline. A stronger snapshot/sentinel detector is deferred; v1 sells this as a
weak signal consistent with "bypass is visible, not impossible," never as
enforcement.

### 7. Template library (grounded in common pain)

Templates pre-fill the parameter model for documented real-world cases. Each
states **why isolation is ruled out** (else isolate instead). The editor's picker
offers these plus `Custom / blank`.

| Template | Documented pain | Isolation ruled out because | `kind` · `acquire` · `run_in` · `release` | guard / protect defaults |
|---|---|---|---|---|
| **Bind-mounted stack, well-known ports** | worktrees fight over `:3000/:5432/:8080`; stack serves off one bind-mounted path | heavy/slow boot; ports baked in; one fixed serve path | `root-dir` · `sync` · `root` · none; `health`+`wait_for` on the stack | guard `{docker compose up}`, `{docker compose run}`; protect `[/.git]` |
| **Browser e2e vs fixed-URL app** | Playwright/Cypress `reuseExistingServer`+fixed `baseURL` go flaky/`EADDRINUSE` across parallel runs | app-under-test must answer at a known URL/port | `root-dir` · `sync` · `root` · none; `wait_for` the URL | guard `{playwright test}`, `{cypress run}`; protect `[/.git]` |
| **iOS `xcodebuild`** | concurrent builds hit `build.db is locked`; keychain/signing/simulator are global singletons | shared `~/Library/Developer`, signing session, one simulator | `device`/`name` · `none` · `worktree` · *(optional build product back)* | guard `{xcodebuild test}`; no sync |
| **Single shared database** | parallel migrations "instantly wipe out the DB state the other relies on" | one DB instance on a fixed socket/port; per-agent clones not worth it | `port` · `command` (db reset) · `worktree` · none | guard migrate/test cmds; (no protect; `env` deferred) |
| **Root-only credentials / `.env`** | an "over-helpful `.env`" / root-only secrets poison or are absent in worktrees | secrets exist only at root by policy | `root-dir` · `sync` (`protect:[/.env]`) · `root` · none | guard `{<build/test exe>}`; **protect `[/.git, /.env, /.env.*]`** |
| **Single device / HIL / license seat** | can't run >1 session on one emulator/device; HIL needs an explicit device lock; paid tools have N seats | the resource is *physically* singular (capacity N) | `device`/`name` · `none` · `worktree` · none; (`capacity` deferred) | guard the device/tool cmd; no sync |

The last two rows prove the engine's generality: **resource ≠ root** — a pure
lease with `acquire: none`, `run_in: worktree` serializes a device, seat, or DB
with no file-sync and no exclusive-or-root policy at all.

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
  the current repo is opted in**. The hook receives **raw shell text**; because a
  match *denies*, parsing is **conservative and fail-open** — a false block is
  worse than a missed guard (the §6 detection flag + awareness are the backstop).
  It uses a **shell-aware lexer** (not a naive operator split) to enumerate
  simple-command segments across `&&`/`||`/`;`/`|`, stripping a leading `cd … &&`,
  `VAR=val` env-assignments, and `env`/`command`/`nohup`/`time` prefixes, then
  matches each segment's executable **basename** + leading **subcommand tokens**
  against the `{exe, sub}` rules (§2). On **anything it cannot confidently parse**
  — quoting/word-split ambiguity, command substitution `$(…)`, shell functions,
  heredocs, `bash -c "…"` bodies, `make`/`npm` targets — it **fails open (allows)**
  rather than guessing. Only a confidently-parsed matching segment → **deny +
  redirect** ("re-run as `queue-run -- …`"); the unparseable residual is the §6
  detection flag's job. Denying (rather than silently wrapping) keeps the lease
  lifecycle inside the one `queue-run` process. Fails open, never blocks startup.
- **Skill + `CLAUDE.md` snippet** for graceful cooperation — don't busy-spin,
  report queue position, understand sync-overwrite semantics, never boot a
  competing stack.

**Deferred — root-write-guard.** A stronger hook denying worktree Bash that
writes under shared-root without a lease was considered and deferred. Accepted
residual: wrapper commands (`make`/`npm run`) slip past command-matching — but
they surface as **out-of-lease flags** in the pane (§6), so bypass is *visible*,
not silent. The honest ceiling is "bypass is rare and visible," never "bypass is
impossible."

### 9. Zero-footprint TUI visual design

**Governing principle:** ~90% of users never touch this feature — they just
manage sessions. So the explorer must not "take over." The feature is
*discoverable* (one footer key, a self-explaining pane) but never intrusive, and
it uses **one consistent keymap for all users** — no mode-dependent or
case-variant bindings.

**Keymap (global, same for everyone):**

- **`q` → toggle the Queues pane.** (This reassigns quit, which today is `q`.)
- **`x` → Exit.** (`x` is currently unbound; verified against `tui.py` BINDINGS.)
- **`s` / `a` / `e`** for set-up / add / edit, and **`Del` (with a confirmation
  dialog)** for remove, are **pane-local** — live only while the resource list is
  focused, so they never appear in the main footer. Crucially **`x` is *only*
  global Exit, never a pane action**: an earlier draft double-bound `x` to remove,
  an accidental-destruction trap in the resource list — that is removed, and
  resource removal is the confirmed `Del`. The only global addition is `q`.

This is a deliberate trade: a `q Queue` entry shows in *everyone's* footer and
quit moves to `x` for everyone. We accept it for consistency + discoverability;
the pane merely explains itself and is one keypress to dismiss — it does not take
over the tree.

**New-session dialog — auto-slug, no opt-in checkbox.** Opt-in now happens via
`q`→`s` (resource list), so the dialog has **no "use queue" checkbox**. It keeps
the existing fields and adds two behaviors: (a) checking *Create git worktree*
**auto-fills the worktree name** with a slug of the session name (lowercase;
spaces/underscores/dots→`-`; drop non-`[a-z0-9-]`; collapse/trim dashes; derived
from the display portion after the last `/`; blank name → blank; a manual edit to
the field stops auto-sync); (b) the worktree checkbox **defaults ON and warns on
plain-root** only when the project has a `root-dir` resource (§5.4).

```
┌─ New session ───────────────────────────────┐     ┌─ New session ───────────────────────────────┐
│ Name  [Sprint 14 Auth_______]               │     │ Name  [Sprint 14 Auth_______]               │
│ Cwd   [/Volumes/Projects/Gym ]              │ ──► │ Cwd   [/Volumes/Projects/Gym ]              │
│ [ ] Create git worktree (-w)                │     │ [x] Create git worktree (-w)                │
│     worktree name (disabled)                │     │     worktree name [sprint-14-auth]          │ ← auto-slug
└─────────────────────────────────────────────┘     └─────────────────────────────────────────────┘
```

**Access flow `q` → `s` → `a`** (standing on GymCompanion):

```
1) q  → Queues pane (idle, project not configured)        2) s → resource list      3) a → resource editor
┌─ Queues ─────────────────────────────────┐   ┌─ Shared resources — GymCompanion ───┐   ┌─ Add resource — GymCompanion ───────┐
│ (no active queues)                       │   │  Resource  Kind     Guard    Run in │   │ Template:[iOS simulator ▾] kind:device│
│                                          │   │  ─────────────────────────────────  │   │ Name:   ios-sim                      │
│ GymCompanion — not using shared resources│   │  (none yet)                         │   │ Guard:  xcodebuild | xcrun simctl    │
│   [s] Set up   guide: …/queue-guide.md   │   │  [a] Add  [e] Edit  [Del] Remove [?]│   │ Run in: ( )root (•)worktree  (sync   │
└───────────────────────────────────────────┘   └──────────────────────────────────────┘   │         knobs hidden)  Health: …      │
                                                                                            │ ┌Test: `xcodebuild test`→QUEUE ─────┐ │
                                                                                            │ [Save] [Cancel]                       │
                                                                                            └────────────────────────────────────────┘
```

**Pane — active, with multiple resources of mixed kinds:**

```
┌─ Queues ─────────────────────────────────────────────────────┐
│ GymCompanion / root      ● holder: feat-auth (0:42)          │
│                          waiting: bugfix (1 of 2) · ui (2 of 2)
│ GymCompanion / ios-sim   ● holder: feat-ui (0:08)            │
│ GymCompanion / db        ○ free                              │
│ OtherProj   / root       ⛔ held by live session ‹main›       │
└──────────────────────────────────────────────────────────────┘
```

**Blocked / refusal states:** the `⛔ held by live session ‹name›` row above; and
the transition-guard refusal surfaces in the editor's test panel and at
`queue-run` time as *"root has uncommitted changes — stash/commit first."*

**Detection toast** (pane may be closed): the best-effort detector (§6) raises a
transient `notify()` — *"⚠ possible out-of-lease access on GymCompanion/root"* —
plus a subtle marker visible if the pane is open. No persistent banner; a weak
signal, not a block.

**Persistent pane visibility (global, but content-gated).** `q` is a sticky
toggle persisted as a single global boolean
(`~/.claude/session-explorer-ui.json` → `{"queue_pane_visible": true}`) — global,
because the pane is a cross-project view, not per-project. To avoid a stray empty
pane, **rendering is content-gated**: with the flag on, the pane takes space only
when there is something to show — ≥1 active queue anywhere, or the
currently-selected project has configured resources. Pressing `q` with nothing
configured/active shows a one-line activation hint for *this* session (so the
keypress isn't a no-op), but a persisted `true` with no content renders **nothing**
on the next launch — so a curious never-opted user who toggled it once never
inherits a permanent empty pane.

**Open implementation details:** exact Textual link widget (click-action +
copyable URL); pane height **content-sized** to the number of active queues
(not a fixed 20%); the test panel as a `[Test ▸]` toggle to keep the editor
compact. Pane toggle stays `q`; no case-variant collisions introduced.

## Build order (independently shippable)

1. **Queue core + CLI** (`queue-run`/`queue-status`/`queue-cancel`) + the `kind`
   model + `sync`/`none`/`command` strategies (with `protect`) +
   `health`/`wait_for` — usable immediately with only `CLAUDE.md` guidance.
2. **TUI** — Queues pane + `q`/`x` keymap + two-level per-project setup/test
   dialog + new-session auto-slug + detection toast + **`docs/queue-guide.md`**
   (added to the release checklist).
3. **Awareness / enforcement** — `SessionStart` injection + command-guard hook +
   skill.

Each is its own spec → plan → implementation cycle.

## Constraints honored

- Minimal deps; no new runtime dependency (`rsync`/`flock` are system tools;
  Textual stays vendored).
- `flock` + temp-file-rename for every queue write (matches existing stores).
- Hooks fail open and never block startup.
- Opt-in, like retention — nothing activates until a project has ≥1 resource.
- Zero footprint for non-users beyond the single `q` key and the self-explaining
  pane; per-user config in `~/.claude/`; the repo is never touched.
- Don't move or rewrite native JSONLs; the queue is orthogonal to them.

## Open items deferred

- **Parameters:** `ensure` (auto-start), `reload`, `env` injection, `capacity`>1
  — schema-reserved; implement when a real template needs them.
- **Root-write-guard hook** (§8) — only if command-guard + detection proves
  insufficient in practice.
- Whether `queue-status` should expose per-resource history/metrics beyond the
  current snapshot (not needed for v1).
- `SPEC.md` is the authoritative source for this project; update it in the same
  change set when implementing, rather than letting code and spec diverge.

## Sources (grounding for §7)

- Penligent — *Git Worktrees Need Runtime Isolation for Parallel AI Agent
  Development*; GPT Frontier — *Preventing Database and Port Collisions with
  Concurrent AI Agents*; worktree-compose (isolate-first control plane).
- Playwright docs — *Web server* / *Parallelism* (`reuseExistingServer`, fixed
  port flakiness).
- *Fixing "build.db is locked" in Xcode* + *Parallel xcodebuild on Cloud Mac CI*
  (DerivedData/signing/simulator singletons).
- DeFlaky — *Parallel Test Execution Causing Flaky Tests* (shared-DB races).
- Appium java-client #1451 (single-device parallel limits); Concourse CI —
  *Hardware in the loop testing* (device locking/queuing).
