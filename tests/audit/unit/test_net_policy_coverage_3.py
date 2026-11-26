"""
tests/audit/unit/test_net_policy_coverage_3.py

Phase 7d — TASK 1: net_policy.py branch coverage tests (round 3).
Targets remaining branches: guarded_call url kwarg, SafeHTTPResponse.json error,
validate_iot_url IP literal, enforce_sensitive_egress allowed_destinations,
_check_peer_ip non-public policy, safe_request redirect follow.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg.net_policy import (
    URLPolicyError,
    OfflineError,
    SafeHTTPResponse,
    check_web_comms_allowed,
    default_dns_resolver,
    enforce_sensitive_egress,
    guarded_call,
    is_forbidden_public_ip,
    is_online,
    is_web_comms_tool,
    load_iot_allowlist,
    safe_get,
    safe_request,
    validate_public_url,
    validate_iot_url,
    validate_web_comms_host,
    _check_peer_ip,
)


# ---------------------------------------------------------------------------
# guarded_call: url kwarg and URL hint from call_args
# ---------------------------------------------------------------------------


def test_guarded_call_url_kwarg(monkeypatch):
    """CB: guarded_call extracts url from kwargs."""
    monkeypatch.setattr("celina_pkg.config.is_web_comms_enabled", lambda: True)
    monkeypatch.setattr("celina_pkg.config.TOOL_DOMAINS", {"web_comms": {"tools": ["web_search"]}})
    monkeypatch.setattr("celina_pkg.net_policy.WEB_COMMS_DEFAULT_TOOLS", frozenset(["web_search"]))
    monkeypatch.setattr("celina_pkg.net_policy.is_online", lambda *a, **k: True)
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: [])
    import celina_pkg.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_AUTO_TOOL_MAP", {"web_search": lambda **kw: "result"})
    monkeypatch.setattr(tools_mod, "_APPROVAL_TOOL_MAP", {})
    result = guarded_call("web_search", url="https://example.com/")
    assert result == "result"


def test_guarded_call_url_hint_from_call_args(monkeypatch):
    """CB: guarded_call extracts url from first string arg starting with http."""
    monkeypatch.setattr("celina_pkg.config.is_web_comms_enabled", lambda: True)
    monkeypatch.setattr("celina_pkg.config.TOOL_DOMAINS", {"web_comms": {"tools": ["web_search"]}})
    monkeypatch.setattr("celina_pkg.net_policy.WEB_COMMS_DEFAULT_TOOLS", frozenset(["web_search"]))
    monkeypatch.setattr("celina_pkg.net_policy.is_online", lambda *a, **k: True)
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: [])
    import celina_pkg.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_AUTO_TOOL_MAP", {"web_search": lambda *a, **kw: "result"})
    monkeypatch.setattr(tools_mod, "_APPROVAL_TOOL_MAP", {})
    result = guarded_call("web_search", "https://example.com/")
    assert result == "result"


# ---------------------------------------------------------------------------
# SafeHTTPResponse.json error path
# ---------------------------------------------------------------------------


def test_safe_http_response_json_invalid():
    """CB: SafeHTTPResponse.json() raises on invalid JSON."""
    resp = SafeHTTPResponse(
        url="https://example.com/",
        status_code=200,
        content_type="application/json",
        body=b"not json",
        headers={},
    )
    with pytest.raises(ValueError):
        resp.json()


# ---------------------------------------------------------------------------
# validate_iot_url: direct IP literal host
# ---------------------------------------------------------------------------


def test_validate_iot_url_ip_literal_host(monkeypatch):
    """CB: validate_iot_url handles IP literal as host."""
    allowlist = {"allowed_hosts": ["192.168.1.100"], "allowed_ports": [8123], "allowed_methods": ["POST"]}
    result = validate_iot_url("http://192.168.1.100:8123/", "POST", allowlist)
    assert result.host == "192.168.1.100"
    assert result.port == 8123


# ---------------------------------------------------------------------------
# enforce_sensitive_egress: allowed_destinations match
# ---------------------------------------------------------------------------


def test_enforce_sensitive_egress_allowed_destinations_match(monkeypatch):
    """CB: enforce_sensitive_egress passes when host in allowed_destinations."""
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: [])
    # Should not raise since example.com is in allowed_destinations
    enforce_sensitive_egress("https://example.com/", allowed_destinations=["example.com"])


# ---------------------------------------------------------------------------
# _check_peer_ip: non-public policy (is_public=False)
# ---------------------------------------------------------------------------


def test_check_peer_ip_non_public_no_check(monkeypatch):
    """CB: _check_peer_ip skips forbidden IP check when is_public=False."""
    resp = MagicMock()
    resp.raw = None
    # Should not raise even with forbidden peer IP since is_public=False
    _check_peer_ip(resp, ("8.8.8.8",), is_public=False, fail_closed=False)


# ---------------------------------------------------------------------------
# safe_request: redirect follow path
# ---------------------------------------------------------------------------


def test_safe_request_redirect_followed(monkeypatch):
    """CB: safe_request follows redirect when allow_redirects=True."""
    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        # First response: 302 redirect
        resp1 = MagicMock()
        resp1.status_code = 302
        resp1.headers = {"Location": "https://example.com/new"}
        resp1.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)

        # Second response: 200 OK
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.headers = {"Content-Type": "text/plain"}
        resp2.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)
        resp2.iter_content.return_value = [b"redirected content"]

        mock_validate.side_effect = [
            MagicMock(url="https://example.com/", resolved_ips=("93.184.216.34",)),
            MagicMock(url="https://example.com/new", resolved_ips=("93.184.216.34",)),
        ]

        with patch("celina_pkg.net_policy.requests.request", side_effect=[resp1, resp2]):
            result = safe_get("https://example.com/", allow_redirects=True, max_redirects=5)
            assert result.status_code == 200
            assert "redirected content" in result.text


def test_safe_request_redirect_exceeds_cap(monkeypatch):
    """CB: safe_request raises when redirects exceed cap."""
    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        resp = MagicMock()
        resp.status_code = 302
        resp.headers = {"Location": "https://evil.com/"}
        resp.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)
        mock_validate.return_value = MagicMock(url="https://example.com/", resolved_ips=("93.184.216.34",))

        with patch("celina_pkg.net_policy.requests.request", return_value=resp):
            with pytest.raises(URLPolicyError, match="Redirects are blocked"):
                safe_get("https://example.com/", allow_redirects=True, max_redirects=0)


def test_safe_request_redirect_missing_location(monkeypatch):
    """CB: safe_request raises when redirect response missing Location header."""
    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        resp = MagicMock()
        resp.status_code = 301
        resp.headers = {}
        resp.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)
        mock_validate.return_value = MagicMock(url="https://example.com/", resolved_ips=("93.184.216.34",))

        with patch("celina_pkg.net_policy.requests.request", return_value=resp):
            with pytest.raises(URLPolicyError, match="missing Location header"):
                safe_get("https://example.com/", allow_redirects=True, max_redirects=5)


# ---------------------------------------------------------------------------
# validate_public_url: port ValueError branch
# ---------------------------------------------------------------------------


def test_validate_public_url_invalid_port():
    """CB: validate_public_url raises on invalid port in URL."""
    with pytest.raises(URLPolicyError, match="Invalid URL port"):
        validate_public_url("https://example.com:notaport/", resolver=lambda h, p: ["93.184.216.34"])


# ---------------------------------------------------------------------------
# default_dns_resolver: gaierror path
# ---------------------------------------------------------------------------


def test_default_dns_resolver_gaierror(monkeypatch):
    """CB: default_dns_resolver raises URLPolicyError on DNS resolution failure."""
    import socket
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS failed")):
        with pytest.raises(URLPolicyError, match="DNS resolution failed"):
            default_dns_resolver("nonexistent.example.invalid", 443)


# ---------------------------------------------------------------------------
# is_forbidden_public_ip: IPv4-mapped IPv6
# ---------------------------------------------------------------------------


def test_is_forbidden_public_ip_ipv4_mapped_ipv6_loopback():
    """CB: is_forbidden_public_ip catches IPv4-mapped IPv6 loopback."""
    bad, reason = is_forbidden_public_ip("::ffff:127.0.0.1")
    assert bad is True
    assert "loopback" in reason.lower() or "forbidden" in reason.lower()


def test_is_forbidden_public_ip_ipv4_mapped_ipv6_metadata():
    """CB: is_forbidden_public_ip catches IPv4-mapped IPv6 metadata."""
    bad, reason = is_forbidden_public_ip("::ffff:169.254.169.254")
    assert bad is True
    assert "metadata" in reason.lower()

