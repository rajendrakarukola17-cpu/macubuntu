"""
tests/audit/unit/test_tools_coverage_24.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 24).
Targets: run_shell_command, set_volume remaining branches.

Covers: run_shell_command empty/catastrophic/no-approval/bwrap-unsandboxed/host-shell paths;
set_volume hardware state/get-state/hardware-set-volume/CLI wpctl/pactl mute/level/delta paths.

No real shell or hardware calls. Mocks approval, command_runner, _can_use_bwrap,
is_catastrophic_command, _celina_hardware, shutil.which.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error="", timed_out=False, returncode=0):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.timed_out = timed_out
        self.returncode = returncode


# ---------------------------------------------------------------------------
# run_shell_command
# ---------------------------------------------------------------------------

def test_run_shell_command_empty(monkeypatch):
    """CB: run_shell_command empty command → error."""
    result = tools.run_shell_command("")
    assert "Empty command" in result


def test_run_shell_command_catastrophic(monkeypatch):
    """CB: run_shell_command catastrophic command → blocked."""
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: True)
    result = tools.run_shell_command("rm -rf /")
    assert "Blocked" in result


def test_run_shell_command_no_approval_cb(monkeypatch):
    """CB: run_shell_command no approval_cb → denied."""
    result = tools.run_shell_command("echo hello")
    assert "Denied" in result


def test_run_shell_command_bwrap_sandbox(monkeypatch):
    """CB: run_shell_command sandbox + bwrap_ok → bwrap path."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="output", returncode=0))
    monkeypatch.setattr(tools, "_build_bwrap_command", lambda cmd, **k: ["bwrap", cmd])

    result = tools.run_shell_command("echo hello", sandbox=True, approval_cb=lambda t, d: True)
    assert result is not None


def test_run_shell_command_unsandboxed_no_bwrap(monkeypatch):
    """CB: run_shell_command sandbox=True, no bwrap → unsandboxed double approval path."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)

    call_count = [0]
    def fake_request(*a, **k):
        call_count[0] += 1
        return ("token", None)

    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="output", returncode=0))

    result = tools.run_shell_command("echo hello", sandbox=True, approval_cb=lambda t, d: True)
    assert result is not None
    assert call_count[0] >= 2  # double approval


def test_run_shell_command_no_sandbox(monkeypatch):
    """CB: run_shell_command sandbox=False → direct host shell approval path."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    monkeypatch.setattr(tools, "is_catastrophic_command", lambda cmd: False)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="output", returncode=0))

    result = tools.run_shell_command("echo hello", sandbox=False, approval_cb=lambda t, d: True)
    assert result is not None


# ---------------------------------------------------------------------------
# set_volume
# ---------------------------------------------------------------------------

def test_set_volume_hardware_state(monkeypatch):
    """CB: set_volume no args + hardware → get_state → returns volume string."""
    fake_hw = MagicMock()
    fake_st = MagicMock()
    fake_st.status = tools._celina_hardware.CapabilityStatus.HEALTHY
    fake_st.volume_percent = 50
    fake_st.muted = False
    fake_st.backend = "pipewire"
    fake_st.detail = None
    fake_hw.audio.get_state = MagicMock(return_value=fake_st)
    monkeypatch.setattr(tools._celina_hardware, "get_hardware", lambda: fake_hw)

    result = tools.set_volume()
    assert "Volume:" in result


def test_set_volume_hardware_unhealthy(monkeypatch):
    """CB: set_volume no args + hardware unhealthy → error."""
    fake_hw = MagicMock()
    fake_st = MagicMock()
    fake_st.status = tools._celina_hardware.CapabilityStatus.FAILED
    fake_st.detail = "no mixer"
    fake_st.volume_percent = 0
    fake_st.muted = False
    fake_st.backend = "none"
    fake_hw.audio.get_state = MagicMock(return_value=fake_st)
    monkeypatch.setattr(tools._celina_hardware, "get_hardware", lambda: fake_hw)

    result = tools.set_volume()
    assert "Error" in result


def test_set_volume_hardware_set_volume(monkeypatch):
    """CB: set_volume level + hardware → hw.audio.set_volume → returns message."""
    fake_hw = MagicMock()
    fake_res = MagicMock()
    fake_res.message = "Volume set to 75%."
    fake_hw.audio.set_volume = MagicMock(return_value=fake_res)
    monkeypatch.setattr(tools._celina_hardware, "get_hardware", lambda: fake_hw)

    result = tools.set_volume(level=75)
    assert "Volume set to 75%." in result


def test_set_volume_wpctl_mute(monkeypatch):
    """CB: set_volume mute=True + wpctl → wpctl set-mute."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="", returncode=0))
    result = tools.set_volume(mute=True)
    assert "muted" in result.lower()


def test_set_volume_pactl_mute(monkeypatch):
    """CB: set_volume mute=True + pactl → pactl set-sink-mute."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/pactl" if cmd == "pactl" else None)
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="", returncode=0))
    result = tools.set_volume(mute=True)
    assert "muted" in result.lower()


def test_set_volume_no_mixer(monkeypatch):
    """CB: set_volume mute + no wpctl/pactl → error."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.set_volume(mute=True)
    assert "No supported audio mixer" in result


def test_set_volume_wpctl_level(monkeypatch):
    """CB: set_volume level + wpctl → wpctl set-volume."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="", returncode=0))
    result = tools.set_volume(level=50)
    assert "Volume set to 50%." in result


def test_set_volume_pactl_level(monkeypatch):
    """CB: set_volume level + pactl → pactl set-sink-volume."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/pactl" if cmd == "pactl" else None)
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="", returncode=0))
    result = tools.set_volume(level=50)
    assert "Volume set to 50%." in result


def test_set_volume_wpctl_delta(monkeypatch):
    """CB: set_volume delta + wpctl → wpctl set-volume -l."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/wpctl" if cmd == "wpctl" else None)
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="", returncode=0))
    result = tools.set_volume(delta=10)
    assert "adjusted" in result.lower()


def test_set_volume_pactl_delta(monkeypatch):
    """CB: set_volume delta + pactl → pactl set-sink-volume."""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/pactl" if cmd == "pactl" else None)
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="", returncode=0))
    result = tools.set_volume(delta=-5)
    assert "adjusted" in result.lower()


def test_set_volume_no_args(monkeypatch):
    """CB: set_volume no args, no hardware → 'Specify level...'"""
    monkeypatch.setattr(tools, "_celina_hardware", None)
    result = tools.set_volume()
    assert "Specify level" in result
