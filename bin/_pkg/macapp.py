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

import os
import platform
import plistlib
import shutil
import subprocess
import sys
from urllib.parse import unquote
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
