from _pkg.tui import (SHARED_ROOT_DEFAULTS, QUEUE_EXPERIMENTAL,
                      parse_path_lines)


def test_shared_root_defaults_are_the_overlay_shape():
    d = SHARED_ROOT_DEFAULTS
    assert d["kind"] == "root-dir"
    assert d["acquire"] == "command"        # NOT sync — no rsync --delete
    assert d["release"] == "command"
    assert d["run_in"] == "root"
    assert d["command_acquire"] == "session-explorer queue-overlay in"
    assert d["command_release"] == "session-explorer queue-overlay out"
    assert d["release_required"] is False
    assert "guard" not in d                 # location guard replaced commands


def test_shared_root_defaults_pass_config_validation(tmp_path):
    # The dialog saves this shape verbatim (+path); it must satisfy
    # queue_config._validate, incl. the overlay in/out pairing rule.
    from _pkg import queue_config
    cfg = str(tmp_path / "qc.json")
    res = dict(SHARED_ROOT_DEFAULTS)
    res["path"] = "/repo"
    queue_config.add_resource(cfg, project_id="p1", display_path="/repo",
                              resource_id="root", resource=res)
    assert queue_config.get_resource(cfg, "p1", "root")["path"] == "/repo"


def test_parse_path_lines():
    assert parse_path_lines("/.git\n  /.env  \n\n/certs") == [
        "/.git", "/.env", "/certs"]


def test_experimental_labels():
    from _pkg.tui import _render_queue_rows, _queue_help_text
    assert "enforced for claude tool calls" in QUEUE_EXPERIMENTAL.lower()
    assert "experimental" in _render_queue_rows([]).lower()  # pane header tag
    assert QUEUE_EXPERIMENTAL in _queue_help_text()          # full caveat
