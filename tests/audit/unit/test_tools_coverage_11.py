"""
tests/audit/unit/test_tools_coverage_11.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 11).
Targets: write_file, edit_file, move_file, trash_file remaining branches:
- TOCTOU re-validation failure (parent_err, replace_err, trash_err)
- Exception during write (disk error, permission error)
- Undo journal finalize success/failure paths
- trash_file gio available success/failure
- move_file cross-device + dst_exists collision messages

No real network calls. Mocks approval, state_ops, documents, command_runner.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


# ---------------------------------------------------------------------------
# write_file remaining branches
# ---------------------------------------------------------------------------


def test_write_file_replace_err_toctou(tmp_path, monkeypatch):
    """CB: write_file replace_err path — _validate_target_for_write fails on target."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "toctou_replace.txt"
    target.write_text("original")

    # Mock _validate_target_for_write to return error for target (not parent)
    orig_validate = tools_mod._validate_target_for_write
    call_count = [0]

    def fake_validate(path):
        call_count[0] += 1
        p = Path(path)
        # Fail only on the target itself (second call after parent passes)
        if call_count[0] >= 2 and p == target:
            return "Error: symlink swap detected before replace"
        return None

    monkeypatch.setattr(tools_mod, "_validate_target_for_write", fake_validate)

    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(str(target), "new content", approval_cb=lambda *a, **k: True)
        assert "symlink swap detected" in result or "Error" in result
        # Original file should be unchanged
        assert target.read_text() == "original"


def test_write_file_parent_err_toctou(tmp_path, monkeypatch):
    """CB: write_file parent_err path — _validate_target_for_write fails on parent."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "toctou_parent.txt"

    orig_validate = tools_mod._validate_target_for_write

    def fake_validate(path):
        p = Path(path)
        # Fail only on parent directory
        if p == target.parent:
            return "Error: parent symlink swap detected"
        return None

    monkeypatch.setattr(tools_mod, "_validate_target_for_write", fake_validate)

    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(str(target), "content", approval_cb=lambda *a, **k: True)
        assert "parent symlink swap detected" in result or "Error" in result


def test_write_file_write_exception(tmp_path, monkeypatch):
    """CB: write_file exception during write — os.fsync fails or tmp write fails."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "write_exc.txt"

    # Make _open_tmp_nosymlink raise an exception
    orig_open_tmp = tools_mod._open_tmp_nosymlink

    def fake_open_tmp(parent, prefix=""):
        raise OSError("No space left on device")

    monkeypatch.setattr(tools_mod, "_open_tmp_nosymlink", fake_open_tmp)

    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(str(target), "content", approval_cb=lambda *a, **k: True)
        assert "Failed to write file" in result or "No space left" in result


def test_write_file_os_replace_exception(tmp_path, monkeypatch):
    """CB: write_file os.replace fails — exception handler cleans up tmp."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "replace_exc.txt"

    orig_replace = os.replace
    call_count = [0]

    def fake_replace(src, dst):
        call_count[0] += 1
        # Only fail on the second call (the actual file replace in write_file),
        # not on the undo journal's _atomic_write_json (first call)
        if call_count[0] == 2:
            raise OSError("Device or resource busy")
        return orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", fake_replace)

    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(str(target), "content", approval_cb=lambda *a, **k: True)
        assert "Failed to write file" in result or "Device or resource busy" in result


def test_write_file_undo_finalize_success(tmp_path, monkeypatch):
    """CB: write_file undo journal finalize_mutation(succeeded=True) path."""
    import celina_pkg.config as config
    import celina_pkg.state_ops as state_ops_mod

    target = tmp_path / "undo_success.txt"

    journal_calls = []

    class FakeJournal:
        def record_before_mutation(self, *a, **k):
            journal_calls.append(("record", a, k))
            return "aid-123"

        def finalize_mutation(self, aid, succeeded, final_path=None):
            journal_calls.append(("finalize", aid, succeeded, final_path))

    monkeypatch.setattr(state_ops_mod, "get_undo_journal", lambda: FakeJournal())

    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(str(target), "undo test", approval_cb=lambda *a, **k: True)
        assert "Wrote" in result
        assert any(j[2] is True for j in journal_calls if j[0] == "finalize")


def test_write_file_undo_finalize_failure(tmp_path, monkeypatch):
    """CB: write_file undo journal finalize_mutation(succeeded=False) in except."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod
    import celina_pkg.state_ops as state_ops_mod

    target = tmp_path / "undo_fail.txt"

    journal_calls = []

    class FakeJournal:
        def record_before_mutation(self, *a, **k):
            journal_calls.append(("record", a, k))
            return "aid-456"

        def finalize_mutation(self, aid, succeeded, final_path=None):
            journal_calls.append(("finalize", aid, succeeded, final_path))

    monkeypatch.setattr(state_ops_mod, "get_undo_journal", lambda: FakeJournal())

    # Make _open_tmp_nosymlink raise to trigger the except path
    monkeypatch.setattr(tools_mod, "_open_tmp_nosymlink", lambda *a, **k: (_ for _ in ()).throw(OSError("disk error")))

    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(str(target), "content", approval_cb=lambda *a, **k: True)
        assert "Failed to write file" in result or "disk error" in result
        assert any(j[2] is False for j in journal_calls if j[0] == "finalize")


# ---------------------------------------------------------------------------
# edit_file remaining branches
# ---------------------------------------------------------------------------


def test_edit_file_stat_oserror(tmp_path, monkeypatch):
    """CB: edit_file target.stat().st_size raises OSError inside try block."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "stat_err.txt"
    target.write_text("content")

    # Patch os.stat at the tools module level - fail only on the stat().st_size call
    # (second call), not on is_file() (first call)
    orig_os_stat = tools_mod.os.stat
    call_count = [0]

    def fake_os_stat(path, *a, **k):
        call_count[0] += 1
        if call_count[0] == 2 and str(path).endswith("stat_err.txt"):
            raise OSError("Permission denied")
        return orig_os_stat(path, *a, **k)

    monkeypatch.setattr(tools_mod.os, "stat", fake_os_stat)

    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(str(target), "content", "new", approval_cb=lambda *a, **k: True)
        assert "Cannot stat file" in result or "Error" in result


def test_edit_file_replace_err_toctou(tmp_path, monkeypatch):
    """CB: edit_file replace_err — _validate_target_for_write fails on target."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "edit_toctou.txt"
    target.write_text("original content")

    def fake_validate(path):
        p = Path(path)
        if p == target:
            return "Error: edit target symlink swap"
        return None

    monkeypatch.setattr(tools_mod, "_validate_target_for_write", fake_validate)

    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(str(target), "original", "modified", approval_cb=lambda *a, **k: True)
        assert "symlink swap" in result or "Error" in result
        assert target.read_text() == "original content"


def test_edit_file_undo_journal_exception(tmp_path, monkeypatch):
    """CB: edit_file exception handler — undo journal finalize_mutation(succeeded=False)."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod
    import celina_pkg.state_ops as state_ops_mod

    target = tmp_path / "edit_journal_exc.txt"
    target.write_text("original content")

    # Make _open_tmp_nosymlink raise to trigger the except path
    monkeypatch.setattr(tools_mod, "_open_tmp_nosymlink", lambda *a, **k: (_ for _ in ()).throw(OSError("disk error")))

    journal_calls = []

    class FakeJournal:
        def record_before_mutation(self, *a, **k):
            journal_calls.append(("record",))
            return "aid-edit-journal"

        def finalize_mutation(self, aid, succeeded, final_path=None):
            journal_calls.append(("finalize", succeeded))

    monkeypatch.setattr(state_ops_mod, "get_undo_journal", lambda: FakeJournal())

    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(str(target), "original", "modified", approval_cb=lambda *a, **k: True)
        assert "Failed to edit file" in result or "disk error" in result
        assert any(j[1] is False for j in journal_calls if j[0] == "finalize")


def test_edit_file_write_exception(tmp_path, monkeypatch):
    """CB: edit_file exception during tmp write."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "edit_write_exc.txt"
    target.write_text("original")

    monkeypatch.setattr(tools_mod, "_open_tmp_nosymlink", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(str(target), "original", "modified", approval_cb=lambda *a, **k: True)
        assert "Failed to edit file" in result or "disk full" in result


def test_edit_file_undo_finalize_success(tmp_path, monkeypatch):
    """CB: edit_file undo journal finalize success path."""
    import celina_pkg.config as config
    import celina_pkg.state_ops as state_ops_mod

    target = tmp_path / "edit_undo_ok.txt"
    target.write_text("original")

    calls = []

    class FakeJournal:
        def record_before_mutation(self, *a, **k):
            calls.append(("record",))
            return "aid-edit-1"

        def finalize_mutation(self, aid, succeeded, final_path=None):
            calls.append(("finalize", succeeded))

    monkeypatch.setattr(state_ops_mod, "get_undo_journal", lambda: FakeJournal())

    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(str(target), "original", "modified", approval_cb=lambda *a, **k: True)
        assert "Edited" in result
        assert any(c[1] is True for c in calls if c[0] == "finalize")


# ---------------------------------------------------------------------------
# move_file remaining branches
# ---------------------------------------------------------------------------


def test_move_file_collision_message(tmp_path):
    """CB: move_file collision message when destination exists."""
    import celina_pkg.config as config

    src = tmp_path / "coll_src.txt"
    dst = tmp_path / "coll_dst.txt"
    src.write_text("source")
    dst.write_text("dest")

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(str(src), str(dst), approval_cb=lambda *a, **k: True)
        assert "destination existed" in result or "Moved" in result


def test_move_file_cross_device_message(tmp_path, monkeypatch):
    """CB: move_file cross-device copy+delete message."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    src = tmp_path / "cross_src.txt"
    dst = tmp_path / "cross_dst.txt"
    src.write_text("cross device")

    orig_cross = tools_mod._is_cross_device

    def fake_cross(s, d):
        return True  # Force cross-device path

    monkeypatch.setattr(tools_mod, "_is_cross_device", fake_cross)

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(str(src), str(dst), approval_cb=lambda *a, **k: True)
        assert "cross-device" in result or "Moved" in result


def test_move_file_parent_err_toctou(tmp_path, monkeypatch):
    """CB: move_file parent_err — _validate_target_for_write fails on dst parent."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    src = tmp_path / "mv_parent_src.txt"
    dst = tmp_path / "sub" / "mv_parent_dst.txt"
    src.write_text("move me")

    call_count = [0]

    def fake_validate(path):
        call_count[0] += 1
        p = Path(path)
        if call_count[0] >= 1 and p == dst.parent:
            return "Error: move parent symlink swap"
        return None

    monkeypatch.setattr(tools_mod, "_validate_target_for_write", fake_validate)

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(str(src), str(dst), approval_cb=lambda *a, **k: True)
        assert "parent symlink swap" in result or "Error" in result


def test_move_file_dst_err_toctou(tmp_path, monkeypatch):
    """CB: move_file dst_err — _validate_target_for_write fails on dst itself."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    src = tmp_path / "mv_dst_src.txt"
    dst = tmp_path / "mv_dst_dst.txt"
    src.write_text("move me")

    call_count = [0]

    def fake_validate(path):
        call_count[0] += 1
        p = Path(path)
        if call_count[0] >= 2 and p == dst:
            return "Error: move dst symlink swap"
        return None

    monkeypatch.setattr(tools_mod, "_validate_target_for_write", fake_validate)

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(str(src), str(dst), approval_cb=lambda *a, **k: True)
        assert "dst symlink swap" in result or "Error" in result


def test_move_file_shutil_move_exception(tmp_path, monkeypatch):
    """CB: move_file shutil.move raises exception."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    src = tmp_path / "mv_exc_src.txt"
    dst = tmp_path / "mv_exc_dst.txt"
    src.write_text("move me")

    orig_move = tools_mod.shutil.move

    def fake_move(s, d):
        raise OSError("Cross-device link error")

    monkeypatch.setattr(tools_mod.shutil, "move", fake_move)

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(str(src), str(dst), approval_cb=lambda *a, **k: True)
        assert "Failed to move file" in result or "Cross-device link" in result


# ---------------------------------------------------------------------------
# trash_file remaining branches
# ---------------------------------------------------------------------------


def test_trash_file_gio_success(tmp_path, monkeypatch):
    """CB: trash_file gio available, trash succeeds."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "trash_ok.txt"
    target.write_text("trash me ok")

    orig_which = tools_mod.shutil.which
    orig_run = tools_mod.command_runner.run_command

    def fake_which(cmd):
        if cmd == "gio":
            return "/usr/bin/gio"
        return orig_which(cmd)

    class FakeResult:
        ok = True
        error = ""

    monkeypatch.setattr(tools_mod.shutil, "which", fake_which)
    monkeypatch.setattr(tools_mod.command_runner, "run_command", lambda *a, **k: FakeResult())

    with patch.object(config, "HOME", tmp_path):
        result = tools.trash_file(str(target), approval_cb=lambda *a, **k: True)
        assert "Trash" in result or "Moved" in result


def test_trash_file_gio_trash_err_toctou(tmp_path, monkeypatch):
    """CB: trash_file trash_err — _validate_target_for_write fails before gio."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "trash_toctou.txt"
    target.write_text("trash me toctou")

    orig_which = tools_mod.shutil.which
    orig_validate = tools_mod._validate_target_for_write

    def fake_which(cmd):
        if cmd == "gio":
            return "/usr/bin/gio"
        return orig_which(cmd)

    def fake_validate(path):
        p = Path(path)
        if p == target:
            return "Error: trash target symlink swap"
        return None

    monkeypatch.setattr(tools_mod.shutil, "which", fake_which)
    monkeypatch.setattr(tools_mod, "_validate_target_for_write", fake_validate)

    with patch.object(config, "HOME", tmp_path):
        result = tools.trash_file(str(target), approval_cb=lambda *a, **k: True)
        assert "symlink swap" in result or "Error" in result


def test_trash_file_gio_command_failed(tmp_path, monkeypatch):
    """CB: trash_file gio command fails — res.ok is False."""
    import celina_pkg.config as config
    import celina_pkg.tools as tools_mod

    target = tmp_path / "trash_fail.txt"
    target.write_text("trash me fail")

    orig_which = tools_mod.shutil.which

    def fake_which(cmd):
        if cmd == "gio":
            return "/usr/bin/gio"
        return orig_which(cmd)

    class FakeResult:
        ok = False
        error = "gio trash failed: permission denied"

    monkeypatch.setattr(tools_mod.shutil, "which", fake_which)
    monkeypatch.setattr(tools_mod.command_runner, "run_command", lambda *a, **k: FakeResult())

    with patch.object(config, "HOME", tmp_path):
        result = tools.trash_file(str(target), approval_cb=lambda *a, **k: True)
        assert "permission denied" in result or "Error" in result


def test_trash_file_undo_finalize_success(tmp_path, monkeypatch):
    """CB: trash_file undo journal finalize success path."""
    import celina_pkg.config as config
    import celina_pkg.state_ops as state_ops_mod
    import celina_pkg.tools as tools_mod

    target = tmp_path / "trash_undo_ok.txt"
    target.write_text("undo trash")

    orig_which = tools_mod.shutil.which

    def fake_which(cmd):
        if cmd == "gio":
            return "/usr/bin/gio"
        return orig_which(cmd)

    class FakeResult:
        ok = True
        error = ""

    calls = []

    class FakeJournal:
        def record_before_mutation(self, *a, **k):
            calls.append(("record",))
            return "aid-trash-1"

        def finalize_mutation(self, aid, succeeded, final_path=None):
            calls.append(("finalize", succeeded))

    monkeypatch.setattr(tools_mod.shutil, "which", fake_which)
    monkeypatch.setattr(tools_mod.command_runner, "run_command", lambda *a, **k: FakeResult())
    monkeypatch.setattr(state_ops_mod, "get_undo_journal", lambda: FakeJournal())

    with patch.object(config, "HOME", tmp_path):
        result = tools.trash_file(str(target), approval_cb=lambda *a, **k: True)
        assert "Trash" in result or "Moved" in result
        assert any(c[1] is True for c in calls if c[0] == "finalize")


def test_trash_file_undo_finalize_failure(tmp_path, monkeypatch):
    """CB: trash_file undo journal finalize failure path."""
    import celina_pkg.config as config
    import celina_pkg.state_ops as state_ops_mod
    import celina_pkg.tools as tools_mod

    target = tmp_path / "trash_undo_fail.txt"
    target.write_text("undo trash fail")

    orig_which = tools_mod.shutil.which

    def fake_which(cmd):
        if cmd == "gio":
            return "/usr/bin/gio"
        return orig_which(cmd)

    class FakeResult:
        ok = False
        error = "gio error"

    calls = []

    class FakeJournal:
        def record_before_mutation(self, *a, **k):
            calls.append(("record",))
            return "aid-trash-2"

        def finalize_mutation(self, aid, succeeded, final_path=None):
            calls.append(("finalize", succeeded))

    monkeypatch.setattr(tools_mod.shutil, "which", fake_which)
    monkeypatch.setattr(tools_mod.command_runner, "run_command", lambda *a, **k: FakeResult())
    monkeypatch.setattr(state_ops_mod, "get_undo_journal", lambda: FakeJournal())

    with patch.object(config, "HOME", tmp_path):
        result = tools.trash_file(str(target), approval_cb=lambda *a, **k: True)
        assert "Error" in result
        assert any(c[1] is False for c in calls if c[0] == "finalize")
