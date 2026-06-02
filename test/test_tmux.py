from _pkg import tmux


def test_parse_version_extracts_major_minor():
    assert tmux.parse_version("tmux 3.4") == (3, 4)
    assert tmux.parse_version("tmux 3.2a") == (3, 2)
    assert tmux.parse_version("tmux next-3.5") == (3, 5)


def test_parse_version_returns_none_on_garbage():
    assert tmux.parse_version("not tmux") is None
    assert tmux.parse_version("") is None


def test_meets_floor():
    assert tmux.meets_floor((3, 1)) is True
    assert tmux.meets_floor((3, 4)) is True
    assert tmux.meets_floor((3, 0)) is False   # needs 3.1 for `-l <n>%` dock
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
    conf = tmux.build_config(persist_flag_path="/tmp/se.flag")
    assert "set -g mouse on" in conf
    assert "set -g status on" in conf
    # remain-on-exit must NOT be set — exited claude panes auto-close so the
    # explorer reclaims the full width.
    assert "remain-on-exit" not in conf
    # F9 switches focus between the two panes; F12 zooms the focused pane.
    assert "bind -n F9 select-pane -t :.+" in conf
    assert "bind -n F12 resize-pane -Z" in conf
    # Window tabs are gone — the explorer tree is the only session switcher.
    assert 'window-status-format ""' in conf
    assert 'window-status-current-format ""' in conf
    # Status-right advertises both keys (always visible, incl. when zoomed).
    # Status-right advertises both keys (always visible, incl. when zoomed).
    assert "F9 ⇄ switch · F12 ⤢ full" in conf
    # Option C: kill the server on detach unless the persist-flag is present.
    assert "client-detached" in conf
    assert "/tmp/se.flag" in conf
    assert "kill-server" in conf


def test_build_config_respects_custom_keys():
    conf = tmux.build_config(persist_flag_path="/tmp/f",
                             switch_key="C-g", zoom_key="C-f")
    assert "bind -n C-g select-pane -t :.+" in conf
    assert "bind -n C-f resize-pane -Z" in conf
    assert "C-g" in conf and "C-f" in conf


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


def test_build_dock_joins_session_into_explorer_on_the_right():
    # -h = horizontal split (side by side); source window `sid` becomes the
    # right pane of the `explorer` window. `-l 65%` sizes the joined (claude)
    # pane to ~65% width. NB: join-pane has no `-p` flag (that's split-window
    # only, and removed from modern tmux) — `-l <n>%` is the size syntax.
    assert tmux.build_dock("sid-1") == [
        "tmux", "-L", "session-explorer",
        "join-pane", "-h", "-l", "65%", "-s", "sid-1", "-t", "explorer"]


def test_build_dock_respects_custom_pct():
    assert tmux.build_dock("sid-1", pct=50) == [
        "tmux", "-L", "session-explorer",
        "join-pane", "-h", "-l", "50%", "-s", "sid-1", "-t", "explorer"]


def test_build_dock_actually_joins_a_pane_on_real_tmux():
    # A pure-argv assertion can't catch a well-formed-but-wrong flag (the
    # original `-p 65` returned "size missing" yet looked fine in the unit
    # test). This runs build_dock's REAL argv against a throwaway tmux server
    # and proves the explorer window goes from 1 pane to 2.
    import shutil
    import subprocess
    import pytest
    if shutil.which("tmux") is None or not tmux.meets_floor(tmux.detected_version()):
        pytest.skip("tmux >= floor not available")
    sock = "se-pytest-dock"
    base = ["tmux", "-L", sock]

    def panes():
        out = subprocess.run(
            base + ["list-panes", "-t", "explorer", "-F", "#{pane_id}"],
            capture_output=True, text=True)
        return [ln for ln in out.stdout.splitlines() if ln]

    subprocess.run(base + ["kill-server"], capture_output=True)
    try:
        subprocess.run(base + ["new-session", "-d", "-s", "explorer",
                               "-n", "explorer", "sleep 600"], check=True)
        subprocess.run(base + ["new-window", "-d", "-n", "mysid",
                               "sleep 600"], check=True)
        assert len(panes()) == 1
        # Run the real build_dock argv, but on the throwaway socket.
        argv = base + tmux.build_dock("mysid")[3:]
        rc = subprocess.run(argv, capture_output=True, text=True)
        assert rc.returncode == 0, rc.stderr
        assert len(panes()) == 2          # the dock actually happened
    finally:
        subprocess.run(base + ["kill-server"], capture_output=True)


def test_build_undock_breaks_pane_back_to_named_background_window():
    # -d keeps the broken-out window in the background; -n names it the sid so
    # session_windows()/reconciliation finds it again.
    assert tmux.build_undock("%7", "sid-1") == [
        "tmux", "-L", "session-explorer",
        "break-pane", "-d", "-s", "%7", "-n", "sid-1"]


def test_build_list_panes_lists_explorer_window_pane_ids():
    assert tmux.build_list_panes() == [
        "tmux", "-L", "session-explorer",
        "list-panes", "-t", "explorer", "-F", "#{pane_id}"]


def test_build_select_pane_targets_pane_id():
    assert tmux.build_select_pane("%7") == [
        "tmux", "-L", "session-explorer", "select-pane", "-t", "%7"]


def test_docked_pane_returns_the_pane_that_is_not_the_explorer():
    # list_panes returns both panes; the explorer's own pane id ($TMUX_PANE)
    # is filtered out, leaving the docked claude pane.
    panes = lambda: ["%0", "%3"]
    assert tmux.docked_pane("%0", _list=panes) == "%3"


def test_docked_pane_returns_none_when_only_explorer_pane():
    panes = lambda: ["%0"]
    assert tmux.docked_pane("%0", _list=panes) is None


def test_docked_pane_none_self_pane_returns_none():
    # Defensive: with no $TMUX_PANE we can't tell our own pane from claude's,
    # so report nothing docked rather than risk break-pane'ing the explorer.
    assert tmux.docked_pane(None, _list=lambda: ["%0", "%3"]) is None
