# M2 — Textual TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Textual-based TUI that turns `session-explorer launch` into a real file-explorer-style session manager — tree view with stats columns, rename/move/delete/notes operations, preview pane, filter, Linux launchers.

**Architecture:** A new module `bin/_pkg/tui.py` defines a Textual `App` that renders the index as a tree (`project → folder → session`). Operations call into existing `bin/_pkg/index.py` + a new `bin/_pkg/rename.py` that appends `custom-title` events to the JSONL (the format Claude Code's own `/rename` writes). Textual and its deps are vendored under `bin/_pkg/_vendor/` so install paths stay zero-`pip`. The CLI's `launch` subcommand is repointed from `list` to the TUI; `launcher.py` grows Linux probing.

**Tech Stack:** Python 3.11+, Textual (vendored), stdlib only otherwise. Tests: pytest for pure logic + Textual's `Pilot` for one happy-path UI test. macOS is the dogfood target; Linux launchers land in this milestone.

---

## File map

**New:**
- `bin/_pkg/_vendor/` — vendored Textual + transitive deps (committed to git).
- `bin/_pkg/tui.py` — `SessionExplorerApp(App)`, screens, dialogs.
- `bin/_pkg/tree_model.py` — pure function `build_tree(index_data) -> ProjectsTree`. Folder/name parsing + grouping. Used by both `tui.py` and `cli.py`.
- `bin/_pkg/format.py` — `fmt_tokens`, `fmt_age`, `fmt_pct`. Used by both `tui.py` and `cli.py`.
- `bin/_pkg/rename.py` — `append_custom_title(transcript_path, session_id, new_name)`; flock + line-append.
- `bin/_pkg/notes.py` — `set_notes(index_path, session_id, notes)`; thin index mutate wrapper.
- `bin/_pkg/folders.py` — `add_folder(index_path, name)`, `remove_folder(index_path, name)`.
- `bin/_pkg/delete.py` — `delete_session(index_path, session_id)` — removes JSONL + index entry.
- `test/test_tree_model.py`, `test/test_format.py`, `test/test_rename.py`, `test/test_notes.py`, `test/test_folders.py`, `test/test_delete.py`, `test/test_tui.py`.
- `test/fixtures/renamed.jsonl` — a synthetic JSONL containing one `custom-title` line in the verified shape (no real PII).

**Modified:**
- `bin/_pkg/__init__.py` — prepend `_vendor` to `sys.path` so `import textual` resolves.
- `bin/_pkg/cli.py` — repoint `_cmd_launch` from `list` to the TUI invocation; replace local `_split_folder`/`_fmt_*` with imports from the new modules.
- `bin/_pkg/launcher.py` — Linux probing per spec (`$TERMINAL` → `x-terminal-emulator` → known emulators).
- `commands/open.md` — no change expected; the binary contract is unchanged.
- `.gitignore` — explicit allow for `bin/_pkg/_vendor/` (in case sibling tooling ignores `_vendor` patterns); add `*.dist-info/` exclusion within `_vendor` if needed for size.
- `README.md` — TUI section (keybindings + screenshot caveat).

**Touched by tests only:**
- `test/conftest.py` — extend with a Textual app-under-test fixture if `Pilot` needs setup.

---

## Pre-flight: Branch + worktree

**Already running in a worktree?** Skip. Otherwise create one via `superpowers:using-git-worktrees` before Task 1.

---

### Task 1: Vendor Textual under `bin/_pkg/_vendor/`

**Files:**
- Create: `bin/_pkg/_vendor/` (populated by pip)
- Modify: `bin/_pkg/__init__.py`
- Test: `test/test_vendor_import.py`

- [ ] **Step 1: Pip-install Textual into the vendor dir**

Run from repo root:

```bash
python3 -m pip install --target bin/_pkg/_vendor --no-compile --upgrade textual
```

Expected: creates `bin/_pkg/_vendor/textual/`, `rich/`, `markdown_it/`, `mdurl/`, `pygments/`, `linkify_it/`, `uc_micro/`, `platformdirs/`, plus a handful of `*.dist-info/` dirs.

- [ ] **Step 2: Prune metadata noise**

```bash
find bin/_pkg/_vendor -type d -name '__pycache__' -prune -exec rm -rf {} +
find bin/_pkg/_vendor -type d -name 'tests' -prune -exec rm -rf {} +
```

Leave `*.dist-info/` in place — Textual reads its own version at import time and some entry-points lookups depend on it. We only delete pyc caches and packaged test suites.

- [ ] **Step 3: Add `_vendor` to `sys.path` in the package `__init__`**

Replace `bin/_pkg/__init__.py` with:

```python
"""session-explorer package."""

import os
import sys

__version__ = "0.1.0"

_VENDOR = os.path.join(os.path.dirname(__file__), "_vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    # Append, don't prepend — never shadow a user's site-packages.
    sys.path.append(_VENDOR)
```

- [ ] **Step 4: Write the failing import test**

Create `test/test_vendor_import.py`:

```python
def test_textual_imports_from_vendor():
    import _pkg  # noqa: F401  triggers sys.path injection
    import textual  # noqa: F401
    from textual.app import App  # noqa: F401
```

Run: `pytest test/test_vendor_import.py -v`
Expected: PASS.

- [ ] **Step 5: Commit the vendor tree and bootstrap**

```bash
git add bin/_pkg/_vendor bin/_pkg/__init__.py test/test_vendor_import.py
git commit -m "M2: vendor Textual under bin/_pkg/_vendor + sys.path bootstrap"
```

(The vendor tree is several MB. That's the design choice in SPEC.md — "No `pip install` runs on either install path".)

---

### Task 2: Inspect Claude's `custom-title` line shape

**Files:**
- Create: `test/fixtures/renamed.jsonl`
- Modify: `bin/_pkg/jsonl.py` (header comment only — append the verified envelope shape)

- [ ] **Step 1: Locate a real renamed transcript**

```bash
grep -l '"type":"custom-title"' ~/.claude/projects/*/*.jsonl | head -1
```

If none exists, rename a throwaway session by running `claude` and `/rename m2-rename-probe`, then re-grep.

Expected: a path like `/Users/<you>/.claude/projects/<encoded>/abcd…uuid.jsonl`.

- [ ] **Step 2: Dump the line and record its envelope**

```bash
grep '"type":"custom-title"' "<path-from-step-1>" | head -1 | python3 -m json.tool
```

Expected output (illustrative — fields will vary):

```json
{
  "type": "custom-title",
  "customTitle": "m2-rename-probe",
  "sessionId": "abcd...",
  "uuid": "...",
  "parentUuid": "...",
  "timestamp": "2026-05-27T..."
}
```

Record the *exact* set of fields Claude writes. Note which are required (`type`, `customTitle`, `sessionId`) vs envelope (`uuid`, `parentUuid`, `timestamp`).

- [ ] **Step 3: Update the docstring in `bin/_pkg/jsonl.py`**

Find the `RENAME SERIALIZATION` block (lines 55-60) and replace it with:

```
RENAME SERIALIZATION (verified 2026-05-27 against a real renamed transcript):
  Line shape:
    {"type":"custom-title",
     "customTitle":"<user label>",
     "sessionId":"<uuid>",
     "uuid":"<line-uuid>",
     "parentUuid":"<previous-line-uuid-or-null>",
     "timestamp":"<ISO8601>"}
  - type, customTitle, sessionId are REQUIRED.
  - uuid is a fresh UUIDv4 per line.
  - parentUuid points at the prior line's uuid (or null if first).
  - timestamp is ISO8601 with timezone offset.
  The LAST custom-title in the file wins (later supersedes earlier).
```

(If your inspection in Step 2 shows additional required envelope fields, add them here.)

- [ ] **Step 4: Write a sanitized fixture**

Create `test/fixtures/renamed.jsonl` containing one synthetic line that mirrors the verified envelope (use placeholder UUIDs/timestamps — no PII):

```jsonl
{"type":"custom-title","customTitle":"planning-sprint14","sessionId":"01HXYZTESTSESSION","uuid":"01HXYZTESTLINE0001","parentUuid":null,"timestamp":"2026-05-27T10:00:00Z"}
```

- [ ] **Step 5: Verify `session_name` reads the fixture**

```bash
pytest test/test_jsonl.py -v
```

Expected: existing tests still PASS. (We aren't adding logic yet — just confirming the fixture round-trips.)

- [ ] **Step 6: Commit**

```bash
git add bin/_pkg/jsonl.py test/fixtures/renamed.jsonl
git commit -m "M2: verify and document Claude's custom-title JSONL shape"
```

---

### Task 3: `format.py` — extract `fmt_tokens`, `fmt_age`, `fmt_pct`

**Files:**
- Create: `bin/_pkg/format.py`
- Modify: `bin/_pkg/cli.py` (replace local helpers with imports)
- Test: `test/test_format.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_format.py`:

```python
from _pkg.format import fmt_tokens, fmt_age, fmt_pct
from datetime import datetime, timezone, timedelta


def test_fmt_tokens_small():
    assert fmt_tokens(0) == "~0"
    assert fmt_tokens(999) == "~999"


def test_fmt_tokens_thousands():
    assert fmt_tokens(10_000) == "~10K"
    assert fmt_tokens(127_456) == "~127K"


def test_fmt_age_none():
    assert fmt_age(None) == "—"


def test_fmt_age_minutes():
    iso = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat()
    assert fmt_age(iso) == "12m"


def test_fmt_age_hours():
    iso = (datetime.now(timezone.utc) - timedelta(hours=2, minutes=5)).isoformat()
    assert fmt_age(iso) == "2h"


def test_fmt_age_days():
    iso = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert fmt_age(iso) == "5d"


def test_fmt_pct():
    assert fmt_pct(19) == "(19%)"
    assert fmt_pct(0) == "(0%)"
    assert fmt_pct(100) == "(100%)"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest test/test_format.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named '_pkg.format'`.

- [ ] **Step 3: Implement `bin/_pkg/format.py`**

```python
"""Pure-Python display formatters shared by the CLI text mode and the TUI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def fmt_tokens(n: int) -> str:
    if n >= 10_000:
        return f"~{n // 1000}K"
    return f"~{n}"


def fmt_age(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    delta = datetime.now(timezone.utc) - ts
    if delta.days >= 1:
        return f"{delta.days}d"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours}h"
    return f"{delta.seconds // 60}m"


def fmt_pct(pct: int) -> str:
    return f"({pct}%)"
```

- [ ] **Step 4: Run tests — verify PASS**

```bash
pytest test/test_format.py -v
```

Expected: all PASS.

- [ ] **Step 5: Repoint `cli.py` to the shared module**

In `bin/_pkg/cli.py`, delete the local `_fmt_tokens` and `_fmt_age` definitions and replace their call sites:

```python
from .format import fmt_tokens, fmt_age, fmt_pct
```

And in `_cmd_list`, change `f"{tokens:>6} ({pct:>3}%)"` to `f"{tokens:>6} {fmt_pct(pct):>5}"` (or keep the inline formatter — keep the diff small if the layout matches).

- [ ] **Step 6: Run the full suite — verify PASS**

```bash
pytest -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/format.py bin/_pkg/cli.py test/test_format.py
git commit -m "M2: extract format helpers into _pkg.format"
```

---

### Task 4: `tree_model.py` — pure tree builder

**Files:**
- Create: `bin/_pkg/tree_model.py`
- Test: `test/test_tree_model.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_tree_model.py`:

```python
from _pkg.tree_model import split_folder, build_tree


def test_split_folder_empty():
    assert split_folder(None) == ("", "")
    assert split_folder("") == ("", "")


def test_split_folder_no_dash():
    assert split_folder("sprint14") == ("", "sprint14")


def test_split_folder_one_dash():
    assert split_folder("planning-sprint14") == ("planning", "sprint14")


def test_split_folder_many_dashes():
    assert split_folder("audits-q1-review-final") == ("audits", "q1-review-final")


def _idx(sessions, folders=()):
    return {"version": 1, "folders": list(folders), "sessions": sessions}


def test_build_tree_groups_by_project_then_folder():
    data = _idx({
        "a": {"project_label": "acme-api", "name_cached": "refactors-checkout",
              "last_active_at": "2026-05-27T10:00:00Z"},
        "b": {"project_label": "acme-api", "name_cached": "refactors-cart",
              "last_active_at": "2026-05-26T10:00:00Z"},
        "c": {"project_label": "acme-web", "name_cached": "planning-sprint14",
              "last_active_at": "2026-05-27T09:00:00Z"},
    })
    tree = build_tree(data)
    assert sorted(tree.keys()) == ["acme-api", "acme-web"]
    acme_api = tree["acme-api"]
    assert "refactors" in acme_api
    assert {sid for sid, _ in acme_api["refactors"]} == {"a", "b"}
    # within a folder, newest first
    assert acme_api["refactors"][0][0] == "a"


def test_build_tree_unnamed_lands_in_unnamed_bucket():
    data = _idx({
        "u1": {"project_label": "proj", "name_cached": None,
               "last_active_at": "2026-05-27T10:00:00Z"},
    })
    tree = build_tree(data)
    assert "(unnamed)" in tree["proj"]
    assert tree["proj"]["(unnamed)"][0][0] == "u1"


def test_build_tree_no_folder_lands_in_no_folder_bucket():
    data = _idx({
        "n1": {"project_label": "proj", "name_cached": "sprint14",
               "last_active_at": "2026-05-27T10:00:00Z"},
    })
    tree = build_tree(data)
    assert "" in tree["proj"]
    assert tree["proj"][""][0][0] == "n1"


def test_build_tree_includes_empty_folders():
    data = _idx({}, folders=["audits/empty-shelf"])
    # Empty folders aren't tied to a project — they live under a synthetic "(unfiled)" project.
    tree = build_tree(data)
    assert "(unfiled)" in tree
    assert "audits/empty-shelf" in tree["(unfiled)"]
    assert tree["(unfiled)"]["audits/empty-shelf"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest test/test_tree_model.py -v
```

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `bin/_pkg/tree_model.py`**

```python
"""Pure tree-building from a loaded index. No I/O, no Textual.

Layout:
  tree: dict[project_label, dict[folder_label, list[(sid, session_dict)]]]

Folder labels:
  ""           — ungrouped (session has a name but no dash)
  "(unnamed)"  — session has no name_cached at all
  "(unfiled)"  — synthetic project bucket holding pre-created empty folders
  any other    — first-dash folder prefix
"""

from __future__ import annotations

from typing import Dict, List, Tuple

ProjectsTree = Dict[str, Dict[str, List[Tuple[str, dict]]]]


def split_folder(name: "str | None") -> Tuple[str, str]:
    """First-dash split. ('', name) when no dash; ('', '') when no name."""
    if not name:
        return ("", "")
    if "-" not in name:
        return ("", name)
    folder, _, display = name.partition("-")
    return (folder, display)


def build_tree(index_data: dict) -> ProjectsTree:
    tree: ProjectsTree = {}
    for sid, s in index_data.get("sessions", {}).items():
        project = s.get("project_label") or "(unknown)"
        name = s.get("name_cached")
        if not name:
            folder = "(unnamed)"
        else:
            folder, _ = split_folder(name)
        tree.setdefault(project, {}).setdefault(folder, []).append((sid, s))

    # Sort each folder's sessions by last_active_at desc.
    for project in tree:
        for folder in tree[project]:
            tree[project][folder].sort(
                key=lambda x: x[1].get("last_active_at", ""), reverse=True
            )

    # Empty folders live under a synthetic "(unfiled)" project bucket.
    empty_folders = index_data.get("folders") or []
    if empty_folders:
        tree.setdefault("(unfiled)", {})
        for f in empty_folders:
            tree["(unfiled)"].setdefault(f, [])

    return tree
```

- [ ] **Step 4: Run tests — verify PASS**

```bash
pytest test/test_tree_model.py -v
```

Expected: all PASS.

- [ ] **Step 5: Repoint `cli.py` `_split_folder` to the shared one**

In `bin/_pkg/cli.py`, delete the local `_split_folder` and import from `.tree_model`:

```python
from .tree_model import split_folder
```

Update its call sites (`_split_folder(...)` → `split_folder(...)`).

- [ ] **Step 6: Run the full suite — verify PASS**

```bash
pytest -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/tree_model.py bin/_pkg/cli.py test/test_tree_model.py
git commit -m "M2: extract tree builder + folder split into _pkg.tree_model"
```

---

### Task 5: `rename.py` — append `custom-title` line to JSONL

**Note (verified in Task 2):** Claude writes `custom-title` lines with EXACTLY three keys — `{type, customTitle, sessionId}`. No `uuid`, `parentUuid`, or `timestamp` envelope. Confirmed across 389 real lines. We mirror that minimal shape exactly.

**Files:**
- Create: `bin/_pkg/rename.py`
- Test: `test/test_rename.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_rename.py`:

```python
import json
import os
import tempfile
from _pkg.rename import append_custom_title
from _pkg.jsonl import session_name


def _tmp_jsonl(initial_lines=()):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(path, "w") as f:
        for line in initial_lines:
            f.write(json.dumps(line) + "\n")
    return path


def test_append_writes_minimal_custom_title():
    path = _tmp_jsonl([{"type": "user", "uuid": "u1"}])
    try:
        append_custom_title(path, session_id="sess-1", new_name="planning-sprint14")
        last = None
        with open(path) as f:
            for line in f:
                last = json.loads(line)
        # Verified empirically: exactly these three keys, nothing more.
        assert set(last.keys()) == {"type", "customTitle", "sessionId"}
        assert last["type"] == "custom-title"
        assert last["customTitle"] == "planning-sprint14"
        assert last["sessionId"] == "sess-1"
    finally:
        os.unlink(path)


def test_append_preserves_prior_lines():
    path = _tmp_jsonl([
        {"type": "user", "uuid": "u1"},
        {"type": "assistant", "uuid": "u2"},
    ])
    try:
        append_custom_title(path, session_id="sess-1", new_name="planning-sprint14")
        with open(path) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 3
        assert lines[0]["uuid"] == "u1"
        assert lines[1]["uuid"] == "u2"
        assert lines[2]["type"] == "custom-title"
    finally:
        os.unlink(path)


def test_session_name_reads_back_the_new_name():
    path = _tmp_jsonl([
        {"type": "ai-title", "aiTitle": "old"},
    ])
    try:
        append_custom_title(path, session_id="sess-1", new_name="planning-sprint14")
        assert session_name(path) == "planning-sprint14"
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest test/test_rename.py -v
```

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `bin/_pkg/rename.py`**

```python
"""Append a Claude-compatible custom-title event to a session JSONL.

The line shape was verified in Task 2 against 389 real custom-title lines
in ~/.claude/projects/: Claude writes EXACTLY three keys — type, customTitle,
sessionId — with no envelope (no uuid, parentUuid, timestamp). We mirror that
minimal shape so Claude's own picker treats the rename as native.

Uses an exclusive flock on the target file to avoid interleaving with a live
Claude write.
"""

from __future__ import annotations

import fcntl
import json
import os


def append_custom_title(transcript_path: str, session_id: str, new_name: str) -> None:
    event = {
        "type": "custom-title",
        "customTitle": new_name,
        "sessionId": session_id,
    }
    line = json.dumps(event, ensure_ascii=False) + "\n"
    # O_APPEND + flock guarantees the line lands atomically at end-of-file.
    fd = os.open(transcript_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
```

- [ ] **Step 4: Run tests — verify PASS**

```bash
pytest test/test_rename.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/rename.py test/test_rename.py
git commit -m "M2: append_custom_title — write Claude-shaped rename events to JSONL"
```

---

### Task 6: `notes.py`, `folders.py`, `delete.py` — index mutators

**Files:**
- Create: `bin/_pkg/notes.py`, `bin/_pkg/folders.py`, `bin/_pkg/delete.py`
- Test: `test/test_notes.py`, `test/test_folders.py`, `test/test_delete.py`

- [ ] **Step 1: Write the failing test for notes**

Create `test/test_notes.py`:

```python
import os
import tempfile
from _pkg import index as _index
from _pkg.notes import set_notes


def _tmp_index_with_session():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    _index.save(path, {"version": 1, "folders": [],
                       "sessions": {"sid-1": {"name_cached": "x", "project_label": "p"}}})
    return path


def test_set_notes_persists():
    path = _tmp_index_with_session()
    try:
        set_notes(path, "sid-1", "hello\nworld")
        data = _index.load(path)
        assert data["sessions"]["sid-1"]["notes"] == "hello\nworld"
    finally:
        os.unlink(path)


def test_set_notes_preserves_other_fields():
    path = _tmp_index_with_session()
    try:
        set_notes(path, "sid-1", "n")
        data = _index.load(path)
        assert data["sessions"]["sid-1"]["name_cached"] == "x"
        assert data["sessions"]["sid-1"]["project_label"] == "p"
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Implement `bin/_pkg/notes.py`**

```python
"""Set a session's notes field. Thin wrapper around index.mutate()."""

from __future__ import annotations

from . import index as _index


def set_notes(index_path: str, session_id: str, notes: str) -> None:
    def mutator(data: dict) -> dict:
        s = data.setdefault("sessions", {}).setdefault(session_id, {})
        s["notes"] = notes
        return data
    _index.mutate(index_path, mutator)
```

- [ ] **Step 3: Run notes tests — verify PASS**

```bash
pytest test/test_notes.py -v
```

Expected: PASS.

- [ ] **Step 4: Write the failing tests for folders**

Create `test/test_folders.py`:

```python
import os
import tempfile
from _pkg import index as _index
from _pkg.folders import add_folder, remove_folder


def _tmp_index():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    _index.save(path, {"version": 1, "folders": [], "sessions": {}})
    return path


def test_add_folder_idempotent():
    path = _tmp_index()
    try:
        add_folder(path, "audits/empty-shelf")
        add_folder(path, "audits/empty-shelf")
        assert _index.load(path)["folders"] == ["audits/empty-shelf"]
    finally:
        os.unlink(path)


def test_remove_folder_no_op_when_absent():
    path = _tmp_index()
    try:
        remove_folder(path, "ghost")
        assert _index.load(path)["folders"] == []
    finally:
        os.unlink(path)


def test_remove_folder_removes_only_matching():
    path = _tmp_index()
    try:
        add_folder(path, "a")
        add_folder(path, "b")
        remove_folder(path, "a")
        assert _index.load(path)["folders"] == ["b"]
    finally:
        os.unlink(path)
```

- [ ] **Step 5: Implement `bin/_pkg/folders.py`**

```python
"""Manage the index's `folders[]` array (pre-created empty folders)."""

from __future__ import annotations

from . import index as _index


def add_folder(index_path: str, folder: str) -> None:
    def mutator(data: dict) -> dict:
        folders = data.setdefault("folders", [])
        if folder not in folders:
            folders.append(folder)
        return data
    _index.mutate(index_path, mutator)


def remove_folder(index_path: str, folder: str) -> None:
    def mutator(data: dict) -> dict:
        data["folders"] = [f for f in data.get("folders", []) if f != folder]
        return data
    _index.mutate(index_path, mutator)
```

- [ ] **Step 6: Run folder tests — verify PASS**

```bash
pytest test/test_folders.py -v
```

Expected: PASS.

- [ ] **Step 7: Write the failing tests for delete**

Create `test/test_delete.py`:

```python
import os
import tempfile
from _pkg import index as _index
from _pkg.delete import delete_session


def _setup():
    fd, idx = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    fd, jsonl = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    with open(jsonl, "w") as f:
        f.write('{"type":"user"}\n')
    _index.save(idx, {"version": 1, "folders": [],
                      "sessions": {"sid": {"transcript_path": jsonl}}})
    return idx, jsonl


def test_delete_removes_jsonl_and_index_entry():
    idx, jsonl = _setup()
    try:
        delete_session(idx, "sid")
        assert not os.path.exists(jsonl)
        assert "sid" not in _index.load(idx)["sessions"]
    finally:
        if os.path.exists(idx):
            os.unlink(idx)


def test_delete_tolerates_missing_jsonl():
    idx, jsonl = _setup()
    os.unlink(jsonl)
    try:
        delete_session(idx, "sid")  # should not raise
        assert "sid" not in _index.load(idx)["sessions"]
    finally:
        os.unlink(idx)


def test_delete_unknown_id_is_noop():
    idx, jsonl = _setup()
    try:
        delete_session(idx, "ghost")
        assert "sid" in _index.load(idx)["sessions"]
    finally:
        os.unlink(idx)
        os.unlink(jsonl)
```

- [ ] **Step 8: Implement `bin/_pkg/delete.py`**

```python
"""Delete a session: removes the JSONL file and the index entry."""

from __future__ import annotations

import os

from . import index as _index


def delete_session(index_path: str, session_id: str) -> None:
    def mutator(data: dict) -> dict:
        entry = data.get("sessions", {}).pop(session_id, None)
        if entry:
            transcript = entry.get("transcript_path")
            if transcript and os.path.exists(transcript):
                try:
                    os.unlink(transcript)
                except OSError:
                    pass
        return data
    _index.mutate(index_path, mutator)
```

- [ ] **Step 9: Run delete tests — verify PASS**

```bash
pytest test/test_delete.py -v
```

Expected: all PASS.

- [ ] **Step 10: Run the full suite — verify PASS**

```bash
pytest -v
```

Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add bin/_pkg/notes.py bin/_pkg/folders.py bin/_pkg/delete.py \
        test/test_notes.py test/test_folders.py test/test_delete.py
git commit -m "M2: notes/folders/delete index mutators with tests"
```

---

### Task 7: TUI skeleton — tree view + stats columns + Quit

**Files:**
- Create: `bin/_pkg/tui.py`
- Modify: `bin/_pkg/cli.py` (add `tui` subcommand for testability)

- [ ] **Step 1: Add a `tui` subcommand to `cli.py`**

In `bin/_pkg/cli.py`, inside `build_parser()`, add:

```python
sub.add_parser("tui", help="Run the Textual TUI in-place (used by `launch`).")
```

And a dispatcher in `main()`:

```python
if args.cmd == "tui":
    from .tui import run
    return run()
```

This lets us call the TUI directly (e.g., from the macOS launcher) without going through `launch`.

- [ ] **Step 2: Implement the skeleton `bin/_pkg/tui.py`**

```python
"""Textual TUI for session-explorer.

Loaded lazily — importing this module triggers the Textual import, which is
several MB of code. Only happens when the user actually runs `tui`/`launch`.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Tree
from textual.widgets.tree import TreeNode

from . import index as _index
from .format import fmt_age, fmt_pct, fmt_tokens
from .tree_model import build_tree, split_folder


def _index_path() -> str:
    return os.environ.get("SESSION_EXPLORER_INDEX") or os.path.expanduser(
        "~/.claude/session-explorer-index.json"
    )


def _row_label(sid: str, s: dict) -> str:
    _, display = split_folder(s.get("name_cached"))
    display = display or sid[:8]
    age = fmt_age(s.get("last_active_at"))
    tokens = fmt_tokens(s.get("tokens_estimate", 0))
    pct = fmt_pct(s.get("tokens_window_pct", 0))
    msgs = s.get("message_count", 0)
    prompt = (s.get("first_prompt") or "").replace("\n", " ")[:40]
    return f"{display:<24} {age:>4}  {tokens:>6} {pct:>5}  {msgs:>4} msgs   {prompt}"


class SessionExplorerApp(App):
    CSS = """
    Tree { padding: 0 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit", show=False),
    ]

    def __init__(self, index_path: str | None = None) -> None:
        super().__init__()
        self._index_path = index_path or _index_path()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        self._tree: Tree[dict] = Tree("sessions")
        yield self._tree
        yield Footer()

    def on_mount(self) -> None:
        self.title = "session-explorer"
        self._populate()

    def _populate(self) -> None:
        self._tree.clear()
        data = _index.load(self._index_path)
        tree = build_tree(data)
        root = self._tree.root
        root.expand()
        total = sum(
            len(sessions)
            for folders in tree.values()
            for sessions in folders.values()
        )
        self.sub_title = f"{total} sessions across {len(tree)} projects"
        for project in sorted(tree):
            folders = tree[project]
            proj_node = root.add(f"▼ {project} ({sum(len(v) for v in folders.values())})", expand=True)
            for folder in sorted(folders):
                sessions = folders[folder]
                if folder and folder != "(unnamed)":
                    folder_node = proj_node.add(f"{folder}/", expand=True)
                else:
                    folder_node = proj_node
                for sid, s in sessions:
                    folder_node.add_leaf(_row_label(sid, s), data={"sid": sid, **s})


def run() -> int:
    SessionExplorerApp().run()
    return 0
```

- [ ] **Step 3: Smoke-test the import**

```bash
SESSION_EXPLORER_INDEX=/tmp/se-empty.json python3 -c "
import json, os, sys
sys.path.insert(0, 'bin')
json.dump({'version':1,'folders':[],'sessions':{}}, open('/tmp/se-empty.json','w'))
from _pkg.tui import SessionExplorerApp
print(SessionExplorerApp().__class__.__name__)
"
```

Expected: prints `SessionExplorerApp` — no import errors.

- [ ] **Step 4: Install dev test deps (host pip — not vendored)**

Textual's `App.run_test()` is async, so the TUI tests need `pytest-asyncio`. That's a *dev* dependency, not a runtime one — we keep it out of `_vendor/` and require it via a tiny dev requirements file.

Create `test/requirements-dev.txt`:

```
pytest>=7
pytest-asyncio>=0.23
```

Install on the dev host:

```bash
python3 -m pip install -r test/requirements-dev.txt
```

Create (or extend) `pytest.ini` at repo root:

```ini
[pytest]
asyncio_mode = auto
testpaths = test
```

- [ ] **Step 5: Headless run test with Textual's Pilot**

Create `test/test_tui.py`:

```python
import json
import os
import tempfile
import pytest


@pytest.fixture
def index_path():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    json.dump({
        "version": 1, "folders": [],
        "sessions": {
            "sid-1": {
                "project_label": "demo",
                "name_cached": "planning-sprint14",
                "last_active_at": "2026-05-27T10:00:00Z",
                "tokens_estimate": 12345,
                "tokens_window_pct": 6,
                "message_count": 18,
                "first_prompt": "hello",
            }
        }
    }, open(path, "w"))
    yield path
    os.unlink(path)


async def test_tui_starts_and_renders_tree(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Tree must contain the project label.
        assert "demo" in str(app._tree.root.children[0].label)


async def test_tui_quit(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
    # Reaching here without timeout means quit worked.
```

(With `asyncio_mode = auto`, the `@pytest.mark.asyncio` decorator is no longer required — `pytest-asyncio` treats every `async def test_*` as async automatically.)

- [ ] **Step 6: Run TUI tests — verify PASS**

```bash
pytest test/test_tui.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/tui.py bin/_pkg/cli.py test/test_tui.py \
        test/requirements-dev.txt pytest.ini
git commit -m "M2: TUI skeleton — tree view, stats columns, q to quit"
```

---

### Task 8: Wire `launch` to spawn the TUI

**Files:**
- Modify: `bin/_pkg/cli.py:_cmd_launch`

- [ ] **Step 1: Repoint `_cmd_launch`**

Replace the body of `_cmd_launch` in `bin/_pkg/cli.py`:

```python
def _cmd_launch() -> int:
    here = os.path.dirname(os.path.realpath(__file__))
    # bin/_pkg/cli.py → bin/session-explorer
    bin_path = os.path.normpath(os.path.join(here, "..", "session-explorer"))
    # `exec` so closing the TUI closes the spawned terminal window cleanly.
    target = f"exec {shlex.quote(bin_path)} tui"
    return _launcher.launch(target)
```

- [ ] **Step 2: Sanity-check the dry-run path**

```bash
SESSION_EXPLORER_DRY_RUN=1 ./bin/session-explorer launch
```

Expected output:

```
DRY RUN: would launch: exec '/.../bin/session-explorer' tui
```

- [ ] **Step 3: Manual smoke test on macOS**

```bash
./bin/session-explorer launch
```

Expected: a new Terminal window opens running the TUI. Press `q` — window closes (because `exec` replaced the shell).

- [ ] **Step 4: Update `test/test_cli.py` if it pins the `list` target**

Search the existing CLI test for the substring `"list"` in launcher assertions and update to `"tui"` if pinned.

```bash
grep -n 'list' test/test_cli.py
```

If a test asserts on the launch target containing `list`, update to `tui`.

- [ ] **Step 5: Run the full suite — verify PASS**

```bash
pytest -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "M2: launch subcommand now spawns the TUI (not text list)"
```

---

### Task 9: Resume on Enter — `exec claude --resume <sid>`

**Files:**
- Modify: `bin/_pkg/tui.py` (extend `SessionExplorerApp`)

- [ ] **Step 1: Add the Enter binding and handler**

In `bin/_pkg/tui.py`, extend `BINDINGS`:

```python
BINDINGS = [
    Binding("enter", "resume", "Resume"),
    Binding("q", "quit", "Quit"),
    Binding("escape", "quit", "Quit", show=False),
]
```

Add the action method on the class:

```python
def action_resume(self) -> None:
    node = self._tree.cursor_node
    if not node or not node.data or "sid" not in node.data:
        self.bell()
        return
    sid = node.data["sid"]
    self.exit()  # let Textual restore the terminal
    # After exit returns from .run(), we can't exec — so the exec lives in run().
    self._resume_target = sid
```

And change `run()`:

```python
def run() -> int:
    app = SessionExplorerApp()
    app.run()
    target = getattr(app, "_resume_target", None)
    if target:
        os.execvp("claude", ["claude", "--resume", target])
    return 0
```

Add `_resume_target = None` as a class attribute or set it in `__init__`.

- [ ] **Step 2: Add a TUI test that triggers resume without execing**

Extend `test/test_tui.py`:

```python
@pytest.mark.asyncio
async def test_enter_sets_resume_target(index_path):
    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Navigate into the first session leaf.
        await pilot.press("right")  # expand project
        await pilot.press("down")   # into folder
        await pilot.press("down")   # into session
        await pilot.press("enter")
        await pilot.pause()
    assert getattr(app, "_resume_target", None) == "sid-1"
```

Adjust the number of `down` presses if the tree shape differs — print `app._tree.cursor_node.label` to debug.

- [ ] **Step 3: Run tests — verify PASS**

```bash
pytest test/test_tui.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "M2: Enter resumes the selected session via exec claude --resume"
```

---

### Task 10: Rename dialog (`r`)

**Files:**
- Modify: `bin/_pkg/tui.py` — add `RenameScreen` modal + binding

- [ ] **Step 1: Implement `RenameScreen`**

In `bin/_pkg/tui.py`, add imports:

```python
from textual.screen import ModalScreen
from textual.widgets import Input, Label
from textual.containers import Vertical
```

Add the screen class above `SessionExplorerApp`:

```python
class RenameScreen(ModalScreen[str]):
    """Prompt for a new session name. Returns the entered string or '' on cancel."""

    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Rename session (Enter to confirm, Esc to cancel):"),
            Input(value=self._current, id="rename-input"),
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())
```

Add the binding to `SessionExplorerApp.BINDINGS`:

```python
Binding("r", "rename", "Rename"),
```

And the action:

```python
def action_rename(self) -> None:
    node = self._tree.cursor_node
    if not node or not node.data or "sid" not in node.data:
        self.bell()
        return
    sid = node.data["sid"]
    current = node.data.get("name_cached") or ""
    transcript = node.data.get("transcript_path")

    def after(new_name: str | None) -> None:
        if not new_name or new_name == current or not transcript:
            return
        from .rename import append_custom_title
        append_custom_title(transcript, session_id=sid, new_name=new_name)
        # Update cache immediately so the UI reflects the new name pre-hook.
        from . import index as _index
        def _mut(d: dict) -> dict:
            d["sessions"].setdefault(sid, {})["name_cached"] = new_name
            return d
        _index.mutate(self._index_path, _mut)
        self._populate()

    self.push_screen(RenameScreen(current), after)
```

- [ ] **Step 2: TUI test — rename round-trip**

Extend `test/test_tui.py`:

```python
@pytest.mark.asyncio
async def test_rename_updates_index(index_path, tmp_path):
    # Add a transcript path to the session so rename can write to it.
    import json
    data = json.load(open(index_path))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"user","uuid":"u1"}\n')
    data["sessions"]["sid-1"]["transcript_path"] = str(transcript)
    json.dump(data, open(index_path, "w"))

    from _pkg.tui import SessionExplorerApp
    app = SessionExplorerApp(index_path=index_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Navigate to the leaf and press r
        await pilot.press("right"); await pilot.press("down")
        await pilot.press("down"); await pilot.press("down")
        await pilot.press("r")
        await pilot.pause()
        # Type new name and submit
        for ch in "renamed":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()

    import json
    assert json.load(open(index_path))["sessions"]["sid-1"]["name_cached"] == "renamed"
```

(Adjust `down` count to land on the session leaf — print the cursor node label if needed.)

- [ ] **Step 3: Run tests — verify PASS**

```bash
pytest test/test_tui.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add bin/_pkg/tui.py test/test_tui.py
git commit -m "M2: r — rename modal writes custom-title + updates index"
```

---

### Task 11: Move dialog (`m`)

**Files:**
- Modify: `bin/_pkg/tui.py` — `MoveScreen` modal + binding

- [ ] **Step 1: Implement `MoveScreen`**

Add to `bin/_pkg/tui.py`:

```python
from textual.widgets import OptionList
from textual.widgets.option_list import Option


class MoveScreen(ModalScreen[str]):
    """Pick or type a folder. Returns the chosen folder name (or '' to ungroup, or None on cancel)."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, existing_folders: list[str], current: str) -> None:
        super().__init__()
        self._existing = sorted(set(existing_folders))
        self._current = current

    def compose(self) -> ComposeResult:
        opts = [Option("(ungroup)", id="__none__")] + [Option(f, id=f) for f in self._existing]
        yield Vertical(
            Label(f"Move to folder (current: {self._current or '(none)'}). Pick or type:"),
            OptionList(*opts, id="move-list"),
            Input(placeholder="…or type a new folder name", id="move-input"),
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        self.dismiss("" if opt_id == "__none__" else opt_id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())
```

Add binding and action:

```python
Binding("m", "move", "Move"),
```

```python
def action_move(self) -> None:
    node = self._tree.cursor_node
    if not node or not node.data or "sid" not in node.data:
        self.bell()
        return
    sid = node.data["sid"]
    name = node.data.get("name_cached") or ""
    transcript = node.data.get("transcript_path")
    current_folder, display = split_folder(name)
    # Build the folder list = folders[] ∪ {folders seen in sessions}
    data = _index.load(self._index_path)
    folders = set(data.get("folders") or [])
    for s in data.get("sessions", {}).values():
        f, _ = split_folder(s.get("name_cached"))
        if f:
            folders.add(f)

    def after(target: str | None) -> None:
        if target is None or not transcript:
            return
        new_name = display if not target else f"{target}-{display or sid[:8]}"
        from .rename import append_custom_title
        append_custom_title(transcript, session_id=sid, new_name=new_name)
        def _mut(d: dict) -> dict:
            d["sessions"].setdefault(sid, {})["name_cached"] = new_name
            return d
        _index.mutate(self._index_path, _mut)
        self._populate()

    self.push_screen(MoveScreen(sorted(folders), current_folder), after)
```

- [ ] **Step 2: Smoke-test interactively**

```bash
./bin/session-explorer tui
```

Press `m` on a named session, pick or type a folder, confirm the tree re-renders with the session under the new folder.

(Automated test for OptionList interaction is brittle — leave the headless test scope to "the action method invokes append_custom_title", which we'd cover via a unit-style test that calls `action_move` after stubbing `push_screen`. Optional; skip if Pilot reaches it cleanly.)

- [ ] **Step 3: Commit**

```bash
git add bin/_pkg/tui.py
git commit -m "M2: m — move modal rewrites the folder prefix"
```

---

### Task 12: New folder dialog (`n`)

**Files:**
- Modify: `bin/_pkg/tui.py`

- [ ] **Step 1: Implement `NewFolderScreen`**

Add to `bin/_pkg/tui.py`:

```python
class NewFolderScreen(ModalScreen[str]):
    BINDINGS = [Binding("escape", "dismiss('')", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("New folder name:"),
            Input(id="newfolder-input"),
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())
```

Binding + action:

```python
Binding("n", "new_folder", "New folder"),
```

```python
def action_new_folder(self) -> None:
    def after(name: str) -> None:
        if not name:
            return
        from .folders import add_folder
        add_folder(self._index_path, name)
        self._populate()
    self.push_screen(NewFolderScreen(), after)
```

- [ ] **Step 2: Manual smoke test**

```bash
./bin/session-explorer tui
```

Press `n`, enter `audits/empty-shelf`, confirm it shows up under the `(unfiled)` project bucket.

- [ ] **Step 3: Commit**

```bash
git add bin/_pkg/tui.py
git commit -m "M2: n — new folder modal adds to folders[]"
```

---

### Task 13: Delete confirm (`d`)

**Files:**
- Modify: `bin/_pkg/tui.py`

- [ ] **Step 1: Implement `ConfirmScreen`**

Add to `bin/_pkg/tui.py`:

```python
from textual.widgets import Button


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "dismiss(False)", "Cancel"),
        Binding("y", "dismiss(True)", "Yes", show=False),
        Binding("n", "dismiss(False)", "No", show=False),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._prompt),
            Label("[y] yes   [n / esc] cancel"),
        )
```

Binding + action:

```python
Binding("d", "delete", "Delete"),
```

```python
def action_delete(self) -> None:
    node = self._tree.cursor_node
    if not node or not node.data or "sid" not in node.data:
        self.bell()
        return
    sid = node.data["sid"]
    name = node.data.get("name_cached") or sid[:8]

    def after(ok: bool) -> None:
        if not ok:
            return
        from .delete import delete_session
        delete_session(self._index_path, sid)
        self._populate()

    self.push_screen(ConfirmScreen(f"Delete '{name}'? This removes the JSONL too."), after)
```

- [ ] **Step 2: Manual smoke test**

```bash
./bin/session-explorer tui
```

Create a throwaway session and confirm `d` → `y` removes it from the tree and from disk. Verify with `ls ~/.claude/projects/<encoded>/`.

- [ ] **Step 3: Commit**

```bash
git add bin/_pkg/tui.py
git commit -m "M2: d — delete confirms then removes JSONL + index entry"
```

---

### Task 14: Notes editor (`e`)

**Files:**
- Modify: `bin/_pkg/tui.py`

- [ ] **Step 1: Implement `NotesScreen`**

Add to `bin/_pkg/tui.py`:

```python
from textual.widgets import TextArea


class NotesScreen(ModalScreen[str]):
    """Multi-line editor. Returns the new notes (may be empty) or None on cancel."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        self._ta = TextArea(self._current)
        yield Vertical(
            Label("Notes (Ctrl+S to save, Esc to cancel):"),
            self._ta,
        )

    def action_save(self) -> None:
        self.dismiss(self._ta.text)
```

Binding + action:

```python
Binding("e", "notes", "Edit notes"),
```

```python
def action_notes(self) -> None:
    node = self._tree.cursor_node
    if not node or not node.data or "sid" not in node.data:
        self.bell()
        return
    sid = node.data["sid"]
    current = node.data.get("notes") or ""

    def after(new_notes: str | None) -> None:
        if new_notes is None:
            return
        from .notes import set_notes
        set_notes(self._index_path, sid, new_notes)
        self._populate()

    self.push_screen(NotesScreen(current), after)
```

- [ ] **Step 2: Manual smoke test**

Open the TUI, press `e` on a session, type a note, Ctrl+S. Re-open the TUI and confirm the note persists.

- [ ] **Step 3: Commit**

```bash
git add bin/_pkg/tui.py
git commit -m "M2: e — notes modal persists multi-line notes to the index"
```

---

### Task 15: Preview pane (`Space`)

**Files:**
- Modify: `bin/_pkg/tui.py`

- [ ] **Step 1: Add a togglable preview Static**

In `bin/_pkg/tui.py`:

```python
from textual.containers import Horizontal
from textual.widgets import Static
```

Replace the contents of `compose()`:

```python
def compose(self) -> ComposeResult:
    yield Header(show_clock=False)
    self._tree: Tree[dict] = Tree("sessions")
    self._preview = Static("", id="preview")
    self._preview.display = False
    yield Horizontal(self._tree, self._preview)
    yield Footer()
```

Add binding + action:

```python
Binding("space", "preview", "Preview"),
```

```python
def action_preview(self) -> None:
    self._preview.display = not self._preview.display
    self._refresh_preview()

def _refresh_preview(self) -> None:
    if not self._preview.display:
        return
    node = self._tree.cursor_node
    s = node.data if node and node.data else {}
    notes = s.get("notes") or "(no notes)"
    prompt = s.get("first_prompt") or "(no first prompt recorded)"
    summary = s.get("summary") or "(no summary)"
    path = s.get("transcript_path") or "(unknown path)"
    self._preview.update(
        f"[b]Notes[/]\n{notes}\n\n"
        f"[b]First prompt[/]\n{prompt}\n\n"
        f"[b]Summary[/]\n{summary}\n\n"
        f"[b]Path[/]\n{path}"
    )
```

Wire cursor changes so the preview tracks selection:

```python
def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
    self._refresh_preview()
```

- [ ] **Step 2: Manual smoke test**

```bash
./bin/session-explorer tui
```

Press Space — preview pane appears on the right, populated from the selected session. Arrow up/down — preview updates. Space again — pane hides.

- [ ] **Step 3: Commit**

```bash
git add bin/_pkg/tui.py
git commit -m "M2: space — toggleable preview pane (notes/prompt/summary/path)"
```

---

### Task 16: Filter (`/`)

**Files:**
- Modify: `bin/_pkg/tui.py`

- [ ] **Step 1: Add a filter Input that re-runs `_populate` with a needle**

In `bin/_pkg/tui.py`, add to `compose()`:

```python
self._filter = Input(placeholder="filter…", id="filter")
self._filter.display = False
yield self._filter
```

(Place it between the Horizontal and the Footer, or in the Header area — wherever fits the layout.)

Add binding + action:

```python
Binding("slash", "filter", "Filter"),
```

```python
def action_filter(self) -> None:
    self._filter.display = True
    self._filter.focus()

def on_input_changed(self, event: Input.Changed) -> None:
    if event.input is self._filter:
        self._filter_needle = event.value.lower().strip()
        self._populate()

def on_input_submitted(self, event: Input.Submitted) -> None:
    # Pressing Enter on the filter input returns focus to the tree.
    if event.input is self._filter:
        self._tree.focus()
```

In `__init__`:

```python
self._filter_needle: str = ""
```

Modify `_populate` to drop sessions that don't match:

```python
def _matches(self, sid: str, s: dict) -> bool:
    if not self._filter_needle:
        return True
    n = self._filter_needle
    haystacks = [
        s.get("name_cached") or "",
        s.get("notes") or "",
        s.get("first_prompt") or "",
        s.get("summary") or "",
        sid,
    ]
    return any(n in h.lower() for h in haystacks)
```

Wrap the session loop in `_populate` with `if self._matches(sid, s):`.

- [ ] **Step 2: Manual smoke test**

```bash
./bin/session-explorer tui
```

Press `/`, type a substring — tree filters live. Esc out of the input — filter persists until cleared.

- [ ] **Step 3: Commit**

```bash
git add bin/_pkg/tui.py
git commit -m "M2: / — live filter across name/notes/prompt/summary"
```

---

### Task 17: Linux terminal launcher

**Files:**
- Modify: `bin/_pkg/launcher.py`
- Modify: `test/test_launcher.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_launcher.py`:

```python
from unittest import mock
from _pkg import launcher


def test_linux_uses_TERMINAL_env(monkeypatch):
    monkeypatch.setenv("SESSION_EXPLORER_DRY_RUN", "1")
    monkeypatch.setenv("TERMINAL", "kitty")
    monkeypatch.setattr("platform.system", lambda: "Linux")
    cmd = launcher.build_linux_command("echo hi", which=lambda x: "/usr/bin/kitty" if x == "kitty" else None)
    assert cmd[0].endswith("kitty")


def test_linux_falls_through_emulator_list(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    found = {"gnome-terminal": "/usr/bin/gnome-terminal"}
    cmd = launcher.build_linux_command("echo hi", which=found.get)
    assert cmd[0].endswith("gnome-terminal")


def test_linux_no_emulator_returns_None(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    assert launcher.build_linux_command("echo hi", which=lambda _: None) is None
```

- [ ] **Step 2: Implement `build_linux_command`**

In `bin/_pkg/launcher.py`, add:

```python
import shutil

_LINUX_CANDIDATES = [
    "x-terminal-emulator",
    "gnome-terminal",
    "konsole",
    "xfce4-terminal",
    "alacritty",
    "kitty",
    "wezterm",
]

# Per-emulator argv shape to pass through a single shell command.
_LINUX_ARGV = {
    "gnome-terminal": lambda binp, cmd: [binp, "--", "bash", "-lc", cmd],
    "konsole":         lambda binp, cmd: [binp, "-e", "bash", "-lc", cmd],
    "xfce4-terminal":  lambda binp, cmd: [binp, "-e", f"bash -lc {cmd!r}"],
    "alacritty":       lambda binp, cmd: [binp, "-e", "bash", "-lc", cmd],
    "kitty":           lambda binp, cmd: [binp, "bash", "-lc", cmd],
    "wezterm":         lambda binp, cmd: [binp, "start", "--", "bash", "-lc", cmd],
    "x-terminal-emulator": lambda binp, cmd: [binp, "-e", "bash", "-lc", cmd],
}


def build_linux_command(target_command: str, which=shutil.which) -> "list[str] | None":
    # 1. $TERMINAL wins if it resolves.
    env_term = os.environ.get("TERMINAL")
    if env_term:
        binp = which(env_term)
        if binp:
            argv_fn = _LINUX_ARGV.get(os.path.basename(env_term),
                                      lambda b, c: [b, "-e", "bash", "-lc", c])
            return argv_fn(binp, target_command)

    # 2. Probe known emulators in order.
    for name in _LINUX_CANDIDATES:
        binp = which(name)
        if binp:
            argv_fn = _LINUX_ARGV.get(name, lambda b, c: [b, "-e", "bash", "-lc", c])
            return argv_fn(binp, target_command)

    return None
```

Update `launch()`:

```python
def launch(target_command: str) -> int:
    if os.environ.get("SESSION_EXPLORER_DRY_RUN") == "1":
        print(f"DRY RUN: would launch: {target_command}")
        return 0

    system = platform.system()
    if system == "Darwin":
        cmd = build_macos_command(target_command)
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0

    if system == "Linux":
        cmd = build_linux_command(target_command)
        if cmd is not None:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return 0

    print(f"Unsupported platform '{system}'. Run this in any terminal:\n  {target_command}")
    return 2
```

- [ ] **Step 3: Run launcher tests — verify PASS**

```bash
pytest test/test_launcher.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add bin/_pkg/launcher.py test/test_launcher.py
git commit -m "M2: Linux launcher — \$TERMINAL + emulator probe"
```

---

### Task 18: README touch-up + screenshot caveat

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a TUI section**

In `README.md`, after the install instructions, add:

```markdown
## Using the TUI

`/session-explorer:open` from any Claude Code session opens the explorer in a new terminal window.

Keybindings:

| Key | Action |
|---|---|
| `↑` `↓` | Move between rows |
| `←` `→` | Collapse / expand the current folder or project |
| `Enter` | Resume the selected session |
| `Space` | Toggle the preview pane (notes, first prompt, summary, path) |
| `r` | Rename (= retag = move to a different folder) |
| `n` | Create an empty folder |
| `m` | Move the selected session to a folder |
| `d` | Delete the selected session (confirms) |
| `e` | Edit notes |
| `/` | Live filter |
| `q` `Esc` | Quit |

Folder semantics: the first dash in a session's name separates folder from
display name. `planning-sprint14` lives in folder `planning` as session
`sprint14`. There is no separate "tag" — the name is the only metadata.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "M2: README — document TUI keybindings"
```

---

### Task 19: Full-suite verification + worktree merge prep

- [ ] **Step 1: Run everything**

```bash
pytest -v
./bin/session-explorer launch    # macOS: confirm Terminal window opens
```

Expected: all tests PASS, TUI launches cleanly, keybindings work, no tracebacks in `~/.claude/session-explorer.log`.

- [ ] **Step 2: Verify the spec coverage**

Open `SPEC.md`, walk the M2 row of the milestones table, and confirm each item in "The TUI" has a Task above:
- ✅ Tree view
- ✅ All keybindings (↑↓←→ Enter Space r n m d e / q Esc)
- ✅ Rename / move / delete / notes
- ✅ Preview pane
- ✅ Stats columns (age, ~tokens, %, msgs, prompt tail)
- ✅ Linux launchers

- [ ] **Step 3: Hand back to `superpowers:finishing-a-development-branch`**

When tests are green and a manual smoke pass is clean, invoke `superpowers:finishing-a-development-branch` to decide between merge, PR, or further review.

---

## Out of scope (deferred to M3 or later)

- `--gc` for sessions and empty folders.
- Search across notes/prompts/summaries via a richer query language (current `/` filter is a substring match).
- Model-aware context-window denominator (still hardcoded 200K).
- `session-explorer uninstall` subcommand.
- Windows / WSL launcher.
- In-place compaction.
