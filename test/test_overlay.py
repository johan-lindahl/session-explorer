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
