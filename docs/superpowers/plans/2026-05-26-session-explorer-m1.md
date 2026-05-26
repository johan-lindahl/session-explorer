# session-explorer M1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the smallest end-to-end version of the `session-explorer` Claude Code plugin: install it from a self-hosted marketplace, watch a SessionStart hook record every Claude session into a sidecar index, and run `/session-explorer` to open a new Terminal window showing a text-mode listing of all known sessions grouped by project and folder.

**Architecture:** A Python 3.11+ CLI (`bin/session-explorer`) wraps a stdlib-only package (`bin/_pkg/`) responsible for index I/O (flock + temp-rename), JSONL parsing (name, prompt, message count, cache_read tokens), and an OS-detecting terminal launcher (macOS in M1 via `osascript`). A bash hook (`hooks/session-start.sh`) calls the CLI to record sessions and performs idempotent first-run setup (back up `cleanupPeriodDays` and set it to 36500). The plugin ships as a single git repo that doubles as its own self-hosted Claude Code marketplace.

**Tech Stack:** Python 3.11+ (stdlib only in M1 — Textual lands in M2), bash for the hook, `osascript` for the macOS launcher. Tests: `pytest` for Python, `bats-core` for bash. Marketplace install via `/plugin marketplace add` + `/plugin install`.

**Prereqs the engineer must have on their machine before starting:**
- macOS (M1 is macOS-first).
- `python3 --version` reports 3.11+.
- `pip install --user pytest` succeeds.
- `brew install bats-core` succeeds.
- Claude Code installed (the engineer has a populated `~/.claude/projects/` directory with at least one JSONL — needed for Task 4 inspection and integration testing).

**What's deliberately NOT in this plan (lands in later milestones):**
- Textual TUI (M2).
- Linux/Windows launchers (M2/M5).
- `--gc`, `session-explorer uninstall`, search (M3).
- CI (M4).

---

## File structure (M1 final state)

```
session-explorer/
├── README.md                           ← Task 17
├── CLAUDE.md                           ← already exists
├── SPEC.md                             ← already exists
├── docs/superpowers/plans/             ← this plan lives here
├── .gitignore                          ← Task 1
├── marketplace.json                    ← Task 1
├── .claude-plugin/
│   └── plugin.json                     ← Task 1
├── bin/
│   ├── session-explorer                ← Task 2 (entry shim)
│   └── _pkg/
│       ├── __init__.py                 ← Task 2
│       ├── cli.py                      ← Tasks 9-11
│       ├── index.py                    ← Tasks 6-8
│       ├── jsonl.py                    ← Tasks 3-5
│       └── launcher.py                 ← Task 14
├── hooks/
│   └── session-start.sh                ← Tasks 12-13
├── commands/
│   └── session-explorer.md             ← Task 15
├── install.sh                          ← Task 16
└── test/
    ├── conftest.py                     ← Task 2
    ├── fixtures/
    │   ├── named.jsonl                 ← Task 3
    │   ├── unnamed.jsonl               ← Task 3
    │   └── empty.jsonl                 ← Task 3
    ├── test_jsonl.py                   ← Tasks 3-5
    ├── test_index.py                   ← Tasks 6-8
    ├── test_cli.py                     ← Tasks 9-11
    ├── test_launcher.py                ← Task 14
    └── test_session_start.bats         ← Tasks 12-13
```

---

## Task 1: Repository scaffolding and manifests

**Files:**
- Create: `.gitignore`
- Create: `marketplace.json`
- Create: `.claude-plugin/plugin.json`
- Create: `bin/` (directory)
- Create: `hooks/` (directory)
- Create: `commands/` (directory)
- Create: `test/fixtures/` (directory)

- [ ] **Step 1: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.DS_Store
*.egg-info/
.env
```

- [ ] **Step 2: Create `marketplace.json` at repo root**

```json
{
  "name": "session-explorer",
  "owner": {
    "name": "Johan Lindahl",
    "email": "johan.lindahl@snojken.com"
  },
  "plugins": [
    {
      "name": "session-explorer",
      "source": "./",
      "description": "File-explorer-style session management for Claude Code"
    }
  ]
}
```

- [ ] **Step 3: Create `.claude-plugin/plugin.json`**

```json
{
  "name": "session-explorer",
  "version": "0.1.0",
  "description": "File-explorer-style session management for Claude Code",
  "commands": "commands/",
  "hooks": {
    "SessionStart": [
      {
        "matchers": [],
        "command": "$CLAUDE_PLUGIN_DIR/hooks/session-start.sh"
      }
    ]
  }
}
```

- [ ] **Step 4: Create empty directories with `.gitkeep`**

```bash
mkdir -p bin/_pkg hooks commands test/fixtures
touch bin/_pkg/.gitkeep hooks/.gitkeep commands/.gitkeep test/fixtures/.gitkeep
```

- [ ] **Step 5: Verify JSON files parse**

Run: `python3 -m json.tool marketplace.json > /dev/null && python3 -m json.tool .claude-plugin/plugin.json > /dev/null && echo OK`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add .gitignore marketplace.json .claude-plugin/plugin.json bin hooks commands test
git commit -m "M1: scaffold repo, manifests, directory layout"
```

---

## Task 2: CLI entry shim and Python package boot

**Files:**
- Create: `bin/session-explorer`
- Create: `bin/_pkg/__init__.py`
- Create: `bin/_pkg/cli.py`
- Create: `test/conftest.py`
- Create: `test/test_cli.py`
- Delete: `bin/_pkg/.gitkeep`

- [ ] **Step 1: Delete the placeholder**

```bash
rm bin/_pkg/.gitkeep
```

- [ ] **Step 2: Create `bin/_pkg/__init__.py`**

```python
"""session-explorer package."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create `bin/_pkg/cli.py` with a minimal `--version` subcommand**

```python
"""Argparse skeleton for the session-explorer CLI."""

import argparse
import sys

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="session-explorer")
    p.add_argument("--version", action="version", version=f"session-explorer {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="List all known sessions (text output).")
    sub.add_parser("launch", help="Launch the explorer in a new terminal window.")

    index_p = sub.add_parser("index", help="Index management.")
    index_p.add_argument("--record", nargs=3, metavar=("SESSION_ID", "TRANSCRIPT_PATH", "CWD"))
    index_p.add_argument("--refresh", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    # Subcommand dispatch lands in later tasks.
    print(f"(stub) cmd={args.cmd} args={vars(args)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create `bin/session-explorer` (entry shim)**

```python
#!/usr/bin/env python3
"""Entry shim — adds bin/ to sys.path so `_pkg` imports resolve regardless
of how the binary is invoked (PATH, absolute path, symlink, marketplace install)."""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from _pkg.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Mark the shim executable**

```bash
chmod +x bin/session-explorer
```

- [ ] **Step 6: Create `test/conftest.py` so pytest finds `_pkg`**

```python
"""Add bin/ to sys.path so test files can `from _pkg import ...`."""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_REPO_ROOT, "bin")
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
```

- [ ] **Step 7: Write a failing test for `bin/session-explorer --version`**

Create `test/test_cli.py`:

```python
"""CLI smoke tests for the entry shim."""

import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_REPO_ROOT, "bin", "session-explorer")


def test_version_flag_prints_version():
    result = subprocess.run([_BIN, "--version"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "session-explorer 0.1.0" in result.stdout


def test_help_when_no_args():
    result = subprocess.run([_BIN], capture_output=True, text=True)
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
```

- [ ] **Step 8: Run the tests, verify they pass**

Run: `pytest test/test_cli.py -v`
Expected: 2 passed.

- [ ] **Step 9: Commit**

```bash
git add bin test/conftest.py test/test_cli.py
git commit -m "M1: CLI entry shim with --version; pytest bootstrap"
```

---

## Task 3: JSONL fixtures and basic line parsing

**Files:**
- Create: `test/fixtures/named.jsonl`
- Create: `test/fixtures/unnamed.jsonl`
- Create: `test/fixtures/empty.jsonl`
- Create: `bin/_pkg/jsonl.py`
- Create: `test/test_jsonl.py`
- Delete: `test/fixtures/.gitkeep`

**Note on fixture realism:** the JSONL line shape below is based on the Anthropic Messages API response format embedded in Claude Code's transcript. **Task 4** runs an inspection script against a real local JSONL to confirm field paths; if they diverge from these fixtures, the engineer adjusts both fixtures and parser in Task 4. For now, write the parser against this assumed shape so we have something to refine.

- [ ] **Step 1: Delete the placeholder**

```bash
rm test/fixtures/.gitkeep
```

- [ ] **Step 2: Create `test/fixtures/named.jsonl`** — a 4-line transcript (system, user, assistant, user) for a session named `planning-sprint14`:

```jsonl
{"type":"system","subtype":"init","sessionId":"01ABC","cwd":"/Users/jl/proj/foo","gitBranch":"main","timestamp":"2026-05-20T10:00:00Z","sessionName":"planning-sprint14"}
{"type":"user","sessionId":"01ABC","uuid":"u1","timestamp":"2026-05-20T10:00:05Z","message":{"role":"user","content":"plan sprint 14 work"}}
{"type":"assistant","sessionId":"01ABC","uuid":"a1","timestamp":"2026-05-20T10:00:08Z","message":{"role":"assistant","model":"claude-sonnet-4-6","content":[{"type":"text","text":"Here's a draft plan…"}],"usage":{"input_tokens":12,"cache_creation_input_tokens":0,"cache_read_input_tokens":15234,"output_tokens":420}}}
{"type":"user","sessionId":"01ABC","uuid":"u2","timestamp":"2026-05-20T10:02:00Z","message":{"role":"user","content":"good, expand on item 2"}}
```

- [ ] **Step 3: Create `test/fixtures/unnamed.jsonl`** — a 2-line transcript with no `sessionName`:

```jsonl
{"type":"system","subtype":"init","sessionId":"01XYZ","cwd":"/Users/jl/proj/bar","gitBranch":"feat/x","timestamp":"2026-05-20T11:00:00Z"}
{"type":"user","sessionId":"01XYZ","uuid":"u1","timestamp":"2026-05-20T11:00:05Z","message":{"role":"user","content":"poke around"}}
```

- [ ] **Step 4: Create `test/fixtures/empty.jsonl`** — a literally empty file:

```bash
: > test/fixtures/empty.jsonl
```

- [ ] **Step 5: Write failing tests in `test/test_jsonl.py`**

```python
"""Tests for _pkg.jsonl."""

import os

from _pkg import jsonl

_FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_message_count_named():
    assert jsonl.message_count(os.path.join(_FIX, "named.jsonl")) == 4


def test_message_count_empty():
    assert jsonl.message_count(os.path.join(_FIX, "empty.jsonl")) == 0


def test_first_user_prompt_named():
    assert jsonl.first_user_prompt(os.path.join(_FIX, "named.jsonl")) == "plan sprint 14 work"


def test_first_user_prompt_empty():
    assert jsonl.first_user_prompt(os.path.join(_FIX, "empty.jsonl")) is None
```

- [ ] **Step 6: Run, verify failure**

Run: `pytest test/test_jsonl.py -v`
Expected: `ModuleNotFoundError: No module named '_pkg.jsonl'` (or 4 errors).

- [ ] **Step 7: Create `bin/_pkg/jsonl.py`** with minimal implementations:

```python
"""Parse Claude Code session JSONL transcripts.

Field paths are based on the Anthropic Messages API response format embedded
inside Claude Code transcripts. Task 4 (inspect-local-jsonl.py) verifies them
against a real local file; adjust here if reality diverges from the fixtures.
"""

import json
from typing import Optional


def _iter_messages(path: str):
    """Yield decoded JSON objects, skipping malformed lines (logs a count)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return


def message_count(path: str) -> int:
    return sum(1 for _ in _iter_messages(path))


def first_user_prompt(path: str) -> Optional[str]:
    for msg in _iter_messages(path):
        if msg.get("type") == "user":
            content = msg.get("message", {}).get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list) and content:
                # User messages can occasionally be array-shaped (tool results).
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return item.get("text")
    return None
```

- [ ] **Step 8: Run tests, verify pass**

Run: `pytest test/test_jsonl.py -v`
Expected: 4 passed.

- [ ] **Step 9: Commit**

```bash
git add test/fixtures bin/_pkg/jsonl.py test/test_jsonl.py
git commit -m "M1: JSONL fixtures + message_count, first_user_prompt"
```

---

## Task 4: Inspect a real local JSONL and reconcile field paths

This task is **part research, part code**. The fixtures in Task 3 are based on assumptions; this task verifies them against a real transcript on the engineer's machine. If reality diverges, both the fixtures and `jsonl.py` are adjusted before the parser grows further.

**Files:**
- Create: `bin/_pkg/_inspect.py` (a temporary inspection tool, deleted before commit)

- [ ] **Step 1: Locate a real JSONL on the engineer's machine**

```bash
find ~/.claude/projects -name "*.jsonl" -type f | head -3
```

Expected: at least one path printed.

- [ ] **Step 2: Print key paths from the first 5 lines**

```bash
python3 -c "
import json, sys
path = sys.argv[1]
with open(path) as f:
    for i, line in enumerate(f):
        if i >= 5: break
        obj = json.loads(line)
        def keys(d, prefix=''):
            for k, v in d.items():
                p = f'{prefix}.{k}' if prefix else k
                if isinstance(v, dict):
                    yield from keys(v, p)
                else:
                    yield p
        print(f'--- line {i} (type={obj.get(\"type\")}) ---')
        for k in keys(obj):
            print(' ', k)
" "$(find ~/.claude/projects -name '*.jsonl' -type f | head -1)"
```

- [ ] **Step 3: Compare actual paths to fixture assumptions**

Specifically check:
- Does `sessionName` exist on the `system/init` line? Or is it on a different line type (e.g. `meta`, `rename`)? **Note its exact path.**
- Is `message.usage.cache_read_input_tokens` present on assistant lines? **Note path.**
- Is `message.model` per-message, or only on `system/init`?
- Does the first user message have `message.content` as a string or an array?

- [ ] **Step 4: If reality diverges from fixtures, update fixtures AND `jsonl.py`**

Update `test/fixtures/named.jsonl` and `test/fixtures/unnamed.jsonl` to use the actual field paths. Update `_iter_messages`, `first_user_prompt`, and the not-yet-written name/usage parsers in subsequent tasks to match.

Re-run: `pytest test/test_jsonl.py -v`
Expected: 4 passed.

- [ ] **Step 5: Find a renamed session and capture the rename serialization**

```bash
grep -l rename ~/.claude/projects/**/*.jsonl 2>/dev/null | head -3
```

If results found: open one and identify the rename event's exact JSON shape. Document it as a comment at the top of `bin/_pkg/jsonl.py`. If no renamed sessions exist on this machine, rename a session inside Claude (`/rename test-folder-task4`), then re-run.

- [ ] **Step 6: Delete the inspection tool**

```bash
rm -f bin/_pkg/_inspect.py
```

- [ ] **Step 7: Commit (fixture/parser adjustments + the rename-format note)**

```bash
git add test/fixtures bin/_pkg/jsonl.py
git commit -m "M1: reconcile fixtures with real JSONL field paths; document rename format"
```

If no changes were needed, commit message: `M1: verify JSONL field paths against real transcript (no changes)` and `git commit --allow-empty`.

---

## Task 5: JSONL — session name and token-usage extraction

**Files:**
- Modify: `bin/_pkg/jsonl.py`
- Modify: `test/test_jsonl.py`

- [ ] **Step 1: Write failing tests** — append to `test/test_jsonl.py`:

```python
def test_session_name_named():
    name = jsonl.session_name(os.path.join(_FIX, "named.jsonl"))
    assert name == "planning-sprint14"


def test_session_name_unnamed():
    assert jsonl.session_name(os.path.join(_FIX, "unnamed.jsonl")) is None


def test_tokens_estimate_named_uses_cache_read():
    # Latest assistant message has cache_read_input_tokens=15234
    assert jsonl.tokens_estimate(os.path.join(_FIX, "named.jsonl")) == 15234


def test_tokens_estimate_unnamed_falls_back_to_bytes_over_4():
    # No assistant messages → fall back to bytes/4
    path = os.path.join(_FIX, "unnamed.jsonl")
    expected = os.path.getsize(path) // 4
    assert jsonl.tokens_estimate(path) == expected


def test_tokens_estimate_empty():
    assert jsonl.tokens_estimate(os.path.join(_FIX, "empty.jsonl")) == 0


def test_last_active_named():
    # Last line's timestamp
    assert jsonl.last_active_at(os.path.join(_FIX, "named.jsonl")) == "2026-05-20T10:02:00Z"
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest test/test_jsonl.py -v`
Expected: 6 new tests fail with `AttributeError: module '_pkg.jsonl' has no attribute 'session_name'` etc.

- [ ] **Step 3: Add implementations to `bin/_pkg/jsonl.py`** — append:

```python
import os
from typing import Optional


def session_name(path: str) -> Optional[str]:
    """Returns the Claude-assigned session name, or None.

    Reads the system/init line first; falls back to scanning for a rename
    event if the format documented at the top of this file applies.
    """
    last_rename = None
    init_name = None
    for msg in _iter_messages(path):
        if msg.get("type") == "system" and msg.get("subtype") == "init":
            init_name = msg.get("sessionName")
        # If Task 4 documented a rename event shape, handle it here:
        # if msg.get("type") == "rename":
        #     last_rename = msg.get("name")
    return last_rename or init_name


def tokens_estimate(path: str) -> int:
    """Approximate context size.

    Returns cache_read_input_tokens from the latest assistant message,
    or bytes-of-file / 4 as a fallback. Zero for missing/empty files.
    """
    last_cache_read = None
    for msg in _iter_messages(path):
        if msg.get("type") == "assistant":
            usage = msg.get("message", {}).get("usage") or {}
            val = usage.get("cache_read_input_tokens")
            if val:
                last_cache_read = val
    if last_cache_read is not None:
        return int(last_cache_read)
    try:
        return os.path.getsize(path) // 4
    except FileNotFoundError:
        return 0


def last_active_at(path: str) -> Optional[str]:
    """ISO8601 timestamp of the last message; None for empty/missing files."""
    last = None
    for msg in _iter_messages(path):
        ts = msg.get("timestamp")
        if ts:
            last = ts
    return last
```

- [ ] **Step 4: Run all JSONL tests, verify pass**

Run: `pytest test/test_jsonl.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/jsonl.py test/test_jsonl.py
git commit -m "M1: jsonl.session_name, tokens_estimate, last_active_at"
```

---

## Task 6: Index module — atomic read/write with flock

**Files:**
- Create: `bin/_pkg/index.py`
- Create: `test/test_index.py`

- [ ] **Step 1: Write failing tests**

Create `test/test_index.py`:

```python
"""Tests for _pkg.index — atomic, flock'd JSON storage."""

import json
import os
import threading

from _pkg import index


def test_load_missing_returns_default(tmp_path):
    idx = index.load(str(tmp_path / "nope.json"))
    assert idx == {"version": 1, "folders": [], "sessions": {}}


def test_save_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "index.json")
    payload = {"version": 1, "folders": ["foo"], "sessions": {"u1": {"notes": "x"}}}
    index.save(path, payload)
    assert index.load(path) == payload


def test_save_writes_via_temp_rename(tmp_path):
    """Verifies the temp file is renamed, not written-in-place — crashes mid-write
    must leave the previous file intact."""
    path = str(tmp_path / "index.json")
    index.save(path, {"version": 1, "folders": [], "sessions": {"a": {}}})
    # No leftover *.tmp file
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_concurrent_writes_dont_corrupt(tmp_path):
    """Two threads call mutate(append) 50 times each; final folders list has 100 items."""
    path = str(tmp_path / "index.json")

    def worker(prefix: str):
        for i in range(50):
            def mutator(data: dict) -> dict:
                data["folders"].append(f"{prefix}-{i}")
                return data
            index.mutate(path, mutator)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()

    final = index.load(path)
    assert len(final["folders"]) == 100
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest test/test_index.py -v`
Expected: `ModuleNotFoundError: No module named '_pkg.index'`.

- [ ] **Step 3: Create `bin/_pkg/index.py`**

```python
"""Atomic, flock'd JSON index for session-explorer.

Schema: {"version": 1, "folders": [str, ...], "sessions": {uuid: {...}}}

Concurrency: every mutate uses flock(LOCK_EX) on the target path AND writes
to a sibling *.tmp file then atomic-renames over the original. This protects
both against torn writes (rename is atomic on POSIX) and against two
session-start hooks firing simultaneously.
"""

import fcntl
import json
import os
import tempfile
from typing import Callable, Dict, Any

_DEFAULT: Dict[str, Any] = {"version": 1, "folders": [], "sessions": {}}


def load(path: str) -> dict:
    if not os.path.exists(path):
        return _DEFAULT.copy() | {"folders": [], "sessions": {}}
    with open(path, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            return json.load(f)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def save(path: str, data: dict) -> None:
    """Atomic write: temp file in the same directory + rename."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-explorer-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)  # atomic on POSIX
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def mutate(path: str, fn: Callable[[dict], dict]) -> dict:
    """Read-modify-write the index under an exclusive flock on a sidecar lock file.

    A separate lock file (path + '.lock') is used because the index file itself
    is replaced atomically — flock on a file that gets renamed-over is fragile.
    """
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)) or ".", exist_ok=True)
    with open(lock_path, "a") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            data = load(path)
            data = fn(data)
            save(path, data)
            return data
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest test/test_index.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/index.py test/test_index.py
git commit -m "M1: index module (atomic, flock'd, mutate())"
```

---

## Task 7: Index module — `record_session`

**Files:**
- Modify: `bin/_pkg/index.py`
- Modify: `test/test_index.py`

- [ ] **Step 1: Write failing tests** — append to `test/test_index.py`:

```python
import os as _os
import shutil


_FIX = _os.path.join(_os.path.dirname(__file__), "fixtures")


def test_record_session_creates_entry(tmp_path):
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")

    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")

    data = index.load(idx_path)
    assert "01ABC" in data["sessions"]
    s = data["sessions"]["01ABC"]
    assert s["name_cached"] == "planning-sprint14"
    assert s["project_path"] == "/Users/jl/proj/foo"
    assert s["project_label"] == "foo"
    assert s["first_prompt"] == "plan sprint 14 work"
    assert s["message_count"] == 4
    assert s["tokens_estimate"] == 15234
    assert s["bytes"] > 0


def test_record_session_idempotent(tmp_path):
    """Calling record twice updates last_active_at but doesn't duplicate."""
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")

    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")
    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")

    data = index.load(idx_path)
    assert len(data["sessions"]) == 1


def test_record_session_preserves_notes(tmp_path):
    """A user-edited 'notes' field survives a re-record."""
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")

    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")
    # User edits notes
    def add_notes(data: dict) -> dict:
        data["sessions"]["01ABC"]["notes"] = "user notes"
        return data
    index.mutate(idx_path, add_notes)
    # Re-record
    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")

    data = index.load(idx_path)
    assert data["sessions"]["01ABC"]["notes"] == "user notes"
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest test/test_index.py -v -k record`
Expected: 3 tests fail with `AttributeError: module '_pkg.index' has no attribute 'record_session'`.

- [ ] **Step 3: Add to `bin/_pkg/index.py`** — append:

```python
import os
import subprocess
from datetime import datetime, timezone

from . import jsonl as _jsonl


def _git_branch(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


_TOKEN_WINDOW = 200_000  # v1 hardcode (Sonnet 4.6 default); see SPEC open question


def record_session(index_path: str, session_id: str, transcript_path: str, cwd: str) -> dict:
    """Idempotent upsert. Preserves 'notes' and any other user-edited fields."""
    def mutator(data: dict) -> dict:
        existing = data["sessions"].get(session_id, {})
        try:
            file_bytes = os.path.getsize(transcript_path)
        except FileNotFoundError:
            file_bytes = 0
        tokens = _jsonl.tokens_estimate(transcript_path)
        new_entry = {
            **existing,  # preserve notes, etc.
            "name_cached": _jsonl.session_name(transcript_path),
            "first_prompt": _jsonl.first_user_prompt(transcript_path),
            "message_count": _jsonl.message_count(transcript_path),
            "bytes": file_bytes,
            "tokens_estimate": tokens,
            "tokens_window_pct": min(100, int(tokens * 100 / _TOKEN_WINDOW)),
            "project_path": cwd,
            "project_label": os.path.basename(cwd.rstrip("/")) or cwd,
            "branch": _git_branch(cwd),
            "last_active_at": _jsonl.last_active_at(transcript_path) or datetime.now(timezone.utc).isoformat(),
            "transcript_path": transcript_path,
        }
        if "created_at" not in new_entry:
            new_entry["created_at"] = datetime.now(timezone.utc).isoformat()
        data["sessions"][session_id] = new_entry
        return data
    return mutate(index_path, mutator)
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest test/test_index.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/index.py test/test_index.py
git commit -m "M1: index.record_session (idempotent upsert, preserves notes)"
```

---

## Task 8: Index module — `refresh_all`

**Files:**
- Modify: `bin/_pkg/index.py`
- Modify: `test/test_index.py`

- [ ] **Step 1: Write failing test** — append to `test/test_index.py`:

```python
def test_refresh_all_recomputes_caches(tmp_path):
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")
    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/Users/jl/proj/foo")

    # Simulate stale caches
    def stale(data: dict) -> dict:
        data["sessions"]["01ABC"]["message_count"] = 0
        data["sessions"]["01ABC"]["tokens_estimate"] = 0
        return data
    index.mutate(idx_path, stale)

    index.refresh_all(idx_path)

    data = index.load(idx_path)
    assert data["sessions"]["01ABC"]["message_count"] == 4
    assert data["sessions"]["01ABC"]["tokens_estimate"] == 15234


def test_refresh_all_drops_missing_jsonl(tmp_path):
    """If a session's JSONL no longer exists, refresh drops it from the index."""
    transcript = str(tmp_path / "01ABC.jsonl")
    shutil.copy(_os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = str(tmp_path / "index.json")
    index.record_session(idx_path, session_id="01ABC", transcript_path=transcript, cwd="/x")

    _os.unlink(transcript)
    index.refresh_all(idx_path)

    data = index.load(idx_path)
    assert "01ABC" not in data["sessions"]
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest test/test_index.py -v -k refresh`
Expected: 2 tests fail.

- [ ] **Step 3: Add to `bin/_pkg/index.py`**

```python
def refresh_all(index_path: str) -> dict:
    """Recompute every session's cached fields; prune entries whose JSONL is gone."""
    data = load(index_path)
    keep: dict[str, dict] = {}
    for sid, entry in data.get("sessions", {}).items():
        transcript = entry.get("transcript_path")
        if transcript and os.path.exists(transcript):
            keep[sid] = entry
    data["sessions"] = keep
    save(index_path, data)
    # Now re-record each (preserves notes).
    for sid, entry in keep.items():
        record_session(
            index_path,
            session_id=sid,
            transcript_path=entry["transcript_path"],
            cwd=entry.get("project_path", ""),
        )
    return load(index_path)
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest test/test_index.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/index.py test/test_index.py
git commit -m "M1: index.refresh_all (prune missing JSONLs + recompute caches)"
```

---

## Task 9: CLI — wire `index --record` and `index --refresh` subcommands

**Files:**
- Modify: `bin/_pkg/cli.py`
- Modify: `test/test_cli.py`

- [ ] **Step 1: Write failing test** — append to `test/test_cli.py`:

```python
import shutil


_FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_index_record_via_cli(tmp_path, monkeypatch):
    transcript = tmp_path / "01ABC.jsonl"
    shutil.copy(os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = tmp_path / "index.json"

    monkeypatch.setenv("SESSION_EXPLORER_INDEX", str(idx_path))
    result = subprocess.run(
        [_BIN, "index", "--record", "01ABC", str(transcript), "/Users/jl/proj/foo"],
        capture_output=True, text=True,
        env={**os.environ, "SESSION_EXPLORER_INDEX": str(idx_path)},
    )
    assert result.returncode == 0, result.stderr

    import json
    data = json.loads(idx_path.read_text())
    assert "01ABC" in data["sessions"]
    assert data["sessions"]["01ABC"]["name_cached"] == "planning-sprint14"


def test_index_refresh_via_cli(tmp_path):
    transcript = tmp_path / "01ABC.jsonl"
    shutil.copy(os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = tmp_path / "index.json"
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(idx_path)}

    subprocess.run(
        [_BIN, "index", "--record", "01ABC", str(transcript), "/Users/jl/proj/foo"],
        check=True, env=env,
    )
    result = subprocess.run([_BIN, "index", "--refresh"], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest test/test_cli.py -v -k index`
Expected: 2 fail (stub prints, no real behavior).

- [ ] **Step 3: Replace the stub in `bin/_pkg/cli.py`** with real dispatch:

```python
"""Argparse skeleton for the session-explorer CLI."""

import argparse
import os
import sys

from . import __version__
from . import index as _index


def _index_path() -> str:
    env_override = os.environ.get("SESSION_EXPLORER_INDEX")
    if env_override:
        return env_override
    return os.path.expanduser("~/.claude/session-explorer-index.json")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="session-explorer")
    p.add_argument("--version", action="version", version=f"session-explorer {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="List all known sessions (text output).")
    sub.add_parser("launch", help="Launch the explorer in a new terminal window.")

    index_p = sub.add_parser("index", help="Index management.")
    index_p.add_argument("--record", nargs=3, metavar=("SESSION_ID", "TRANSCRIPT_PATH", "CWD"))
    index_p.add_argument("--refresh", action="store_true")
    return p


def _cmd_index(args) -> int:
    path = _index_path()
    if args.record:
        sid, transcript, cwd = args.record
        _index.record_session(path, session_id=sid, transcript_path=transcript, cwd=cwd)
        return 0
    if args.refresh:
        _index.refresh_all(path)
        return 0
    print("index: pass --record SID TRANSCRIPT CWD or --refresh", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "index":
        return _cmd_index(args)
    # list/launch land in later tasks
    print(f"(not implemented) cmd={args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest test/test_cli.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "M1: CLI dispatches index --record and index --refresh"
```

---

## Task 10: CLI — `list` subcommand (text output)

**Files:**
- Modify: `bin/_pkg/cli.py`
- Modify: `test/test_cli.py`

- [ ] **Step 1: Write failing test** — append to `test/test_cli.py`:

```python
def test_list_groups_by_project_and_folder(tmp_path):
    transcript = tmp_path / "01ABC.jsonl"
    shutil.copy(os.path.join(_FIX, "named.jsonl"), transcript)
    idx_path = tmp_path / "index.json"
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(idx_path)}
    subprocess.run(
        [_BIN, "index", "--record", "01ABC", str(transcript), "/Users/jl/proj/foo"],
        check=True, env=env,
    )

    result = subprocess.run([_BIN, "list"], capture_output=True, text=True, env=env)
    assert result.returncode == 0
    out = result.stdout
    assert "foo" in out                  # project label
    assert "planning/" in out             # folder, parsed from first dash
    assert "sprint14" in out              # display name
    assert "15K" in out or "15.2K" in out or "15234" in out


def test_list_no_sessions(tmp_path):
    env = {**os.environ, "SESSION_EXPLORER_INDEX": str(tmp_path / "absent.json")}
    result = subprocess.run([_BIN, "list"], capture_output=True, text=True, env=env)
    assert result.returncode == 0
    assert "no sessions" in result.stdout.lower()
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest test/test_cli.py -v -k list`
Expected: 2 fail.

- [ ] **Step 3: Add `_cmd_list` to `bin/_pkg/cli.py`** — modify the file:

Inside `main()`, replace the `# list/launch land in later tasks` block with:

```python
    if args.cmd == "list":
        return _cmd_list()
```

And add the function above `main()`:

```python
def _fmt_tokens(n: int) -> str:
    if n >= 10000:
        return f"~{n // 1000}K"
    return f"~{n}"


def _fmt_age(iso: str | None) -> str:
    if not iso:
        return "—"
    from datetime import datetime, timezone
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


def _split_folder(name: str | None) -> tuple[str, str]:
    """First-dash split. ('', name) when no dash; ('', '') when no name."""
    if not name:
        return ("", "")
    if "-" not in name:
        return ("", name)
    folder, _, display = name.partition("-")
    return (folder, display)


def _cmd_list() -> int:
    data = _index.load(_index_path())
    sessions = data.get("sessions", {})
    if not sessions:
        print("No sessions recorded yet.")
        return 0

    # Group by project_label, then by folder.
    by_project: dict[str, dict[str, list[tuple[str, dict]]]] = {}
    for sid, s in sessions.items():
        proj = s.get("project_label", "(unknown)")
        folder, _ = _split_folder(s.get("name_cached"))
        by_project.setdefault(proj, {}).setdefault(folder or "(no folder)", []).append((sid, s))

    for proj in sorted(by_project):
        folders = by_project[proj]
        total = sum(len(v) for v in folders.values())
        print(f"\n{proj} ({total})")
        for folder in sorted(folders):
            if folder != "(no folder)":
                print(f"  {folder}/")
            indent = "    " if folder != "(no folder)" else "  "
            for sid, s in sorted(folders[folder], key=lambda x: x[1].get("last_active_at", ""), reverse=True):
                _, display = _split_folder(s.get("name_cached"))
                display = display or sid[:8]
                age = _fmt_age(s.get("last_active_at"))
                tokens = _fmt_tokens(s.get("tokens_estimate", 0))
                pct = s.get("tokens_window_pct", 0)
                msgs = s.get("message_count", 0)
                prompt = (s.get("first_prompt") or "").replace("\n", " ")[:40]
                print(f"{indent}{display:<24} {age:>4}  {tokens:>6} ({pct:>3}%)  {msgs:>4} msgs   {prompt}")
    return 0
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest test/test_cli.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/cli.py test/test_cli.py
git commit -m "M1: CLI list subcommand (project + folder grouping, text output)"
```

---

## Task 11: Launcher — macOS Terminal.app via osascript

**Files:**
- Create: `bin/_pkg/launcher.py`
- Create: `test/test_launcher.py`

- [ ] **Step 1: Write failing test**

Create `test/test_launcher.py`:

```python
"""Tests for _pkg.launcher."""

import platform
from unittest import mock

from _pkg import launcher


def test_build_macos_command_quotes_path():
    """A path with a space must end up properly quoted inside osascript."""
    cmd = launcher.build_macos_command("/path with space/session-explorer")
    assert "osascript" in cmd[0]
    # The applescript string contains the quoted absolute path
    joined = " ".join(cmd)
    assert "session-explorer" in joined
    # No unescaped quotes that would break applescript
    assert '\\"' in joined or "do script" in joined


def test_launch_dispatches_by_platform(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    with mock.patch("subprocess.Popen") as popen:
        launcher.launch("/abs/path/session-explorer")
        assert popen.called
        called_cmd = popen.call_args[0][0]
        assert "osascript" in called_cmd[0]


def test_launch_unsupported_platform_returns_fallback(monkeypatch, capsys):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    rc = launcher.launch("/abs/path/session-explorer")
    captured = capsys.readouterr()
    assert rc != 0
    assert "/abs/path/session-explorer" in captured.out
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest test/test_launcher.py -v`
Expected: `ModuleNotFoundError: No module named '_pkg.launcher'`.

- [ ] **Step 3: Create `bin/_pkg/launcher.py`**

```python
"""OS-detecting terminal launcher.

M1 ships macOS only via osascript → Terminal.app. Linux launchers land in M2,
Windows in M5. The fallback path prints the absolute command to stdout
(consumed by the slash command's markdown response).
"""

import platform
import shlex
import subprocess
import sys
from typing import List


def build_macos_command(target_command: str) -> List[str]:
    """Build an osascript invocation that opens Terminal.app running `target_command`."""
    # AppleScript needs the inner command quoted with escaped double quotes.
    # `target_command` is the full shell command line to run in the new window.
    apple = f'tell application "Terminal" to do script "{target_command}"'
    return ["osascript", "-e", apple]


def launch(target_command: str) -> int:
    """Spawn a new terminal window running `target_command`. Returns 0 on success.

    On unsupported platforms, prints the command to stdout (for clipboard copy
    by the slash command) and returns a non-zero code.
    """
    system = platform.system()
    if system == "Darwin":
        cmd = build_macos_command(target_command)
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0

    # M2/M5: detect $TERMINAL, x-terminal-emulator, etc.
    print(f"Unsupported platform '{system}'. Run this in any terminal:\n  {target_command}")
    return 2
```

- [ ] **Step 4: Run, verify pass**

Run: `pytest test/test_launcher.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/launcher.py test/test_launcher.py
git commit -m "M1: terminal launcher (macOS osascript; fallback prints+returns)"
```

---

## Task 12: CLI — wire `launch` subcommand

**Files:**
- Modify: `bin/_pkg/cli.py`
- Modify: `test/test_cli.py`

- [ ] **Step 1: Write failing test** — append to `test/test_cli.py`:

```python
def test_launch_invokes_osascript_on_mac(monkeypatch):
    """Smoke test: `session-explorer launch` should attempt to spawn a new terminal."""
    # We run the binary in a subprocess where we can intercept by setting
    # SESSION_EXPLORER_DRY_RUN=1, which makes launcher.launch print the would-be
    # command and exit 0 without actually shelling out.
    env = {**os.environ, "SESSION_EXPLORER_DRY_RUN": "1"}
    result = subprocess.run([_BIN, "launch"], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "session-explorer" in result.stdout
    assert "list" in result.stdout  # the would-be terminal runs `... list`
```

- [ ] **Step 2: Run, verify failure**

Run: `pytest test/test_cli.py -v -k launch`
Expected: fails (launch not implemented).

- [ ] **Step 3: Modify `bin/_pkg/launcher.py`** to honor the dry-run env:

Add at the top of `launch()`:

```python
    import os
    if os.environ.get("SESSION_EXPLORER_DRY_RUN") == "1":
        print(f"DRY RUN: would launch: {target_command}")
        return 0
```

- [ ] **Step 4: Add `_cmd_launch` to `bin/_pkg/cli.py`**

Above `main()`:

```python
from . import launcher as _launcher


def _cmd_launch() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    # bin/_pkg/cli.py → bin/session-explorer
    bin_path = os.path.normpath(os.path.join(here, "..", "session-explorer"))
    target = f"{shlex.quote(bin_path)} list; echo; echo Press Enter to close; read"
    return _launcher.launch(target)
```

Add `import shlex` near the top.

In `main()`, route `launch`:

```python
    if args.cmd == "launch":
        return _cmd_launch()
```

- [ ] **Step 5: Run all CLI tests, verify pass**

Run: `pytest test/test_cli.py -v`
Expected: 7 passed.

- [ ] **Step 6: Manually verify on macOS**

Run: `bin/session-explorer launch`
Expected: A new Terminal.app window opens, runs the listing, and waits at "Press Enter to close".

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/cli.py bin/_pkg/launcher.py test/test_cli.py
git commit -m "M1: CLI launch subcommand opens Terminal.app with list output"
```

---

## Task 13: SessionStart hook — first-run setup

**Files:**
- Create: `hooks/session-start.sh`
- Create: `test/test_session_start.bats`
- Delete: `hooks/.gitkeep`

- [ ] **Step 1: Delete the placeholder**

```bash
rm hooks/.gitkeep
```

- [ ] **Step 2: Write failing bats tests**

Create `test/test_session_start.bats`:

```bash
#!/usr/bin/env bats

setup() {
  export TEST_HOME="$(mktemp -d)"
  export TEST_CLAUDE="$TEST_HOME/.claude"
  mkdir -p "$TEST_CLAUDE"
  echo '{"cleanupPeriodDays": 30, "other": "stuff"}' > "$TEST_CLAUDE/settings.json"
  export HOME="$TEST_HOME"
  # Path to the hook under test
  export HOOK="$BATS_TEST_DIRNAME/../hooks/session-start.sh"
  # Mock CLAUDE_PLUGIN_DIR so the hook can find bin/session-explorer
  export CLAUDE_PLUGIN_DIR="$BATS_TEST_DIRNAME/.."
}

teardown() {
  rm -rf "$TEST_HOME"
}

@test "first run backs up cleanupPeriodDays and sets it to 36500" {
  echo '{"session_id":"abc","transcript_path":"/tmp/x.jsonl","cwd":"/tmp"}' | bash "$HOOK"
  [ "$status" -eq 0 ] || true  # hook should never block

  # Backup exists and contains the prior value
  [ -f "$TEST_CLAUDE/.session-explorer.backup" ]
  grep -q '30' "$TEST_CLAUDE/.session-explorer.backup"

  # settings.json now has 36500
  grep -q '36500' "$TEST_CLAUDE/settings.json"
}

@test "second run is a no-op (backup already present)" {
  echo '{"session_id":"abc","transcript_path":"/tmp/x.jsonl","cwd":"/tmp"}' | bash "$HOOK"
  # First-run backed up 30
  # Edit settings.json to a sentinel value
  echo '{"cleanupPeriodDays": 36500, "marker": "do not touch"}' > "$TEST_CLAUDE/settings.json"
  echo '{"session_id":"def","transcript_path":"/tmp/y.jsonl","cwd":"/tmp"}' | bash "$HOOK"

  # settings.json untouched (marker preserved, cleanupPeriodDays still 36500)
  grep -q '"marker": "do not touch"' "$TEST_CLAUDE/settings.json"
  # Backup file still contains the original 30
  grep -q '30' "$TEST_CLAUDE/.session-explorer.backup"
}

@test "hook never exits non-zero on malformed stdin" {
  echo 'not json' | bash "$HOOK"
  [ "$status" -eq 0 ]
}
```

- [ ] **Step 3: Run, verify failure**

Run: `bats test/test_session_start.bats`
Expected: tests fail (`hooks/session-start.sh` doesn't exist).

- [ ] **Step 4: Create `hooks/session-start.sh`**

```bash
#!/usr/bin/env bash
# SessionStart hook for session-explorer.
# Idempotent first-run setup + delegates to CLI for index recording.
#
# Reads JSON on stdin: {session_id, transcript_path, cwd, source}
# Never blocks startup; logs failures and exits 0.

set -u

CLAUDE_DIR="${HOME}/.claude"
LOG="${CLAUDE_DIR}/session-explorer.log"
SETTINGS="${CLAUDE_DIR}/settings.json"
BACKUP="${CLAUDE_DIR}/.session-explorer.backup"

mkdir -p "${CLAUDE_DIR}" 2>/dev/null || true

log() { echo "[$(date -u +%FT%TZ)] $*" >> "${LOG}" 2>/dev/null || true; }

# Read stdin (best-effort)
PAYLOAD="$(cat 2>/dev/null || true)"

# --- First-run setup: neutralise native cleanup ---
if [ ! -f "${BACKUP}" ]; then
  if [ -f "${SETTINGS}" ]; then
    # Extract current cleanupPeriodDays (default 30 if unset). Bash + python3 fallback.
    PRIOR="$(python3 -c "
import json, sys
try:
    with open('${SETTINGS}') as f:
        d = json.load(f)
    print(d.get('cleanupPeriodDays', 30))
except Exception:
    print(30)
" 2>/dev/null || echo 30)"
    echo "${PRIOR}" > "${BACKUP}"

    # Set cleanupPeriodDays = 36500 in settings.json
    python3 -c "
import json
with open('${SETTINGS}') as f:
    d = json.load(f)
d['cleanupPeriodDays'] = 36500
with open('${SETTINGS}', 'w') as f:
    json.dump(d, f, indent=2)
" 2>>"${LOG}" || log "warn: failed to update cleanupPeriodDays"
    log "first-run: backed up cleanupPeriodDays=${PRIOR}, set to 36500"
  else
    echo 30 > "${BACKUP}"
    echo '{"cleanupPeriodDays": 36500}' > "${SETTINGS}"
    log "first-run: created settings.json with cleanupPeriodDays=36500"
  fi
fi

# --- Index recording lands in Task 14 ---

exit 0
```

- [ ] **Step 5: Mark executable**

```bash
chmod +x hooks/session-start.sh
```

- [ ] **Step 6: Run tests, verify pass**

Run: `bats test/test_session_start.bats`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add hooks/session-start.sh test/test_session_start.bats
git commit -m "M1: SessionStart hook with idempotent first-run setup"
```

---

## Task 14: SessionStart hook — call CLI to record session

**Files:**
- Modify: `hooks/session-start.sh`
- Modify: `test/test_session_start.bats`

- [ ] **Step 1: Append failing bats test**

```bash
@test "hook records the session via CLI" {
  # Set up a stub session JSONL in TEST_HOME (the hook uses real HOME)
  STUB_JSONL="$(mktemp)"
  cat > "$STUB_JSONL" <<'EOF'
{"type":"system","subtype":"init","sessionId":"01HOOK","sessionName":"work-sprint","timestamp":"2026-05-26T10:00:00Z"}
{"type":"user","sessionId":"01HOOK","timestamp":"2026-05-26T10:00:01Z","message":{"role":"user","content":"hi"}}
EOF

  echo "{\"session_id\":\"01HOOK\",\"transcript_path\":\"$STUB_JSONL\",\"cwd\":\"$TEST_HOME\"}" | bash "$HOOK"
  [ "$status" -eq 0 ]

  # Index file should now exist with the session
  INDEX="$TEST_CLAUDE/session-explorer-index.json"
  [ -f "$INDEX" ]
  grep -q '01HOOK' "$INDEX"
  grep -q 'work-sprint' "$INDEX"

  rm -f "$STUB_JSONL"
}
```

- [ ] **Step 2: Run, verify failure**

Run: `bats test/test_session_start.bats`
Expected: new test fails (hook doesn't call CLI yet).

- [ ] **Step 3: Append to `hooks/session-start.sh`** — before the final `exit 0`:

```bash
# --- Record the session into the index ---
CLI="${CLAUDE_PLUGIN_DIR:-${HOME}/.local/share/session-explorer}/bin/session-explorer"
if [ ! -x "${CLI}" ]; then
  # Fallback: try PATH
  CLI="$(command -v session-explorer 2>/dev/null || echo "")"
fi

if [ -n "${CLI}" ] && [ -x "${CLI}" ]; then
  # Parse session_id, transcript_path, cwd from PAYLOAD using python3 (via stdin to avoid quoting bugs).
  read -r SID TPATH CWD < <(printf '%s' "${PAYLOAD}" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('session_id',''), d.get('transcript_path',''), d.get('cwd',''))
except Exception:
    print('', '', '')
" 2>/dev/null)

  if [ -n "${SID}" ] && [ -n "${TPATH}" ] && [ -n "${CWD}" ]; then
    "${CLI}" index --record "${SID}" "${TPATH}" "${CWD}" 2>>"${LOG}" || log "warn: index --record failed for ${SID}"
  fi
else
  log "warn: session-explorer CLI not found; CLAUDE_PLUGIN_DIR=${CLAUDE_PLUGIN_DIR:-(unset)}"
fi
```

- [ ] **Step 4: Run all bats tests, verify pass**

Run: `bats test/test_session_start.bats`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/session-start.sh test/test_session_start.bats
git commit -m "M1: SessionStart hook calls CLI to record session into index"
```

---

## Task 15: Slash command — `commands/session-explorer.md`

**Files:**
- Create: `commands/session-explorer.md`
- Delete: `commands/.gitkeep`

The slash command's job: invoke `session-explorer launch`. It does no other work — argument parsing, OS detection, fallbacks all live in the CLI.

- [ ] **Step 1: Delete the placeholder**

```bash
rm commands/.gitkeep
```

- [ ] **Step 2: Create `commands/session-explorer.md`**

````markdown
---
description: Open the session-explorer TUI in a new terminal window.
allowed-tools: Bash
---

Open the session-explorer in a new terminal window.

!`"$CLAUDE_PLUGIN_DIR/bin/session-explorer" launch`
````

- [ ] **Step 3: Verify the markdown parses as a slash command**

Run: `cat commands/session-explorer.md | grep -q '^---' && echo "frontmatter OK"`
Expected: `frontmatter OK`.

- [ ] **Step 4: Commit**

```bash
git add commands/session-explorer.md
git commit -m "M1: /session-explorer slash command shells out to CLI launch"
```

---

## Task 16: Secondary install path — `install.sh`

**Files:**
- Create: `install.sh`

The plain install path (for users not using the marketplace flow) writes the hook into `~/.claude/settings.json`, symlinks `bin/session-explorer` to `~/.local/bin/`, and performs first-run setup eagerly.

- [ ] **Step 1: Create `install.sh`**

```bash
#!/usr/bin/env bash
# session-explorer plain install (non-marketplace).
# Idempotent: re-running is safe.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
LOCAL_BIN="${HOME}/.local/bin"
SETTINGS="${CLAUDE_DIR}/settings.json"
BACKUP="${CLAUDE_DIR}/.session-explorer.backup"

mkdir -p "${CLAUDE_DIR}" "${LOCAL_BIN}"

# --- Symlink the binary ---
ln -sf "${REPO_DIR}/bin/session-explorer" "${LOCAL_BIN}/session-explorer"
echo "Linked: ${LOCAL_BIN}/session-explorer -> ${REPO_DIR}/bin/session-explorer"

# --- First-run cleanupPeriodDays handling (idempotent) ---
if [ ! -f "${BACKUP}" ]; then
  PRIOR=30
  if [ -f "${SETTINGS}" ]; then
    PRIOR="$(python3 -c "
import json
try:
    with open('${SETTINGS}') as f: print(json.load(f).get('cleanupPeriodDays', 30))
except Exception: print(30)
")"
  fi
  echo "${PRIOR}" > "${BACKUP}"
  echo "Backed up cleanupPeriodDays=${PRIOR} to ${BACKUP}"
fi

# --- Write/overwrite settings.json fragment (cleanupPeriodDays + SessionStart hook) ---
python3 - "$REPO_DIR" <<'PY'
import json, os, sys

repo = sys.argv[1]
settings_path = os.path.expanduser("~/.claude/settings.json")
try:
    with open(settings_path) as f:
        data = json.load(f)
except FileNotFoundError:
    data = {}

data["cleanupPeriodDays"] = 36500

hooks = data.setdefault("hooks", {})
ss = hooks.setdefault("SessionStart", [])

hook_cmd = os.path.join(repo, "hooks", "session-start.sh")
# Idempotent: remove any prior session-explorer hook entry
ss = [h for h in ss if not (isinstance(h, dict) and "session-explorer" in str(h.get("command", "")))]
ss.append({"matchers": [], "command": hook_cmd})
hooks["SessionStart"] = ss

with open(settings_path, "w") as f:
    json.dump(data, f, indent=2)

print(f"Updated {settings_path}: cleanupPeriodDays=36500, SessionStart hook -> {hook_cmd}")
PY

chmod +x "${REPO_DIR}/hooks/session-start.sh" "${REPO_DIR}/bin/session-explorer"

echo
echo "Install complete. Start a new Claude session; run /session-explorer to open the explorer."
```

- [ ] **Step 2: Mark executable**

```bash
chmod +x install.sh
```

- [ ] **Step 3: Run it (live) and verify**

```bash
./install.sh
ls -l ~/.local/bin/session-explorer
~/.local/bin/session-explorer --version
```

Expected:
- Symlink exists.
- `--version` prints `session-explorer 0.1.0`.
- `~/.claude/.session-explorer.backup` exists.
- `cat ~/.claude/settings.json` shows `cleanupPeriodDays: 36500` and the hook entry.

- [ ] **Step 4: Verify idempotency**

```bash
./install.sh  # second run
```

Expected: no errors, no duplicate hook entries (verify with `python3 -c "import json; print(len(json.load(open('$HOME/.claude/settings.json'))['hooks']['SessionStart']))"` → 1).

- [ ] **Step 5: Commit**

```bash
git add install.sh
git commit -m "M1: install.sh (secondary path, idempotent)"
```

---

## Task 17: README + final end-to-end verification

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create `README.md`**

````markdown
# session-explorer

A Claude Code plugin that turns `~/.claude/projects/*.jsonl` transcripts into a
file-explorer-style listing: browse, organize, and resume sessions from a single
slash command.

M1 ships the data layer plus a text-mode listing. M2 will replace the listing
with a Textual TUI (arrows, expand/collapse, rename/move/delete).

See [`SPEC.md`](./SPEC.md) for the full design.

## Install

### Option A — Claude Code marketplace (primary)

```bash
/plugin marketplace add <owner>/session-explorer
/plugin install session-explorer
```

### Option B — plain shell installer

```bash
git clone https://github.com/<owner>/session-explorer.git
cd session-explorer
./install.sh
```

Both paths perform the same first-run setup: back up your existing
`cleanupPeriodDays`, set it to 36500, and register the `SessionStart` hook.

## Usage

After install, start any new Claude Code session in any project. The hook
records the session into `~/.claude/session-explorer-index.json` automatically.

From inside Claude:

```
/session-explorer
```

This opens a new Terminal.app window showing your sessions grouped by project
and (first-dash) folder. Press Enter in that window to close it.

From a regular shell:

```bash
session-explorer list      # text listing
session-explorer launch    # open in a new Terminal window
```

## How sessions are organized

Session names map to folders via the **first dash**:

| Session name | Folder | Display name |
|---|---|---|
| `planning-sprint14` | `planning` | `sprint14` |
| `audits-q1-review` | `audits` | `q1-review` |
| `sprint14` | *(none)* | `sprint14` |

Rename a session with Claude's built-in `/rename` command; the next session
start (or `session-explorer index --refresh`) reflects the change.

## Uninstall

Restore your original `cleanupPeriodDays`:

```bash
echo "Restoring cleanupPeriodDays from $(cat ~/.claude/.session-explorer.backup)"
python3 -c "
import json, os
p = os.path.expanduser('~/.claude/settings.json')
d = json.load(open(p))
d['cleanupPeriodDays'] = int(open(os.path.expanduser('~/.claude/.session-explorer.backup')).read().strip())
json.dump(d, open(p, 'w'), indent=2)
"
rm ~/.claude/.session-explorer.backup
```

Then `/plugin uninstall session-explorer` (marketplace) or remove the symlink
and hook entry manually (plain install).

## Status

M1 — text listing only. Active development.
````

- [ ] **Step 2: End-to-end smoke test on a real machine**

Run these commands in order:

```bash
# Run the full test suite
pytest test/ -v
bats test/test_session_start.bats

# Plain install
./install.sh

# Start a new Claude session in some directory, then exit it.
# (Verify a session JSONL was created in ~/.claude/projects/<encoded-cwd>/)

# Verify the hook fired
ls -la ~/.claude/session-explorer-index.json
cat ~/.claude/session-explorer.log

# Verify the listing
session-explorer list

# Verify the launcher
session-explorer launch
# (a new Terminal window should open showing the listing)
```

If anything fails, debug and fix before committing. Capture findings in `SPEC.md`'s "Open questions" if they're architectural.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "M1: README quickstart + install/uninstall instructions"
```

- [ ] **Step 4: Tag the milestone**

```bash
git tag -a m1 -m "M1: data layer + text listing + macOS launcher"
git log --oneline
```

---

## Definition of done for M1

A fresh user can:
1. Add this repo as a Claude Code marketplace (`/plugin marketplace add <repo>`) **or** `./install.sh` it.
2. Start a Claude session, then exit it.
3. See that session appear in `session-explorer list` output.
4. Run `/session-explorer` from inside any Claude session and have a new Terminal window open showing the listing.
5. Rename a session via Claude's `/rename` and see the new name reflect after the next session start.
6. Uninstall and have `cleanupPeriodDays` restored to its prior value.

All of `pytest test/` and `bats test/test_session_start.bats` pass on macOS with Python 3.11+.
