"""
tests/audit/unit/test_toctou_write_file.py

Phase 7 remediation — Test for SUSPECTED-2:
TOCTOU between resolve() and os.replace() in tools.write_file (and
edit_file, move_file, trash_file).

Verifies that:
1. A target path resolving outside home is rejected by _validate_target_for_write.
2. A parent directory inside home that is a symlink to outside home is rejected.
3. A symlink swap mid-operation is caught by re-validation before os.replace.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools


# ---------------------------------------------------------------------------
# Helper: approval callback that always approves
# ---------------------------------------------------------------------------


def _always_approve(action, params, cb=None, title="", target=""):
    class Token:
        pass

    return Token(), None


# ---------------------------------------------------------------------------
# Test 1: _validate_target_for_write rejects paths outside home
# ---------------------------------------------------------------------------


def test_validate_target_for_write_outside_home():
    """_validate_target_for_write must reject paths outside home."""
    err = tools._validate_target_for_write(Path("/etc/shadow"))
    assert err is not None
    assert "outside home" in err.lower() or "sensitive" in err.lower()


def test_validate_target_for_write_inside_home_ok(tmp_path):
    """_validate_target_for_write must accept paths inside home."""
    import celina_pkg.config as config

    with patch.object(config, "HOME", tmp_path):
        inside = tmp_path / "safe_file.txt"
        err = tools._validate_target_for_write(inside)
        assert err is None, f"Expected None for safe path, got: {err}"


# ---------------------------------------------------------------------------
# Test 2: Symlinked parent directory outside home → rejected for write_file
# ---------------------------------------------------------------------------


def test_write_file_symlinked_parent_outside_home_rejected(tmp_path):
    """write_file must reject when parent is symlinked outside home."""
    import celina_pkg.config as config

    real_home = config.HOME

    # outside_dir is NOT inside tmp_path (which will be HOME)
    outside_dir = Path(tempfile.mkdtemp()) / "outside_home"
    outside_dir.mkdir()

    # inside_link is inside tmp_path (= HOME) but points outside home
    inside_link = tmp_path / "inside_link"
    inside_link.symlink_to(outside_dir)

    try:
        with patch.object(config, "HOME", tmp_path):
            target = inside_link / "secret.txt"
            result = tools.write_file(
                str(target), "malicious content", approval_cb=_always_approve
            )
            assert "Error" in result, (
                f"Expected error for symlinked parent outside home, got: {result}"
            )
    finally:
        # Cleanup symlink
        if inside_link.exists() or inside_link.is_symlink():
            inside_link.unlink()


# ---------------------------------------------------------------------------
# Test 3: Symlink swap mid-operation caught before os.replace
# ---------------------------------------------------------------------------


def test_write_file_symlink_swap_before_replace_rejected(tmp_path, monkeypatch):
    """If target is swapped to outside home between mkdir and os.replace,
    the re-validation catches it and aborts."""
    import celina_pkg.config as config

    # outside_dir is NOT inside tmp_path
    outside_dir = Path(tempfile.mkdtemp()) / "outside_target"
    outside_dir.mkdir()

    # safe_dir inside tmp_path (= HOME)
    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    target = safe_dir / "target.txt"

    with patch.object(config, "HOME", tmp_path):
        original_validate = tools._validate_target_for_write
        call_count = [0]

        def mock_validate(path):
            call_count[0] += 1
            resolved = Path(path).expanduser().resolve()
            # Second call (target check before replace) — reject as outside home
            if call_count[0] >= 2 and resolved == target.resolve():
                return "Error: write target changed during operation (TOCTOU check)."
            return original_validate(path)

        monkeypatch.setattr(tools, "_validate_target_for_write", mock_validate)

        result = tools.write_file(str(target), "content", approval_cb=_always_approve)
        assert "Error" in result, f"Expected TOCTOU error, got: {result}"
        assert call_count[0] >= 2, "validate_target_for_write should be called at least twice"


# ---------------------------------------------------------------------------
# Test 4: edit_file TOCTOU - re-validation catches outside-home target
# ---------------------------------------------------------------------------


def test_edit_file_toctou_rejected(tmp_path, monkeypatch):
    """edit_file re-validation must catch a target swapped to outside home."""
    import celina_pkg.config as config

    outside_dir = Path(tempfile.mkdtemp()) / "outside_target"
    outside_dir.mkdir()

    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    target = safe_dir / "target.txt"
    target.write_text("original content")

    with patch.object(config, "HOME", tmp_path):
        original_validate = tools._validate_target_for_write
        call_count = [0]

        def mock_validate(path):
            call_count[0] += 1
            resolved = Path(path).expanduser().resolve()
            # edit_file calls _validate_target_for_write once before os.replace
            if call_count[0] >= 1 and resolved == target.resolve():
                return "Error: write target changed during operation (TOCTOU check)."
            return original_validate(path)

        monkeypatch.setattr(tools, "_validate_target_for_write", mock_validate)

        result = tools.edit_file(
            str(target), "original", "modified", approval_cb=_always_approve
        )
        assert "Error" in result, f"Expected TOCTOU error, got: {result}"
        assert call_count[0] >= 1, "validate_target_for_write should be called at least once"


# ---------------------------------------------------------------------------
# Test 5: move_file TOCTOU - re-validation catches outside-home destination
# ---------------------------------------------------------------------------


def test_move_file_toctou_rejected(tmp_path, monkeypatch):
    """move_file re-validation must catch a destination swapped to outside home."""
    import celina_pkg.config as config

    outside_dir = Path(tempfile.mkdtemp()) / "outside_target"
    outside_dir.mkdir()

    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    src = safe_dir / "source.txt"
    src.write_text("source content")
    dst = safe_dir / "dest.txt"

    with patch.object(config, "HOME", tmp_path):
        original_validate = tools._validate_target_for_write
        call_count = [0]

        def mock_validate(path):
            call_count[0] += 1
            resolved = Path(path).expanduser().resolve()
            if call_count[0] >= 2 and resolved == dst.resolve():
                return "Error: write target changed during operation (TOCTOU check)."
            return original_validate(path)

        monkeypatch.setattr(tools, "_validate_target_for_write", mock_validate)

        result = tools.move_file(str(src), str(dst), approval_cb=_always_approve)
        assert "Error" in result, f"Expected TOCTOU error, got: {result}"


# ---------------------------------------------------------------------------
# Test 6: trash_file TOCTOU - re-validation catches outside-home target
# ---------------------------------------------------------------------------


def test_trash_file_toctou_rejected(tmp_path, monkeypatch):
    """trash_file re-validation must catch a target swapped to outside home."""
    import celina_pkg.config as config

    outside_dir = Path(tempfile.mkdtemp()) / "outside_target"
    outside_dir.mkdir()

    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    target = safe_dir / "target.txt"
    target.write_text("content")

    with patch.object(config, "HOME", tmp_path):
        original_validate = tools._validate_target_for_write
        call_count = [0]

        def mock_validate(path):
            call_count[0] += 1
            resolved = Path(path).expanduser().resolve()
            # trash_file calls _validate_target_for_write once before gio trash
            if call_count[0] >= 1 and resolved == target.resolve():
                return "Error: write target changed during operation (TOCTOU check)."
            return original_validate(path)

        monkeypatch.setattr(tools, "_validate_target_for_write", mock_validate)

        result = tools.trash_file(str(target), approval_cb=_always_approve)
        assert "Error" in result, f"Expected TOCTOU error, got: {result}"
        assert call_count[0] >= 1, "validate_target_for_write should be called at least once"
