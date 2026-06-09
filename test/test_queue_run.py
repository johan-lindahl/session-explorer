import os
import subprocess

import pytest

from _pkg import queue_config as qc
from _pkg import queue_run, queue_store


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _repo(tmp_path):
    r = tmp_path / "main"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("x")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-qm", "init")
    return r


@pytest.fixture
def paths(tmp_path):
    return {
        "config": str(tmp_path / "qc.json"),
        "queues_root": str(tmp_path / "queues"),
        "live": str(tmp_path / "live.json"),
    }


def test_none_strategy_runs_command_and_returns_its_code(tmp_path, paths):
    root = _repo(tmp_path)
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "port", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    marker = tmp_path / "ran"
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["sh", "-c", f"touch {marker}"], cwd=str(root),
        sid="s1", pid=os.getpid())
    assert rc == 0
    assert marker.exists()


def test_command_failure_exit_code_is_preserved(tmp_path, paths):
    root = _repo(tmp_path)
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["sh", "-c", "exit 7"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == 7


def test_unknown_resource_is_refusal_code(tmp_path, paths):
    root = _repo(tmp_path)
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id(str(root))
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="missing",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT


def test_ticket_released_after_run(tmp_path, paths):
    root = _repo(tmp_path)
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    qdir = queue_run.queue_dir(paths["queues_root"], pid, "db")
    assert queue_store.holder(qdir) is None   # no ticket left behind


def test_root_dir_sync_from_root_cwd_refuses(tmp_path, paths):
    """A root-dir sync invoked from root itself must refuse (no worktree source)."""
    root = _repo(tmp_path)
    from _pkg import project_id as _pid
    pid = _pid.project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="root",
                    resource={"kind": "root-dir", "path": _pid.main_root(str(root)),
                              "run_in": "root", "acquire": "sync", "release": "none",
                              "sync": {"delete": True, "exclude": ["/.git"],
                                       "protect": ["/.git"]}})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="root",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT


def test_command_acquire_runs_before_command(tmp_path, paths):
    root = _repo(tmp_path)
    from _pkg import project_id as _pid
    pid = _pid.project_id(str(root))
    acq = tmp_path / "acquired"
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "port", "path": "", "run_in": "worktree",
                              "acquire": "command", "release": "none",
                              "command_acquire": f"touch {acq}"})
    main = tmp_path / "order"
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["sh", "-c", f"test -f {acq} && touch {main}"],
        cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == 0 and main.exists()   # acquire ran before the command


def test_live_root_blocks_then_proceeds_when_cleared(tmp_path, paths, monkeypatch):
    """A live root session makes a worktree queue-run WAIT (holding its ticket),
    then run once the session clears — not refuse and lose its place (spec §5)."""
    from _pkg import project_id as pid_mod
    root = _repo(tmp_path)
    wt = tmp_path / "feat"
    _git(root, "worktree", "add", "-q", str(wt))
    pid = pid_mod.project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="root",
                    resource={"kind": "root-dir", "path": pid_mod.main_root(str(root)),
                              "run_in": "worktree", "acquire": "none", "release": "none"})
    calls = {"n": 0}

    def fake_live(_live_path, root_arg, **_kw):
        calls["n"] += 1
        # "held" for the first two polls, then the session clears.
        return ({"sid": "x", "cwd": root_arg, "name": "main"}
                if calls["n"] < 3 else None)

    monkeypatch.setattr(queue_run.exclusive, "live_root_session", fake_live)
    # No-op the sleep so the wait loop spins fast (it still polls fake_live).
    monkeypatch.setattr(queue_run.time, "sleep", lambda _s: None)
    marker = tmp_path / "ran"
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="root",
        command=["sh", "-c", f"touch {marker}"], cwd=str(wt), sid="s1",
        pid=os.getpid())
    assert rc == 0 and marker.exists()
    assert calls["n"] >= 3   # it polled (waited) before proceeding


def test_interrupt_while_waiting_releases_ticket(tmp_path, paths, monkeypatch):
    """SIGINT during the wait raises _Interrupted; the finally still drops the
    ticket and leaves the line (spec §4)."""
    pid = __import__("_pkg.project_id", fromlist=["project_id"]).project_id
    root = _repo(tmp_path)
    project_id = pid(str(root))
    qc.add_resource(paths["config"], project_id=project_id, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "none"})

    def boom(*_a, **_k):
        raise queue_run._Interrupted(2)

    monkeypatch.setattr(queue_store, "wait_for_turn", boom)
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=project_id, resource_id="db",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT
    qdir = queue_run.queue_dir(paths["queues_root"], project_id, "db")
    assert queue_store.holder(qdir) is None   # ticket released despite interrupt


def test_release_runs_after_acquire_even_when_readiness_fails(tmp_path, paths):
    """Acquire succeeded but wait_for never readies -> release hook must STILL
    run before the ticket is released (spec §4)."""
    from _pkg import project_id as pid_mod
    root = _repo(tmp_path)
    pid = pid_mod.project_id(str(root))
    rel = tmp_path / "released"
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "port", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "command",
                              "command_release": f"touch {rel}",
                              "wait_for": {"type": "command", "target": "false",
                                           "timeout": 0.2}})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT   # readiness timed out
    assert rel.exists()                    # release hook still ran


def test_missing_executable_is_controlled_exit_and_runs_release(tmp_path, paths):
    """A non-existent command must return a controlled code (no traceback) and
    still run the release hook (acquire succeeded)."""
    from _pkg import project_id as pid_mod
    root = _repo(tmp_path)
    pid = pid_mod.project_id(str(root))
    rel = tmp_path / "released"
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "command",
                              "command_release": f"touch {rel}"})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["this-executable-does-not-exist-xyz"], cwd=str(root),
        sid="s1", pid=os.getpid())
    assert rc == queue_run.REFUSAL_EXIT     # controlled, not a traceback
    assert rel.exists()                      # release hook still ran
    qdir = queue_run.queue_dir(paths["queues_root"], pid, "db")
    assert queue_store.holder(qdir) is None  # ticket released


def test_release_exception_still_releases_ticket(tmp_path, paths, monkeypatch):
    """A release hook that raises an unexpected exception must NOT strand the
    ticket — the innermost finally still removes it (spec §4 liveness)."""
    from _pkg import project_id as pid_mod
    root = _repo(tmp_path)
    pid = pid_mod.project_id(str(root))
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="db",
                    resource={"kind": "name", "path": "", "run_in": "worktree",
                              "acquire": "none", "release": "command",
                              "command_release": "true"})

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(queue_run, "_do_release", boom)
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="db",
        command=["true"], cwd=str(root), sid="s1", pid=os.getpid())
    assert rc == 0                            # child succeeded; release error logged
    qdir = queue_run.queue_dir(paths["queues_root"], pid, "db")
    assert queue_store.holder(qdir) is None   # ticket released despite the raise


def test_sync_marker_not_written_when_dry_run_fails(tmp_path, monkeypatch):
    """Fail-closed: a dry-run error refuses AND must not settle the baseline."""
    from _pkg import qsync
    qdir = str(tmp_path / "q")
    resource = {"kind": "root-dir", "acquire": "sync", "release": "none",
                "path": str(tmp_path / "root"),
                "sync": {"delete": True, "exclude": ["/.git"], "protect": ["/.git"]}}
    monkeypatch.setattr(queue_run.exclusive, "transition_guard", lambda _r: None)
    monkeypatch.setattr(qsync, "dry_run_deletions",
                        lambda *a, **k: (_ for _ in ()).throw(qsync.SyncDryRunError("boom")))
    msg = queue_run._do_acquire(resource, src=str(tmp_path / "wt"),
                                root=resource["path"], qdir=qdir)
    assert msg and "verify" in msg.lower()
    assert qsync.in_sandbox(qdir) is False


def test_sync_marker_not_written_when_real_rsync_fails(tmp_path, monkeypatch):
    """A failed real rsync must not settle the baseline (gate re-fires next run)."""
    from _pkg import qsync
    qdir = str(tmp_path / "q")
    resource = {"kind": "root-dir", "acquire": "sync", "release": "none",
                "path": str(tmp_path / "root"),
                "sync": {"delete": True, "exclude": ["/.git"], "protect": ["/.git"]}}
    monkeypatch.setattr(queue_run.exclusive, "transition_guard", lambda _r: None)
    monkeypatch.setattr(qsync, "dry_run_deletions", lambda *a, **k: [])

    class _Failed:
        returncode = 1

    monkeypatch.setattr(queue_run.subprocess, "run", lambda *a, **k: _Failed())
    msg = queue_run._do_acquire(resource, src=str(tmp_path / "wt"),
                                root=resource["path"], qdir=qdir)
    assert msg == "rsync acquire failed"
    assert qsync.in_sandbox(qdir) is False


def test_command_hooks_receive_all_se_queue_env(tmp_path, paths):
    """Both acquire AND release hooks see all three SE_QUEUE_* vars. Run from a
    real worktree so WORKTREE, ROOT and STATE_DIR are three distinct values — a
    bug omitting any one fails here, not silently at runtime."""
    root = _repo(tmp_path)
    from _pkg import project_id as _pid
    pid = _pid.project_id(str(root))
    wt = tmp_path / "wt"
    _git(root, "worktree", "add", "-q", "-b", "feat", str(wt))
    acq, rel = tmp_path / "acq.txt", tmp_path / "rel.txt"
    dump = ("(printenv SE_QUEUE_WORKTREE; printenv SE_QUEUE_ROOT; "
            "printenv SE_QUEUE_STATE_DIR)")
    qc.add_resource(paths["config"], project_id=pid, display_path=str(root),
                    resource_id="ov",
                    resource={"kind": "root-dir", "path": _pid.main_root(str(root)),
                              "run_in": "root", "acquire": "command",
                              "release": "command",
                              "command_acquire": f"{dump} > {acq}",
                              "command_release": f"{dump} > {rel}"})
    rc = queue_run.run_lease(
        config_path=paths["config"], queues_root=paths["queues_root"],
        live_path=paths["live"], project_id=pid, resource_id="ov",
        command=["true"], cwd=str(wt), sid="s1", pid=os.getpid())
    assert rc == 0
    qdir = queue_run.queue_dir(paths["queues_root"], pid, "ov")
    expected = [os.path.realpath(str(wt)), _pid.main_root(str(root)),
                os.path.realpath(qdir)]
    assert acq.read_text().splitlines() == expected   # acquire saw all three
    assert rel.read_text().splitlines() == expected   # release saw all three
