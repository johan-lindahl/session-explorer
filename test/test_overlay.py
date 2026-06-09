import subprocess

from _pkg import overlay


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _repo_with_worktree(tmp_path):
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "src.txt").write_text("ROOT\n")
    _git(root, "add", "src.txt")
    _git(root, "commit", "-qm", "init")
    wt = tmp_path / "wt"
    _git(root, "worktree", "add", "-q", "-b", "feat", str(wt))
    return root, wt


def test_apply_then_restore_modified_and_added(tmp_path):
    root, wt = _repo_with_worktree(tmp_path)
    state = tmp_path / "state"
    # Worktree modifies an existing file and adds a new (untracked) one.
    (wt / "src.txt").write_text("WORKTREE\n")
    (wt / "new.txt").write_text("NEW\n")

    manifest = overlay.apply_overlay(str(wt), str(root), str(state))
    by_path = {m["path"]: m["status"] for m in manifest}
    assert by_path == {"src.txt": "modified", "new.txt": "added"}
    assert (root / "src.txt").read_text() == "WORKTREE\n"   # overlaid in
    assert (root / "new.txt").read_text() == "NEW\n"
    assert (state / overlay.MANIFEST_NAME).exists()

    assert overlay.restore_overlay(str(root), str(state)) == []
    assert (root / "src.txt").read_text() == "ROOT\n"       # checkout-restored
    assert not (root / "new.txt").exists()                  # rm-restored
    assert not (state / overlay.MANIFEST_NAME).exists()     # manifest cleaned


def test_added_file_survives_root_baseline_drift(tmp_path):
    """Regression (the empty-manifest no-op): changed_files must capture the
    worktree BRANCH's own delta (vs the merge-base with root), not vs root's
    live HEAD. If root's baseline has drifted to already contain a branch-added
    file — e.g. a prior SIGKILL'd lease whose copied file got committed into
    root — the overlay must STILL apply that file, not silently drop it and
    report an empty/partial manifest as success."""
    root, wt = _repo_with_worktree(tmp_path)
    state = tmp_path / "state"
    # The branch COMMITS a new module file (added) and modifies the existing one.
    (wt / "new.txt").write_text("NEW\n")
    (wt / "src.txt").write_text("WORKTREE\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "feature: add module + modify")
    # Root baseline drifts: the added file leaks into root's HEAD (committed),
    # leaving root's working tree CLEAN — so transition_guard would let the
    # overlay proceed, yet diffing against root HEAD would no longer see it.
    (root / "new.txt").write_text("NEW\n")
    _git(root, "add", "new.txt")
    _git(root, "commit", "-qm", "added file leaked into baseline")

    manifest = overlay.apply_overlay(str(wt), str(root), str(state))
    paths = {m["path"] for m in manifest}
    assert "new.txt" in paths      # must NOT be dropped despite living in root HEAD
    assert "src.txt" in paths


def test_changed_files_unaffected_when_root_on_fork_point(tmp_path):
    # Happy path: root sits on the branch's fork point, so merge-base == root
    # HEAD and the captured delta is exactly the worktree's own changes.
    root, wt = _repo_with_worktree(tmp_path)
    (wt / "src.txt").write_text("WORKTREE\n")
    (wt / "new.txt").write_text("NEW\n")
    assert set(overlay.changed_files(str(wt), str(root))) == {"src.txt", "new.txt"}


def test_restore_without_manifest_is_noop(tmp_path):
    root, _ = _repo_with_worktree(tmp_path)
    overlay.restore_overlay(str(root), str(tmp_path / "empty"))  # must not raise


def test_restore_failure_is_reported_and_keeps_manifest(tmp_path):
    """A git-checkout that can't restore a path is reported in the return value,
    and the manifest is preserved so the dirty root stays detectable."""
    import json
    root, _ = _repo_with_worktree(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    # 'ghost.txt' is not tracked in root, so `git checkout -- ghost.txt` fails.
    (state / overlay.MANIFEST_NAME).write_text(
        json.dumps([{"path": "ghost.txt", "status": "modified"}]))
    failed = overlay.restore_overlay(str(root), str(state))
    assert failed == ["ghost.txt"]
    assert (state / overlay.MANIFEST_NAME).exists()   # preserved on failure


def test_restore_removes_added_file_in_new_subdir(tmp_path):
    root, wt = _repo_with_worktree(tmp_path)
    state = tmp_path / "state"
    (wt / "sub").mkdir()
    (wt / "sub" / "new.txt").write_text("NEW\n")
    overlay.apply_overlay(str(wt), str(root), str(state))
    assert (root / "sub" / "new.txt").exists()
    assert overlay.restore_overlay(str(root), str(state)) == []
    assert not (root / "sub" / "new.txt").exists()
