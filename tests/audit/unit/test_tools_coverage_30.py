"""
tests/audit/unit/test_tools_coverage_30.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 30).
Targets: handle_starttag remaining branches (final push to 85%).

Covers: handle_starttag tag != "a" but in {a,div,span} with result__snippet → elif True;
  handle_starttag tag == "a" with result__a but href without uddg= → else branch.

No real network calls. Mocks nothing needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

from celina_pkg import tools  # noqa: E402


def test_handle_starttag_snippet_div(monkeypatch):
    """CB: handle_starttag div with result__snippet → elif branch sets _in_snippet."""
    parser = tools._DDGParser()
    parser.handle_starttag("div", [("class", "result__snippet")])
    assert parser._in_snippet is True
    assert parser._cur_snippet == ""


def test_handle_starttag_a_without_uddg(monkeypatch):
    """CB: handle_starttag a with result__a but href without uddg= → else branch."""
    parser = tools._DDGParser()
    parser.handle_starttag("a", [("class", "result__a"), ("href", "https://example.com")])
    assert parser._in_title is True
    assert parser._cur_href == "https://example.com"


def test_handle_starttag_span_snippet(monkeypatch):
    """CB: handle_starttag span with result__snippet → elif branch."""
    parser = tools._DDGParser()
    parser.handle_starttag("span", [("class", "result__snippet")])
    assert parser._in_snippet is True


def test_handle_starttag_a_with_uddg(monkeypatch):
    """CB: handle_starttag a with result__a and href with uddg= → unquote branch."""
    parser = tools._DDGParser()
    parser.handle_starttag("a", [("class", "result__a"), ("href", "https://duckduckgo.com/?uddg=https%3A%2F%2Fexample.com")])
    assert parser._in_title is True
    assert "example.com" in parser._cur_href
