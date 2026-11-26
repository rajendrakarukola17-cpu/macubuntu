"""
tests/audit/unit/test_net_policy_coverage_2.py

Phase 7d — TASK 1: net_policy.py branch coverage tests (round 2).
Targets: is_online, is_web_comms_tool, validate_web_comms_host,
check_web_comms_allowed, guarded_call, SafeHTTPResponse, load_iot_allowlist,
validate_iot_url URL validation branches, enforce_sensitive_egress,
safe_request/safe_get request paths.

No real network calls — mocks socket, requests, config, privacy.
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


class _FakeSocket:
    """Context-manager fake for socket.create_connection."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_mock_response(peer_ip, has_sock=True):
    """Build a mock requests.Response with a socket returning peer_ip."""
    if not has_sock:
        resp = MagicMock()
        resp.raw = None
        return resp
    mock_sock = MagicMock()
    mock_sock.getpeername.return_value = (peer_ip, 12345)
    mock_conn = MagicMock()
    mock_conn.sock = mock_sock
    mock_raw = MagicMock()
    mock_raw._connection = mock_conn
    resp = MagicMock()
    resp.raw = mock_raw
    return resp


# ---------------------------------------------------------------------------
# is_online
# ---------------------------------------------------------------------------


def test_is_online_true(monkeypatch):
    """CB: is_online returns True when socket.create_connection succeeds."""
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: _FakeSocket())
    assert is_online("1.1.1.1", 443, 2.0) is True


def test_is_online_false(monkeypatch):
    """CB: is_online returns False when socket.create_connection raises OSError."""
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("net down")))
    assert is_online("1.1.1.1", 443, 2.0) is False


# ---------------------------------------------------------------------------
# is_web_comms_tool / validate_web_comms_host
# ---------------------------------------------------------------------------


def test_is_web_comms_tool_known(monkeypatch):
    """CB: is_web_comms_tool returns True for known web_comms tool."""
    # WEB_COMMS_DEFAULT_TOOLS includes "web_search" by default
    assert is_web_comms_tool("web_search") is True


def test_is_web_comms_tool_unknown(monkeypatch):
    """CB: is_web_comms_tool returns False for unknown tool."""
    assert is_web_comms_tool("some_random_tool_xyz") is False


def test_validate_web_comms_host_empty_allowlist(monkeypatch):
    """CB: validate_web_comms_host returns early when allowlist empty."""
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: [])
    # Should not raise
    validate_web_comms_host("https://anything.com/")


def test_validate_web_comms_host_match(monkeypatch):
    """CB: validate_web_comms_host passes when host matches allowlist."""
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: ["example.com"])
    validate_web_comms_host("https://example.com/page")


def test_validate_web_comms_host_subdomain_match(monkeypatch):
    """CB: validate_web_comms_host passes for subdomain of allowed host."""
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: ["example.com"])
    validate_web_comms_host("https://api.example.com/page")


def test_validate_web_comms_host_no_match(monkeypatch):
    """CB: validate_web_comms_host raises when host not in allowlist."""
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: ["example.com"])
    with pytest.raises(URLPolicyError, match="not in web_comms.allowed_hosts"):
        validate_web_comms_host("https://evil.com/page")


def test_validate_web_comms_host_no_hostname(monkeypatch):
    """CB: validate_web_comms_host raises when URL missing hostname."""
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: ["example.com"])
    with pytest.raises(URLPolicyError, match="missing hostname"):
        validate_web_comms_host("https:///")


# ---------------------------------------------------------------------------
# check_web_comms_allowed
# ---------------------------------------------------------------------------


def test_check_web_comms_allowed_non_web_comms_tool(monkeypatch):
    """CB: check_web_comms_allowed returns early for non-web_comms tool."""
    monkeypatch.setattr("celina_pkg.config.TOOL_DOMAINS", {"web_comms": {"tools": []}})
    monkeypatch.setattr("celina_pkg.net_policy.WEB_COMMS_DEFAULT_TOOLS", frozenset())
    # Should not raise
    check_web_comms_allowed("some_random_tool", "https://example.com/")


def test_check_web_comms_allowed_disabled(monkeypatch):
    """CB: check_web_comms_allowed raises OfflineError when web_comms disabled."""
    monkeypatch.setattr("celina_pkg.config.is_web_comms_enabled", lambda: False)
    monkeypatch.setattr("celina_pkg.config.TOOL_DOMAINS", {"web_comms": {"tools": ["web_search"]}})
    monkeypatch.setattr("celina_pkg.net_policy.WEB_COMMS_DEFAULT_TOOLS", frozenset(["web_search"]))
    with pytest.raises(OfflineError, match="disabled"):
        check_web_comms_allowed("web_search", "https://example.com/")


def test_check_web_comms_allowed_offline(monkeypatch):
    """CB: check_web_comms_allowed raises OfflineError when offline."""
    monkeypatch.setattr("celina_pkg.config.is_web_comms_enabled", lambda: True)
    monkeypatch.setattr("celina_pkg.config.TOOL_DOMAINS", {"web_comms": {"tools": ["web_search"]}})
    monkeypatch.setattr("celina_pkg.net_policy.WEB_COMMS_DEFAULT_TOOLS", frozenset(["web_search"]))
    monkeypatch.setattr("celina_pkg.net_policy.is_online", lambda *a, **k: False)
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: [])
    with pytest.raises(OfflineError, match="offline"):
        check_web_comms_allowed("web_search", "https://example.com/")


def test_check_web_comms_allowed_online_ok(monkeypatch):
    """CB: check_web_comms_allowed passes when online and enabled."""
    monkeypatch.setattr("celina_pkg.config.is_web_comms_enabled", lambda: True)
    monkeypatch.setattr("celina_pkg.config.TOOL_DOMAINS", {"web_comms": {"tools": ["web_search"]}})
    monkeypatch.setattr("celina_pkg.net_policy.WEB_COMMS_DEFAULT_TOOLS", frozenset(["web_search"]))
    monkeypatch.setattr("celina_pkg.net_policy.is_online", lambda *a, **k: True)
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: [])
    # Should not raise
    check_web_comms_allowed("web_search", "https://example.com/")


# ---------------------------------------------------------------------------
# SafeHTTPResponse
# ---------------------------------------------------------------------------


def test_safe_http_response_text_and_json():
    """CB: SafeHTTPResponse.text decodes body, .json() parses JSON."""
    resp = SafeHTTPResponse(
        url="https://example.com/",
        status_code=200,
        content_type="application/json",
        body=b'{"key": "value"}',
        headers={},
    )
    assert resp.text == '{"key": "value"}'
    assert resp.json() == {"key": "value"}


# ---------------------------------------------------------------------------
# guarded_call
# ---------------------------------------------------------------------------


def test_guarded_call_web_comms_disabled(monkeypatch):
    """CB: guarded_call raises OfflineError for web_comms tool when disabled."""
    monkeypatch.setattr("celina_pkg.config.is_web_comms_enabled", lambda: False)
    monkeypatch.setattr("celina_pkg.config.TOOL_DOMAINS", {"web_comms": {"tools": ["web_search"]}})
    monkeypatch.setattr("celina_pkg.net_policy.WEB_COMMS_DEFAULT_TOOLS", frozenset(["web_search"]))
    with pytest.raises(OfflineError):
        guarded_call("web_search", lambda: "ok")


def test_guarded_call_unknown_tool_keyerror(monkeypatch):
    """CB: guarded_call raises KeyError for unknown web_comms tool."""
    import celina_pkg.tools as tools_mod

    monkeypatch.setattr("celina_pkg.config.is_web_comms_enabled", lambda: True)
    monkeypatch.setattr("celina_pkg.config.TOOL_DOMAINS", {"web_comms": {"tools": ["web_search"]}})
    monkeypatch.setattr("celina_pkg.net_policy.WEB_COMMS_DEFAULT_TOOLS", frozenset(["web_search"]))
    monkeypatch.setattr("celina_pkg.net_policy.is_online", lambda *a, **k: True)
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: [])
    monkeypatch.setattr(tools_mod, "_AUTO_TOOL_MAP", {})
    monkeypatch.setattr(tools_mod, "_APPROVAL_TOOL_MAP", {})
    with pytest.raises(KeyError, match="Unknown tool"):
        guarded_call("nonexistent_tool_xyz", online_probe=lambda: True)


# ---------------------------------------------------------------------------
# load_iot_allowlist
# ---------------------------------------------------------------------------


def test_load_iot_allowlist_missing_file(tmp_path):
    """CB: load_iot_allowlist returns default when file doesn't exist."""
    result = load_iot_allowlist(tmp_path / "nonexistent.json")
    assert result == {"allowed_hosts": [], "allowed_ports": [8123, 1880], "allowed_methods": ["POST"]}


def test_load_iot_allowlist_invalid_json(tmp_path):
    """CB: load_iot_allowlist returns default when JSON is invalid."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    result = load_iot_allowlist(bad)
    assert result["allowed_hosts"] == []
    assert result["allowed_ports"] == [8123, 1880]


def test_load_iot_allowlist_valid(tmp_path):
    """CB: load_iot_allowlist parses valid JSON allowlist."""
    good = tmp_path / "good.json"
    good.write_text('{"allowed_hosts": ["myhome.local"], "allowed_ports": [8123], "allowed_methods": ["POST", "GET"]}')
    result = load_iot_allowlist(good)
    assert "myhome.local" in result["allowed_hosts"]
    assert result["allowed_ports"] == [8123]
    assert "GET" in result["allowed_methods"]


def test_load_iot_allowlist_not_dict(tmp_path):
    """CB: load_iot_allowlist returns default when JSON is not a dict."""
    not_dict = tmp_path / "notdict.json"
    not_dict.write_text("[1, 2, 3]")
    result = load_iot_allowlist(not_dict)
    assert result == {"allowed_hosts": [], "allowed_ports": [8123, 1880], "allowed_methods": ["POST"]}


# ---------------------------------------------------------------------------
# validate_iot_url: scheme / credentials / port / method / host branches
# ---------------------------------------------------------------------------


def test_validate_iot_url_bad_scheme():
    """CB: validate_iot_url rejects non-http(s) scheme."""
    allowlist = {"allowed_hosts": ["example.com"], "allowed_ports": [8123], "allowed_methods": ["POST"]}
    with pytest.raises(URLPolicyError, match="must use http"):
        validate_iot_url("ftp://example.com/", "POST", allowlist)


def test_validate_iot_url_credentials():
    """CB: validate_iot_url rejects embedded credentials."""
    allowlist = {"allowed_hosts": ["example.com"], "allowed_ports": [8123], "allowed_methods": ["POST"]}
    with pytest.raises(URLPolicyError, match="embedded credentials"):
        validate_iot_url("http://user:pass@example.com/", "POST", allowlist)


def test_validate_iot_url_metadata_host():
    """CB: validate_iot_url rejects metadata host."""
    allowlist = {"allowed_hosts": ["169.254.169.254"], "allowed_ports": [80], "allowed_methods": ["GET"]}
    with pytest.raises(URLPolicyError, match="Cloud metadata"):
        validate_iot_url("http://169.254.169.254/", "GET", allowlist)


def test_validate_iot_url_ollama_port():
    """CB: validate_iot_url blocks Ollama ports 11434/11435."""
    allowlist = {"allowed_hosts": ["example.com"], "allowed_ports": [11434], "allowed_methods": ["POST"]}
    with pytest.raises(URLPolicyError, match="11434"):
        validate_iot_url("http://example.com:11434/", "POST", allowlist)


def test_validate_iot_url_method_not_allowed():
    """CB: validate_iot_url rejects method not in allowlist."""
    allowlist = {"allowed_hosts": ["example.com"], "allowed_ports": [8123], "allowed_methods": ["POST"]}
    with pytest.raises(URLPolicyError, match="not in IoT allowlist"):
        validate_iot_url("http://example.com:8123/", "DELETE", allowlist)


def test_validate_iot_url_host_not_allowed():
    """CB: validate_iot_url rejects host not in allowlist."""
    allowlist = {"allowed_hosts": ["allowed.com"], "allowed_ports": [8123], "allowed_methods": ["POST"]}
    with pytest.raises(URLPolicyError, match="not in"):
        validate_iot_url("http://evil.com:8123/", "POST", allowlist)


def test_validate_iot_url_port_not_allowed():
    """CB: validate_iot_url rejects port not in allowlist."""
    allowlist = {"allowed_hosts": ["example.com"], "allowed_ports": [8123], "allowed_methods": ["POST"]}
    with pytest.raises(URLPolicyError, match="Port 443 is not in IoT allowlist"):
        validate_iot_url("https://example.com/", "POST", allowlist)


def test_validate_iot_url_resolved_metadata_ip():
    """CB: validate_iot_url rejects resolved metadata IP."""
    allowlist = {"allowed_hosts": ["evil.com"], "allowed_ports": [8123], "allowed_methods": ["POST"]}
    with patch("celina_pkg.net_policy.default_dns_resolver", return_value=["169.254.169.254"]):
        with pytest.raises(URLPolicyError, match="forbidden"):
            validate_iot_url("http://evil.com:8123/", "POST", allowlist)


def test_validate_iot_url_success(monkeypatch):
    """CB: validate_iot_url returns ValidatedURL on success."""
    allowlist = {"allowed_hosts": ["example.com"], "allowed_ports": [8123], "allowed_methods": ["POST"]}
    with patch("celina_pkg.net_policy.default_dns_resolver", return_value=["93.184.216.34"]):
        result = validate_iot_url("http://example.com:8123/", "POST", allowlist)
        assert result.host == "example.com"
        assert result.port == 8123


# ---------------------------------------------------------------------------
# enforce_sensitive_egress
# ---------------------------------------------------------------------------


def test_enforce_sensitive_egress_confidential():
    """CB: enforce_sensitive_egress blocks CONFIDENTIAL classification."""
    with pytest.raises(URLPolicyError, match="Fail-closed egress block"):
        enforce_sensitive_egress("https://example.com/", classification="CONFIDENTIAL")


def test_enforce_sensitive_egress_secret():
    """CB: enforce_sensitive_egress blocks SECRET classification."""
    with pytest.raises(URLPolicyError, match="Fail-closed egress block"):
        enforce_sensitive_egress("https://example.com/", classification="SECRET")


def test_enforce_sensitive_egress_public_ok():
    """CB: enforce_sensitive_egress allows PUBLIC classification."""
    # Should not raise
    enforce_sensitive_egress("https://example.com/", classification="PUBLIC")


def test_enforce_sensitive_egress_secret_payload(monkeypatch):
    """CB: enforce_sensitive_egress blocks payload with secrets."""
    fake_vault = MagicMock()
    fake_vault.redact.return_value = ("", MagicMock(has_secrets=True, has_pii=False, summary="secrets found"))
    monkeypatch.setattr("celina_pkg.privacy.RedactionVault", lambda: fake_vault)
    with pytest.raises(URLPolicyError, match="sensitive-data egress block"):
        enforce_sensitive_egress("https://example.com/", payload_text="sk-1234567890abcdef")


def test_enforce_sensitive_egress_allowed_destinations(monkeypatch):
    """CB: enforce_sensitive_egress validates against allowed_destinations."""
    monkeypatch.setattr("celina_pkg.config.get_web_comms_allowed_hosts", lambda: [])
    # Should not raise since example.com is not restricted by empty allowlist
    enforce_sensitive_egress("https://example.com/", allowed_destinations=["example.com"])


# ---------------------------------------------------------------------------
# _check_peer_ip: additional branches
# ---------------------------------------------------------------------------


def test_check_peer_ip_no_sock_no_fail_closed():
    """CB: _check_peer_ip returns early when sock is None and fail_closed=False."""
    resp = MagicMock()
    resp.raw = None
    # Should not raise
    _check_peer_ip(resp, ("8.8.8.8",), is_public=True, fail_closed=False)


def test_check_peer_ip_public_ip_forbidden():
    """CB: _check_peer_ip raises for forbidden public peer IP."""
    resp = _make_mock_response("169.254.169.254")
    with patch("celina_pkg.net_policy.is_forbidden_public_ip", return_value=(True, "metadata")):
        with pytest.raises(URLPolicyError, match="DNS rebinding detected"):
            _check_peer_ip(resp, ("8.8.8.8",), is_public=True)


def test_check_peer_ip_peer_not_in_allowed():
    """CB: _check_peer_ip raises when peer IP not in allowed_ips."""
    resp = _make_mock_response("10.0.0.99")
    # Make is_forbidden_public_ip return False so it reaches the allowed_ips check
    with patch("celina_pkg.net_policy.is_forbidden_public_ip", return_value=(False, "")):
        with pytest.raises(URLPolicyError):
            _check_peer_ip(resp, ("8.8.8.8",), is_public=True)


# ---------------------------------------------------------------------------
# safe_request / safe_get: validation + request paths
# ---------------------------------------------------------------------------


def test_safe_get_blocked_by_url_policy():
    """CB: safe_get raises URLPolicyError for blocked URL."""
    with pytest.raises(URLPolicyError, match="Blocked resolved IP"):
        safe_get("https://evil.com/", resolver=lambda host, port: ["127.0.0.1"])


def test_safe_request_iot_policy_redirects_capped(monkeypatch):
    """CB: safe_request with iot policy caps redirects at 0."""
    allowlist = {"allowed_hosts": ["example.com"], "allowed_ports": [8123], "allowed_methods": ["POST"]}
    with patch("celina_pkg.net_policy.validate_iot_url") as mock_validate:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.iter_content.return_value = [b"ok"]
        mock_resp.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)
        mock_validate.return_value = MagicMock(url="http://example.com:8123/", resolved_ips=("93.184.216.34",))

        with patch("celina_pkg.net_policy.requests.request", return_value=mock_resp):
            result = safe_request("POST", "http://example.com:8123/", policy="iot", iot_allowlist=allowlist)
            assert result.status_code == 200


def test_safe_request_redirect_blocked(monkeypatch):
    """CB: safe_request raises when redirects are blocked."""
    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.headers = {"Location": "https://evil.com/"}
        mock_resp.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)
        mock_validate.return_value = MagicMock(url="https://example.com/", resolved_ips=("93.184.216.34",))

        with patch("celina_pkg.net_policy.requests.request", return_value=mock_resp):
            with pytest.raises(URLPolicyError, match="Redirects are blocked"):
                safe_get("https://example.com/", allow_redirects=False)


def test_safe_request_content_type_blocked(monkeypatch):
    """CB: safe_request raises for disallowed Content-Type."""
    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/octet-stream"}
        mock_resp.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)
        mock_resp.iter_content.return_value = [b"binary data"]
        mock_validate.return_value = MagicMock(url="https://example.com/", resolved_ips=("93.184.216.34",))

        with patch("celina_pkg.net_policy.requests.request", return_value=mock_resp):
            with pytest.raises(URLPolicyError, match="disallowed Content-Type"):
                safe_get("https://example.com/", allowed_content_types={"text/html"})


def test_safe_request_content_length_exceeded(monkeypatch):
    """CB: safe_request raises when Content-Length exceeds max_bytes."""
    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/plain", "Content-Length": "999999999"}
        mock_resp.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)
        mock_validate.return_value = MagicMock(url="https://example.com/", resolved_ips=("93.184.216.34",))

        with patch("celina_pkg.net_policy.requests.request", return_value=mock_resp):
            with pytest.raises(URLPolicyError, match="Response too large"):
                safe_get("https://example.com/", max_bytes=100)


def test_safe_request_size_exceeded_streaming(monkeypatch):
    """CB: safe_request raises when streamed response exceeds max_bytes."""
    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)
        mock_resp.iter_content.return_value = [b"x" * 200000, b"x" * 200000]
        mock_validate.return_value = MagicMock(url="https://example.com/", resolved_ips=("93.184.216.34",))

        with patch("celina_pkg.net_policy.requests.request", return_value=mock_resp):
            with pytest.raises(URLPolicyError, match="exceeded maximum allowed size"):
                safe_get("https://example.com/", max_bytes=100)


def test_safe_request_success(monkeypatch):
    """CB: safe_request returns SafeHTTPResponse on success."""
    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.raw._connection.sock.getpeername.return_value = ("93.184.216.34", 12345)
        mock_resp.iter_content.return_value = [b"Hello World"]
        mock_validate.return_value = MagicMock(url="https://example.com/", resolved_ips=("93.184.216.34",))

        with patch("celina_pkg.net_policy.requests.request", return_value=mock_resp):
            result = safe_get("https://example.com/")
            assert result.status_code == 200
            assert "Hello World" in result.text

