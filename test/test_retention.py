"""Tests for _pkg.retention — the opt-in cleanupPeriodDays management."""

import json
import os

from _pkg import retention


def test_state_undecided_by_default(tmp_path):
    d = str(tmp_path)
    assert retention.is_enabled(d) is False
    assert retention.is_declined(d) is False
    assert retention.is_decided(d) is False


def test_enable_backs_up_prior_and_sets_neutralised(tmp_path):
    d = str(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 14, "x": 1}))
    prior = retention.enable(d)
    assert prior == 14
    data = json.loads((tmp_path / "settings.json").read_text())
    assert data["cleanupPeriodDays"] == 36500
    assert data["x"] == 1  # preserves other settings
    assert (tmp_path / ".session-explorer.backup").read_text().strip() == "14"
    assert retention.is_enabled(d) is True
    assert retention.is_decided(d) is True


def test_enable_defaults_prior_to_30_when_unset(tmp_path):
    d = str(tmp_path)
    prior = retention.enable(d)  # no settings.json
    assert prior == 30
    assert (tmp_path / ".session-explorer.backup").read_text().strip() == "30"
    assert json.loads((tmp_path / "settings.json").read_text())["cleanupPeriodDays"] == 36500


def test_enable_idempotent_keeps_original_backup(tmp_path):
    d = str(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 7}))
    retention.enable(d)
    # Tamper, then re-enable: backup must still hold the ORIGINAL prior (7).
    (tmp_path / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 36500}))
    retention.enable(d)
    assert (tmp_path / ".session-explorer.backup").read_text().strip() == "7"


def test_decline_records_marker_without_touching_settings(tmp_path):
    d = str(tmp_path)
    retention.decline(d)
    assert retention.is_declined(d) is True
    assert retention.is_decided(d) is True
    assert retention.is_enabled(d) is False
    assert not (tmp_path / "settings.json").exists()  # native cleanup left alone


def test_enable_clears_prior_decline(tmp_path):
    d = str(tmp_path)
    retention.decline(d)
    retention.enable(d)
    assert retention.is_declined(d) is False
    assert retention.is_enabled(d) is True


def test_disable_restores_prior_and_removes_backup(tmp_path):
    cd = str(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 45}))
    retention.enable(cd)  # backs up 45, sets 36500
    assert json.load(open(tmp_path / "settings.json"))["cleanupPeriodDays"] == 36500
    retention.disable(cd)
    assert json.load(open(tmp_path / "settings.json"))["cleanupPeriodDays"] == 45
    assert not retention.is_enabled(cd)  # backup gone


def test_disable_without_backup_is_noop(tmp_path):
    retention.disable(str(tmp_path))  # must not raise
