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
    conf = tmux.build_config()
    assert "set -g mouse on" in conf
    assert "set -g status on" in conf
    assert "remain-on-exit" not in conf
    assert "bind -n F9 select-pane -t :.+" in conf
    assert "bind -n F12 resize-pane -Z" in conf
    assert 'window-status-format ""' in conf
    assert 'window-status-current-format ""' in conf
    assert "F9 ⇄ switch · F12 ⤢ full" in conf
    # Persist-by-default: detaching the client must NOT kill the server.
    assert "client-detached" not in conf
    assert "kill-server" not in conf


def test_build_config_respects_custom_keys():
    conf = tmux.build_config(switch_key="C-g", zoom_key="C-f")
    assert "bind -n C-g select-pane -t :.+" in conf
    assert "bind -n C-f resize-pane -Z" in conf


def test_build_set_label_targets_window_by_sid():
    assert tmux.build_set_label("sid-7", "sprint14") == [
        "tmux", "-L", "session-explorer",
        "set-option", "-w", "-t", "sid-7", "@se_label", "sprint14"]



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


def test_build_new_session_window_blank_name_omits_dash_n():
    from _pkg.tmux import build_new_session_window
    argv = build_new_session_window("sid-9", "/tmp/p", "")
    # The inner `claude` command is the last element (a shlex-joined string).
    # The tmux window is still named with the sid via the new-window flags, but
    # the inner command must carry no `-n` (so claude starts unnamed).
    assert argv[-1] == "exec claude --session-id sid-9"


def test_build_new_session_window_named_still_has_dash_n():
    from _pkg.tmux import build_new_session_window
    inner = build_new_session_window("sid-9", "/tmp/p", "feature")[-1]
    assert "-n feature" in inner


def test_build_new_session_window_redirects_stderr_when_err_path():
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature",
                                         err_path="/tmp/se-launch-sid-9.err")
    assert argv[-1] == (
        "exec claude --session-id sid-9 -n feature 2>/tmp/se-launch-sid-9.err")


def test_build_new_session_window_quotes_err_path_with_spaces():
    # The `2>` operator stays unquoted while the path is shlex-quoted, so a path
    # with spaces (or shell metacharacters) can never break out of the redirect.
    argv = tmux.build_new_session_window("sid-9", "/proj", "feature",
                                         err_path="/tmp/a b/err.log")
    assert argv[-1] == (
        "exec claude --session-id sid-9 -n feature 2>'/tmp/a b/err.log'")


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


def test_build_dock_focus_false_adds_dash_d():
    # -d joins the pane WITHOUT selecting it, so the explorer keeps focus —
    # used by cursor-follow sync (don't yank the user out of the tree).
    assert tmux.build_dock("sid-1", focus=False) == [
        "tmux", "-L", "session-explorer",
        "join-pane", "-d", "-h", "-l", "65%", "-s", "sid-1", "-t", "explorer"]


def test_build_dock_focus_true_is_default_and_omits_dash_d():
    assert "-d" not in tmux.build_dock("sid-1")


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


def test_build_probe_window_sets_env_and_cwd():
    argv = tmux.build_probe_window("/home/x/.claude/.session-explorer-probe")
    assert argv == [
        "tmux", "-L", "session-explorer", "new-window", "-d",
        "-n", "se-usage-probe", "-c", "/home/x/.claude/.session-explorer-probe",
        "SESSION_EXPLORER_PROBE=1 exec claude",
    ]


def test_build_send_keys_passes_keys_through():
    assert tmux.build_send_keys("se-usage-probe", "/usage", "Enter") == [
        "tmux", "-L", "session-explorer", "send-keys", "-t", "se-usage-probe",
        "/usage", "Enter",
    ]


def test_build_capture_plain_has_no_escapes_flag():
    assert tmux.build_capture_plain("se-usage-probe") == [
        "tmux", "-L", "session-explorer", "capture-pane", "-p",
        "-t", "se-usage-probe",
    ]


def test_build_set_status_left_escapes_percent():
    # tmux runs status-left through strftime, so a literal % must be doubled
    # ('%%') or it gets eaten. render_bar emits a single % (human-readable);
    # the tmux builder is where the escaping happens.
    assert tmux.build_set_status_left(" [██] 1% ↺1am") == [
        "tmux", "-L", "session-explorer", "set-option", "-g",
        "status-left", " [██] 1%% ↺1am",
    ]


def test_build_config_sets_status_left_length():
    cfg = tmux.build_config()
    assert "set -g status-left-length 40" in cfg


def test_build_set_remain_on_exit_failed():
    """The TUI marks its own pane remain-on-exit=failed so a crash preserves
    the pane (traceback on screen) instead of letting the docked claude pane
    swallow the explorer window. 'failed' (tmux >= 3.2) keeps the pane only on
    a non-zero exit, so a clean quit still closes it."""
    argv = tmux.build_set_remain_on_exit("%7")
    assert argv == ["tmux", "-L", "session-explorer", "set-option", "-p",
                    "-t", "%7", "remain-on-exit", "failed"]


# --- explorer-window self-heal (recover a claude-swallowed explorer on /open) ---

def test_explorer_window_has_tui_recognizes_python_pane():
    """The explorer's own pane runs the Textual TUI (python). A window holding
    such a pane is a live explorer."""
    assert tmux.explorer_window_has_tui(["Python"]) is True
    assert tmux.explorer_window_has_tui(["python3"]) is True
    assert tmux.explorer_window_has_tui(["session-explorer"]) is True


def test_explorer_window_has_tui_false_when_only_claude():
    """A window named 'explorer' whose only pane(s) run claude has been
    swallowed — the TUI pane is gone. (claude reports its version string as the
    pane command, e.g. '2.1.196', or 'claude'/'node'.)"""
    assert tmux.explorer_window_has_tui(["2.1.196"]) is False
    assert tmux.explorer_window_has_tui(["claude"]) is False
    assert tmux.explorer_window_has_tui(["node"]) is False
    assert tmux.explorer_window_has_tui([]) is False


def test_explorer_window_has_tui_true_when_tui_plus_docked_claude():
    """A healthy docked layout: explorer TUI pane + a docked claude pane."""
    assert tmux.explorer_window_has_tui(["Python", "2.1.196"]) is True


def test_sid_from_claude_cmd_extracts_session_id():
    cmd = ("claude --session-id 34f4be54-a854-405d-adc2-efbd47e2eb8b "
           "-n user-story/45331 -w slug")
    assert tmux.sid_from_claude_cmd(cmd) == "34f4be54-a854-405d-adc2-efbd47e2eb8b"


def test_sid_from_claude_cmd_extracts_resume_uuid():
    cmd = "claude --resume=43e4a61a-86bc-4d17-b0ed-6b519613710b"
    assert tmux.sid_from_claude_cmd(cmd) == "43e4a61a-86bc-4d17-b0ed-6b519613710b"


def test_sid_from_claude_cmd_none_when_absent():
    assert tmux.sid_from_claude_cmd("claude") is None
    assert tmux.sid_from_claude_cmd("") is None
    assert tmux.sid_from_claude_cmd(None) is None


def test_heal_explorer_impostors_renames_swallowed_window_to_its_sid():
    """When a window named 'explorer' contains only a claude pane (the TUI pane
    closed and a docked claude took the window), rename it to its session id so
    it rejoins the background-session windows and the launcher's recreate step
    rebuilds a fresh explorer. A healthy explorer (with a TUI pane) is left
    alone."""
    sid = "34f4be54-a854-405d-adc2-efbd47e2eb8b"
    renames = []
    tmux.heal_explorer_impostors(
        list_windows=lambda: ["explorer", "other-sid-window"],
        panes_of=lambda w: {"explorer": [("2.1.196", 45228)]}.get(w, []),
        cmd_of_pid=lambda pid: f"claude --session-id {sid} -n foo -w bar",
        rename=lambda old, new: renames.append((old, new)),
    )
    assert renames == [("explorer", sid)]


def test_heal_explorer_impostors_leaves_healthy_explorer_alone():
    renames = []
    tmux.heal_explorer_impostors(
        list_windows=lambda: ["explorer"],
        panes_of=lambda w: [("Python", 52289)],   # live TUI pane present
        cmd_of_pid=lambda pid: "",
        rename=lambda old, new: renames.append((old, new)),
    )
    assert renames == []


def test_heal_explorer_impostors_falls_back_when_no_sid_derivable():
    """A swallowed window whose claude args yield no UUID still must stop
    masquerading as 'explorer' (so select-window/recreate are unambiguous);
    rename to a unique, non-'explorer' fallback that keeps it running."""
    renames = []
    tmux.heal_explorer_impostors(
        list_windows=lambda: ["explorer"],
        panes_of=lambda w: [("claude", 999)],
        cmd_of_pid=lambda pid: "claude",          # no session id in args
        rename=lambda old, new: renames.append((old, new)),
    )
    assert len(renames) == 1
    old, new = renames[0]
    assert old == "explorer"
    assert new != "explorer"


# --- explorer window reconcile on startup (no orphan docks after a respawn) ---

def test_reclaim_explorer_panes_breaks_out_orphan_docks():
    """A fresh/respawned explorer can inherit leftover claude pane(s) in its
    window (docked by a previous process lifetime). Reclaim breaks each out to
    its own background window named by sid, leaving only the explorer's own
    pane — so it can't stack a second pane on the next dock."""
    sid1 = "11111111-1111-1111-1111-111111111111"
    sid2 = "22222222-2222-2222-2222-222222222222"
    cmds = {101: f"claude --resume={sid1}", 102: f"claude --session-id {sid2} -n x"}
    broke = []
    tmux.reclaim_explorer_panes(
        "%0",
        panes=lambda: [("%0", 100), ("%5", 101), ("%6", 102)],
        cmd_of_pid=lambda pid: cmds.get(pid, ""),
        break_pane=lambda pane, name: broke.append((pane, name)),
    )
    assert broke == [("%5", sid1), ("%6", sid2)]


def test_reclaim_explorer_panes_leaves_self_pane():
    broke = []
    tmux.reclaim_explorer_panes(
        "%0", panes=lambda: [("%0", 100)],
        cmd_of_pid=lambda pid: "", break_pane=lambda p, n: broke.append((p, n)))
    assert broke == []


def test_reclaim_explorer_panes_noop_when_self_pane_absent():
    """Safety floor: if our own pane id isn't even in the window's pane list we
    are NOT this server's explorer (e.g. a unit test with a fake $TMUX_PANE) —
    never break out panes that aren't ours."""
    broke = []
    tmux.reclaim_explorer_panes(
        "%99",
        panes=lambda: [("%0", 100), ("%5", 101)],
        cmd_of_pid=lambda pid: "claude --resume=33333333-3333-3333-3333-333333333333",
        break_pane=lambda p, n: broke.append((p, n)))
    assert broke == []


def test_reclaim_explorer_panes_noop_when_self_pane_none():
    broke = []
    tmux.reclaim_explorer_panes(
        None, panes=lambda: [("%0", 100), ("%5", 101)],
        cmd_of_pid=lambda pid: "", break_pane=lambda p, n: broke.append((p, n)))
    assert broke == []


def test_reclaim_explorer_panes_fallback_name_without_sid():
    broke = []
    tmux.reclaim_explorer_panes(
        "%0", panes=lambda: [("%0", 100), ("%5", 999)],
        cmd_of_pid=lambda pid: "claude", break_pane=lambda p, n: broke.append((p, n)))
    assert broke == [("%5", "orphan-999")]


def test_build_start_window_with_worktree():
    """Resuming a relocated worktree-born session re-isolates it: `claude -w
    <leaf> --resume=<sid>` recreates the worktree instead of running in root."""
    argv = tmux.build_start_window("sid-123", "/repo", worktree="46415-thing")
    assert argv == [
        "tmux", "-L", "session-explorer", "new-window", "-d",
        "-n", "sid-123", "-c", "/repo",
        "exec claude -w 46415-thing --resume=sid-123",
    ]
