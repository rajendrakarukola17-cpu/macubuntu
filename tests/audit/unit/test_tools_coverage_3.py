"""
tests/audit/unit/test_tools_coverage_3.py

Phase 7d — TASK 1a: tools.py branch coverage tests (round 3).
Targets control_connectivity, web_search, run_shell_command, close_application.

No real network calls. Mocks command_runner, shutil.which, _celina_hardware,
net_policy, cache, and approval functions.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


def _always_approve(action, params, cb=None, title="", target=""):
    class Token:
        pass
    return Token(), None


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error="", message="", timed_out=False, returncode=0):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.message = message
        self.timed_out = timed_out
        self.returncode = returncode


class _FakeNetState:
    def __init__(self, status, wifi_enabled=False, active_ssid=None, powered=False, controller="default", detail=None):
        self.status = status
        self.wifi_enabled = wifi_enabled
        self.active_ssid = active_ssid
        self.powered = powered
        self.controller = controller
        self.detail = detail


class _FakeCapStatus:
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"


class _FakeNetwork:
    def __init__(self, state=None):
        self._state = state or _FakeNetState(_FakeCapStatus.HEALTHY, True, "MySSID")

    def get_state(self):
        return self._state

    def set_wifi(self, on):
        return _FakeResult(ok=True, message=f"Wi-Fi {'on' if on else 'off'}")


class _FakeBluetooth:
    def __init__(self, state=None):
        self._state = state or _FakeNetState(_FakeCapStatus.HEALTHY, powered=True, controller="hci0")

    def get_state(self):
        return self._state

    def set_power(self, on):
        return _FakeResult(ok=True, message=f"Bluetooth power {'on' if on else 'off'}")


class _FakeHardware:
    def __init__(self, network=None, bluetooth=None):
        self.network = network or _FakeNetwork()
        self.bluetooth = bluetooth or _FakeBluetooth()
        self.CapabilityStatus = _FakeCapStatus

    def get_hardware(self):
        return self


# ---------------------------------------------------------------------------
# control_connectivity
# ---------------------------------------------------------------------------


def test_control_connectivity_invalid_action():
    """CB: control_connectivity rejects invalid action."""
    result = tools.control_connectivity("wifi", "bogus")
    assert "Action must be" in result


def test_control_connectivity_invalid_target():
    """CB: control_connectivity rejects invalid target."""
    result = tools.control_connectivity("storage", "on")
    assert "Target must be" in result


def test_control_connectivity_wifi_hardware_status_healthy(monkeypatch):
    """CB: control_connectivity wifi hardware status healthy."""
    monkeypatch.setattr(tools, "_celina_hardware", _FakeHardware())
    result = tools.control_connectivity("wifi", "status")
    assert "Wi-Fi:" in result and "ON" in result


def test_control_connectivity_wifi_hardware_status_unhealthy(monkeypatch):
    """CB: control_connectivity wifi hardware status unhealthy."""
    hw = _FakeHardware(network=_FakeNetwork(state=_FakeNetState(_FakeCapStatus.UNHEALTHY, detail="NM down")))
    monkeypatch.setattr(tools, "_celina_hardware", hw)
    result = tools.control_connectivity("wifi", "status")
    assert "Error" in result and "NM down" in result


def test_control_connectivity_wifi_hardware_on(monkeypatch):
    """CB: control_connectivity wifi hardware set on."""
    monkeypatch.setattr(tools, "_celina_hardware", _FakeHardware())
    result = tools.control_connectivity("wifi", "on")
    assert "on" in result.lower()


def test_control_connectivity_wifi_hardware_off(monkeypatch):
    """CB: control_connectivity wifi hardware set off."""
    monkeypatch.setattr(tools, "_celina_hardware", _FakeHardware())
    result = tools.control_connectivity("wifi", "off")
    assert "off" in result.lower()


def test_control_connectivity_wifi_nmcli_not_installed(monkeypatch):
    """CB: control_connectivity wifi nmcli not installed."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.control_connectivity("wifi", "on")
    assert "nmcli not installed" in result


def test_control_connectivity_wifi_nmcli_status(monkeypatch):
    """CB: control_connectivity wifi nmcli status."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/nmcli" if cmd == "nmcli" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True, stdout="wlp2s0:wifi:connected:MySSID"),
    )
    result = tools.control_connectivity("wifi", "status")
    assert "MySSID" in result


def test_control_connectivity_wifi_nmcli_status_error(monkeypatch):
    """CB: control_connectivity wifi nmcli status error."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/nmcli" if cmd == "nmcli" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=False, error="device not found"),
    )
    result = tools.control_connectivity("wifi", "status")
    assert "Error: device not found" in result


def test_control_connectivity_wifi_nmcli_on(monkeypatch):
    """CB: control_connectivity wifi nmcli on."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/nmcli" if cmd == "nmcli" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True),
    )
    result = tools.control_connectivity("wifi", "on")
    assert "Wi-Fi turned on" in result


def test_control_connectivity_wifi_nmcli_off_error(monkeypatch):
    """CB: control_connectivity wifi nmcli off error."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/nmcli" if cmd == "nmcli" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=False, error="radio off failed"),
    )
    result = tools.control_connectivity("wifi", "off")
    assert "Error: radio off failed" in result


def test_control_connectivity_bluetooth_hardware_status(monkeypatch):
    """CB: control_connectivity bluetooth hardware status."""
    monkeypatch.setattr(tools, "_celina_hardware", _FakeHardware())
    result = tools.control_connectivity("bluetooth", "status")
    assert "Bluetooth" in result and "ON" in result


def test_control_connectivity_bluetooth_hardware_unhealthy(monkeypatch):
    """CB: control_connectivity bluetooth hardware status unhealthy."""
    hw = _FakeHardware(bluetooth=_FakeBluetooth(state=_FakeNetState(_FakeCapStatus.UNHEALTHY, detail="BT down")))
    monkeypatch.setattr(tools, "_celina_hardware", hw)
    result = tools.control_connectivity("bluetooth", "status")
    assert "Error" in result and "BT down" in result


def test_control_connectivity_bluetooth_hardware_on(monkeypatch):
    """CB: control_connectivity bluetooth hardware on."""
    monkeypatch.setattr(tools, "_celina_hardware", _FakeHardware())
    result = tools.control_connectivity("bluetooth", "on")
    assert "on" in result.lower()


def test_control_connectivity_bluetooth_no_btctl(monkeypatch):
    """CB: control_connectivity bluetooth no bluetoothctl."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.control_connectivity("bluetooth", "on")
    assert "bluetoothctl not installed" in result


def test_control_connectivity_bluetooth_btctl_status(monkeypatch):
    """CB: control_connectivity bluetooth btctl status."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/bluetoothctl" if cmd == "bluetoothctl" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True, stdout="Controller hci0"),
    )
    result = tools.control_connectivity("bluetooth", "status")
    assert "Controller hci0" in result


def test_control_connectivity_bluetooth_btctl_power_on(monkeypatch):
    """CB: control_connectivity bluetooth btctl power on."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/bluetoothctl" if cmd == "bluetoothctl" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True),
    )
    result = tools.control_connectivity("bluetooth", "on")
    assert "Bluetooth turned on" in result


def test_control_connectivity_wifi_aliases(monkeypatch):
    """CB: control_connectivity accepts wifi/wi-fi/network aliases."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    for tgt in ("wifi", "wi-fi", "network"):
        result = tools.control_connectivity(tgt, "on")
        assert "nmcli not installed" in result


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


def test_web_search_empty_query():
    """CB: web_search rejects empty query."""
    result = tools.web_search("")
    assert "Provide a search query" in result


def test_web_search_whitespace_query():
    """CB: web_search rejects whitespace-only query."""
    result = tools.web_search("   ")
    assert "Provide a search query" in result


def test_web_search_cache_hit(monkeypatch):
    """CB: web_search returns cached result."""
    import celina_pkg.cache as cache_mod

    fake_cache = MagicMock()
    fake_cache.get.return_value = ("cached result",)
    monkeypatch.setattr(cache_mod, "get_cache", lambda: fake_cache)
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda tool, q: None)
    result = tools.web_search("test query")
    assert result == "cached result"


def test_web_search_net_policy_blocks(monkeypatch):
    """CB: web_search propagates net policy errors (outside try block)."""
    def boom(tool, q):
        raise Exception("blocked")
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", boom)
    with pytest.raises(Exception, match="blocked"):
        tools.web_search("test query")


def test_web_search_no_results(monkeypatch):
    """CB: web_search returns no results when DDG and fallback both empty."""
    import celina_pkg.cache as cache_mod

    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    monkeypatch.setattr(cache_mod, "get_cache", lambda: fake_cache)
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda tool, q: None)
    monkeypatch.setattr(tools, "_search_ddg_html", lambda q: [])
    monkeypatch.setattr(tools, "_search_fallback_apis", lambda q: [])
    result = tools.web_search("test query")
    assert "No web results found" in result


def test_web_search_ddg_results(monkeypatch):
    """CB: web_search returns DDG results."""
    import celina_pkg.cache as cache_mod

    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    fake_cache.set = MagicMock()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: fake_cache)
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda tool, q: None)
    monkeypatch.setattr(
        tools,
        "_search_ddg_html",
        lambda q: [{"title": "T", "url": "https://example.com", "snippet": "S"}],
    )
    result = tools.web_search("test query")
    assert "T" in result
    assert "https://example.com" in result
    assert "S" in result


def test_web_search_ddg_empty_fallback_used(monkeypatch):
    """CB: web_search uses fallback APIs when DDG returns nothing."""
    import celina_pkg.cache as cache_mod

    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    fake_cache.set = MagicMock()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: fake_cache)
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda tool, q: None)
    monkeypatch.setattr(tools, "_search_ddg_html", lambda q: [])
    monkeypatch.setattr(
        tools,
        "_search_fallback_apis",
        lambda q: [{"title": "F", "url": "https://fallback.com", "snippet": "FS"}],
    )
    result = tools.web_search("test query")
    assert "F" in result


def test_web_search_exception(monkeypatch):
    """CB: web_search catches exceptions."""
    import celina_pkg.cache as cache_mod

    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    monkeypatch.setattr(cache_mod, "get_cache", lambda: fake_cache)
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda tool, q: None)
    monkeypatch.setattr(tools, "_search_ddg_html", lambda q: (_ for _ in ()).throw(Exception("ddg boom")))
    result = tools.web_search("test query")
    assert "Web search failed: ddg boom" in result


# ---------------------------------------------------------------------------
# run_shell_command
# ---------------------------------------------------------------------------


def test_run_shell_command_empty():
    """CB: run_shell_command rejects empty command."""
    result = tools.run_shell_command("")
    assert "Empty command" in result


def test_run_shell_command_catastrophic():
    """CB: run_shell_command blocks catastrophic commands."""
    result = tools.run_shell_command("rm -rf /")
    assert "Blocked" in result and "catastrophic" in result.lower()


def test_run_shell_command_no_approval_cb():
    """CB: run_shell_command requires approval callback."""
    result = tools.run_shell_command("ls")
    assert "Denied" in result and "approval" in result.lower()


def test_run_shell_command_bwrap_sandbox(monkeypatch):
    """CB: run_shell_command uses bwrap sandbox when available."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools, "_build_bwrap_command", lambda cmd, **k: ["bwrap", "--", "bash", "-lc", cmd])
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True, stdout="output", stderr="", returncode=0),
    )
    def fake_request(tool, args, cb, title, target, extra_warning=None):
        return MagicMock(), None
    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *a, **k: (True, None))
    result = tools.run_shell_command("ls", approval_cb=lambda *a, **k: True)
    assert "[bwrap sandbox]" in result


def test_run_shell_command_unsandboxed_bwrap_unavailable(monkeypatch):
    """CB: run_shell_command unsandboxed path when bwrap unavailable."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    args_capture = {}
    def fake_request(tool, args, cb, title, target, extra_warning=None):
        args_capture["args"] = args
        return MagicMock(), None
    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *a, **k: (True, None))
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True, stdout="host output", returncode=0),
    )
    result = tools.run_shell_command("ls", approval_cb=lambda *a, **k: True)
    assert "[UNSANDBOXED HOST SHELL]" in result


def test_run_shell_command_unsandboxed_first_ack_denied(monkeypatch):
    """CB: run_shell_command refuses unsandboxed when first ack denied."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    def fake_request(tool, args, cb, title, target, extra_warning=None):
        if args.get("mode") == "sandbox_unavailable_ack":
            return None, "Refused"
        return MagicMock(), None
    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda *a, **k: (True, None))
    result = tools.run_shell_command("ls", approval_cb=lambda *a, **k: True)
    assert "Refused unsandboxed fallback" in result


def test_run_shell_command_unsandboxed_second_denied(monkeypatch):
    """CB: run_shell_command refuses unsandboxed when second confirmation denied."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    call_count = [0]
    def fake_request(tool, args, cb, title, target, extra_warning=None):
        call_count[0] += 1
        return MagicMock(), None
    def fake_verify(token, tool, args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            return False, "User cancelled"
        return True, None
    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", fake_verify)
    result = tools.run_shell_command("ls", approval_cb=lambda *a, **k: True)
    assert "User cancelled" in result


# ---------------------------------------------------------------------------
# close_application
# ---------------------------------------------------------------------------


def test_close_application_empty():
    """CB: close_application rejects empty name."""
    result = tools.close_application("")
    assert "Blocked" in result


def test_close_application_protected():
    """CB: close_application blocks protected processes."""
    result = tools.close_application("systemd")
    assert "Blocked" in result


def test_close_application_approval_denied(tmp_path, monkeypatch):
    """CB: close_application rejects when approval denied."""
    import celina_pkg.config as config

    monkeypatch.setattr(config, "HOME", tmp_path)
    result = tools.close_application("firefox", approval_cb=lambda *a, **k: False)
    assert "Cancelled" in result or "denied" in result.lower() or "Error" in result


def test_close_application_no_pkill(monkeypatch):
    """CB: close_application returns error when pkill not available."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.close_application("firefox", approval_cb=_always_approve)
    assert "pkill not available" in result


def test_close_application_pkill_success(monkeypatch):
    """CB: close_application sends pkill successfully."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/pkill" if cmd == "pkill" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=True),
    )
    result = tools.close_application("firefox", approval_cb=_always_approve)
    assert "Sent close signal" in result


def test_close_application_pkill_not_found(monkeypatch):
    """CB: close_application reports no process when pkill fails."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/pkill" if cmd == "pkill" else None)
    monkeypatch.setattr(
        tools.command_runner,
        "run_command",
        lambda *a, **k: _FakeResult(ok=False),
    )
    result = tools.close_application("firefox", approval_cb=_always_approve)
    assert "No running user process" in result


def _always_approve(action, params, cb=None, title="", target=""):
    class Token:
        pass
    return Token(), None
