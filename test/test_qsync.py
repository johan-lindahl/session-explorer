from _pkg import qsync


def test_filters_anchor_and_dedupe():
    f = qsync.build_filters(exclude=["/.git", "node_modules"],
                            protect=["/.git", "/.env"])
    # exclude + protect unioned, each rendered as an anchored exclude filter
    assert "--filter=exclude /.git" in f
    assert "--filter=exclude /node_modules" in f
    assert "--filter=exclude /.env" in f
    # /.git appears once despite being in both lists
    assert f.count("--filter=exclude /.git") == 1


def test_rsync_command_shape():
    cmd = qsync.rsync_command("/wt", "/root", exclude=["/.git"], protect=["/.env"],
                              dry_run=False)
    assert cmd[0] == "rsync"
    assert "-a" in cmd and "--delete" in cmd
    assert "--delete-excluded" not in cmd     # never; excluded must survive
    assert cmd[-2] == "/wt/" and cmd[-1] == "/root/"   # trailing slashes


def test_dry_run_adds_itemize_flags():
    cmd = qsync.rsync_command("/wt", "/root", exclude=[], protect=[], dry_run=True)
    assert "-n" in cmd and "-i" in cmd


def test_trailing_slashes_normalized():
    cmd = qsync.rsync_command("/wt/", "/root/", exclude=[], protect=[], dry_run=False)
    assert cmd[-2] == "/wt/" and cmd[-1] == "/root/"
