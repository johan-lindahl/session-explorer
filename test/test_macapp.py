"""Unit tests for the pure builders in macapp (cross-platform; no FS/Dock)."""

import os
import plistlib

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
    launcher = os.path.join(app, "Contents", "MacOS", "session-explorer-launch")
    assert os.path.exists(launcher)
    assert os.access(launcher, os.X_OK)                  # chmod 0755
    icns = os.path.join(app, "Contents", "Resources", "icon.icns")
    assert os.path.exists(icns)
    with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
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
