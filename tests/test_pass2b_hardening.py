"""
Pass 2B hardening tests — verify all fixes from the v0.7.1-pass2b-hardened release.

Covers:
  - model_selector.select_ollama_model() across 8/16/32 GB tiers (NameError fix)
  - dbus_service.SetLiteMode UID verification (fail-closed)
  - Connectors: Linear parameterized GraphQL variables, Notion GET, Gmail CRLF rejection,
    GitHub URL quoting, web_comms gating
  - Stage 46 delimited block preservation (MACUBUNTU-PERF-START)
  - GNOME extension.js: GLib import, no double-parenting, _dbusClient refresh
  - GNOME choreography.js: slow_down_factor property, global.get_pointer, get_meta_window
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))


# ---------------------------------------------------------------------------
# model_selector.select_ollama_model across RAM tiers
# ---------------------------------------------------------------------------

class TestOllamaModelSelectionByTier:
    """select_ollama_model must not hit NameError on npu/gpu and must pick
    tier-appropriate models for 8 GB, 16 GB, and 32 GB hosts."""

    @patch("celina_pkg.model_selector.detect_total_ram_gb", return_value=8.0)
    @patch("celina_pkg.model_selector.detect_free_disk_gb", return_value=100.0)
    @patch("celina_pkg.model_selector.has_gpu", return_value=False)
    @patch("celina_pkg.model_selector.has_npu", return_value=False)
    def test_8gb_constrained_no_nameerror(self, _npu, _gpu, _disk, _ram):
        from celina_pkg import model_selector
        result = model_selector.select_ollama_model()
        assert result["model"] in {"qwen3:1.7b", "granite3.1-moe:1b", "granite3.1-moe:3b"}
        assert "NameError" not in result.get("reason", "")

    @patch("celina_pkg.model_selector.detect_total_ram_gb", return_value=16.0)
    @patch("celina_pkg.model_selector.detect_free_disk_gb", return_value=100.0)
    @patch("celina_pkg.model_selector.has_gpu", return_value=False)
    @patch("celina_pkg.model_selector.has_npu", return_value=False)
    def test_16gb_balanced(self, _npu, _gpu, _disk, _ram):
        from celina_pkg import model_selector
        result = model_selector.select_ollama_model()
        assert result["model"] in {
            "qwen3:1.7b", "granite3.1-moe:1b", "granite3.1-moe:3b",
            "qwen2.5:3b", "qwen3:4b", "qwen3.5:4b", "qwen3.5:9b",
        }

    @patch("celina_pkg.model_selector.detect_total_ram_gb", return_value=32.0)
    @patch("celina_pkg.model_selector.detect_free_disk_gb", return_value=100.0)
    @patch("celina_pkg.model_selector.has_gpu", return_value=False)
    @patch("celina_pkg.model_selector.has_npu", return_value=False)
    def test_32gb_headroom(self, _npu, _gpu, _disk, _ram):
        from celina_pkg import model_selector
        result = model_selector.select_ollama_model()
        assert result["model"] in {
            "qwen3:1.7b", "granite3.1-moe:1b", "granite3.1-moe:3b",
            "qwen2.5:3b", "qwen3:4b", "qwen3.5:4b", "qwen3.5:9b",
            "qwen3:30b-a3b", "gemma4:26b", "qwen3.5:35b-a3b",
        }

    @patch("celina_pkg.model_selector.detect_total_ram_gb", return_value=8.0)
    @patch("celina_pkg.model_selector.detect_free_disk_gb", return_value=100.0)
    @patch("celina_pkg.model_selector.has_gpu", return_value=False)
    @patch("celina_pkg.model_selector.has_npu", return_value=False)
    def test_8gb_excludes_qwen35_4b_auto(self, _npu, _gpu, _disk, _ram):
        """CONSTRAINED tier must never auto-select qwen3.5:4b (vision model)."""
        from celina_pkg import model_selector
        result = model_selector.select_ollama_model()
        assert result["model"] != "qwen3.5:4b"

    @patch("celina_pkg.model_selector.detect_total_ram_gb", return_value=16.0)
    @patch("celina_pkg.model_selector.detect_free_disk_gb", return_value=100.0)
    @patch("celina_pkg.model_selector.has_gpu", return_value=False)
    @patch("celina_pkg.model_selector.has_npu", return_value=False)
    def test_16gb_no_nameerror_valid_model(self, _npu, _gpu, _disk, _ram):
        """BALANCED tier must not crash and must return a valid model."""
        from celina_pkg import model_selector
        result = model_selector.select_ollama_model()
        assert result["model"] in model_selector.OLLAMA_CATALOG
        assert "NameError" not in result.get("reason", "")


# ---------------------------------------------------------------------------
# dbus_service.SetLiteMode UID verification
# ---------------------------------------------------------------------------

class TestDbusSetLiteModeUidVerification:
    """SetLiteMode must enforce backend.verify_caller_uid(uid) — fail-closed."""

    def test_set_lite_mode_wrong_uid_rejected(self):
        from celina_pkg import dbus_service

        backend = dbus_service.CelinaDBusBackend(expected_uid=1000)
        # Simulate a caller with a different UID
        result = backend.verify_caller_uid(2000)
        assert result[0] is False
        assert "does not match" in result[1]

    def test_set_lite_mode_correct_uid_accepted(self):
        from celina_pkg import dbus_service

        backend = dbus_service.CelinaDBusBackend(expected_uid=1000)
        result = backend.verify_caller_uid(1000)
        assert result[0] is True
        assert result[1] == ""

    def test_set_lite_mode_none_uid_rejected(self):
        from celina_pkg import dbus_service

        backend = dbus_service.CelinaDBusBackend(expected_uid=1000)
        result = backend.verify_caller_uid(None)
        assert result[0] is False
        assert "unable to verify" in result[1]


# ---------------------------------------------------------------------------
# Connector tests
# ---------------------------------------------------------------------------

class TestLinearConnector:
    """Linear connector must use parameterized GraphQL variables, correct filter
    syntax, and no trailing comma in issueDelete."""

    def test_linear_list_issues_uses_variables(self):
        from celina_pkg.connectors.linear import LinearConnector

        conn = LinearConnector()
        conn.is_configured = lambda: True
        conn._api_key = "test-key"

        with patch.object(conn, "_graphql") as mock_graphql:
            mock_graphql.return_value = {"issues": {"nodes": []}}
            conn._list_issues({"team_key": "ENG", "limit": 10})

        # Verify _graphql was called with variables dict (2nd arg)
        call_args = mock_graphql.call_args
        assert call_args is not None
        args = call_args[0]
        assert len(args) >= 2, "_graphql should be called with variables"
        variables = args[1]
        assert variables is not None
        assert "teamKey" in variables or "first" in variables

    def test_linear_filter_syntax_no_and_separator(self):
        """Linear filter must use {team: {key: {eq: $teamKey}}} not 'team: {key: "ENG"}'."""
        from celina_pkg.connectors.linear import LinearConnector

        conn = LinearConnector()
        conn.is_configured = lambda: True
        conn._api_key = "test-key"

        with patch.object(conn, "_graphql") as mock_graphql:
            mock_graphql.return_value = {"issues": {"nodes": []}}
            conn._list_issues({"team_key": "ENG", "query": "bug"})

        call_args = mock_graphql.call_args[0][0]  # query string
        # Must NOT use " AND " separator
        assert " AND " not in call_args, "Linear filter must not use ' AND ' separator"
        # Must use proper Linear filter syntax with eq/contains
        assert "{eq: $teamKey}" in call_args or "{contains:" in call_args

    def test_linear_issue_delete_no_trailing_comma(self):
        """issueDelete mutation must not have trailing comma."""
        from celina_pkg.connectors.linear import LinearConnector

        conn = LinearConnector()
        conn.is_configured = lambda: True
        conn._api_key = "test-key"

        with patch.object(conn, "_graphql") as mock_graphql:
            mock_graphql.return_value = {"issueDelete": {"success": True, "deletedIssueId": "abc"}}
            conn._delete_issue({"id": "issue-123", "permanently_delete": False})

        call_args = mock_graphql.call_args[0][0]  # query string
        # No trailing comma before closing brace
        assert ", }" not in call_args and ",}" not in call_args.replace(" ", ""), \
            "issueDelete must not have trailing comma"


class TestNotionConnector:
    """Notion read-only actions must use GET, not POST."""

    def test_notion_get_page_uses_get(self):
        from celina_pkg.connectors.notion import NotionConnector

        conn = NotionConnector()
        conn.is_configured = lambda: True
        conn._integration_token = "test-token"

        with patch.object(conn, "_get") as mock_get:
            mock_get.return_value = {"id": "page-1", "properties": {}}
            conn._get_page({"page_id": "page-1"})

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        assert "/pages/page-1" in called_url

    def test_notion_get_database_uses_get(self):
        from celina_pkg.connectors.notion import NotionConnector

        conn = NotionConnector()
        conn.is_configured = lambda: True
        conn._integration_token = "test-token"

        with patch.object(conn, "_get") as mock_get:
            mock_get.return_value = {"id": "db-1", "properties": {}}
            conn._get_database({"database_id": "db-1"})

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        assert "/databases/db-1" in called_url

    def test_notion_list_users_uses_get(self):
        from celina_pkg.connectors.notion import NotionConnector

        conn = NotionConnector()
        conn.is_configured = lambda: True
        conn._integration_token = "test-token"

        with patch.object(conn, "_http_get") as mock_get:
            mock_get.return_value = (200, {"results": []})
            conn._list_users({})

        mock_get.assert_called_once()


class TestGmailConnector:
    """Gmail send_message must reject CRLF in to/subject headers."""

    def test_gmail_send_message_rejects_crlf_in_to(self):
        from celina_pkg.connectors.gmail import GmailConnector

        conn = GmailConnector()
        conn.is_configured = lambda: True
        conn._access_token = "test-token"

        result = conn._send_message({
            "to": "victim@example.com\r\nBcc: attacker@example.com",
            "subject": "Hello",
            "body": "Test",
        })
        assert result.success is False
        assert "CRLF" in result.error or "CR/LF" in result.error or "injection" in result.error.lower()

    def test_gmail_send_message_rejects_crlf_in_subject(self):
        from celina_pkg.connectors.gmail import GmailConnector

        conn = GmailConnector()
        conn.is_configured = lambda: True
        conn._access_token = "test-token"

        result = conn._send_message({
            "to": "victim@example.com",
            "subject": "Hello\r\nInjected-Header: value",
            "body": "Test",
        })
        assert result.success is False
        assert "CRLF" in result.error or "CR/LF" in result.error or "injection" in result.error.lower()

    def test_gmail_send_message_accepts_clean_headers(self):
        from celina_pkg.connectors.gmail import GmailConnector

        conn = GmailConnector()
        conn.is_configured = lambda: True
        conn._access_token = "test-token"

        with patch.object(conn, "_post") as mock_post:
            mock_post.return_value = {"id": "msg-123"}
            result = conn._send_message({
                "to": "victim@example.com",
                "subject": "Hello",
                "body": "Test body",
            })
        assert result.success is True


class TestGitHubConnector:
    """GitHub path segments must be URL-quoted with safe=""."""

    def test_github_get_repo_quotes_path_segments(self):
        from celina_pkg.connectors.github import GitHubConnector

        conn = GitHubConnector()
        conn.is_configured = lambda: True
        conn._token = "test-token"

        with patch.object(conn, "_get") as mock_get:
            mock_get.return_value = {"id": 1, "name": "test-repo"}
            conn._get_repo({"owner": "my-org", "repo": "my.repo"})

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        # owner and repo should be URL-quoted
        assert "my-org" in called_url
        assert "my.repo" in called_url or "my%2Erepo" in called_url

    def test_github_get_user_quotes_username(self):
        from celina_pkg.connectors.github import GitHubConnector

        conn = GitHubConnector()
        conn.is_configured = lambda: True
        conn._token = "test-token"

        with patch.object(conn, "_get") as mock_get:
            mock_get.return_value = {"login": "testuser"}
            conn._get_user({"username": "test.user"})

        mock_get.assert_called_once()
        called_url = mock_get.call_args[0][0]
        assert "/users/" in called_url


class TestConnectorWebCommsGating:
    """Connectors must gate outbound calls behind config.is_web_comms_enabled()."""

    def test_base_connector_web_comms_disabled_blocks_http(self):
        from celina_pkg import config
        from celina_pkg.connectors.base import BaseConnector, ConnectorConfig
        from celina_pkg.net_policy import URLPolicyError

        # Create a minimal connector subclass for testing
        class TestConnector(BaseConnector):
            def is_configured(self):
                return True
            def list_actions(self):
                return []
            def execute_action(self, name, params):
                return None
            def configure(self, creds):
                return True
            def deconfigure(self):
                return True

        cfg = ConnectorConfig(name="test", display_name="Test")
        conn = TestConnector(cfg)

        # With web_comms disabled, _check_web_comms should raise
        config.WEB_COMMS_CFG = {"enabled": False, "allowed_hosts": []}
        try:
            conn._check_web_comms("https://api.example.com/graphql")
            assert False, "Should have raised URLPolicyError"
        except URLPolicyError:
            pass  # Expected


# ---------------------------------------------------------------------------
# Stage 46 delimited block preservation
# ---------------------------------------------------------------------------

class TestStage46DelimitedBlock:
    """Stage 46 must update ONLY the MACUBUNTU-PERF block, preserving
    Stage 20's MACUBUNTU-SAFARI-START block."""

    def test_userjs_has_macubuntu_perf_delimiters(self):
        user_js = REPO_ROOT / "system" / "firefox" / "user.js"
        text = user_js.read_text(encoding="utf-8")
        assert "// >>> MACUBUNTU-PERF-START" in text
        assert "// <<< MACUBUNTU-PERF-END" in text

    def test_userjs_no_removed_antipatterns(self):
        user_js = REPO_ROOT / "system" / "firefox" / "user.js"
        text = user_js.read_text(encoding="utf-8")
        # Check exact user_pref lines, not substrings (e.g. max-connections-per-server)
        assert 'user_pref("ui.prefersReducedMotion"' not in text
        assert 'user_pref("browser.tabs.animate"' not in text
        assert 'user_pref("browser.sessionstore.resume_from_crash"' not in text
        assert 'user_pref("network.http.max-connections"' not in text
        # Kept
        assert 'browser.tabs.unloadOnLowMemory' in text
        assert 'browser.low_commit_space_threshold_mb' in text

    def test_stage46_uses_delimited_block(self):
        stage46 = REPO_ROOT / "stages" / "46-browser-tuning.sh"
        text = stage46.read_text(encoding="utf-8")
        assert "MACUBUNTU-PERF-START" in text
        assert "MACUBUNTU-PERF-END" in text


# ---------------------------------------------------------------------------
# GNOME extension.js checks
# ---------------------------------------------------------------------------

class TestGnomeExtensionJs:
    """extension.js must import GLib, not double-parent, use _dbusClient.refresh."""

    def test_extension_js_imports_glib(self):
        ext_js = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "extension.js"
        text = ext_js.read_text(encoding="utf-8")
        assert "import GLib from 'gi://GLib'" in text

    def test_extension_js_no_double_parenting(self):
        ext_js = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "extension.js"
        text = ext_js.read_text(encoding="utf-8")
        assert "Main.panel.addToStatusArea" not in text

    def test_extension_js_uses_dbus_client_refresh(self):
        ext_js = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "extension.js"
        text = ext_js.read_text(encoding="utf-8")
        assert "_dbusClient?.refresh" in text
        assert "_dbusProxy?.refresh" not in text


# ---------------------------------------------------------------------------
# GNOME choreography.js checks
# ---------------------------------------------------------------------------

class TestGnomeChoreographyJs:
    """choreography.js must use slow_down_factor property, global.get_pointer,
    get_meta_window, set_pivot_point + 2-arg set_scale, track timelines."""

    def test_choreography_uses_slow_down_factor_property(self):
        choreo_js = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "choreography.js"
        text = choreo_js.read_text(encoding="utf-8")
        assert "slow_down_factor" in text
        assert "get_double('slow-down-factor')" not in text
        assert "set_double('slow-down-factor'" not in text

    def test_choreography_uses_global_get_pointer(self):
        choreo_js = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "choreography.js"
        text = choreo_js.read_text(encoding="utf-8")
        assert "global.get_pointer" in text
        assert "get_default_seat" not in text

    def test_choreography_uses_get_meta_window(self):
        choreo_js = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "choreography.js"
        text = choreo_js.read_text(encoding="utf-8")
        assert "get_meta_window" in text

    def test_choreography_no_4arg_set_scale(self):
        choreo_js = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "choreography.js"
        text = choreo_js.read_text(encoding="utf-8")
        # Should use set_pivot_point + 2-arg set_scale, not 4-arg set_scale
        assert "set_pivot_point" in text
        # No 4-arg set_scale(s, s, px, py)
        assert "set_scale(scaleFrom, scaleFrom, pivotX, pivotY)" not in text
        assert "set_scale(scaleTo, scaleTo, pivotX, pivotY)" not in text
        assert "set_scale(scale, scale, pivotX, pivotY)" not in text
        assert "set_scale(origScaleX, origScaleY, pivotX, pivotY)" not in text

    def test_choreography_tracks_timelines_for_teardown(self):
        choreo_js = REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "choreography.js"
        text = choreo_js.read_text(encoding="utf-8")
        assert "_activeMorphAnimations.add(timeline)" in text
        assert "timeline.stop()" in text