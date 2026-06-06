import pytest

from _pkg import queue_config as qc


def test_default_path_for_is_sibling_of_index():
    p = qc.default_path_for("/x/y/session-explorer-index.json")
    assert p == "/x/y/session-explorer-queue-config.json"


def test_load_missing_returns_empty(tmp_path):
    assert qc.load(str(tmp_path / "c.json")) == {"version": 1, "projects": {}}


@pytest.mark.parametrize("rid,ok", [
    ("root", True), ("ios-sim", True), ("db2", True),
    ("Root", False), ("a/b", False), ("a.b", False),
    ("..", False), ("-x", False), ("", False),
])
def test_resource_id_validation(rid, ok):
    assert qc.valid_resource_id(rid) is ok


def test_add_and_get_resource(tmp_path):
    p = str(tmp_path / "c.json")
    qc.add_resource(p, project_id="pid1", display_path="/repo/Gym",
                    resource_id="root",
                    resource={"kind": "root-dir", "path": "/repo/Gym",
                              "run_in": "root", "acquire": "sync",
                              "release": "none"})
    r = qc.get_resource(p, "pid1", "root")
    assert r["kind"] == "root-dir"
    assert r["path"] == "/repo/Gym"
    # display_path is stored as project metadata, not on a key
    data = qc.load(p)
    assert data["projects"]["pid1"]["display_path"] == "/repo/Gym"


def test_add_rejects_bad_kind(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="x",
                        resource={"kind": "bogus", "run_in": "worktree",
                                  "acquire": "none", "release": "none"})


def test_add_rejects_bad_resource_id(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="A/B",
                        resource={"kind": "name", "run_in": "worktree",
                                  "acquire": "none", "release": "none"})


def test_add_rejects_bad_release(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="x",
                        resource={"kind": "name", "run_in": "worktree",
                                  "acquire": "none", "release": "bogus"})


def test_add_command_acquire_requires_command(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="db",
                        resource={"kind": "port", "run_in": "worktree",
                                  "acquire": "command", "release": "none"})


def test_add_command_release_requires_command(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="db",
                        resource={"kind": "port", "run_in": "worktree",
                                  "acquire": "none", "release": "command"})


def test_add_root_dir_requires_path(tmp_path):
    p = str(tmp_path / "c.json")
    with pytest.raises(ValueError):
        qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="root",
                        resource={"kind": "root-dir", "run_in": "root",
                                  "acquire": "sync", "release": "none",
                                  "sync": {"delete": True, "exclude": ["/.git"],
                                           "protect": ["/.git"]}})


def test_remove_resource_and_opt_out_when_empty(tmp_path):
    p = str(tmp_path / "c.json")
    qc.add_resource(p, project_id="pid1", display_path="/r", resource_id="db",
                    resource={"kind": "port", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    assert qc.is_opted_in(p, "pid1") is True
    qc.remove_resource(p, "pid1", "db")
    assert qc.get_resource(p, "pid1", "db") is None
    assert qc.is_opted_in(p, "pid1") is False  # no resources -> opted out


def test_list_resources_and_all_projects(tmp_path):
    p = str(tmp_path / "c.json")
    qc.add_resource(p, project_id="pid1", display_path="/a", resource_id="root",
                    resource={"kind": "root-dir", "path": "/a", "run_in": "root",
                              "acquire": "sync", "release": "none"})
    qc.add_resource(p, project_id="pid2", display_path="/b", resource_id="sim",
                    resource={"kind": "device", "run_in": "worktree",
                              "acquire": "none", "release": "none"})
    assert set(qc.list_resources(p, "pid1")) == {"root"}
    assert set(qc.all_projects(p)) == {"pid1", "pid2"}
