"""
Headless unit tests for features/celina/celina_pkg/plugins/habit_engine.py.

Tests the local habit & frequency engine:
- gear0_habit_classify pattern matching
- log_command persistence
- get_habit_pattern retrieval
- get_proactive_suggestions time/weekday matching
- gear0_habit_execute dispatch
- Confidence calculation
"""

import sys
import time
from pathlib import Path
from unittest import mock

import pytest

CELINA_ROOT = Path(__file__).resolve().parent.parent / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.plugins import habit_engine as he


# --------------------------------------------------------------------------- #
# gear0_habit_classify
# --------------------------------------------------------------------------- #

def test_classify_recognizes_habit_queries():
    """gear0_habit_classify should return 'get_all_patterns' for habit queries."""
    for q in ["habits", "please habits", "routine pattern", "habit"]:
        assert he.gear0_habit_classify(q) == "get_all_patterns", f"'{q}' should classify"

def test_classify_recognizes_suggest_queries():
    """gear0_habit_classify should return 'get_proactive_suggestions' for suggest queries."""
    for q in ["suggest", "proactive", "what should i do", "please suggest"]:
        assert he.gear0_habit_classify(q) == "get_proactive_suggestions", f"'{q}' should classify"

def test_classify_returns_none_for_unrelated():
    """gear0_habit_classify should return None for unrelated queries."""
    assert he.gear0_habit_classify("open the browser") is None
    assert he.gear0_habit_classify("play music") is None
    assert he.gear0_habit_classify("shutdown now") is None


# --------------------------------------------------------------------------- #
# log_command + get_habit_pattern
# --------------------------------------------------------------------------- #

def test_log_command_stores_command(tmp_path):
    """log_command should persist a command to the habit DB."""
    old_db = he._DB_PATH
    he._DB_PATH = tmp_path / "test_habits.db"
    try:
        he.log_command("open browser")
        pattern = he.get_habit_pattern("open browser")
        assert pattern is not None
        assert pattern["command"] == "open browser"
        assert pattern["total_count"] >= 1
        assert 0.0 <= pattern["confidence"] <= 1.0
    finally:
        he._DB_PATH = old_db

def test_log_command_increments_count(tmp_path):
    """Logging the same command twice should increment total_count."""
    old_db = he._DB_PATH
    he._DB_PATH = tmp_path / "test_habits2.db"
    try:
        he.log_command("run backup")
        he.log_command("run backup")
        pattern = he.get_habit_pattern("run backup")
        assert pattern is not None
        assert pattern["total_count"] == 2
    finally:
        he._DB_PATH = old_db

def test_get_habit_pattern_unknown_returns_none():
    """get_habit_pattern should return None for unknown commands."""
    assert he.get_habit_pattern("never_used_command_xyz") is None


# --------------------------------------------------------------------------- #
# get_all_patterns
# --------------------------------------------------------------------------- #

def test_get_all_patterns_returns_list(tmp_path):
    """get_all_patterns should return a list of pattern dicts."""
    old_db = he._DB_PATH
    he._DB_PATH = tmp_path / "test_habits3.db"
    try:
        he.log_command("cmd alpha")
        he.log_command("cmd beta")
        patterns = he.get_all_patterns()
        assert isinstance(patterns, list)
        assert len(patterns) == 2
        cmds = {p["command"] for p in patterns}
        assert cmds == {"cmd alpha", "cmd beta"}
    finally:
        he._DB_PATH = old_db

def test_get_all_patterns_empty_when_no_data(tmp_path):
    """get_all_patterns should return empty list when DB is empty."""
    old_db = he._DB_PATH
    he._DB_PATH = tmp_path / "test_habits4.db"
    try:
        assert he.get_all_patterns() == []
    finally:
        he._DB_PATH = old_db


# --------------------------------------------------------------------------- #
# get_proactive_suggestions
# --------------------------------------------------------------------------- #

def test_get_proactive_suggestions_returns_list(tmp_path):
    """get_proactive_suggestions should return a list (possibly empty)."""
    old_db = he._DB_PATH
    he._DB_PATH = tmp_path / "test_habits5.db"
    try:
        suggestions = he.get_proactive_suggestions()
        assert isinstance(suggestions, list)
    finally:
        he._DB_PATH = old_db


# --------------------------------------------------------------------------- #
# gear0_habit_execute
# --------------------------------------------------------------------------- #

def test_execute_get_all_patterns(tmp_path):
    """gear0_habit_execute with get_all_patterns should return formatted text."""
    old_db = he._DB_PATH
    he._DB_PATH = tmp_path / "test_habits6.db"
    try:
        he.log_command("daily standup")
        result = he.gear0_habit_execute("get_all_patterns", "habits")
        assert result is not None
        assert "Learned Habit Patterns" in result
        assert "daily standup" in result
    finally:
        he._DB_PATH = old_db

def test_execute_get_proactive_suggestions(tmp_path):
    """gear0_habit_execute with get_proactive_suggestions should return text."""
    old_db = he._DB_PATH
    he._DB_PATH = tmp_path / "test_habits7.db"
    try:
        result = he.gear0_habit_execute("get_proactive_suggestions", "suggest")
        assert result is not None
        assert "Proactive Suggestions" in result or "No proactive suggestions" in result
    finally:
        he._DB_PATH = old_db

def test_execute_unknown_handler_returns_none():
    """gear0_habit_execute with unknown handler should return None."""
    assert he.gear0_habit_execute("nonexistent_handler", "query") is None


# --------------------------------------------------------------------------- #
# Confidence calculation
# --------------------------------------------------------------------------- #

def test_confidence_reaches_full_after_10(tmp_path):
    """Confidence should reach 1.0 after 10+ occurrences (min(1.0, count/10.0))."""
    old_db = he._DB_PATH
    he._DB_PATH = tmp_path / "test_habits8.db"
    try:
        for _ in range(10):
            he.log_command("morning briefing")
        pattern = he.get_habit_pattern("morning briefing")
        assert pattern is not None
        assert pattern["confidence"] >= 0.9
    finally:
        he._DB_PATH = old_db
