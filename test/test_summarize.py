import subprocess

import pytest

from _pkg import summarize


def test_run_puts_digest_in_prompt_arg_not_stdin(monkeypatch):
    captured = {}

    class FakeProc:
        def __init__(self, *a, **k):
            captured["args"] = a[0]
            captured["env"] = k.get("env")
            captured["stdin"] = k.get("stdin")
            captured["cwd"] = k.get("cwd")

        def communicate(self, timeout=None):
            return ("  a short summary\n", "")

        @property
        def returncode(self):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(summarize.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: "/usr/bin/claude")
    out = summarize.run("USER: refactor auth", model="claude-haiku-4-5")
    assert out == "a short summary"
    # guard env so our SessionStart hook leaves no trace
    assert captured["env"]["SESSION_EXPLORER_SUMMARIZER"] == "1"
    assert captured["env"]["SESSION_EXPLORER_PROBE"] == "1"
    # the transcript rides in the -p ARGUMENT (stdin is ignored by the CLI)
    argv = captured["args"]
    assert argv[1] == "-p"
    assert "USER: refactor auth" in argv[2]
    assert "claude-haiku-4-5" in argv
    # stdin is closed (no 3s pipe-wait) and it runs in a neutral cwd
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["cwd"] and captured["cwd"] != "."


def test_run_raises_when_claude_missing(monkeypatch):
    monkeypatch.setattr(summarize.shutil, "which", lambda _c: None)
    with pytest.raises(summarize.SummaryError):
        summarize.run("x")


def test_run_raises_on_nonzero(monkeypatch):
    class FakeProc:
        def __init__(self, *a, **k):
            pass

        def communicate(self, timeout=None):
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


def test_run_raises_on_empty_output(monkeypatch):
    class FakeProc:
        def __init__(self, *a, **k):
            pass

        def communicate(self, timeout=None):
            return ("   \n", "")

        @property
        def returncode(self):
            return 0

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

        def communicate(self, timeout=None):
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
