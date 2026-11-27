"""
tests/audit/unit/test_tools_coverage_16.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 16).
Targets: execute_tool (remaining branches), _coerce_and_filter_tool_args,
open_application.

Covers: capability failure with is_t2=True (evidence recording path),
_APPROVAL_TOOL_MAP path, _AUTO_TOOL_MAP success path, BulkheadRejectedError,
OfflineError, _coerce_and_filter_tool_args filtering, open_application.

No real network calls. Mocks config, capabilities, circuit breaker, evidence,
TOOL_BULKHEAD, guardrails, _APPROVAL_TOOL_MAP, _AUTO_TOOL_MAP.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools  # noqa: E402


# ---------------------------------------------------------------------------
# Capability failure with is_t2=True → evidence recording path
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: Tier-2 execute_tool fails closed without a trusted broker")
def test_execute_tool_capability_failure_t2(monkeypatch):
    """CB: capability check fails for T2 tool → evidence recorded → cap_err returned."""
    monkeypatch.setattr(tools.config, "TIER1_AUTO_TOOLS", set())
    monkeypatch.setattr(tools.config, "TIER2_APPROVAL_TOOLS", {"submit_goal"})
    monkeypatch.setattr(
        tools.capabilities, "check_tool_execution_boundary",
        lambda name, is_tier2=False: (False, "blocked by capability policy"),
    )
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: None
    )
    recorded = []
    fake_ledger = MagicMock()
    fake_ledger.record_execution = MagicMock(side_effect=lambda **kw: recorded.append(kw))
    monkeypatch.setattr(tools.evidence, "get_evidence_ledger", lambda: fake_ledger)
    monkeypatch.setattr(tools.evidence, "get_current_why_context", lambda: "why-context")

    result = tools.execute_tool("submit_goal", {"goal": "test"})
    assert "blocked by capability policy" in result
    assert len(recorded) == 1


# ---------------------------------------------------------------------------
# _APPROVAL_TOOL_MAP path
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="Superseded by Gate 4.7: Tier-2 execute_tool fails closed without a trusted broker")
def test_execute_tool_approval_tool_map_path(monkeypatch):
    """CB: tool in _APPROVAL_TOOL_MAP → _coerce_and_filter → validate → call."""
    monkeypatch.setattr(tools.config, "TIER1_AUTO_TOOLS", set())
    monkeypatch.setattr(tools.config, "TIER2_APPROVAL_TOOLS", {"submit_goal"})
    monkeypatch.setattr(
        tools.capabilities, "check_tool_execution_boundary",
        lambda name, is_tier2=False: (True, ""),
    )
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: None
    )

    # Mock submit_goal in _APPROVAL_TOOL_MAP
    original_map = dict(tools._APPROVAL_TOOL_MAP)
    tools._APPROVAL_TOOL_MAP["submit_goal"] = lambda goal="", priority=5, approval_cb=None: "goal submitted"
    try:
        monkeypatch.setattr(tools, "_coerce_and_filter_tool_args",
                           lambda name, fn, args: dict(args))
        monkeypatch.setattr(tools.guardrails, "validate_tool_arguments",
                           lambda name, args, schemas: (True, ""))
        result = tools.execute_tool("submit_goal", {"goal": "test goal"})
        assert "goal submitted" in result
    finally:
        tools._APPROVAL_TOOL_MAP.clear()
        tools._APPROVAL_TOOL_MAP.update(original_map)


# ---------------------------------------------------------------------------
# _AUTO_TOOL_MAP success path
# ---------------------------------------------------------------------------

def test_execute_tool_auto_tool_success(monkeypatch):
    """CB: tool in _AUTO_TOOL_MAP → success → circuit breaker records success."""
    monkeypatch.setattr(tools.config, "TIER1_AUTO_TOOLS", {"get_system_stats"})
    monkeypatch.setattr(tools.config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(
        tools.capabilities, "check_tool_execution_boundary",
        lambda name, is_tier2=False: (True, ""),
    )
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: None
    )

    original_map = dict(tools._AUTO_TOOL_MAP)
    tools._AUTO_TOOL_MAP["get_system_stats"] = lambda **k: "system stats ok"
    try:
        monkeypatch.setattr(tools, "_coerce_and_filter_tool_args",
                           lambda name, fn, args: dict(args))
        monkeypatch.setattr(tools.guardrails, "validate_tool_arguments",
                           lambda name, args, schemas: (True, ""))
        result = tools.execute_tool("get_system_stats", {})
        assert "system stats ok" in result
    finally:
        tools._AUTO_TOOL_MAP.clear()
        tools._AUTO_TOOL_MAP.update(original_map)


# ---------------------------------------------------------------------------
# BulkheadRejectedError handler
# ---------------------------------------------------------------------------

def test_execute_tool_bulkhead_rejected(monkeypatch):
    """CB: TOOL_BULKHEAD.acquire raises BulkheadRejectedError → error returned."""
    monkeypatch.setattr(tools.config, "TIER1_AUTO_TOOLS", {"get_system_stats"})
    monkeypatch.setattr(tools.config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(
        tools.capabilities, "check_tool_execution_boundary",
        lambda name, is_tier2=False: (True, ""),
    )
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: None
    )

    # Make TOOL_BULKHEAD.acquire raise BulkheadRejectedError
    original_acquire = tools.TOOL_BULKHEAD.acquire
    tools.TOOL_BULKHEAD.acquire = MagicMock(side_effect=tools.guardrails.BulkheadRejectedError("bulkhead full"))
    try:
        result = tools.execute_tool("get_system_stats", {})
        assert "bulkhead full" in result or "Error:" in result
    finally:
        tools.TOOL_BULKHEAD.acquire = original_acquire


# ---------------------------------------------------------------------------
# OfflineError handler
# ---------------------------------------------------------------------------

def test_execute_tool_offline_error(monkeypatch):
    """CB: tool raises net_policy.OfflineError → offline fallback returned."""
    monkeypatch.setattr(tools.config, "TIER1_AUTO_TOOLS", {"get_system_stats"})
    monkeypatch.setattr(tools.config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(
        tools.capabilities, "check_tool_execution_boundary",
        lambda name, is_tier2=False: (True, ""),
    )
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: None
    )

    original_map = dict(tools._AUTO_TOOL_MAP)
    tools._AUTO_TOOL_MAP["get_system_stats"] = lambda **k: (_ for _ in ()).throw(
        tools.net_policy.OfflineError("no network")
    )
    try:
        monkeypatch.setattr(tools, "_coerce_and_filter_tool_args",
                           lambda name, fn, args: dict(args))
        monkeypatch.setattr(tools.guardrails, "validate_tool_arguments",
                           lambda name, args, schemas: (True, ""))
        result = tools.execute_tool("get_system_stats", {})
        assert "Offline fallback" in result
    finally:
        tools._AUTO_TOOL_MAP.clear()
        tools._AUTO_TOOL_MAP.update(original_map)


# ---------------------------------------------------------------------------
# Circuit breaker: check_open returns number, not disabled
# ---------------------------------------------------------------------------

def test_execute_tool_circuit_breaker_cooldown(monkeypatch):
    """CB: circuit breaker open but not session-disabled → cooldown message."""
    monkeypatch.setattr(tools.config, "TIER1_AUTO_TOOLS", {"get_system_stats"})
    monkeypatch.setattr(tools.config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(
        tools.capabilities, "check_tool_execution_boundary",
        lambda name, is_tier2=False: (True, ""),
    )
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: 3.0
    )
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "is_session_disabled", lambda name: None
    )
    monkeypatch.setattr(tools.TOOL_CIRCUIT_BREAKER, "threshold", 5)

    result = tools.execute_tool("get_system_stats", {})
    assert "Circuit breaker open" in result
    assert "cooling down" in result


# ---------------------------------------------------------------------------
# _coerce_and_filter_tool_args
# ---------------------------------------------------------------------------

def test_coerce_and_filter_tool_args_removes_disallowed():
    """CB: _coerce_and_filter_tool_args removes keys not in function signature."""
    def sample_fn(a: str, b: int = 0):
        return "ok"

    result = tools._coerce_and_filter_tool_args("test_tool", sample_fn,
                                                 {"a": "hello", "b": 42, "extra": "ignored", "approval_cb": None})
    assert result == {"a": "hello", "b": 42}


def test_coerce_and_filter_tool_args_keeps_approval_cb():
    """CB: _coerce_and_filter_tool_args keeps approval_cb if function accepts it."""
    def sample_fn(a: str, approval_cb=None):
        return "ok"

    result = tools._coerce_and_filter_tool_args("test_tool", sample_fn,
                                                 {"a": "hello", "approval_cb": "cb"})
    assert "approval_cb" in result


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error=""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error


# ---------------------------------------------------------------------------
# open_application
# ---------------------------------------------------------------------------

def test_open_application_found(monkeypatch):
    """CB: open_application finds app → launches it."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: "/usr/bin/calc" if cmd == "calc" else None)
    with patch.object(tools.command_runner, "run_command", return_value=_FakeResult(ok=True)):
        result = tools.open_application("calc")
        assert result is not None


def test_open_application_not_found(monkeypatch):
    """CB: open_application doesn't find app → error message."""
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.open_application("nonexistent_app_xyz")
    assert "not found" in result.lower() or "Error" in result or "error" in result.lower()
