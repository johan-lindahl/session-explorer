"""Session names (and notes/summaries/prompts) are user text, not markup.

Regression guard for the v1.19.4 crash: a session named
`feature/51010-[ADMIN] Extract a shared SSO base module …` was interpolated
straight into the Queues pane's markup string. `_trunc` cut it to
`feature/51010-[ADMI…`, and Textual's parser treats a `[` run it can't
tokenize as *literal text* — swallowing the `]` of the NEXT real tag
(`[dim]`), so the template's trailing `[/]`s had nothing to close and
`Static.update` raised `MarkupError` from inside the `_poll_live` interval
callback, killing the app.

Two parsers matter and they disagree, so both are asserted here:
  * Textual `Content.from_markup` — the preview/queues `Static.update` path.
    Treats `[ADMIN]` as a tag.
  * Rich `Text.from_markup` — the Tree-label and live-preview path. Treats
    only `[a-z#/@]`-initial tags, but still chokes on a bare `[/]`.
"""

import os

import pytest

# Import _pkg.tui BEFORE textual/rich: _pkg/__init__ puts the vendored Textual
# on sys.path (see test_tui_queue for the full note). Order matters.
from _pkg.tui import _esc, _preview_text, _render_queue_rows, _row_label
from rich.text import Text
from textual.content import Content

# The name that actually crashed the explorer, plus the shapes most likely to
# recur: a bare auto-close, a real style tag, a path-in-brackets, LLM prose.
NASTY = [
    "feature/51010-[ADMIN] Extract a shared SSO base module (Royal_Sso) for admin",
    "feature/51010-[ADMI…",
    "[/]",
    "[dim]sneaky",
    "a[b/c]d",
    "[Image #3]",
    "fix [the login bug](https://example/1)",
    "arr[0][1]",
]


def _both(markup: str) -> None:
    """Parse with both engines; either raising is the regression."""
    Content.from_markup(markup)
    Text.from_markup(markup)


@pytest.mark.parametrize("raw", NASTY)
def test_esc_round_trips_through_both_parsers(raw):
    esc = _esc(raw)
    assert Content.from_markup("[b]" + esc + "[/] TAIL").plain == raw + " TAIL"
    assert Text.from_markup("[b]" + esc + "[/b] TAIL").plain == raw + " TAIL"


def _queue_rows(name):
    return [{
        "project": "/Volumes/Projects/RoyalUnibrew/magento2",
        "resource": "root",
        "live_root_block": None,
        "holder": {"sid": "00e17817", "name": name, "elapsed": "0:00"},
        "waiting": [{"name": name, "pos": "2nd"}],
        "active": True,
    }]


@pytest.mark.parametrize("raw", NASTY)
def test_queue_pane_survives_bracketed_session_name(raw):
    """The exact crash: holder + waiting names go into the pane's markup."""
    _both(_render_queue_rows(_queue_rows(raw)))


@pytest.mark.parametrize("raw", NASTY)
def test_queue_pane_blocked_row_survives_bracketed_name(raw):
    rows = _queue_rows(raw)
    rows[0]["holder"] = None
    rows[0]["live_root_block"] = {"name": raw}
    _both(_render_queue_rows(rows))


def test_queue_pane_still_shows_the_name_text():
    out = Content.from_markup(_render_queue_rows(_queue_rows("[ADMIN] ship it"))).plain
    assert "[ADMIN] ship" in out


PREVIEW_FIELDS = ["name_cached", "notes", "summary", "first_prompt", "branch",
                  "project_path", "transcript_path", "last_launch_error", "model"]


@pytest.mark.parametrize("raw", NASTY)
@pytest.mark.parametrize("field", PREVIEW_FIELDS)
def test_preview_survives_bracketed_field(field, raw):
    """_preview_text feeds BOTH parsers (Static.update and the live preview's
    Text.from_markup), so both must accept every field."""
    s = {"sid": "00e17817-0031-41fb-9165-b785b56a126b", "name_cached": "plain",
         field: raw}
    _both(_preview_text(s))


def test_preview_still_shows_the_bracketed_name():
    s = {"sid": "00e17817", "name_cached": "[ADMIN] ship it"}
    assert "[ADMIN] ship it" in Content.from_markup(_preview_text(s)).plain


@pytest.mark.parametrize("raw", NASTY)
def test_tree_row_label_survives_bracketed_name_and_prompt(raw):
    """Tree labels go through Rich's parser (Tree.process_label)."""
    Text.from_markup(_row_label("00e17817", {"name_cached": raw,
                                             "first_prompt": raw}, 2))


def test_row_label_columns_stay_aligned_when_escaped():
    """Escaping adds backslashes that render as nothing — the stat columns must
    line up with an unbracketed row of the same visible length."""
    plain = _row_label("00e17817", {"name_cached": "abcdefgh", "first_prompt": ""}, 2)
    nasty = _row_label("00e17817", {"name_cached": "ab[cd]fgh", "first_prompt": ""}, 2)
    assert len(Text.from_markup(nasty).plain) == len(Text.from_markup(plain).plain)


# --- search.py -------------------------------------------------------------
# search.py feeds BOTH engines: format_session -> OptionList (Rich), while
# empty_state -> _status.update and format_match_block -> preview pane are
# Textual. It used to escape with `rich.markup.escape`, which ignores
# `[ADMIN]` — on the Textual side the user's own search term was silently
# swallowed ("No matches for ''").

SNIPPETS = [{"role": "user", "snippet": "see [ADMIN] and [/] here",
             "match_start": 4, "match_end": 11}]


@pytest.mark.parametrize("raw", NASTY)
def test_search_empty_state_survives_and_shows_the_needle(raw):
    from _pkg import search as _search
    m = _search.empty_state(raw, "magento2", 5, False)
    _both(m)
    assert raw in Content.from_markup(m).plain
    assert raw in Text.from_markup(m).plain


@pytest.mark.parametrize("raw", NASTY)
def test_search_match_block_survives_bracketed_needle(raw):
    from _pkg import search as _search
    m = _search.format_match_block(raw, SNIPPETS)
    _both(m)
    assert raw in Content.from_markup(m).plain


@pytest.mark.parametrize("raw", NASTY)
def test_search_result_card_survives_bracketed_name(raw):
    from _pkg import search as _search
    m = _search.format_session(
        {"name": raw, "hit_count": 1, "last_active_at": "2026-09-22",
         "snippets": SNIPPETS, "overflow": 0}, raw)
    _both(m)
    assert raw in Text.from_markup(m).plain


def test_search_snippet_text_is_not_swallowed():
    """The bracketed run inside a transcript snippet must reach the screen."""
    from _pkg import search as _search
    m = _search.format_match_block("x", SNIPPETS)
    assert "see [ADMIN] and [/] here" in Content.from_markup(m).plain


def test_search_uses_the_shared_escape_not_richs():
    """rich.markup.escape ignores `[ADMIN]`, which Textual parses as a tag."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "bin", "_pkg",
                            "search.py")).read()
    assert "from rich.markup import escape" not in src
    assert "from .markup import escape" in src
