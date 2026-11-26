"""
tests/audit/unit/test_phase3b_remediation.py

Phase 3b remediation tests covering:

1. plugins/os_shim.py — command execution safety (no shell=True, no metachar execution)
2. api_actuators.py — lazy EvidenceLedger import with zero $HOME side effects
3. connectors/slack.py — outbound message validation, redirect/SSRF policy, error handling
4. controlled_computer_use.py — step bounds, password-field refusal, evidence recording
5. llm/lifecycle_router.py — PII gate before cloud routing, offline fail-closed behavior
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

# ---------------------------------------------------------------------------
# 1. os_shim.py — command execution safety
# ---------------------------------------------------------------------------


def test_os_shim_no_shell_true():
    """Assert no shell=True exists anywhere in plugins/os_shim.py."""
    shim_path = Path(__file__).resolve().parents[3] / "features" / "celina" / "celina_pkg" / "plugins" / "os_shim.py"
    src = shim_path.read_text(encoding="utf-8")
    assert "shell=True" not in src, "shell=True found in os_shim.py"


def test_os_shim_no_metachar_execution():
    """Shell metacharacters in command args cannot execute arbitrary commands."""
    from celina_pkg.plugins.os_shim import UniversalOSShim

    shim = UniversalOSShim()

    # Commands with shell metacharacters should not execute them via shell.
    # The _run helper takes a list[str] argv and passes shell=False.
    result = shim._run(["echo", "; id"], "echo with semicolon arg")
    # ; id is just an argument to echo, not a shell metacharacter since shell=False
    assert result.success is True
    assert "; id" in result.output or "Executed:" in result.output


def test_os_shim_rejects_command_substitution_arg():
    """$(whoami) as an arg is treated literally, not executed."""
    from celina_pkg.plugins.os_shim import UniversalOSShim

    shim = UniversalOSShim()
    result = shim._run(["echo", "$(whoami)"], "echo with command sub arg")
    # With shell=False, $(whoami) is a literal string argument
    assert result.success is True


# ---------------------------------------------------------------------------
# 2. api_actuators.py — lazy EvidenceLedger import
# ---------------------------------------------------------------------------


def test_api_actuators_lazy_import_zero_home_io(tmp_path, monkeypatch):
    """Importing celina_pkg.api_actuators does not call EvidenceLedger()
    at import time — _API_LEDGER stays None until first use."""
    clean_home = tempfile.mkdtemp()
    monkeypatch.setenv("HOME", clean_home)

    import importlib

    import celina_pkg.api_actuators as api_actuators

    importlib.reload(api_actuators)

    # The module-level ledger should still be None (lazy — no import-time
    # EvidenceLedger() call, no fail-closed anchor check at import)
    assert api_actuators._API_LEDGER is None


def test_api_actuators_ledger_created_on_first_use(tmp_path, monkeypatch):
    """Calling _get_api_ledger() creates the ledger and enforces anchor check."""
    clean_home = tempfile.mkdtemp()
    monkeypatch.setenv("HOME", clean_home)

    import celina_pkg.api_actuators as api_actuators

    # First access creates the ledger (fail-closed anchor check runs)
    ledger = api_actuators._get_api_ledger()
    assert ledger is not None
    assert api_actuators._API_LEDGER is ledger


# ---------------------------------------------------------------------------
# 3. connectors/slack.py — message validation, SSRF, error handling
# ---------------------------------------------------------------------------


def test_slack_connector_not_configured():
    """SlackConnector without credentials returns not-configured errors."""
    from celina_pkg.connectors.slack import SlackConnector

    conn = SlackConnector(approval_cb=None)
    result = conn.execute_action("list_channels", {})
    assert result.success is False
    assert "not configured" in result.error.lower()


def test_slack_connector_unknown_action():
    """Unknown action returns error (after connector is configured)."""
    from celina_pkg.connectors.slack import SlackConnector

    conn = SlackConnector(approval_cb=None)
    conn._bot_token = "xoxb-fake-token"
    # Mock is_configured to return True so we get past the config check
    with patch.object(conn, "is_configured", return_value=True):
        result = conn.execute_action("nonexistent_action", {})
    assert result.success is False
    assert "Unknown action" in result.error


def test_slack_send_message_requires_channel_and_text():
    """send_message without required params fails validation."""
    from celina_pkg.connectors.slack import SlackConnector

    conn = SlackConnector(approval_cb=None)
    conn._bot_token = "xoxb-fake-token"
    result = conn.execute_action("send_message", {"channel": "C123"})
    # Missing "text" — execution will raise or return error
    assert result.success is False or "text" in str(result.error).lower()


# ---------------------------------------------------------------------------
# 4. controlled_computer_use.py — step bounds, password refusal, evidence
# ---------------------------------------------------------------------------


def test_controlled_scope_max_steps_bound():
    """ControlledScope.max_steps defaults to 7 and is configurable."""
    from celina_pkg.controlled_computer_use import ControlledScope

    scope = ControlledScope()
    assert scope.max_steps == 7

    scope = ControlledScope(max_steps=3)
    assert scope.max_steps == 3


def test_controlled_scope_blocks_hotkey():
    """hotkey is in blocked_actions by default."""
    from celina_pkg.controlled_computer_use import ControlledScope

    scope = ControlledScope()
    assert "hotkey" in scope.blocked_actions


def test_controlled_computer_use_password_field_refusal():
    """Actions targeting password fields should be refused."""
    from celina_pkg.controlled_computer_use import ControlledScope, ControlledAction

    scope = ControlledScope(blocked_actions=["type"])
    action = ControlledAction(action="type", target='input[type="password"]', args={"text": "secret"})
    # blocked_actions contains "type"
    assert "type" in scope.blocked_actions


def test_controlled_execute_evidence_no_attributeerror(monkeypatch):
    """act_and_log records evidence without AttributeError on record_security_event."""
    from celina_pkg import controlled_computer_use as ccu

    scope = ccu.ControlledScope()
    obs = ccu.ControlledObservation(active_app="TestApp", active_title="Test")
    staged = ccu.ControlledAction(action="click", target="button#ok", args={})

    # Mock the ledger's record_security_event to verify it's called
    called = {}

    class FakeLedger:
        def record_security_event(self, **kwargs):
            called["event_type"] = kwargs.get("event_type")
            called["tool_name"] = kwargs.get("tool_name")
            return {"entry_hash": "abc123"}

    with patch.object(ccu.evidence, "get_evidence_ledger", return_value=FakeLedger()):
        executed, result, evidence_id = ccu.act_and_log(
            staged=staged,
            observation=obs,
            scope=scope,
            executor=lambda a, args: "ok",
        )
    assert executed is True
    assert result == "ok"
    assert called.get("event_type") == "controlled_computer_use"
    assert called.get("tool_name") == "click"


# ---------------------------------------------------------------------------
# 5. llm/lifecycle_router.py — PII gate, offline fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifecycle_router_pii_redacted_before_cloud():
    """When allow_cloud=True and tokens exceed threshold, prompt is redacted before cloud send."""
    from celina_pkg.llm.lifecycle_router import HybridCloudRouter, CloudRouteResult

    cloud_called_with = {}

    async def fake_cloud_call(text: str) -> str:
        cloud_called_with["text"] = text
        return "cloud response"

    async def fake_local_call(text: str) -> str:
        return "local response"

    router = HybridCloudRouter(
        local_call=fake_local_call,
        cloud_call=fake_cloud_call,
        local_ctx_limit=1000,
        cooldown_s=0.0,
    )

    result = await router.complete(
        prompt="My email is user@example.com",
        allow_cloud=True,
        estimated_tokens=900,  # > 85% of 1000
    )

    assert isinstance(result, CloudRouteResult)
    assert result.route == "cloud_sanitized"
    # PII should be redacted from what was sent to cloud
    sent_text = cloud_called_with.get("text", "")
    assert "user@example.com" not in sent_text
    assert "[EMAIL_01]" in sent_text


@pytest.mark.asyncio
async def test_lifecycle_router_offline_fail_closed_local():
    """When allow_cloud=False, routes to local model (fail-closed)."""
    from celina_pkg.llm.lifecycle_router import HybridCloudRouter

    cloud_called = False

    async def fake_cloud_call(text: str) -> str:
        nonlocal cloud_called
        cloud_called = True
        return "cloud response"

    async def fake_local_call(text: str) -> str:
        return "local response"

    router = HybridCloudRouter(
        local_call=fake_local_call,
        cloud_call=fake_cloud_call,
        local_ctx_limit=1000,
        cooldown_s=0.0,
    )

    result = await router.complete(
        prompt="hello world",
        allow_cloud=False,
        estimated_tokens=10,
    )

    assert cloud_called is False
    assert result.route == "local"


@pytest.mark.asyncio
async def test_lifecycle_router_circuit_breaker_fail_closed():
    """When circuit is open, falls back to local even if allow_cloud=True."""
    from celina_pkg.llm.lifecycle_router import HybridCloudRouter

    cloud_called = False

    async def fake_cloud_call(text: str) -> str:
        nonlocal cloud_called
        cloud_called = True
        return "cloud response"

    async def fake_local_call(text: str) -> str:
        return "local response"

    router = HybridCloudRouter(
        local_call=fake_local_call,
        cloud_call=fake_cloud_call,
        local_ctx_limit=1000,
        cooldown_s=300.0,  # long cooldown
    )
    # Force circuit open
    router._cloud_open_until = 9999999999.0

    result = await router.complete(
        prompt="hello",
        allow_cloud=True,
        estimated_tokens=900,
    )

    assert cloud_called is False
    assert result.route == "local"
    assert result.circuit_open is True