"""
tests/audit/unit/test_net_policy_redirects.py

Phase 3 unit tests for celina_pkg.net_policy.safe_request — redirect blocking,
body cap, 429 handling, single POST on timeout.

Uses a mock requests.Session to avoid real network calls.

Key security properties verified:
- Default: allow_redirects=False, max_redirects=0 → redirects cause URLPolicyError (fail-closed)
- allow_redirects=True with max_redirects=0 → redirects_cap=0 → redirects blocked
- POST is non-idempotent: attempted exactly once on timeout
- max_bytes: oversized bodies rejected
- 429/5xx: returned as-is (no automatic retry)
- Error responses surface status + body
"""

from __future__ import annotations

import sys
from unittest import mock

import pytest

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg.net_policy import safe_request, URLPolicyError


@pytest.fixture(autouse=True)
def offline_dns(monkeypatch):
    """Resolve through a fixed public test IP without any live DNS lookup."""
    from celina_pkg import net_policy

    def reject_live_dns(*_args, **_kwargs):
        raise AssertionError("live DNS must not be used by redirect unit tests")

    monkeypatch.setattr("socket.getaddrinfo", reject_live_dns)
    monkeypatch.setattr(net_policy, "default_dns_resolver", lambda _host, _port: ["93.184.216.34"])


class _FakeResponse:
    """Minimal requests.Response-like object."""

    def __init__(self, status_code, content=b"", headers=None, url=""):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.url = url
        self.text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)

    def iter_content(self, chunk_size=8192, decode_unicode=False):
        yield self.content

    def close(self):
        pass


class _FakeSession:
    """Session that records every request and can be configured per-call."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise ConnectionError("no more responses configured")
        resp_spec = self.responses.pop(0)
        if callable(resp_spec):
            status, content, headers = resp_spec(self.calls[-1])
        else:
            status, content, headers = resp_spec
        return _FakeResponse(status, content, headers, url=url)


# ---------------------------------------------------------------------------
# No redirect following (default allow_redirects=False → redirects_cap=0)
# ---------------------------------------------------------------------------


def test_default_redirect_causes_policy_error():
    """With default settings, a 302 response causes URLPolicyError (fail-closed)."""
    session = _FakeSession([
        (302, b"redirecting", {"Location": "https://evil.com/target"}),
    ])
    try:
        safe_request("GET", "https://example.com", session=session)
        assert False, "Expected URLPolicyError for redirect"
    except URLPolicyError as e:
        assert "Redirects are blocked" in str(e)
    assert len(session.calls) == 1  # only one request, no follow


def test_redirect_to_different_host_blocked():
    """A 302 to a different host must not be followed — policy error instead."""
    session = _FakeSession([
        (302, b"moved", {"Location": "https://different-host.com/steal"}),
    ])
    try:
        safe_request("GET", "https://example.com", session=session)
        assert False, "Expected URLPolicyError for cross-host redirect"
    except URLPolicyError as e:
        assert "Redirects are blocked" in str(e)
    assert len(session.calls) == 1


# ---------------------------------------------------------------------------
# allow_redirects=True with max_redirects=0 still blocks redirects
# ---------------------------------------------------------------------------


def test_allow_redirects_true_max_redirects_zero_blocks():
    session = _FakeSession([
        (302, b"moved", {"Location": "https://example.com/next"}),
    ])
    try:
        safe_request("GET", "https://example.com", session=session, allow_redirects=True, max_redirects=0)
        assert False, "Expected URLPolicyError for blocked redirect"
    except URLPolicyError as e:
        assert "Redirects are blocked" in str(e)
    assert len(session.calls) == 1


# ---------------------------------------------------------------------------
# 5xx and 429: returned as-is (no automatic retry)
# ---------------------------------------------------------------------------


def test_5xx_returned_as_is_no_retry():
    session = _FakeSession([
        (500, b"server error", {}),
    ])
    resp = safe_request("GET", "https://example.com", session=session)
    assert resp.status_code == 500
    assert len(session.calls) == 1  # no retry


def test_429_returned_as_is_no_retry():
    session = _FakeSession([
        (429, b"rate limited", {"Retry-After": "1"}),
    ])
    resp = safe_request("GET", "https://example.com", session=session)
    assert resp.status_code == 429
    assert len(session.calls) == 1  # no retry


# ---------------------------------------------------------------------------
# POST is attempted exactly once on timeout (non-idempotent)
# ---------------------------------------------------------------------------


def test_post_not_retried_on_timeout():
    """POST is non-idempotent — must be attempted exactly once on timeout."""
    call_count = [0]

    def raising_request(method, url, **kwargs):
        call_count[0] += 1
        raise TimeoutError("connect timeout")

    session = _FakeSession([])
    session.request = raising_request
    try:
        safe_request("POST", "https://example.com/submit", session=session, json_body={"key": "value"})
        assert False, "Expected TimeoutError to propagate"
    except TimeoutError:
        pass
    assert call_count[0] == 1, f"POST should be attempted once, got {call_count[0]}"


# ---------------------------------------------------------------------------
# Body cap (max_bytes) — Content-Length header check
# ---------------------------------------------------------------------------


def test_max_bytes_rejects_by_content_length():
    """Oversized Content-Length header causes rejection before body download."""
    session = _FakeSession([
        (200, b"small", {"Content-Length": str(10 * 1024 * 1024)}),  # claims 10MB
    ])
    try:
        safe_request("GET", "https://example.com", session=session, max_bytes=1024)
        assert False, "Expected URLPolicyError for oversized Content-Length"
    except URLPolicyError as e:
        assert "Response too large" in str(e) or "exceeds limit" in str(e)


def test_max_bytes_allows_small_body():
    session = _FakeSession([
        (200, b"hello world", {}),
    ])
    resp = safe_request("GET", "https://example.com", session=session, max_bytes=1024)
    assert resp.status_code == 200
    assert resp.body == b"hello world"


# ---------------------------------------------------------------------------
# Error responses surface status + message
# ---------------------------------------------------------------------------


def test_403_surfaces_status_and_message():
    session = _FakeSession([
        (403, b"Forbidden: invalid token", {}),
    ])
    resp = safe_request("GET", "https://example.com", session=session)
    assert resp.status_code == 403
    assert "Forbidden" in resp.text


def test_200_returns_body():
    session = _FakeSession([
        (200, b'{"ok": true}', {"Content-Type": "application/json"}),
    ])
    resp = safe_request("GET", "https://example.com/api", session=session)
    assert resp.status_code == 200
    assert b'"ok"' in resp.body
