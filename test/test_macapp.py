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
