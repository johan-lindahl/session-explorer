# Leased ground — location-enforced shared root

**Date:** 2026-06-10
**Status:** Design approved; pending spec review → implementation plan.
**Supersedes:** the advisory command-guard model of
`2026-06-05-shared-root-test-queue-design.md` §8 (awareness + `{exe, sub}`
command matching, fail-open) and the instruction-hardening of
`2026-06-09-lease-root-instruction-design.md`. The queue core, overlay helper,
and exclusive-or policy from those specs are **kept**; only the
awareness/enforcement layer and the configuration surface change.

## Problem

The shared-resource queue bet on **cooperation**: awareness text injected at
SessionStart, an advisory fail-open `PreToolUse` guard keyed on declared
commands, and refusal messages. Field evidence across three incidents shows
the bet failing in one consistent shape:

1. A worktree session hand-rolled a `cp`-overlay + `git restore` into the
   shared installed root with no lease; the queue truthfully showed "free"
   (2026-06-08 spec).
2. A session **read the awareness contract, narrated it back, and then
   rationalized a "host-side prep" exception** — `cp *.sample` + `npm install`
   directly at the root, no lease (2026-06-09 spec).
3. After instruction hardening (v1.16.4), bypasses continue. Each bypass is
   simply a command nobody thought to declare in a guard list (`cp`,
   `npm install`, `php bin/magento …`), because the guard keys on **command
   identity** while the real invariant is **location**.

The damage is then caught late by the dirty-root `transition_guard`, which
blocks *subsequent* legitimate work and presents confusing multi-tenant state.

The second, compounding failure is **operational complexity**: five resource
kinds, a template library, `{exe, sub}` guard vocabularies, classification
gates, and an mtime-based detection heuristic — almost all of it existing to
prop up advisory enforcement (every escape needs a new template, a new guard
rule, a new paragraph of contract text). The only workload in real use is
**one shared installed app root** (the Magento overlay-test case).

### Constraints that unlock the fix

- Every dangerous writer is a **Claude Code session** whose hooks and
  permissions this plugin controls. The user's own hand access (terminal,
  editor) needs no restriction.
- Claude agents can only touch the filesystem **through tools**, and
  `PreToolUse` hooks can **deny** a tool call with a reason the agent reads.

So enforcement does not have to be cooperative. The advisory model was a
choice, not a necessity.

## Goal

Replace "asked nicely, fail-open" with a single enforced invariant:

> **For a Claude session in a worktree, the shared root is unreachable through
> tools except via `session-explorer queue-*`.**

There is no contract to read and nothing to rationalize: the bypass *action
itself* fails, and the deny reason contains the exact `queue-run` rewrite.
Then delete the complexity that existed only to compensate for advisory
enforcement.

### Non-goals

- Stopping non-Claude writers (other processes, scripts run outside Claude,
  the user by hand). The dirty-root `transition_guard` remains the backstop
  for those, exactly as today. The subsystem stays labeled experimental; the
  claim upgrades from "advisory, cooperate actively" to **"enforced for
  Claude tool calls, advisory beyond them."**
- A broker/executor daemon. Considered (agents submit jobs to a root-owning
  executor) and rejected: it reintroduces a daemon into a deliberately
  daemon-less crash-safe design, needs result plumbing, and its only real
  advantage — agents physically can't do it wrong — still requires this same
  deny hook to stop agents bypassing the broker. The deny hook alone delivers
  the benefit.
- Per-worktree installed roots (dissolving the singleton via hardlink clones).
  Cleanest end state but Magento-infra work outside this tool's scope; noted
  as a possible future complement, not part of this design.
- Removing the queue engine. The flock FIFO core, `queue-run` lifecycle,
  overlay helper, and exclusive-or policy work and are crash-safe; they remain
  the one door through the wall this design builds.

## Design

### 1. The deny hook — location, not command identity

The existing `PreToolUse` wiring is reused: `hooks/pre-tool-use.sh` stays a
thin pipe into the CLI's `queue-guard` subcommand (script unchanged); the
subcommand's **internals** are replaced. The hook **matcher widens** from
`Bash` to `Bash|Edit|Write|NotebookEdit` — in `.claude-plugin/plugin.json`
**and** `install.sh`, with teardown in `uninstall.py`
(`_HOOK_MARKERS`/`_HOOK_EVENTS`), the three kept in sync per the Phase-3
contract.

Decision logic lives in a new pure module **`bin/_pkg/root_guard.py`**
(Textual-free, no argparse, unit-tested), replacing
`queue_awareness.guard_reason` + `guard_match` on the hook path:

```
root_guard.decide(payload: dict, config_path: str, live_path: str)
    -> str | None        # deny reason, or None = allow
```

**Resolution.** From payload `cwd`, resolve the project via
`project_id.project_id`; load the queue config; find the project's `root-dir`
resource → shared root `R`. No `root-dir` resource → `None` (silent allow).
`R` is held as a small set of **path aliases**: the configured path, its
`os.path.realpath`, and the macOS `/private`-prefixed variant, each
normalized without trailing slash. Comparison is by path-prefix on resolved
paths (for structured file paths) and by substring of any alias (for Bash
text). Case-sensitivity follows the filesystem as stored; a case-twiddled
path on a case-insensitive APFS volume is an accepted miss (backstopped per
§5).

**Session location — registry first, call cwd second.** A tool call's `cwd`
alone cannot distinguish "session legitimately started in root" from
"worktree session that `cd`-drifted into root". The payload's `session_id` is
looked up in the live registry (`live.py` / `session-explorer-live.json`,
which records each session's starting cwd):

- Registered cwd inside `R` and **not** under `R/.claude/worktrees/` →
  **root session** → allow everything (it is the implicit exclusive holder;
  the existing exclusive-or already blocks worktree leases while it lives).
- Registered cwd in a worktree (under `R/.claude/worktrees/` or an external
  `git worktree add` tree that collapses to this project) → **worktree
  session** → the deny rules below apply. If the *tool call's* `cwd` has
  drifted inside `R`, that alone is a **deny** ("you have cd'd into the
  shared root; work in your worktree") — closing the
  cd-then-relative-paths hole.
- Session absent from the registry (hook raced registration, foreign
  launcher) → fall back to classifying the payload `cwd` by the same rule.
  Weaker, accepted.

**Edit / Write / NotebookEdit.** Resolve `file_path` (or `notebook_path`).
If it lies under `R` and outside `R/.claude/worktrees/` → **deny**:
*"This file is in the shared installed root. Edit the copy in your worktree;
your changes reach the root via queue-run's overlay."* Structured input, zero
parsing ambiguity.

**Bash — mention = deny.** For a worktree session, deny when the command text:

- contains any alias of `R` as a substring; **or**
- climbs with `../..` (two-plus parent steps) when the session's worktree
  lives under `R/.claude/worktrees/` (three levels below `R`, so `../..`
  already reaches shared ground at `R/.claude`). External worktrees get only
  the alias rule — climbing from them does not lexically reach `R`.

There is **no "confident parse" requirement** — the old guard denied only
what it could confidently match and allowed everything else; this guard
denies on mention and accepts false positives, because a worktree session has
*no* legitimate raw root-touching Bash, ever: a lease only exists inside a
`queue-run` process. The rule is stateless — no "is a lease currently held"
lookup.

**The single allowlist.** A command is allowed despite mentioning `R` iff it
parses (shlex) as **one simple command** — no `&&`/`||`/`;`/`|`/newline/
redirect/substitution — whose executable basename is `session-explorer` and
whose first argument starts with `queue-`. This keeps
`session-explorer queue-run --resource r -- cp x <R>/app/etc/` runnable while
`echo queue-run && cp x <R>/…` still denies (compound → never allowlisted).
Env-assignment prefixes (`FOO=1 session-explorer …`) are tolerated, mirroring
the existing lexer's prefix handling.

**Deny reason** (the agent's recovery path, one step):

> `<path>` is the shared installed root — it is leased ground, unreachable
> outside a lease. Re-run the ENTIRE command through the queue:
>
> `session-explorer queue-run --resource <rid> -- bash -c '<original command>'`
>
> To merely inspect root files, use the Read tool (reads are not blocked).
> `session-explorer queue-status` shows who holds the lease.

Emitted as the standard `hookSpecificOutput` → `permissionDecision: "deny"` +
`permissionDecisionReason` JSON, as the current guard does.

**Plumbing fails open, semantics fail closed.** If the CLI is missing, the
payload is unparseable JSON, or `root_guard` raises, the hook emits nothing
and exits 0 — a broken hook must not brick every Bash call on the machine.
But within working plumbing the default for a root mention is **deny**. This
inverts the old guard's posture (which required confident parsing to deny)
while keeping the operational safety of fail-open infrastructure.

### 2. What stays — the door

Unchanged because it works and is crash-safe:

- the flock FIFO ticket core (`queue_store.py`) and `queue-run` lifecycle
  (`queue_run.py`), including signal handling and `finally`-release;
- the overlay helper (`overlay.py`, `queue-overlay in`/`out`) — the light
  borrow-and-restore path the deny message funnels agents into;
- the live-root exclusive-or (`exclusive.py`), including the multi-tenant
  dirty-root refusal wording from v1.16.4;
- `queue-status` / `queue-cancel`;
- the Queues pane as a status view.

### 3. Deletions and collapses — the simplification

- **`guard_match.py` and the `guard` config field — removed from the live
  path.** Location replaces command identity. The config loader tolerates and
  ignores a `guard` field in existing files (no migration); the resource
  editor stops offering guard lists; `guard_match.py` is deleted along with
  its tests once nothing imports it.
- **`queue_detect.py` (mtime-heuristic "possible out-of-lease access" toast)
  — deleted**, with its pane/toast wiring in `tui.py`. The deny hook replaces
  detection for the only writers the heuristic could plausibly catch; the
  user's own hand edits don't need toasts.
- **The generic resource editor + template library collapse** to one
  per-project dialog: **"Shared installed root"** — root path (auto-derived
  from the canonical helper, editable), protect list, and on/off. Saving
  applies the `overlay-installed-root` shape (`kind=root-dir`,
  `acquire/release=command` → `queue-overlay in`/`out`) implicitly. The
  `kind` machinery and `sync` strategy stay in the schema and engine
  (back-compat for any existing configured resource; design room), but stop
  being a UI surface. `q → s` opens this dialog directly.
- **Awareness text shrinks** (`queue_awareness._render_context`) from the
  ~15-line cooperation contract to a ~3-line usage hint:

  > This project's installed root at `<path>` is shared across worktrees and
  > write-blocked outside a lease. Run anything that needs it (tests, builds,
  > installs) as: `session-explorer queue-run --resource <rid> -- <cmd>` —
  > it overlays your changed files into the root, runs, and restores.
  > `session-explorer queue-status` shows current holder/queue.

  It is a hint about a wall, not a plea for cooperation. The
  `session_context` entry point and SessionStart wiring are unchanged.

### 4. Agent workflow after this change

1. Agent edits freely in its worktree. Any tool-level attempt to touch `R`
   is denied with the rewrite instruction.
2. Agent runs `session-explorer queue-run --resource <rid> -- <cmd>`:
   ticket → FIFO wait → exclusive-or check → overlay-in → run in root →
   overlay-out (engine `finally`) → release.
3. The user works in root by hand whenever they like; a live Claude root
   session blocks worktree leases via the existing exclusive-or.

### 5. Honest limits (recorded in SPEC.md)

- A Bash command that **computes** the root path at runtime (`$TARGET`,
  `$(git rev-parse …)`, a script that cd's internally) slips past lexical
  matching. Backstop unchanged: the next overlay acquire refuses the dirty
  root (`transition_guard`) with the multi-tenant message. The difference
  from today is that this is now the rare exception path, not the main
  highway.
- Non-Claude writers are out of scope by definition.
- Bash **reads** of root files (`cat <R>/file`) are false-positive denied;
  the deny reason points at the Read tool. Accepted cost of mention = deny.
- The "experimental" labeling shipped in v1.16.0 stays; only the claim text
  upgrades to "enforced for Claude tool calls, advisory beyond them."

## Files touched (anticipated)

- `bin/_pkg/root_guard.py` — **new**: pure decision logic (§1).
- `bin/_pkg/cli.py` — `queue-guard` subcommand internals switch to
  `root_guard.decide`; handles the widened tool set.
- `.claude-plugin/plugin.json`, `install.sh`, `bin/_pkg/uninstall.py` —
  `PreToolUse` matcher `Bash` → `Bash|Edit|Write|NotebookEdit`, kept in sync.
- `bin/_pkg/queue_awareness.py` — context text shrinks (§3);
  `guard_reason` and `_redirect_command` removed.
- `bin/_pkg/guard_match.py`, `bin/_pkg/queue_detect.py` — deleted (+ their
  TUI wiring and tests).
- `bin/_pkg/tui.py` — resource editor collapses to the "Shared installed
  root" dialog; detection toast removed; template list retired from the UI.
- `bin/_pkg/queue_config.py` — `guard` tolerated-but-ignored; validation no
  longer requires it anywhere.
- `SPEC.md` — §8 rewritten (enforcement model), §6 detection flag removed,
  experimental claim updated. `README.md`, `docs/queue-guide.md`,
  `CHANGELOG.md` updated to match.
- `test/` — see Testing.
- Version: minor bump (v1.17.0) via the `cutting-a-release` skill.

## Testing

- **`test_root_guard.py` (the dense suite):** root- vs worktree-session
  classification from the live registry; registry-miss fallback; cd-drift
  deny; Edit/Write/NotebookEdit path resolution incl. `R/.claude/worktrees/`
  carve-out and symlink//private aliases; Bash mention-deny across alias
  forms; `../..` climb rule (managed worktree only); the
  single-simple-command allowlist incl. bypass attempts
  (`echo queue-run && …`, `; session-explorer queue-run`, env prefixes);
  fail-open on garbage payloads / missing config / unresolvable project.
- **Engine/CLI:** `queue-guard` subprocess tests in `test_cli.py` updated for
  the new payload shapes (Edit/Write payloads, deny JSON).
- **TUI:** collapsed setup dialog round-trips a resource with the overlay
  shape; detection-toast tests removed.
- **bats (`test/hook.bats`):** matcher registration for the widened tool set
  on both install paths; hook stays silent with no config.
- Full suites: `python3 -m pytest test/ -q` and the bats files.
