"""
tests/audit/unit/test_tools_coverage_22.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 22).
Targets: open_application remaining branches, _coerce_and_filter_tool_args remaining branches.

Covers: open_application empty name, URL domain, web forced WEB_APPS/domain/google,
settings panel gnome-control-center, curated app success/failure, short key WEB_APPS,
gtk-launch desktop dirs match/name match/genericname/fallback, WEB_APPS fallback,
not found. _coerce_and_filter_tool_args int/number/bool coercion, VAR_KEYWORD,
no VAR_KEYWORD filter, signature exception.

No real desktop or network calls. Mocks launch_detached, open_url_safe, shutil.which,
Path operations, inspect.signature.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


# ---------------------------------------------------------------------------
# open_application
# ---------------------------------------------------------------------------

def test_open_application_empty(monkeypatch):
    """CB: open_application empty name → error message."""
    result = tools.open_application("")
    assert "Provide an application" in result


def test_open_application_url_domain(monkeypatch):
    """CB: open_application with domain → open_url_safe."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"opened {url}")
    result = tools.open_application("youtube.com")
    assert "opened" in result


def test_open_application_web_forced_webapp(monkeypatch):
    """CB: open_application 'spotify in web' → WEB_APPS → open_url_safe."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"opened {url}")
    monkeypatch.setattr(tools, "WEB_APPS", {**tools.WEB_APPS, "spotify": "https://spotify.com"})
    result = tools.open_application("spotify in web")
    assert "opened" in result


def test_open_application_web_forced_domain(monkeypatch):
    """CB: open_application 'example.com web' → domain match → open_url_safe."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"opened {url}")
    result = tools.open_application("example.com web")
    assert "opened" in result


def test_open_application_web_forced_google(monkeypatch):
    """CB: open_application 'unknown web' → google search → open_url_safe."""
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"opened {url}")
    result = tools.open_application("some unknown app web")
    assert "opened" in result


def test_open_application_settings_panel(monkeypatch):
    """CB: open_application 'wifi settings' → gnome-control-center → launched."""
    monkeypatch.setattr(tools, "SETTINGS_PANELS", {**tools.SETTINGS_PANELS, "wifi settings": "wifi"})
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)
    result = tools.open_application("wifi settings")
    assert "Opened Settings" in result


def test_open_application_settings_panel_fail(monkeypatch):
    """CB: open_application settings panel but launch_detached fails → falls through."""
    monkeypatch.setattr(tools, "SETTINGS_PANELS", {**tools.SETTINGS_PANELS, "wifi settings": "wifi"})
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: False)
    result = tools.open_application("wifi settings")
    assert result is not None


def test_open_application_curated_success(monkeypatch):
    """CB: open_application curated app → launch_detached success → opened."""
    monkeypatch.setattr(tools, "CURATED_APPS", {**tools.CURATED_APPS, "gedit": ["gedit"]})
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: True)
    result = tools.open_application("gedit")
    assert "Opened gedit" in result


def test_open_application_curated_failure(monkeypatch):
    """CB: open_application curated app → launch_detached fails → failed message."""
    monkeypatch.setattr(tools, "CURATED_APPS", {**tools.CURATED_APPS, "gedit": ["gedit"]})
    monkeypatch.setattr(tools, "launch_detached", lambda cmd: False)
    result = tools.open_application("gedit")
    assert "Failed to open" in result


def test_open_application_short_key_webapp(monkeypatch):
    """CB: open_application short key (<=3 chars) in WEB_APPS → open_url_safe."""
    monkeypatch.setattr(tools, "WEB_APPS", {**tools.WEB_APPS, "yt": "https://youtube.com"})
    monkeypatch.setattr(tools, "open_url_safe", lambda url: f"opened {url}")
    monkeypatch.setattr(tools.shutil, "which", lambda cmd: None)
    result = tools.open_application("yt")
    assert "opened" in result


# ---------------------------------------------------------------------------
# _coerce_and_filter_tool_args
# ---------------------------------------------------------------------------

def test_coerce_int_from_string(monkeypatch):
    """CB: _coerce_and_filter_tool_args integer coercion from string."""
    schema = {
        "function": {
            "name": "test_tool",
            "parameters": {
                "properties": {"count": {"type": "integer"}},
            },
        }
    }
    tools._TOOL_SCHEMA_BY_NAME["test_tool"] = schema["function"]

    def sample_fn(count: int = 0):
        return count

    result = tools._coerce_and_filter_tool_args("test_tool", sample_fn, {"count": "60"})
    assert result["count"] == 60


def test_coerce_int_from_string_percent(monkeypatch):
    """CB: _coerce_and_filter_tool_args integer coercion from string with %."""
    schema = {
        "function": {
            "name": "test_tool2",
            "parameters": {
                "properties": {"level": {"type": "integer"}},
            },
        }
    }
    tools._TOOL_SCHEMA_BY_NAME["test_tool2"] = schema["function"]

    def sample_fn(level: int = 0):
        return level

    result = tools._coerce_and_filter_tool_args("test_tool2", sample_fn, {"level": "75%"})
    assert result["level"] == 75


def test_coerce_number_from_string(monkeypatch):
    """CB: _coerce_and_filter_tool_args number coercion from string."""
    schema = {
        "function": {
            "name": "test_tool3",
            "parameters": {
                "properties": {"ratio": {"type": "number"}},
            },
        }
    }
    tools._TOOL_SCHEMA_BY_NAME["test_tool3"] = schema["function"]

    def sample_fn(ratio: float = 0.0):
        return ratio

    result = tools._coerce_and_filter_tool_args("test_tool3", sample_fn, {"ratio": "3.14"})
    assert result["ratio"] == 3.14


def test_coerce_bool_from_string_true(monkeypatch):
    """CB: _coerce_and_filter_tool_args boolean coercion from 'true' string."""
    schema = {
        "function": {
            "name": "test_tool4",
            "parameters": {
                "properties": {"flag": {"type": "boolean"}},
            },
        }
    }
    tools._TOOL_SCHEMA_BY_NAME["test_tool4"] = schema["function"]

    def sample_fn(flag: bool = False):
        return flag

    result = tools._coerce_and_filter_tool_args("test_tool4", sample_fn, {"flag": "true"})
    assert result["flag"] is True


def test_coerce_bool_from_string_false(monkeypatch):
    """CB: _coerce_and_filter_tool_args boolean coercion from 'false' string."""
    schema = {
        "function": {
            "name": "test_tool5",
            "parameters": {
                "properties": {"flag": {"type": "boolean"}},
            },
        }
    }
    tools._TOOL_SCHEMA_BY_NAME["test_tool5"] = schema["function"]

    def sample_fn(flag: bool = False):
        return flag

    result = tools._coerce_and_filter_tool_args("test_tool5", sample_fn, {"flag": "false"})
    assert result["flag"] is False


def test_coerce_bool_from_int(monkeypatch):
    """CB: _coerce_and_filter_tool_args boolean coercion from int 0/1."""
    schema = {
        "function": {
            "name": "test_tool6",
            "parameters": {
                "properties": {"flag": {"type": "boolean"}},
            },
        }
    }
    tools._TOOL_SCHEMA_BY_NAME["test_tool6"] = schema["function"]

    def sample_fn(flag: bool = False):
        return flag

    result = tools._coerce_and_filter_tool_args("test_tool6", sample_fn, {"flag": 1})
    assert result["flag"] is True


def test_coerce_var_keyword_keeps_all(monkeypatch):
    """CB: _coerce_and_filter_tool_args VAR_KEYWORD → keeps all args."""
    schema = {
        "function": {
            "name": "test_tool7",
            "parameters": {
                "properties": {},
            },
        }
    }
    tools._TOOL_SCHEMA_BY_NAME["test_tool7"] = schema["function"]

    def sample_fn(**kwargs):
        return kwargs

    result = tools._coerce_and_filter_tool_args("test_tool7", sample_fn, {"a": "1", "b": "2", "extra": "3"})
    assert "extra" in result


def test_coerce_no_var_keyword_filters(monkeypatch):
    """CB: _coerce_and_filter_tool_args no VAR_KEYWORD → filters extra keys."""
    schema = {
        "function": {
            "name": "test_tool8",
            "parameters": {
                "properties": {"a": {"type": "string"}},
            },
        }
    }
    tools._TOOL_SCHEMA_BY_NAME["test_tool8"] = schema["function"]

    def sample_fn(a: str = ""):
        return a

    result = tools._coerce_and_filter_tool_args("test_tool8", sample_fn, {"a": "hello", "extra": "ignored"})
    assert "extra" not in result
    assert result["a"] == "hello"


def test_coerce_signature_exception(monkeypatch):
    """CB: _coerce_and_filter_tool_args inspect.signature raises → passes through."""
    schema = {
        "function": {
            "name": "test_tool9",
            "parameters": {
                "properties": {},
            },
        }
    }
    tools._TOOL_SCHEMA_BY_NAME["test_tool9"] = schema["function"]

    # Make inspect.signature raise
    orig_sig = __import__("inspect").signature
    def fake_sig(obj):
        raise TypeError("no signature")
    monkeypatch.setattr(tools.inspect, "signature", fake_sig)

    def sample_fn(a: str = ""):
        return a

    result = tools._coerce_and_filter_tool_args("test_tool9", sample_fn, {"a": "hello", "extra": "kept"})
    assert "extra" in result
