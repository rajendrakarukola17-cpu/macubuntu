"""
tests/audit/unit/test_tools_coverage_8.py

Phase 7d — TASK 1a: tools.py branch coverage tests (round 8).
Targets transcribe_system_audio_tool, set_assistant_identity,
save_workspace_snapshot, trigger_iot_webhook.

No real network calls. Mocks voice, config, automation, skills, net_policy,
approval, and json modules.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error="", message="", timed_out=False, returncode=0, status_code=200, text=""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.message = message
        self.timed_out = timed_out
        self.returncode = returncode
        self.status_code = status_code
        self.text = text


def _always_approve(action, params, cb=None, title="", target=""):
    class Token:
        pass
    return Token(), None


# ---------------------------------------------------------------------------
# transcribe_system_audio_tool
# ---------------------------------------------------------------------------


def test_transcribe_system_audio_tool_approval_denied():
    """CB: transcribe_system_audio_tool rejects when approval denied."""
    result = tools.transcribe_system_audio_tool(approval_cb=lambda *a, **k: False)
    assert "Cancelled" in result or "denied" in result.lower() or "Error" in result


def test_transcribe_system_audio_tool_success(monkeypatch):
    """CB: transcribe_system_audio_tool calls voice.transcribe_system_audio."""
    import celina_pkg.voice as voice_mod
    monkeypatch.setattr(voice_mod, "transcribe_system_audio", lambda **k: "Transcription result")
    result = tools.transcribe_system_audio_tool(
        seconds=30, lang_hint="en", approval_cb=_always_approve
    )
    assert "Transcription result" in result


def test_transcribe_system_audio_tool_clamped_seconds(monkeypatch):
    """CB: transcribe_system_audio_tool clamps seconds to 3-120."""
    import celina_pkg.voice as voice_mod
    captured = {}
    monkeypatch.setattr(voice_mod, "transcribe_system_audio", lambda **k: captured.update(k) or "done")
    tools.transcribe_system_audio_tool(seconds=200, approval_cb=_always_approve)
    assert captured.get("seconds") == 120


# ---------------------------------------------------------------------------
# set_assistant_identity
# ---------------------------------------------------------------------------


def test_set_assistant_identity_success(monkeypatch):
    """CB: set_assistant_identity updates identity successfully."""
    monkeypatch.setattr(tools.config, "set_assistant_identity", lambda name=None, nickname=None: ("Celina", None))
    result = tools.set_assistant_identity("Celina")
    assert "Identity updated" in result
    assert "Celina" in result


def test_set_assistant_identity_with_nickname(monkeypatch):
    """CB: set_assistant_identity with nickname."""
    monkeypatch.setattr(tools.config, "set_assistant_identity", lambda name=None, nickname=None: ("Celly", "Celly"))
    result = tools.set_assistant_identity("Celly", nickname="Celly")
    assert "nickname" in result.lower()


def test_set_assistant_identity_persistence_error(monkeypatch):
    """CB: set_assistant_identity handles PersistenceError."""
    monkeypatch.setattr(tools.config, "set_assistant_identity", lambda name=None, nickname=None: (_ for _ in ()).throw(tools.config.PersistenceError("disk full")))
    result = tools.set_assistant_identity("Celina")
    assert "Identity NOT saved" in result


def test_set_assistant_identity_value_error(monkeypatch):
    """CB: set_assistant_identity handles ValueError."""
    monkeypatch.setattr(tools.config, "set_assistant_identity", lambda name=None, nickname=None: (_ for _ in ()).throw(ValueError("bad name")))
    result = tools.set_assistant_identity("")
    assert "Error:" in result


# ---------------------------------------------------------------------------
# save_workspace_snapshot
# ---------------------------------------------------------------------------


def test_save_workspace_snapshot_empty_name():
    """CB: save_workspace_snapshot rejects empty name."""
    result = tools.save_workspace_snapshot("")
    assert "Provide a workspace snapshot name" in result


def test_save_workspace_snapshot_no_apps(monkeypatch):
    """CB: save_workspace_snapshot with no visible apps."""
    monkeypatch.setattr(tools.automation, "list_visible_workspace_apps", lambda: [])
    result = tools.save_workspace_snapshot("Test")
    assert "No visible desktop applications" in result


def test_save_workspace_snapshot_success(monkeypatch):
    """CB: save_workspace_snapshot captures apps and saves skill."""
    monkeypatch.setattr(tools.automation, "list_visible_workspace_apps", lambda: [
        {"app": "firefox", "title": "Home", "doc_path": ""},
        {"app": "code", "title": "Project", "doc_path": ""},
    ])
    monkeypatch.setattr(tools.skills, "learn_skill", lambda **k: "Skill saved")
    result = tools.save_workspace_snapshot("Coding")
    assert "Saved Workspace Snapshot" in result
    assert "firefox" in result
    assert "code" in result


def test_save_workspace_snapshot_with_apps_arg(monkeypatch):
    """CB: save_workspace_snapshot with explicit apps list."""
    monkeypatch.setattr(tools.skills, "learn_skill", lambda **k: "Skill saved")
    result = tools.save_workspace_snapshot("Test", apps=["firefox", "code"])
    assert "firefox" in result
    assert "code" in result


def test_save_workspace_snapshot_triggers(monkeypatch):
    """CB: save_workspace_snapshot adds default triggers."""
    monkeypatch.setattr(tools.automation, "list_visible_workspace_apps", lambda: [
        {"app": "firefox", "title": "Home", "doc_path": ""},
    ])
    captured_triggers = []
    monkeypatch.setattr(tools.skills, "learn_skill", lambda name=None, steps=None, triggers=None, description=None: captured_triggers.extend(triggers) or "saved")
    tools.save_workspace_snapshot("Test", triggers="custom1,custom2")
    assert "custom1" in captured_triggers
    assert "custom2" in captured_triggers
    assert "test workspace" in captured_triggers


# ---------------------------------------------------------------------------
# trigger_iot_webhook
# ---------------------------------------------------------------------------


def test_trigger_iot_webhook_blocked_by_policy(monkeypatch):
    """CB: trigger_iot_webhook blocked by IoT network policy."""
    class URLPolicyError(Exception):
        pass
    monkeypatch.setattr(tools.net_policy, "URLPolicyError", URLPolicyError)
    monkeypatch.setattr(tools.net_policy, "load_iot_allowlist", lambda path=None: [])
    def validate(url, method, **k):
        raise URLPolicyError("not allowlisted")
    monkeypatch.setattr(tools.net_policy, "validate_iot_url", validate)
    result = tools.trigger_iot_webhook("https://evil.example.com/webhook")
    assert "Blocked by IoT network policy" in result


def test_trigger_iot_webhook_invalid_payload(monkeypatch):
    """CB: trigger_iot_webhook rejects invalid JSON payload."""
    result = tools.trigger_iot_webhook("https://example.com/webhook", payload_json="not json{")
    assert "Invalid payload_json" in result


def test_trigger_iot_webhook_payload_not_dict(monkeypatch):
    """CB: trigger_iot_webhook rejects non-dict JSON payload."""
    result = tools.trigger_iot_webhook("https://example.com/webhook", payload_json='[1,2,3]')
    assert "payload_json must be a JSON object" in result


def test_trigger_iot_webhook_approval_denied(monkeypatch):
    """CB: trigger_iot_webhook rejects when approval denied."""
    monkeypatch.setattr(tools.net_policy, "load_iot_allowlist", lambda path=None: [])
    monkeypatch.setattr(tools.net_policy, "validate_iot_url", lambda url, method, **k: None)
    result = tools.trigger_iot_webhook(
        "https://example.com/webhook",
        approval_cb=lambda *a, **k: False,
    )
    assert "Cancelled" in result or "denied" in result.lower() or "Error" in result


def test_trigger_iot_webhook_success(monkeypatch):
    """CB: trigger_iot_webhook succeeds."""
    monkeypatch.setattr(tools.net_policy, "load_iot_allowlist", lambda path=None: [])
    monkeypatch.setattr(tools.net_policy, "validate_iot_url", lambda url, method, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_request", lambda method, url, **k: _FakeResult(status_code=200, text="OK"))
    monkeypatch.setattr(tools.api_actuators, "_record_api_evidence", MagicMock())
    result = tools.trigger_iot_webhook(
        "https://example.com/webhook",
        payload_json='{"key": "value"}',
        approval_cb=_always_approve,
    )
    assert "HTTP 200" in result


def test_trigger_iot_webhook_get_method(monkeypatch):
    """CB: trigger_iot_webhook with GET method (no json body)."""
    monkeypatch.setattr(tools.net_policy, "load_iot_allowlist", lambda path=None: [])
    monkeypatch.setattr(tools.net_policy, "validate_iot_url", lambda url, method, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_request", lambda method, url, **k: _FakeResult(status_code=200, text="OK"))
    monkeypatch.setattr(tools.api_actuators, "_record_api_evidence", MagicMock())
    result = tools.trigger_iot_webhook(
        "https://example.com/webhook",
        method="GET",
        approval_cb=_always_approve,
    )
    assert "HTTP 200" in result
