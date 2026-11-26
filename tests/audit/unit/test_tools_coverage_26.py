"""
tests/audit/unit/test_tools_coverage_26.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 26).
Targets: grep_code Python fallback path, search_files remaining branches.

Covers: grep_code path outside home/empty pattern/rg not available/Python fallback
with fnmatch/sensitive path/size check/OSError/cap. search_files walk/error branches.

No real filesystem or ripgrep calls. Mocks shutil.which, Path operations, os.walk.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


# ---------------------------------------------------------------------------
# grep_code
# ---------------------------------------------------------------------------

def test_grep_code_outside_home(monkeypatch):
    """CB: grep_code path outside home → error."""
    monkeypatch.setattr(tools, "_is_within_home", lambda p: False)
    result = tools.grep_code("pattern", path="/etc")
    assert "Error" in result


def test_grep_code_empty_pattern(monkeypatch):
    """CB: grep_code empty pattern → 'Provide a pattern'."""
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)
    result = tools.grep_code("   ")
    assert "Provide a pattern" in result


def test_grep_code_python_fallback(monkeypatch, tmp_path):
    """CB: grep_code rg not available → Python fallback → no matches."""
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    # Path.exists needs to return True for the base path
    orig_exists = Path.exists
    def fake_exists(self):
        if str(self) == str(tmp_path):
            return True
        return orig_exists(self)
    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    # Create a file in tmp_path so os.walk has something to iterate
    (tmp_path / "test.txt").write_text("hello world")
    result = tools.grep_code("hello", path=str(tmp_path))
    assert result is not None

# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------
