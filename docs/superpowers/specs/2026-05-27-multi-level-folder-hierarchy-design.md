# Multi-level folder hierarchy — design

**Date:** 2026-05-27
**Status:** approved (brainstormed)
**Supersedes:** the first-dash folder convention documented in `SPEC.md §Naming and folders`.

## Problem

Today the session-explorer derives folders from the **first dash** of a session's `custom-title`: `planning-sprint14` → folder `planning`, display `sprint14`. This gives a single level of folders and conflates the separator with a character that is common inside session names (`bugfix-watch-lockup`, `42289-migration-reconciliation-report`). Names that *should* nest deeper (`team/planning/q1` style) have no way to express it, and names that legitimately contain dashes can't escape being interpreted as a folder split.

## Goals

1. Switch the folder separator to `/` and support **arbitrary nesting depth**.
2. Store folder structure in a **separate JSON file** keyed by project, so the index file is only about sessions.
3. Auto-map a session whose name contains `/` into the matching folder path (creating intermediate folders in the store as needed).
4. Let the user pre-create folders in the TUI via `n`; pre-created folders persist even when empty.

## Non-goals

- Backward-compatible auto-rewriting of existing dash-names to `/`. Existing renamed sessions stay as-is and collapse to the project root; users rename manually if they want them organized. (Decided in brainstorming.)
- Folder rename and explicit folder delete. Same as today, these are implicit via moving sessions and the `--gc` 90-day empty-folder sweep.
- Cross-project moves, drag-and-drop, or any other reorganization beyond `n` and `m`.

## Design

### 1. Name parsing

The session's `custom-title` is interpreted as a `/`-separated path:

```
<segment>/<segment>/…/<display>     → all but last → folder path; last → display name
<just-a-name>  (no /)               → at project root; display = name
(no name)                           → unnamed, hidden by default (unchanged)
```

- `/` is the only separator. Dashes have no special meaning — `bugfix-watch-lockup` displays as one name at the project root.
- Empty segments (from `foo//bar`, leading/trailing `/`) are collapsed/trimmed during parsing. A name of just `/` or whitespace parses to no display name and is treated as unnamed.
- `tree_model.split_folder(name) -> (folder_str, display_str)` is replaced by `tree_model.split_path(name) -> (folder_segments: list[str], display: str)`. The folder path is represented as a list of clean segments throughout the codebase; the `/`-joined string form is for storage and display only.

### 2. Folder store — `~/.claude/session-explorer-folders.json`

Persistent file separate from the session index. Schema:

```json
{
  "version": 1,
  "projects": {
    "acme-api": ["planning", "planning/sprint14", "bugfix"],
    "acme-app": ["watch", "watch/v2"]
  }
}
```

- **Flat list of folder paths per project**, not a nested dict. Membership tests, additions, and removals are O(1)-ish on a list and trivial to make atomic with the existing flock+temp-file-rename pattern.
- Project keys are `project_label` values — the same labels that already collapse worktrees under their parent repo (see `index._project_label`).
- Intermediate folders are implicit: storing `planning/sprint14` implies `planning` exists in the rendered tree. We do not store ancestor paths.
- All writes go through the same atomic `mutate(path, fn)` pattern used by `index.py`, against a sibling `*.lock` file.

### 3. Tree building

`tree_model.build_tree(index_data, folders_data, include_unnamed=False)` returns an in-memory nested structure suitable for the Textual `Tree` widget:

```python
{
  "acme-api": {
    "_sessions": [(sid, s), ...],                          # sessions at the project root
    "_folders": {
      "planning": {
        "_sessions": [(sid, s), ...],
        "_folders": {
          "sprint14": {"_sessions": [...], "_folders": {}},
        },
      },
      "bugfix": {"_sessions": [...], "_folders": {}},
    },
  },
  ...
}
```

The builder unions two sources of folder paths:
- **From sessions:** each session's `name_cached` is split with `split_path`; intermediate segments become folder nodes in the tree.
- **From the folder store:** each path string is split on `/` and added as folder nodes (without sessions).

Empty folders (folder store paths with no sessions under them) render as expandable folder nodes with no leaves — same UX intuition as today's `(unfiled)` empty folders.

Worktree sessions group under their parent repo because `project_label` already collapses to the parent (unchanged behavior).

### 4. Auto-create on indexing

`index.record_session` is extended: after writing the session entry, if `name_cached` contains `/`, the folder path (all segments except the last, joined by `/`) is added to the folder store under that project. Idempotent — duplicates are filtered.

This is what makes "rename a session to `team/planning/sprint14`" materialize the folder tree even if the session is later renamed away — the folder persists in the store until `--gc` prunes it.

### 5. UX — `n` (new folder)

Context-aware on the tree cursor:

- **Project node** → modal asks `"new folder path under <project>:"`, accepting multi-segment input like `planning/sprint14`. Stored as one path entry under the resolved project.
- **Folder node** → modal prefilled with the folder's path + trailing `/`, so the user just types the child segment(s).
- **Session leaf** → treat as the parent folder (or the project, when at root).

Empty input cancels. If the cursor sits at a position with no resolvable project (shouldn't happen normally), the action bells.

### 6. UX — `m` (move)

The modal lists *all* folder paths in the current project as full strings (`planning`, `planning/sprint14`, `bugfix/v2`), sorted alphabetically. Plus the `(ungroup)` option to move to project root. Typing a new path is allowed and auto-creates it (added to the folder store).

The move action writes a new `custom-title` to the session's JSONL, joining the chosen folder path and the current display name with `/`. So moving session `req-lists` from `planning` to `bugfix` writes `bugfix/req-lists`. Moving to `(ungroup)` writes just `req-lists`.

### 7. `--gc` and `--refresh`

- **`--refresh`**: re-indexes session metadata as today, and re-runs the auto-create step so any newly-renamed `/`-bearing sessions repopulate folder-store paths.
- **`--gc`**: prunes folder paths from the store that have been empty for >90 days, scoped per-project. A stored path `P` is "empty" iff (a) no indexed session's folder path equals `P` or starts with `P + "/"` (no session lives in `P` or any descendant), AND (b) no *other* stored path starts with `P + "/"` (no other stored folder lives under `P`).
- **`--backfill`**: unchanged. Auto-create on indexing handles any historical sessions whose names happen to contain `/`.

### 8. Schema migration

The session index bumps `version: 1 → 2`. On load:

- If the existing index has a `folders[]` field with entries, those entries are interpreted as today (synthetic `(unfiled)` project — there was no project scoping before). They are migrated into `session-explorer-folders.json` under a literal `(unfiled)` project key, and the `folders` field is removed from the index. `(unfiled)` continues to render as a top-level pseudo-project in the tree, matching today's behavior — it exists *only* for migrated legacy entries; new pre-created folders always go under a real project.
- The migration runs once, gated by `version == 1`. After migration the index is written with `version: 2` and no `folders` key. Order: write the folder-store file first, then write v2 index. If we crash between the two, `version == 1` still holds and the migration retries — `folder_store.add` is idempotent so duplicate runs are harmless.
- The folder-store file is created lazily on first write (no eager initialization at load time).

### 9. Module layout

- **New:** `bin/_pkg/folder_store.py` with `load(path)`, `save(path, data)`, `mutate(path, fn)`, `add(path, project, folder)`, `remove(path, project, folder)`, `list_paths(path, project) -> list[str]`. Mirrors `index.py`'s API and concurrency model.
- **Replaced:** `bin/_pkg/folders.py` is deleted; its callers are repointed to `folder_store`.
- **Changed:** `tree_model.split_folder` → `tree_model.split_path`; `build_tree` takes both stores and returns the nested form above.
- **Changed:** `index.record_session` calls `folder_store.add` when `name_cached` has `/`.
- **Changed:** `tui` modules — `MoveScreen`, `NewFolderScreen`, `_populate`, `action_move`, `action_new_folder` rewritten for nested paths and project-scoped folder lists. The `Tree` widget's nested rendering already supports arbitrary depth so no new widget code is needed.

### 10. Testing

Unit:
- `tree_model.split_path`: empty, no-slash, one slash, many slashes, leading slash, trailing slash, double-slash, whitespace-only.
- `tree_model.build_tree`: unioning store + session-derived paths, nesting, projects with mixed root + nested sessions, sessions whose name has more segments than any stored path (auto-creates intermediates in the rendered tree), include_unnamed flag.
- `folder_store.add/remove/list_paths`: idempotency, atomic write, missing-project handling, multi-project isolation.
- `index.record_session`: writes folder-store entries for `/`-bearing names; doesn't for root names; preserves user-edited fields on re-record.
- `index` schema migration: v1 with `folders[]` becomes v2 with empty `folders` (removed) and the file `session-explorer-folders.json` has the migrated entries under `(unfiled)`.

TUI:
- `n` on a project node creates a top-level folder path.
- `n` on a folder node prefills the path and creates a child.
- `m` lists all project folders as full paths and writes a custom-title joining the chosen path with the display name.
- `m` with `(ungroup)` writes a bare display name.
- `m` with a typed new path auto-adds it to the store.

### 11. Scope guard

In scope:
- `/` separator, multi-level folders, folder store split, n/m UX rewrite, store migration, schema bump.

Out of scope:
- Reading old `-` names as paths (decided: leave existing dash-names alone, project root).
- Explicit folder rename / delete commands.
- Drag-and-drop reorganization.
- Cross-project moves.
- Backward-compatible v1-index reads beyond the one-shot migration on first load.
