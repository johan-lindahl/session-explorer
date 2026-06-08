# Overlay-and-restore test-queue resource — design

**Date:** 2026-06-08
**Status:** Design (approved for spec review)
**Supersedes for this use case:** the `bind-mounted-stack` template applied to a
shared installed app root.

## Problem

The shared-resource queue (Phase 1–3) reported `royal-magento-docker` as **free**
while a worktree session (44042) was actively mutating the shared parent root
`/Volumes/Projects/RoyalUnibrew/magento-os` — running an "overlay": `cp` its
changed files into the installed root, run `phpunit`, then `git restore`.

Investigation (against live state) found the queue was **accurate, not broken** —
nobody held a lease — and identified three compounding causes:

1. **The session never called `queue-run`.** Tickets are created only by
   `queue-run`; there is no auto-acquire. No ticket → `holder()` is `None` →
   "free". (Confirmed: the queue dir held only `.lock`, zero ticket files.)
2. **The advisory `PreToolUse` guard could not catch the overlay.** The resource's
   guard list was only `docker compose up` / `docker compose run`; the overlay
   used `cp` + `phpunit`. Per SPEC the guard is advisory/fail-open — a missed
   guard is preferred over a false deny.
3. **The resource was misconfigured.** It is the `bind-mounted-stack` template
   verbatim (`kind=root-dir, acquire=sync, run_in=root`, docker-compose guards).
   That template models a different workflow ("`docker compose up` bind-mounts
   cwd, so rsync the worktree into root first"). The actual coordinated operation
   — phpunit overlays into the installed root — is neither synced for nor guarded.

### Root-cause framing

Only the **parent root** is a fully installed Magento app (`vendor/`,
`generated/`, DI compilation, env, DB config). Worktrees are bare code checkouts,
so tests must run **in the root**. Each worktree session overlays its changed
files into root, runs the tool, and `git restore`s. The shared state is the
**installed app root**; the collision is two overlays (or one session's
`git restore`) clobbering each other. Mutual exclusion on *borrowing the installed
root* is the need.

The reason sessions bypass the queue: **the existing "safe" path is heavier than
the bypass.** The `root-dir` model's safe acquire is `rsync -a --delete` of the
*entire* worktree into root — slow and scary for a Magento tree. Sessions
rationally hand-roll the lighter `cp`-a-few-files-and-`git restore` overlay
instead, outside the queue.

`cp into parent && phpunit` is **fundamentally unguardable** — phpunit runs
legitimately in worktrees too, and a `cp` into the parent is indistinguishable
from any other `cp`. So no resource model makes this reliably *enforceable*. The
lever is making the **safe path easy** (lighter than the bypass) plus awareness —
not a better guard regex.

## Goal

Add the missing "shared installed app root, overlay-and-restore" pattern to the
queue system as a first-class, correctly-modeled resource, so that:

- the **safe path** (a serialized overlay via `queue-run`) is **as light as** the
  hand-rolled bypass — copy only changed files, restore exactly those;
- the restore is **crash-safe** (runs even on test failure / SIGKILL), unlike the
  hand-rolled `git restore` that leaks on failure;
- the guard becomes **meaningful** because the safe path is now the easy path;
- the whole queue subsystem is clearly labeled **experimental** so users do not
  rely on it for safety.

### Non-goals

- Making the overlay enforceable against an uncoordinated `cp`. It stays
  advisory/fail-open per SPEC. We make the safe path easy and nudge; we do not
  claim prevention.
- Per-worktree fully-installed Magento apps (a Magento-infra change outside this
  tool's control). We coordinate the user's existing overlay workflow.
- A first-class `acquire=overlay` kind with its own editor fields, dry-run
  preview, and conflict UI (considered and rejected as over-engineered — see
  "Approaches considered").

## Approaches considered

- **A — Template + sharper awareness (advisory only).** New correct mutex
  template + sharpened awareness text. Cheapest, but the session still hand-rolls
  cp/restore; the safe path is not made easier, so behavior likely will not
  change.
- **B — Overlay becomes the safe path (template + shipped helper). [chosen]**
  Mutex template wired to a packaged overlay helper; `queue-run -- phpunit` does
  the borrow + engine-guaranteed restore. Reuses existing
  `acquire=command`/`release=command` machinery; minimal core change.
- **C — First-class `acquire=overlay` primitive.** Most robust, most code,
  rejected as over-engineered relative to B.

## Design

### 1. The resource model — new template

Add a PHP/Magento-flavored template to `QUEUE_TEMPLATES` (`bin/_pkg/tui.py`):

```
key:    overlay-installed-root
title:  "Shared installed app root (overlay tests)"
defaults:
  kind:    root-dir          # FIFO mutex + live-root exclusive-or, NO rsync
  acquire: command           # -> overlay-in helper (see §2)
  release: command           # -> overlay-out helper, run in engine's finally
  run_in:  root              # the command executes in the installed root
  command_acquire: "session-explorer queue-overlay in"
  command_release: "session-explorer queue-overlay out"
  release_required: false
  guard:
    - {exe: phpunit, sub: []}
    - {exe: phpstan, sub: []}
    - {exe: magento, sub: [setup:di:compile]}    # basename of bin/magento
    - {exe: magento, sub: [setup:upgrade]}
  # Deliberately NOT phpcs / php-cs-fixer — worktree-safe static checks must not
  # be serialized through the root mutex.
```

`guard_match` compares `os.path.basename(seg[0])`, so the exe is `magento`
(matches both `bin/magento` and an absolute path). Known blind spot, consistent
with the documented make/npm case: `php bin/magento …` lexes with `seg[0]=php`
and is not caught — mitigated by the awareness injection, not the guard.

Notes:
- `kind=root-dir` keeps the existing FIFO mutex and the live-root exclusive-or
  (`queue_run.py:212` `_wait_for_root_free`), but **without** `acquire=sync`, so
  the rsync-source check (`queue_run.py:162`) and the destructive `--delete` path
  are not used.
- The overlay mechanism is **command-agnostic**: the helper copies files and
  restores; the command between (`phpunit`, `phpstan`, `bin/magento …`, a
  `composer` script, anything) is opaque to it. PHP-ness lives **only** in the
  curated guard list.
- The guard list is **project-curated**, not a hardcoded PHP grab-bag.
  `phpcs`/`php-cs-fixer` are excluded because they run fine in a bare worktree and
  must not be funneled through a mutex they do not need. The guide explains which
  tools need the root and why phpcs is excluded.

### 2. The overlay helper (new code)

A packaged subcommand pair, `session-explorer queue-overlay in` and
`queue-overlay out`, wired as the template's `command_acquire`/`command_release`.
It is a shipped, tested helper — **not** user-typed shell — because the restore
must be exact and crash-safe.

Lives in a new pure-logic module (e.g. `bin/_pkg/overlay.py`) with a thin CLI
subcommand in `cli.py`, mirroring the other queue modules (Textual-free,
unit-tested).

**Contract (env provided by the engine — see §3):**
- `SE_QUEUE_WORKTREE` — the holder's worktree (the overlay source).
- `SE_QUEUE_ROOT` — the installed app root (the overlay target; also cwd).
- `SE_QUEUE_STATE_DIR` — the resource's queue dir; where the manifest is written.

**`queue-overlay in` (acquire, cwd = root):**
1. Refuse if root is dirty on paths to be overlaid — reuse
   `exclusive.transition_guard(root)` so a restore can never wipe real work in
   root. (Refusal → engine treats acquire as failed; nothing to release.)
2. Compute the **changed path set**: the worktree's working-tree state that
   differs from root's checked-out commit —
   `git -C $WT diff --name-only <root-HEAD>` — which captures both the branch's
   committed changes and any uncommitted edits in one pass.
3. For each path, copy the worktree's version into root. Classify each as
   **modified** (existed in root) or **added** (new in root).
4. Write a **manifest** to `$SE_QUEUE_STATE_DIR/overlay-<sid>.manifest` recording
   each path and its class.

**`queue-overlay out` (release, cwd = root):**
1. Read the manifest. For **modified** paths: `git -C $ROOT checkout -- <path>`.
   For **added** paths: `rm <path>`.
2. Delete the manifest.
3. The engine already calls release in a `finally` (`queue_run.py` release hook),
   so this runs on normal completion, test failure, readiness refusal, or
   SIGINT/SIGTERM — strictly better than the hand-rolled `git restore`.

### 3. The one engine change

Expose the worktree, root, and state dir to the `command` acquire/release hooks
as environment variables, so the helper can find its source and manifest
location:

```python
# queue_run.py — _do_acquire / _do_release "command" branches
env = {"SE_QUEUE_WORKTREE": src, "SE_QUEUE_ROOT": root, "SE_QUEUE_STATE_DIR": qdir}
_run_shell(cmd, cwd=root, env=..., timeout=...)
```

- `_do_release` currently does not receive `qdir` — add it to the signature and
  call site.
- `_run_shell` gains an `env` parameter (merged onto `os.environ`, not
  replacing).
- Additive and backward-compatible: existing `command_acquire` users simply gain
  three unused env vars.

### 4. Guard + awareness

- The curated guard list (§1) makes the `PreToolUse` hook (`queue-guard`) deny a
  bare `phpunit`/`phpstan`/guarded `bin/magento` and redirect it to
  `queue-run -- <cmd>`. Now meaningful because the safe path is lighter than the
  manual overlay.
- Sharpen the SessionStart awareness text (`queue_awareness.py`) for projects with
  an overlay resource: explicitly state that tests/QA needing the installed root
  must run via `queue-run -- <cmd>`, and that sessions should **not** hand-roll
  `cp`/`git restore`. This is the **primary lever**; the guard is the backstop.
- Decision text and guard matching remain single-sourced in `queue_awareness.py`
  (reusing `guard_match`), per the Phase-3 contract.

### 5. Safety & honest limitations

- **Dirty root:** reuse `exclusive.transition_guard(root)` to refuse the overlay
  when root has uncommitted changes to overlaid paths.
- **Generated artifacts** (`generated/`, `var/` written by di:compile / phpunit)
  are **not** overlaid and **not** restored — out of scope; the user's existing
  reality.
- **Deleted files** (worktree removed a file) are **not** propagated in v1 —
  copy-in / add only. Documented limitation.
- **Still bypassable:** `cp into parent` outside `queue-run` remains invisible.
  Advisory/fail-open per SPEC. No safety guarantee.

### 6. Experimental labeling (whole queue subsystem)

Make it unmistakable that the queue system is experimental and must not be relied
on for correctness — it coordinates cooperatively but cannot *prevent* an
uncoordinated write. A single shared phrasing is threaded through every surface so
the wording cannot drift: one constant in code (reused by pane + dialogs + help),
the same sentence echoed in the docs.

**TUI:**
- **Queues pane header** → `Queues — experimental` (every time the pane is open).
- **Resource setup screens** (`ResourceListScreen` / `ResourceEditorScreen`) → a
  one-line top banner: *"Experimental. Cooperative only — it cannot stop an
  uncoordinated process from touching the resource. Don't rely on it for safety."*
- **First-time activation hint** (the existing one-line hint shown when the pane
  is opened with nothing to show) → carries the experimental note, so the very
  first encounter says it.
- **Help screen** (`QueueHelpScreen`) → leads with the experimental caveat.

**Documentation:**
- **README** → an `> ⚠️ Experimental` callout on the queue section.
- **SPEC.md** → the "Shared-resource lease engine" heading flagged experimental,
  with the load-bearing note that advisory/fail-open means **no safety
  guarantee**.
- **docs/queue-guide.md** → a banner at the very top.
- **CHANGELOG** → the entry names it experimental.

### 7. Testing & docs

- Pure-logic unit tests for `overlay.py`: manifest build, modified-vs-added
  classification, restore behavior (checkout for modified, rm for added), and the
  dirty-root refusal. No Textual, like the other queue modules.
- Engine test: `command` acquire/release receive `SE_QUEUE_WORKTREE` /
  `SE_QUEUE_ROOT` / `SE_QUEUE_STATE_DIR`.
- Template + guard-seed test in the `tui.py` template suite (verifies the curated
  guard list, including the deliberate phpcs exclusion).
- `docs/queue-guide.md` gains an overlay section (already on the release checklist
  so it cannot diverge); `SPEC.md` "Shared-resource lease engine" updated in the
  same change.
- Version bump + GitHub release per the `cutting-a-release` skill (minor — new
  feature).

## Files touched (anticipated)

- `bin/_pkg/overlay.py` — **new** pure-logic overlay helper.
- `bin/_pkg/cli.py` — `queue-overlay` subcommand.
- `bin/_pkg/queue_run.py` — env exposure in `_do_acquire`/`_do_release`;
  `_run_shell` `env` param; `_do_release` gains `qdir`.
- `bin/_pkg/tui.py` — new template; Queues pane header label; setup-screen banner;
  activation-hint note; shared experimental-phrase constant.
- `bin/_pkg/queue_awareness.py` — sharpened awareness text for overlay resources.
- `README.md`, `SPEC.md`, `docs/queue-guide.md`, `CHANGELOG.md` — experimental
  labeling + overlay docs.
- `test/` — unit tests per §7.
- Version bump: `bin/_pkg/__init__.py`, `.claude-plugin/plugin.json`.
