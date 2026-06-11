# Shared-resource queue — user guide

> ⚠️ **Experimental.** Enforced for Claude tool calls; non-Claude writers
> (scripts, the user's own terminal, runtime-computed paths) remain advisory.
> The dirty-root refusal at the next overlay acquire is the backstop for those.

> Reach for this only when isolation is genuinely impossible.

## When NOT to use this

If each worktree can get its own resource, isolate instead — it is simpler and
has no destructive step:

- **Ports:** dynamic ports / `--project-name` per worktree.
- **Databases:** a per-worktree DB volume or schema.
- **Build/derived data:** per-job `-derivedDataPath` / build dir.

Use the queue **only** when the resource is heavy/slow to boot, tied to a fixed
path (bind-mounted) or well-known ports, or physically singular (one simulator,
one device, N license seats).

## The one dangerous primitive: `sync`

A `root-dir` resource with `acquire: sync` runs, on every acquire:

    rsync -a --delete <your-worktree>/ <shared-root>/

with anchored `--filter=exclude` rules. `--delete` means the acquire **removes
whatever the previous holder left** — that is the point (the next acquire is the
reset), but it also means anything in the shared root that is not protected and
not in your worktree is deleted.

- **`protect`** lists root-only paths to keep untouched (secrets, certs,
  fixtures). `/.git`, `/.env`, `/.env.*`, `/.claude/worktrees` are protected by
  default (`.claude/worktrees` holds the repo's sibling worktrees — deleting it
  would wipe them, so it is never synced).
- **`exclude`** lists worktree junk that must not be copied in (`/.git`,
  `node_modules`).
- The first time a root enters sandbox mode, `queue-run` refuses until every
  untracked/gitignored path the dry-run would delete is classified as *protect*
  or *allow-delete*.

## root is exclusive-or

A `root-dir` root is **either** a live working session **or** the lease sandbox,
never both. While a live Claude session is working in root, worktree leases
block (visible in the Queues pane). Create worktree sessions, not plain root
sessions, in shared-root projects — the new-session dialog defaults to this.

## Leased ground — the location rule

For a Claude session **in a worktree**, the shared installed root is
**unreachable through tools** (`Bash`/`Edit`/`Write`/`NotebookEdit`) except via a
single `session-explorer queue-*` command. This is enforced by the `PreToolUse`
hook (`root_guard.py`), not by convention.

### What a deny looks like

The hook emits a deny reason with the exact rewrite:

```
<path> is the shared installed root — it is leased ground, unreachable
outside a lease. Re-run the ENTIRE command through the queue:

    session-explorer queue-run --resource <rid> -- bash -c '<original command>'

To merely inspect root files, use the Read tool (reads are not blocked).
session-explorer queue-status shows who holds the lease.
```

For **compound commands** (`cmd1 && cmd2`, pipelines, `; cmd`), wrap in
`bash -c '...'` as shown. For commands with newlines, backticks, or `$(…)` that
cannot be safely quoted in a single argument, put them in a script file and run
the script through `queue-run`.

### The one door

All legitimate root access goes through `queue-run`:

    session-explorer queue-run --resource <rid> -- <cmd>

The engine: ticket → FIFO wait → exclusive-or check → overlay-in → run in root
→ overlay-out (engine `finally`) → release. The overlay copies your worktree's
changed files into root, runs your command, and restores them — even on failure
or signal.

Bash reads of root files are denied like any other root mention — path
presence in the command is the trigger, not intent. Inspect root files with the
**Read tool**, which is never blocked by the hook.

### Shared installed app root (overlay tests)

When only your repo's main checkout is a fully installed app (vendor/, generated/,
DB, env) and worktrees are bare checkouts, tests must run *in* the root. Set up
the shared installed root once (`q` → `s` in the explorer), then run as:

    session-explorer queue-run --resource <name> -- phpunit path/to/Test.php

### Setting up

In the explorer, select the project and press **s**. Sharing has nothing to
configure — the root is the repo's main working tree and the overlay shape is
implicit — so `s` is a confirm toggle: press it once to **enable** shared-root
queueing (a `y`/`n` confirm explains what it does and links the guide), and
again later to **stop sharing**. The Queues pane (**q**) shows live holders and
waiters across every opted-in project, and its footer repeats the `s` hint.

### Limits

- **Runtime-computed paths** (env vars, `$(git rev-parse …)`, scripts that `cd`
  internally) slip past lexical matching. The next `queue-run` acquire refuses a
  dirty root (`transition_guard`) with the multi-tenant message.
- **Non-Claude writers** — other processes, scripts run outside Claude, the user
  by hand — are out of scope. The dirty-root refusal is the backstop.
- `Bash` reads of root files are false-positive denied; use the Read tool.

## As a Claude agent in this project

If you are a Claude session working in a worktree of a shared-root project, the
SessionStart hook has already given you a short usage hint. The key points:

- **The shared root is write-blocked at the tool layer.** Any `Bash`/`Edit`/
  `Write`/`NotebookEdit` call that touches the root is denied with the exact
  rewrite. You don't need to remember; you can't forget.
- **The one door:** `session-explorer queue-run --resource <name> -- <cmd>`.
  queue-run takes the lease (FIFO order if busy), overlays your changed files
  into root, runs the command, and restores.
- **To inspect root files** without a lease, use the **Read tool** — reads are
  not blocked.
- **Never start your own copy of a shared stack / server / database.** It is
  already running and warm; a second copy collides on fixed ports and paths.
- **Don't busy-spin or force a busy resource.** Check your queue position with
  `session-explorer queue-status` and wait.
- **Expect an `overlay` lease** to copy your changed files into root on acquire
  and restore them on release. Keep secrets / local-only files out of tracked
  paths (add them to the resource's protect list).

### CLAUDE.md snippet (copy into an opted-in project)

```markdown
## Shared resources

This repo's shared root is leased ground. For a Claude session in a worktree:
- ALL tool calls that mention the root path (Bash/Edit/Write/NotebookEdit) are
  denied with a rewrite — Bash root mentions are blocked regardless of intent
  (reads included), not just writes.
- The only door: `session-explorer queue-run --resource <name> -- <cmd>`.
- To read root files without a lease, use the Read tool.
- Do NOT start your own stack/server/db — it is already warm and will collide.
- Check who holds a resource: `session-explorer queue-status`.
```
