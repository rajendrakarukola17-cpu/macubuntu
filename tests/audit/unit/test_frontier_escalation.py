"""
tests/audit/unit/test_frontier_escalation.py

Phase 3 unit tests for celina_pkg.frontier_escalation — opt-in frontier
escalation MCP. Tests the should_escalate heuristic, dataclass models,
endpoint registration, and context sanitization.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg.frontier_escalation import (
    CloudEndpoint,
    EscalationRequest,
    EscalationResult,
    should_escalate,
    sanitize_escalation_context,
)


# ---------------------------------------------------------------------------
# should_escalate heuristic
# ---------------------------------------------------------------------------


def test_should_escalate_local_context_exhausted():
    """Local context >80% should always trigger escalation."""
    offer, reason = should_escalate(
        context_len_chars=1000,
        task_type="simple",
        local_ctx_used_pct=0.85,
    )
    assert offer is True
    assert "local context exhausted" in reason


def test_should_escalate_large_context():
    """Context >200K chars should trigger escalation."""
    offer, reason = should_escalate(
        context_len_chars=250_000,
        task_type="simple",
        local_ctx_used_pct=0.1,
    )
    assert offer is True
    assert "large context" in reason


def test_should_escalate_high_stakes_task():
    """High-stakes task types should trigger escalation."""
    for task_type in {"legal", "technical_drafting", "multi_file_refactor", "high_stakes"}:
        offer, reason = should_escalate(
            context_len_chars=1000,
            task_type=task_type,
            local_ctx_used_pct=0.1,
        )
        assert offer is True, f"task_type={task_type} should escalate"
        assert task_type in reason


def test_should_escalate_not_needed():
    """Small context + low-stakes task + low local usage should not escalate."""
    offer, reason = should_escalate(
        context_len_chars=5000,
        task_type="simple",
        local_ctx_used_pct=0.3,
    )
    assert offer is False
    assert "local handling sufficient" in reason


def test_should_escalate_boundary_200k():
    """Exactly 200,000 chars should NOT trigger large-context escalation."""
    offer, reason = should_escalate(
        context_len_chars=200_000,
        task_type="simple",
        local_ctx_used_pct=0.1,
    )
    assert offer is False


def test_should_escalate_boundary_80pct():
    """Exactly 80% local context should NOT trigger exhaustion escalation."""
    offer, reason = should_escalate(
        context_len_chars=1000,
        task_type="simple",
        local_ctx_used_pct=0.80,
    )
    assert offer is False


# ---------------------------------------------------------------------------
# Dataclass models
# ---------------------------------------------------------------------------


def test_cloud_endpoint_defaults():
    ep = CloudEndpoint(name="test", base_url="https://example.com/v1", model="gpt-4")
    assert ep.name == "test"
    assert ep.enabled is False
    assert ep.requires_approval is True
    assert ep.max_tokens == 1_048_576


def test_escalation_request_defaults():
    req = EscalationRequest(
        task_id="task-1",
        goal="summarize",
        context="some context",
        endpoint="test-endpoint",
        model="gpt-4",
        estimated_tokens=500,
        reason="needs more tokens",
    )
    assert req.task_id == "task-1"
    assert req.approved is False
    assert req.source_trust == "untrusted_web"  # mcp_guard.DEFAULT_SOURCE_TRUST


def test_escalation_result_required_fields():
    res = EscalationResult(ok=False, task_id="task-1", endpoint="ep", model="gpt-4")
    assert res.ok is False
    assert res.response == ""
    assert res.tokens_used == 0


# ---------------------------------------------------------------------------
# sanitize_escalation_context
# ---------------------------------------------------------------------------


def test_sanitize_untrusted_source_flagged():
    """Untrusted source context gets [UNTRUSTED_SOURCE] prefix."""
    sanitized, _intent = sanitize_escalation_context(
        raw_context="Contact me at user@example.com",
        source_trust="untrusted_web",
    )
    assert "[UNTRUSTED_SOURCE]" in sanitized


def test_sanitize_untrusted_source_keeps_text():
    """Untrusted source context keeps text but gets [UNTRUSTED_SOURCE] prefix."""
    sanitized, _intent = sanitize_escalation_context(
        raw_context="This is a safe document about cats.",
        source_trust="untrusted_web",
    )
    assert "[UNTRUSTED_SOURCE]" in sanitized
    assert "cats" in sanitized


def test_sanitize_preserves_safe_text():
    sanitized, _intent = sanitize_escalation_context(
        raw_context="This is a safe document about cats.",
    )
    assert "cats" in sanitized


# ---------------------------------------------------------------------------
# Endpoint registration
# ---------------------------------------------------------------------------


def test_register_and_list_endpoint(tmp_path, monkeypatch):
    from celina_pkg import frontier_escalation as fe

    monkeypatch.setattr(fe, "ENDPOINTS_FILE", tmp_path / "endpoints.json")
    monkeypatch.setattr(fe.net_policy, "default_dns_resolver", lambda _host, _port: ["93.184.216.34"])
    monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live DNS forbidden")))

    ep = fe.register_endpoint(
        name="astra",
        base_url="https://example.com/v1",
        model="gpt-6.2",
    )

    endpoints = fe.list_endpoints()
    assert len(endpoints) == 1
    assert endpoints[0].name == "astra"
    assert endpoints[0].enabled is False


def test_enable_endpoint(tmp_path, monkeypatch):
    from celina_pkg import frontier_escalation as fe

    monkeypatch.setattr(fe, "ENDPOINTS_FILE", tmp_path / "endpoints.json")
    monkeypatch.setattr(fe.net_policy, "default_dns_resolver", lambda _host, _port: ["93.184.216.34"])
    monkeypatch.setattr("socket.getaddrinfo", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live DNS forbidden")))

    fe.register_endpoint(
        name="astra",
        base_url="https://example.com/v1",
        model="gpt-6.2",
    )

    ok = fe.enable_endpoint("astra", enabled=True)
    assert ok is True

    endpoints = fe.list_endpoints()
    assert endpoints[0].enabled is True


def test_enable_unknown_endpoint_returns_false(tmp_path, monkeypatch):
    from celina_pkg import frontier_escalation as fe

    monkeypatch.setattr(fe, "ENDPOINTS_FILE", tmp_path / "endpoints.json")

    ok = fe.enable_endpoint("nonexistent", enabled=True)
    assert ok is False


def test_register_blocked_url_raises(tmp_path, monkeypatch):
    from celina_pkg import frontier_escalation as fe

    monkeypatch.setattr(fe, "ENDPOINTS_FILE", tmp_path / "endpoints.json")

    try:
        fe.register_endpoint(
            name="local",
            base_url="http://127.0.0.1:9999/v1",
            model="gpt-4",
        )
        assert False, "should have raised ValueError for loopback URL"
    except ValueError:
        pass  # expected — loopback blocked by net_policy