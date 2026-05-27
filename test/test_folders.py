import os
import tempfile
from _pkg import index as _index
from _pkg.folders import add_folder, remove_folder


def _tmp_index():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    _index.save(path, {"version": 1, "folders": [], "sessions": {}})
    return path


def test_add_folder_idempotent():
    path = _tmp_index()
    try:
        add_folder(path, "audits/empty-shelf")
        add_folder(path, "audits/empty-shelf")
        assert _index.load(path)["folders"] == ["audits/empty-shelf"]
    finally:
        os.unlink(path)


def test_remove_folder_no_op_when_absent():
    path = _tmp_index()
    try:
        remove_folder(path, "ghost")
        assert _index.load(path)["folders"] == []
    finally:
        os.unlink(path)


def test_remove_folder_removes_only_matching():
    path = _tmp_index()
    try:
        add_folder(path, "a")
        add_folder(path, "b")
        remove_folder(path, "a")
        assert _index.load(path)["folders"] == ["b"]
    finally:
        os.unlink(path)
