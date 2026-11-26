"""
tests/audit/unit/test_tools_coverage.py

Phase 7b — TASK 7: tools.py branch coverage tests.
Targets validation functions, path checks, circuit breaker, and other testable utilities.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


# ---------------------------------------------------------------------------
# _is_within_home
# ---------------------------------------------------------------------------


def test_is_within_home_true(tmp_path, monkeypatch):
    """CB: _is_within_home returns True for paths inside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    target = tmp_path / "sub" / "file.txt"
    assert tools._is_within_home(target) is True


def test_is_within_home_false_outside(tmp_path, monkeypatch):
    """CB: _is_within_home returns False for paths outside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    outside = Path("/tmp/outside.txt")
    assert tools._is_within_home(outside) is False


# ---------------------------------------------------------------------------
# _is_sensitive_path
# ---------------------------------------------------------------------------


def test_is_sensitive_path_dot_ssh(tmp_path, monkeypatch):
    """CB: _is_sensitive_path returns True for .ssh paths."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    assert tools._is_sensitive_path(tmp_path / ".ssh" / "id_rsa") is True


def test_is_sensitive_path_dot_aws(tmp_path, monkeypatch):
    """CB: _is_sensitive_path returns True for .aws paths."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    assert tools._is_sensitive_path(tmp_path / ".aws" / "credentials") is True


def test_is_sensitive_path_not_sensitive(tmp_path, monkeypatch):
    """CB: _is_sensitive_path returns False for normal paths."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    assert tools._is_sensitive_path(tmp_path / "documents" / "file.txt") is False


# ---------------------------------------------------------------------------
# _validate_target_for_write
# ---------------------------------------------------------------------------


def test_validate_target_for_write_ok(tmp_path, monkeypatch):
    """CB: _validate_target_for_write returns None for valid paths."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools._validate_target_for_write(tmp_path / "safe.txt")
    assert result is None


def test_validate_target_for_write_outside_home(tmp_path, monkeypatch):
    """CB: _validate_target_for_write returns error for outside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools._validate_target_for_write(Path("/etc/passwd"))
    assert result is not None


def test_validate_target_for_write_sensitive(tmp_path, monkeypatch):
    """CB: _validate_target_for_write returns error for sensitive paths."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools._validate_target_for_write(tmp_path / ".ssh" / "id_rsa")
    assert result is not None


# ---------------------------------------------------------------------------
# _open_tmp_nosymlink
# ---------------------------------------------------------------------------


def test_open_tmp_nosymlink(tmp_path, monkeypatch):
    """CB: _open_tmp_nosymlink creates temp file in parent dir."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    parent = tmp_path / "subdir"
    parent.mkdir()
    fd, tmppath = tools._open_tmp_nosymlink(parent, prefix=".test.")
    with os.fdopen(fd, "w") as f:
        f.write("test data")
    assert Path(tmppath).exists()
    assert Path(tmppath).read_text() == "test data"


# ---------------------------------------------------------------------------
# ToolCircuitBreaker
# ---------------------------------------------------------------------------


def test_tool_circuit_breaker_initial():
    """CB: ToolCircuitBreaker initial state allows calls."""
    cb = tools.ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
    assert cb.is_session_disabled("tool") is None


def test_tool_circuit_breaker_record_failure_opens():
    """CB: ToolCircuitBreaker opens after threshold failures."""
    cb = tools.ToolCircuitBreaker(threshold=2, cooldown_sec=60.0)
    cb.record_failure("tool", error_code="E1")
    assert cb.is_session_disabled("tool") is None  # different codes, no streak
    cb.record_failure("tool", error_code="E2")
    # cnt >= threshold → _open_until set, but _session_disabled not set (different codes)
    assert cb.is_session_disabled("tool") is None
    # But check_open should return remaining cooldown
    remaining = cb.check_open("tool")
    assert isinstance(remaining, float)


def test_tool_circuit_breaker_record_success_resets():
    """CB: ToolCircuitBreaker record_success resets failure count."""
    cb = tools.ToolCircuitBreaker(threshold=2, cooldown_sec=60.0)
    cb.record_failure("tool", error_code="E1")
    cb.record_success("tool")
    assert cb.is_session_disabled("tool") is None


def test_tool_circuit_breaker_same_code_streak_disables():
    """CB: ToolCircuitBreaker session-disables after same-code streak >= threshold."""
    cb = tools.ToolCircuitBreaker(threshold=2, cooldown_sec=60.0)
    cb.record_failure("tool", error_code="SAME")
    assert cb.is_session_disabled("tool") is None  # streak=1 < 2
    cb.record_failure("tool", error_code="SAME")
    disabled = cb.is_session_disabled("tool")
    assert disabled is not None  # streak=2 >= 2 → session disabled


def test_tool_circuit_breaker_check_open_returns_float():
    """CB: ToolCircuitBreaker.check_open returns remaining cooldown when open."""
    cb = tools.ToolCircuitBreaker(threshold=1, cooldown_sec=60.0)
    cb.record_failure("tool", error_code="E1")
    result = cb.check_open("tool")
    assert result is not None  # inf or float remaining time


def test_tool_circuit_breaker_check_open_none_when_enabled():
    """CB: ToolCircuitBreaker.check_open returns None when not disabled."""
    cb = tools.ToolCircuitBreaker(threshold=5, cooldown_sec=60.0)
    result = cb.check_open("tool")
    assert result is None


# ---------------------------------------------------------------------------
# _is_cross_device
# ---------------------------------------------------------------------------


def test_is_cross_device_same_device(tmp_path, monkeypatch):
    """CB: _is_cross_device returns False for same device."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.touch()
    f2.touch()
    assert tools._is_cross_device(f1, f2) is False


def test_is_cross_device_nonexistent(tmp_path, monkeypatch):
    """CB: _is_cross_device handles nonexistent paths."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    # Should not raise for nonexistent paths
    result = tools._is_cross_device(tmp_path / "nonexistent1", tmp_path / "nonexistent2")
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _rg_sensitive_globs
# ---------------------------------------------------------------------------


def test_rg_sensitive_globs():
    """CB: _rg_sensitive_globs returns list of glob patterns."""
    result = tools._rg_sensitive_globs()
    assert isinstance(result, list)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# _filter_rg_output
# ---------------------------------------------------------------------------


def test_filter_rg_output_removes_sensitive(tmp_path, monkeypatch):
    """CB: _filter_rg_output removes lines with sensitive paths (rg format: path:line:content)."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    sensitive = tmp_path / ".ssh" / "id_rsa"
    safe = tmp_path / "docs" / "safe.txt"
    output = f"{sensitive}:1:some content\n{safe}:5:other content"
    result = tools._filter_rg_output(output)
    assert str(sensitive) not in result
    assert "other content" in result


def test_filter_rg_output_no_colon():
    """CB: _filter_rg_output keeps lines without colons."""
    result = tools._filter_rg_output("just some text")
    assert "just some text" in result


def test_filter_rg_output_empty():
    """CB: _filter_rg_output handles empty input."""
    result = tools._filter_rg_output("")
    assert result == ""


def test_filter_rg_output_no_colon():
    """CB: _filter_rg_output keeps lines without colons."""
    result = tools._filter_rg_output("just some text")
    assert "just some text" in result


# ---------------------------------------------------------------------------
# _is_public_http_url
# ---------------------------------------------------------------------------


def test_is_public_http_url_https_with_resolver(monkeypatch):
    """CB: _is_public_http_url returns True for https:// public URL with valid DNS."""
    import celina_pkg.net_policy as net_policy_mod

    monkeypatch.setattr(net_policy_mod, "default_dns_resolver", lambda host, port: ["93.184.216.34"])
    assert tools._is_public_http_url("https://example.com/") is True


def test_is_public_http_url_http_false():
    """CB: _is_public_http_url returns False for http:// (not https)."""
    assert tools._is_public_http_url("http://example.com/") is False


def test_is_public_http_url_localhost():
    """CB: _is_public_http_url returns False for localhost."""
    assert tools._is_public_http_url("https://localhost/") is False


def test_is_public_http_url_private_ip():
    """CB: _is_public_http_url returns False for private IP."""
    assert tools._is_public_http_url("https://10.0.0.1/") is False


# ---------------------------------------------------------------------------
# get_system_stats
# ---------------------------------------------------------------------------


def test_get_system_stats():
    """CB: get_system_stats returns a string."""
    result = tools.get_system_stats()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# set_volume
# ---------------------------------------------------------------------------


def test_set_volume(monkeypatch):
    """CB: set_volume works with mocked amixer."""
    monkeypatch.setattr(tools.subprocess, "run", MagicMock(returncode=0))
    result = tools.set_volume(50)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# set_brightness
# ---------------------------------------------------------------------------


def test_set_brightness(monkeypatch):
    """CB: set_brightness works with mocked brightnessctl."""
    monkeypatch.setattr(tools.subprocess, "run", MagicMock(returncode=0))
    result = tools.set_brightness(50)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# control_media
# ---------------------------------------------------------------------------


def test_control_media_play(monkeypatch):
    """CB: control_media play works with mocked playerctl."""
    monkeypatch.setattr(tools.subprocess, "run", MagicMock(returncode=0, stdout="playing"))
    result = tools.control_media("play")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# get_media_title
# ---------------------------------------------------------------------------


def test_get_media_title(monkeypatch):
    """CB: get_media_title works with mocked playerctl."""
    monkeypatch.setattr(tools.subprocess, "run", MagicMock(returncode=0, stdout="Song Title"))
    result = tools.get_media_title()
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# run_capture
# ---------------------------------------------------------------------------

def test_run_capture_utility_not_found():
    """CB: run_capture returns error when utility not found."""
    result = tools.run_capture(["nonexistent_utility_xyz123"])
    assert "not found" in result


def test_run_capture_success(monkeypatch):
    """CB: run_capture returns stdout on success."""
    mock_res = MagicMock()
    mock_res.stdout = "output text"
    mock_res.stderr = ""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: mock_res)
    result = tools.run_capture(["echo", "hi"])
    assert "output text" in result


def test_run_capture_timeout(monkeypatch):
    """CB: run_capture returns timeout error."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(tools.subprocess.TimeoutExpired("cmd", 5)))
    result = tools.run_capture(["sleep", "100"])
    assert "timed out" in result


def test_run_capture_exception(monkeypatch):
    """CB: run_capture returns error on unexpected exception."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(tools.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    result = tools.run_capture(["somecmd"])
    assert "Error: boom" in result


# ---------------------------------------------------------------------------
# launch_detached
# ---------------------------------------------------------------------------

def test_launch_detached_empty_cmd():
    """CB: launch_detached returns False for empty command."""
    assert tools.launch_detached([]) is False


def test_launch_detached_utility_not_found():
    """CB: launch_detached returns False when utility not found."""
    assert tools.launch_detached(["nonexistent_utility_xyz123"]) is False


def test_launch_detached_success(monkeypatch):
    """CB: launch_detached returns True on success."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(tools.subprocess, "Popen", MagicMock())
    assert tools.launch_detached(["somecmd"]) is True


def test_launch_detached_exception(monkeypatch):
    """CB: launch_detached returns False on exception."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    monkeypatch.setattr(tools.subprocess, "Popen", MagicMock(side_effect=OSError("boom")))
    assert tools.launch_detached(["somecmd"]) is False


# ---------------------------------------------------------------------------
# send_notification
# ---------------------------------------------------------------------------

def test_send_notification_no_notify_send(monkeypatch):
    """CB: send_notification does nothing when notify-send not available."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    called = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: called.append(cmd) or True)
    tools.send_notification("title", "body")
    assert called == []


def test_send_notification_with_notify_send(monkeypatch):
    """CB: send_notification calls launch_detached when notify-send available."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/notify-send" if cmd == "notify-send" else None)
    called = []
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: called.append(cmd) or True)
    tools.send_notification("title", "body")
    assert len(called) == 1
    assert called[0][0] == "notify-send"


# ---------------------------------------------------------------------------
# AppNapController: _is_protected
# ---------------------------------------------------------------------------

def test_appnap_is_protected_empty():
    """CB: _is_protected returns True for empty name."""
    ctrl = tools.AppNapController()
    assert ctrl._is_protected("") is True
    assert ctrl._is_protected("   ") is True


def test_appnap_is_protected_excluded():
    """CB: _is_protected returns True for excluded processes."""
    ctrl = tools.AppNapController()
    assert ctrl._is_protected("systemd") is True
    assert ctrl._is_protected("gnome-shell") is True


def test_appnap_is_protected_protected_set():
    """CB: _is_protected returns True for _PROTECTED_PROCESSES."""
    ctrl = tools.AppNapController()
    assert ctrl._is_protected("python3") is True
    assert ctrl._is_protected("sshd") is True


def test_appnap_is_protected_token_match():
    """CB: _is_protected returns True for token matches."""
    ctrl = tools.AppNapController()
    assert ctrl._is_protected("my_celina_helper") is True  # contains "celina"
    assert ctrl._is_protected("my_terminal_app") is True  # contains "terminal"


def test_appnap_is_protected_not_protected():
    """CB: _is_protected returns False for non-protected processes."""
    ctrl = tools.AppNapController()
    assert ctrl._is_protected("firefox") is False
    assert ctrl._is_protected("gedit") is False


# ---------------------------------------------------------------------------
# AppNapController: nap - protected process
# ---------------------------------------------------------------------------

def test_appnap_nap_protected(monkeypatch):
    """CB: nap() blocks protected processes."""
    ctrl = tools.AppNapController()
    result = ctrl.nap("systemd")
    assert "Blocked" in result


def test_appnap_nap_foreground_app(monkeypatch):
    """CB: nap() blocks foreground app."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(tools.automation, "get_active_window_info", lambda: {"app": "firefox"})
    result = ctrl.nap("firefox")
    assert "Blocked" in result and "foreground" in result


# ---------------------------------------------------------------------------
# AppNapController: nap - no targets found
# ---------------------------------------------------------------------------

def test_appnap_nap_no_targets(monkeypatch):
    """CB: nap() returns 'No running user process' when no PIDs found."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(ctrl, "_is_protected", lambda name: False)
    monkeypatch.setattr(ctrl, "_find_exact_user_pids", lambda name: [])
    result = ctrl.nap("nonexistent_app")
    assert "No running user process" in result


# ---------------------------------------------------------------------------
# AppNapController: _find_exact_user_pids - no /proc
# ---------------------------------------------------------------------------

def test_appnap_find_pids_no_proc(monkeypatch):
    """CB: _find_exact_user_pids returns [] when /proc doesn't exist."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(tools.Path, "exists", lambda self: False)  # /proc doesn't exist
    result = ctrl._find_exact_user_pids("firefox")
    assert result == []


# ---------------------------------------------------------------------------
# AppNapController: _proc_info - OSError
# ---------------------------------------------------------------------------

def test_appnap_proc_info_oserror(monkeypatch):
    """CB: _proc_info returns None on OSError."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(tools.Path, "read_text", lambda self, **k: (_ for _ in ()).throw(OSError()))
    result = ctrl._proc_info(1234)
    assert result is None


# ---------------------------------------------------------------------------
# AppNapController: _proc_info - malformed stat (no parens)
# ---------------------------------------------------------------------------

def test_appnap_proc_info_malformed(monkeypatch, tmp_path):
    """CB: _proc_info returns None when stat has no parens."""
    ctrl = tools.AppNapController()
    monkeypatch.setattr(tools.Path, "read_text", lambda self, **k: "bad stat no parens")
    result = ctrl._proc_info(1234)
    assert result is None


# ---------------------------------------------------------------------------
# AppNapController: toggle_app_nap - restore_all
# ---------------------------------------------------------------------------

def test_toggle_app_nap_restore_all(monkeypatch):
    """CB: toggle_app_nap with restore_all calls controller.restore_all."""
    called = []
    monkeypatch.setattr(tools.app_nap_controller, "restore_all", lambda: called.append(1) or [1, 2])
    result = tools.toggle_app_nap("anything", "restore_all")
    assert "Restored" in result
    assert called == [1]


def test_toggle_app_nap_nap_action(monkeypatch):
    """CB: toggle_app_nap with nap action calls controller.nap."""
    called = []
    monkeypatch.setattr(tools.app_nap_controller, "nap", lambda name: called.append(name) or "napped")
    result = tools.toggle_app_nap("firefox", "nap")
    assert result == "napped"
    assert called == ["firefox"]


def test_toggle_app_nap_wake_action(monkeypatch):
    """CB: toggle_app_nap with wake action calls controller.wake."""
    called = []
    monkeypatch.setattr(tools.app_nap_controller, "wake", lambda name: called.append(name) or "woken")
    result = tools.toggle_app_nap("firefox", "wake")
    assert result == "woken"
    assert called == ["firefox"]


# ---------------------------------------------------------------------------
# control_connectivity: invalid action
# ---------------------------------------------------------------------------

def test_control_connectivity_invalid_action():
    """CB: control_connectivity rejects invalid actions."""
    result = tools.control_connectivity("wifi", "invalid")
    assert "Action must be" in result


# ---------------------------------------------------------------------------
# control_connectivity: invalid target
# ---------------------------------------------------------------------------

def test_control_connectivity_invalid_target():
    """CB: control_connectivity rejects invalid targets."""
    result = tools.control_connectivity("storage", "on")
    assert "Target must be" in result


# ---------------------------------------------------------------------------
# control_connectivity: wifi nmcli not installed
# ---------------------------------------------------------------------------

def test_control_connectivity_wifi_no_nmcli(monkeypatch):
    """CB: control_connectivity wifi returns error when nmcli not installed."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.control_connectivity("wifi", "on")
    assert "nmcli not installed" in result


# ---------------------------------------------------------------------------
# control_connectivity: bluetooth bluetoothctl not installed
# ---------------------------------------------------------------------------

def test_control_connectivity_bluetooth_no_btctl(monkeypatch):
    """CB: control_connectivity bluetooth returns error when bluetoothctl not installed."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.control_connectivity("bluetooth", "on")
    assert "bluetoothctl not installed" in result


# ---------------------------------------------------------------------------
# open_url_safe
# ---------------------------------------------------------------------------

def test_open_url_safe_empty():
    """CB: open_url_safe rejects empty URL."""
    result = tools.open_url_safe("")
    assert "Empty URL" in result


def test_open_url_safe_control_chars():
    """CB: open_url_safe blocks control characters."""
    result = tools.open_url_safe("http://example.com\x00evil")
    assert "Blocked" in result


def test_open_url_safe_unsafe_scheme():
    """CB: open_url_safe blocks non-http(s) schemes."""
    result = tools.open_url_safe("ftp://example.com")
    assert "Blocked" in result


def test_open_url_safe_bare_domain(monkeypatch):
    """CB: open_url_safe prepends https:// for bare domains."""
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)
    result = tools.open_url_safe("example.com")
    assert "Opened" in result


def test_open_url_safe_invalid_netloc(monkeypatch):
    """CB: open_url_safe blocks URL without dot in netloc."""
    result = tools.open_url_safe("http://localhost")
    assert "Blocked" in result


def test_open_url_safe_xdg_open_success(monkeypatch):
    """CB: open_url_safe succeeds via xdg-open."""
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)
    result = tools.open_url_safe("https://example.com")
    assert "Opened" in result


def test_open_url_safe_xdg_open_fails_browser(monkeypatch):
    """CB: open_url_safe falls back to browser when xdg-open fails."""
    calls = []
    def fake_launch(cmd):
        calls.append(cmd)
        return cmd[0] == "firefox"  # only firefox succeeds
    monkeypatch.setattr(tools, "launch_detached", fake_launch)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/firefox" if cmd == "firefox" else None)
    result = tools.open_url_safe("https://example.com")
    assert "Opened" in result


def test_open_url_safe_no_browser(monkeypatch):
    """CB: open_url_safe reports failure when no browser available."""
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: False)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.open_url_safe("https://example.com")
    assert "Failed to open" in result


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


def test_open_application_not_found():
    """CB: open_application reports not found for unknown apps."""
    result = tools.open_application("nonexistent_app_xyz123")
    assert "not found" in result


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
# list_files
# ---------------------------------------------------------------------------

def test_list_files_outside_home(tmp_path, monkeypatch):
    """CB: list_files blocks paths outside home."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.list_files("/etc")
    assert "limited to directories inside your home" in result


def test_list_files_sensitive_path(tmp_path, monkeypatch):
    """CB: list_files blocks sensitive directories."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    result = tools.list_files(str(tmp_path / ".ssh"))
    assert "sensitive" in result


def test_list_files_not_a_dir(tmp_path, monkeypatch):
    """CB: list_files returns error for non-directory."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    f = tmp_path / "afile.txt"
    f.write_text("data")
    result = tools.list_files(str(f))
    assert "Not a directory" in result


def test_list_files_no_entries(tmp_path, monkeypatch):
    """CB: list_files returns 'No entries' for empty dir."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    d = tmp_path / "emptydir"
    d.mkdir()
    result = tools.list_files(str(d))
    assert "No entries" in result


def test_list_files_with_entries(tmp_path, monkeypatch):
    """CB: list_files lists directory entries."""
    monkeypatch.setattr(tools.config, "HOME", tmp_path)
    d = tmp_path / "mydir"
    d.mkdir()
    (d / "afile.txt").write_text("a")
    (d / "subdir").mkdir()
    result = tools.list_files(str(d))
    assert "afile.txt" in result
    assert "subdir/" in result


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
# get_clipboard / get_highlighted_text / set_clipboard
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
# _trigger_matches
# ---------------------------------------------------------------------------

def test_trigger_matches_empty():
    """CB: _trigger_matches returns False for empty trigger."""
    assert tools._trigger_matches("", "hello") is False


def test_trigger_matches_single_word_boundary():
    """CB: _trigger_matches uses word boundary for single alnum word."""
    assert tools._trigger_matches("read", "proofreading") is False
    assert tools._trigger_matches("read", "I will read now") is True


def test_trigger_matches_multi_word():
    """CB: _trigger_matches uses substring for multi-word triggers."""
    assert tools._trigger_matches("open file", "please open file now") is True


# ---------------------------------------------------------------------------
# _domain_for_tool
# ---------------------------------------------------------------------------

def test_domain_for_tool_known_domain(monkeypatch):
    """CB: _domain_for_tool returns domain for known tool."""
    monkeypatch.setattr(tools.config, "TOOL_DOMAINS", {"web": {"tools": ["fetch_url", "fetch_webpage"]}})
    assert tools._domain_for_tool("fetch_url") == "web"


def test_domain_for_tool_unknown_domain():
    """CB: _domain_for_tool defaults to os_hardware for unknown tools."""
    assert tools._domain_for_tool("nonexistent_tool") == "os_hardware"


# ---------------------------------------------------------------------------
# _search_flathub_app
# ---------------------------------------------------------------------------

def test_search_flathub_app_no_flatpak(monkeypatch):
    """CB: _search_flathub_app returns None when flatpak not installed."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    assert tools._search_flathub_app("spotify") is None


def test_search_flathub_app_empty_query(monkeypatch):
    """CB: _search_flathub_app returns None for empty query."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    assert tools._search_flathub_app("") is None


def test_search_flathub_app_query_too_long(monkeypatch):
    """CB: _search_flathub_app returns None for query > 50 chars."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    assert tools._search_flathub_app("x" * 51) is None


def test_search_flathub_app_no_results(monkeypatch):
    """CB: _search_flathub_app returns None when flatpak search returns nothing."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: type("R", (), {"ok": True, "stdout": "", "stderr": "", "error": ""})())
    assert tools._search_flathub_app("spotify") is None


def test_search_flathub_app_found(monkeypatch):
    """CB: _search_flathub_app returns (name, app_id) for matching app."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/flatpak")
    mock_res = type("R", (), {"ok": True, "stdout": "org.spotify.Client\tSpotify\n", "stderr": "", "error": ""})()
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: mock_res)
    result = tools._search_flathub_app("spotify")
    assert result == ("Spotify", "org.spotify.Client")


# ---------------------------------------------------------------------------
# _check_apt_package
# ---------------------------------------------------------------------------

def test_check_apt_package_no_apt_cache(monkeypatch):
    """CB: _check_apt_package returns None when apt-cache not installed."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    assert tools._check_apt_package("ffmpeg") is None


def test_check_apt_package_invalid_name(monkeypatch):
    """CB: _check_apt_package returns None for invalid package name."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/apt-cache")
    assert tools._check_apt_package("invalid package name!!") is None


def test_check_apt_package_found(monkeypatch):
    """CB: _check_apt_package returns package name when found."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/apt-cache")
    mock_res = type("R", (), {"ok": True, "stdout": "Package: ffmpeg\n", "stderr": "", "error": ""})()
    monkeypatch.setattr(tools.command_runner, "run_command", lambda *a, **k: mock_res)
    result = tools._check_apt_package("ffmpeg")
    assert result == "ffmpeg"


