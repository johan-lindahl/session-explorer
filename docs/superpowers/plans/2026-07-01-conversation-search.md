# Full-text Conversation Search (`f`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `f` key that live-searches the current project's transcript bodies (your + Claude's messages) and shows matching sessions with in-context snippets.

**Architecture:** A new Textual-free module `bin/_pkg/search.py` extracts user/assistant text per message, substring-matches a needle, and builds snippet windows — then aggregates hits per session for one project. `tui.py` gains a `SearchScreen` (Input + OptionList of results) driven by a guarded thread worker, plus an `action_search` that resolves the current project and, on selection, moves the tree cursor to the chosen session (non-destructive — no resume).

**Tech Stack:** Python 3.11+, vendored Textual (`OptionList`, `Input`, `Label`), pytest + pytest-asyncio (`run_test()` pilot), stdlib `re`/`os`.

## Global Constraints

- **One Python dep: vendored Textual.** No new dependency; no `pip install`. (spec Non-goals)
- **Live scan only — no sidecar, no index.** Read JSONL at search time. (spec Non-goals)
- **Scope = current project only.** No cross-project search. (spec Non-goals)
- **Case-insensitive substring only.** No regex/phrase/ranking. (spec Non-goals)
- **`search.py` is Textual-free** (like `root_guard.py`/`gc.py`) so it unit-tests without a UI. (spec §1)
- **Guarded worker:** any `@work` thread body must be wrapped log-and-skip via `_log_line(...)` — an unguarded worker exception exits the whole app. (CLAUDE.md; `_summarize_tick` is the reference pattern)
- **Keep `f` in sync across `BINDINGS`, `check_action`, and `_help_text`.** (CLAUDE.md rule, already applied to `u`/`,`)
- **`/` (the metadata filter) is unchanged.** `f` is a separate tool. (spec Non-goals)
- **`include_unnamed` defaults off**, matching the tree's default view. (spec Default)
- Extraction mirrors `summary.build_digest` (`bin/_pkg/summary.py:96`): walk `jsonl._iter_messages`, keep `user`/`assistant` text (string `content` or `type=="text"` list parts), drop tool_use / tool_result / thinking / snapshots / system.
- Tests run: `python3 -m pytest test/ -q`. Never run the full suite against a live tmux server (it calls kill-server); these tests don't touch tmux, but confirm the server is down first if unsure.

---

### Task 1: `search.py` — message extraction, per-transcript search, snippet windowing

**Files:**
- Create: `bin/_pkg/search.py`
- Test: `test/test_search.py`

**Interfaces:**
- Consumes: `jsonl._iter_messages(path)` (yields decoded JSON objects, skips malformed lines).
- Produces:
  - `iter_text_messages(path) -> Iterator[tuple[str, str]]` — `(role, text)` for user/assistant messages.
  - `search_transcript(path, needle) -> list[dict]` — one hit dict per matching message: `{"role": str, "snippet": str, "match_start": int, "match_end": int}` (first match per message only).
  - Module constants `SNIPPET_WIDTH = 80`, `MAX_SNIPPETS_PER_SESSION = 5`.

- [ ] **Step 1: Write the failing tests**

Create `test/test_search.py`:

```python
import json

from _pkg import search


def _write(tmp_path, lines):
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(t)


def test_iter_text_messages_keeps_user_and_assistant_drops_noise(tmp_path):
    p = _write(tmp_path, [
        {"type": "user", "message": {"content": "tag it media-common please"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Renamed media-common."}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "NOISE"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "file-history-snapshot", "snapshot": "x"},
        {"type": "system", "content": "sys"},
    ])
    got = list(search.iter_text_messages(p))
    assert got == [
        ("user", "tag it media-common please"),
        ("assistant", "Renamed media-common."),
    ]


def test_search_transcript_case_insensitive_substring(tmp_path):
    p = _write(tmp_path, [
        {"type": "user", "message": {"content": "Use the Media-Common bucket"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "no match here"}]}},
    ])
    hits = search.search_transcript(p, "media-common")
    assert len(hits) == 1
    assert hits[0]["role"] == "user"
    snip = hits[0]["snippet"]
    assert snip[hits[0]["match_start"]:hits[0]["match_end"]].lower() == "media-common"


def test_search_transcript_empty_needle_returns_nothing(tmp_path):
    p = _write(tmp_path, [{"type": "user", "message": {"content": "anything"}}])
    assert search.search_transcript(p, "") == []


def test_search_transcript_missing_file_is_empty(tmp_path):
    assert search.search_transcript(str(tmp_path / "nope.jsonl"), "x") == []


def test_snippet_collapses_whitespace_and_marks_clipping(tmp_path):
    long_pre = "word " * 40
    long_post = " tail" * 40
    p = _write(tmp_path, [
        {"type": "user", "message": {"content": long_pre + "NEEDLE\nhere" + long_post}},
    ])
    hits = search.search_transcript(p, "needle")
    snip = hits[0]["snippet"]
    assert "\n" not in snip                 # newlines collapsed to spaces
    assert snip.startswith("…") and snip.endswith("…")  # clipped both sides
    assert len(snip) <= search.SNIPPET_WIDTH + 8        # window + ellipses slack
    assert snip[hits[0]["match_start"]:hits[0]["match_end"]] == "NEEDLE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_search.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.search'`.

- [ ] **Step 3: Write the minimal implementation**

Create `bin/_pkg/search.py`:

```python
"""Live full-text search over session transcripts (spec 2026-07-01).

Textual-free so it unit-tests without a UI. Reads JSONL bodies at search time —
no sidecar, no index. Extraction mirrors summary.build_digest: user/assistant
text only, tool/thinking/system dropped.
"""
import os
import re

from . import jsonl as _jsonl

SNIPPET_WIDTH = 80              # chars of context around a match
MAX_SNIPPETS_PER_SESSION = 5   # cap snippets shown per session

_WS = re.compile(r"\s+")


def iter_text_messages(path):
    """Yield (role, text) for user/assistant messages. Skips tool_use,
    tool_result, thinking, snapshots, and system lines."""
    for msg in _jsonl._iter_messages(path):
        t = msg.get("type")
        if t not in ("user", "assistant"):
            continue
        content = (msg.get("message") or {}).get("content")
        if t == "user" and isinstance(content, str):
            if content.strip():
                yield ("user", content)
            continue
        if isinstance(content, list):
            for item in content:
                if (isinstance(item, dict) and item.get("type") == "text"
                        and item.get("text")):
                    yield (t, item["text"])


def _window(norm, start, end, width=SNIPPET_WIDTH):
    """Return (snippet, rel_start, rel_end): a ~width-char window of `norm`
    centred on [start:end], '…' where clipped, match offset within the snippet."""
    pad = max(0, (width - (end - start)) // 2)
    a = max(0, start - pad)
    b = min(len(norm), end + pad)
    prefix = "…" if a > 0 else ""
    suffix = "…" if b < len(norm) else ""
    snippet = prefix + norm[a:b] + suffix
    rs = len(prefix) + (start - a)
    return snippet, rs, rs + (end - start)


def search_transcript(path, needle):
    """Case-insensitive substring search over user/assistant text. Returns one
    hit per matching message (first match): {role, snippet, match_start,
    match_end}."""
    needle_l = (needle or "").lower()
    if not needle_l:
        return []
    hits = []
    for role, text in iter_text_messages(path):
        norm = _WS.sub(" ", text).strip()
        idx = norm.lower().find(needle_l)
        if idx == -1:
            continue
        snippet, rs, re_ = _window(norm, idx, idx + len(needle_l))
        hits.append({"role": role, "snippet": snippet,
                     "match_start": rs, "match_end": re_})
    return hits
```

(`jsonl._iter_messages` already swallows a missing file, so `search_transcript` on a nonexistent path returns `[]` naturally.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_search.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/search.py test/test_search.py
git commit -m "feat: search.py — transcript text extraction + substring hits with snippets"
```

---

### Task 2: `search.py` — per-project aggregation + result formatters

**Files:**
- Modify: `bin/_pkg/search.py`
- Test: `test/test_search.py`

**Interfaces:**
- Consumes: `search_transcript`, constants from Task 1; row dicts carry `name_cached`, `transcript_path`, `last_active_at`.
- Produces:
  - `search_project(rows, needle, *, include_unnamed, max_snippets=MAX_SNIPPETS_PER_SESSION, progress=None) -> list[dict]` — `rows` is an iterable of `(sid, s)` for ONE project. Returns result dicts `{"sid", "name", "last_active_at", "hit_count", "snippets", "overflow"}` for sessions with ≥1 hit, ordered by `last_active_at` descending. `progress(done, total)` (optional) is called after each scanned session.
  - `format_session(result, needle) -> str` — Rich markup for one result (header line + snippet lines, match reverse-highlighted).
  - `empty_state(needle, project_label, searched, include_unnamed) -> str` — Rich markup for the no-match case.

- [ ] **Step 1: Write the failing tests**

Append to `test/test_search.py`:

```python
def _row(sid, name, path, last="2026-01-01T00:00:00Z"):
    return (sid, {"name_cached": name, "transcript_path": path, "last_active_at": last})


def test_search_project_filters_orders_and_counts(tmp_path):
    a = _write(tmp_path, [{"type": "user", "message": {"content": "media-common tag"}},
                          {"type": "assistant", "message": {"content": [{"type": "text", "text": "media-common again"}]}}])
    b = _write(tmp_path, [{"type": "user", "message": {"content": "unrelated"}}])
    c = _write(tmp_path, [{"type": "user", "message": {"content": "media-common here"}}])
    rows = [
        _row("a", "alpha", a, "2026-01-01T00:00:00Z"),
        _row("b", "beta", b, "2026-03-01T00:00:00Z"),
        _row("c", "gamma", c, "2026-02-01T00:00:00Z"),
    ]
    res = search.search_project(rows, "media-common", include_unnamed=False)
    assert [r["sid"] for r in res] == ["c", "a"]      # b has no hit; c newer than a
    assert res[1]["hit_count"] == 2                    # 'a' matched twice


def test_search_project_include_unnamed_toggle(tmp_path):
    p = _write(tmp_path, [{"type": "user", "message": {"content": "media-common"}}])
    rows = [("u", {"name_cached": None, "transcript_path": p, "last_active_at": "2026-01-01T00:00:00Z"})]
    assert search.search_project(rows, "media-common", include_unnamed=False) == []
    got = search.search_project(rows, "media-common", include_unnamed=True)
    assert len(got) == 1 and got[0]["name"] == "(unnamed)"


def test_search_project_skips_missing_transcript(tmp_path):
    rows = [_row("x", "x", str(tmp_path / "gone.jsonl"))]
    assert search.search_project(rows, "media-common", include_unnamed=False) == []


def test_search_project_caps_snippets_with_overflow(tmp_path):
    lines = [{"type": "user", "message": {"content": f"media-common {i}"}} for i in range(9)]
    p = _write(tmp_path, lines)
    res = search.search_project([_row("a", "a", p)], "media-common",
                                include_unnamed=False, max_snippets=5)
    assert res[0]["hit_count"] == 9
    assert len(res[0]["snippets"]) == 5
    assert res[0]["overflow"] == 4


def test_search_project_progress_callback(tmp_path):
    p = _write(tmp_path, [{"type": "user", "message": {"content": "media-common"}}])
    seen = []
    search.search_project([_row("a", "a", p)], "media-common",
                          include_unnamed=False, progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 1)]


def test_format_session_highlights_match_and_shows_count():
    r = {"sid": "a", "name": "team/sprint14", "last_active_at": "2026-01-01T00:00:00Z",
         "hit_count": 2, "overflow": 0,
         "snippets": [{"role": "user", "snippet": "tag media-common now",
                       "match_start": 4, "match_end": 16}]}
    out = search.format_session(r, "media-common")
    assert "team/sprint14" in out
    assert "2 hits" in out
    assert "[reverse]media-common[/reverse]" in out


def test_format_session_escapes_markup_in_snippet():
    r = {"sid": "a", "name": "n", "last_active_at": "", "hit_count": 1, "overflow": 0,
         "snippets": [{"role": "assistant", "snippet": "see [red]media-common[/red]",
                       "match_start": 10, "match_end": 22}]}
    out = search.format_session(r, "media-common")
    assert "\\[red]" in out          # literal bracket escaped, not a real tag


def test_empty_state_names_project_and_toggle():
    out = search.empty_state("media-common", "myrepo", 12, include_unnamed=False)
    assert "media-common" in out and "myrepo" in out and "12" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_search.py -q`
Expected: FAIL — `AttributeError: module '_pkg.search' has no attribute 'search_project'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `bin/_pkg/search.py`:

```python
def search_project(rows, needle, *, include_unnamed,
                   max_snippets=MAX_SNIPPETS_PER_SESSION, progress=None):
    """Search every session in one project. `rows` is (sid, s) pairs. Returns
    result dicts for sessions with >=1 hit, newest first."""
    candidates = []
    for sid, s in rows:
        if not include_unnamed and not s.get("name_cached"):
            continue
        path = s.get("transcript_path")
        if not path or not os.path.exists(path):
            continue
        candidates.append((sid, s, path))
    total = len(candidates)
    results = []
    for i, (sid, s, path) in enumerate(candidates, 1):
        hits = search_transcript(path, needle)
        if hits:
            results.append({
                "sid": sid,
                "name": s.get("name_cached") or "(unnamed)",
                "last_active_at": s.get("last_active_at") or "",
                "hit_count": len(hits),
                "snippets": hits[:max_snippets],
                "overflow": max(0, len(hits) - max_snippets),
            })
        if progress is not None:
            progress(i, total)
    results.sort(key=lambda r: r["last_active_at"], reverse=True)
    return results


def _highlight(snippet, start, end):
    from rich.markup import escape
    return (escape(snippet[:start]) + "[reverse]" + escape(snippet[start:end])
            + "[/reverse]" + escape(snippet[end:]))


def format_session(result, needle):
    """Rich markup for one session result: bold header + indented snippets."""
    from rich.markup import escape
    hits = result["hit_count"]
    plural = "hit" if hits == 1 else "hits"
    when = (result.get("last_active_at") or "")[:10]
    head = f"[b]{escape(result['name'])}[/b]  [dim]· {hits} {plural}"
    if when:
        head += f" · {when}"
    head += "[/dim]"
    lines = [head]
    for h in result["snippets"]:
        who = "you" if h["role"] == "user" else "claude"
        body = _highlight(h["snippet"], h["match_start"], h["match_end"])
        lines.append(f"  [dim]{who}:[/dim] {body}")
    if result["overflow"]:
        lines.append(f"  [dim]+{result['overflow']} more[/dim]")
    return "\n".join(lines)


def empty_state(needle, project_label, searched, include_unnamed):
    from rich.markup import escape
    toggle = "on" if include_unnamed else "off"
    return (f"[dim]No matches for[/dim] '{escape(needle)}' [dim]in[/dim] "
            f"{escape(project_label)} [dim]({searched} sessions searched, "
            f"unnamed {toggle}).[/dim]")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_search.py -q`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/search.py test/test_search.py
git commit -m "feat: search.py — per-project aggregation, ordering, result formatters"
```

---

### Task 3: `SearchScreen` — modal UI with results OptionList and guarded worker

**Files:**
- Modify: `bin/_pkg/tui.py` (add `SearchScreen` class; add CSS entries)
- Test: `test/test_tui_search.py` (new)

**Interfaces:**
- Consumes: `search.search_project`, `search.format_session`, `search.empty_state`; module-level `_log_line`; Textual `ModalScreen`, `Input`, `OptionList`, `Option`, `Label`, `Vertical`, `VerticalScroll`; `rich.text.Text`.
- Produces: `SearchScreen(rows, project_label)` — a `ModalScreen[str | None]`. Dismisses with the chosen `sid` (str) on selection, or `None` on Esc. Constructor stores `rows` (all project `(sid, s)` pairs, named + unnamed) and `project_label`. Public attribute `include_unnamed: bool = False`.

**Placement:** add `SearchScreen` immediately after `HelpScreen` (around `tui.py:823`), before `class SessionExplorerApp`.

- [ ] **Step 1: Write the failing test**

Create `test/test_tui_search.py`:

```python
import json

import pytest

from _pkg import tui


def _seed_index(tmp_path):
    """One project 'repo' with two named sessions; only one mentions the needle."""
    proj = str(tmp_path / "repo")
    ta = tmp_path / "a.jsonl"
    ta.write_text(json.dumps({"type": "user", "message": {"content": "tag media-common"}}) + "\n")
    tb = tmp_path / "b.jsonl"
    tb.write_text(json.dumps({"type": "user", "message": {"content": "nothing here"}}) + "\n")
    idx = tmp_path / "se-index.json"
    idx.write_text(json.dumps({"version": 2, "sessions": {
        "a": {"name_cached": "alpha", "project_path": proj, "transcript_path": str(ta),
              "last_active_at": "2026-01-02T00:00:00Z"},
        "b": {"name_cached": "beta", "project_path": proj, "transcript_path": str(tb),
              "last_active_at": "2026-01-01T00:00:00Z"},
    }}))
    return str(idx), proj


@pytest.mark.asyncio
async def test_search_screen_lists_only_matching_sessions(tmp_path):
    idx, proj = _seed_index(tmp_path)
    rows = [("a", {"name_cached": "alpha", "transcript_path": str(tmp_path / "a.jsonl"),
                   "last_active_at": "2026-01-02T00:00:00Z"}),
            ("b", {"name_cached": "beta", "transcript_path": str(tmp_path / "b.jsonl"),
                   "last_active_at": "2026-01-01T00:00:00Z"})]
    app = tui.SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        screen = tui.SearchScreen(rows, "repo")
        await app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#search-input").value = "media-common"
        await screen._run_search()          # await the worker deterministically
        await pilot.pause()
        ol = screen.query_one("#search-results")
        # exactly one matching session, and its option id is the sid
        assert ol.option_count == 1
        assert ol.get_option_at_index(0).id == "a"
```

(Note: `_run_search` is written as an awaitable in Step 3 so the test can drive it without racing a background thread; the `f`-key + Enter path is covered in Task 4's pilot test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_tui_search.py -q`
Expected: FAIL — `AttributeError: module '_pkg.tui' has no attribute 'SearchScreen'`.

- [ ] **Step 3: Write the minimal implementation**

Add this class after `HelpScreen` in `bin/_pkg/tui.py`:

```python
class SearchScreen(ModalScreen[str | None]):
    """Live full-text search over one project's transcripts (spec 2026-07-01).
    Type a term, Enter searches, results are matching sessions with in-context
    snippets. Enter on a result dismisses with its sid; Esc cancels. ctrl+u
    toggles including unnamed sessions."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+u", "toggle_unnamed", "Incl. unnamed"),
    ]

    def __init__(self, rows, project_label):
        super().__init__()
        self._rows = list(rows)            # all (sid, s) for the project
        self._project_label = project_label
        self.include_unnamed = False

    def compose(self) -> ComposeResult:
        self._input = Input(placeholder=f"search {self._project_label}…",
                            id="search-input")
        self._status = Label("", id="search-status")
        self._results = OptionList(id="search-results")
        yield Vertical(self._input, self._status, self._results, id="search-panel")

    def on_mount(self) -> None:
        self._update_status_idle()
        self._input.focus()

    def _update_status_idle(self) -> None:
        toggle = "on" if self.include_unnamed else "off"
        self._status.update(f"[dim]Enter to search · ctrl+u unnamed: {toggle}[/dim]")

    def action_toggle_unnamed(self) -> None:
        self.include_unnamed = not self.include_unnamed
        if self._input.value.strip():
            self.run_worker(self._run_search())
        else:
            self._update_status_idle()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is self._input:
            self.run_worker(self._run_search())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is self._results and event.option.id:
            self.dismiss(event.option.id)

    async def _run_search(self) -> None:
        """Scan in a thread, then render on the UI thread. Awaitable so tests can
        drive it deterministically; guarded so a scan failure can't kill the app
        (see _summarize_tick / CLAUDE.md worker rule)."""
        from . import search as _search
        needle = self._input.value.strip()
        if not needle:
            return
        self._status.update("[dim]searching…[/dim]")

        def progress(done, total):
            self.app.call_from_thread(
                self._status.update, f"[dim]searched {done}/{total}[/dim]")

        def scan():
            return _search.search_project(
                self._rows, needle, include_unnamed=self.include_unnamed,
                progress=progress)

        try:
            results = await self.run_worker(scan, thread=True).wait()
        except Exception:
            import traceback
            _log_line("conversation search failed (skipped):\n" + traceback.format_exc())
            self._status.update("[dim]search failed (see ~/.claude/session-explorer.log)[/dim]")
            return
        self._render(needle, results)

    def _render(self, needle, results) -> None:
        from . import search as _search
        from rich.text import Text
        self._results.clear_options()
        searched = sum(
            1 for _sid, s in self._rows
            if (self.include_unnamed or s.get("name_cached")))
        if not results:
            self._status.update(
                _search.empty_state(needle, self._project_label, searched,
                                    self.include_unnamed))
            return
        for r in results:
            markup = _search.format_session(r, needle)
            self._results.add_option(Option(Text.from_markup(markup), id=r["sid"]))
        toggle = "on" if self.include_unnamed else "off"
        self._status.update(
            f"[dim]{len(results)} sessions · {searched} searched · "
            f"ctrl+u unnamed: {toggle}[/dim]")
        self._results.focus()
```

Add the imports at the top of `tui.py` if missing: `Option` comes from `from textual.widgets.option_list import Option` — add that import near the other `textual.widgets` imports (line 19). `OptionList`, `Input`, `Label`, `Vertical`, `VerticalScroll`, `Binding`, `ModalScreen`, `ComposeResult` are already imported.

Add CSS to `SessionExplorerApp.CSS` (after the `#help` block, ~line 835):

```css
    SearchScreen { align: center middle; }
    #search-panel { width: 90; max-width: 95%; height: auto; max-height: 90%;
                    padding: 1 2; border: round $accent; background: $surface; }
    #search-results { height: auto; max-height: 80%; }
    #search-status { color: $text-muted; padding: 0 0 1 0; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_tui_search.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_search.py
git commit -m "feat: SearchScreen modal — input, results OptionList, guarded scan worker"
```

---

### Task 4: App wiring — `f` binding, `action_search`, `_rows_for_project`, selection, help/check_action sync

**Files:**
- Modify: `bin/_pkg/tui.py` (BINDINGS, `check_action`, `_help_text`, add `action_search` + `_rows_for_project`)
- Test: `test/test_tui_search.py`

**Interfaces:**
- Consumes: `SearchScreen` (Task 3); `tree_model.session_root(s)`; `_index.load`; `_project_and_prefix_for_cursor()` → `(project_root, prefix)`; `_pending_select_sid` + `_populate()` selection machinery; `self._view_mode`.
- Produces: `action_search()` (bound to `f`); `_rows_for_project(project_root) -> list[tuple[str, dict]]` (all rows for that root, named + unnamed).

- [ ] **Step 1: Write the failing tests**

Append to `test/test_tui_search.py`:

```python
def test_rows_for_project_groups_by_repo_root(tmp_path):
    idx, proj = _seed_index(tmp_path)
    # add a worktree session of the same repo + a session of a different repo
    import json as _json
    data = _json.loads((tmp_path / "se-index.json").read_text())
    data["sessions"]["wt"] = {"name_cached": "wt", "project_path": proj + "/.claude/worktrees/x",
                              "transcript_path": str(tmp_path / "a.jsonl"), "last_active_at": "z"}
    data["sessions"]["other"] = {"name_cached": "o", "project_path": str(tmp_path / "elsewhere"),
                                 "transcript_path": str(tmp_path / "a.jsonl"), "last_active_at": "z"}
    (tmp_path / "se-index.json").write_text(_json.dumps(data))
    app = tui.SessionExplorerApp(index_path=idx)
    rows = app._rows_for_project(proj)
    sids = {sid for sid, _ in rows}
    assert sids == {"a", "b", "wt"}          # worktree collapses in; other repo excluded


@pytest.mark.asyncio
async def test_f_key_opens_search_and_selection_moves_cursor(tmp_path):
    idx, proj = _seed_index(tmp_path)
    app = tui.SessionExplorerApp(index_path=idx)
    async with app.run_test() as pilot:
        app._scanned = True
        app._populate()
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, tui.SearchScreen)
        screen = app.screen
        screen.query_one("#search-input").value = "media-common"
        await screen._run_search()
        await pilot.pause()
        screen.dismiss("a")                  # simulate picking session 'a'
        await pilot.pause()
        node = app._tree.cursor_node
        assert node is not None and (node.data or {}).get("sid") == "a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test/test_tui_search.py -q`
Expected: FAIL — `AttributeError: 'SessionExplorerApp' object has no attribute '_rows_for_project'`.

- [ ] **Step 3: Write the minimal implementation**

Add the binding to `SessionExplorerApp.BINDINGS` (after the `slash` filter binding, ~line 854):

```python
        Binding("f", "search", "Search"),
```

Add `"search"` to the two guards in `check_action` — the modal-open list (~line 937) and the filter-focus list (~line 941):

```python
        if action in ("resume", "rename", "move", "new_folder", "new_session", "delete", "notes", "update_summary", "preview", "close_preview", "filter", "search", "cycle_view", "toggle_collapse", "toggle_usage", "rescan", "help", "expand_node", "collapse_node", "quit", "toggle_queues", "resource_setup", "settings") and isinstance(self.screen, ModalScreen):
            return False
        if action in ("quit", "toggle_queues", "search") and getattr(self, "_filter", None) is not None and self._filter.has_focus:
            return False
```

Add the two methods (place `action_search` next to `action_filter`, ~line 2673, and `_rows_for_project` near `_project_and_prefix_for_cursor`, ~line 1931):

```python
    def _rows_for_project(self, project_root):
        """All (sid, s) rows whose repo root == project_root (worktrees collapse
        in), named and unnamed alike — SearchScreen applies the unnamed filter."""
        from . import tree_model as _tm
        data = _index.load(self._index_path).get("sessions", {})
        return [(sid, s) for sid, s in data.items()
                if _tm.session_root(s) == project_root]
```

```python
    def action_search(self) -> None:
        project, _ = self._project_and_prefix_for_cursor()
        if not project:
            self.bell(); return
        rows = self._rows_for_project(project)
        label = os.path.basename(project) or project

        def after(sid: "str | None") -> None:
            if not sid:
                return
            data = _index.load(self._index_path).get("sessions", {})
            s = data.get(sid) or {}
            # Reveal unnamed rows if the pick is unnamed and the tree hides them,
            # so _populate's pending-select can actually land the cursor.
            if not s.get("name_cached") and self._view_mode == 0:
                self._view_mode = 2
            self._pending_select_sid = sid
            self._populate()

        self.push_screen(SearchScreen(rows, label), after)
```

Add a help line in `_help_text` (in the keybinding list; put it near the "What you see" or navigation section). Find the block that lists keys and add:

```python
        "[b]Search[/]",
        "Press [b]f[/] to full-text search the current project's conversations —",
        "it reads the transcripts and lists sessions whose messages match, with",
        "snippets. [b]ctrl+u[/] includes unnamed sessions. (The [b]/[/] filter still",
        "matches names, notes, the first prompt and summaries only.)",
        "",
```

(Place this block after the "What you see" section so the `/` vs `f` distinction reads together. If `_help_text` uses the `key(k, desc)` helper for a compact key list elsewhere, also add `key("f", "Full-text search current project")` there to keep the list complete.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test/test_tui_search.py -q`
Expected: PASS (all 4 tests in the file).

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest test/ -q`
Expected: PASS (no regressions). If a keybinding-count or help-text snapshot test exists, update it to include `f`.

- [ ] **Step 6: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui_search.py
git commit -m "feat: wire f=search — action_search, _rows_for_project, help + check_action sync"
```

---

### Task 5: Docs, SPEC, and release

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md`, `README.md`, `CHANGELOG.md`, `bin/_pkg/__init__.py`, `.claude-plugin/plugin.json`

**Interfaces:** none (docs + version).

- [ ] **Step 1: Update SPEC.md**

Add a subsection under the TUI behavior section describing `f`: live full-text scan of the *current project's* transcripts (user+Claude message text only), results as sessions-with-snippets in a modal `OptionList`, `ctrl+u` toggles unnamed (default off), Enter moves the tree cursor to the pick (no resume), no sidecar/index, `/` unchanged. Add a one-line load-bearing note mirroring the spec's Global Constraints (live scan, current-project scope, `search.py` is Textual-free).

- [ ] **Step 2: Update CLAUDE.md**

Add a load-bearing bullet: "**Full-text search (`f`) is a live scan, not an index.** `search.py` (Textual-free) reads the current project's JSONLs at search time — no fourth sidecar. `/` remains the metadata-only filter; don't fold body search into `_matches`. Keep `f` in sync across `BINDINGS`, `check_action`, `_help_text`."

- [ ] **Step 3: Update README.md**

Add `f` to the keybindings list/table and a sentence in the feature overview ("search across your past conversations, not just their names").

- [ ] **Step 4: Bump version + CHANGELOG**

Bump `bin/_pkg/__init__.py` `__version__` and `.claude-plugin/plugin.json` `version` from `1.18.3` to **`1.19.0`** (minor — new feature). Add a `CHANGELOG.md` section:

```markdown
## v1.19.0

- **Full-text conversation search (`f`).** Press `f` to search the current
  project's transcripts by content — lists sessions whose you/Claude messages
  match, with in-context snippets. `ctrl+u` includes unnamed sessions. Live
  scan, no index; the `/` filter (names/notes/first-prompt/summary) is unchanged.
```

- [ ] **Step 5: Run the full suite once more**

Run: `python3 -m pytest test/ -q`
Expected: PASS.

- [ ] **Step 6: Commit, then release via the skill**

```bash
git add SPEC.md CLAUDE.md README.md CHANGELOG.md bin/_pkg/__init__.py .claude-plugin/plugin.json
git commit -m "docs: full-text search + v1.19.0"
```

Then follow the **`cutting-a-release`** skill for the tag/GitHub release (`gh release create v1.19.0`). Per the phased-delivery rule, open **one PR** for the whole branch and cut **one** version bump at the end — do not bump mid-way.

---

## Self-Review

**Spec coverage:**
- Result view = snippets in context grouped by session → Task 2 `format_session` + Task 3 `SearchScreen` OptionList. ✓
- Scope = current project + include-unnamed toggle → Task 4 `_rows_for_project` / `action_search`; Task 3 `action_toggle_unnamed`, default off. ✓
- Content = you+Claude messages, skip tool/thinking → Task 1 `iter_text_messages`. ✓
- Mechanism = live scan, guarded `@work` thread, progress line → Task 3 `_run_search` (guarded, `progress` callback + status). ✓
- Key = `f`, `/` unchanged, keys in sync → Task 4 BINDINGS/check_action/_help_text. ✓
- Non-destructive selection → Task 4 `after` uses `_pending_select_sid` (no resume). ✓
- Testing (extraction, matching, snippet, cap, ordering, unnamed, dangling path, row grouping) → Task 1/2/4 tests. ✓
- Docs/release (SPEC/CLAUDE/README/CHANGELOG/bump, one PR/one bump) → Task 5. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; every command has expected output. ✓

**Type consistency:** `search_project` returns dicts with `sid/name/last_active_at/hit_count/snippets/overflow`; `format_session` consumes exactly those. Hit dicts (`role/snippet/match_start/match_end`) produced by `search_transcript`, consumed by `format_session._highlight`. `SearchScreen(rows, project_label)` matches `action_search`'s call. `_rows_for_project(project_root)` returns `(sid, s)` pairs consumed by `SearchScreen`/`search_project`. ✓
