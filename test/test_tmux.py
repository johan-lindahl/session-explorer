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


def test_build_config_contains_core_settings():
    conf = tmux.build_config(persist_flag_path="/tmp/se.flag", back_key="F12")
    assert "set -g mouse on" in conf
    assert "set -g status on" in conf
    # remain-on-exit must NOT be set — exited windows should auto-close so the
    # user pops back to the explorer instead of staring at a dead [exited] pane.
    assert "remain-on-exit" not in conf
    # Back-to-explorer key (no-prefix root binding):
    assert "bind -n F12 select-window -t explorer" in conf
    # Option C: kill the server on detach unless the persist-flag is present.
    assert "client-detached" in conf
    assert "/tmp/se.flag" in conf
    assert "kill-server" in conf
    # Status bar renders the human label (@se_label), not the raw sid window name.
    assert "window-status-format" in conf
    assert "@se_label" in conf
    # "back to explorer" hint on the right, suppressed in the explorer window.
    assert "status-right" in conf
    assert "F12 → explorer" in conf


def test_build_config_respects_custom_back_key():
    conf = tmux.build_config(persist_flag_path="/tmp/f", back_key="C-g")
    assert "bind -n C-g select-window -t explorer" in conf
    assert "C-g → explorer" in conf


def test_build_set_label_targets_window_by_sid():
    assert tmux.build_set_label("sid-7", "sprint14") == [
        "tmux", "-L", "session-explorer",
        "set-option", "-w", "-t", "sid-7", "@se_label", "sprint14"]


def test_persist_flag_set_clear_check(tmp_path):
    flag = str(tmp_path / "persist.flag")
    assert tmux.persist_flag_set(flag) is False
    tmux.set_persist_flag(flag)
    assert tmux.persist_flag_set(flag) is True
    tmux.clear_persist_flag(flag)
    assert tmux.persist_flag_set(flag) is False
    tmux.clear_persist_flag(flag)  # idempotent, no raise


def test_session_windows_excludes_explorer():
    assert tmux.session_windows(
        _list=lambda: ["explorer", "sid-1", "sid-2"]) == ["sid-1", "sid-2"]


def test_build_new_session_window_bare():
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature")
    assert argv == [
        "tmux", "-L", "session-explorer", "new-window", "-d",
        "-n", "sid-9", "-c", "/proj",
        "exec claude --session-id sid-9 -n feature",
    ]


def test_build_new_session_window_with_folder_name_no_quoting():
    argv = tmux.build_new_session_window("sid-9", "/proj", "planning/sprint14")
    assert argv[-1] == "exec claude --session-id sid-9 -n planning/sprint14"


def test_build_new_session_window_quotes_name_with_spaces():
    argv = tmux.build_new_session_window("sid-9", "/proj", "my session")
    assert argv[-1] == "exec claude --session-id sid-9 -n 'my session'"


def test_build_new_session_window_bare_worktree():
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature", worktree="")
    assert argv[-1] == "exec claude --session-id sid-9 -n feature -w"


def test_build_new_session_window_named_worktree():
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature", worktree="wt1")
    assert argv[-1] == "exec claude --session-id sid-9 -n feature -w wt1"
