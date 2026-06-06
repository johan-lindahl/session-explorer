import os

import pytest

from _pkg import queue_store as qs


def _qdir(tmp_path):
    return str(tmp_path / "q")


def test_take_ticket_publishes_locked_file(tmp_path):
    qdir = _qdir(tmp_path)
    t = qs.take_ticket(qdir, sid="s1", cwd="/wt", command=["echo", "hi"],
                       pid=1234, label="Gym/root", now_iso="2026-06-06T00:00:00+00:00")
    assert t.number == 1
    assert os.path.exists(t.path)
    # the owner holds it -> a foreign liveness probe says alive
    assert qs._probe_alive(t.path) is True
    t.release()


def test_numbers_are_monotonic_among_live_tickets(tmp_path):
    qdir = _qdir(tmp_path)
    a = qs.take_ticket(qdir, sid="a", cwd="/", command=["x"], pid=1, label="l", now_iso="t")
    b = qs.take_ticket(qdir, sid="b", cwd="/", command=["x"], pid=2, label="l", now_iso="t")
    assert (a.number, b.number) == (1, 2)
    a.release(); b.release()


def test_holder_is_lowest_live_ticket(tmp_path):
    qdir = _qdir(tmp_path)
    a = qs.take_ticket(qdir, sid="a", cwd="/", command=["x"], pid=1, label="l", now_iso="t")
    b = qs.take_ticket(qdir, sid="b", cwd="/", command=["x"], pid=2, label="l", now_iso="t")
    assert qs.holder(qdir) == a.number
    a.release()
    assert qs.holder(qdir) == b.number   # advances after the holder leaves
    b.release()
    assert qs.holder(qdir) is None


def test_dead_owner_is_reaped_and_holder_advances(tmp_path):
    """Simulate a crashed holder: a published ticket file whose lock nobody holds."""
    qdir = _qdir(tmp_path)
    # Hand-craft a 'dead' lower ticket (no live flock on it).
    os.makedirs(qdir, exist_ok=True)
    dead = os.path.join(qdir, qs.ticket_name(1, "dead"))
    with open(dead, "w") as f:
        f.write('{"number": 1, "sid": "dead", "pid": 999999}')
    live = qs.take_ticket(qdir, sid="live", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    assert live.number == 2
    assert qs.holder(qdir) == live.number     # dead #1 reaped, #2 is holder
    assert not os.path.exists(dead)           # reaped from disk
    live.release()


def test_release_removes_ticket(tmp_path):
    qdir = _qdir(tmp_path)
    t = qs.take_ticket(qdir, sid="s", cwd="/", command=["x"], pid=1, label="l", now_iso="t")
    p = t.path
    t.release()
    assert not os.path.exists(p)


def test_position_reports_place_in_line(tmp_path):
    qdir = _qdir(tmp_path)
    a = qs.take_ticket(qdir, sid="a", cwd="/", command=["x"], pid=1, label="l", now_iso="t")
    b = qs.take_ticket(qdir, sid="b", cwd="/", command=["x"], pid=2, label="l", now_iso="t")
    c = qs.take_ticket(qdir, sid="c", cwd="/", command=["x"], pid=3, label="l", now_iso="t")
    assert qs.position(qdir, a.number) == (1, 3)
    assert qs.position(qdir, b.number) == (2, 3)
    assert qs.position(qdir, c.number) == (3, 3)
    a.release(); b.release(); c.release()


def test_list_tickets_returns_sorted_live_entries(tmp_path):
    qdir = _qdir(tmp_path)
    a = qs.take_ticket(qdir, sid="a", cwd="/", command=["x"], pid=1, label="Gym/root", now_iso="t")
    rows = qs.list_tickets(qdir)
    assert [r["sid"] for r in rows] == ["a"]
    assert rows[0]["label"] == "Gym/root"
    a.release()


import time


def test_wait_returns_acquired_when_holder(tmp_path):
    qdir = _qdir(tmp_path)
    t = qs.take_ticket(qdir, sid="solo", cwd="/", command=["x"], pid=os.getpid(),
                       label="l", now_iso="t")
    outcome = qs.wait_for_turn(qdir, t, poll_interval=0.01, timeout=1.0)
    assert outcome == "acquired"
    t.release()


def test_wait_times_out_behind_a_live_holder(tmp_path):
    qdir = _qdir(tmp_path)
    head = qs.take_ticket(qdir, sid="head", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    me = qs.take_ticket(qdir, sid="me", cwd="/", command=["x"], pid=os.getpid(),
                        label="l", now_iso="t")
    outcome = qs.wait_for_turn(qdir, me, poll_interval=0.01, timeout=0.2)
    assert outcome == "timeout"
    me.release(); head.release()


def test_cancel_waiter_unlinks_and_tombstones(tmp_path):
    qdir = _qdir(tmp_path)
    head = qs.take_ticket(qdir, sid="head", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    waiter = qs.take_ticket(qdir, sid="wait", cwd="/", command=["x"], pid=os.getpid(),
                            label="l", now_iso="t")
    assert qs.cancel(qdir, sid="wait", reason="user cancelled") is True
    assert not os.path.exists(waiter.path)             # ticket unlinked
    assert qs.cancelled_reason(qdir, waiter.number, "wait") == "user cancelled"
    head.release(); waiter.release()


def test_cancel_refuses_current_holder(tmp_path):
    qdir = _qdir(tmp_path)
    head = qs.take_ticket(qdir, sid="head", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    assert qs.cancel(qdir, sid="head", reason="nope") is False  # holder protected
    assert os.path.exists(head.path)
    head.release()


def test_wait_returns_cancelled_when_ticket_removed(tmp_path):
    qdir = _qdir(tmp_path)
    head = qs.take_ticket(qdir, sid="head", cwd="/", command=["x"], pid=os.getpid(),
                          label="l", now_iso="t")
    me = qs.take_ticket(qdir, sid="me", cwd="/", command=["x"], pid=os.getpid(),
                        label="l", now_iso="t")
    qs.cancel(qdir, sid="me", reason="bye")
    outcome = qs.wait_for_turn(qdir, me, poll_interval=0.01, timeout=1.0)
    assert outcome == "cancelled:bye"
    head.release()
