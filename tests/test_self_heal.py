"""
Headless unit tests for features/celina/celina_pkg/plugins/self_heal.py.

Tests the self-healing fallback chain:
- gear0_heal_classify pattern matching
- gear0_heal_execute dispatch
- SelfHealingFallback chain resolution
- FallbackChain execution (primary → secondary → fallback)
- Tool availability checking
- Execution history recording
- Reliability reporting
"""

import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

CELINA_ROOT = Path(__file__).resolve().parent.parent / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.plugins import self_heal as sh


# --------------------------------------------------------------------------- #
# gear0_heal_classify
# --------------------------------------------------------------------------- #

def test_classify_recognizes_heal_queries():
    """gear0_heal_classify should return 'execute_with_fallback' for heal queries."""
    for q in ["heal", "self heal", "recover", "fix this", "please heal"]:
        assert sh.gear0_heal_classify(q) == "execute_with_fallback", f"'{q}' should classify"

def test_classify_recognizes_reliability():
    """gear0_heal_classify should return 'reliability_report' for reliability queries."""
    assert sh.gear0_heal_classify("reliability report") == "reliability_report"
    assert sh.gear0_heal_classify("reliability") == "reliability_report"

def test_classify_recognizes_fallback():
    """gear0_heal_classify should return 'execute_with_fallback' for fallback queries."""
    assert sh.gear0_heal_classify("fallback chain") == "execute_with_fallback"

def test_classify_returns_none_for_unrelated():
    """gear0_heal_classify should return None for unrelated queries."""
    assert sh.gear0_heal_classify("open browser") is None
    assert sh.gear0_heal_classify("play music") is None
    assert sh.gear0_heal_classify("") is None


# --------------------------------------------------------------------------- #
# gear0_heal_execute
# --------------------------------------------------------------------------- #

def test_execute_reliability_report():
    """gear0_heal_execute with reliability_report should return status text."""
    result = sh.gear0_heal_execute("reliability_report", "check reliability")
    assert result is not None
    assert "Self-healing fallback chain is active" in result

def test_execute_with_fallback_no_chain():
    """gear0_heal_execute with execute_with_fallback should handle missing chain gracefully."""
    result = sh.gear0_heal_execute("execute_with_fallback", "echo hello")
    assert result is not None
    assert "No fallback chain registered" in result

def test_execute_unknown_handler_returns_none():
    """gear0_heal_execute with unknown handler should return None."""
    assert sh.gear0_heal_execute("nonexistent_handler", "query") is None


# --------------------------------------------------------------------------- #
# SelfHealingFallback
# --------------------------------------------------------------------------- #

def test_healer_instantiation(tmp_path):
    """SelfHealingFallback should instantiate with empty custom chains."""
    old_db = sh._DB_PATH
    sh._DB_PATH = tmp_path / "test_selfheal.db"
    try:
        healer = sh.SelfHealingFallback()
        assert healer is not None
        assert healer._custom_chains == {}
    finally:
        sh._DB_PATH = old_db

def test_get_chain_defaults():
    """get_chain should return default chains for known actions."""
    healer = sh.get_healer()
    chain = healer.get_chain("open_pdf")
    assert chain is not None
    assert chain.action == "open_pdf"
    assert chain.primary is not None
    assert chain.primary.name == "xdg_open"

def test_get_chain_unknown_returns_none():
    """get_chain should return None for unknown actions."""
    healer = sh.get_healer()
    assert healer.get_chain("nonexistent_action") is None

def test_register_and_get_custom_chain():
    """register_chain + get_chain should work for custom chains."""
    old_db = sh._DB_PATH
    sh._DB_PATH = Path(tempfile.mkdtemp()) / "test_custom.db"
    try:
        healer = sh.SelfHealingFallback()
        chain = sh.FallbackChain(
            action="custom_action",
            primary=sh.FallbackStep("echo", "echo", ["hello"]),
        )
        healer.register_chain(chain)
        retrieved = healer.get_chain("custom_action")
        assert retrieved is not None
        assert retrieved.action == "custom_action"
        assert retrieved.primary.name == "echo"
    finally:
        sh._DB_PATH = old_db

def test_custom_chain_takes_precedence():
    """Custom chains should take precedence over defaults."""
    old_db = sh._DB_PATH
    sh._DB_PATH = Path(tempfile.mkdtemp()) / "test_prec.db"
    try:
        healer = sh.SelfHealingFallback()
        # Register a custom chain for open_pdf (which has a default)
        chain = sh.FallbackChain(
            action="open_pdf",
            primary=sh.FallbackStep("custom_primary", "true"),
        )
        healer.register_chain(chain)
        retrieved = healer.get_chain("open_pdf")
        assert retrieved.primary.name == "custom_primary"
    finally:
        sh._DB_PATH = old_db

def test_tool_available_false():
    """_tool_available should return False for nonexistent tools."""
    healer = sh.get_healer()
    assert healer._tool_available("nonexistent_tool_xyz_12345") is False

def test_execute_no_chain_returns_failure():
    """execute with no registered chain should return failure result."""
    old_db = sh._DB_PATH
    sh._DB_PATH = Path(tempfile.mkdtemp()) / "test_nochain.db"
    try:
        healer = sh.SelfHealingFallback()
        result = healer.execute("nonexistent_action_xyz")
        assert result.success is False
        assert result.step_used == "none"
        assert "No fallback chain registered" in result.output
    finally:
        sh._DB_PATH = old_db

def test_execute_successful_step(tmp_path):
    """execute should return success when a step succeeds."""
    old_db = sh._DB_PATH
    sh._DB_PATH = tmp_path / "test_success.db"
    try:
        healer = sh.SelfHealingFallback()
        chain = sh.FallbackChain(
            action="test_action",
            primary=sh.FallbackStep("true_cmd", "true"),
        )
        result = healer.execute("test_action", chain=chain)
        assert result.success is True
        assert result.step_used == "true_cmd"
    finally:
        sh._DB_PATH = old_db

def test_execute_falls_back_to_secondary(tmp_path):
    """execute should try secondary when primary fails."""
    old_db = sh._DB_PATH
    sh._DB_PATH = tmp_path / "test_fallback.db"
    try:
        healer = sh.SelfHealingFallback()
        chain = sh.FallbackChain(
            action="test_action",
            primary=sh.FallbackStep("false_cmd", "false"),
            secondary=sh.FallbackStep("true_cmd", "true"),
        )
        result = healer.execute("test_action", chain=chain)
        assert result.success is True
        assert result.step_used == "true_cmd"
        assert len(result.attempts) == 2
    finally:
        sh._DB_PATH = old_db

def test_execute_all_steps_fail(tmp_path):
    """execute should report failure when all steps fail."""
    old_db = sh._DB_PATH
    sh._DB_PATH = tmp_path / "test_allfail.db"
    try:
        healer = sh.SelfHealingFallback()
        chain = sh.FallbackChain(
            action="test_action",
            primary=sh.FallbackStep("false_cmd1", "false"),
            secondary=sh.FallbackStep("false_cmd2", "false"),
        )
        result = healer.execute("test_action", chain=chain)
        assert result.success is False
        assert result.step_used == "all_failed"
        assert len(result.attempts) == 2
    finally:
        sh._DB_PATH = old_db

def test_required_tool_not_available(tmp_path):
    """_execute_step should fail fast when required_tool is missing."""
    old_db = sh._DB_PATH
    sh._DB_PATH = tmp_path / "test_req.db"
    try:
        healer = sh.SelfHealingFallback()
        chain = sh.FallbackChain(
            action="test_action",
            primary=sh.FallbackStep("missing_tool", "nonexistent_binary_xyz", required_tool="nonexistent_binary_xyz"),
        )
        result = healer.execute("test_action", chain=chain)
        assert result.success is False
        assert "Required tool not found" in result.attempts[0]["output"]
    finally:
        sh._DB_PATH = old_db

def test_get_history_returns_list(tmp_path):
    """get_history should return a list of history records."""
    old_db = sh._DB_PATH
    sh._DB_PATH = tmp_path / "test_history.db"
    try:
        healer = sh.SelfHealingFallback()
        history = healer.get_history(limit=20)
        assert isinstance(history, list)
    finally:
        sh._DB_PATH = old_db

def test_get_reliability_report_returns_dict(tmp_path):
    """get_reliability_report should return a dict."""
    old_db = sh._DB_PATH
    sh._DB_PATH = tmp_path / "test_report.db"
    try:
        healer = sh.SelfHealingFallback()
        report = healer.get_reliability_report()
        assert isinstance(report, dict)
    finally:
        sh._DB_PATH = old_db

def test_execute_with_fallback_convenience():
    """execute_with_fallback convenience function should work."""
    result = sh.execute_with_fallback("nonexistent_action_xyz")
    assert result.success is False

# --------------------------------------------------------------------------- #
# FallbackChain / FallbackStep dataclasses
# --------------------------------------------------------------------------- #

def test_fallback_step_creation():
    """FallbackStep should create with name, command, optional args."""
    step = sh.FallbackStep(name="test", command="echo", args=["hello"])
    assert step.name == "test"
    assert step.command == "echo"
    assert step.args == ["hello"]
    assert step.required_tool is None

def test_fallback_chain_creation():
    """FallbackChain should create with action and primary step."""
    primary = sh.FallbackStep("primary", "echo", ["hello"])
    chain = sh.FallbackChain(action="test", primary=primary)
    assert chain.action == "test"
    assert chain.primary == primary
    assert chain.secondary is None
    assert chain.fallback is None

def test_execution_result_creation():
    """ExecutionResult should create with all fields."""
    result = sh.ExecutionResult(
        success=True,
        step_used="test",
        output="output",
        total_time_ms=5.0,
    )
    assert result.success is True
    assert result.step_used == "test"
    assert result.output == "output"
    assert result.total_time_ms == 5.0
    assert result.attempts == []
