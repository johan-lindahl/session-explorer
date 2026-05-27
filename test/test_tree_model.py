from _pkg.tree_model import split_folder, build_tree, split_path, build_nested_tree


def test_split_folder_empty():
    assert split_folder(None) == ("", "")
    assert split_folder("") == ("", "")


def test_split_folder_no_dash():
    assert split_folder("sprint14") == ("", "sprint14")


def test_split_folder_one_dash():
    assert split_folder("planning-sprint14") == ("planning", "sprint14")


def test_split_folder_many_dashes():
    assert split_folder("audits-q1-review-final") == ("audits", "q1-review-final")


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
    acme_api = tree["acme-api"]
    assert "refactors" in acme_api
    assert {sid for sid, _ in acme_api["refactors"]} == {"a", "b"}
    # within a folder, newest first
    assert acme_api["refactors"][0][0] == "a"


def test_build_tree_unnamed_lands_in_unnamed_bucket():
    data = _idx({
        "u1": {"project_label": "proj", "name_cached": None,
               "last_active_at": "2026-05-27T10:00:00Z"},
    })
    tree = build_tree(data)
    assert "(unnamed)" in tree["proj"]
    assert tree["proj"]["(unnamed)"][0][0] == "u1"


def test_build_tree_excludes_unnamed_when_disabled():
    data = _idx({
        "n1": {"project_label": "proj", "name_cached": "sprint14",
               "last_active_at": "2026-05-27T10:00:00Z"},
        "u1": {"project_label": "proj", "name_cached": None,
               "last_active_at": "2026-05-27T09:00:00Z"},
        "u2": {"project_label": "lonely", "name_cached": None,
               "last_active_at": "2026-05-27T08:00:00Z"},
    })
    tree = build_tree(data, include_unnamed=False)
    # Named session is preserved.
    assert "" in tree["proj"]
    assert tree["proj"][""][0][0] == "n1"
    # Unnamed session is dropped — no (unnamed) bucket in proj.
    assert "(unnamed)" not in tree["proj"]
    # A project whose only sessions are unnamed disappears entirely.
    assert "lonely" not in tree


def test_build_tree_includes_empty_folders_when_unnamed_disabled():
    # Empty user-created folders should still surface even when unnamed
    # sessions are hidden — they're a separate concept.
    data = _idx({}, folders=["audits/empty-shelf"])
    tree = build_tree(data, include_unnamed=False)
    assert "(unfiled)" in tree
    assert "audits/empty-shelf" in tree["(unfiled)"]


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


# ---------------------------------------------------------------------------
# split_path tests
# ---------------------------------------------------------------------------

def test_split_path_none():
    assert split_path(None) == ([], "")


def test_split_path_empty():
    assert split_path("") == ([], "")


def test_split_path_no_slash():
    assert split_path("sprint14") == ([], "sprint14")


def test_split_path_one_slash():
    assert split_path("planning/sprint14") == (["planning"], "sprint14")


def test_split_path_many_slashes():
    assert split_path("team/planning/q1/notes") == (["team", "planning", "q1"], "notes")


def test_split_path_leading_slash_dropped():
    assert split_path("/planning/x") == (["planning"], "x")


def test_split_path_trailing_slash_dropped():
    assert split_path("planning/x/") == (["planning"], "x")


def test_split_path_double_slash_collapses():
    assert split_path("planning//x") == (["planning"], "x")


def test_split_path_whitespace_only_segments_dropped():
    assert split_path("planning/  /x") == (["planning"], "x")


def test_split_path_only_slashes_returns_empty():
    assert split_path("///") == ([], "")


def test_split_path_preserves_dashes_in_segments():
    """Dashes are no longer separators — they're literal characters in segments."""
    assert split_path("bugfix-watch/v2") == (["bugfix-watch"], "v2")
    assert split_path("bugfix-watch-lockup") == ([], "bugfix-watch-lockup")


# ---------------------------------------------------------------------------
# build_nested_tree tests
# ---------------------------------------------------------------------------

def _fs_data(projects):
    return {"version": 1, "projects": dict(projects)}


def test_build_nested_tree_root_session_no_slash():
    idx = _idx({
        "a": {"project_label": "acme-api", "name_cached": "sprint14",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({"acme-api": []})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    assert set(t.keys()) == {"acme-api"}
    proj = t["acme-api"]
    assert [sid for sid, _ in proj["_sessions"]] == ["a"]
    assert proj["_folders"] == {}


def test_build_nested_tree_session_with_path_creates_intermediates():
    idx = _idx({
        "a": {"project_label": "acme-api",
              "name_cached": "team/planning/sprint14",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    team = t["acme-api"]["_folders"]["team"]
    planning = team["_folders"]["planning"]
    assert team["_sessions"] == []
    assert planning["_sessions"] != []
    sid, s = planning["_sessions"][0]
    assert sid == "a"
    assert s["name_cached"] == "team/planning/sprint14"


def test_build_nested_tree_stored_path_creates_empty_folders():
    idx = _idx({})
    fs = _fs_data({"acme-api": ["planning/sprint14"]})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    planning = t["acme-api"]["_folders"]["planning"]
    sprint = planning["_folders"]["sprint14"]
    assert planning["_sessions"] == []
    assert sprint["_sessions"] == []
    assert sprint["_folders"] == {}


def test_build_nested_tree_sessions_sorted_desc_within_folder():
    idx = _idx({
        "a": {"project_label": "p", "name_cached": "x/a",
              "last_active_at": "2026-05-26T10:00:00Z"},
        "b": {"project_label": "p", "name_cached": "x/b",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    sids = [sid for sid, _ in t["p"]["_folders"]["x"]["_sessions"]]
    assert sids == ["b", "a"]


def test_build_nested_tree_unnamed_hidden_by_default():
    idx = _idx({
        "u": {"project_label": "p", "name_cached": None,
              "last_active_at": "2026-05-27T10:00:00Z"},
        "n": {"project_label": "p", "name_cached": "kept",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    sids = [sid for sid, _ in t["p"]["_sessions"]]
    assert sids == ["n"]
    assert "(unnamed)" not in t["p"]["_folders"]


def test_build_nested_tree_unnamed_surfaced_in_pseudo_folder():
    idx = _idx({
        "u": {"project_label": "p", "name_cached": None,
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({})
    t = build_nested_tree(idx, fs, include_unnamed=True)
    assert "(unnamed)" in t["p"]["_folders"]
    assert [sid for sid, _ in t["p"]["_folders"]["(unnamed)"]["_sessions"]] == ["u"]


def test_build_nested_tree_unfiled_project_appears_when_store_has_it():
    idx = _idx({})
    fs = _fs_data({"(unfiled)": ["legacy-shelf"]})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    assert "(unfiled)" in t
    assert "legacy-shelf" in t["(unfiled)"]["_folders"]


def test_build_nested_tree_root_and_nested_sessions_coexist():
    """A project with both a root-level named session and a folder-grouped one
    must materialize both: the root in `_sessions`, the nested under `_folders`."""
    idx = _idx({
        "root-sid": {"project_label": "p", "name_cached": "standalone",
                     "last_active_at": "2026-05-27T10:00:00Z"},
        "folder-sid": {"project_label": "p", "name_cached": "plans/q1",
                       "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({})
    t = build_nested_tree(idx, fs, include_unnamed=False)
    root_sids = [sid for sid, _ in t["p"]["_sessions"]]
    folder_sids = [sid for sid, _ in t["p"]["_folders"]["plans"]["_sessions"]]
    assert root_sids == ["root-sid"]
    assert folder_sids == ["folder-sid"]


def test_build_nested_tree_stored_path_overlapping_session_folder_does_not_duplicate():
    """A session named plans/q1/notes has folder path ["plans","q1"] and display
    "notes", so it lands in `plans/q1`. When the folder store ALSO carries
    "plans/q1", the merged tree must keep a single `q1` node carrying the
    session — setdefault must preserve the existing node, not replace it."""
    idx = _idx({
        "a": {"project_label": "p", "name_cached": "plans/q1/notes",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({"p": ["plans/q1"]})  # overlaps the session's folder path
    t = build_nested_tree(idx, fs, include_unnamed=False)
    plans = t["p"]["_folders"]["plans"]
    q1 = plans["_folders"]["q1"]
    # Session lives directly under q1 (its display is "notes"); no extra subfolders.
    assert [sid for sid, _ in q1["_sessions"]] == ["a"]
    assert q1["_folders"] == {}
    # No duplicate q1 sibling under plans.
    assert list(plans["_folders"].keys()) == ["q1"]
