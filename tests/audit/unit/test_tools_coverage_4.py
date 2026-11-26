"""
tests/audit/unit/test_tools_coverage_4.py

Phase 7d — TASK 1a: tools.py branch coverage tests (round 4).
Targets _mpris_dbus_control, fetch_webpage, fetch_url_text, get_weather,
get_news_headlines, get_current_time_zone.

No real network calls. Mocks gi/D-Bus, net_policy, cache, and subprocess.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


class _FakeResult:
    def __init__(self, ok=True, stdout="", stderr="", error="", message="", timed_out=False, returncode=0, status_code=200, text=""):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.message = message
        self.timed_out = timed_out
        self.returncode = returncode
        self.status_code = status_code
        self.text = text


# ---------------------------------------------------------------------------
# _mpris_dbus_control
# ---------------------------------------------------------------------------


def test_mpris_dbus_control_dbus_unavailable(monkeypatch):
    """CB: _mpris_dbus_control returns (False, 'dbus_unavailable') when gi import fails."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gi":
            raise ImportError("No module named 'gi'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = tools._mpris_dbus_control("play")
    assert result == (False, "dbus_unavailable")


def test_mpris_dbus_control_unsupported_action(monkeypatch):
    """CB: _mpris_dbus_control returns (False, 'unsupported') for unknown action."""
    # Mock gi import to provide a working D-Bus with players.
    import builtins
    import types

    real_import = builtins.__import__

    class _Variant:
        def __init__(self, val):
            self._val = val
        def unpack(self):
            return (self._val,)

    class _Gio:
        BusType = MagicMock()
        SESSION = "SESSION"
        DBusCallFlags = MagicMock()
        NONE = 0

        @staticmethod
        def bus_get_sync(bus_type, *args):
            return _FakeBus(["org.mpris.MediaPlayer2.firefox"])

    class _GLib:
        Variant = _Variant

    class _gi:
        @staticmethod
        def require_version(version, *args):
            pass

        repository = MagicMock()
        repository.Gio = _Gio
        repository.GLib = _GLib

    def fake_import(name, *args, **kwargs):
        if name == "gi":
            return _gi
        if name == "gi.repository":
            return _gi.repository
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # "bogus" not in _MPRIS_METHOD_MAP → returns unsupported
    result = tools._mpris_dbus_control("bogus")
    assert result == (False, "unsupported")


class _FakeBus:
    def __init__(self, players):
        self._players = players

    def call_sync(self, *args, **kwargs):
        if args[0] == "org.freedesktop.DBus":
            class _Names:
                def __init__(self, names):
                    self._names = names
                def unpack(self):
                    return (self._names,)
            return _Names(["org.freedesktop.DBus"] + self._players)
        if len(args) >= 4 and args[3] == "Get":
            class _V:
                def __init__(self, val):
                    self._val = val
                def unpack(self):
                    return (self._val,)
            return _V("paused")
        return None


# ---------------------------------------------------------------------------
# fetch_webpage / fetch_url_text / get_weather / get_news_headlines / get_current_time_zone
# ---------------------------------------------------------------------------


def _patch_net_policy_ok(monkeypatch):
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", lambda tool, val: None)
    monkeypatch.setattr(tools.net_policy, "validate_public_url", lambda url, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda url, **k: _FakeResult(status_code=200, text="<html>default</html>"))


def test_fetch_webpage_net_policy_blocks(monkeypatch):
    """CB: fetch_webpage raises when net policy check fails (outside try)."""
    def boom(tool, url):
        raise Exception("not allowed")
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", boom)
    with pytest.raises(Exception, match="not allowed"):
        tools.fetch_webpage("https://example.com")


def test_fetch_webpage_url_policy_error(monkeypatch):
    """CB: fetch_webpage returns blocked on URLPolicyError."""
    _patch_net_policy_ok(monkeypatch)
    class URLPolicyError(Exception):
        pass
    monkeypatch.setattr(tools.net_policy, "URLPolicyError", URLPolicyError)
    def raise_policy(url, **kwargs):
        raise URLPolicyError("private IP")
    monkeypatch.setattr(tools.net_policy, "validate_public_url", raise_policy)
    result = tools.fetch_webpage("https://10.0.0.1/")
    assert "Blocked by network policy" in result


def test_fetch_webpage_cache_hit(monkeypatch):
    """CB: fetch_webpage returns cached result."""
    import celina_pkg.cache as cache_mod
    fake_cache = MagicMock()
    fake_cache.get.return_value = ("cached page",)
    monkeypatch.setattr(cache_mod, "get_cache", lambda: fake_cache)
    _patch_net_policy_ok(monkeypatch)
    monkeypatch.setattr(tools.net_policy, "validate_public_url", lambda url, **k: None)
    result = tools.fetch_webpage("https://example.com")
    assert result == "cached page"


def test_fetch_webpage_http_error(monkeypatch):
    """CB: fetch_webpage returns error on HTTP >= 400."""
    import celina_pkg.cache as cache_mod
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    monkeypatch.setattr(cache_mod, "get_cache", lambda: fake_cache)
    _patch_net_policy_ok(monkeypatch)
    monkeypatch.setattr(tools.net_policy, "validate_public_url", lambda url, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda url, **k: _FakeResult(status_code=404, text=""))
    result = tools.fetch_webpage("https://example.com")
    assert "Failed to fetch webpage: HTTP 404" in result


def test_fetch_webpage_success(monkeypatch):
    """CB: fetch_webpage returns parsed text on success."""
    import celina_pkg.cache as cache_mod
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    fake_cache.set = MagicMock()
    monkeypatch.setattr(cache_mod, "get_cache", lambda: fake_cache)
    _patch_net_policy_ok(monkeypatch)
    monkeypatch.setattr(tools.net_policy, "validate_public_url", lambda url, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda url, **k: _FakeResult(status_code=200, text="<html>Hello World</html>"))
    result = tools.fetch_webpage("https://example.com")
    assert "Hello World" in result


def test_fetch_webpage_exception(monkeypatch):
    """CB: fetch_webpage catches exceptions from safe_get."""
    import celina_pkg.cache as cache_mod
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    monkeypatch.setattr(cache_mod, "get_cache", lambda: fake_cache)
    _patch_net_policy_ok(monkeypatch)
    monkeypatch.setattr(tools.net_policy, "validate_public_url", lambda url, **k: None)
    monkeypatch.setattr(tools.net_policy, "safe_get", lambda url, **k: (_ for _ in ()).throw(Exception("network down")))
    result = tools.fetch_webpage("https://example.com")
    assert "Failed to fetch webpage: network down" in result


def test_fetch_url_text_net_policy_blocks(monkeypatch):
    """CB: fetch_url_text raises when net policy check fails."""
    def boom(tool, url):
        raise Exception("blocked")
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", boom)
    with pytest.raises(Exception, match="blocked"):
        tools.fetch_url_text("https://example.com")


def test_fetch_url_text_url_policy_error(monkeypatch):
    """CB: fetch_url_text returns blocked on URLPolicyError."""
    _patch_net_policy_ok(monkeypatch)
    class URLPolicyError(Exception):
        pass
    monkeypatch.setattr(tools.net_policy, "URLPolicyError", URLPolicyError)
    def raise_policy(url, **kwargs):
        raise URLPolicyError("private IP")
    monkeypatch.setattr(tools.net_policy, "validate_public_url", raise_policy)
    result = tools.fetch_url_text("https://10.0.0.1/")
    assert "Blocked by network policy" in result


def test_get_weather_net_policy_blocks(monkeypatch):
    """CB: get_weather raises when net policy check fails."""
    def boom(tool, city):
        raise Exception("blocked")
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", boom)
    with pytest.raises(Exception, match="blocked"):
        tools.get_weather("London")


def test_get_news_headlines_net_policy_blocks(monkeypatch):
    """CB: get_news_headlines raises when net policy check fails."""
    def boom(tool, topic):
        raise Exception("blocked")
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", boom)
    with pytest.raises(Exception, match="blocked"):
        tools.get_news_headlines("world")


def test_get_current_time_zone_net_policy_blocks(monkeypatch):
    """CB: get_current_time_zone raises when net policy check fails."""
    def boom(tool, city):
        raise Exception("blocked")
    monkeypatch.setattr(tools.net_policy, "check_web_comms_allowed", boom)
    with pytest.raises(Exception, match="blocked"):
        tools.get_current_time_zone("London")
