# Re-isolate relocated worktree sessions on resume (design)

**Date:** 2026-07-03 · **Status:** awaiting review · **Target:** v1.19.2 (fix)

## Problem

Since v1.19.1 the explorer *follows* a transcript that Claude Code relocates when
a worktree is removed (`reconcile_relocated` re-points the index row at the moved
`<sid>.jsonl` under the parent-repo project dir). That keeps the session visible
— but it also sets `project_path` to the **root**, and `_resolve_resume_cwd`
then returns the root (it exists and is non-empty), so the explorer now resumes
the session **in the shared root**.

A session running in the root is classified by `root_guard.decide()` as a
**root-owning** session (`_is_root_location` → `return None`, "a root session owns
its tree") and is therefore **exempt from the queue guard**. It touches the
shared installed root without a lease, dirties it, and every other worktree
agent's lease acquisition then refuses (dirty-root) — i.e. one relocated session
**blocks the root queue for all other agents**.

Root cause chain: worktree removed → native `/resume` relocates the transcript to
the root → v1.19.1 records `project_path = root` → resume runs in root →
guard-exempt → queue blocked.

## Empirical findings (throwaway-repo, `claude 2.1.199`, `claude -p` non-interactive)

Reproduced the full lifecycle: create `claude -w wt1` session → remove worktree →
hand-construct the relocated state (move JSONL to the root key + append the exact
`{"type":"relocated","relocatedCwd":<root>}` line Claude writes).

1. `claude --resume=<sid>` is **strictly scoped to the cwd's project key**. From
   the root it runs a relocated session **in the root** (reproduces the bug); it
   cannot even find a worktree-key session from the root ("No conversation
   found").
2. **`claude -w <leaf> --resume=<sid>`, launched from the repo root, re-isolates
   the session.** It recreates the worktree on the kept `worktree-<leaf>` branch,
   resumes with full context, and the session's working cwd moves back into the
   worktree (verified: the last messages' `cwd` is the worktree). Claude keeps
   the transcript FILE at the root key (it does not move it back) — which is fine:
   we read from there and always relaunch via `-w`.
3. The worktree identity survives relocation: the transcript retains its old
   `…/.claude/worktrees/<leaf>` message cwds, and the `worktree-<leaf>` branch is
   never deleted by worktree removal (only permanent-delete `purge` deletes it).

Conclusion: resuming via `-w <leaf>` restores isolation and queue-safety without
the explorer ever moving a native JSONL (Claude owns the relocation).

## Design

### 1. Track the worktree origin (`jsonl` + index)

- New `jsonl.worktree_origin(path) -> str | None`: the worktree **leaf** the
  session lived in, recovered by scanning message `cwd`s for the first value
  containing `/.claude/worktrees/<leaf>`. Textual-free, tolerant of a mid-write
  transcript (same shape as the other `jsonl` readers).
- `index.reconcile_relocated` already reads the transcript for `effective_cwd`;
  in the same pass it calls `worktree_origin` and, when the session was relocated
  AND has a worktree origin, stores `worktree_leaf` on the row. `record_session`
  preserves it (via the `**existing` merge). This makes "this session has been in
  a worktree" **explicit in the index**, not just inferrable.

### 2. Resume decision (tui resume path)

Given a row, choose the resume argv + cwd:

| Session state | cwd | argv |
|---|---|---|
| Live worktree (dir present) | the worktree | `claude --resume=<sid>` *(unchanged)* |
| **Relocated worktree-born** (`worktree_leaf` set, transcript at root key) | the **root** | `claude -w <leaf> --resume=<sid>` *(new)* |
| Dead worktree, not yet relocated (transcript still at worktree key) | recreate via existing `_recreate_worktree` | `claude --resume=<sid>` *(unchanged; still works)* |
| Genuine root session (never a worktree) | the root | `claude --resume=<sid>` *(unchanged)* |

The new case launches from the root (so `--resume` finds the root-key transcript)
and lets `-w <leaf>` recreate + re-enter the worktree. Argv built with the same
`=`-binding hardening as `_resume_argv` (injection-safe), and `<leaf>` is clamped
to `WORKTREE_NAME_MAX` (64, the limit `claude -w` enforces).

### 3. Queue-safety (root_guard) — no code change expected

After a `-w` resume the session's recorded cwd (live registry, written by
SessionStart) is the worktree, so `root_guard` classifies it as a **worktree
session** — it must `queue-run` to touch the root, exactly like any worktree
session. **Verification gate:** confirm in integration that the SessionStart
payload cwd for a `-w` launch is the worktree (message cwds strongly indicate it;
must be observed, not assumed — see "Verification").

### 4. Worktree glyph

`_worktree_state` currently keys off `/.claude/worktrees/` in `project_path`; a
relocated row has `project_path = root`, so it renders as a root session. Drive
the ⎇ glyph off `worktree_leaf` when present so a re-isolatable session still
reads as a worktree session in the tree.

### UX decision — DEFAULT: automatic

Re-isolation is **automatic**: the explorer always resumes a relocated
worktree-born session via `-w <leaf>`, silently rebuilding the worktree. Matches
the v1.12.0 intent (reclaim worktree disk while idle, rebuild on resume) and
avoids ever re-offering the queue-blocking "resume in root" path. *(User to
confirm; alternative was a per-resume prompt.)*

## Non-goals / decisions

- **Do NOT move native JSONLs.** Re-isolation is delegated entirely to `claude
  -w`; the explorer never relocates a transcript itself (preserves the
  load-bearing "don't move native JSONLs" invariant).
- Not addressing the disk cost of rebuilding the worktree on resume — that IS the
  intended trade (reclaim while idle, rebuild on resume).
- No change to `worktree.remove` / `purge` / gc.

## Verification (before release)

- Unit: `jsonl.worktree_origin` (worktree cwd present / absent / relocated);
  `reconcile_relocated` stores `worktree_leaf`; resume-argv builder picks
  `-w <leaf>` only for the relocated-worktree-born case.
- Integration (TUI): a relocated row resumes with argv `claude -w <leaf>
  --resume=<sid>` and cwd = root.
- **End-to-end (mandatory, per project rule "verify LLM shell-out e2e"):** on a
  throwaway repo, drive the real `-w --resume` and confirm (a) worktree recreated
  on `worktree-<leaf>`, (b) session's working cwd is the worktree, (c) the
  SessionStart-recorded cwd is the worktree so `root_guard` denies a root write.

## Files

- `bin/_pkg/jsonl.py` — `worktree_origin`
- `bin/_pkg/index.py` — store `worktree_leaf` in `reconcile_relocated`
- `bin/_pkg/tui.py` — resume decision (`-w <leaf>` case) + glyph from `worktree_leaf`
- `test/test_jsonl.py`, `test/test_index.py`, `test/test_tui.py`
- Docs/version: `__init__.py`, `plugin.json`, `README.md`, `SPEC.md`, `CHANGELOG.md`, `CLAUDE.md` (v1.19.2 via cutting-a-release)
