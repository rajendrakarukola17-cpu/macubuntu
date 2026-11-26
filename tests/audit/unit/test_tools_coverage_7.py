"""
tests/audit/unit/test_tools_coverage_7.py

Phase 7d — TASK 1a: tools.py branch coverage tests (round 7).
Targets cache_status, clear_cache, mcp_*, remember_fact, link_knowledge,
manage_goal, search_memory, is_catastrophic_command.

No real network calls. Mocks cache, memory, and mcp_client modules.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


# ---------------------------------------------------------------------------
# cache_status / clear_cache
# ---------------------------------------------------------------------------


def test_cache_status(monkeypatch):
    """CB: cache_status returns formatted stats."""
    fake_cache = MagicMock()
    fake_cache.format_stats.return_value = "Cache: 100 KB, 50 entries"
    monkeypatch.setattr(tools.cache, "get_cache", lambda: fake_cache)
    result = tools.cache_status()
    assert "Cache: 100 KB" in result


def test_clear_cache(monkeypatch):
    """CB: clear_cache clears and returns confirmation."""
    fake_cache = MagicMock()
    monkeypatch.setattr(tools.cache, "get_cache", lambda: fake_cache)
    result = tools.clear_cache()
    assert "Smart cache cleared" in result
    fake_cache.clear.assert_called_once()


# ---------------------------------------------------------------------------
# mcp_list_servers / mcp_list_tools / mcp_call_tool
# ---------------------------------------------------------------------------


def test_mcp_list_servers(monkeypatch):
    """CB: mcp_list_servers returns server list."""
    fake_mcp = MagicMock()
    fake_mcp.list_servers.return_value = ["server1", "server2"]
    monkeypatch.setattr(tools.mcp_client, "get_mcp", lambda: fake_mcp)
    result = tools.mcp_list_servers()
    assert "server1" in result


def test_mcp_list_tools(monkeypatch):
    """CB: mcp_list_tools returns tool list for a server."""
    fake_mcp = MagicMock()
    fake_mcp.list_tools.return_value = ["tool1", "tool2"]
    monkeypatch.setattr(tools.mcp_client, "get_mcp", lambda: fake_mcp)
    result = tools.mcp_list_tools("server1")
    assert "tool1" in result


def test_mcp_call_tool(monkeypatch):
    """CB: mcp_call_tool calls call_tool with args."""
    fake_mcp = MagicMock()
    fake_mcp.call_tool.return_value = "tool result"
    monkeypatch.setattr(tools.mcp_client, "get_mcp", lambda: fake_mcp)
    result = tools.mcp_call_tool("server1", "tool1", {"arg": "val"})
    assert "tool result" in result


def test_mcp_call_tool_no_args(monkeypatch):
    """CB: mcp_call_tool works without arguments."""
    fake_mcp = MagicMock()
    fake_mcp.call_tool.return_value = "tool result"
    monkeypatch.setattr(tools.mcp_client, "get_mcp", lambda: fake_mcp)
    result = tools.mcp_call_tool("server1", "tool1")
    assert "tool result" in result


# ---------------------------------------------------------------------------
# remember_fact
# ---------------------------------------------------------------------------


def test_remember_fact(monkeypatch):
    """CB: remember_fact saves fact to memory."""
    fake_db = MagicMock()
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.remember_fact("mykey", "myvalue")
    assert "Saved to long-term memory" in result
    assert "mykey = myvalue" in result
    fake_db.set_fact.assert_called_once_with("mykey", "myvalue")


# ---------------------------------------------------------------------------
# link_knowledge
# ---------------------------------------------------------------------------


def test_link_knowledge_query_only(monkeypatch):
    """CB: link_knowledge queries graph with query_entity only."""
    fake_db = MagicMock()
    fake_db.query_knowledge_graph.return_value = [
        {"hop": 1, "subject": "A", "predicate": "knows", "object": "B"},
    ]
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.link_knowledge(query_entity="A")
    assert "Knowledge Graph links" in result
    assert "A --[knows]--> B" in result


def test_link_knowledge_query_no_links(monkeypatch):
    """CB: link_knowledge query returns no links found."""
    fake_db = MagicMock()
    fake_db.query_knowledge_graph.return_value = []
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.link_knowledge(query_entity="A")
    assert "No Knowledge Graph links found" in result


def test_link_knowledge_add_triple(monkeypatch):
    """CB: link_knowledge adds a triple."""
    fake_db = MagicMock()
    fake_db.add_knowledge_triple.return_value = {
        "subject": "A", "predicate": "knows", "object": "B",
    }
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.link_knowledge("A", "knows", "B")
    assert "Saved Knowledge Graph link" in result


def test_link_knowledge_missing_params(monkeypatch):
    """CB: link_knowledge errors when missing subject/predicate/obj."""
    fake_db = MagicMock()
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.link_knowledge("A", "", "")
    assert "Error: Provide subject, predicate, and obj" in result


# ---------------------------------------------------------------------------
# manage_goal
# ---------------------------------------------------------------------------


def test_manage_goal_list_empty(monkeypatch):
    """CB: manage_goal list returns no goals found."""
    fake_db = MagicMock()
    fake_db.list_goals.return_value = []
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.manage_goal(action="list")
    assert "No goals found" in result


def test_manage_goal_list_with_goals(monkeypatch):
    """CB: manage_goal list returns goals."""
    fake_db = MagicMock()
    fake_db.list_goals.return_value = [
        {"goal_id": "g1", "title": "Test goal", "status": "active", "deadline": "", "sub_tasks": []},
    ]
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.manage_goal(action="list")
    assert "Persistent Goals" in result
    assert "Test goal" in result


def test_manage_goal_complete(monkeypatch):
    """CB: manage_goal complete marks goal completed."""
    fake_db = MagicMock()
    fake_db.upsert_goal.return_value = {"goal_id": "g1", "title": "Test", "status": "completed"}
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.manage_goal(action="complete", goal_id="g1")
    assert "COMPLETED" in result


def test_manage_goal_complete_no_id(monkeypatch):
    """CB: manage_goal complete errors when no goal_id or title."""
    fake_db = MagicMock()
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.manage_goal(action="complete")
    assert "Error: Provide goal_id or title" in result


def test_manage_goal_no_title_or_id(monkeypatch):
    """CB: manage_goal errors when no title or goal_id for upsert."""
    fake_db = MagicMock()
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.manage_goal(action="upsert")
    assert "Error: Provide a goal title or goal_id" in result


def test_manage_goal_upsert(monkeypatch):
    """CB: manage_goal upsert creates a goal."""
    fake_db = MagicMock()
    fake_db.upsert_goal.return_value = {
        "goal_id": "g1", "title": "Test", "status": "active",
        "deadline": "", "sub_tasks": [],
    }
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.manage_goal(action="upsert", title="Test goal")
    assert "Goal saved" in result


def test_manage_goal_upsert_with_deadline(monkeypatch):
    """CB: manage_goal upsert includes deadline."""
    fake_db = MagicMock()
    fake_db.upsert_goal.return_value = {
        "goal_id": "g1", "title": "Test", "status": "active",
        "deadline": "2026-12-01", "sub_tasks": [],
    }
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.manage_goal(action="upsert", title="Test", deadline="2026-12-01")
    assert "due 2026-12-01" in result


# ---------------------------------------------------------------------------
# search_memory
# ---------------------------------------------------------------------------


def test_search_memory_no_results(monkeypatch):
    """CB: search_memory returns no results message."""
    fake_db = MagicMock()
    fake_db.recall_relevant.return_value = ([], [])
    fake_db.recall_knowledge_triples.return_value = []
    fake_db.search_history.return_value = []
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.search_memory("nothing here")
    assert "No memory entries found" in result


def test_search_memory_with_facts(monkeypatch):
    """CB: search_memory returns matching facts."""
    fake_db = MagicMock()
    fake_db.recall_relevant.return_value = ([("key1", "val1")], [])
    fake_db.recall_knowledge_triples.return_value = []
    fake_db.search_history.return_value = []
    monkeypatch.setattr(tools.memory, "get_db", lambda: fake_db)
    result = tools.search_memory("test query")
    assert "Matching facts" in result
    assert "key1: val1" in result


# ---------------------------------------------------------------------------
# is_catastrophic_command
# ---------------------------------------------------------------------------


def test_is_catastrophic_command_rm_rf():
    """CB: is_catastrophic_command detects rm -rf /."""
    assert tools.is_catastrophic_command("rm -rf /") is True


def test_is_catastrophic_command_safe():
    """CB: is_catastrophic_command returns False for safe commands."""
    assert tools.is_catastrophic_command("ls -la") is False


def test_is_catastrophic_command_mkfs():
    """CB: is_catastrophic_command detects mkfs."""
    assert tools.is_catastrophic_command("mkfs.ext4 /dev/sda1") is True
