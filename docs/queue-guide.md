# Shared-resource queue — user guide

> ⚠️ **Experimental — cooperative only.** This system coordinates by convention.
> It cannot prevent an uncoordinated process from touching a shared resource.

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

Always preview with the editor's **dry-run** (ctrl-r) before relying on a sync
resource.

## root is exclusive-or

A `root-dir` root is **either** a live working session **or** the lease sandbox,
never both. While a live Claude session is working in root, worktree leases
block (visible in the Queues pane). Create worktree sessions, not plain root
sessions, in shared-root projects — the new-session dialog defaults to this.

## Template catalog

| Template | Use when | kind · acquire · run_in |
|---|---|---|
| Bind-mounted stack, well-known ports | heavy Docker stack on fixed ports | root-dir · sync · root |
| Browser e2e vs fixed-URL app | Playwright/Cypress at a fixed baseURL | root-dir · sync · root |
| iOS simulator / xcodebuild | one simulator, global signing/build.db | device · none · worktree |
| Single shared database | one DB on a fixed socket/port | port · none by default (command once you add a DB-reset shell) · worktree |
| Root-only credentials / .env | secrets exist only at root | root-dir · sync (protect .env) · root |
| Single device / HIL / license seat | physically singular resource | device/name · none · worktree |

## Shared installed app root (overlay tests)

When only your repo's main checkout is a fully installed app (vendor/, generated/,
DB, env) and worktrees are bare checkouts, tests must run *in* the root. Use the
"Shared installed app root (overlay tests)" template: it takes a FIFO mutex on the
root, copies your worktree's changed files in, runs your command, and restores
them after — even if the command fails. Run tests as:

    session-explorer queue-run --resource <name> -- phpunit path/to/Test.php

Guard the tools that need the root (phpunit, phpstan, `bin/magento setup:*`). Do
NOT guard phpcs / php-cs-fixer — they run fine in a bare worktree and must not be
serialized. `php bin/magento …` is a known guard blind spot (mitigated by the
awareness injection).

## Setting up

In the explorer, select a project and press **s**. Add a resource from a
template, edit its fields, test the guard and dry-run, and save. The Queues pane
(**q**) shows live holders and waiters across every opted-in project.

## Cooperating as an agent

If you are a Claude session working in a project that shares singleton resources,
the SessionStart hook already told you which resources exist. To cooperate:

- **Never boot your own copy of a shared stack / server / database.** It is
  already running and warm; a second copy collides on its fixed ports and paths.
- **Run guarded commands through a lease:**
  `session-explorer queue-run --resource <name> -- <command>`. queue-run takes
  the lease (waiting in FIFO order if it's busy), runs the command, and releases.
- **Don't busy-spin or force a busy resource.** Report your queue position
  (`session-explorer queue-status`) and wait.
- **Expect a `sync` lease to overwrite the shared root** with your worktree's
  files on acquire. Keep secrets / local-only files out of tracked paths.

### CLAUDE.md snippet (copy into an opted-in project)

```markdown
## Shared resources

This repo shares singleton resources across worktrees via session-explorer.
- Do NOT start your own stack/server/db — it is already warm and will collide.
- Run guarded commands as: `session-explorer queue-run --resource <name> -- <cmd>`.
- Check who holds a resource with `session-explorer queue-status`.
```
