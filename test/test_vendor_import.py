def test_textual_imports_from_vendor():
    import _pkg  # noqa: F401  triggers sys.path injection
    import textual  # noqa: F401
    from textual.app import App  # noqa: F401
