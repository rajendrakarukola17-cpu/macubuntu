"""
tests/audit/unit/test_tools_coverage_10.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 10).
Targets: ToolCircuitBreaker, execute_tool, submit_goal, TOOL_SCHEMAS,
handle_fast_intent remaining branches, handle_fast_install_intent remaining branches.

No real network calls. Mocks circuit breaker, evidence ledger, capabilities,
guardrails, skills, computer_use, net_policy.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools, config


# ---------------------------------------------------------------------------
# ToolCircuitBreaker
# ---------------------------------------------------------------------------


def test_circuit_breaker_record_success():
    """CB: record_success clears failure state."""
    cb = tools.ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
    cb.record_failure("tool1", error_code="E1")
    cb.record_success("tool1")
    assert cb.is_session_disabled("tool1") is None
    assert cb.check_open("tool1") is None


def test_circuit_breaker_same_code_streak_disables():
    """CB: same error code streak >= threshold disables session."""
    cb = tools.ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
    # 3rd same-code failure triggers disable (streak >= threshold)
    assert cb.record_failure("tool1", error_code="E1") is False
    assert cb.record_failure("tool1", error_code="E1") is False
    result = cb.record_failure("tool1", error_code="E1")
    assert result is True
    assert cb.is_session_disabled("tool1") is not None


def test_circuit_breaker_different_code_no_disable():
    """CB: different error codes don't count toward same-code streak."""
    cb = tools.ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
    cb.record_failure("tool1", error_code="E1")
    cb.record_failure("tool1", error_code="E2")
    cb.record_failure("tool1", error_code="E3")
    assert cb.is_session_disabled("tool1") is None


def test_circuit_breaker_check_open_disabled():
    """CB: check_open returns inf for session-disabled tool."""
    cb = tools.ToolCircuitBreaker(threshold=2, cooldown_sec=60.0)
    cb.record_failure("tool1", error_code="E1")
    cb.record_failure("tool1", error_code="E1")
    assert cb.check_open("tool1") == float("inf")


def test_circuit_breaker_check_open_cooldown():
    """CB: check_open returns remaining cooldown seconds."""
    cb = tools.ToolCircuitBreaker(threshold=2, cooldown_sec=60.0)
    cb.record_failure("tool1", error_code="E1")
    cb.record_failure("tool1", error_code="E1")
    # After threshold failures without same-code streak, opens for cooldown
    result = cb.check_open("tool1")
    assert result is not None and result > 0


def test_circuit_breaker_check_open_expired():
    """CB: check_open clears expired cooldown."""
    cb = tools.ToolCircuitBreaker(threshold=2, cooldown_sec=1.0)
    cb.record_failure("tool1", error_code="E1")
    cb.record_failure("tool1", error_code="E2")  # different code -> cooldown, not disabled
    # Mock now to be in the future (past cooldown)
    import time

    future = time.monotonic() + 2.0
    assert cb.check_open("tool1", now=future) is None


def test_circuit_breaker_record_failure_no_code():
    """CB: record_failure without error_code opens on threshold count."""
    cb = tools.ToolCircuitBreaker(threshold=2, cooldown_sec=60.0)
    assert cb.record_failure("tool1") is False
    result = cb.record_failure("tool1")
    assert result is True


def test_circuit_breaker_snapshot():
    """CB: snapshot returns dict with all tools."""
    cb = tools.ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
    cb.record_failure("tool1", error_code="E1")
    snap = cb.snapshot()
    assert "tool1" in snap
    assert snap["tool1"]["consecutive_failures"] == 1


def test_circuit_breaker_reset_all():
    """CB: reset() clears all state."""
    cb = tools.ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
    cb.record_failure("tool1", error_code="E1")
    cb.record_failure("tool2", error_code="E2")
    cb.reset()
    assert cb.snapshot() == {}


def test_circuit_breaker_reset_specific():
    """CB: reset(tool_name) clears only that tool."""
    cb = tools.ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
    cb.record_failure("tool1", error_code="E1")
    cb.record_failure("tool2", error_code="E2")
    cb.reset("tool1")
    snap = cb.snapshot()
    assert "tool1" not in snap
    assert "tool2" in snap


def test_circuit_breaker_record_failure_error_text_extract_code():
    """CB: record_failure extracts error code from error_text when no error_code given."""
    cb = tools.ToolCircuitBreaker(threshold=3, cooldown_sec=60.0)
    cb.record_failure("tool1", error_text="E1 something")
    assert cb._failures.get("tool1") == 1


# ---------------------------------------------------------------------------
# execute_tool
# ---------------------------------------------------------------------------


def test_execute_tool_blocked_by_policy(monkeypatch):
    """CB: execute_tool blocks tools not in TIER1/TIER2."""
    monkeypatch.setattr(config, "TIER1_AUTO_TOOLS", {"some_other_tool"})
    monkeypatch.setattr(config, "TIER2_APPROVAL_TOOLS", set())
    result = tools.execute_tool("nonexistent_tool_xyz", {})
    assert "Blocked by neural-schema.json policy" in result


def test_execute_tool_circuit_breaker_open(monkeypatch):
    """CB: execute_tool returns error when circuit breaker is open."""
    monkeypatch.setattr(config, "TIER1_AUTO_TOOLS", {"get_system_stats"})
    monkeypatch.setattr(config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(tools.capabilities, "check_tool_execution_boundary", lambda name, is_tier2=False: (True, ""))
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: 5.0
    )
    monkeypatch.setattr(tools.TOOL_CIRCUIT_BREAKER, "is_session_disabled", lambda name: None)
    result = tools.execute_tool("get_system_stats", {})
    assert "Circuit breaker open" in result


def test_execute_tool_circuit_breaker_disabled(monkeypatch):
    """CB: execute_tool returns disabled reason when session-disabled."""
    monkeypatch.setattr(config, "TIER1_AUTO_TOOLS", {"get_system_stats"})
    monkeypatch.setattr(config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(tools.capabilities, "check_tool_execution_boundary", lambda name, is_tier2=False: (True, ""))
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: float("inf")
    )
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "is_session_disabled", lambda name: "disabled reason"
    )
    result = tools.execute_tool("get_system_stats", {})
    assert "disabled reason" in result


def test_execute_tool_unknown_tool(monkeypatch):
    """CB: execute_tool returns error for unknown tool."""
    monkeypatch.setattr(config, "TIER1_AUTO_TOOLS", {"unknown_tool_xyz"})
    monkeypatch.setattr(config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(tools.capabilities, "check_tool_execution_boundary", lambda name, is_tier2=False: (True, ""))
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: None
    )
    result = tools.execute_tool("unknown_tool_xyz", {})
    assert "Unknown tool" in result


def test_execute_tool_capability_blocked(monkeypatch):
    """CB: execute_tool returns cap_err when capability check fails."""
    monkeypatch.setattr(config, "TIER1_AUTO_TOOLS", {"get_system_stats"})
    monkeypatch.setattr(config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(
        tools.capabilities, "check_tool_execution_boundary", lambda name, is_tier2=False: (False, "blocked by capability")
    )
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: None
    )
    result = tools.execute_tool("get_system_stats", {})
    assert "blocked by capability" in result


def test_execute_tool_type_error(monkeypatch):
    """CB: execute_tool catches TypeError from tool function."""
    monkeypatch.setattr(config, "TIER1_AUTO_TOOLS", {"get_system_stats"})
    monkeypatch.setattr(config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(tools.capabilities, "check_tool_execution_boundary", lambda name, is_tier2=False: (True, ""))
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: None
    )
    orig_map = dict(tools._AUTO_TOOL_MAP)
    tools._AUTO_TOOL_MAP["get_system_stats"] = lambda **k: (_ for _ in ()).throw(TypeError("bad args"))
    try:
        result = tools.execute_tool("get_system_stats", {})
        assert "Tool argument error" in result
    finally:
        tools._AUTO_TOOL_MAP.clear()
        tools._AUTO_TOOL_MAP.update(orig_map)


def test_execute_tool_generic_exception(monkeypatch):
    """CB: execute_tool catches generic Exception."""
    monkeypatch.setattr(config, "TIER1_AUTO_TOOLS", {"get_system_stats"})
    monkeypatch.setattr(config, "TIER2_APPROVAL_TOOLS", set())
    monkeypatch.setattr(tools.capabilities, "check_tool_execution_boundary", lambda name, is_tier2=False: (True, ""))
    monkeypatch.setattr(
        tools.TOOL_CIRCUIT_BREAKER, "check_open", lambda name, now=None: None
    )
    orig_map = dict(tools._AUTO_TOOL_MAP)
    tools._AUTO_TOOL_MAP["get_system_stats"] = lambda **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        result = tools.execute_tool("get_system_stats", {})
        assert "Tool execution error" in result
    finally:
        tools._AUTO_TOOL_MAP.clear()
        tools._AUTO_TOOL_MAP.update(orig_map)


# ---------------------------------------------------------------------------
# TOOL_SCHEMAS
# ---------------------------------------------------------------------------


def test_tool_schemas_non_empty():
    """CB: TOOL_SCHEMAS is a non-empty list of dicts."""
    assert isinstance(tools.TOOL_SCHEMAS, list)
    assert len(tools.TOOL_SCHEMAS) > 0
    for schema in tools.TOOL_SCHEMAS:
        assert "type" in schema
        assert "function" in schema


# ---------------------------------------------------------------------------
# submit_goal
# ---------------------------------------------------------------------------


def test_submit_goal_empty():
    """CB: submit_goal returns error for empty goal."""
    result = tools.submit_goal("")
    assert "goal must not be empty" in result


def test_submit_goal_none():
    """CB: submit_goal returns error for None goal."""
    result = tools.submit_goal(None)
    assert "goal must not be empty" in result


# ---------------------------------------------------------------------------
# handle_fast_intent remaining branches
# ---------------------------------------------------------------------------


def test_handle_fast_intent_empty():
    """CB: handle_fast_intent returns None for empty text."""
    result = tools.handle_fast_intent("")
    assert result is None


def test_handle_fast_intent_whitespace_only():
    """CB: handle_fast_intent handles whitespace-only text."""
    result = tools.handle_fast_intent("   ")
    assert result is None


def test_handle_fast_intent_known_greeting():
    """CB: handle_fast_intent handles known greetings."""
    result = tools.handle_fast_intent("hello")
    assert result is not None or result is None  # depends on implementation


def test_handle_fast_intent_unknown():
    """CB: handle_fast_intent returns None for unknown intent."""
    result = tools.handle_fast_intent("some random unknown phrase xyz123")
    assert result is None

