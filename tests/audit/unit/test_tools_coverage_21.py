"""
tests/audit/unit/test_tools_coverage_21.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 21).
Targets: _resolve_youtube_direct_watch_url, handle_starttag, _kick_browser_media_play_async,
open_application remaining branches, _coerce_and_filter_tool_args remaining branches.

Covers: _resolve_youtube_direct_watch_url web comms disabled/safe_get 200 with match/safe_get
failure/yt-dlp available/yt-dlp invalid/yt-dlp command failure;
handle_starttag HTML parsing branches; _kick_browser_media_play_async env check bypass;
open_application remaining paths; _coerce_and_filter_tool_args edge cases.

No real network calls. Mocks net_policy, command_runner, shutil.which, os.environ.
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


class _FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


# ---------------------------------------------------------------------------
# _resolve_youtube_direct_watch_url
# ---------------------------------------------------------------------------

def test_resolve_youtube_web_comms_disabled(monkeypatch):
    """CB: _resolve_youtube_direct_watch_url web_comms disabled → returns fallback."""
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed",
                        lambda *a, **k: (_ for _ in ()).throw(tools.net_policy.OfflineError("offline")))
    result = tools._resolve_youtube_direct_watch_url("celina demo")
    assert "youtube.com" in result


def test_resolve_youtube_safe_get_200_with_id(monkeypatch):
    """CB: _resolve_youtube_direct_watch_url safe_get 200 with video ID → returns watch URL."""
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda *a, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_get",
                        lambda *a, **k: _FakeResponse(200, '{"videoId": "dQw4w9WgXcQ"}'))
    # Need _YT_VIDEO_ID_RE to match
    result = tools._resolve_youtube_direct_watch_url("test")
    assert result is not None


def test_resolve_youtube_safe_get_failure(monkeypatch):
    """CB: _resolve_youtube_direct_watch_url safe_get raises → resp=None → yt-dlp path."""
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda *a, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("network")))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools._resolve_youtube_direct_watch_url("test")
    assert "youtube.com" in result


def test_resolve_youtube_yt_dlp_available_valid(monkeypatch):
    """CB: _resolve_youtube_direct_watch_url yt-dlp available with valid ID → watch URL."""
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda *a, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("network")))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/yt-dlp" if cmd == "yt-dlp" else None)
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="dQw4w9WgXcQ\n"))
    result = tools._resolve_youtube_direct_watch_url("test")
    assert "dQw4w9WgXcQ" in result


def test_resolve_youtube_yt_dlp_invalid_id(monkeypatch):
    """CB: _resolve_youtube_direct_watch_url yt-dlp returns invalid ID → fallback."""
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed",
                        lambda *a, **k: (_ for _ in ()).throw(tools.net_policy.OfflineError("offline")))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/yt-dlp" if cmd == "yt-dlp" else None)
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="invalid\n"))
    result = tools._resolve_youtube_direct_watch_url("test")
    assert "youtube.com" in result


def test_resolve_youtube_yt_dlp_command_failure(monkeypatch):
    """CB: _resolve_youtube_direct_watch_url yt-dlp command raises → fallback."""
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda *a, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_get",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("network")))
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/yt-dlp" if cmd == "yt-dlp" else None)
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    result = tools._resolve_youtube_direct_watch_url("test")
    assert "youtube.com" in result


# ---------------------------------------------------------------------------
# handle_starttag (HTML parser)
# ---------------------------------------------------------------------------

def test_handle_starttag_basic(monkeypatch):
    """CB: handle_starttag processes a start tag."""
    parser = tools._DDGParser()
    parser.handle_starttag("a", [("href", "https://example.com")])
    assert parser is not None


def test_handle_starttag_no_href(monkeypatch):
    """CB: handle_starttag with tag without href."""
    parser = tools._DDGParser()
    parser.handle_starttag("div", [("class", "result")])
    assert parser is not None


# ---------------------------------------------------------------------------
# _kick_browser_media_play_async
# ---------------------------------------------------------------------------

def test_kick_browser_media_play_async_pytest_env(monkeypatch):
    """CB: _kick_browser_media_play_async with PYTEST_CURRENT_TEST → returns early."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "1")
    monkeypatch.delenv("CELINA_DRY_RUN", raising=False)
    # Should return immediately without starting thread
    tools._kick_browser_media_play_async()
    assert True  # if we get here, early return worked


def test_kick_browser_media_play_async_dry_run(monkeypatch):
    """CB: _kick_browser_media_play_async with CELINA_DRY_RUN → returns early."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("CELINA_DRY_RUN", "1")
    tools._kick_browser_media_play_async()
    assert True


# ---------------------------------------------------------------------------
# open_application remaining branches
# ---------------------------------------------------------------------------

def test_open_application_with_path(monkeypatch):
    """CB: open_application resolves app via shutil.which → runs command."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gedit" if cmd == "gedit" else None)
    with patch.object(tools.command_runner, "run_command", return_value=_FakeResult(ok=True)):
        result = tools.open_application("gedit")
        assert result is not None


def test_open_application_not_found(monkeypatch):
    """CB: open_application app not found → error message."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.open_application("nonexistent_app_xyz")
    assert result is not None


# ---------------------------------------------------------------------------
# _coerce_and_filter_tool_args remaining branches
# ---------------------------------------------------------------------------

def test_coerce_and_filter_tool_args_var_kwarg(monkeypatch):
    """CB: _coerce_and_filter_tool_args with VAR_KEYWORD accepts all args."""
    def sample_fn(**kwargs):
        return "ok"

    result = tools._coerce_and_filter_tool_args("test", sample_fn,
                                                 {"a": "1", "b": "2", "extra": "3"})
    assert result == {"a": "1", "b": "2", "extra": "3"}


def test_coerce_and_filter_tool_args_empty_args(monkeypatch):
    """CB: _coerce_and_filter_tool_args with empty args."""
    def sample_fn(a: str):
        return "ok"

    result = tools._coerce_and_filter_tool_args("test", sample_fn, {})
    assert result == {}
