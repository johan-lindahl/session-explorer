"""Neutralising USER text before it goes into a markup template.

Textual-free (like `search.py`/`gc.py`/`root_guard.py`) so both the TUI and the
Textual-free helpers can share exactly one escape rule.

Why not `rich.markup.escape` / `textual.markup.escape`: both only escape tags
that START with `[a-z#/@]`, so they let `[ADMIN]` through. Rich's own parser
agrees (it also ignores `[ADMIN]`), but **Textual's does not** — `Content`
markup treats any token in brackets as a tag. Since the same strings are parsed
by both engines here (the preview pane goes through `Static.update` → Textual
*and* through `Text.from_markup` in the live preview), the rich-only escape is
wrong on half the call sites, where it silently swallows the user's text.

Escaping *every* `[` is safe for both: `\\[` is unescaped back to a literal `[`
by Rich and Textual alike, so this is lossless in both directions.
"""


def escape(text: str) -> str:
    """Return `text` so a markup parser renders it verbatim.

    Load-bearing, not cosmetic. A `[` run the parser cannot tokenize is emitted
    as *literal text*, which swallows the `]` of the next real tag and leaves
    the template's trailing `[/]`s with nothing to close — `MarkupError` out of
    `Static.update`, raised inside an interval callback, killing the app. That
    is exactly how a session named `feature/51010-[ADMIN] …` took down the
    explorer (v1.19.4).

    Escape LAST, after any truncation or column padding: the added backslashes
    render as zero cells, so a width computed on the raw text still lines up,
    and truncating already-escaped text could cut a `\\[` in half.
    """
    return text.replace("[", "\\[")
