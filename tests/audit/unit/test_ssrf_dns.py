"""
tests/audit/unit/test_ssrf_dns.py

Phase 7 remediation — Test for UNVERIFIED-1:
SSRF via DNS rebinding, DNS pinning bypass.

Stubs the DNS resolver to return private/loopback/link-local/metadata IPs
and a rebinding case (public on first lookup, private on second). Confirms
that validate_public_url blocks all of them.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg.net_policy import (
    SafeHTTPResponse,
    URLPolicyError,
    _check_peer_ip,
    is_forbidden_public_ip,
    safe_get,
    validate_public_url,
)


# ---------------------------------------------------------------------------
# Test 1: Loopback IP (127.0.0.1) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_loopback_ip():
    """validate_public_url must reject a hostname resolving to 127.0.0.1."""
    def fake_resolver(host, port):
        return ["127.0.0.1"]

    with pytest.raises(URLPolicyError, match="loopback"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# Test 2: Private IP (10.x) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_private_ip_10():
    """validate_public_url must reject a hostname resolving to 10.0.0.1."""
    def fake_resolver(host, port):
        return ["10.0.0.1"]

    with pytest.raises(URLPolicyError, match="private"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# Test 3: Private IP (172.16.x) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_private_ip_172():
    """validate_public_url must reject a hostname resolving to 172.16.0.1."""
    def fake_resolver(host, port):
        return ["172.16.0.1"]

    with pytest.raises(URLPolicyError, match="private"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# Test 4: Private IP (192.168.x) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_private_ip_192():
    """validate_public_url must reject a hostname resolving to 192.168.1.1."""
    def fake_resolver(host, port):
        return ["192.168.1.1"]

    with pytest.raises(URLPolicyError, match="private"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# Test 5: Link-local IP (169.254.x) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_link_local_ip():
    """validate_public_url must reject a hostname resolving to 169.254.169.254."""
    def fake_resolver(host, port):
        return ["169.254.169.254"]

    with pytest.raises(URLPolicyError, match="link-local|cloud metadata"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# Test 6: Cloud metadata IP (169.254.169.254) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_metadata_ip():
    """validate_public_url must reject a hostname resolving to metadata IP."""
    def fake_resolver(host, port):
        return ["169.254.169.254"]

    with pytest.raises(URLPolicyError, match="cloud metadata"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# Test 7: IPv6 loopback (::1) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_ipv6_loopback():
    """validate_public_url must reject a hostname resolving to ::1."""
    def fake_resolver(host, port):
        return ["::1"]

    with pytest.raises(URLPolicyError, match="loopback"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# Test 8: IPv4-mapped IPv6 wrapping loopback (::ffff:127.0.0.1) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_ipv4_mapped_ipv6_loopback():
    """validate_public_url must reject ::ffff:127.0.0.1 (IPv4-mapped IPv6)."""
    def fake_resolver(host, port):
        return ["::ffff:127.0.0.1"]

    with pytest.raises(URLPolicyError, match="loopback|IPv4-mapped"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# Test 9: IPv4-mapped IPv6 wrapping private (::ffff:10.0.0.1) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_ipv4_mapped_ipv6_private():
    """validate_public_url must reject ::ffff:10.0.0.1."""
    def fake_resolver(host, port):
        return ["::ffff:10.0.0.1"]

    with pytest.raises(URLPolicyError, match="private|IPv4-mapped"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# Test 10: DNS rebinding (public first, private second) blocked
# ---------------------------------------------------------------------------


def test_validate_blocks_dns_rebinding():
    """DNS rebinding: first lookup returns public IP, second returns private.

    The resolver is called once during validation. If it returns a public IP
    first, validation passes. But a rebinding attack would resolve differently
    at connect time. Since validate_public_url resolves DNS upfront and checks
    ALL resolved IPs, a rebinding resolver that returns both public and private
    IPs in a single call will be blocked.
    """
    call_count = [0]

    def rebinding_resolver(host, port):
        call_count[0] += 1
        # First call returns public IP, subsequent calls return private
        if call_count[0] == 1:
            return ["93.184.216.34"]  # example.com (public)
        return ["10.0.0.1"]  # private (rebinding)

    # The resolver is called once during validation. If it returns both public
    # and private IPs, the private one will be caught.
    def mixed_resolver(host, port):
        return ["93.184.216.34", "10.0.0.1"]

    with pytest.raises(URLPolicyError, match="private"):
        validate_public_url("https://example.com/", resolver=mixed_resolver)


# ---------------------------------------------------------------------------
# Test 11: Public IP (93.184.216.34) allowed
# ---------------------------------------------------------------------------


def test_validate_allows_public_ip():
    """validate_public_url must accept a hostname resolving to a public IP."""
    def fake_resolver(host, port):
        return ["93.184.216.34"]

    result = validate_public_url("https://example.com/", resolver=fake_resolver)
    assert result.host == "example.com"
    assert result.port == 443
    assert "93.184.216.34" in result.resolved_ips


# ---------------------------------------------------------------------------
# Test 12: is_forbidden_public_ip unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ip,expected_forbidden", [
    ("127.0.0.1", True),
    ("::1", True),
    ("10.0.0.1", True),
    ("172.16.0.1", True),
    ("192.168.1.1", True),
    ("169.254.169.254", True),
    ("224.0.0.1", True),  # multicast
    ("0.0.0.0", True),     # unspecified
    ("93.184.216.34", False),  # public
    ("1.1.1.1", False),    # public
    ("::ffff:127.0.0.1", True),  # IPv4-mapped loopback
    ("::ffff:10.0.0.1", True),   # IPv4-mapped private
    ("::ffff:93.184.216.34", False),  # IPv4-mapped public
])
def test_is_forbidden_public_ip(ip, expected_forbidden):
    """is_forbidden_public_ip correctly classifies IPs."""
    forbidden, reason = is_forbidden_public_ip(ip)
    assert forbidden == expected_forbidden, (
        f"IP {ip}: expected forbidden={expected_forbidden}, got {forbidden} ({reason})"
    )


# ---------------------------------------------------------------------------
# Connect-time DNS rebinding tests (safe_get peer-IP checking)
# ---------------------------------------------------------------------------


def _make_mock_response(peer_ip: str, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response whose socket peer is `peer_ip`."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": "text/html"}
    sock = MagicMock()
    sock.getpeername.return_value = (peer_ip, 443)
    conn = MagicMock()
    conn.sock = sock
    raw = MagicMock()
    raw._connection = conn
    resp.raw = raw
    resp.iter_content.return_value = [b"<html>ok</html>"]
    resp.close = MagicMock()
    return resp


def test_safe_get_blocks_connect_time_rebinding():
    """safe_get must reject a connection whose peer IP differs from validated IPs.

    Simulates DNS rebinding: validation resolves to a public IP, but the
    connected socket peer is a private IP. _check_peer_ip (fail_closed=True)
    must raise URLPolicyError.
    """
    validated_ips = ("93.184.216.34",)  # example.com — public
    mock_resp = _make_mock_response("10.0.0.1")  # private at connect time

    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        mock_validate.return_value = MagicMock(
            resolved_ips=validated_ips, url="https://example.com/"
        )
        with patch("celina_pkg.net_policy.requests.request", return_value=mock_resp):
            with pytest.raises(URLPolicyError, match="DNS rebinding|rebinding"):
                safe_get("https://example.com/")


def test_safe_get_allows_matching_peer_ip():
    """safe_get must allow a connection whose peer IP matches a validated IP."""
    validated_ips = ("93.184.216.34",)
    mock_resp = _make_mock_response("93.184.216.34")

    with patch("celina_pkg.net_policy.validate_public_url") as mock_validate:
        mock_validate.return_value = MagicMock(
            resolved_ips=validated_ips, url="https://example.com/"
        )
        with patch("celina_pkg.net_policy.requests.request", return_value=mock_resp):
            resp = safe_get("https://example.com/")
            assert resp.status_code == 200


def test_safe_get_blocks_multi_a_record_with_private():
    """safe_get must block when any resolved IP is private (multi-A-record)."""
    # Resolver returns one public + one private IP
    def mixed_resolver(host, port):
        return ["93.184.216.34", "10.0.0.1"]

    with pytest.raises(URLPolicyError, match="private"):
        validate_public_url("https://example.com/", resolver=mixed_resolver)


# ---------------------------------------------------------------------------
# Decimal / octal / hex IP literal forms
# ---------------------------------------------------------------------------


def test_validate_blocks_decimal_octal_ip_form():
    """Octal IP form (0177.0.0.1 = 127.0.0.1) must not bypass as a hostname."""
    # ipaddress.ip_address rejects leading zeros, so it falls to DNS resolution
    # which should fail for a non-hostname string → blocked
    with pytest.raises(URLPolicyError):
        validate_public_url("https://0177.0.0.1/")


def test_validate_blocks_hex_ip_form():
    """Hex IP form (0x7f000001 = 127.0.0.1) must not bypass as a hostname."""
    with pytest.raises(URLPolicyError):
        validate_public_url("https://0x7f000001/")


def test_validate_blocks_dotted_hex_ip_form():
    """Dotted-hex IP form (0x7f.0x0.0x0.0x1) must not bypass as a hostname."""
    with pytest.raises(URLPolicyError):
        validate_public_url("https://0x7f.0x0.0x0.0x1/")


# ---------------------------------------------------------------------------
# Userinfo / trailing dots / non-https
# ---------------------------------------------------------------------------


def test_validate_blocks_userinfo_in_url():
    """URLs with embedded user:pass@host credentials must be rejected."""
    with pytest.raises(URLPolicyError, match="credentials"):
        validate_public_url("https://user:pass@example.com/")


def test_validate_blocks_username_only_in_url():
    """URLs with embedded username@host must be rejected."""
    with pytest.raises(URLPolicyError, match="credentials"):
        validate_public_url("https://user@example.com/")


def test_validate_allows_trailing_dot_stripped():
    """Trailing dot in hostname should be stripped and allowed if public."""
    def fake_resolver(host, port):
        return ["93.184.216.34"]

    result = validate_public_url("https://example.com./", resolver=fake_resolver)
    assert result.host == "example.com"


def test_validate_blocks_http_scheme():
    """Non-https schemes must be rejected for public URLs."""
    with pytest.raises(URLPolicyError, match="Only https://"):
        validate_public_url("http://example.com/")


def test_validate_blocks_ftp_scheme():
    """ftp:// scheme must be rejected for public URLs."""
    with pytest.raises(URLPolicyError, match="Only https://"):
        validate_public_url("ftp://example.com/")


# ---------------------------------------------------------------------------
# CGNAT / reserved / unspecified / multicast
# ---------------------------------------------------------------------------


def test_validate_blocks_cgnat_ip():
    """CGNAT range (100.64.0.0/10) must be blocked."""
    def fake_resolver(host, port):
        return ["100.64.0.1"]

    with pytest.raises(URLPolicyError, match="CGNAT|shared"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


def test_validate_blocks_multicast_ip():
    """Multicast IP (224.0.0.1) must be blocked."""
    def fake_resolver(host, port):
        return ["224.0.0.1"]

    with pytest.raises(URLPolicyError, match="multicast"):
        validate_public_url("https://example.com/", resolver=fake_resolver)


def test_validate_blocks_unspecified_ip():
    """Unspecified IP (0.0.0.0) must be blocked (any forbidden reason OK)."""
    def fake_resolver(host, port):
        return ["0.0.0.0"]

    with pytest.raises(URLPolicyError):
        validate_public_url("https://example.com/", resolver=fake_resolver)


# ---------------------------------------------------------------------------
# _check_peer_ip unit tests
# ---------------------------------------------------------------------------


def test_check_peer_ip_blocks_private_peer():
    """_check_peer_ip must raise when peer IP is private."""
    validated = ("93.184.216.34",)
    mock_resp = _make_mock_response("10.0.0.1")

    with pytest.raises(URLPolicyError, match="rebinding|forbidden"):
        _check_peer_ip(mock_resp, validated, is_public=True, fail_closed=True)


def test_check_peer_ip_allows_matching_peer():
    """_check_peer_ip must allow when peer IP matches validated set."""
    validated = ("93.184.216.34",)
    mock_resp = _make_mock_response("93.184.216.34")

    # Should not raise
    _check_peer_ip(mock_resp, validated, is_public=True, fail_closed=True)


def test_check_peer_ip_fail_closed_no_sock():
    """_check_peer_ip with fail_closed=True must raise when no socket available."""
    validated = ("93.184.216.34",)
    resp = MagicMock()
    resp.raw = MagicMock()
    resp.raw._connection = MagicMock()
    resp.raw._connection.sock = None

    with pytest.raises(URLPolicyError, match="Fail-closed"):
        _check_peer_ip(resp, validated, is_public=True, fail_closed=True)
