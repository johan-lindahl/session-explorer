# Live-indicator polish — design (dialogs + live refresh + docs)

**Date:** 2026-05-29
**Status:** Approved (brainstorm); pending implementation plan
**Branch:** fresh branch off `main` (v1.1.0 / PR #16 already merged)
**Affects:** `bin/_pkg/tui.py` (modal screens + live-refresh wiring + help text), `README.md`, `CHANGELOG.md`, `.claude-plugin/plugin.json` + `bin/_pkg/__init__.py` (version), `SPEC.md` (one line on refresh cadence), new dev-only screenshot generator + regenerated `docs/images/*.png`, tests.

## Goal

Polish the v1.1.0 live-session indicator with three independent improvements:

1. **Redesign the modal dialogs** (rename / move / new-folder / delete-confirm / notes) as centered bordered overlays like the help screen, instead of Textual's full-screen black default.
2. **Live-metadata refresh** — a live session's row currently shows its `SessionStart` snapshot (empty first prompt, 0 msgs, 0 tokens) until a manual F5; refresh live sessions' index metadata automatically so the row fills in and stats tick as the agent works.
3. **Docs** — explain the live glyphs (working spinner, idle `○`, `● N active`) in both the in-app help screen and the README, with regenerated screenshots showing the states.

These are cohesive polish on the shipped feature and fit one spec/plan. Version bump 1.1.0 → **1.2.0**.

## Part 1 — Dialog redesign (shared modal styling)

**Problem.** `RenameScreen`, `MoveScreen`, `NewFolderScreen`, `ConfirmScreen`, `NotesScreen` (`bin/_pkg/tui.py`) define **no CSS**, so they fall back to Textual's opaque full-screen black `ModalScreen` default. `HelpScreen` already has the liked look (`HelpScreen { align: center middle; } #help { width: 78; max-width: 90%; height: auto; max-height: 90%; padding: 1 2; border: round $accent; background: $surface; }`). There is no shared base; each modal repeats its own Esc binding.

**Design.** Introduce a shared base `_PanelScreen(ModalScreen)` carrying one `DEFAULT_CSS`:

- `_PanelScreen { align: center middle; background: $surface-darken-1 30%; }` — a **dimmed translucent backdrop** so the session tree shows through (chosen visual: centered panel, dimmed tree behind).
- An inner panel container (class `.panel`, or reuse `#panel`) with `border: round $accent; background: $surface; padding: 1 2; width: auto; max-width: 80%; height: auto; max-height: 90%`.
- A consistent **bold title line** at the top of each panel and a **dim footer hint** (`enter save · esc cancel`, `y yes · n cancel`, `ctrl-s save · esc cancel` for notes — match each dialog's existing keys).

Each of the five dialogs subclasses `_PanelScreen`, wraps its existing widgets (Label/Input, OptionList+Input, TextArea) in the panel container, and keeps **identical behavior**: same constructor args, same return values (`str` / `bool` / `None`), same key handling and dismiss semantics. The shared Esc-to-cancel binding moves to the base (dialogs that need a different cancel value override). `HelpScreen` may also adopt the base for consistency (optional; its current CSS already matches — keep it working either way).

**Boundaries.** Pure presentation: no change to `action_rename/move/new_folder/delete/notes` logic, the index/folder-store writes they trigger, or the values returned to their callbacks. The dimmed backdrop must not capture/leak key events to the tree underneath (modal already grabs focus; verify Esc closes the dialog, not the preview).

## Part 2 — Live-metadata refresh

**Problem.** `_poll_live` (2s) updates `_live_states` and relabels glyphs, but never refreshes the **index** entry. A new session's index row is the `SessionStart` snapshot (`first_prompt=None`, `message_count=0`, `tokens_estimate=0`) until F5 reindexes. Measured cost (this machine): a full F5 reindex re-reads 374 transcripts / 404 MB ≈ 5.4s + a `git` call each; re-reading a *single* live transcript's metadata is ≈ 31 ms (2.4 MB) to ≈ 120 ms (worst-case 12 MB).

**Design.** After `_poll_live` updates `_live_states`, kick a background refresh of **only the live sessions** (chosen cadence: every 2s poll — stats tick live):

- A `@work(thread=True, exclusive=True)` worker (`exclusive` so a slow refresh can't stack across polls) iterates the current live sids and calls `index.record_session(self._index_path, sid, transcript_path, cwd)` for each — the existing single-session updater, which re-reads that one JSONL and writes the flock'd index, **preserving** user-edited fields (notes) and `created_at`. `transcript_path`/`cwd` come from the live registry entry (recorded at SessionStart) or the existing index row. Persisting to the index (vs in-memory only) means the row stays correct after the session goes inactive, with no later F5 needed.
- On completion the worker marshals back via `call_from_thread` to a UI updater that **reloads the index and refreshes only the live rows in place** — update each live leaf's stored `data` dict and re-render its label (reusing the existing `_row_nodes` map and `_relabel_live_rows`/`_row_label` path). No full `_populate()` on the metadata path, so cursor / scroll / expansion are preserved. (A genuine *visibility* change — an unnamed session appearing/dying — still goes through the existing `_visibility_changed` → `_populate` path with cursor restore; unchanged.)
- Resilience: the worker swallows exceptions (a transcript mid-write, a vanished file) and never breaks the UI, matching `_poll_live`'s existing contract. A live session missing from the index is simply skipped (record_session would add it; backfill/F5 remains the catch-all).

**Why not change F5 / liveness cadence.** F5 stays the full reindex. Liveness/spinner stay on the 2s/200ms timers. Only the *metadata* of the 1–3 live sessions is refreshed, off-thread — ~150× cheaper than F5.

## Part 3 — Docs (help screen + README + screenshots)

**In-app help.** Extend the help text (`_help_text()` content rendered by `HelpScreen`) with a **"Live sessions"** section: the animated green spinner = a session actively working; the dim `○` = a session open but idle; the `● N active` subtitle = count of live sessions; live rows refresh from their transcript about every 2 seconds (first prompt, message count, tokens, context % update as the agent works); live sessions appear even when unnamed.

**README.** Add the live indicator to the "What it looks like" feature list and add a short legend (spinner / `○` / `● N active`) plus the ~2s live-refresh note; mention dialogs now match the help overlay. A unit test asserting `__version__` appears in the help text already exists — keep help text changes compatible.

**Screenshots (programmatic — proven recipe).** Commit a **dev-only** generator script (e.g. `scripts/gen_screenshots.py`, NOT under `bin/_pkg/`, so it adds no plugin runtime dependency — the "one vendored dep: Textual" rule is preserved; only the resulting PNGs ship). It builds a fabricated index, drives the real app headless via Textual `run_test()` + `app.save_screenshot()` (SVG), and — for the live image — sets `app._live_states = {…: "working", …: "idle"}` and `app._spinner_frame` so the spinner glyph and `○` render. Pipeline (tools confirmed present: Google Chrome, ImageMagick):

1. `app.save_screenshot("…/<name>.svg")`
2. `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 --default-background-color=00000000 --window-size=1439,731 --screenshot=<name>.png "file://…/<name>.svg"` (Chrome renders the SVG faithfully; ImageMagick's native SVG renderer is too low-fidelity).
3. `magick <name>.chrome.png -resize 1600x -strip docs/images/<name>.png`

Regenerate `tree.png` (now showing live glyphs on a couple of rows) and add a dedicated `docs/images/live.png` (working + idle + inactive rows, with the `● N active` subtitle) referenced from the new README legend. `preview.png`/`help.png` regenerated only if the dialog/help changes alter them. See `reference-readme-screenshots` memory.

## Testing

- **Dialogs:** existing `test_tui.py` modal tests must still pass (behavior unchanged). Add/adjust where a test inspects modal structure. A `run_test()`-based smoke test that each dialog opens, accepts input/Enter, and Esc cancels — asserting return values — guards the refactor. (Pure-CSS changes aren't unit-testable; rely on the behavior tests + the generated screenshot for visual confirmation.)
- **Live refresh:** a `run_test()` test that seeds an index entry with empty `first_prompt`/`0 msgs` for a live sid pointing at a fixture transcript that *does* contain a prompt, runs the refresh path, and asserts the index entry (and the rendered row) now shows the prompt/msgs — and that a non-live session is left untouched. Assert the metadata path does **not** reset cursor (no full `_populate`).
- **Help text:** extend the existing help-content test to assert the new "Live sessions" wording is present.
- Full suite green (`pytest test/ -q`, `bats test/install.bats test/uninstall.bats test/hook.bats`).

## Tunables / decisions locked

| Decision | Choice |
|---|---|
| Dialog look | Centered panel, **dimmed tree behind**, shared `_PanelScreen` base |
| Live-metadata refresh cadence | **Every 2s poll**, off-thread, persisted to index, in-place row relabel |
| Screenshot method | **Programmatic** (headless Textual → Chrome → magick), dev-only generator committed |
| Version | 1.1.0 → **1.2.0** |

## Out of scope

- Changing F5 / full-reindex behavior or the liveness/spinner cadences.
- Any change to the hook/registry/death-detection from v1.1.0.
- Native Windows screenshot tooling.
- Re-theming beyond the modal dialogs (no broad TUI restyle).
