import os
from datetime import datetime, timedelta, timezone

from _pkg import live


T0 = datetime(2026, 5, 29, 7, 0, 0, tzinfo=timezone.utc)


def test_default_path_for_is_sibling_of_index():
    p = live.default_path_for("/x/y/session-explorer-index.json")
    assert p == "/x/y/session-explorer-live.json"


def test_load_missing_returns_v1_empty(tmp_path):
    data = live.load(str(tmp_path / "live.json"))
    assert data == {"version": 1, "sessions": {}}


def test_session_start_records_idle_with_pid(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1",
                      transcript_path="/t/s1.jsonl", cwd="/repo", pid=4242, now=T0)
    e = live.load(lp)["sessions"]["s1"]
    assert e["state"] == "idle"
    assert e["pid"] == 4242
    assert e["transcript_path"] == "/t/s1.jsonl"
    assert e["cwd"] == "/repo"
    assert e["last_seen"] == T0.isoformat()


def test_user_prompt_submit_sets_working(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1", pid=1, now=T0)
    live.record_event(lp, event="UserPromptSubmit", session_id="s1", now=T0)
    assert live.load(lp)["sessions"]["s1"]["state"] == "working"


def test_stop_sets_idle(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1", pid=1, now=T0)
    live.record_event(lp, event="UserPromptSubmit", session_id="s1", now=T0)
    live.record_event(lp, event="Stop", session_id="s1", now=T0)
    assert live.load(lp)["sessions"]["s1"]["state"] == "idle"


def test_notification_sets_idle(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1", pid=1, now=T0)
    live.record_event(lp, event="UserPromptSubmit", session_id="s1", now=T0)
    live.record_event(lp, event="Notification", session_id="s1", now=T0)
    assert live.load(lp)["sessions"]["s1"]["state"] == "idle"


def test_session_end_removes_entry(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1", pid=1, now=T0)
    live.record_event(lp, event="SessionEnd", session_id="s1", now=T0)
    assert "s1" not in live.load(lp)["sessions"]


def test_event_for_unknown_session_creates_entry(tmp_path):
    # UserPromptSubmit may arrive without a prior SessionStart in this process.
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="UserPromptSubmit", session_id="ghost", now=T0)
    assert live.load(lp)["sessions"]["ghost"]["state"] == "working"


def test_session_end_on_missing_session_is_safe(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionEnd", session_id="never-existed", now=T0)
    assert live.load(lp)["sessions"] == {}


def test_version_preserved_when_recording_into_existing_file(tmp_path):
    lp = str(tmp_path / "live.json")
    live.record_event(lp, event="SessionStart", session_id="s1", pid=1, now=T0)
    live.record_event(lp, event="Stop", session_id="s1", now=T0)
    assert live.load(lp)["version"] == 1


def test_load_corrupt_file_falls_back_to_v1_default(tmp_path):
    lp = tmp_path / "live.json"
    lp.write_text("{ this is not valid json")
    assert live.load(str(lp)) == {"version": 1, "sessions": {}}


def _seed(tmp_path, **entry):
    lp = str(tmp_path / "live.json")
    live.mutate(lp, lambda d: {**d, "sessions": {**d.get("sessions", {}), "s1": entry}})
    return lp


def test_poll_keeps_alive_pid_and_returns_state(tmp_path):
    lp = _seed(tmp_path, state="working", pid=os.getpid(),
               last_seen=T0.isoformat())
    states = live.poll(lp, now=T0)
    assert states == {"s1": "working"}


def test_poll_prunes_dead_pid(tmp_path):
    # An almost-certainly-dead high pid.
    dead_pid = 2 ** 22
    lp = _seed(tmp_path, state="idle", pid=dead_pid, last_seen=T0.isoformat())
    states = live.poll(lp, now=T0)
    assert states == {}
    assert "s1" not in live.load(lp)["sessions"]  # pruned from disk


def test_poll_ttl_backstop_prunes_alive_pid_when_stale(tmp_path):
    lp = _seed(tmp_path, state="idle", pid=os.getpid(), last_seen=T0.isoformat())
    later = T0 + timedelta(seconds=live.DEFAULT_TTL_SECONDS + 1)
    assert live.poll(lp, now=later) == {}


def test_poll_no_pid_uses_ttl_only(tmp_path):
    lp = _seed(tmp_path, state="idle", pid=None, last_seen=T0.isoformat())
    assert live.poll(lp, now=T0) == {"s1": "idle"}
    later = T0 + timedelta(seconds=live.DEFAULT_TTL_SECONDS + 1)
    assert live.poll(lp, now=later) == {}


def test_poll_does_not_write_when_nothing_dead(tmp_path):
    lp = _seed(tmp_path, state="idle", pid=os.getpid(), last_seen=T0.isoformat())
    before = os.path.getmtime(lp)
    live.poll(lp, now=T0)
    assert os.path.getmtime(lp) == before  # no needless rewrite


def test_poll_prunes_zero_or_negative_pid(tmp_path):
    lp = _seed(tmp_path, state="idle", pid=0, last_seen=T0.isoformat())
    assert live.poll(lp, now=T0) == {}


def test_poll_alive_pid_with_missing_last_seen_is_kept(tmp_path):
    # A live pid with no timestamp stays alive on pid alone (age is None).
    lp = _seed(tmp_path, state="working", pid=os.getpid())
    assert live.poll(lp, now=T0) == {"s1": "working"}


def test_poll_future_last_seen_is_treated_as_alive(tmp_path):
    # Clock skew: a future last_seen yields negative age, not stale.
    from datetime import timedelta as _td
    future = (T0 + _td(seconds=120)).isoformat()
    lp = _seed(tmp_path, state="idle", pid=os.getpid(), last_seen=future)
    assert live.poll(lp, now=T0) == {"s1": "idle"}
