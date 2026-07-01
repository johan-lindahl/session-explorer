# Full-text conversation search (`f`)

**Date:** 2026-07-01
**Status:** Approved, implementing
**Scope:** New module `bin/_pkg/search.py` (Textual-free, unit-tested); new
`SearchScreen` + `action_search` wiring in `tui.py`; a new `f` binding kept in
sync across `BINDINGS`, `check_action`, and `_help_text`. Docs: `SPEC.md`,
`CLAUDE.md`, `README.md`, help screen, `CHANGELOG.md`. No new sidecar, no new
dependency, no settings/marker changes.

## Problem

Every session's complete conversation is on disk — the JSONL transcripts under
`~/.claude/projects/` contain every user message and assistant reply (confirmed:
~1,884 files / ~885 MB on the author's machine; each file carries `user` and
`assistant` message events with full `content`). But **none of that body text is
searchable.**

The explorer's `/` filter (`tui.py:_matches`, ~line 1169) only matches *cached
metadata*: `name_cached`, `notes`, `first_prompt`, `summary` (if generated), and
the session id. The index (`index.py`) stores only metadata — never message
bodies. `jsonl.py` extracts just the first prompt, name, counts, and token
estimates; it never surfaces the conversation body.

Concrete failure case (the motivating one): the user recalls discussing a
"media-common" tag in *some* past session but doesn't remember which session or
what was decided. The text exists on disk, but a mid-conversation mention is
invisible to today's search. There is no way to answer "which of my sessions
talked about X, and what did we say?"

## Non-goals (YAGNI for v1)

- **No cross-project "search everything".** Scope is the **current project**.
  (The recall case is project-local; global search is deferrable and noisier.)
- **No persisted search index / sidecar.** Live scan only — nothing to keep
  fresh, nothing that can go stale. (A cached index or SQLite FTS was considered
  and dropped; per-project live scan is sub-second and honours the "one Python
  dep: vendored Textual" rule.)
- **No regex / phrase / ranking.** Case-insensitive substring match only.
- **No jump-to-message-inside-transcript.** Snapshots stay read-only; `Enter`
  selects the session in the tree (resume is a separate, deliberate action).
- **No change to `/`.** The metadata filter is untouched and remains the fast,
  in-memory path. `f` is a distinct, deeper tool.

## Design

### 1. Search core — `bin/_pkg/search.py` (Textual-free)

Kept pure and UI-free (like `root_guard.py`, `gc.py`) so it unit-tests without a
running app. Reuses the extraction shape already proven in
`summary.build_digest` (`summary.py:96`), which walks `jsonl._iter_messages` and
pulls text from `user`/`assistant` messages only — string `content` or the
`type == "text"` parts of a list `content` — dropping tool_use / tool_result /
thinking / snapshots / system lines.

- `iter_text_messages(path) -> Iterator[tuple[str, str]]` — yields
  `(role, text)` per user/assistant message, untruncated. (Factor this shared
  extractor so `build_digest` can call it too; low-risk refactor, same output.)
- `search_transcript(path, needle) -> list[Hit]` — case-insensitive substring
  scan of each message's text. A `Hit` carries `role`, the matched `snippet`
  (see below), and the match offset for highlighting. Wrap per-line parsing in
  the same try/except `jsonl` already uses, so a partially-written live
  transcript can't raise.
- `search_project(rows, needle, *, include_unnamed) -> list[SessionResult]` —
  iterates the given session rows, calls `search_transcript` on each existing
  `transcript_path`, keeps rows with ≥1 hit, orders by `last_active_at`
  descending. Each `SessionResult` carries `sid`, display name/label,
  `hit_count`, and a **capped** snippet list (first N, with an `overflow` count
  for "+N more"). `include_unnamed=False` drops rows where `name_cached is None`
  (the same predicate the default tree view uses); `True` keeps them.
- **Snippet builder:** given the full message text and a match offset, return a
  ~80-char window centred on the match with surrounding whitespace/newlines
  collapsed to single spaces, plus the match's start/end within the window for
  highlighting. Ellipsis on either side when clipped.

Missing/dangling `transcript_path` (the index path can precede the file on disk)
is skipped, not an error.

### 2. UI — `SearchScreen` (a `ModalScreen`, like `HelpScreen`/`SettingsScreen`)

- A query `Input` at the top; below it a scrollable results region grouped by
  session: a header line per session (name · hit count · relative date) followed
  by indented snippet lines with the matched term highlighted, then a dim
  "+N more" when snippets were capped.
- **Footer bindings:** `Enter` = open the selected session, `Esc` = close, and a
  visible **"include unnamed" toggle** (its own key; shows current on/off state
  and re-runs the search when flipped). Up/down navigate results.
- **Execution:** the scan runs in a **guarded `@work` thread worker** — the
  CLAUDE.md rule that an unguarded periodic/worker exception kills the whole app
  applies; the tick body is wrapped log-and-skip. Search fires on `Enter` in the
  input (not per-keystroke, to avoid re-scanning files on every character). A
  short **determinate** progress line ("searched N/M sessions") shows while it
  runs, per the project's progress-feedback preference.
- **Empty state:** names the project and how many sessions were searched, and
  hints at the include-unnamed toggle.

### 3. Wiring — `tui.py`

- New `Binding("f", "search", "Search")`. `f` is currently free (in-use single
  keys: `r m n c d w e u z g q s h x`, plus `/ , tab space` and function keys).
- `action_search()` resolves the **current project** from the highlighted tree
  node (the project of the selected session, or the project node under the
  cursor), gathers that project's session rows honouring the include-unnamed
  toggle's current state, and pushes `SearchScreen`.
- On dismiss with a chosen `sid`, select that node in the tree (reusing existing
  selection logic). **Non-destructive:** selecting doesn't resume; the user
  previews/resumes from the tree as normal.
- Keep `f` in sync across `BINDINGS`, `check_action`, and `_help_text` — the
  same discipline the spec already mandates for `u`/`,`.
- Factor the project-row-gathering into a pure helper so it can be unit-tested
  without the UI (mirrors how `_settings_rows` is testable).

### Default

`include_unnamed` defaults **off**, matching the tree's default view (unnamed
sessions hidden). The user opts into the noisier, more complete scan per search.

## Data flow

```
press f
  → action_search: resolve current project + gather its rows
  → push SearchScreen (include_unnamed = off)
type "media-common", Enter
  → guarded @work worker: search_project(rows, "media-common", include_unnamed)
      → per row: search_transcript → iter_text_messages → substring hits → snippets
  → call_from_thread: render grouped results + "searched N/M"
navigate, Enter on a result
  → dismiss(sid) → select that session node in the tree (no resume)
```

## Error handling

- Worker body is guarded (log-and-skip); a scan failure surfaces as an error
  line, never an app crash.
- Per-line JSON parse errors are swallowed (partial live transcripts).
- Dangling/missing `transcript_path` rows are skipped.
- Empty needle: no-op (mirrors the `/` filter's empty-needle behaviour).

## Testing

- **`test_search.py`** (unit, temp-JSONL fixtures like the existing suite):
  - extraction: string `content`, list `content` with a `text` part, skips
    `tool_result`/`tool_use`/thinking/system.
  - matching: case-insensitive substring; multiple hits per session; zero hits.
  - snippet: window centring, whitespace collapse, ellipsis clipping, correct
    highlight offsets.
  - `search_project`: recency ordering, per-session snippet cap + overflow
    count, include/exclude-unnamed filtering, skipped dangling paths.
- **App-level:** unit-test the project-row-gathering helper (right rows for a
  project × toggle state) without spinning the UI, mirroring `_settings_rows`
  tests.

## Docs & release

- Update **SPEC.md** (authoritative) with the search feature and a load-bearing
  note ("`f` = live full-text scan of the current project's transcripts; no
  index; `/` remains the metadata filter").
- Update `CLAUDE.md`, `README.md`, and the in-app help screen keybindings.
- One version bump (**minor** — a feature), a `CHANGELOG.md` section, and the
  GitHub release, all at the end via the `cutting-a-release` skill. Build the
  whole feature before any bump/PR (one PR, one bump).
