from _pkg.tree_model import split_folder, build_tree


def test_split_folder_empty():
    assert split_folder(None) == ("", "")
    assert split_folder("") == ("", "")


def test_split_folder_no_dash():
    assert split_folder("sprint14") == ("", "sprint14")


def test_split_folder_one_dash():
    assert split_folder("planning-sprint14") == ("planning", "sprint14")


def test_split_folder_many_dashes():
    assert split_folder("audits-q1-review") == ("audits", "q1-review")


def _idx(sessions, folders=()):
    return {"version": 1, "folders": list(folders), "sessions": sessions}


def test_build_tree_groups_by_project_then_folder():
    data = _idx({
        "a": {"project_label": "acme-api", "name_cached": "refactors-checkout",
              "last_active_at": "2026-05-27T10:00:00Z"},
        "b": {"project_label": "acme-api", "name_cached": "refactors-cart",
              "last_active_at": "2026-05-26T10:00:00Z"},
        "c": {"project_label": "acme-web", "name_cached": "planning-sprint14",
              "last_active_at": "2026-05-27T09:00:00Z"},
    })
    tree = build_tree(data)
    assert sorted(tree.keys()) == ["acme-api", "acme-web"]
    acme = tree["acme-api"]
    assert "refactors" in acme
    assert {sid for sid, _ in acme["refactors"]} == {"a", "b"}
    # within a folder, newest first
    assert acme["refactors"][0][0] == "a"


def test_build_tree_unnamed_lands_in_unnamed_bucket():
    data = _idx({
        "u1": {"project_label": "proj", "name_cached": None,
               "last_active_at": "2026-05-27T10:00:00Z"},
    })
    tree = build_tree(data)
    assert "(unnamed)" in tree["proj"]
    assert tree["proj"]["(unnamed)"][0][0] == "u1"


def test_build_tree_no_folder_lands_in_no_folder_bucket():
    data = _idx({
        "n1": {"project_label": "proj", "name_cached": "sprint14",
               "last_active_at": "2026-05-27T10:00:00Z"},
    })
    tree = build_tree(data)
    assert "" in tree["proj"]
    assert tree["proj"][""][0][0] == "n1"


def test_build_tree_includes_empty_folders():
    data = _idx({}, folders=["audits/empty-shelf"])
    # Empty folders aren't tied to a project — they live under a synthetic "(unfiled)" project.
    tree = build_tree(data)
    assert "(unfiled)" in tree
    assert "audits/empty-shelf" in tree["(unfiled)"]
    assert tree["(unfiled)"]["audits/empty-shelf"] == []
