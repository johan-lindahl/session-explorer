from _pkg import tmux


def test_parse_version_extracts_major_minor():
    assert tmux.parse_version("tmux 3.4") == (3, 4)
    assert tmux.parse_version("tmux 3.2a") == (3, 2)
    assert tmux.parse_version("tmux next-3.5") == (3, 5)


def test_parse_version_returns_none_on_garbage():
    assert tmux.parse_version("not tmux") is None
    assert tmux.parse_version("") is None


def test_meets_floor():
    assert tmux.meets_floor((3, 0)) is True
    assert tmux.meets_floor((3, 4)) is True
    assert tmux.meets_floor((2, 9)) is False


def test_available_false_when_not_on_path():
    assert tmux.available(which=lambda _: None) is False


def test_available_true_when_on_path():
    assert tmux.available(which=lambda _: "/usr/bin/tmux") is True


def test_build_base_uses_dedicated_socket():
    assert tmux.build_base() == ["tmux", "-L", "session-explorer"]


def test_build_start_window():
    argv = tmux.build_start_window("sid-123", "/proj")
    assert argv == [
        "tmux", "-L", "session-explorer", "new-window", "-d",
        "-n", "sid-123", "-c", "/proj", "exec claude --resume=sid-123",
    ]


def test_build_select_window():
    assert tmux.build_select_window("sid-123") == [
        "tmux", "-L", "session-explorer", "select-window", "-t", "sid-123"]


def test_build_capture():
    assert tmux.build_capture("sid-123") == [
        "tmux", "-L", "session-explorer", "capture-pane", "-ep", "-t", "sid-123"]


def test_build_list_windows():
    assert tmux.build_list_windows() == [
        "tmux", "-L", "session-explorer", "list-windows", "-F", "#{window_name}"]


def test_build_kill_window_and_server_and_detach():
    assert tmux.build_kill_window("sid-9")[-2:] == ["-t", "sid-9"]
    assert tmux.build_kill_server()[-1] == "kill-server"
    assert tmux.build_detach()[-1] == "detach-client"
