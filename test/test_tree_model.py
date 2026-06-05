from _pkg.tree_model import (
    split_path, build_nested_tree, replace_folder_prefix, disambiguate,
)


def _idx(sessions):
    return {"version": 1, "sessions": sessions}


def _all_sessions(node):
    out = list(node["_sessions"])
    for child in node["_folders"].values():
        out.extend(_all_sessions(child))
    return out


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
# replace_folder_prefix tests
# ---------------------------------------------------------------------------

def test_replace_folder_prefix_session_directly_in_folder():
    # team/planning/sprint14 lives in folder ["team","planning"], display sprint14.
    assert replace_folder_prefix(
        "team/planning/sprint14", ["team", "planning"], ["team", "strategy"]
    ) == "team/strategy/sprint14"


def test_replace_folder_prefix_session_in_subfolder_preserves_tail():
    assert replace_folder_prefix(
        "team/planning/q1/sprint14", ["team", "planning"], ["team", "strategy"]
    ) == "team/strategy/q1/sprint14"


def test_replace_folder_prefix_reparent_to_new_root():
    # Moving folder ["team","planning"] under ["archive"] → ["archive","planning"].
    assert replace_folder_prefix(
        "team/planning/sprint14", ["team", "planning"], ["archive", "planning"]
    ) == "archive/planning/sprint14"


def test_replace_folder_prefix_reparent_to_top_level():
    # Ungrouping folder ["team","planning"] → ["planning"].
    assert replace_folder_prefix(
        "team/planning/sprint14", ["team", "planning"], ["planning"]
    ) == "planning/sprint14"


def test_replace_folder_prefix_not_under_folder_returns_none():
    # A sibling folder that merely shares a string prefix must NOT match.
    assert replace_folder_prefix(
        "team/planning-extra/a", ["team", "planning"], ["team", "strategy"]
    ) is None


def test_replace_folder_prefix_shallower_name_returns_none():
    # A top-level session named "team" is not inside folder ["team","planning"].
    assert replace_folder_prefix(
        "team", ["team", "planning"], ["team", "strategy"]
    ) is None


def test_replace_folder_prefix_unrelated_returns_none():
    assert replace_folder_prefix(
        "other/thing", ["team", "planning"], ["team", "strategy"]
    ) is None


def test_replace_folder_prefix_handles_messy_input_name():
    # Leading/trailing/double slashes in the stored name are normalised first.
    assert replace_folder_prefix(
        "/team//planning/sprint14/", ["team", "planning"], ["team", "strategy"]
    ) == "team/strategy/sprint14"


# ---------------------------------------------------------------------------
# disambiguate tests
# ---------------------------------------------------------------------------

def test_disambiguate_distinct_basenames_use_basename():
    out = disambiguate(["/u/acme/api", "/u/globex/web"])
    assert out == {"/u/acme/api": "api", "/u/globex/web": "web"}


def test_disambiguate_single_root_uses_basename():
    assert disambiguate(["/u/work/magento2"]) == {"/u/work/magento2": "magento2"}


def test_disambiguate_collision_adds_immediate_parent():
    out = disambiguate(["/u/acme/magento2", "/u/globex/magento2"])
    assert out == {
        "/u/acme/magento2": "acme/magento2",
        "/u/globex/magento2": "globex/magento2",
    }


def test_disambiguate_deep_collision_uses_ellipsis():
    # Immediate parent ("clients") and grandparent are identical; the first
    # differing ancestor is the top segment, so skipped levels collapse to "…".
    out = disambiguate([
        "/work/clients/acme/magento2",
        "/home/clients/acme/magento2",
    ])
    assert out == {
        "/work/clients/acme/magento2": "work/…/magento2",
        "/home/clients/acme/magento2": "home/…/magento2",
    }


def test_disambiguate_mixed_unique_and_colliding():
    out = disambiguate([
        "/u/solo/widget",
        "/u/acme/magento2",
        "/u/globex/magento2",
    ])
    assert out == {
        "/u/solo/widget": "widget",
        "/u/acme/magento2": "acme/magento2",
        "/u/globex/magento2": "globex/magento2",
    }


def test_disambiguate_bare_root_without_parent_keeps_itself():
    # A root with no path separators has no ancestor to borrow; it stays as-is
    # even if it shares a "basename" with an absolute path root.
    out = disambiguate(["magento2", "/u/acme/magento2"])
    assert out["magento2"] == "magento2"
    assert out["/u/acme/magento2"] == "acme/magento2"


def test_disambiguate_labels_are_unique():
    out = disambiguate([
        "/work/clients/acme/magento2",
        "/home/clients/acme/magento2",
        "/u/acme/magento2",
    ])
    assert len(set(out.values())) == 3


def test_disambiguate_pathological_collision_falls_back_to_full_path():
    # /a/p/m and /a/q/m would both reduce to "a/…/m"; the fallback keeps every
    # label unique by using the full root path for the colliding pair.
    roots = ["/a/p/m", "/a/q/m", "/z/p/m", "/z/q/m"]
    out = disambiguate(roots)
    assert len(set(out.values())) == len(roots)


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


def test_build_nested_tree_live_unnamed_surfaced_even_when_hidden():
    idx = {"sessions": {
        "u1": {"project_label": "proj", "name_cached": None, "last_active_at": "2026-01-01"},
    }}
    fs = {"projects": {}}
    # Without live_ids: hidden.
    assert build_nested_tree(idx, fs, include_unnamed=False) == {}
    # With u1 live: surfaced under the synthetic (unnamed) folder.
    t = build_nested_tree(idx, fs, include_unnamed=False, live_ids={"u1"})
    assert "proj" in t
    assert any(sid == "u1" for sid, _ in t["proj"]["_folders"]["(unnamed)"]["_sessions"])


def test_build_nested_tree_live_ids_none_is_default_behaviour():
    idx = {"sessions": {"u1": {"project_label": "proj", "name_cached": None,
                               "last_active_at": "2026-01-01"}}}
    assert build_nested_tree(idx, {"projects": {}}, include_unnamed=False) == {}


def test_build_nested_tree_only_live_unnamed_surfaces_others_stay_hidden():
    # Two unnamed sessions in the same project; only the live one surfaces.
    idx = {"sessions": {
        "live1": {"project_label": "proj", "name_cached": None, "last_active_at": "2026-01-02"},
        "dead1": {"project_label": "proj", "name_cached": None, "last_active_at": "2026-01-01"},
    }}
    t = build_nested_tree(idx, {"projects": {}}, include_unnamed=False, live_ids={"live1"})
    unnamed = t["proj"]["_folders"]["(unnamed)"]["_sessions"]
    sids = {sid for sid, _ in unnamed}
    assert sids == {"live1"}  # dead1 stays hidden


def test_build_nested_tree_live_named_session_routes_to_its_folder_not_unnamed():
    # A live session that IS named must land in its real folder, not (unnamed).
    idx = {"sessions": {
        "n1": {"project_label": "proj", "name_cached": "team/sprint", "last_active_at": "2026-01-01"},
    }}
    t = build_nested_tree(idx, {"projects": {}}, include_unnamed=False, live_ids={"n1"})
    assert "(unnamed)" not in t["proj"]["_folders"]
    team = t["proj"]["_folders"]["team"]
    assert any(sid == "n1" for sid, _ in team["_sessions"])


def test_build_nested_tree_live_only_keeps_only_live_sessions():
    idx = {"sessions": {
        "named-live": {"project_label": "p", "name_cached": "feature"},
        "named-dead": {"project_label": "p", "name_cached": "other"},
        "unnamed-live": {"project_label": "p", "name_cached": None},
        "unnamed-dead": {"project_label": "p", "name_cached": None},
    }}
    t = build_nested_tree(idx, {"projects": {}}, live_only=True,
                          live_ids={"named-live", "unnamed-live"})
    flat = {sid for proj in t.values()
            for sid, _ in _all_sessions(proj)}
    assert flat == {"named-live", "unnamed-live"}


def test_build_nested_tree_live_only_empty_when_nothing_live():
    idx = {"sessions": {
        "a": {"project_label": "p", "name_cached": "x"},
    }}
    assert build_nested_tree(idx, {"projects": {}}, live_only=True,
                             live_ids=set()) == {}


# ---------------------------------------------------------------------------
# build_nested_tree: same-named repos split by root (duplicate-project bug)
# ---------------------------------------------------------------------------

def test_build_nested_tree_splits_same_named_repos_by_root():
    """Two distinct magento2 checkouts must become two separate top-level nodes,
    keyed by repo root, each carrying a disambiguated display label."""
    idx = _idx({
        "a": {"project_label": "magento2", "name_cached": "feature-x",
              "project_path": "/u/acme/magento2",
              "last_active_at": "2026-05-27T10:00:00Z"},
        "b": {"project_label": "magento2", "name_cached": "feature-y",
              "project_path": "/u/globex/magento2",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    t = build_nested_tree(_idx(idx["sessions"]), _fs_data({}))
    assert set(t.keys()) == {"/u/acme/magento2", "/u/globex/magento2"}
    assert t["/u/acme/magento2"]["_label"] == "acme/magento2"
    assert t["/u/globex/magento2"]["_label"] == "globex/magento2"
    assert [sid for sid, _ in t["/u/acme/magento2"]["_sessions"]] == ["a"]
    assert [sid for sid, _ in t["/u/globex/magento2"]["_sessions"]] == ["b"]


def test_build_nested_tree_single_repo_keeps_bare_label():
    idx = _idx({
        "a": {"project_label": "magento2", "name_cached": "feature-x",
              "project_path": "/u/acme/magento2",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    t = build_nested_tree(idx, _fs_data({}))
    assert set(t.keys()) == {"/u/acme/magento2"}
    assert t["/u/acme/magento2"]["_label"] == "magento2"


def test_build_nested_tree_worktree_groups_under_parent_repo_root():
    """A worktree session and a normal session in the same repo share one root
    node (the repo root), so worktrees don't fragment the tree."""
    idx = _idx({
        "main": {"project_label": "magento2", "name_cached": "trunk",
                 "project_path": "/u/acme/magento2",
                 "last_active_at": "2026-05-27T10:00:00Z"},
        "wt": {"project_label": "magento2", "name_cached": "branch",
               "project_path": "/u/acme/magento2/.claude/worktrees/feat",
               "last_active_at": "2026-05-27T11:00:00Z"},
    })
    t = build_nested_tree(idx, _fs_data({}))
    assert set(t.keys()) == {"/u/acme/magento2"}
    sids = {sid for sid, _ in t["/u/acme/magento2"]["_sessions"]}
    assert sids == {"main", "wt"}


def test_build_nested_tree_folder_store_keyed_by_root():
    """Empty folders are stored under the repo root, so two same-named repos
    each get only their own stored folders."""
    idx = _idx({
        "a": {"project_label": "magento2", "name_cached": "x",
              "project_path": "/u/acme/magento2",
              "last_active_at": "2026-05-27T10:00:00Z"},
        "b": {"project_label": "magento2", "name_cached": "y",
              "project_path": "/u/globex/magento2",
              "last_active_at": "2026-05-27T10:00:00Z"},
    })
    fs = _fs_data({"/u/acme/magento2": ["planning"]})
    t = build_nested_tree(idx, fs)
    assert "planning" in t["/u/acme/magento2"]["_folders"]
    assert "planning" not in t["/u/globex/magento2"]["_folders"]
