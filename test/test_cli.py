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
