"""
Security Test Suite Part 1:
  - Phase 3: Centralized SSRF, DNS-rebinding, redirect & IoT webhook policy (net_policy.py)
  - Phase 4: Risk-tiered argument-bound approvals, expiration, single-use & MCP hardening
  - Phase 2: Private permissions (0700/0600) & sensitive-data cache exclusions
"""

import json
import os
import socket
import stat
import time
from pathlib import Path
from unittest import mock

import pytest

from celina_pkg import approval, cache, config, mcp_client, memory, net_policy, tools


# ============================================================================
# 1. Centralized Network Policy: SSRF, DNS Rebinding, Redirects & IoT Allowlist
# ============================================================================

class TestNetPolicySSRF:
    def test_blocks_http_file_and_credentials_in_public_url(self):
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_public_url("http://example.com")
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_public_url("file:///etc/passwd")
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_public_url("https://user:pass@example.com/secret")
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_public_url("https://example.com:8443/")

    @pytest.mark.parametrize(
        "blocked_url",
        [
            "https://127.0.0.1/",
            "https://localhost/",
            "https://[::1]/",
            "https://10.0.0.1/admin",
            "https://172.16.5.10/",
            "https://192.168.1.1/",
            "https://169.254.169.254/latest/meta-data/",
            "https://metadata.google.internal/computeMetadata/v1/",
            "https://printer.local/",
        ],
    )
    def test_blocks_private_loopback_and_metadata_urls(self, blocked_url: str):
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_public_url(blocked_url)

    def test_blocks_dns_rebinding_to_private_or_metadata_ip(self):
        def evil_resolver(_host: str, _port: int):
            return ["169.254.169.254"]

        with pytest.raises(net_policy.URLPolicyError, match="Blocked resolved IP"):
            net_policy.validate_public_url("https://rebind.attacker.example/data", resolver=evil_resolver)

    def test_blocks_dns_rebinding_mixed_public_and_private_ips(self):
        def mixed_resolver(_host: str, _port: int):
            return ["93.184.216.34", "10.0.0.5"]

        with pytest.raises(net_policy.URLPolicyError, match="Blocked resolved IP"):
            net_policy.validate_public_url("https://dual.attacker.example/", resolver=mixed_resolver)

    def test_allows_public_https_ip_pinning(self):
        def ok_resolver(_host: str, _port: int):
            return ["93.184.216.34"]

        target = net_policy.validate_public_url("https://example.com/article", resolver=ok_resolver)
        assert target.host == "example.com"
        assert target.port == 443
        assert target.resolved_ips == ("93.184.216.34",)

    def test_blocks_redirect_chain_to_internal_ip(self):
        def resolver(host: str, _port: int):
            if host == "public.example":
                return ["93.184.216.34"]
            if host == "internal.example":
                return ["127.0.0.1"]
            raise net_policy.URLPolicyError("unknown host")

        class FakeResp:
            status_code = 302
            headers = {"Location": "https://internal.example/secret", "Content-Type": "text/html"}
            raw = None

            def iter_content(self, chunk_size: int = 8192):
                return iter([])

            def close(self):
                pass

        fake_session = mock.MagicMock()
        fake_session.request.return_value = FakeResp()

        with pytest.raises(net_policy.URLPolicyError, match="Blocked resolved IP"):
            net_policy.safe_get(
                "https://public.example/go",
                allow_redirects=True,
                max_redirects=3,
                resolver=resolver,
                session=fake_session,
            )

    def test_blocks_oversized_response_and_bad_content_type(self):
        def ok_resolver(_host: str, _port: int):
            return ["93.184.216.34"]

        class BigResp:
            status_code = 200
            headers = {"Content-Type": "text/plain"}
            raw = None

            def iter_content(self, chunk_size: int = 8192):
                yield b"A" * 2048

            def close(self):
                pass

        fake_session = mock.MagicMock()
        fake_session.request.return_value = BigResp()
        with pytest.raises(net_policy.URLPolicyError, match="max byte|exceeded|limit|size"):
            net_policy.safe_get("https://example.com/big", max_bytes=1024, resolver=ok_resolver, session=fake_session)

        class ExeResp:
            status_code = 200
            headers = {"Content-Type": "application/x-msdownload"}
            raw = None

            def iter_content(self, chunk_size: int = 8192):
                yield b"MZ"

            def close(self):
                pass

        fake_session.request.return_value = ExeResp()
        with pytest.raises(net_policy.URLPolicyError, match="Content-Type"):
            net_policy.safe_get(
                "https://example.com/a",
                allowed_content_types={"text/plain"},
                resolver=ok_resolver,
                session=fake_session,
            )


class TestIoTAllowlist:
    def test_iot_blocks_unlisted_host_metadata_and_ollama(self, tmp_path: Path):
        allow_file = tmp_path / "iot-allowlist.json"
        allow_file.write_text(
            json.dumps(
                {
                    "allowed_hosts": ["192.168.1.50"],
                    "allowed_ports": [8123],
                    "allowed_methods": ["POST"],
                }
            ),
            encoding="utf-8",
        )
        rules = net_policy.load_iot_allowlist(allow_file)

        # Allowed exact rule passes validation
        t = net_policy.validate_iot_url("http://192.168.1.50:8123/api/webhook/lights", "POST", allowlist=rules)
        assert t.host == "192.168.1.50"

        # Disallowed method / port / host / metadata / Ollama port 11434 all blocked
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_iot_url("http://192.168.1.50:8123/api/webhook/lights", "GET", allowlist=rules)
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_iot_url("http://192.168.1.50:9999/api/webhook/lights", "POST", allowlist=rules)
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_iot_url("http://192.168.1.99:8123/api/webhook/lights", "POST", allowlist=rules)
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_iot_url("http://169.254.169.254/latest/meta-data/", "POST", allowlist=rules)
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.validate_iot_url("http://127.0.0.1:11434/api/generate", "POST", allowlist=rules)

    def test_trigger_iot_webhook_denies_when_not_allowlisted(self, tmp_path: Path):
        empty_allow = tmp_path / "iot.json"
        empty_allow.write_text('{"endpoints": []}', encoding="utf-8")
        res = tools.trigger_iot_webhook(
            "http://192.168.1.50:8123/api/webhook/test",
            approval_cb=lambda _t, _d: True,
            allowlist_path=empty_allow,
        )
        assert res.startswith("Blocked by IoT network policy:")


# ============================================================================
# 2. Approval Binding, Expiration, Single-Use Replay & MCP Policy
# ============================================================================

class TestApprovalEngine:
    def test_argument_binding_tamper_expiry_and_replay_rejection(self):
        args = {"path": "/home/user/demo.txt", "content_len": 12}
        tok, err = approval.request_bound_approval(
            "write_file",
            args,
            approval_cb=lambda _t, _d: True,
            ttl_seconds=30.0,
        )
        assert err is None and tok is not None

        # Tampered arguments MUST fail
        ok, msg = approval.verify_and_consume(tok, "write_file", {"path": "/home/user/evil.txt", "content_len": 12})
        assert not ok and ("changed" in msg.lower() or "mismatch" in msg.lower())

        # Tampered tool name MUST fail
        ok, msg = approval.verify_and_consume(tok, "trash_file", args)
        assert not ok and ("mismatch" in msg.lower() or "does not match" in msg.lower())

        # Exact match succeeds ONCE
        ok, msg = approval.verify_and_consume(tok, "write_file", args)
        assert ok and msg == ""

        # Replay of consumed single-use token MUST fail
        ok, msg = approval.verify_and_consume(tok, "write_file", args)
        assert not ok and "already consumed" in msg.lower()

        # Expired token MUST fail
        tok2, _ = approval.request_bound_approval("write_file", args, approval_cb=lambda _t, _d: True, ttl_seconds=1.0)
        assert tok2 is not None
        ok, msg = approval.verify_and_consume(tok2, "write_file", args, now=tok2.expires_at + 5.0)
        assert not ok and "expired" in msg.lower()

    def test_high_risk_actions_require_fresh_confirmation(self):
        for high_tool in (
            "run_shell_command",
            "trash_file",
            "move_file",
            "compose_message",
            "trigger_iot_webhook",
            "mcp_call_tool",
            "install_community_skill",
            "approve_quarantined_skill",
            "click_position",
            "drag_and_drop",
        ):
            assert approval.requires_fresh_confirmation(high_tool)

        # Typing into a terminal or password window ALWAYS escalates
        assert approval.requires_fresh_confirmation("type_text", {"text": "ls"}, active_window="gnome-terminal")
        assert approval.requires_fresh_confirmation("type_text", {"text": "x"}, active_window="Unlock Keyring")


class TestMCPSecurity:
    def test_unknown_mcp_tools_with_read_prefixes_still_require_approval(self, tmp_path: Path):
        cfg = tmp_path / "mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "servers": {
                        "demo": {
                            "enabled": True,
                            "transport": "stdio",
                            "command": ["echo"],
                            "auto_approve_read_only": True,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        mgr = mcp_client.MCPManager(cfg_path=cfg)

        # Even with get_/list_/read_/search_ prefixes AND readOnlyHint=True, approval MUST be demanded!
        for tricky_name in ("get_secrets", "list_credentials", "read_private_key", "search_tokens"):
            res = mgr.call_tool(
                "demo",
                tricky_name,
                {"q": "all"},
                approval_cb=None,
                server_annotation_hint={"readOnlyHint": True},
            )
            assert res.startswith("Denied:")

        # Cancelling approval blocks dispatch
        prompts: list[str] = []
        res = mgr.call_tool(
            "demo",
            "get_secrets",
            {"q": "all"},
            approval_cb=lambda title, detail: (prompts.append(f"{title}\n{detail}"), False)[1],
            server_annotation_hint={"readOnlyHint": True},
        )
        assert res == "Cancelled by user."
        assert prompts and "unverified" in prompts[0].lower()

    def test_mcp_http_transport_blocks_ssrf(self, tmp_path: Path):
        cfg = tmp_path / "mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "servers": {
                        "evil": {
                            "enabled": True,
                            "transport": "http",
                            "url": "http://169.254.169.254/latest/meta-data/",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        mgr = mcp_client.MCPManager(cfg_path=cfg)
        res = mgr.call_tool("evil", "dump", {}, approval_cb=lambda _t, _d: True)
        assert "blocked by network policy" in res.lower()


# ============================================================================
# 3. Private Permissions (0700 / 0600) & Sensitive Cache Exclusions
# ============================================================================

class TestPrivacyAndPermissions:
    def test_memory_and_cache_db_enforce_0700_0600(self, tmp_path: Path):
        priv_dir = tmp_path / "priv_celina"
        mem = memory.MemoryDB(db_path=priv_dir / "memory.db")
        sc = cache.SmartCache(db_path=priv_dir / "cache.db")
        try:
            assert stat.S_IMODE(priv_dir.stat().st_mode) == 0o700
            assert stat.S_IMODE((priv_dir / "memory.db").stat().st_mode) == 0o600
            assert stat.S_IMODE((priv_dir / "cache.db").stat().st_mode) == 0o600
        finally:
            mem.close()
            sc.close()

    def test_cache_refuses_clipboard_biometrics_passwords_and_screenshots(self, tmp_path: Path):
        sc = cache.SmartCache(db_path=tmp_path / "cache.db")
        try:
            assert sc.set("clipboard", "k1", "some clipboard snippet") is False
            assert sc.set("biometric", "k2", "finger status") is False
            assert sc.set("screenshot", "k3", "raw screen capture") is False
            assert sc.set("llm_fast", "k4", "data:image/jpeg;base64,/9j/4AAQ...") is False
            assert sc.set("llm_fast", "k5", "-----BEGIN PRIVATE KEY-----\nabc") is False
            assert sc.set("llm_fast", "k6", "fingerprint_template=abc123") is False
            assert sc.set("llm_fast", "k7", "password=hunter2") is False
            # Safe public answer caches cleanly
            assert sc.set("llm_fast", "capital of france", "Paris is the capital of France.") is True
        finally:
            sc.close()
