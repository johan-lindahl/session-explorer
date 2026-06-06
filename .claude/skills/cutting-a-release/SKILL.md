---
name: cutting-a-release
description: Use when shipping a new session-explorer version — bumping the version number, cutting a GitHub release/tag, or asked to "release", "cut a version", or "bump to vX.Y.Z". Covers which files must change and how to publish the release.
---

# Cutting a session-explorer release

## Overview

A release is **two phases**: (1) the version bump + docs land on `main` (normally inside the feature PR that did the work), then (2) a git tag + GitHub release are cut from `main`. Both phases must happen — code can be at the new version while the GitHub tag lags (this has happened: tags lapsed v1.4.0 → v1.10.0 while the files marched on).

**SemVer:** `minor` for features, `patch` for fixes. Match the bump to what shipped.

## Phase 1 — bump version + docs (lands on `main`)

Every one of these must show the new version. Grep before tagging to prove none lag:

```bash
NEW=<x.y.z>   # set this to the version you are shipping, e.g. 1.10.1
grep -rn "$NEW" .claude-plugin/plugin.json bin/_pkg/__init__.py README.md SPEC.md CHANGELOG.md
```

**Normal path:** the bump rides along in the same PR as the feature/fix. **If the work already merged to `main` without a bump** (or you're cutting a release of already-merged work): branch from fresh `main`, make the bump-only changes below, open a PR, merge it, then `git pull` before Phase 2. Do not push the bump onto the already-merged feature branch, and don't commit straight to `main` if branch protection is on.

| What to update | Where | Note |
|---|---|---|
| `__version__` | `bin/_pkg/__init__.py` | Source of truth |
| `"version"` | `.claude-plugin/plugin.json` | **Easy to forget — must match `__init__.py`** |
| Status line | `README.md` (`**vX.Y.Z.** Released and installable…`) | |
| Status line + milestone footer | `SPEC.md` (`**Status:** Shipped — **vX.Y.Z**` and `current release: vX.Y.Z`) | |
| New section | `CHANGELOG.md` | See below |
| **Help screen** | `bin/_pkg/tui.py` → `_help_text()` | Version renders **dynamically** from `__version__` (no edit). But the **keybinding descriptions are hand-written** — if the release changed/added keys, update the `key(...)` lines AND the matching `Binding(...)` rows. |
| **Queue guide** | `docs/queue-guide.md` | Linked from the editor's `?` help (`QUEUE_GUIDE_URL` in `tui.py`); if the shared-resource model, `--delete`/`protect` rule, or template catalog changed, keep the guide in sync so the in-dialog link doesn't silently diverge. |

### CHANGELOG.md

Newest version on top, descending order. Group entries under `### Added` / `### Changed` / `### Fixed`. Keep the prose dense and behavior-focused, matching existing entries. Pull the substance from the PR description, not the commit subject.

## Phase 2 — cut the GitHub release

Only after the bump is merged to `main`, `HEAD == origin/main`, and CI/tests are green:

```bash
git fetch && git rev-parse HEAD origin/main   # confirm in sync, clean tree
```

**Write the notes file first** — `--notes-file` will not create it for you. Mirror the CHANGELOG entry you just wrote; crib the trailer format from a prior release (`gh release view v1.9.0 --json body`):

```bash
cat > /tmp/relnotes.md <<'EOF'
## <headline>

<prose mirroring this version's CHANGELOG section>

**Full changelog:** https://github.com/johan-lindahl/session-explorer/blob/main/CHANGELOG.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF

gh release create vX.Y.Z --target main \
  --title "vX.Y.Z — <short headline>" \
  --notes-file /tmp/relnotes.md
gh release list -L 3                          # confirm it shows as "Latest"
```

- **Tag format is `v`-prefixed** (`v1.10.0`). `gh release create` creates and pushes the tag itself — no separate `git tag`.
- Release-note body mirrors the CHANGELOG entry; end with the `**Full changelog:**` link and the Claude Code attribution line (see prior releases via `gh release view v1.9.0 --json body`).
- Title style: `vX.Y.Z — <headline>` (e.g. `v1.10.0 — TUI quality-of-life`).

### Retroactive release (a newer version already shipped)

When a tag lapsed and `main` has since moved past that version (happened with v1.12.0: v1.12.1 was released first), `--target main` would tag the wrong tree. Instead, tag the merge commit whose tree carries that version, push the tag, then create the release from the existing tag with `--latest=false` so the actual newest release stays Latest:

```bash
git tag vX.Y.Z <merge-commit>        # verify first: git show <sha>:bin/_pkg/__init__.py
git push origin vX.Y.Z
gh release create vX.Y.Z --latest=false --title "..." --notes-file /tmp/relnotes.md
```

(`gh release create --target <sha>` is rejected — "target_commitish is invalid" — which is why the tag is pushed first.)

## Checklist

- [ ] Decide bump type (minor=feature, patch=fix)
- [ ] `__init__.py` + `plugin.json` both bumped and **equal**
- [ ] README, SPEC (x2) status lines updated
- [ ] CHANGELOG.md section added, newest-on-top
- [ ] Help screen keybinding descriptions updated **iff** keys changed (covers the `q`/`x`/`s` queue keys)
- [ ] `docs/queue-guide.md` kept in sync **iff** the shared-resource model/templates changed
- [ ] `grep -rn "$NEW"` shows every file incl. CHANGELOG.md (no laggards)
- [ ] Bump merged to `main`; `HEAD == origin/main`; CI/tests green
- [ ] Notes file written (mirrors CHANGELOG); `gh release create vX.Y.Z --target main` run; shows as Latest

## Common mistakes

| Mistake | Reality |
|---|---|
| Bumping `__init__.py` but not `plugin.json` | The marketplace reads `plugin.json`; mismatched versions ship a wrong number. Always bump both. |
| Forgetting a `SPEC.md` status line (there are two) | These have silently lagged real releases. Grep, don't eyeball. |
| Hand-editing the version in the help screen | It's dynamic (`__version__`) — don't. Only edit `_help_text()` for **keybinding** changes. |
| Tagging before the bump is on `main` | Cut the release only when `HEAD == origin/main`, or the tag points at the wrong tree. |
| `gh release ... --json isLatest` | That field doesn't exist; use `gh release list` to confirm Latest. |
| Pushing the bump to a merged feature branch | If its PR already merged, branch from fresh `main` instead (watch for `[new branch]` in push output). |
