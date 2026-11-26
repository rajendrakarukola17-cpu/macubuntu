"""
tests/audit/unit/test_tools_coverage_28.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 28).
Targets: search_files remaining branches, grep_code remaining branches.

Covers: search_files fd-find path with extension, fd-find sensitive path filtering,
  Python fallback visited > 3000 break, Python fallback extension filter skip,
  Python fallback needle match, Python fallback no matches.
  grep_code ripgrep path with results, ripgrep path with no results,
  Python fallback with match, Python fallback cap at 50.

No real filesystem or ripgrep calls. Mocks shutil.which, os.walk, run_capture,
_path operations, _is_sensitive_path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


# ---------------------------------------------------------------------------
# search_files - fd-find path with extension
# ---------------------------------------------------------------------------

def test_search_files_fd_find_with_ext(monkeypatch):
    """CB: search_files fd-find available + ext provided → --extension flag used."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/fd" if cmd == "fd" else None)
    captured = {}
    def fake_run_capture(cmd, **k):
        captured["cmd"] = list(cmd)
        return ""
    monkeypatch.setattr(tools, "run_capture", fake_run_capture)
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)

    tools.search_files("query", directory="~", extension="py")
    assert captured["cmd"] is not None
    assert "--extension" in captured["cmd"]
    assert "py" in captured["cmd"]


def test_search_files_fd_find_sensitive_filtered(monkeypatch):
    """CB: search_files fd-find → _is_sensitive_path filters some results out."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/fd" if cmd == "fd" else None)
    monkeypatch.setattr(tools, "run_capture", lambda *a, **k: "/etc/sensitive/file\n/home/user/safe/file\n")
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)

    sensitive_calls = []
    def fake_is_sensitive(p):
        sensitive_calls.append(str(p))
        return str(p).startswith("/etc/")
    monkeypatch.setattr(tools, "_is_sensitive_path", fake_is_sensitive)

    result = tools.search_files("query")
    assert "/home/user/safe/file" in result
    assert "/etc/sensitive/file" not in result
    assert len(sensitive_calls) == 2


def test_search_files_fd_find_no_results(monkeypatch):
    """CB: search_files fd-find → no results → 'No files matching' message."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/fd" if cmd == "fd" else None)
    monkeypatch.setattr(tools, "run_capture", lambda *a, **k: "")
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)

    result = tools.search_files("nonexistent")
    assert "No files matching" in result


# ---------------------------------------------------------------------------
# search_files - Python fallback path
# ---------------------------------------------------------------------------

def test_search_files_python_fallback_visited_break(monkeypatch, tmp_path):
    """CB: search_files Python fallback → visited > 3000 → break."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)

    # Make os.walk yield 3001 iterations
    walk_count = [0]
    def fake_walk(base):
        while walk_count[0] < 3001:
            walk_count[0] += 1
            yield (str(tmp_path), [], [])
    monkeypatch.setattr(tools.os, "walk", fake_walk)

    result = tools.search_files("query")
    assert result is not None


def test_search_files_python_fallback_ext_filter_skip(monkeypatch, tmp_path):
    """CB: search_files Python fallback → low_ext and not low_f.endswith(low_ext) → skip."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)

    # File that doesn't match extension
    (tmp_path / "readme.txt").write_text("content")
    monkeypatch.setattr(tools.os, "walk", lambda base: iter([(str(tmp_path), [], ["readme.txt"])]))

    result = tools.search_files("query", extension="py")
    assert "No files matching" in result or result == ""


def test_search_files_python_fallback_needle_match(monkeypatch, tmp_path):
    """CB: search_files Python fallback → low_needle in low_f → match found."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)

    (tmp_path / "my_query_file.txt").write_text("content")
    monkeypatch.setattr(tools.os, "walk", lambda base: iter([(str(tmp_path), [], ["my_query_file.txt"])]))

    result = tools.search_files("query")
    assert "my_query_file.txt" in result


def test_search_files_python_fallback_no_matches(monkeypatch, tmp_path):
    """CB: search_files Python fallback → no matches → 'No files matching'."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)

    (tmp_path / "other_file.txt").write_text("content")
    monkeypatch.setattr(tools.os, "walk", lambda base: iter([(str(tmp_path), [], ["other_file.txt"])]))

    result = tools.search_files("nonexistent_query_xyz")
    assert "No files matching" in result


# ---------------------------------------------------------------------------
# grep_code - ripgrep path
# ---------------------------------------------------------------------------

def test_grep_code_rg_with_results(monkeypatch):
    """CB: grep_code rg available → results found → return lines."""
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/rg" if cmd == "rg" else None)
    monkeypatch.setattr(tools, "_rg_sensitive_globs", lambda: [])
    monkeypatch.setattr(tools, "run_capture", lambda *a, **k: "file1.py:10: match line\nfile2.py:20: match line\n")
    monkeypatch.setattr(tools, "_filter_rg_output", lambda out: out)

    result = tools.grep_code("pattern")
    assert "match line" in result


def test_grep_code_rg_no_results(monkeypatch):
    """CB: grep_code rg available → no results → 'No matches for' message."""
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/rg" if cmd == "rg" else None)
    monkeypatch.setattr(tools, "_rg_sensitive_globs", lambda: [])
    monkeypatch.setattr(tools, "run_capture", lambda *a, **k: "")
    monkeypatch.setattr(tools, "_filter_rg_output", lambda out: out)

    result = tools.grep_code("nonexistent_pattern_xyz")
    assert "No matches for" in result


def test_grep_code_python_fallback_with_match(monkeypatch, tmp_path):
    """CB: grep_code rg not available → Python fallback → match found."""
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)

    (tmp_path / "test.py").write_text("def hello():\n    print('world')\n")
    monkeypatch.setattr(tools.os, "walk", lambda base: iter([(str(tmp_path), [], ["test.py"])]))

    # Mock Path.stat to return small file
    class FakeStat:
        st_size = 100
    monkeypatch.setattr(tools.Path, "stat", lambda self: FakeStat())
    monkeypatch.setattr(tools.Path, "read_text", lambda self, **k: "def hello():\n    print('world')\n")
    monkeypatch.setattr(tools, "_is_sensitive_path", lambda p: False)

    result = tools.grep_code("hello", path=str(tmp_path))
    assert "hello" in result


def test_grep_code_python_fallback_cap_50(monkeypatch, tmp_path):
    """CB: grep_code Python fallback → count >= 50 → capped at 50 matches."""
    monkeypatch.setattr(tools, "_is_within_home", lambda p: True)
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)

    # Create many matching lines in one file
    lines = "\n".join(f"match line {i}" for i in range(60))
    (tmp_path / "test.py").write_text(lines)
    monkeypatch.setattr(tools.os, "walk", lambda base: iter([(str(tmp_path), [], ["test.py"])]))

    class FakeStat:
        st_size = 100
    monkeypatch.setattr(tools.Path, "stat", lambda self: FakeStat())
    monkeypatch.setattr(tools.Path, "read_text", lambda self, **k: lines)
    monkeypatch.setattr(tools, "_is_sensitive_path", lambda p: False)

    result = tools.grep_code("match", path=str(tmp_path))
    assert "...(capped at 50 matches)" in result
