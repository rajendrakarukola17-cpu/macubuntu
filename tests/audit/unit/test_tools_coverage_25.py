"""
tests/audit/unit/test_tools_coverage_25.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 25).
Targets: run_python_sandbox remaining branches.

Covers: run_python_sandbox empty/long/no-approval/bwrap-success/bwrap-timeout/
bwrap-failure/unsandboxed-double-approval/unsandboxed-success paths.

No real shell or sandbox calls. Mocks _can_use_bwrap, approval, command_runner,
tempfile.TemporaryDirectory, Path operations, shutil.which, sys.executable.
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
# run_python_sandbox
# ---------------------------------------------------------------------------

def test_run_python_sandbox_empty(monkeypatch):
    """CB: run_python_sandbox empty code → error."""
    result = tools.run_python_sandbox("")
    assert "Empty Python code" in result


def test_run_python_sandbox_too_long(monkeypatch):
    """CB: run_python_sandbox code > 16KB → error."""
    long_code = "x = 1\n" * 5000
    result = tools.run_python_sandbox(long_code)
    assert "exceeds 16 KB" in result


def test_run_python_sandbox_no_approval_cb(monkeypatch):
    """CB: run_python_sandbox no approval_cb → denied."""
    result = tools.run_python_sandbox("print(1)")
    assert "Denied" in result


def test_run_python_sandbox_bwrap_success(monkeypatch):
    """CB: run_python_sandbox bwrap_ok → approval → success → verified OK."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="hello", returncode=0, timed_out=False))

    result = tools.run_python_sandbox("print(1)", approval_cb=lambda t, d: True)
    assert "Verified OK" in result or "bwrap-python" in result


def test_run_python_sandbox_bwrap_timeout(monkeypatch):
    """CB: run_python_sandbox bwrap → command timed out → timeout error."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=False, stdout="", stderr="", timed_out=True, returncode=1))

    result = tools.run_python_sandbox("print(1)", approval_cb=lambda t, d: True)
    assert "timed out" in result.lower()


def test_run_python_sandbox_bwrap_failure(monkeypatch):
    """CB: run_python_sandbox bwrap → non-zero exit → error."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: True)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=False, stdout="", stderr="error", timed_out=False, returncode=1))

    result = tools.run_python_sandbox("print(1)", approval_cb=lambda t, d: True)
    assert "Error" in result and "exit" in result


def test_run_python_sandbox_unsandboxed_success(monkeypatch):
    """CB: run_python_sandbox no bwrap → double approval → success."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)

    call_count = [0]
    def fake_request(*a, **k):
        call_count[0] += 1
        return ("token", None)

    monkeypatch.setattr(tools.approval, "request_bound_approval", fake_request)
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=True, stdout="hello", returncode=0, timed_out=False))

    result = tools.run_python_sandbox("print(1)", approval_cb=lambda t, d: True)
    assert result is not None
    assert call_count[0] >= 2  # double approval


def test_run_python_sandbox_unsandboxed_timeout(monkeypatch):
    """CB: run_python_sandbox no bwrap → timeout → timeout error."""
    monkeypatch.setattr(tools, "_can_use_bwrap", lambda: False)
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: ("token", None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))
    monkeypatch.setattr(tools.command_runner, "run_command",
                        lambda *a, **k: _FakeResult(ok=False, stdout="", stderr="", timed_out=True, returncode=1))

    result = tools.run_python_sandbox("print(1)", approval_cb=lambda t, d: True)
    assert "timed out" in result.lower()
