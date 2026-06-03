# macOS Dock Launcher App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `session-explorer install-app` subcommand that builds a hand-rolled macOS `.app` bundle (custom icon, tmux-aware launcher) in `~/Applications` and best-effort pins it to the Dock, plus teardown on uninstall.

**Architecture:** A new pure-logic module `bin/_pkg/macapp.py` builds the `Info.plist` XML, the launcher shell script, and a "is this app already pinned?" predicate — all unit-testable cross-platform. A thin `_cmd_install_app` in `cli.py` wires the subcommand to filesystem/Dock side effects (macOS-guarded). The committed icon lives under a new `assets/` dir and is copied verbatim into the bundle. Teardown is a new best-effort step in the existing `uninstall.teardown()`.

**Tech Stack:** Python 3.11+ stdlib only (no new deps); macOS `defaults`/`lsregister`/`killall` for Dock + icon-cache; pytest for pure logic; bats (macOS-gated) for the real filesystem build.

---

## File Structure

- **Create** `bin/_pkg/macapp.py` — pure builders + side-effecting `install_app()`.
- **Create** `assets/app-icon.icns`, `assets/app-icon.png`, `assets/app-icon.svg` — committed icon art (copied from `/tmp/se-icon/`).
- **Modify** `bin/_pkg/cli.py` — register the `install-app` subparser + dispatch to `_cmd_install_app`.
- **Modify** `bin/_pkg/uninstall.py` — add a best-effort `_remove_mac_app()` step to `teardown()`.
- **Modify** `bin/_pkg/__init__.py` and `.claude-plugin/plugin.json` — version bump to `1.9.0`.
- **Create** `test/test_macapp.py` — pytest unit coverage of the pure builders.
- **Create** `test/macapp.bats` — macOS-gated end-to-end build test.
- **Modify** `README.md`, `SPEC.md`, `CHANGELOG.md` — docs.

A note on the icon source: the three art files currently sit in `/tmp/se-icon/` (`icon.icns`, `source.png`, `icon.svg`). `/tmp` is volatile, so Task 1 copies them into the repo immediately, before anything depends on them.

---

## Task 1: Commit the icon assets

**Files:**
- Create: `assets/app-icon.icns` (from `/tmp/se-icon/icon.icns`)
- Create: `assets/app-icon.png` (from `/tmp/se-icon/source.png`)
- Create: `assets/app-icon.svg` (from `/tmp/se-icon/icon.svg`)

- [ ] **Step 1: Copy the art into the repo**

```bash
cd /Volumes/Projects/ClaudeSessionExplorer
mkdir -p assets
cp /tmp/se-icon/icon.icns  assets/app-icon.icns
cp /tmp/se-icon/source.png assets/app-icon.png
cp /tmp/se-icon/icon.svg   assets/app-icon.svg
```

- [ ] **Step 2: Verify the icns is valid and the PNG is 1024²**

Run:
```bash
sips -g format assets/app-icon.icns | tail -1
sips -g pixelWidth -g pixelHeight assets/app-icon.png
```
Expected: `format: icns`, and `pixelWidth: 1024` / `pixelHeight: 1024`.

> If `/tmp/se-icon/` was cleared (e.g. after a reboot), regenerate from the SVG that lives in this conversation's design doc, or ask the user. The SVG is small and committed in Step 1 once copied.

- [ ] **Step 3: Commit**

```bash
git add assets/app-icon.icns assets/app-icon.png assets/app-icon.svg
git commit -m "assets: add session-explorer app icon (icns + png + svg source)"
```

---

## Task 2: Pure builder — `Info.plist` XML

**Files:**
- Create: `bin/_pkg/macapp.py`
- Test: `test/test_macapp.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_macapp.py
"""Unit tests for the pure builders in macapp (cross-platform; no FS/Dock)."""

from _pkg import macapp


def test_info_plist_has_icon_file_and_no_icon_name():
    xml = macapp.build_info_plist(name="Session Explorer", version="1.9.0")
    # The Automator trap: CFBundleIconName routes the icon through Assets.car and
    # overrides the loose icns. We must emit CFBundleIconFile and NEVER IconName.
    assert "<key>CFBundleIconFile</key>" in xml
    assert "<string>icon</string>" in xml
    assert "CFBundleIconName" not in xml


def test_info_plist_embeds_name_executable_and_version():
    xml = macapp.build_info_plist(name="My Explorer", version="2.3.4")
    assert "<string>My Explorer</string>" in xml
    assert "<key>CFBundleExecutable</key>" in xml
    assert "<string>session-explorer-launch</string>" in xml
    assert "<string>2.3.4</string>" in xml
    assert xml.startswith("<?xml")


def test_info_plist_escapes_name():
    xml = macapp.build_info_plist(name="A & B", version="1.9.0")
    assert "A &amp; B" in xml
    assert "A & B" not in xml.replace("A &amp; B", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_macapp.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '_pkg.macapp'`.

- [ ] **Step 3: Write minimal implementation**

```python
# bin/_pkg/macapp.py
"""Build and install a macOS .app bundle that launches the explorer.

The bundle is hand-rolled (no Automator/Xcode): a directory with Info.plist, a
shell-script executable, and an icns. Two reasons this exists rather than a
user-made Automator applet:

  1. PATH. A GUI-launched Automator script runs with a stripped PATH that omits
     /opt/homebrew/bin, so tmux is invisible and `launch` silently drops to its
     non-tmux fallback. Our launcher repairs PATH explicitly.
  2. Icon override. Automator applets ship Assets.car + a CFBundleIconName key,
     which modern macOS prefers over a replaced loose icns. We author Info.plist
     ourselves with CFBundleIconFile and NO CFBundleIconName.

The build* functions are pure (string in, string out) so they unit-test without
touching the filesystem or the Dock.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

BUNDLE_ID = "com.snojken.session-explorer.dock"
EXECUTABLE_NAME = "session-explorer-launch"


def build_info_plist(*, name: str, version: str) -> str:
    """Return the Info.plist XML for the bundle. Carries CFBundleIconFile and
    deliberately omits CFBundleIconName (the Automator icon-override trap)."""
    n = escape(name)
    v = escape(version)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>{n}</string>
    <key>CFBundleDisplayName</key>
    <string>{n}</string>
    <key>CFBundleIdentifier</key>
    <string>{BUNDLE_ID}</string>
    <key>CFBundleExecutable</key>
    <string>{EXECUTABLE_NAME}</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleVersion</key>
    <string>{v}</string>
    <key>CFBundleShortVersionString</key>
    <string>{v}</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSBackgroundOnly</key>
    <false/>
</dict>
</plist>
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_macapp.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/macapp.py test/test_macapp.py
git commit -m "feat(macapp): Info.plist builder (CFBundleIconFile, no IconName)"
```

---

## Task 3: Pure builder — launcher shell script

**Files:**
- Modify: `bin/_pkg/macapp.py`
- Test: `test/test_macapp.py`

- [ ] **Step 1: Write the failing test**

```python
# append to test/test_macapp.py
def test_launcher_repairs_path_for_tmux():
    sh = macapp.build_launcher_script()
    # /opt/homebrew/bin must be on PATH or tmux.available() returns False in the
    # GUI launch context and `launch` drops its tmux behaviour.
    assert 'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"' in sh
    assert sh.startswith("#!/bin/zsh")


def test_launcher_resolves_binary_with_fallbacks():
    sh = macapp.build_launcher_script()
    # Marketplace install path (versioned, changes on update) resolved at run time,
    # then PATH lookup, then the plain-install symlink.
    assert "installed_plugins.json" in sh
    assert "session-explorer@session-explorer" in sh
    assert "command -v session-explorer" in sh
    assert "$HOME/.local/bin/session-explorer" in sh
    assert 'exec "$CLI" launch' in sh
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_macapp.py -q`
Expected: FAIL with `AttributeError: module '_pkg.macapp' has no attribute 'build_launcher_script'`.

- [ ] **Step 3: Write minimal implementation**

Append to `bin/_pkg/macapp.py`:

```python
def build_launcher_script() -> str:
    """Return the bundle's MacOS/ executable: a zsh script that repairs PATH so
    tmux is discoverable, resolves the session-explorer binary in a way that
    survives plugin version bumps, and execs `launch`."""
    return r'''#!/bin/zsh
# Generated by `session-explorer install-app`. Do not edit by hand.
# Repair PATH: a GUI-launched app inherits a stripped PATH without Homebrew, so
# tmux would be invisible and `launch` would drop its tmux behaviour.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# Resolve the session-explorer binary. Marketplace installs live under a
# versioned cache path that changes on update, so resolve at run time rather
# than baking an absolute path into the bundle.
CLI="$(/usr/bin/python3 - <<'PY' 2>/dev/null
import json, os
f = os.path.expanduser("~/.claude/plugins/installed_plugins.json")
try:
    d = json.load(open(f))
except Exception:
    d = {}
e = d.get("plugins", {}).get("session-explorer@session-explorer", [])
p = (e[0].get("installPath", "") + "/bin/session-explorer") if e else ""
print(p if os.path.exists(p) else "")
PY
)"
[ -x "$CLI" ] || CLI="$(command -v session-explorer)"
[ -x "$CLI" ] || CLI="$HOME/.local/bin/session-explorer"
exec "$CLI" launch
'''
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_macapp.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/macapp.py test/test_macapp.py
git commit -m "feat(macapp): PATH-repairing launcher script with binary resolver"
```

---

## Task 4: Pure predicate — "is this app already pinned?"

**Files:**
- Modify: `bin/_pkg/macapp.py`
- Test: `test/test_macapp.py`

This predicate keeps Dock pinning idempotent: re-running `install-app` must not add a duplicate Dock tile. It operates on the parsed `persistent-apps` list (a list of dock-entry dicts as `defaults export ... | plistlib` yields), so it is pure and testable.

- [ ] **Step 1: Write the failing test**

```python
# append to test/test_macapp.py
def test_app_already_pinned_true_when_path_present():
    persistent = [
        {"tile-data": {"file-data": {"_CFURLString": "file:///Applications/Safari.app/"}}},
        {"tile-data": {"file-data": {"_CFURLString": "file:///Users/x/Applications/Session%20Explorer.app/"}}},
    ]
    assert macapp.app_already_pinned(persistent, "/Users/x/Applications/Session Explorer.app")


def test_app_already_pinned_false_when_absent():
    persistent = [
        {"tile-data": {"file-data": {"_CFURLString": "file:///Applications/Safari.app/"}}},
    ]
    assert not macapp.app_already_pinned(persistent, "/Users/x/Applications/Session Explorer.app")


def test_app_already_pinned_tolerates_malformed_entries():
    persistent = [{}, {"tile-data": {}}, "garbage", None]
    assert not macapp.app_already_pinned(persistent, "/Users/x/Applications/Session Explorer.app")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_macapp.py -q`
Expected: FAIL with `AttributeError: module '_pkg.macapp' has no attribute 'app_already_pinned'`.

- [ ] **Step 3: Write minimal implementation**

Append to `bin/_pkg/macapp.py` (add `from urllib.parse import unquote` and `import os` to the imports at the top of the file):

```python
def app_already_pinned(persistent_apps: list, app_path: str) -> bool:
    """True if any dock entry's file URL points at app_path. Tolerant of the
    malformed/partial entries real Dock plists accumulate."""
    target = os.path.realpath(app_path).rstrip("/")
    for entry in persistent_apps or []:
        try:
            url = entry["tile-data"]["file-data"]["_CFURLString"]
        except (TypeError, KeyError):
            continue
        if not isinstance(url, str) or not url.startswith("file://"):
            continue
        path = unquote(url[len("file://"):]).rstrip("/")
        if os.path.realpath(path).rstrip("/") == target:
            return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_macapp.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/macapp.py test/test_macapp.py
git commit -m "feat(macapp): idempotency predicate for Dock pinning"
```

---

## Task 5: Side-effecting `install_app()` — build the bundle on disk

**Files:**
- Modify: `bin/_pkg/macapp.py`
- Test: `test/test_macapp.py`

`install_app()` writes the bundle, then does best-effort icon-cache refresh and Dock pinning. The Dock/cache steps are split into private helpers so the bundle-build can be tested directly without invoking `defaults`/`killall`. The build step takes an explicit `icon_src` and `dest` so tests can point at a tmp dir.

- [ ] **Step 1: Write the failing test**

```python
# append to test/test_macapp.py
import os as _os
import plistlib


def test_build_bundle_creates_full_structure(tmp_path):
    icon = tmp_path / "src-icon.icns"
    icon.write_bytes(b"icns-bytes")
    app = macapp.build_bundle(
        dest=str(tmp_path / "Applications"),
        name="Session Explorer",
        version="1.9.0",
        icon_src=str(icon),
    )
    assert app.endswith("/Session Explorer.app")
    launcher = _os.path.join(app, "Contents", "MacOS", "session-explorer-launch")
    assert _os.path.exists(launcher)
    assert _os.access(launcher, _os.X_OK)               # chmod 0755
    icns = _os.path.join(app, "Contents", "Resources", "icon.icns")
    assert _os.path.exists(icns)
    with open(_os.path.join(app, "Contents", "Info.plist"), "rb") as f:
        pl = plistlib.load(f)
    assert pl["CFBundleIconFile"] == "icon"
    assert "CFBundleIconName" not in pl


def test_build_bundle_is_idempotent(tmp_path):
    icon = tmp_path / "src-icon.icns"
    icon.write_bytes(b"icns-bytes")
    args = dict(dest=str(tmp_path / "Applications"), name="Session Explorer",
                version="1.9.0", icon_src=str(icon))
    macapp.build_bundle(**args)
    macapp.build_bundle(**args)                          # must not raise / duplicate
    apps = list((tmp_path / "Applications").glob("*.app"))
    assert len(apps) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_macapp.py -q`
Expected: FAIL with `AttributeError: module '_pkg.macapp' has no attribute 'build_bundle'`.

- [ ] **Step 3: Write minimal implementation**

Append to `bin/_pkg/macapp.py` (ensure `import os`, `import shutil`, `import stat` at top):

```python
def build_bundle(*, dest: str, name: str, version: str, icon_src: str) -> str:
    """Create <dest>/<name>.app with Info.plist, launcher, and icon. Idempotent:
    an existing bundle at the path is removed first. Returns the .app path."""
    dest = os.path.expanduser(dest)
    app = os.path.join(dest, f"{name}.app")
    if os.path.exists(app):
        shutil.rmtree(app)
    contents = os.path.join(app, "Contents")
    macos = os.path.join(contents, "MacOS")
    resources = os.path.join(contents, "Resources")
    os.makedirs(macos)
    os.makedirs(resources)

    with open(os.path.join(contents, "Info.plist"), "w", encoding="utf-8") as f:
        f.write(build_info_plist(name=name, version=version))

    launcher = os.path.join(macos, EXECUTABLE_NAME)
    with open(launcher, "w", encoding="utf-8") as f:
        f.write(build_launcher_script())
    os.chmod(launcher, 0o755)

    shutil.copyfile(icon_src, os.path.join(resources, "icon.icns"))
    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test/test_macapp.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add bin/_pkg/macapp.py test/test_macapp.py
git commit -m "feat(macapp): build_bundle writes the .app on disk (idempotent)"
```

---

## Task 6: `install_app()` orchestrator + Dock/cache side effects

**Files:**
- Modify: `bin/_pkg/macapp.py`

These helpers shell out to macOS tools, so they aren't unit-tested directly (the bats test in Task 9 covers the real build with `--no-dock`; Dock mutation is left to manual verification). They are written defensively: every macOS call is wrapped so a failure degrades to a printed instruction rather than an exception.

- [ ] **Step 1: Implement the orchestrator + helpers**

Append to `bin/_pkg/macapp.py` (ensure `import platform`, `import plistlib`, `import subprocess`, `import sys` at top):

```python
def _icon_asset_path() -> str:
    """assets/app-icon.icns at the repo/plugin root (……/bin/_pkg/macapp.py → root)."""
    here = os.path.dirname(os.path.realpath(__file__))
    root = os.path.normpath(os.path.join(here, "..", ".."))
    return os.path.join(root, "assets", "app-icon.icns")


def _refresh_icon_cache(app: str) -> None:
    """Best-effort: re-register the bundle and clear the user icon cache so the
    new icon shows immediately. Never raises."""
    lsreg = ("/System/Library/Frameworks/CoreServices.framework/Versions/A/"
             "Frameworks/LaunchServices.framework/Versions/A/Support/lsregister")
    for cmd in (["/usr/bin/touch", app], [lsreg, "-f", app]):
        try:
            subprocess.run(cmd, check=False, capture_output=True)
        except Exception:
            pass
    try:
        cache = subprocess.run(["getconf", "DARWIN_USER_CACHE_DIR"],
                               capture_output=True, text=True).stdout.strip()
        if cache:
            store = os.path.join(cache, "com.apple.iconservices.store")
            shutil.rmtree(store, ignore_errors=True)
    except Exception:
        pass
    _killall("Dock")


def _killall(proc: str) -> None:
    try:
        subprocess.run(["killall", proc], check=False, capture_output=True)
    except Exception:
        pass


def _pin_to_dock(app: str) -> bool:
    """Append a persistent-apps entry unless one already points at `app`.
    Returns True if pinned (or already pinned), False if the attempt failed."""
    try:
        raw = subprocess.run(
            ["defaults", "export", "com.apple.dock", "-"],
            capture_output=True, check=True).stdout
        plist = plistlib.loads(raw)
        if app_already_pinned(plist.get("persistent-apps", []), app):
            return True
        entry = (
            '<dict><key>tile-data</key><dict><key>file-data</key><dict>'
            '<key>_CFURLString</key><string>file://%s/</string>'
            '<key>_CFURLStringType</key><integer>0</integer>'
            '</dict></dict></dict>' % app
        )
        subprocess.run(
            ["defaults", "write", "com.apple.dock", "persistent-apps",
             "-array-add", entry],
            check=True, capture_output=True)
        _killall("Dock")
        return True
    except Exception:
        return False


def install_app(*, dest: str = "~/Applications", name: str = "Session Explorer",
                pin_dock: bool = True) -> int:
    """Build the bundle and (best-effort) refresh the icon cache + pin to Dock.
    macOS-only. Returns a process exit code."""
    if platform.system() != "Darwin":
        print("install-app is macOS-only.", file=sys.stderr)
        return 1
    from . import __version__
    icon = _icon_asset_path()
    if not os.path.exists(icon):
        print(f"icon asset missing: {icon}", file=sys.stderr)
        return 1
    dest_abs = os.path.expanduser(dest)
    os.makedirs(dest_abs, exist_ok=True)
    app = build_bundle(dest=dest_abs, name=name, version=__version__, icon_src=icon)
    print(f"Created {app}")
    _refresh_icon_cache(app)
    if pin_dock:
        if _pin_to_dock(app):
            print("Pinned to the Dock.")
        else:
            print(f"Could not pin automatically — drag {app} to your Dock to pin it.")
    else:
        print(f"Drag {app} to your Dock to pin it.")
    return 0
```

- [ ] **Step 2: Smoke-check import (no behavioural test; helpers are side-effecting)**

Run: `python3 -c "import sys; sys.path.insert(0,'bin'); from _pkg import macapp; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add bin/_pkg/macapp.py
git commit -m "feat(macapp): install_app orchestrator + best-effort Dock/icon-cache"
```

---

## Task 7: Wire the `install-app` subcommand into the CLI

**Files:**
- Modify: `bin/_pkg/cli.py` (parser ~line 60–66; dispatch ~line 233)
- Test: `test/test_macapp.py`

- [ ] **Step 1: Write the failing test (non-macOS guard via subprocess)**

```python
# append to test/test_macapp.py
import subprocess as _sp
import sys as _sys


def test_cli_install_app_is_macos_guarded(monkeypatch):
    """On non-Darwin the subcommand must refuse cleanly (exit 1), not traceback.
    We assert the guard message rather than the platform so it passes on CI Linux
    and is a documented contract on macOS dev machines."""
    import platform
    from _pkg import macapp
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    rc = macapp.install_app(dest="/tmp/se-doesnotmatter", pin_dock=False)
    assert rc == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_macapp.py::test_cli_install_app_is_macos_guarded -q`
Expected: PASS only if `install_app` already guards (it does from Task 6) — but the CLI wiring is still missing, verified in Step 4. Run the full file: `python3 -m pytest test/test_macapp.py -q` → 11 passed.

> This task's real surface is the argparse wiring; the guard itself is covered above. Step 4 verifies the subcommand is reachable.

- [ ] **Step 3: Add the subparser and dispatch**

In `bin/_pkg/cli.py`, after the `uninstall_p` block (before `return p` at the end of `build_parser`), add:

```python
    app_p = sub.add_parser(
        "install-app",
        help="(macOS) Create a Dock launcher app in ~/Applications.")
    app_p.add_argument("--dest", default="~/Applications",
                       help="Parent directory for the .app (default ~/Applications).")
    app_p.add_argument("--name", default="Session Explorer",
                       help="App display name (default 'Session Explorer').")
    app_p.add_argument("--no-dock", action="store_true",
                       help="Create the app but do not pin it to the Dock.")
```

Add a dispatch handler function near `_cmd_launch` (e.g. after it):

```python
def _cmd_install_app(args) -> int:
    from . import macapp
    return macapp.install_app(dest=args.dest, name=args.name,
                              pin_dock=not args.no_dock)
```

In `main()`, alongside the other `if args.cmd == ...` branches (after the `launch` branch), add:

```python
    if args.cmd == "install-app":
        return _cmd_install_app(args)
```

- [ ] **Step 4: Verify the subcommand is reachable**

Run: `bin/session-explorer install-app --help`
Expected: usage text listing `--dest`, `--name`, `--no-dock` (exit 0).

Run (on Linux/CI this exercises the guard; on macOS use `--no-dock` to avoid Dock mutation in a smoke test):
```bash
bin/session-explorer install-app --help && echo "reachable"
```
Expected: `reachable`.

- [ ] **Step 5: Run the full pytest file**

Run: `python3 -m pytest test/test_macapp.py -q`
Expected: PASS (11 passed).

- [ ] **Step 6: Commit**

```bash
git add bin/_pkg/cli.py test/test_macapp.py
git commit -m "feat(cli): wire install-app subcommand"
```

---

## Task 8: Teardown — remove the app on uninstall

**Files:**
- Modify: `bin/_pkg/uninstall.py`
- Test: `test/test_uninstall.py`

- [ ] **Step 1: Write the failing test**

```python
# append to test/test_uninstall.py
import os
from _pkg import uninstall


def test_teardown_removes_mac_app(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    apps = tmp_path / "Applications"
    app = apps / "Session Explorer.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "session-explorer-launch").write_text("#!/bin/zsh\n")

    actions = uninstall.teardown(
        claude_dir=str(claude_dir),
        settings_path=str(claude_dir / "settings.json"),
        mac_apps_dir=str(apps),
    )
    assert not (apps / "Session Explorer.app").exists()
    assert any("Session Explorer.app" in a for a in actions)


def test_teardown_no_mac_app_is_noop(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    apps = tmp_path / "Applications"
    apps.mkdir()
    actions = uninstall.teardown(
        claude_dir=str(claude_dir),
        settings_path=str(claude_dir / "settings.json"),
        mac_apps_dir=str(apps),
    )
    assert not any("Session Explorer.app" in a for a in actions)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test/test_uninstall.py -k mac_app -q`
Expected: FAIL — `teardown() got an unexpected keyword argument 'mac_apps_dir'`.

- [ ] **Step 3: Implement the teardown step**

In `bin/_pkg/uninstall.py`, change the `teardown` signature to accept the new optional arg (keep the default pointing at the real `~/Applications`, derived from `claude_dir`'s parent which is `$HOME`):

```python
def teardown(*, claude_dir: str, settings_path: "str | None" = None,
             purge_data: bool = False, mac_apps_dir: "str | None" = None) -> list[str]:
```

Add a new step after step 4b (the tmux server kill), before step 5 (user data). It uses `shutil.rmtree`; add `import shutil` at the top of the file:

```python
    # 4c. Remove the macOS Dock launcher app (best-effort; macOS only in practice
    #     but path-driven so it is testable cross-platform).
    home = os.path.dirname(os.path.abspath(claude_dir))
    apps_dir = mac_apps_dir or os.path.join(home, "Applications")
    app = os.path.join(apps_dir, "Session Explorer.app")
    if os.path.isdir(app):
        shutil.rmtree(app, ignore_errors=True)
        actions.append("removed Session Explorer.app")
        # Unpin from the Dock (best-effort; only meaningful on macOS).
        try:
            from . import macapp
            macapp._unpin_from_dock(app)  # added below
        except Exception:
            pass
```

- [ ] **Step 4: Add the `_unpin_from_dock` helper to macapp.py**

In `bin/_pkg/macapp.py`, append:

```python
def _unpin_from_dock(app: str) -> None:
    """Best-effort: drop any persistent-apps entry pointing at `app`. Never raises."""
    try:
        raw = subprocess.run(
            ["defaults", "export", "com.apple.dock", "-"],
            capture_output=True, check=True).stdout
        plist = plistlib.loads(raw)
        apps = plist.get("persistent-apps", [])
        kept = [e for e in apps if not app_already_pinned([e], app)]
        if len(kept) == len(apps):
            return
        plist["persistent-apps"] = kept
        subprocess.run(["defaults", "import", "com.apple.dock", "-"],
                       input=plistlib.dumps(plist), check=True, capture_output=True)
        _killall("Dock")
    except Exception:
        pass
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest test/test_uninstall.py -k mac_app -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the full uninstall + macapp suites (regression check)**

Run: `python3 -m pytest test/test_uninstall.py test/test_macapp.py -q`
Expected: PASS (all green).

- [ ] **Step 7: Commit**

```bash
git add bin/_pkg/uninstall.py bin/_pkg/macapp.py test/test_uninstall.py
git commit -m "feat(uninstall): remove + unpin the macOS Dock app on teardown"
```

---

## Task 9: macOS-gated bats test for the real build

**Files:**
- Create: `test/macapp.bats`

- [ ] **Step 1: Write the test**

```bash
#!/usr/bin/env bats
# End-to-end build test for `install-app`. macOS-only (skipped elsewhere); uses
# --dest into a tmp dir and --no-dock so it never touches ~/Applications or the
# real Dock.

setup() {
  REPO="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  TMP="$(mktemp -d)"
  if [ "$(uname)" != "Darwin" ]; then skip "macOS-only"; fi
}

teardown() {
  [ -n "$TMP" ] && rm -rf "$TMP" 2>/dev/null || true
}

@test "install-app builds a complete bundle" {
  run "$REPO/bin/session-explorer" install-app --dest "$TMP" --no-dock
  [ "$status" -eq 0 ]

  APP="$TMP/Session Explorer.app"
  LAUNCH="$APP/Contents/MacOS/session-explorer-launch"
  [ -x "$LAUNCH" ]
  [ -f "$APP/Contents/Resources/icon.icns" ]

  # PATH repair + resolver fallbacks present in the launcher.
  grep -q 'export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"' "$LAUNCH"
  grep -q '\$HOME/.local/bin/session-explorer' "$LAUNCH"

  # Regression guard for the Automator icon trap.
  grep -q "CFBundleIconFile" "$APP/Contents/Info.plist"
  ! grep -q "CFBundleIconName" "$APP/Contents/Info.plist"

  # Valid icns.
  run sips -g format "$APP/Contents/Resources/icon.icns"
  [[ "$output" == *"format: icns"* ]]
}

@test "install-app is idempotent" {
  "$REPO/bin/session-explorer" install-app --dest "$TMP" --no-dock
  "$REPO/bin/session-explorer" install-app --dest "$TMP" --no-dock
  run bash -c "ls -d '$TMP'/*.app | wc -l | tr -d ' '"
  [ "$output" -eq 1 ]
}
```

- [ ] **Step 2: Run the bats test**

Run: `bats test/macapp.bats`
Expected (macOS): 2 passing. (Linux/CI: 2 skipped.)

- [ ] **Step 3: Commit**

```bash
git add test/macapp.bats
git commit -m "test(macapp): macOS-gated end-to-end bundle build"
```

---

## Task 10: Version bump to 1.9.0

**Files:**
- Modify: `bin/_pkg/__init__.py:6`
- Modify: `.claude-plugin/plugin.json:3`

- [ ] **Step 1: Bump both version strings**

In `bin/_pkg/__init__.py` change `__version__ = "1.8.0"` → `__version__ = "1.9.0"`.
In `.claude-plugin/plugin.json` change `"version": "1.8.0",` → `"version": "1.9.0",`.

- [ ] **Step 2: Verify**

Run: `bin/session-explorer --version`
Expected: `session-explorer 1.9.0`.

- [ ] **Step 3: Commit**

```bash
git add bin/_pkg/__init__.py .claude-plugin/plugin.json
git commit -m "chore: bump version to 1.9.0"
```

---

## Task 11: Docs — README, SPEC, CHANGELOG

**Files:**
- Modify: `README.md` (Install section, ~line 84+)
- Modify: `SPEC.md`
- Modify: `CHANGELOG.md` (top, ~line 5)

- [ ] **Step 1: README — add a Dock subsection**

After the install Options A/B in `README.md`, add:

```markdown
### Add to your Dock (macOS)

Once installed (either path above), create a clickable Dock launcher:

```
session-explorer install-app
```

This builds `~/Applications/Session Explorer.app` with the explorer icon and
pins it to your Dock. Clicking it opens the explorer in a new Terminal window —
**with tmux**, the same as `/session-explorer:open`. If automatic pinning
doesn't take, drag the app from `~/Applications` onto your Dock yourself.

To remove it later, run the uninstall (it removes the app and unpins it):

```
session-explorer uninstall
```
```

- [ ] **Step 2: SPEC — record the bundle + the Automator pitfall**

Add a subsection to `SPEC.md` under the launcher/install material:

```markdown
### macOS Dock launcher (`install-app`)

`session-explorer install-app` builds a hand-rolled `.app` under
`~/Applications` (no Automator/Xcode) and best-effort pins it to the Dock.

- **Bundle:** `Contents/Info.plist` (authored by us), `Contents/MacOS/
  session-explorer-launch` (generated zsh script, 0755), `Contents/Resources/
  icon.icns` (copied from `assets/app-icon.icns`).
- **Why a custom launcher and not Automator.** Two traps. (1) **PATH** — a
  GUI-launched Automator *Run Shell Script* inherits a stripped PATH without
  `/opt/homebrew/bin`, so `tmux.available()` returns False and `launch` silently
  drops its tmux behaviour. The generated launcher prepends the Homebrew paths.
  (2) **Icon override** — Automator applets carry a compiled `Assets.car` and a
  `CFBundleIconName` key, which modern macOS prefers over a replaced loose
  `.icns`. Our `Info.plist` sets `CFBundleIconFile` and **never**
  `CFBundleIconName`.
- **Binary resolution** is done at run time inside the launcher (read
  `installed_plugins.json` → versioned `installPath`, else `command -v`, else
  the `~/.local/bin` symlink) so it survives plugin version bumps.
- **Idempotent / best-effort.** Re-running rebuilds the bundle and reconciles a
  single Dock entry; every Dock/icon-cache call degrades to a printed
  drag-to-Dock instruction on failure. `uninstall` removes the app and unpins it.
```

- [ ] **Step 3: CHANGELOG — add the 1.9.0 entry**

Insert below the `# Changelog` intro lines in `CHANGELOG.md`:

```markdown
## 1.9.0

### Added
- **macOS Dock launcher (`session-explorer install-app`).** Builds a clickable
  `~/Applications/Session Explorer.app` with the explorer icon and pins it to the
  Dock. The bundled launcher repairs `PATH` so it opens **with tmux**, and
  resolves the binary at run time so it survives plugin updates. `uninstall`
  removes the app and unpins it. The build is hand-rolled (no Automator), which
  avoids the `CFBundleIconName`/`Assets.car` icon-override and stripped-`PATH`
  traps of an Automator applet.
```

- [ ] **Step 4: Commit**

```bash
git add README.md SPEC.md CHANGELOG.md
git commit -m "docs: document install-app (README, SPEC, CHANGELOG) for 1.9.0"
```

---

## Task 12: Full suite + final verification

- [ ] **Step 1: Run the whole Python suite**

Run: `python3 -m pytest test/ -q`
Expected: all pass (no regressions).

- [ ] **Step 2: Run the shell suite**

Run: `bats test/install.bats test/uninstall.bats test/hook.bats test/macapp.bats`
Expected: all pass on macOS (macapp's 2 tests skip on Linux).

- [ ] **Step 3: Real end-to-end (macOS, manual)**

Run:
```bash
bin/session-explorer install-app
```
Expected: prints `Created ~/Applications/Session Explorer.app` and either
`Pinned to the Dock.` or the drag fallback. Confirm the icon is the terminal-tree
art and clicking the app opens the explorer with tmux (the explorer appears as
the left pane of a tmux `explorer` window).

- [ ] **Step 4: Final commit if anything is outstanding, then hand off for review**

```bash
git status   # should be clean
```

---

## Self-Review Notes

- **Spec coverage:** subcommand (T7), bundle build incl. Info.plist/launcher/icon (T2–T6), `assets/` icon (T1), PATH+resolver (T3), Dock pin idempotency (T4/T6), best-effort fallback (T6), teardown (T8), bats+pytest split (T2–T9), README/SPEC/CHANGELOG + version bump (T10/T11). All spec sections map to a task.
- **Naming consistency:** `build_info_plist`, `build_launcher_script`, `app_already_pinned`, `build_bundle`, `install_app`, `_unpin_from_dock`, `EXECUTABLE_NAME`/`BUNDLE_ID` are used identically across tasks and tests.
- **No placeholders:** every code step shows complete code; every run step states expected output.
