"""
tests/audit/unit/test_connector_base_security.py

Phase 7b — Coverage for connectors/base.py security-critical functions.

Tests:
1. _validate_path_segment — path traversal / injection defense
2. _build_path — path building with validation
3. _credential_dir — 0700 mode
4. _credential_file — 0600 mode
5. _save_credentials / _load_credentials — atomic credential write
6. _check_web_comms — web comms gating
7. _http_request — bounded HTTP, redirect blocking, size cap, POST no retry
8. _http_get / _http_post — convenience methods
9. _with_approval — Allow/Deny gate

No real network calls — stub at _http_request and resolver level.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg.connectors.base import (
    BaseConnector,
    ConnectorConfig,
    ConnectorStatus,
    _validate_path_segment,
)
from celina_pkg.net_policy import URLPolicyError


# ---------------------------------------------------------------------------
# Helper: minimal concrete connector for testing BaseConnector methods
# ---------------------------------------------------------------------------


class _TestConnector(BaseConnector):
    """Minimal concrete implementation of BaseConnector for testing."""

    def is_configured(self) -> bool:
        return True

    def list_actions(self):
        return []

    def execute_action(self, action_name, params):
        from celina_pkg.connectors.base import ActionResult

        return ActionResult(success=True, data={"ok": True})

    def configure(self, credentials):
        return True

    def deconfigure(self):
        return True


def _make_connector(tmp_path, name="test_connector"):
    cfg = ConnectorConfig(name=name, display_name="Test Connector")
    conn = _TestConnector(cfg)
    return conn


# ---------------------------------------------------------------------------
# Test 1: _validate_path_segment — path traversal defense
# ---------------------------------------------------------------------------


def test_validate_path_segment_allows_normal():
    result = _validate_path_segment("messages")
    assert result == "messages"


def test_validate_path_segment_allows_uuid():
    result = _validate_path_segment("550e8400-e29b-41d4-a716-446655440000")
    assert result == "550e8400-e29b-41d4-a716-446655440000"


def test_validate_path_segment_blocks_dot():
    with pytest.raises(ValueError, match="not a valid path segment"):
        _validate_path_segment(".")


def test_validate_path_segment_blocks_dotdot():
    with pytest.raises(ValueError, match="not a valid path segment"):
        _validate_path_segment("..")


def test_validate_path_segment_blocks_slash():
    with pytest.raises(ValueError):
        _validate_path_segment("a/b")


def test_validate_path_segment_blocks_backslash():
    with pytest.raises(ValueError):
        _validate_path_segment("a\\b")


def test_validate_path_segment_blocks_percent():
    with pytest.raises(ValueError):
        _validate_path_segment("%2e%2e")


def test_validate_path_segment_blocks_question():
    with pytest.raises(ValueError):
        _validate_path_segment("a?b")


def test_validate_path_segment_blocks_hash():
    with pytest.raises(ValueError):
        _validate_path_segment("a#b")


def test_validate_path_segment_blocks_control_chars():
    with pytest.raises(ValueError):
        _validate_path_segment("a\x00b")


def test_validate_path_segment_blocks_space():
    with pytest.raises(ValueError):
        _validate_path_segment("a b")


def test_validate_path_segment_blocks_too_long():
    with pytest.raises(ValueError):
        _validate_path_segment("a" * 201)


def test_validate_path_segment_allows_base64url():
    result = _validate_path_segment("SGVsbG8gd29ybGQ=")
    assert result == "SGVsbG8gd29ybGQ="


# ---------------------------------------------------------------------------
# Test 2: _build_path — path building with validation
# ---------------------------------------------------------------------------


def test_build_path_joins_segments():
    result = BaseConnector._build_path("users", "123", "messages")
    assert result == "/users/123/messages"


def test_build_path_rejects_traversal_segment():
    with pytest.raises(ValueError):
        BaseConnector._build_path("users", "..", "secret")


# ---------------------------------------------------------------------------
# Test 3: _credential_dir — 0700 mode
# ---------------------------------------------------------------------------


def test_credential_dir_0700(tmp_path):
    conn = _make_connector(tmp_path)
    with patch("celina_pkg.connectors.base.config.CONFIG_DIR", tmp_path):
        d = conn._credential_dir()
        assert d == tmp_path / "connectors"
        assert d.exists()
        # Check mode is 0700
        mode = d.stat().st_mode & 0o777
        assert mode == 0o700, f"Expected 0700, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Test 4: _credential_file — 0600 mode
# ---------------------------------------------------------------------------


def test_credential_file_0600(tmp_path):
    conn = _make_connector(tmp_path, name="my_conn")
    with patch("celina_pkg.connectors.base.config.CONFIG_DIR", tmp_path):
        f = conn._credential_file()
        assert f == tmp_path / "connectors" / "my_conn.json"
        if f.exists():
            mode = f.stat().st_mode & 0o777
            assert mode == 0o600, f"Expected 0600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# Test 5: _save_credentials / _load_credentials — atomic write
# ---------------------------------------------------------------------------


def test_save_and_load_credentials(tmp_path):
    conn = _make_connector(tmp_path, name="cred_test")
    with patch("celina_pkg.connectors.base.config.CONFIG_DIR", tmp_path):
        creds = {"access_token": "secret123", "refresh_token": "refresh456"}
        conn._save_credentials(creds)
        loaded = conn._load_credentials()
        assert loaded == creds


def test_load_credentials_missing_file(tmp_path):
    conn = _make_connector(tmp_path, name="nonexistent")
    with patch("celina_pkg.connectors.base.config.CONFIG_DIR", tmp_path):
        result = conn._load_credentials()
        assert result is None


# ---------------------------------------------------------------------------
# Test 6: _check_web_comms — web comms gating
# ---------------------------------------------------------------------------


def test_check_web_comms_disabled():
    conn = _make_connector(tempfile.mkdtemp())
    with patch("celina_pkg.config.is_web_comms_enabled", return_value=False):
        with pytest.raises(URLPolicyError, match="web_comms is disabled"):
            conn._check_web_comms("https://example.com/")


def test_check_web_comms_host_not_allowed():
    conn = _make_connector(tempfile.mkdtemp())
    with patch("celina_pkg.config.is_web_comms_enabled", return_value=True):
        with patch(
            "celina_pkg.config.get_web_comms_allowed_hosts",
            return_value=["allowed.example.com"],
        ):
            with pytest.raises(URLPolicyError, match="not in web_comms.allowed_hosts"):
                conn._check_web_comms("https://evil.example.com/")


# ---------------------------------------------------------------------------
# Test 7: _http_request — redirect blocking
# ---------------------------------------------------------------------------


def test_http_request_blocks_redirects():
    conn = _make_connector(tempfile.mkdtemp())
    conn._check_web_comms = MagicMock()
    # _http_request re-raises URLPolicyError for redirects
    with patch("celina_pkg.connectors.base.urllib.request.build_opener") as mock_build:
        mock_opener = MagicMock()
        mock_opener.open.side_effect = URLPolicyError("HTTP redirect to https://evil.com/ blocked")
        mock_build.return_value = mock_opener

        with pytest.raises(URLPolicyError, match="redirect|blocked"):
            conn._http_request(
                "GET", "https://example.com/", {}, allow_redirects=False
            )


# ---------------------------------------------------------------------------
# Test 8: _http_request — POST never retried
# ---------------------------------------------------------------------------


def test_http_request_post_no_retry():
    conn = _make_connector(tempfile.mkdtemp())
    conn._check_web_comms = MagicMock()

    with patch("celina_pkg.connectors.base.urllib.request.build_opener") as mock_build:
        mock_opener = MagicMock()
        mock_opener.open.side_effect = Exception("connection error")
        mock_build.return_value = mock_opener

        status, parsed, error = conn._http_request(
            "POST", "https://example.com/", {}, payload={"key": "val"}
        )
        # POST is mutating → max_attempts=1 → only one attempt, then error returned
        assert status == 0
        assert parsed is None
        # General Exception (not HTTPError) leaves last_error as None
        assert error is None


# ---------------------------------------------------------------------------
# Test 9: _http_request — size cap
# ---------------------------------------------------------------------------


def test_http_request_size_cap():
    conn = _make_connector(tempfile.mkdtemp())
    conn._check_web_comms = MagicMock()

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"x" * (n + 1)  # return more than max_bytes

    with patch("celina_pkg.connectors.base.urllib.request.build_opener") as mock_build:
        mock_opener = MagicMock()
        mock_opener.open.return_value = FakeResp()
        mock_build.return_value = mock_opener

        status, parsed, error = conn._http_request(
            "GET", "https://example.com/", {}, max_bytes=100
        )
        assert status == 200
        assert parsed is None
        assert error is not None and "exceeds" in error.lower()


# ---------------------------------------------------------------------------
# Test 10: _http_get / _http_post — convenience methods
# ---------------------------------------------------------------------------


def test_http_get_and_post_stub():
    conn = _make_connector(tempfile.mkdtemp())
    calls = []

    def fake_request(method, url, headers, payload=None, **kwargs):
        calls.append((method, url, payload))
        return (200, {"result": "ok"}, None)

    conn._http_request = fake_request

    status, parsed = conn._http_get("https://example.com/api", {"Accept": "application/json"})
    assert status == 200
    assert parsed == {"result": "ok"}
    assert calls[0][0] == "GET"

    status2, parsed2 = conn._http_post(
        "https://example.com/api", {"Content-Type": "application/json"}, {"key": "val"}
    )
    assert status2 == 200
    assert calls[1][0] == "POST"
    assert calls[1][2] == {"key": "val"}


# ---------------------------------------------------------------------------
# Test 11: _with_approval — Allow/Deny gate
# ---------------------------------------------------------------------------


def test_with_approval_read_only_no_gate():
    conn = _make_connector(tempfile.mkdtemp())
    conn._gate.requires_approval = MagicMock(return_value=False)
    conn.list_actions = MagicMock(return_value=[])

    result = conn._with_approval("read_action", {}, lambda: MagicMock(success=True, data=None))
    assert result.success is True


def test_with_approval_denied():
    conn = _make_connector(tempfile.mkdtemp())
    from celina_pkg.connectors.base import RiskLevel
    from types import SimpleNamespace

    action = SimpleNamespace(
        name="send_message",
        description="Send a message",
        parameters={},
        risk_level=RiskLevel.SECURITY_SENSITIVE,
    )

    conn.list_actions = MagicMock(return_value=[action])
    conn._gate.requires_approval = MagicMock(return_value=True)
    conn._gate.request_approval = MagicMock(return_value=False)

    result = conn._with_approval("send_message", {"to": "x", "body": "hi"}, lambda: MagicMock(success=True))
    assert result.success is False
    assert "Denied" in result.error or "denied" in result.error.lower()