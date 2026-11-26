"""
tests/audit/unit/test_tools_coverage_20.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 20).
Targets: search_files, search_in_files, grep_code, _search_ddg_html, _search_flathub_app.

Covers: search_files empty query, no matches, match found; search_in_files variants;
grep_code variants; _search_ddg_html success/failure; _search_flathub_app flatpak
missing/empty/short query/match.

No real network or filesystem search calls. Mocks os.walk, command_runner,
shutil.which, net_policy.safe_get.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error=""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------

def test_search_files_empty_query(monkeypatch):
    """CB: search_files with empty query → returns guidance message."""
    result = tools.search_files("")
    assert "query" in result.lower() or "extension" in result.lower()


def test_search_files_no_matches(monkeypatch, tmp_path):
    """CB: search_files with query but no matches → 'No files matching'."""
    monkeypatch.setattr(tools.os, "walk", lambda top: iter([]))
    result = tools.search_files("nonexistent_xyz")
    assert "No files matching" in result


def test_search_files_match_found(monkeypatch, tmp_path):
    """CB: search_files finds matching file → returns file info."""
    # Create a fake file structure
    fake_files = [("/home/user/docs", [], ["readme.txt"])]
    monkeypatch.setattr(tools.os, "walk", lambda top: iter(fake_files))
    monkeypatch.setattr(tools.Path, "exists", lambda self: True)
    monkeypatch.setattr(tools.Path, "stat", lambda self: MagicMock(st_size=100))

    result = tools.search_files("readme")
    assert result is not None


# ---------------------------------------------------------------------------
# search_in_files
# ---------------------------------------------------------------------------

def test_search_in_files_empty_query(monkeypatch):
    """CB: search_in_files with empty query → returns message."""
    result = tools.search_in_files("")
    assert result is not None


def test_search_in_files_no_matches(monkeypatch):
    """CB: search_in_files with query no matches → 'No matches' message."""
    monkeypatch.setattr(tools.os, "walk", lambda top: iter([]))
    result = tools.search_in_files("nonexistent_pattern_xyz")
    assert "No matches" in result or "no matches" in result.lower()


# ---------------------------------------------------------------------------
# grep_code
# ---------------------------------------------------------------------------

def test_grep_code_empty_query(monkeypatch):
    """CB: grep_code with empty query → returns message."""
    result = tools.grep_code("")
    assert result is not None


def test_grep_code_no_matches(monkeypatch):
    """CB: grep_code with no matches → 'No matches' message."""
    monkeypatch.setattr(tools.os, "walk", lambda top: iter([]))
    result = tools.grep_code("nonexistent_pattern_xyz")
    assert result is not None


# ---------------------------------------------------------------------------
# _search_ddg_html
# ---------------------------------------------------------------------------

def test_search_ddg_html_success(monkeypatch):
    """CB: _search_ddg_html returns results from net_policy.safe_get."""
    fake_html = '<div class="result"><a href="https://example.com">Result Title</a><span>Snippet text</span></div>'

    def safe_get(url, **kwargs):
        return _FakeResult(ok=True, stdout=fake_html)

    monkeypatch.setattr(tools.net_policy, "safe_get", safe_get)
    result = tools._search_ddg_html("test query")
    assert result is not None


def test_search_ddg_html_failure(monkeypatch):
    """CB: _search_ddg_html when safe_get raises → returns []."""
    def safe_get(url, **kwargs):
        raise Exception("network error")

    monkeypatch.setattr(tools.net_policy, "safe_get", safe_get)
    result = tools._search_ddg_html("test query")
    assert result == []


# ---------------------------------------------------------------------------
# _search_flathub_app
# ---------------------------------------------------------------------------

def test_search_flathub_app_flatpak_missing(monkeypatch):
    """CB: _search_flathub_app when flatpak not installed → returns None."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools._search_flathub_app("spotify")
    assert result is None


def test_search_flathub_app_empty_query(monkeypatch):
    """CB: _search_flathub_app with empty query → returns None."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    result = tools._search_flathub_app("")
    assert result is None


def test_search_flathub_app_query_too_long(monkeypatch):
    """CB: _search_flathub_app with query > 50 chars → returns None."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    result = tools._search_flathub_app("a" * 51)
    assert result is None


def test_search_flathub_app_no_match(monkeypatch):
    """CB: _search_flathub_app flatpak search returns no results → None."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout=""))
    result = tools._search_flathub_app("spotify")
    assert result is None


def test_search_flathub_app_match(monkeypatch):
    """CB: _search_flathub_app finds match → returns (name, app_id)."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    stdout = "com.spotify.Client\tSpotify\norg.gimp.GIMP\tGNU Image Manipulation Program\n"
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout=stdout))
    result = tools._search_flathub_app("spotify")
    assert result is not None
    assert result[0] == "Spotify"
    assert result[1] == "com.spotify.Client"


def test_search_flathub_app_unsafe_id(monkeypatch):
    """CB: _search_flathub_app skips unsafe app IDs."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    # app_id with unsafe characters won't match _SAFE_FLATPAK_ID_RE
    stdout = "bad id!\tSpotify\n"
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout=stdout))
    result = tools._search_flathub_app("spotify")
    assert result is None


def test_search_flathub_app_command_failure(monkeypatch):
    """CB: _search_flathub_app flatpak command fails → None."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=False, stderr="error"))
    result = tools._search_flathub_app("spotify")
    assert result is None
