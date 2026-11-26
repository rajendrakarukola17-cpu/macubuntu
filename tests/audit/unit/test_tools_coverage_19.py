"""
tests/audit/unit/test_tools_coverage_19.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 19).
Targets: _search_fallback_apis, vision_ocr, vision_analyze, _execute_mcp_perception_actuation.

Covers: _search_fallback_apis Wikipedia success/HN success/both fail,
vision_ocr mirage sufficient/mirage escalates/mirage fails/MCP fallback,
vision_analyze mirage sufficient/mirage fails/MCP fallback,
_execute_mcp_perception_actuation success/approval denied/failure.

No real network calls. Mocks net_policy.safe_get, automation.capture_screenshot,
_mirage_vision_triage, _execute_mcp_perception_actuation, approval_cb.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import json

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text
    def json(self):
        return self._json


# ---------------------------------------------------------------------------
# _search_fallback_apis
# ---------------------------------------------------------------------------

def test_search_fallback_apis_wikipedia_success(monkeypatch):
    """CB: _search_fallback_apis Wikipedia returns 200 with 4+ items → results appended."""
    wiki_data = [
        ["query"],
        ["Title 1", "Title 2"],
        ["Desc 1", "Desc 2"],
        ["https://en.wikipedia.org/wiki/1", "https://en.wikipedia.org/wiki/2"],
    ]
    hn_data = {"hits": []}

    def safe_get(url, **kwargs):
        if "wikipedia" in url:
            return _FakeResponse(200, wiki_data)
        return _FakeResponse(200, hn_data)

    monkeypatch.setattr(tools.net_policy, "safe_get", safe_get)
    result = tools._search_fallback_apis("test query")
    assert len(result) > 0
    assert any("Wikipedia" in r["title"] for r in result)


def test_search_fallback_apis_hn_success(monkeypatch):
    """CB: _search_fallback_apis Wikipedia fails, HN returns hits → results appended."""
    def safe_get(url, **kwargs):
        if "wikipedia" in url:
            raise Exception("wiki down")
        if "hn.algolia" in url:
            return _FakeResponse(200, {
                "hits": [
                    {"title": "HN Story", "url": "https://example.com", "objectID": "123", "points": 10, "num_comments": 5},
                ]
            })
        return _FakeResponse(200, {})

    monkeypatch.setattr(tools.net_policy, "safe_get", safe_get)
    result = tools._search_fallback_apis("test query")
    assert len(result) > 0
    assert any("HN" in r["title"] or "HN discussion" in r.get("snippet", "") for r in result)


def test_search_fallback_apis_both_fail(monkeypatch):
    """CB: _search_fallback_apis both Wikipedia and HN fail → returns []."""
    def safe_get(url, **kwargs):
        raise Exception("network down")

    monkeypatch.setattr(tools.net_policy, "safe_get", safe_get)
    result = tools._search_fallback_apis("test query")
    assert result == []


def test_search_fallback_apis_wikipedia_not_200(monkeypatch):
    """CB: _search_fallback_apis Wikipedia returns non-200 → skips Wikipedia, tries HN."""
    def safe_get(url, **kwargs):
        if "wikipedia" in url:
            return _FakeResponse(500, {})
        return _FakeResponse(200, {"hits": []})

    monkeypatch.setattr(tools.net_policy, "safe_get", safe_get)
    result = tools._search_fallback_apis("test query")
    assert result == []  # HN also empty


# ---------------------------------------------------------------------------
# vision_ocr
# ---------------------------------------------------------------------------

def test_vision_ocr_mirage_sufficient(monkeypatch, tmp_path):
    """CB: vision_ocr mirage OCR sufficient (not escalate) → returns JSON record."""
    import tempfile
    orig_mkstemp = tempfile.mkstemp

    def fake_mkstemp(*a, **k):
        fd, path = orig_mkstemp(*a, **k)
        return fd, path  # code calls os.close(fd) itself

    monkeypatch.setattr(tempfile, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(tools.automation, "capture_screenshot", lambda p: None)

    fake_record = {"text": "hello world", "confidence": 0.95}
    fake_result = MagicMock()
    fake_result.escalate_to_vlm = False
    monkeypatch.setattr(tools, "_mirage_vision_triage", lambda path, **k: (fake_record, fake_result))

    result = tools.vision_ocr(region="full", language="en")
    assert "hello world" in result


def test_vision_ocr_mirage_escalates(monkeypatch):
    """CB: vision_ocr mirage escalates to VLM → falls through to MCP."""
    monkeypatch.setattr(tools.automation, "capture_screenshot", lambda p: None)
    fake_result = MagicMock()
    fake_result.escalate_to_vlm = True
    monkeypatch.setattr(tools, "_mirage_vision_triage", lambda path, **k: ({}, fake_result))
    monkeypatch.setattr(tools, "_execute_mcp_perception_actuation", lambda name, args, **k: "mcp result")

    result = tools.vision_ocr(region="full", language="en")
    assert result == "mcp result"


def test_vision_ocr_mirage_none(monkeypatch):
    """CB: vision_ocr _mirage_vision_triage returns None → falls through to MCP."""
    monkeypatch.setattr(tools.automation, "capture_screenshot", lambda p: None)
    monkeypatch.setattr(tools, "_mirage_vision_triage", lambda path, **k: (None, None))
    monkeypatch.setattr(tools, "_execute_mcp_perception_actuation", lambda name, args, **k: "mcp result")

    result = tools.vision_ocr(region="full", language="en")
    assert result == "mcp result"


def test_vision_ocr_capture_screenshot_error(monkeypatch):
    """CB: vision_ocr capture_screenshot returns error → falls through to MCP."""
    monkeypatch.setattr(tools.automation, "capture_screenshot", lambda p: "screenshot error")
    monkeypatch.setattr(tools, "_mirage_vision_triage", lambda path, **k: ({}, None))
    monkeypatch.setattr(tools, "_execute_mcp_perception_actuation", lambda name, args, **k: "mcp result")

    result = tools.vision_ocr(region="full", language="en")
    assert result == "mcp result"


def test_vision_ocr_exception_in_try(monkeypatch):
    """CB: vision_ocr exception in try block → caught, falls through to MCP."""
    monkeypatch.setattr(tools.automation, "capture_screenshot", lambda p: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(tools, "_execute_mcp_perception_actuation", lambda name, args, **k: "mcp result")

    result = tools.vision_ocr(region="full", language="en")
    assert result == "mcp result"


# ---------------------------------------------------------------------------
# vision_analyze
# ---------------------------------------------------------------------------

def test_vision_analyze_mirage_sufficient(monkeypatch):
    """CB: vision_analyze mirage sufficient → returns JSON record."""
    monkeypatch.setattr(tools.automation, "capture_screenshot", lambda p: None)
    fake_record = {"text": "analysis result", "confidence": 0.9}
    fake_result = MagicMock()
    fake_result.escalate_to_vlm = False
    monkeypatch.setattr(tools, "_mirage_vision_triage", lambda path, **k: (fake_record, fake_result))

    result = tools.vision_analyze("What is in this image?")
    assert "analysis result" in result


def test_vision_analyze_mirage_escalates(monkeypatch):
    """CB: vision_analyze mirage escalates → falls through to MCP."""
    monkeypatch.setattr(tools.automation, "capture_screenshot", lambda p: None)
    fake_result = MagicMock()
    fake_result.escalate_to_vlm = True
    monkeypatch.setattr(tools, "_mirage_vision_triage", lambda path, **k: ({}, fake_result))
    monkeypatch.setattr(tools, "_execute_mcp_perception_actuation", lambda name, args, **k: "mcp result")

    result = tools.vision_analyze("What is in this image?")
    assert result == "mcp result"


def test_vision_analyze_mirage_none(monkeypatch):
    """CB: vision_analyze _mirage_vision_triage returns None → falls through to MCP."""
    monkeypatch.setattr(tools.automation, "capture_screenshot", lambda p: None)
    monkeypatch.setattr(tools, "_mirage_vision_triage", lambda path, **k: (None, None))
    monkeypatch.setattr(tools, "_execute_mcp_perception_actuation", lambda name, args, **k: "mcp result")

    result = tools.vision_analyze("What is in this image?")
    assert result == "mcp result"


def test_vision_analyze_exception_in_try(monkeypatch):
    """CB: vision_analyze exception in try block → caught, falls through to MCP."""
    monkeypatch.setattr(tools.automation, "capture_screenshot", lambda p: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(tools, "_execute_mcp_perception_actuation", lambda name, args, **k: "mcp result")

    result = tools.vision_analyze("What is in this image?")
    assert result == "mcp result"


# ---------------------------------------------------------------------------
# _execute_mcp_perception_actuation
# ---------------------------------------------------------------------------

def test_execute_mcp_perception_actuation_success(monkeypatch):
    """CB: _execute_mcp_perception_actuation full success path → returns receipt.output."""
    # Mock MCP_TOOL_ROUTING
    fake_route = {
        "server": "vision-server",
        "transport": "stdio",
        "remote_tool": "ocr_tool",
    }
    monkeypatch.setattr("celina_pkg.autonomy.mcp_loader.MCP_TOOL_ROUTING", {"vision_ocr": fake_route})

    # Mock approval
    fake_token = MagicMock()
    fake_token.verify_and_consume_atomic = MagicMock(return_value=(True, ""))
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: (fake_token, None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))

    # Mock mcp_loader
    fake_receipt = MagicMock()
    fake_receipt.ok = True
    fake_receipt.output = "MCP OCR result"
    fake_loader = MagicMock()
    fake_loader.execute_domain_tool = MagicMock(return_value=fake_receipt)
    monkeypatch.setattr("celina_pkg.autonomy.mcp_loader.get_mcp_loader", lambda: fake_loader)

    result = tools._execute_mcp_perception_actuation("vision_ocr", {"region": "full"}, approval_cb=lambda t, d: True)
    assert "MCP OCR result" in result


def test_execute_mcp_perception_actuation_approval_denied(monkeypatch):
    """CB: _execute_mcp_perception_actuation approval denied → returns deny_err."""
    fake_route = {
        "server": "vision-server",
        "transport": "stdio",
        "remote_tool": "ocr_tool",
    }
    monkeypatch.setattr("celina_pkg.autonomy.mcp_loader.MCP_TOOL_ROUTING", {"vision_ocr": fake_route})
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: (None, "Denied: approval required"))

    result = tools._execute_mcp_perception_actuation("vision_ocr", {"region": "full"}, approval_cb=None)
    assert "Denied" in result


def test_execute_mcp_perception_actuation_verify_fails(monkeypatch):
    """CB: _execute_mcp_perception_actuation verify_and_consume returns False → returns err."""
    fake_route = {
        "server": "vision-server",
        "transport": "stdio",
        "remote_tool": "ocr_tool",
    }
    monkeypatch.setattr("celina_pkg.autonomy.mcp_loader.MCP_TOOL_ROUTING", {"vision_ocr": fake_route})
    fake_token = MagicMock()
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: (fake_token, None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (False, "Token expired"))

    result = tools._execute_mcp_perception_actuation("vision_ocr", {"region": "full"}, approval_cb=lambda t, d: True)
    assert "Token expired" in result


def test_execute_mcp_perception_actuation_mcp_failure(monkeypatch):
    """CB: _execute_mcp_perception_actuation MCP receipt not ok → error message."""
    fake_route = {
        "server": "vision-server",
        "transport": "stdio",
        "remote_tool": "ocr_tool",
    }
    monkeypatch.setattr("celina_pkg.autonomy.mcp_loader.MCP_TOOL_ROUTING", {"vision_ocr": fake_route})
    fake_token = MagicMock()
    monkeypatch.setattr(tools.approval, "request_bound_approval", lambda *a, **k: (fake_token, None))
    monkeypatch.setattr(tools.approval, "verify_and_consume", lambda token, name, args: (True, ""))

    fake_receipt = MagicMock()
    fake_receipt.ok = False
    fake_receipt.error = "connection refused"
    fake_loader = MagicMock()
    fake_loader.execute_domain_tool = MagicMock(return_value=fake_receipt)
    monkeypatch.setattr("celina_pkg.autonomy.mcp_loader.get_mcp_loader", lambda: fake_loader)

    result = tools._execute_mcp_perception_actuation("vision_ocr", {"region": "full"}, approval_cb=lambda t, d: True)
    assert "Error: MCP" in result
    assert "connection refused" in result
