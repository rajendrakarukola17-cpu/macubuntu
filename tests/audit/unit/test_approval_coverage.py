"""
tests/audit/unit/test_approval_coverage.py

Phase 7b — TASK 7: Approval module branch coverage tests.
Targets missing branches: _jsonable dict fallback, classify_risk paths,
infer_target static targets, request_bound_approval args truncation,
verify_and_consume None token, ApprovalEngine.request_sync paths.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import approval
from celina_pkg.approval import (
    ApprovalEngine,
    ApprovalToken,
    RiskLevel,
    classify_risk,
    infer_target,
    request_bound_approval,
    verify_and_consume,
)


# ---------------------------------------------------------------------------
# _jsonable: dict fallback branch
# ---------------------------------------------------------------------------


def test_jsonable_dict_fallback():
    """CB: _jsonable handles dict."""
    result = approval._jsonable({"a": 1, "b": [2, 3]})
    assert result == {"a": 1, "b": [2, 3]}


def test_jsonable_datetime_fallback():
    """CB: _jsonable falls back to str() for non-JSON types."""
    result = approval._jsonable(datetime.now())
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# classify_risk: mcp_call_tool, trigger_iot_webhook, command-like args
# ---------------------------------------------------------------------------


def test_classify_risk_mcp_call_tool():
    """CB: classify_risk returns SECURITY_SENSITIVE for mcp_call_tool."""
    risk = classify_risk("mcp_call_tool", {"tool": "x"})
    assert risk == RiskLevel.SECURITY_SENSITIVE


def test_classify_risk_trigger_iot_webhook():
    """CB: classify_risk returns NETWORK_REQUEST for trigger_iot_webhook."""
    risk = classify_risk("trigger_iot_webhook", {})
    assert risk == RiskLevel.NETWORK_REQUEST


def test_classify_risk_command_like_args():
    """CB: classify_risk escalates command-like args for non-shell tools."""
    risk = classify_risk("write_file", {"content": "apt install nginx"})
    assert risk == RiskLevel.SECURITY_SENSITIVE


def test_classify_risk_normal_tool():
    """CB: classify_risk returns base risk for normal tool."""
    risk = classify_risk("read_file", {"path": "/home/user/test.txt"})
    assert risk == RiskLevel.READ_ONLY


# ---------------------------------------------------------------------------
# infer_target: static targets vs fallback
# ---------------------------------------------------------------------------


def test_infer_target_static_run_shell_command():
    """CB: infer_target returns sandbox-shell for run_shell_command."""
    target = infer_target("run_shell_command", {})
    assert target == "sandbox-shell"


def test_infer_target_static_not_in_map():
    """CB: infer_target returns 'desktop' fallback."""
    target = infer_target("unknown_tool", {})
    assert target == "desktop"


# ---------------------------------------------------------------------------
# request_bound_approval: args_preview truncation
# ---------------------------------------------------------------------------


def test_request_bound_approval_args_truncation():
    """CB: request_bound_approval truncates long args preview."""
    big_args = {"payload": "x" * 500}
    token, err = request_bound_approval(
        "write_file", big_args, approval_cb=lambda title, detail: True,
        title="Test", target="/home/user/test.txt",
    )
    assert err is None
    assert token is not None


# ---------------------------------------------------------------------------
# verify_and_consume: None token
# ---------------------------------------------------------------------------


def test_verify_and_consume_none_token():
    """CB: verify_and_consume returns False for None token."""
    ok, err = verify_and_consume(None, "write_file", {})
    assert ok is False
    assert "Missing bound approval token" in err


# ---------------------------------------------------------------------------
# ApprovalEngine.request_sync: no callback, not approved, hash mismatch
# ---------------------------------------------------------------------------


def test_request_sync_no_callback():
    """CB: request_sync returns False when _approval_cb is None."""
    engine = ApprovalEngine(approval_cb=None)
    result = engine.request_sync(tool="write_file", arguments_hash="abc", context="test")
    assert result is False


def test_request_sync_not_approved():
    """CB: request_sync returns False when callback returns False."""
    engine = ApprovalEngine(approval_cb=lambda title, detail: False)
    result = engine.request_sync(tool="write_file", arguments_hash="abc", context="test")
    assert result is False


def test_request_sync_callback_raises():
    """CB: request_sync returns False when callback raises (fail-closed)."""
    engine = ApprovalEngine(approval_cb=lambda title, detail: (_ for _ in ()).throw(RuntimeError("boom")))
    result = engine.request_sync(tool="write_file", arguments_hash="abc", context="test")
    assert result is False


def test_request_sync_empty_tool():
    """CB: request_sync returns False for empty tool."""
    engine = ApprovalEngine(approval_cb=lambda title, detail: True)
    result = engine.request_sync(tool="", arguments_hash="abc", context="test")
    assert result is False


def test_request_sync_empty_hash():
    """CB: request_sync returns False for empty arguments_hash."""
    engine = ApprovalEngine(approval_cb=lambda title, detail: True)
    result = engine.request_sync(tool="write_file", arguments_hash="", context="test")
    assert result is False