import os


def test_textual_imports_from_vendor():
    import _pkg  # noqa: F401  triggers sys.path injection
    import textual

    vendor_root = os.path.realpath(os.path.join(
        os.path.dirname(_pkg.__file__), "_vendor"
    ))
    textual_path = os.path.realpath(textual.__file__)
    assert textual_path.startswith(vendor_root + os.sep), (
        f"textual must load from vendored copy at {vendor_root}, "
        f"but loaded from {textual_path}"
    )
