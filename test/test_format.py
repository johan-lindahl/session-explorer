from _pkg.format import fmt_tokens, fmt_age, fmt_pct
from datetime import datetime, timezone, timedelta


def test_fmt_tokens_small():
    assert fmt_tokens(0) == "~0"
    assert fmt_tokens(999) == "~999"


def test_fmt_tokens_thousands():
    assert fmt_tokens(10_000) == "~10K"
    assert fmt_tokens(127_456) == "~127K"


def test_fmt_age_none():
    assert fmt_age(None) == "—"


def test_fmt_age_minutes():
    iso = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat()
    assert fmt_age(iso) == "12m"


def test_fmt_age_hours():
    iso = (datetime.now(timezone.utc) - timedelta(hours=2, minutes=5)).isoformat()
    assert fmt_age(iso) == "2h"


def test_fmt_age_days():
    iso = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert fmt_age(iso) == "5d"


def test_fmt_pct():
    assert fmt_pct(19) == "(19%)"
    assert fmt_pct(0) == "(0%)"
    assert fmt_pct(100) == "(100%)"
