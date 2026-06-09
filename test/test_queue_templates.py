from _pkg.tui import QUEUE_TEMPLATES, template_resource


def test_templates_cover_documented_cases():
    keys = {t["key"] for t in QUEUE_TEMPLATES}
    assert {"bind-mounted-stack", "browser-e2e", "ios-sim",
            "shared-db", "root-env", "device-seat", "custom"} <= keys


def test_template_resource_root_dir_has_sync_and_protect():
    res = template_resource("root-env", path="/repo")
    assert res["kind"] == "root-dir"
    assert res["acquire"] == "sync"
    assert res["run_in"] == "root"
    assert "/.env" in res["sync"]["protect"]


def test_template_resource_device_forces_worktree_no_sync():
    res = template_resource("ios-sim", path="")
    assert res["kind"] in ("device", "name")
    assert res["run_in"] == "worktree"
    assert res["acquire"] == "none"
    assert "sync" not in res


def test_custom_template_is_blank_none_strategy():
    res = template_resource("custom", path="")
    assert res["acquire"] == "none"
    assert res["run_in"] == "worktree"


from _pkg.tui import (parse_guard_lines, format_guard_lines, parse_path_lines,
                      parse_wait_for, format_wait_for)


def test_parse_guard_lines():
    rules = parse_guard_lines("docker compose up\nplaywright test\n\n  \n")
    assert rules == [{"exe": "docker", "sub": ["compose", "up"]},
                     {"exe": "playwright", "sub": ["test"]}]


def test_guard_lines_roundtrip():
    rules = [{"exe": "docker", "sub": ["compose", "up"]}, {"exe": "xcodebuild", "sub": ["test"]}]
    assert parse_guard_lines(format_guard_lines(rules)) == rules


def test_parse_path_lines():
    assert parse_path_lines("/.git\n  /.env  \n\n/certs") == ["/.git", "/.env", "/certs"]


def test_parse_wait_for():
    assert parse_wait_for("url http://localhost:8080", "120") == {
        "type": "url", "target": "http://localhost:8080", "timeout": 120.0}
    assert parse_wait_for("port localhost:5432", "") == {
        "type": "port", "target": "localhost:5432", "timeout": 60.0}
    assert parse_wait_for("", "30") is None          # empty → no spec
    assert parse_wait_for("bogus x", "10") is None    # unknown type → no spec


def test_wait_for_roundtrip():
    spec = {"type": "url", "target": "http://localhost:8080", "timeout": 120.0}
    line, t = format_wait_for(spec)
    assert parse_wait_for(line, t) == spec
    assert format_wait_for(None) == ("", "")


def test_overlay_template_is_command_mutex_with_curated_guard():
    res = template_resource("overlay-installed-root", path="/repo")
    assert res["kind"] == "root-dir"
    assert res["acquire"] == "command"      # NOT sync — no rsync --delete
    assert res["release"] == "command"
    assert res["run_in"] == "root"
    assert res["path"] == "/repo"
    assert res["command_acquire"] == "session-explorer queue-overlay in"
    assert res["command_release"] == "session-explorer queue-overlay out"
    exes = {(r["exe"], tuple(r["sub"])) for r in res["guard"]}
    assert ("phpunit", ()) in exes
    assert ("phpstan", ()) in exes
    assert ("magento", ("setup:di:compile",)) in exes
    assert ("magento", ("setup:upgrade",)) in exes
    # phpcs is worktree-safe and must NOT be guarded through the root mutex.
    assert not any(r["exe"] in ("phpcs", "php-cs-fixer") for r in res["guard"])
