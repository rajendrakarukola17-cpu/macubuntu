"""Tests for Item 1, 2, 5, 6, 7: offline guard, opt-in web_comms live tools, router OfflineFallback, config defaults & LIMITATIONS.md."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_pkg import config, net_policy, router, tools  # noqa: E402


def test_config_defaults_and_limitations_offline_cutoff():
    # Default config keys in neural-schema.json & runtime config
    schema = json.loads((REPO_ROOT / "features" / "celina" / "config" / "neural-schema.json").read_text(encoding="utf-8"))
    assert schema["web_comms"]["enabled"] is False
    assert schema["web_comms"]["allowed_hosts"] == []
    assert schema["libreoffice"]["uno_port"] == 2002
    assert schema["autonomy"]["observe_only"] is True

    assert config.WEB_COMMS_CFG["enabled"] is False
    assert config.WEB_COMMS_CFG["allowed_hosts"] == []
    assert config.get_libreoffice_uno_port() == 2002
    assert config.is_autonomy_observe_only() is True

    lim = (REPO_ROOT / "LIMITATIONS.md").read_text(encoding="utf-8")
    assert "Offline cutoff: local model frozen at training date; live data requires opt-in web_comms." in lim


def test_is_online_and_guarded_call_blocks_web_comms_when_disabled_or_offline(monkeypatch):
    # 1. is_online returns False on OSError
    def _fail_connect(*_a, **_kw):
        raise OSError("Network unreachable")

    monkeypatch.setattr(socket, "create_connection", _fail_connect)
    assert net_policy.is_online("1.1.1.1", 443, timeout=0.1) is False

    # 2. web_comms.enabled=false blocks web_comms even if online
    monkeypatch.setitem(config.WEB_COMMS_CFG, "enabled", False)
    with pytest.raises(net_policy.OfflineError, match="disabled"):
        net_policy.guarded_call("get_weather", lambda city: f"ok:{city}", "Hyderabad", online_probe=lambda: True)

    # 3. web_comms.enabled=true + offline -> raises OfflineError
    monkeypatch.setitem(config.WEB_COMMS_CFG, "enabled", True)
    with pytest.raises(net_policy.OfflineError, match="offline"):
        net_policy.guarded_call("get_weather", lambda city: f"ok:{city}", "Hyderabad", online_probe=lambda: False)

    # 4. Non-web_comms tool is NEVER blocked while offline
    assert net_policy.guarded_call("get_system_stats", lambda: "cpu=5%", online_probe=lambda: False) == "cpu=5%"

    # 5. Online + enabled + allowed_hosts enforcement
    monkeypatch.setitem(config.WEB_COMMS_CFG, "allowed_hosts", ["example.com"])
    with pytest.raises(net_policy.URLPolicyError, match="allowed_hosts"):
        net_policy.guarded_call(
            "fetch_url_text",
            lambda url: url,
            "https://evil.org/data",
            online_probe=lambda: True,
        )
    assert (
        net_policy.guarded_call(
            "fetch_url_text",
            lambda url: f"fetched:{url}",
            "https://docs.example.com/page",
            online_probe=lambda: True,
        )
        == "fetched:https://docs.example.com/page"
    )


def test_four_opt_in_live_data_tools_metadata_and_execution(monkeypatch):
    # All 4 live-data tools + fetch_webpage are explicitly marked opt_in=True in web_comms
    for name in ("get_weather", "get_news_headlines", "get_current_time_zone", "fetch_url_text", "fetch_webpage"):
        fn = tools._AUTO_TOOL_MAP[name]
        assert getattr(fn, "opt_in", False) is True
        assert getattr(fn, "domain", "") == "web_comms"
        assert name in tools.OPT_IN_TOOLS

    # When offline or disabled, direct calls raise OfflineError & execute_tool degrades cleanly
    monkeypatch.setitem(config.WEB_COMMS_CFG, "enabled", False)
    with pytest.raises(net_policy.OfflineError):
        tools.get_weather("Hyderabad")
    with pytest.raises(net_policy.OfflineError):
        tools.get_news_headlines("linux")
    with pytest.raises(net_policy.OfflineError):
        tools.get_current_time_zone("Tokyo")
    with pytest.raises(net_policy.OfflineError):
        tools.fetch_url_text("https://example.com")

    # execute_tool returns clean offline fallback or Gate 4.7 fail-closed denial without crashing
    out = tools.execute_tool("get_weather", {"city": "Hyderabad"})
    assert "Offline fallback" in out or out.startswith("Denied:")

    # When enabled + online, all 4 tools succeed via net_policy.safe_get (mocked)
    monkeypatch.setitem(config.WEB_COMMS_CFG, "enabled", True)
    monkeypatch.setitem(config.WEB_COMMS_CFG, "allowed_hosts", [])
    monkeypatch.setattr(net_policy, "is_online", lambda *_a, **_kw: True)
    monkeypatch.setattr(
        net_policy,
        "validate_public_url",
        lambda url, **_kw: net_policy.ValidatedURL(url, "https", "example.com", 443, ("93.184.216.34",)),
    )

    def _fake_safe_get(url, **_kw):
        if "wttr.in" in url:
            body = json.dumps(
                {
                    "current_condition": [
                        {"temp_C": "29", "FeelsLikeC": "31", "humidity": "55", "windspeedKmph": "12", "weatherDesc": [{"value": "Sunny"}]}
                    ]
                }
            ).encode()
            return net_policy.SafeHTTPResponse(url, 200, "application/json", body, {})
        if "news.google.com" in url:
            xml_body = b"<rss><channel><item><title>Ubuntu 26.04 Released</title><pubDate>Sat, 14 Nov 2026</pubDate></item></channel></rss>"
            return net_policy.SafeHTTPResponse(url, 200, "application/rss+xml", xml_body, {})
        if "timeapi.io" in url:
            body = json.dumps({"timeZone": "Asia/Tokyo", "dateTime": "2026-11-14T21:00:00"}).encode()
            return net_policy.SafeHTTPResponse(url, 200, "application/json", body, {})
        return net_policy.SafeHTTPResponse(url, 200, "text/html", b"<html><body>Hello Live Web</body></html>", {})

    monkeypatch.setattr(net_policy, "safe_get", _fake_safe_get)
    assert "Sunny" in tools.get_weather("Hyderabad") and "29°C" in tools.get_weather("Hyderabad")
    assert "Ubuntu 26.04 Released" in tools.get_news_headlines("ubuntu")
    assert "Asia/Tokyo" in tools.get_current_time_zone("Tokyo")
    assert "Hello Live Web" in tools.fetch_url_text("https://example.com/live")


def test_router_returns_offline_fallback_for_web_comms(monkeypatch):
    monkeypatch.setitem(config.WEB_COMMS_CFG, "enabled", True)
    decision = router.RouteDecision(tool_name="get_weather", domain="web_comms", tier=1)
    res_off = router.apply_offline_and_uno_policy(decision, online_probe=lambda: False)
    assert isinstance(res_off, router.OfflineFallback)
    assert res_off.tool == "respond_offline"
    assert "offline" in res_off.message.lower()

    res_on = router.apply_offline_and_uno_policy(decision, online_probe=lambda: True)
    assert isinstance(res_on, router.RouteDecision)
    assert res_on.tool_name == "get_weather"

    # Also through CelinaRouter.route_for_autonomy
    rt = router.CelinaRouter()
    dec = rt.route_for_autonomy("check get_weather in Hyderabad", context={"online_probe": lambda: False})
    assert dec["tool_name"] == "respond_offline"
    assert dec["offline_fallback"] is True
