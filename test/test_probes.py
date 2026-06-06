import socket
import threading

from _pkg import probes


def test_health_ok_on_zero_exit():
    ok, _ = probes.health_check("true", timeout=5)
    assert ok is True


def test_health_down_on_nonzero_exit():
    ok, _ = probes.health_check("false", timeout=5)
    assert ok is False


def test_health_none_command_is_ok():
    # No health command declared -> treated as "not checked", reported up.
    ok, detail = probes.health_check(None, timeout=5)
    assert ok is True and "no health" in detail.lower()


def test_wait_for_command_succeeds():
    spec = {"type": "command", "target": "true", "timeout": 2}
    assert probes.wait_for(spec, poll_interval=0.05) is True


def test_wait_for_command_times_out():
    spec = {"type": "command", "target": "false", "timeout": 0.2}
    assert probes.wait_for(spec, poll_interval=0.05) is False


def test_wait_for_port_open():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        spec = {"type": "port", "target": f"127.0.0.1:{port}", "timeout": 2}
        assert probes.wait_for(spec, poll_interval=0.05) is True
    finally:
        srv.close()


def test_wait_for_port_closed_times_out():
    spec = {"type": "port", "target": "127.0.0.1:1", "timeout": 0.2}
    assert probes.wait_for(spec, poll_interval=0.05) is False


def test_wait_for_none_spec_is_ready():
    assert probes.wait_for(None) is True
