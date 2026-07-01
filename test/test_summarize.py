import subprocess

import pytest

from _pkg import summarize


def test_run_returns_trimmed_stdout(monkeypatch):
    captured = {}

    class FakeProc:
        def __init__(self, *a, **k):
            captured["args"] = a[0]
            captured["env"] = k.get("env")

        def communicate(self, input=None, timeout=None):
            captured["input"] = input
            return ("  a short summary\n", "")

        @property
        def returncode(self):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(summarize.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: "/usr/bin/claude")
    out = summarize.run("USER: hi", model="claude-haiku-4-5")
    assert out == "a short summary"
    # guard env is set so our SessionStart hook leaves no trace
    assert captured["env"]["SESSION_EXPLORER_SUMMARIZER"] == "1"
    assert captured["env"]["SESSION_EXPLORER_PROBE"] == "1"
    # digest is piped on stdin, model flag present
    assert "USER: hi" in captured["input"]
    assert "claude-haiku-4-5" in captured["args"]


def test_run_raises_when_claude_missing(monkeypatch):
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: None)
    with pytest.raises(summarize.SummaryError):
        summarize.run("x")


def test_run_raises_on_nonzero(monkeypatch):
    class FakeProc:
        def __init__(self, *a, **k):
            pass

        def communicate(self, input=None, timeout=None):
            return ("", "boom")

        @property
        def returncode(self):
            return 1

        def kill(self):
            pass

    monkeypatch.setattr(summarize.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: "/usr/bin/claude")
    with pytest.raises(summarize.SummaryError):
        summarize.run("x")


def test_run_raises_on_timeout(monkeypatch):
    class FakeProc:
        def __init__(self, *a, **k):
            pass

        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)

        @property
        def returncode(self):
            return None

        def kill(self):
            pass

    monkeypatch.setattr(summarize.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: "/usr/bin/claude")
    with pytest.raises(summarize.SummaryError):
        summarize.run("x", timeout=0.01)
