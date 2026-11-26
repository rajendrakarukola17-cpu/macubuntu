"""
tests/audit/unit/test_tools_file_ops_coverage.py

Phase 7b — TASK 7: tools.py file operation coverage tests (write_file,
edit_file, move_file, trash_file). Tests normal flow + approval + TOCTOU
re-validation + fail-closed error cases.

No real network calls. Uses tmp_path for HOME directory isolation.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _always_approve(action, params, cb=None, title="", target=""):
    class Token:
        pass

    return Token(), None


# ---------------------------------------------------------------------------
# write_file tests
# ---------------------------------------------------------------------------


def test_write_file_normal_flow(tmp_path):
    import celina_pkg.config as config

    target = tmp_path / "test.txt"
    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(
            str(target), "hello world", approval_cb=_always_approve
        )
        assert "Wrote" in result
        assert target.read_text() == "hello world"


def test_write_file_creates_parent_dirs(tmp_path):
    import celina_pkg.config as config

    target = tmp_path / "sub" / "deep" / "test.txt"
    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(
            str(target), "nested content", approval_cb=_always_approve
        )
        assert "Wrote" in result
        assert target.read_text() == "nested content"


def test_write_file_outside_home_rejected(tmp_path):
    import celina_pkg.config as config

    outside = Path(tempfile.mkdtemp()) / "outside.txt"
    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(
            str(outside), "content", approval_cb=_always_approve
        )
        assert "Error" in result


def test_write_file_sensitive_path_rejected(tmp_path):
    import celina_pkg.config as config

    sensitive = tmp_path / ".bashrc"
    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(
            str(sensitive), "content", approval_cb=_always_approve
        )
        assert "Error" in result


# ---------------------------------------------------------------------------
# edit_file tests
# ---------------------------------------------------------------------------


def test_edit_file_normal_flow(tmp_path):
    import celina_pkg.config as config

    target = tmp_path / "edit_test.txt"
    target.write_text("original content")
    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(
            str(target), "original", "modified", approval_cb=_always_approve
        )
        assert "Edited" in result
        assert target.read_text() == "modified content"


def test_edit_file_text_not_found(tmp_path):
    import celina_pkg.config as config

    target = tmp_path / "edit_test2.txt"
    target.write_text("original content")
    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(
            str(target), "nonexistent", "replacement", approval_cb=_always_approve
        )
        assert "Error" in result


# ---------------------------------------------------------------------------
# move_file tests
# ---------------------------------------------------------------------------


def test_move_file_normal_flow(tmp_path):
    import celina_pkg.config as config

    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("move me")
    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(str(src), str(dst), approval_cb=_always_approve)
        assert "Moved" in result
        assert dst.read_text() == "move me"
        assert not src.exists()


# ---------------------------------------------------------------------------
# trash_file tests
# ---------------------------------------------------------------------------


def test_trash_file_gio_not_available(tmp_path, monkeypatch):
    import celina_pkg.config as config

    target = tmp_path / "trash_test.txt"
    target.write_text("trash me")
    with patch.object(config, "HOME", tmp_path):
        # Mock shutil.which to return None for gio
        import celina_pkg.tools as tools_mod

        monkeypatch.setattr(tools_mod.shutil, "which", lambda cmd: None)
        result = tools.trash_file(str(target), approval_cb=_always_approve)
        assert "Error" in result
        # File should still exist (not trashed)
        assert target.exists()


# ---------------------------------------------------------------------------
# Approval denial tests
# ---------------------------------------------------------------------------


def test_write_file_approval_denied(tmp_path):
    import celina_pkg.config as config

    def deny(title, detail):
        return False

    target = tmp_path / "denied.txt"
    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(
            str(target), "content", approval_cb=deny
        )
        assert "Cancelled" in result or "denied" in result.lower() or "Error" in result
        assert not target.exists()


def test_edit_file_approval_denied(tmp_path):
    import celina_pkg.config as config

    target = tmp_path / "edit_denied.txt"
    target.write_text("original")
    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(
            str(target), "original", "modified",
            approval_cb=lambda title, detail: False,
        )
        assert "Cancelled" in result or "denied" in result.lower() or "Error" in result
        assert target.read_text() == "original"  # unchanged


# ---------------------------------------------------------------------------
# write_file: token replay, arg-hash mismatch, symlinked parent
# ---------------------------------------------------------------------------


def test_write_file_token_replay(tmp_path, monkeypatch):
    """CB: write_file rejects when token already consumed (replay attack)."""
    import celina_pkg.config as config
    import celina_pkg.approval as approval_mod

    target = tmp_path / "replay.txt"

    call_count = [0]
    def fake_verify(token, tool, args, **kwargs):
        return False, "Approval token already consumed; fresh confirmation required."

    monkeypatch.setattr(approval_mod, "verify_and_consume", fake_verify)

    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(
            str(target), "content", approval_cb=lambda *a, **k: True
        )
        assert "already consumed" in result or "Error" in result
        assert not target.exists()


def test_write_file_arg_hash_mismatch(tmp_path, monkeypatch):
    """CB: write_file rejects when arguments hash doesn't match token."""
    import celina_pkg.config as config
    import celina_pkg.approval as approval_mod

    target = tmp_path / "hashmismatch.txt"

    def fake_verify(token, tool, args, **kwargs):
        return False, "Approval arguments changed after confirmation"

    monkeypatch.setattr(approval_mod, "verify_and_consume", fake_verify)

    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(
            str(target), "content", approval_cb=lambda *a, **k: True
        )
        assert "changed after confirmation" in result or "Error" in result
        assert not target.exists()


def test_write_file_symlinked_parent_outside_home(tmp_path, monkeypatch):
    """CB: write_file rejects when parent is symlink to outside HOME."""
    import celina_pkg.config as config

    outside_dir = Path(tempfile.mkdtemp()) / "outside_target"
    outside_dir.mkdir()

    home_link = tmp_path / "linked_home"
    home_link.symlink_to(outside_dir)

    target = home_link / "file.txt"

    with patch.object(config, "HOME", tmp_path):
        result = tools.write_file(str(target), "content", approval_cb=_always_approve)
        assert "Error" in result


# ---------------------------------------------------------------------------
# edit_file: token replay, arg-hash mismatch, 50MB cap
# ---------------------------------------------------------------------------


def test_edit_file_token_replay(tmp_path, monkeypatch):
    """CB: edit_file rejects when token already consumed."""
    import celina_pkg.config as config
    import celina_pkg.approval as approval_mod

    target = tmp_path / "edit_replay.txt"
    target.write_text("original content")

    def fake_verify(token, tool, args, **kwargs):
        return False, "Approval token already consumed; fresh confirmation required."

    monkeypatch.setattr(approval_mod, "verify_and_consume", fake_verify)

    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(
            str(target), "original", "modified",
            approval_cb=lambda *a, **k: True,
        )
        assert "already consumed" in result or "Error" in result
        assert target.read_text() == "original content"


def test_edit_file_arg_hash_mismatch(tmp_path, monkeypatch):
    """CB: edit_file rejects when arguments hash doesn't match token."""
    import celina_pkg.config as config
    import celina_pkg.approval as approval_mod

    target = tmp_path / "edit_hash.txt"
    target.write_text("original content")

    def fake_verify(token, tool, args, **kwargs):
        return False, "Approval arguments changed after confirmation"

    monkeypatch.setattr(approval_mod, "verify_and_consume", fake_verify)

    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(
            str(target), "original", "modified",
            approval_cb=lambda *a, **k: True,
        )
        assert "changed after confirmation" in result or "Error" in result
        assert target.read_text() == "original content"


def test_edit_file_over_50mb_cap(tmp_path, monkeypatch):
    """CB: edit_file rejects files over 50MB cap."""
    import celina_pkg.config as config

    target = tmp_path / "large.txt"
    # Write 50MB + 1 byte file
    large_content = "x" * (50 * 1024 * 1024 + 1)
    target.write_text(large_content)

    with patch.object(config, "HOME", tmp_path):
        result = tools.edit_file(
            str(target), "x", "y",
            approval_cb=_always_approve,
        )
        assert "File too large" in result or "Error" in result


# ---------------------------------------------------------------------------
# move_file: approval denied, outside home, source not found, sensitive, collision
# ---------------------------------------------------------------------------


def test_move_file_approval_denied(tmp_path):
    """CB: move_file rejects when approval denied."""
    import celina_pkg.config as config

    src = tmp_path / "move_src.txt"
    dst = tmp_path / "move_dst.txt"
    src.write_text("move me")

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(
            str(src), str(dst),
            approval_cb=lambda title, detail: False,
        )
        assert "Cancelled" in result or "denied" in result.lower() or "Error" in result
        assert src.exists()
        assert not dst.exists()


def test_move_file_outside_home(tmp_path):
    """CB: move_file rejects when src or dst outside HOME."""
    import celina_pkg.config as config

    src = tmp_path / "src.txt"
    src.write_text("move me")
    outside = Path(tempfile.mkdtemp()) / "outside.txt"

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(str(src), str(outside), approval_cb=_always_approve)
        assert "Error" in result

        result2 = tools.move_file(str(outside), str(dst := tmp_path / "dst.txt"), approval_cb=_always_approve)
        assert "Error" in result2


def test_move_file_source_not_found(tmp_path):
    """CB: move_file rejects when source doesn't exist."""
    import celina_pkg.config as config

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(
            str(tmp_path / "nonexistent.txt"), str(tmp_path / "dst.txt"),
            approval_cb=_always_approve,
        )
        assert "Source not found" in result or "Error" in result


def test_move_file_sensitive_path(tmp_path):
    """CB: move_file rejects sensitive paths."""
    import celina_pkg.config as config

    sensitive_src = tmp_path / ".ssh" / "id_rsa"
    sensitive_src.parent.mkdir(parents=True)
    sensitive_src.write_text("secret key")

    dst = tmp_path / "moved.txt"

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(
            str(sensitive_src), str(dst), approval_cb=_always_approve,
        )
        assert "Sensitive" in result or "Error" in result


def test_move_file_collision(tmp_path):
    """CB: move_file handles destination already exists."""
    import celina_pkg.config as config

    src = tmp_path / "collision_src.txt"
    dst = tmp_path / "collision_dst.txt"
    src.write_text("source content")
    dst.write_text("destination content")

    with patch.object(config, "HOME", tmp_path):
        result = tools.move_file(str(src), str(dst), approval_cb=_always_approve)
        assert "Moved" in result or "Error" in result
        assert not src.exists()
        assert dst.exists()


# ---------------------------------------------------------------------------
# trash_file: approval denied, not found, home root, sensitive
# ---------------------------------------------------------------------------


def test_trash_file_approval_denied(tmp_path):
    """CB: trash_file rejects when approval denied."""
    import celina_pkg.config as config

    target = tmp_path / "trash_denied.txt"
    target.write_text("trash me")

    with patch.object(config, "HOME", tmp_path):
        result = tools.trash_file(
            str(target),
            approval_cb=lambda title, detail: False,
        )
        assert "Cancelled" in result or "denied" in result.lower() or "Error" in result
        assert target.exists()


def test_trash_file_not_found(tmp_path):
    """CB: trash_file rejects path not found."""
    import celina_pkg.config as config

    with patch.object(config, "HOME", tmp_path):
        result = tools.trash_file(
            str(tmp_path / "nonexistent.txt"), approval_cb=_always_approve,
        )
        assert "not found" in result.lower() or "Error" in result


def test_trash_file_home_root(tmp_path):
    """CB: trash_file rejects home root."""
    import celina_pkg.config as config

    with patch.object(config, "HOME", tmp_path):
        result = tools.trash_file(str(tmp_path), approval_cb=_always_approve)
        assert "Cannot trash home root" in result or "Error" in result


def test_trash_file_sensitive(tmp_path):
    """CB: trash_file rejects sensitive paths."""
    import celina_pkg.config as config

    sensitive = tmp_path / ".ssh" / "id_rsa"
    sensitive.parent.mkdir(parents=True)
    sensitive.write_text("secret")

    with patch.object(config, "HOME", tmp_path):
        result = tools.trash_file(
            str(sensitive), approval_cb=_always_approve,
        )
        assert "sensitive" in result.lower() or "Error" in result