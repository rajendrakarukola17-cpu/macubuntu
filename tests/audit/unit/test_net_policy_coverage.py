"""
tests/audit/unit/test_net_policy_coverage.py

Phase 7b — TASK 7: net_policy.py branch coverage tests.
Targets missing branches: validate_public_url allowlist paths, guarded_call
tool resolution, is_forbidden_public_ip IP category branches, default_dns_resolver paths,
validate_iot_url branches, _check_peer_ip peer verification branches.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg.net_policy import (
    URLPolicyError,
    default_dns_resolver,
    guarded_call,
    is_forbidden_public_ip,
    validate_public_url,
    validate_iot_url,
    _check_peer_ip,
)


# ---------------------------------------------------------------------------
# validate_public_url: allowlist match / no match
# ---------------------------------------------------------------------------


def test_validate_public_url_allowed():
    """CB: validate_public_url allows host in allowlist."""
    validate_public_url(
        "https://example.com/api",
        resolver=lambda host, port: ["93.184.216.34"],
    )


def test_validate_public_url_not_allowed():
    """CB: validate_public_url rejects host that resolves to forbidden IP."""
    with pytest.raises(URLPolicyError, match="Blocked resolved IP"):
        validate_public_url(
            "https://evil.com/api",
            resolver=lambda host, port: ["127.0.0.1"],
        )


def test_validate_public_url_local_suffix_blocked():
    """CB: validate_public_url blocks .local suffix hosts."""
    with pytest.raises(URLPolicyError, match="Local/internal host"):
        validate_public_url(
            "https://myservice.local/",
            resolver=lambda host, port: ["93.184.216.34"],
        )


# ---------------------------------------------------------------------------
# guarded_call: tool resolution paths
# ---------------------------------------------------------------------------


def test_guarded_call_direct_fn():
    """CB: guarded_call with callable as first arg."""
    result = guarded_call("any_tool", lambda x: x * 2, 21)
    assert result == 42


def test_guarded_call_kwarg_fn():
    """CB: guarded_call with fn= kwarg."""
    result = guarded_call("any_tool", fn=lambda x: x + 1, x=5)
    assert result == 6


def test_guarded_call_unknown_tool():
    """CB: guarded_call raises KeyError for unknown tool."""
    with pytest.raises(KeyError, match="Unknown tool"):
        guarded_call("nonexistent_tool_xyz", 1, 2, online_probe=lambda: False)


# ---------------------------------------------------------------------------
# is_forbidden_public_ip: IP category branches
# ---------------------------------------------------------------------------


def test_is_forbidden_public_ip_loopback():
    """CB: is_forbidden_public_ip returns True for loopback."""
    bad, reason = is_forbidden_public_ip("127.0.0.1")
    assert bad is True
    assert "loopback" in reason


def test_is_forbidden_public_ip_multicast():
    """CB: is_forbidden_public_ip returns True for multicast."""
    bad, reason = is_forbidden_public_ip("224.0.0.1")
    assert bad is True
    assert "multicast" in reason


def test_is_forbidden_public_ip_private():
    """CB: is_forbidden_public_ip returns True for private."""
    bad, reason = is_forbidden_public_ip("10.0.0.1")
    assert bad is True
    assert "private" in reason


def test_is_forbidden_public_ip_metadata_ip():
    """CB: is_forbidden_public_ip returns True for metadata IP (checked first)."""
    bad, reason = is_forbidden_public_ip("169.254.169.254")
    assert bad is True
    assert "cloud metadata" in reason


def test_is_forbidden_public_ip_link_local_non_metadata():
    """CB: is_forbidden_public_ip returns True for non-metadata link-local."""
    bad, reason = is_forbidden_public_ip("169.254.1.1")
    assert bad is True
    assert "link-local" in reason


def test_is_forbidden_public_ip_unspecified():
    """CB: is_forbidden_public_ip returns True for unspecified (0.0.0.0 is private per is_private)."""
    bad, reason = is_forbidden_public_ip("0.0.0.0")
    assert bad is True
    assert "private" in reason


def test_is_forbidden_public_ip_cgnat():
    """CB: is_forbidden_public_ip returns True for CGNAT."""
    bad, reason = is_forbidden_public_ip("100.64.0.1")
    assert bad is True
    assert "CGNAT" in reason


def test_is_forbidden_public_ip_non_global_ipv6():
    """CB: is_forbidden_public_ip returns True for non-global IPv6."""
    bad, reason = is_forbidden_public_ip("::1")
    assert bad is True


def test_is_forbidden_public_ip_public():
    """CB: is_forbidden_public_ip returns False for public IP."""
    bad, reason = is_forbidden_public_ip("8.8.8.8")
    assert bad is False


def test_is_forbidden_public_ip_metadata_ip():
    """CB: is_forbidden_public_ip returns True for metadata IP."""
    bad, reason = is_forbidden_public_ip("169.254.169.254")
    assert bad is True


# ---------------------------------------------------------------------------
# default_dns_resolver: no IPs path
# ---------------------------------------------------------------------------


def test_default_dns_resolver_no_ips():
    """CB: default_dns_resolver raises when no IPs resolved."""
    with patch("socket.getaddrinfo", return_value=[]):
        with pytest.raises(URLPolicyError, match="No IP addresses resolved"):
            default_dns_resolver("example.com", 443)


# ---------------------------------------------------------------------------
# validate_iot_url: IP validation branches
# ---------------------------------------------------------------------------


def test_validate_iot_url_forbidden_ip():
    """CB: validate_iot_url rejects forbidden resolved IP."""
    allowlist = {
        "allowed_hosts": ["169.254.169.254"],
        "allowed_ports": [80],
        "allowed_methods": ["GET"],
    }
    with patch("celina_pkg.net_policy.default_dns_resolver", return_value=["169.254.169.254"]):
        with pytest.raises(URLPolicyError, match="forbidden"):
            validate_iot_url("http://169.254.169.254/", "GET", allowlist)


# ---------------------------------------------------------------------------
# _check_peer_ip: peer IP verification branches
# ---------------------------------------------------------------------------


def _make_mock_response(peer_ip, has_sock=True):
    """Build a mock requests.Response with a socket returning peer_ip."""
    mock_sock = MagicMock()
    mock_sock.getpeername.return_value = (peer_ip, 12345)
    mock_conn = MagicMock()
    mock_conn.sock = mock_sock
    mock_raw = MagicMock()
    mock_raw._connection = mock_conn
    resp = MagicMock()
    resp.raw = mock_raw
    return resp


def test_check_peer_ip_fail_closed_no_sock():
    """CB: _check_peer_ip raises when sock is None and fail_closed=True."""
    resp = MagicMock()
    resp.raw = None
    with pytest.raises(URLPolicyError, match="peer IP unavailable"):
        _check_peer_ip(resp, ("8.8.8.8",), is_public=True, fail_closed=True)


def test_check_peer_ip_forbidden_public_peer():
    """CB: _check_peer_ip raises for forbidden public peer IP."""
    resp = _make_mock_response("169.254.169.254")
    with patch("celina_pkg.net_policy.is_forbidden_public_ip", return_value=(True, "metadata")):
        with pytest.raises(URLPolicyError, match="DNS rebinding detected"):
            _check_peer_ip(resp, ("8.8.8.8",), is_public=True)


def test_check_peer_ip_peer_getpeername_raises():
    """CB: _check_peer_ip raises when getpeername raises."""
    mock_sock = MagicMock()
    mock_sock.getpeername.side_effect = OSError("connection reset")
    mock_conn = MagicMock()
    mock_conn.sock = mock_sock
    mock_raw = MagicMock()
    mock_raw._connection = mock_conn
    resp = MagicMock()
    resp.raw = mock_raw
    with pytest.raises(URLPolicyError, match="peer verification failed"):
        _check_peer_ip(resp, ("8.8.8.8",), is_public=False, fail_closed=True)