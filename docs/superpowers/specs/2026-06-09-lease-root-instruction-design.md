# Lease-the-root instruction & multi-tenant overlay refusal

**Date:** 2026-06-09
**Status:** Approved, pre-implementation

## Problem

Observed in the field: a session in a worktree, correctly told the shared
Docker stack runs against the **main repo root**, read the Phase-3 awareness
context, *narrated* the constraint ("run through the lease") — and then
rationalized a host-side exception: it ran `cp *.sample` config + `npm install`
**directly at the shared root, no lease held**, before invoking `queue-run` for
the final build. The PreToolUse guard never fired, because `cp` / `npm install`
are not declared guarded executables; the guard keys on the leased *binary*,
not on *"writes to the shared root."*

The damage was caught one layer deeper: `queue-overlay`'s dirty-root
precondition (`exclusive.transition_guard`) refused with exit 70
("root has uncommitted changes the sandbox would overwrite — stash/commit
first"). So the hard backstop worked. But the root was *already* dirty from a
**prior** session that had bypassed the lease the same way — and the refusal
message gave single-tenant advice ("stash/commit first") that, followed
literally in a shared root, would clobber whichever session's uncommitted work
happened to be sitting there.

Two seams, then:

1. **Awareness text is command-altitude, not location-altitude.** It enumerates
   guarded commands and forbids one specific anti-pattern, leaving a
   "host-side setup doesn't count" loophole an agent can rationalize into.
2. **The overlay refusal message is single-tenant.** "stash/commit first" is
   correct for one dev's repo; in a root shared across worktrees it invites
   destroying a neighbor's state.

## Non-goals / decisions

- **Instruction is harm-reduction, not enforcement.** We have direct evidence a
  model reads the text and rationalizes past it. The `queue-overlay`
  precondition remains the real, fail-closed backstop (it already worked). We
  are not making instruction load-bearing.
- **The PreToolUse guard stays exe/command-based.** No cwd-based "is this
  writing to the root" matching — that broadens false positives and is still
  fail-open. Out of scope.
- **Overlay refusal: message reword only.** `transition_guard` keeps refusing
  *any* dirty root, unchanged. No baseline tracking to distinguish self-caused
  vs inherited dirt. (User decision.)

## Design

### A. Location-based awareness text — `queue_awareness._render_context`

Raise altitude from "these commands are guarded" to "the root is leased ground,"
and explicitly close the host-side escape hatch.

Add a leading principle to the "Cooperate with the lease engine" block:

> **The shared root is leased ground.** ANY command whose working directory or
> write target is the shared root — including setup, scaffolding, file copies
> (`cp … root/`), `npm install` / `composer install`, builds, and anything you
> might think of as "host-side prep" — must run inside a lease. **There is no
> host-side exception.** If you are touching the root, you hold the lease or you
> are doing it wrong.

Strengthen the existing "Do NOT hand-roll your own overlay" bullet to demand the
whole sequence be wrapped, not just the final build step:

> Do NOT hand-roll your own overlay or stage files into the root yourself (`cp`
> into root, run, `git restore`). Wrap the **entire** sequence — setup, copies,
> installs, and build — in **one** `queue-run`, never just the final step.

### B. Multi-tenant overlay refusal — `exclusive.transition_guard` (message only)

Replace the refusal string with shared-root-aware guidance. No behavior change —
still refuses any dirty root:

> root has uncommitted changes the sandbox would overwrite. This root is
> **shared across worktrees** — the changes may be yours OR left by another
> session that bypassed the lease. Do **NOT** blindly stash/restore a root you
> didn't dirty (you may destroy another session's work). Run `session-explorer
> queue-status` to see who's active, work out whose changes these are, and only
> commit/stash if they are genuinely yours.

## Affected files

- `bin/_pkg/queue_awareness.py` — `_render_context` (A)
- `bin/_pkg/exclusive.py` — `transition_guard` return string (B)
- `SPEC.md` — §8 awareness text, §5 exclusive message
- Tests: `test_queue_awareness` / `test_exclusive` substring assertions
- Release: `bin/_pkg/__init__.py` + `.claude-plugin/plugin.json` (patch),
  `CHANGELOG.md`, then `cutting-a-release` flow

## Testing

- Update awareness-context test(s) to assert the new "shared root is leased
  ground" / "no host-side exception" phrasing is present.
- Update exclusive-guard test(s) to assert the new multi-tenant wording and that
  refusal behavior (returns non-None on a dirty root) is unchanged.
- Full suite: `python3 -m pytest test/ -q`.
