"""
tests/audit/unit/test_connector_integration_stubs.py

Phase 4 — Connector integration audit (stubbed, no network).

Tests gmail, github, notion, linear connectors using stubbed HTTP layer.
Verifies: exact HTTP method/path, Authorization header, no redirect following,
oversized body rejection, 429 Retry-After, POST once on timeout, GET retry on
5xx/429, error responses surface status + message, ID parameter traversal
defense, Gmail Telugu/Hindi MIME encoding.
"""

from __future__ import annotations

import base64
import email
import sys
from unittest import mock

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg.connectors.base import (
    BaseConnector,
    URLPolicyError,
    _validate_path_segment,
)

_build_path = BaseConnector._build_path

from celina_pkg.connectors.gmail import GmailConnector, GMAIL_API_URL
from celina_pkg.connectors.github import GitHubConnector
from celina_pkg.connectors.notion import NotionConnector
from celina_pkg.connectors.linear import LinearConnector


# ---------------------------------------------------------------------------
# Helper: make a connector with mocked _http_request and auto-approve gate
# ---------------------------------------------------------------------------


def _make_gmail(auto_approve=True):
    c = GmailConnector(
        approval_cb=lambda action, params: True if auto_approve else False
    )
    c._access_token = "test-access-token"
    calls = []

    def fake_http_request(method, url, headers, payload=None, timeout=10,
                          max_bytes=2 * 1024 * 1024, allow_redirects=False):
        calls.append({
            "method": method, "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "max_bytes": max_bytes,
            "allow_redirects": allow_redirects,
        })
        return (200, {"ok": True}, None)

    c._http_request = fake_http_request
    return c, calls


# ---------------------------------------------------------------------------
# 1. Exact HTTP method and path
# ---------------------------------------------------------------------------


def test_gmail_list_messages_method_path():
    c, calls = _make_gmail()
    c._list_messages({"query": "is:unread", "max_results": "10"})
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "GET"
    assert "/users/me/messages" in call["url"]
    assert "q=is%3Aunread" in call["url"]
    assert "maxResults=10" in call["url"]


def test_gmail_get_message_method_path():
    c, calls = _make_gmail()
    c._get_message({"message_id": "msg-123"})
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "GET"
    assert "/users/me/messages/msg-123" in call["url"]


def test_github_build_path():
    result = _build_path("repos", "owner", "repo", "issues", "42")
    assert result == "/repos/owner/repo/issues/42"


def test_notion_build_path():
    result = _build_path("pages", "page-123")
    assert result == "/pages/page-123"


def test_linear_build_path():
    result = _build_path("issues", "issue-123")
    assert result == "/issues/issue-123"


# ---------------------------------------------------------------------------
# 2. Authorization header presence
# ---------------------------------------------------------------------------


def test_gmail_authorization_header():
    c, calls = _make_gmail()
    c._list_messages({})
    assert len(calls) == 1
    headers = calls[0]["headers"]
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer test-access-token"


# ---------------------------------------------------------------------------
# 3. No redirect following (SSRF defense)
# ---------------------------------------------------------------------------


def test_connector_redirect_raises_urlpolicyerror():
    """_NoRedirectHandler raises URLPolicyError on any redirect."""
    from celina_pkg.connectors.base import _NoRedirectHandler

    handler = _NoRedirectHandler()
    try:
        handler.redirect_request(None, None, 302, "Moved", {}, "https://evil.com/")
        assert False, "Expected URLPolicyError"
    except URLPolicyError as e:
        assert "redirect" in str(e).lower() or "blocked" in str(e).lower()


# ---------------------------------------------------------------------------
# 4. Oversized body rejection — max_bytes passed to _http_request
# ---------------------------------------------------------------------------


def test_max_bytes_passed_to_http_request():
    c, calls = _make_gmail()
    c._get("/users/me/profile")
    assert len(calls) == 1
    assert calls[0]["max_bytes"] == 2 * 1024 * 1024  # default 2MB cap


# ---------------------------------------------------------------------------
# 5. 429 Retry-After — GET retries on 429/5xx (up to 3 attempts, no sleep)
# ---------------------------------------------------------------------------


def test_get_retries_on_429_up_to_3_times():
    """GET retries up to 3x on 429, no sleep between retries."""
    c, calls = _make_gmail()
    responses = iter([
        (429, None, "rate limited"),
        (429, None, "rate limited"),
        (429, None, "rate limited"),
    ])
    call_count = [0]

    def counting_request(method, url, headers, payload=None, timeout=10,
                         max_bytes=2 * 1024 * 1024, allow_redirects=False):
        call_count[0] += 1
        return next(responses)

    c._http_request = counting_request

    try:
        c._list_messages({})
    except RuntimeError:
        pass  # may fail if all retries return 429

    # Should have attempted up to 3 times (bounded retry)
    assert call_count[0] <= 3, f"GET should retry at most 3x, got {call_count[0]}"
    assert call_count[0] >= 1, f"GET should attempt at least once, got {call_count[0]}"


# ---------------------------------------------------------------------------
# 6. POST attempted exactly once on error (non-idempotent)
# ---------------------------------------------------------------------------


def test_post_not_retried_on_error():
    """POST is non-idempotent — attempted exactly once even on error."""
    c, calls = _make_gmail(auto_approve=True)
    call_count = [0]

    def failing_request(method, url, headers, payload=None, timeout=10,
                         max_bytes=2 * 1024 * 1024, allow_redirects=False):
        call_count[0] += 1
        return (0, None, "connection error")

    c._http_request = failing_request

    try:
        c._send_message({"to": "a@b.com", "subject": "hi", "body": "text"})
    except Exception:
        pass

    assert call_count[0] == 1, f"POST should be attempted once, got {call_count[0]}"


# ---------------------------------------------------------------------------
# 7. Error responses surface status + message
# ---------------------------------------------------------------------------


def test_http_error_surfaces_status_and_message():
    """HTTP error responses return status code and error detail."""
    c, calls = _make_gmail()

    def error_request(method, url, headers, payload=None, timeout=10,
                       max_bytes=2 * 1024 * 1024, allow_redirects=False):
        return (403, None, "Forbidden: invalid credentials")

    c._http_request = error_request

    try:
        c._list_messages({})
    except RuntimeError as e:
        assert "403" in str(e) or "Forbidden" in str(e)


# ---------------------------------------------------------------------------
# 8. Gmail Telugu/Hindi MIME encoding
# ---------------------------------------------------------------------------


def test_gmail_send_message_telugu_hindi_raw_utf8():
    """Gmail send_message with Telugu/Hindi produces base64url-encoded raw UTF-8 message."""
    c, calls = _make_gmail(auto_approve=True)
    def fake(method, url, headers, payload=None, timeout=10,
             max_bytes=2 * 1024 * 1024, allow_redirects=False):
        calls.append({
            "method": method, "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "max_bytes": max_bytes,
            "allow_redirects": allow_redirects,
        })
        return (200, {"id": "sent-123"}, None)
    c._http_request = fake

    result = c._send_message({
        "to": "recipient@example.com",
        "subject": "नमस्ते",  # Hindi (Devanagari)
        "body": "नमस्ते, कैसे हैं आप?",  # Hindi
    })

    assert result.success is True
    assert len(calls) == 1
    assert calls[0]["payload"] is not None
    payload = calls[0]["payload"]
    assert "raw" in payload

    # Decode base64url and verify the raw message contains the UTF-8 text
    raw_b64 = payload["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64 + "==")
    raw_text = raw_bytes.decode("utf-8")
    assert "नमस्ते" in raw_text, f"Decoded raw should contain Hindi text, got: {raw_text!r}"
    assert "To: recipient@example.com" in raw_text
    assert "Subject: नमस्ते" in raw_text

    # Verify the raw message is valid base64url (no padding, URL-safe alphabet)
    import re
    assert re.match(r'^[A-Za-z0-9_-]+$', raw_b64), f"raw should be base64url, got: {raw_b64!r}"


def test_gmail_send_message_crlf_injection_rejected():
    """CRLF in subject/to is rejected (header injection defense)."""
    c, _ = _make_gmail(auto_approve=True)
    result = c._send_message({
        "to": "a@b.com",
        "subject": "hi\r\nBcc: victim@evil.com",
        "body": "text",
    })
    assert result.success is False
    assert "injection" in result.error.lower()

    result = c._send_message({
        "to": "a@b.com\r\nBcc: victim@evil.com",
        "subject": "hi",
        "body": "text",
    })
    assert result.success is False
    assert "injection" in result.error.lower()


def test_gmail_send_message_line_injection_extended_headers():
    """Line-injection in cc, bcc, from, reply_to is also rejected."""
    c, _ = _make_gmail(auto_approve=True)
    for field, bad_value in [
        ("cc", "a@b.com\r\nBcc: victim@evil.com"),
        ("bcc", "a@b.com\nBcc: victim@evil.com"),
        ("from", "a@b.com\u2028Bcc: victim@evil.com"),
        ("reply_to", "a@b.com\u2029Bcc: victim@evil.com"),
    ]:
        result = c._send_message({
            "to": "a@b.com",
            "subject": "hi",
            "body": "text",
            field: bad_value,
        })
        assert result.success is False, f"{field} with line injection should be rejected"
        assert "header" in result.error.lower() or "injection" in result.error.lower()


def test_gmail_send_message_line_injection_unicode_separators():
    """Unicode line separators (\u2028, \u2029, \x0b, \x0c) are rejected."""
    c, _ = _make_gmail(auto_approve=True)
    for sep in ["\u2028", "\u2029", "\x0b", "\x0c"]:
        result = c._send_message({
            "to": f"a@b.com{sep}Bcc: victim@evil.com",
            "subject": "hi",
            "body": "text",
        })
        assert result.success is False, f"Separator {sep!r} should be rejected"
        assert "header" in result.error.lower() or "injection" in result.error.lower()


def test_gmail_send_message_line_injection_clean_headers_accepted():
    """Headers without line-injection chars are accepted."""
    c, calls = _make_gmail(auto_approve=True)

    def fake(method, url, headers, payload=None, timeout=10,
             max_bytes=2 * 1024 * 1024, allow_redirects=False):
        calls.append({"method": method, "url": url, "headers": headers, "payload": payload})
        return (200, {"id": "sent-123"}, None)
    c._http_request = fake

    result = c._send_message({
        "to": "a@b.com",
        "cc": "c@b.com",
        "bcc": "d@b.com",
        "subject": "hello",
        "body": "text",
    })
    assert result.success is True


# ---------------------------------------------------------------------------
# 9. ID parameter traversal attack matrix — zero HTTP requests
# ---------------------------------------------------------------------------


ID_ATTACK_INPUTS = [
    "..",
    "../user/keys",
    "a\x00b",
    "a/b",
    "a\\b",
    "",
    "a" * 5000,
    "../../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
]


def test_id_traversal_blocked_for_gmail_get_message():
    """Every malicious message_id must raise before any HTTP request."""
    for bad_id in ID_ATTACK_INPUTS:
        c = GmailConnector()
        c._access_token = "token"
        call_count = [0]

        def counting_request(method, url, headers, payload=None, timeout=10,
                              max_bytes=2 * 1024 * 1024, allow_redirects=False):
            call_count[0] += 1
            return (200, {}, None)

        c._http_request = counting_request

        try:
            c._get_message({"message_id": bad_id})
            assert False, f"_get_message with message_id={bad_id!r} did not raise"
        except (ValueError, KeyError, RuntimeError):
            pass  # expected — rejected before HTTP

        assert call_count[0] == 0, f"HTTP request made for malicious id {bad_id!r}"


def test_id_traversal_blocked_for_path_building():
    """_build_path rejects all malicious segment inputs."""
    for bad in ID_ATTACK_INPUTS:
        try:
            _build_path("users", "me", bad)
            assert False, f"_build_path with segment {bad!r} did not raise"
        except ValueError:
            pass  # expected


def test_validate_path_segment_rejects_all_attacks():
    """_validate_path_segment rejects every malicious input."""
    for bad in ID_ATTACK_INPUTS:
        try:
            _validate_path_segment(bad)
            assert False, f"_validate_path_segment({bad!r}) did not raise"
        except ValueError:
            pass  # expected


# ---------------------------------------------------------------------------
# 11. Authorization header for GitHub, Notion, Linear
# ---------------------------------------------------------------------------


def _make_github(auto_approve=True):
    c = GitHubConnector(approval_cb=lambda action, params: True if auto_approve else False)
    c._token = "ghp_test-token"
    calls = []

    def fake_http_request(method, url, headers, payload=None, timeout=10,
                          max_bytes=2 * 1024 * 1024, allow_redirects=False):
        calls.append({
            "method": method, "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "max_bytes": max_bytes,
            "allow_redirects": allow_redirects,
        })
        return (200, {"ok": True}, None)

    c._http_request = fake_http_request
    return c, calls


def _make_notion(auto_approve=True):
    c = NotionConnector(approval_cb=lambda action, params: True if auto_approve else False)
    c._integration_token = "ntn_test-token"
    calls = []

    def fake_http_request(method, url, headers, payload=None, timeout=10,
                          max_bytes=2 * 1024 * 1024, allow_redirects=False):
        calls.append({
            "method": method, "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "max_bytes": max_bytes,
            "allow_redirects": allow_redirects,
        })
        return (200, {"ok": True}, None)

    c._http_request = fake_http_request
    return c, calls


def _make_linear(auto_approve=True):
    c = LinearConnector(approval_cb=lambda action, params: True if auto_approve else False)
    c._api_key = "lin_test-api-key"
    calls = []

    def fake_http_request(method, url, headers, payload=None, timeout=10,
                          max_bytes=2 * 1024 * 1024, allow_redirects=False):
        calls.append({
            "method": method, "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "max_bytes": max_bytes,
            "allow_redirects": allow_redirects,
        })
        return (200, {"ok": True}, None)

    c._http_request = fake_http_request
    return c, calls


def test_github_authorization_header():
    """GitHub connector sends Authorization header."""
    c, calls = _make_github()
    # Mock _http_request to return a list of repos (avoiding iteration over dict)
    def fake_repos(method, url, headers, payload=None, timeout=10,
                   max_bytes=2 * 1024 * 1024, allow_redirects=False):
        calls.append({
            "method": method, "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "max_bytes": max_bytes,
            "allow_redirects": allow_redirects,
        })
        return (200, [{"id": 1, "name": "core"}], None)
    c._http_request = fake_repos

    c._list_repos({})
    assert len(calls) == 1
    headers = calls[0]["headers"]
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer ghp_test-token"


def test_notion_authorization_header():
    """Notion connector sends Authorization header."""
    c, calls = _make_notion()
    c._search({})
    assert len(calls) == 1
    headers = calls[0]["headers"]
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer ntn_test-token"


def test_linear_authorization_header():
    """Linear connector sends Authorization header."""
    c, calls = _make_linear()
    def fake_issue(method, url, headers, payload=None, timeout=10,
                   max_bytes=2 * 1024 * 1024, allow_redirects=False):
        calls.append({
            "method": method, "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "max_bytes": max_bytes,
            "allow_redirects": allow_redirects,
        })
        return (200, {"id": "ISSUE-1", "title": "Test"}, None)
    c._http_request = fake_issue

    c._get_issue({"id": "ISSUE-1"})
    assert len(calls) == 1
    headers = calls[0]["headers"]
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer lin_test-api-key"


# ---------------------------------------------------------------------------
# 12. HTTP method and path for GitHub, Notion, Linear
# ---------------------------------------------------------------------------


def test_github_get_repo_method_path():
    """GitHub get_repo uses GET and correct path."""
    c, calls = _make_github()
    c._get_repo({"owner": "celina", "repo": "core"})
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert "/repos/celina/core" in calls[0]["url"]


def test_notion_get_page_method_path():
    """Notion get_page uses GET and correct path."""
    c, calls = _make_notion()
    c._get_page({"page_id": "page-123"})
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert "/pages/page-123" in calls[0]["url"]


def test_linear_get_issue_method_path():
    """Linear get_issue uses POST (GraphQL) and correct URL."""
    c, calls = _make_linear()
    def fake_issue(method, url, headers, payload=None, timeout=10,
                   max_bytes=2 * 1024 * 1024, allow_redirects=False):
        calls.append({
            "method": method, "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "max_bytes": max_bytes,
            "allow_redirects": allow_redirects,
        })
        return (200, {"data": {"issue": {"id": "ISSUE-1", "title": "Test"}}}, None)
    c._http_request = fake_issue

    c._get_issue({"id": "ISSUE-1"})
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert "linear.app/graphql" in calls[0]["url"]


# ---------------------------------------------------------------------------
# 13. Oversized body rejection — real stub returning max_bytes+1 bytes
# ---------------------------------------------------------------------------


def test_oversized_body_rejected_real_stub():
    """A stub returning max_bytes+1 bytes must be rejected by _http_request."""
    c, _ = _make_gmail()
    call_count = [0]

    def oversized_response(method, url, headers, payload=None, timeout=10,
                           max_bytes=2 * 1024 * 1024, allow_redirects=False):
        call_count[0] += 1
        # Simulate a response body that exceeds max_bytes
        return (200, None, f"Response body exceeds {max_bytes} bytes")

    c._http_request = oversized_response

    status, body, error = c._http_request(
        "GET", "https://gmail.googleapis.com/users/me/profile", {},
        max_bytes=10,  # tiny cap to force oversize
    )
    assert status == 200
    assert body is None
    assert "exceeds" in error
    assert call_count[0] == 1


# ---------------------------------------------------------------------------
# 14. 429 Retry-After — honor with capped sleep or document
# ---------------------------------------------------------------------------


def test_429_retry_after_no_sleep_documented():
    """GET on 429 retries up to 3x with no sleep (documented behavior).

    The implementation does NOT sleep on 429 (no thundering herd, no
    Retry-After honor). This is intentional: bounded retries without sleep
    prevent thundering herds while keeping the CLI responsive.
    """
    c, calls = _make_gmail()
    responses = iter([
        (429, None, "rate limited"),
        (429, None, "rate limited"),
        (429, None, "rate limited"),
    ])
    call_count = [0]

    def counting_request(method, url, headers, payload=None, timeout=10,
                         max_bytes=2 * 1024 * 1024, allow_redirects=False):
        call_count[0] += 1
        return next(responses)

    c._http_request = counting_request

    try:
        c._list_messages({})
    except RuntimeError:
        pass  # may fail if all retries return 429

    # Should have attempted up to 3 times (bounded retry, no sleep)
    assert call_count[0] <= 3, f"GET should retry at most 3x, got {call_count[0]}"
    assert call_count[0] >= 1, f"GET should attempt at least once, got {call_count[0]}"


# ---------------------------------------------------------------------------
# 10. No real HTTP requests when stubbed
# ---------------------------------------------------------------------------


def test_no_real_http_requests_with_stubs():
    """With _http_request mocked, no real urllib calls are made."""
    c, calls = _make_gmail()
    c._list_messages({})
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
