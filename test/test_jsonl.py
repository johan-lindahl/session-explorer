"""Tests for _pkg.jsonl."""

import os

from _pkg import jsonl

_FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_message_count_named():
    assert jsonl.message_count(os.path.join(_FIX, "named.jsonl")) == 4


def test_message_count_empty():
    assert jsonl.message_count(os.path.join(_FIX, "empty.jsonl")) == 0


def test_first_user_prompt_named():
    assert jsonl.first_user_prompt(os.path.join(_FIX, "named.jsonl")) == "plan sprint 14 work"


def test_first_user_prompt_empty():
    assert jsonl.first_user_prompt(os.path.join(_FIX, "empty.jsonl")) is None
