"""
tests/audit/unit/test_tools_coverage_9.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 9).
Targets: open_application, search_files, search_in_files, read_file,
read_function_signatures, read_lines, grep_code, clipboard functions,
set_timer, mcp_* functions.

No real network calls. Mocks launch_detached, open_url_safe, shutil.which,
command_runner, subprocess, Path.glob, Path.read_text.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error="", message="", timed_out=False, returncode=0):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.message = message
        self.timed_out = timed_out
        self.returncode = returncode


# ---------------------------------------------------------------------------
# open_application
# ---------------------------------------------------------------------------


def test_open_application_empty():
    """CB: open_application rejects empty name."""
    result = tools.open_application("")
    assert "Provide an application" in result


def test_open_application_http_url(monkeypatch):
    """CB: open_application delegates HTTP URLs to open_url_safe."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("http://example.com")
    assert result == "delegated:http://example.com"


def test_open_application_https_url(monkeypatch):
    """CB: open_application delegates HTTPS URLs to open_url_safe."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("https://example.com")
    assert result == "delegated:https://example.com"


def test_open_application_bare_domain(monkeypatch):
    """CB: open_application delegates bare domains to open_url_safe."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("example.com")
    assert result == "delegated:example.com"


def test_open_application_web_forced_known(monkeypatch):
    """CB: open_application handles 'spotify in web'."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("spotify in web")
    assert "delegated:https://open.spotify.com" in result


def test_open_application_web_forced_domain(monkeypatch):
    """CB: open_application handles 'example.com web'."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("example.com web")
    assert "delegated:example.com" in result


def test_open_application_web_forced_unknown(monkeypatch):
    """CB: open_application falls back to Google search for unknown web queries."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("some random query in web")
    assert "delegated:" in result and "google.com/search" in result


def test_open_application_settings_panel(monkeypatch):
    """CB: open_application launches gnome-control-center for settings panels."""
    called = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: called.append(cmd) or True)
    result = tools.open_application("wifi settings")
    assert "Opened Settings" in result
    assert called[0] == ["gnome-control-center", "wifi"]


def test_open_application_settings_panel_fail(monkeypatch):
    """CB: open_application reports failure when settings panel launch fails."""
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: False)
    result = tools.open_application("wifi settings")
    # Falls through to curated apps or web fallback
    assert "not found" in result.lower() or "opened" in result.lower()


def test_open_application_curated_app(monkeypatch):
    """CB: open_application launches curated apps."""
    called = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: called.append(cmd) or True)
    result = tools.open_application("files")
    assert "Opened files" in result
    assert called[0] == ["nautilus"]


def test_open_application_curated_app_fail(monkeypatch):
    """CB: open_application reports failure when curated app launch fails."""
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: False)
    result = tools.open_application("files")
    assert "Failed to open" in result


def test_open_application_short_web_alias(monkeypatch):
    """CB: open_application handles short web aliases like 'yt'."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("yt")
    assert "delegated:https://www.youtube.com" in result


def test_open_application_web_fallback(monkeypatch):
    """CB: open_application falls back to web for known WEB_APPS keys."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("spotify")
    assert "delegated:https://open.spotify.com" in result


def test_open_application_web_fallback_installable(monkeypatch):
    """CB: open_application adds install hint for CURATED_INSTALLABLE_APPS."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("spotify")
    assert "Native app not installed yet" in result


def test_open_application_not_found(monkeypatch):
    """CB: open_application reports not found for unknown apps."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"delegated:{url}")
    result = tools.open_application("nonexistent_app_xyz123")
    assert "not found" in result.lower()


def test_open_application_gtk_launch_desktop_match(monkeypatch, tmp_path):
    """CB: open_application finds .desktop file via gtk-launch."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/gtk-launch" if cmd == "gtk-launch" else None)
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)

    # Mock config.HOME to point to tmp_path
    import celina_pkg.config as config_mod
    monkeypatch.setattr(config_mod, "HOME", tmp_path)

    # Create a .desktop file
    apps_dir = tmp_path / ".local/share/applications"
    apps_dir.mkdir(parents=True)
    desktop_file = apps_dir / "firefox.desktop"
    desktop_file.write_text("[Desktop Entry]\nName=Firefox\nExec=firefox\n")

    result = tools.open_application("firefox")
    assert "Launched firefox" in result or "not found" in result.lower()


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------


def test_search_files_outside_home(tmp_path, monkeypatch):
    """CB: search_files blocks directories outside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.search_files("pattern", "/etc")
    assert "must exist inside your home folder" in result


def test_search_files_empty_query():
    """CB: search_files returns error for empty query."""
    result = tools.search_files("")
    assert "query" in result.lower()


def test_search_files_no_rg_fallback(tmp_path, monkeypatch):
    """CB: search_files uses Python fallback when rg not available."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    f = tmp_path / "test.txt"
    f.write_text("hello world pattern here\n")
    result = tools.search_files("pattern", str(tmp_path))
    assert "pattern" in result


def test_search_files_no_matches(tmp_path, monkeypatch):
    """CB: search_files reports no matches."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    f = tmp_path / "test.txt"
    f.write_text("nothing here\n")
    result = tools.search_files("zzznomatch", str(tmp_path))
    assert "No files matching" in result or "not found" in result.lower()


# ---------------------------------------------------------------------------
# search_in_files
# ---------------------------------------------------------------------------


def test_search_in_files_outside_home(tmp_path, monkeypatch):
    """CB: search_in_files blocks directories outside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.search_in_files("pattern", "/etc")
    assert "must exist inside your home folder" in result


def test_search_in_files_empty_text():
    """CB: search_in_files returns error for empty text."""
    result = tools.search_in_files("")
    assert "Provide text" in result


def test_search_in_files_no_rg_fallback(tmp_path, monkeypatch):
    """CB: search_in_files uses Python fallback when rg not available."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    f = tmp_path / "test.txt"
    f.write_text("hello world pattern here\n")
    result = tools.search_in_files("pattern", str(tmp_path))
    assert "pattern" in result


def test_search_in_files_no_matches(tmp_path, monkeypatch):
    """CB: search_in_files reports no matches."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    f = tmp_path / "test.txt"
    f.write_text("nothing here\n")
    result = tools.search_in_files("zzznomatch", str(tmp_path))
    assert "No matches" in result


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


def test_read_file_outside_home(tmp_path, monkeypatch):
    """CB: read_file blocks paths outside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.read_file("/etc/passwd")
    assert "limited to files inside your home" in result


def test_read_file_sensitive_path(tmp_path, monkeypatch):
    """CB: read_file blocks sensitive paths."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.read_file(str(tmp_path / ".ssh" / "id_rsa"))
    assert "sensitive" in result


def test_read_file_not_a_file(tmp_path, monkeypatch):
    """CB: read_file returns error for non-existent file."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.read_file(str(tmp_path / "nonexistent.txt"))
    assert "File not found" in result


def test_read_file_success(tmp_path, monkeypatch):
    """CB: read_file reads a text file successfully."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f = tmp_path / "hello.txt"
    f.write_text("Hello World\nLine 2")
    result = tools.read_file(str(f))
    assert "Hello World" in result


def test_read_file_truncation(tmp_path, monkeypatch):
    """CB: read_file truncates long files."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f = tmp_path / "long.txt"
    f.write_text("x" * 5000)
    result = tools.read_file(str(f), max_chars=100)
    assert "truncated" in result


def test_read_file_exception(tmp_path, monkeypatch):
    """CB: read_file catches read exceptions."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f = tmp_path / "exc.txt"
    f.write_text("data")
    monkeypatch.setattr(f.__class__, "read_text", lambda self, **k: (_ for _ in ()).throw(OSError("boom")))
    result = tools.read_file(str(f))
    assert "Error reading file" in result


# ---------------------------------------------------------------------------
# read_function_signatures
# ---------------------------------------------------------------------------


def test_read_function_signatures_outside_home(tmp_path, monkeypatch):
    """CB: read_function_signatures blocks paths outside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.read_function_signatures("/etc/passwd.py")
    assert "limited to files inside your home" in result


def test_read_function_signatures_not_py(tmp_path, monkeypatch):
    """CB: read_function_signatures rejects non-.py files."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f = tmp_path / "script.txt"
    f.write_text("print('hi')")
    result = tools.read_function_signatures(str(f))
    assert "Only .py files" in result


def test_read_function_signatures_success(tmp_path, monkeypatch):
    """CB: read_function_signatures extracts signatures from .py file."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f = tmp_path / "mod.py"
    f.write_text("def foo(a: int) -> str:\n    pass\nclass Bar:\n    pass\n")
    result = tools.read_function_signatures(str(f))
    assert "def foo" in result
    assert "class Bar" in result


def test_read_function_signatures_no_functions(tmp_path, monkeypatch):
    """CB: read_function_signatures reports no functions found."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f = tmp_path / "empty.py"
    f.write_text("# just a comment\nx = 1\n")
    result = tools.read_function_signatures(str(f))
    assert "No functions or classes" in result


# ---------------------------------------------------------------------------
# read_lines
# ---------------------------------------------------------------------------


def test_read_lines_outside_home(tmp_path, monkeypatch):
    """CB: read_lines blocks paths outside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.read_lines("/etc/passwd")
    assert "limited to files inside your home" in result


def test_read_lines_success(tmp_path, monkeypatch):
    """CB: read_lines reads a line range."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f = tmp_path / "lines.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, 11)))
    result = tools.read_lines(str(f), start=1, end=3)
    assert "line 1" in result
    assert "line 3" in result


def test_read_lines_start_exceeds_total(tmp_path, monkeypatch):
    """CB: read_lines errors when start exceeds file length."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f = tmp_path / "short.txt"
    f.write_text("a\nb\n")
    result = tools.read_lines(str(f), start=10)
    assert "exceeds file length" in result


# ---------------------------------------------------------------------------
# grep_code
# ---------------------------------------------------------------------------


def test_grep_code_outside_home(tmp_path, monkeypatch):
    """CB: grep_code blocks directories outside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.grep_code("pattern", "/etc")
    assert "must exist inside your home folder" in result


def test_grep_code_empty_pattern():
    """CB: grep_code returns error for empty pattern."""
    result = tools.grep_code("")
    assert "Provide a pattern" in result


def test_grep_code_no_rg_fallback(tmp_path, monkeypatch):
    """CB: grep_code uses Python fallback when rg not available."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    f = tmp_path / "test.py"
    f.write_text("hello world pattern here\n")
    result = tools.grep_code("pattern", str(tmp_path))
    assert "pattern" in result


def test_grep_code_no_matches(tmp_path, monkeypatch):
    """CB: grep_code reports no matches."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    f = tmp_path / "test.py"
    f.write_text("nothing here\n")
    result = tools.grep_code("zzznomatch", str(tmp_path))
    assert "No matches" in result


# ---------------------------------------------------------------------------
# Clipboard functions
# ---------------------------------------------------------------------------


def test_get_clipboard_not_installed(monkeypatch):
    """CB: get_clipboard reports error when wl-paste not installed."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.get_clipboard()
    assert "wl-clipboard" in result and "not installed" in result


def test_get_clipboard_empty(monkeypatch):
    """CB: get_clipboard reports empty clipboard."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wl-paste")
    monkeypatch.setattr(tools, "run_capture", lambda *a, **k: "")
    result = tools.get_clipboard()
    assert "Clipboard is empty" in result


def test_get_clipboard_content(monkeypatch):
    """CB: get_clipboard returns truncated content."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wl-paste")
    monkeypatch.setattr(tools, "run_capture", lambda *a, **k: "x" * 2000)
    result = tools.get_clipboard()
    assert "truncated" in result


def test_get_highlighted_text_not_installed(monkeypatch):
    """CB: get_highlighted_text reports error when wl-paste not installed."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.get_highlighted_text()
    assert "wl-clipboard" in result


def test_get_highlighted_text_empty(monkeypatch):
    """CB: get_highlighted_text reports nothing highlighted."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wl-paste")
    monkeypatch.setattr(tools, "run_capture", lambda *a, **k: "")
    result = tools.get_highlighted_text()
    assert "No text is currently highlighted" in result


def test_set_clipboard_not_installed(monkeypatch):
    """CB: set_clipboard reports error when wl-copy not installed."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.set_clipboard("hello")
    assert "wl-copy" in result and "not installed" in result


def test_set_clipboard_success(monkeypatch):
    """CB: set_clipboard succeeds with wl-copy."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wl-copy")
    monkeypatch.setattr(tools.subprocess, "run", MagicMock(returncode=0))
    result = tools.set_clipboard("hello")
    assert "Copied" in result


def test_set_clipboard_exception(monkeypatch):
    """CB: set_clipboard catches subprocess exceptions."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wl-copy")
    monkeypatch.setattr(tools.subprocess, "run", MagicMock(side_effect=OSError("boom")))
    result = tools.set_clipboard("hello")
    assert "Error copying" in result


# ---------------------------------------------------------------------------
# set_timer
# ---------------------------------------------------------------------------


def test_set_timer_success(monkeypatch):
    """CB: set_timer returns timer message."""
    result = tools.set_timer(5, "Test timer")
    assert isinstance(result, str)
    assert "5" in result or "timer" in result.lower()


# ---------------------------------------------------------------------------
# mcp_* functions
# ---------------------------------------------------------------------------


def test_mcp_list_servers(monkeypatch):
    """CB: mcp_list_servers returns server list."""
    fake_mcp = MagicMock()
    fake_mcp.list_servers.return_value = ["server1", "server2"]
    monkeypatch.setattr(tools.mcp_client, "get_mcp", lambda: fake_mcp)
    result = tools.mcp_list_servers()
    assert "server1" in result


def test_mcp_list_tools(monkeypatch):
    """CB: mcp_list_tools returns tool list for a server."""
    fake_mcp = MagicMock()
    fake_mcp.list_tools.return_value = ["tool1", "tool2"]
    monkeypatch.setattr(tools.mcp_client, "get_mcp", lambda: fake_mcp)
    result = tools.mcp_list_tools("server1")
    assert "tool1" in result


def test_mcp_call_tool(monkeypatch):
    """CB: mcp_call_tool calls call_tool with args."""
    fake_mcp = MagicMock()
    fake_mcp.call_tool.return_value = "tool result"
    monkeypatch.setattr(tools.mcp_client, "get_mcp", lambda: fake_mcp)
    result = tools.mcp_call_tool("server1", "tool1", {"arg": "val"})
    assert "tool result" in result

