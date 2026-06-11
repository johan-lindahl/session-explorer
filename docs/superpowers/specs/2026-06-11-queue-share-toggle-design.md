# Shared-root setup: collapse the dialog to a toggle

**Date:** 2026-06-11
**Status:** Approved, implementing
**Scope:** TUI only (`bin/_pkg/tui.py`), Queues pane + shared-root setup. No engine/config changes.

## Problem

The per-project shared-root setup dialog (`SharedRootScreen`, reached by `s`) has four
user-reported issues:

1. **`ctrl+d` (stop sharing) is dead.** The dialog's only focusable widget is the Protect
   `TextArea`, which always holds focus. The vendored Textual `TextArea` binds `ctrl+d` to
   forward-delete (`_vendor/textual/widgets/_text_area.py:363`), so it swallows the key
   before the screen's `ctrl+d → stop_sharing` binding ever sees it. With no other focusable
   widget, the binding can never fire. (`ctrl+s` survives only because `TextArea` doesn't
   bind it.)
2. **The Protect field is inert here.** `protect` only matters for the `sync` acquire
   strategy (rsync `--delete`). The leased-ground redesign removed `sync` from the UI:
   `SharedRootScreen` only ever writes `acquire: command` (overlay), where the box's value
   flows into `res["sync"]["protect"]` that the overlay never reads — the code already calls
   it "data for future use + display" (`tui.py:720`).
3. **No discoverability of `s` in the populated Queues pane.** The empty-state hint says
   "press `s` to set up"; once a resource is configured, that hint disappears.
4. **Long holder session names wrap** in the Queues pane (the name column is unbounded).

Once Protect is removed, the "share/save" action has **zero parameters** — the dialog is a
toggle wearing a text-editor costume. New shares already use a fixed resource id (`"root"`,
`tui.py:661`); there is no naming input.

## Design

### 1. Replace `SharedRootScreen` with a confirm-based toggle

Delete `SharedRootScreen`, `QueueHelpScreen`, and `_queue_help_text`. `action_resource_setup`
becomes the whole flow:

- Resolve the selected project + `project_id` (unchanged; still requires a git repo).
- Find the existing `root-dir` resource (any shape) and its id.
- **Decision:** "currently shared the safe way" ⇔ a `root-dir` resource exists **and**
  `acquire == "command"`.
  - **Shared (overlay) →** `ConfirmScreen("Stop sharing the installed root ('<rid>')?",
    detail="Queue config only — no files are touched.")`. Yes → `remove_resource`.
  - **Not shared, or a legacy non-overlay resource exists →** `ConfirmScreen("Enable
    shared-root queueing for <project>?" / "Re-save <rid> as the safe overlay shape?",
    detail=<how it works + experimental caveat + guide URL>)`. Yes → `add_resource` with
    `SHARED_ROOT_DEFAULTS` (+`path`), reusing the existing rid or `"root"`.

This single "write overlay shape, keep-or-create id" action preserves the **migration**
guarantee (a legacy `acquire: sync` resource is rewritten to the safe overlay shape on
enable, keeping its id) while collapsing to two outcomes.

`ConfirmScreen` gains an optional `detail: str = ""` second line (rendered as a
`dialog-hint` Label) so the enable path can carry the "how queues work" explainer. Its
Yes/No/Esc keys already work — the `ctrl+d` problem evaporates with no `TextArea` present.

`QUEUE_GUIDE_URL` and `QUEUE_EXPERIMENTAL` constants stay; the enable detail text reuses
them. `parse_path_lines` loses its only caller and is removed.

### 2. Queues-pane footer hint

`_render_queue_rows` appends a dim footer line:
`press s to set up sharing · guide: docs/queue-guide.md`. Shown whenever the populated pane
renders (the empty-state branch in `_render_queues` keeps its own hint).

### 3. Truncate session names

A `_trunc(name, _QUEUE_NAME_MAX=20)` helper (ellipsis) wraps the holder, waiter, and
live-root-block session names in `_render_queue_rows` so a long title can't line-wrap.

## Out of scope

- No engine/config changes. `sync`/`protect` remain understood by the engine for
  back-compat configs; they're just not a UI surface (already true).
- No change to the location guard, `queue-run`, or overlay mechanics.

## Tests

- Rewrite the two `SharedRootScreen` tests in `test/test_tui_queue.py` to drive
  `action_resource_setup` (enable writes overlay; legacy sync resource migrates on enable,
  id kept).
- Drop `test_parse_path_lines` and the `_queue_help_text` assertions; add a check that the
  enable detail text contains `QUEUE_EXPERIMENTAL` and `QUEUE_GUIDE_URL`.
- Add: populated pane shows the `s` footer hint; a long holder name is truncated with `…`.

## Release

Patch bump (bugfix + UX). Update `SPEC.md` setup/help sections + status line, `CLAUDE.md`
load-bearing note, `CHANGELOG.md`, help-screen keybindings if changed.
