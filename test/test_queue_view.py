from datetime import datetime, timezone

from _pkg import queue_config, queue_run, queue_store, queue_view


def _now():
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)


def _seed_ticket(qdir, *, sid, created, label):
    # take_ticket allocates the ticket number itself (monotonic per qdir), so we
    # don't pass one — sequential calls deterministically get 1, 2, 3 … in order.
    # The returned Ticket holds the lifetime flock; the caller must release() it.
    return queue_store.take_ticket(
        qdir, sid=sid, cwd="/x", command=["test"], pid=1,
        label=label, now_iso=created)


def test_fmt_elapsed():
    assert queue_view.fmt_elapsed(0) == "0:00"
    assert queue_view.fmt_elapsed(42) == "0:42"
    assert queue_view.fmt_elapsed(83) == "1:23"
    assert queue_view.fmt_elapsed(3725) == "1:02:05"


def test_snapshot_free_resource(tmp_path):
    cfg = str(tmp_path / "qc.json")
    queues = str(tmp_path / "queues")
    queue_config.add_resource(
        cfg, project_id="abc123", display_path="/repo/Gym",
        resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    rows = queue_view.snapshot(cfg, queues, str(tmp_path / "live.json"),
                               now=_now())
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "abc123/db"
    assert r["project"] == "/repo/Gym"
    assert r["resource"] == "db"
    assert r["kind"] == "port"
    assert r["holder"] is None
    assert r["waiting"] == []
    assert r["live_root_block"] is None
    assert r["active"] is False


def test_snapshot_holder_and_waiters(tmp_path):
    cfg = str(tmp_path / "qc.json")
    queues = str(tmp_path / "queues")
    queue_config.add_resource(
        cfg, project_id="abc123", display_path="/repo/Gym",
        resource_id="db",
        resource={"kind": "port", "run_in": "worktree",
                  "acquire": "none", "release": "none"})
    qdir = queue_run.queue_dir(queues, "abc123", "db")
    held = []
    held.append(_seed_ticket(qdir, sid="feat-auth",
                             created="2026-06-06T11:59:18+00:00", label="Gym/db"))
    held.append(_seed_ticket(qdir, sid="bugfix",
                             created="2026-06-06T11:59:50+00:00", label="Gym/db"))
    held.append(_seed_ticket(qdir, sid="ui",
                             created="2026-06-06T11:59:55+00:00", label="Gym/db"))
    try:
        rows = queue_view.snapshot(cfg, queues, str(tmp_path / "live.json"),
                                   now=_now())
    finally:
        for t in held:
            t.release()
    r = rows[0]
    assert r["holder"]["sid"] == "feat-auth"
    assert r["holder"]["elapsed"] == "0:42"
    assert [w["sid"] for w in r["waiting"]] == ["bugfix", "ui"]
    assert r["waiting"][0]["pos"] == "1 of 2"
    assert r["waiting"][1]["pos"] == "2 of 2"
    assert r["active"] is True
