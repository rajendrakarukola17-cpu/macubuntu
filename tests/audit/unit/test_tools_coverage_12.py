"""
tests/audit/unit/test_tools_coverage_12.py

Phase 7d — TASK 1: tools.py branch coverage tests (round 12).
Targets: get_contextual_tool_schemas (36 missing branches — highest impact).

Covers: empty domains, always_active skip, prompt/window hit, recording domains,
lexical rescue, semantic rescue, fast gear path, deep gear fallback,
capability boundary filter, presentable fallback, or-all-enabled fallback.

No real network calls. Mocks config.TOOL_DOMAINS, _trigger_matches,
_lexical_rank_tools, _semantic_rank_tools, capabilities, get_enabled_tool_schemas.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg import tools


# ---------------------------------------------------------------------------
# Helper schemas
# ---------------------------------------------------------------------------


def _schema(name: str, description: str = "") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


ALL_SCHEMAS = [
    _schema("write_file", "Write content to a file"),
    _schema("read_file", "Read a file"),
    _schema("move_file", "Move a file"),
    _schema("trash_file", "Trash a file"),
    _schema("get_system_stats", "Check system stats"),
    _schema("web_search", "Search the web"),
    _schema("remember_fact", "Remember a fact"),
    _schema("computer_click", "Click on screen"),
]


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: empty domains
# ---------------------------------------------------------------------------


def test_gcts_empty_domains_returns_all(monkeypatch):
    """CB: empty TOOL_DOMAINS returns all schemas with ['all'] domain."""
    import celina_pkg.config as config_mod

    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", {})
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("hello world")
        assert result[0] == ALL_SCHEMAS
        assert result[1] == ["all"]


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: always_active domain skip
# ---------------------------------------------------------------------------


def test_gcts_always_active_skipped(monkeypatch):
    """CB: domain with always_active=True is skipped (continue branch).
    Note: os_hardware is always re-added at the end of the function."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": True,
            "triggers": ["system"],
            "window_hints": ["terminal"],
            "tools": ["get_system_stats"],
        },
        "files_office": {
            "always_active": False,
            "triggers": ["file", "write"],
            "window_hints": ["editor"],
            "tools": ["write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("file write")
        # files_office matched via prompt trigger; os_hardware re-added at end
        assert "files_office" in result[1]
        assert "os_hardware" in result[1]  # always re-added at end
        names = [t["function"]["name"] for t in result[0]]
        assert "write_file" in names
        assert "get_system_stats" in names  # os_hardware re-added at end


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: prompt hit / window hit
# ---------------------------------------------------------------------------


def test_gcts_prompt_hit(monkeypatch):
    """CB: prompt_hit branch — domain trigger matches prompt text."""
    import celina_pkg.config as config_mod

    domains = {
        "files_office": {
            "always_active": False,
            "triggers": ["write_file", "edit_file"],
            "window_hints": [],
            "tools": ["write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("write_file something")
        assert "files_office" in result[1]
        names = [t["function"]["name"] for t in result[0]]
        assert "write_file" in names


def test_gcts_window_hit(monkeypatch):
    """CB: win_hit branch — window hint matches active_window when gear != fast."""
    import celina_pkg.config as config_mod

    domains = {
        "files_office": {
            "always_active": False,
            "triggers": [],
            "window_hints": ["editor"],
            "tools": ["write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas(
            "hello", gear="tactical", active_window="Editor Window"
        )
        assert "files_office" in result[1]
        names = [t["function"]["name"] for t in result[0]]
        assert "write_file" in names


def test_gcts_window_hit_gear_fast_skipped(monkeypatch):
    """CB: window_hit is False when gear == 'fast' (gear != fast branch)."""
    import celina_pkg.config as config_mod

    domains = {
        "files_office": {
            "always_active": False,
            "triggers": [],
            "window_hints": ["editor"],
            "tools": ["write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("hello", gear="fast", active_window="Editor Window")
        # Window hints not checked in fast gear; no domain matched via prompt either
        assert "files_office" not in result[1]


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: recording domains
# ---------------------------------------------------------------------------


def test_gcts_recording_adds_domains(monkeypatch):
    """CB: is_recording=True adds skills_memory + wayland_gui domains.
    Note: os_hardware is always re-added at the end of the function."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
        "skills_memory": {
            "always_active": False,
            "triggers": ["remember"],
            "window_hints": [],
            "tools": ["remember_fact"],
        },
        "wayland_gui": {
            "always_active": False,
            "triggers": ["click"],
            "window_hints": [],
            "tools": ["computer_click"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("system stats", is_recording=True)
        assert "skills_memory" in result[1]
        assert "wayland_gui" in result[1]
        assert "os_hardware" in result[1]  # always re-added at end
        names = [t["function"]["name"] for t in result[0]]
        assert "remember_fact" in names


def test_gcts_recording_domain_already_active(monkeypatch):
    """CB: recording domain already in active_domains is NOT re-added."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
        "skills_memory": {
            "always_active": False,
            "triggers": ["remember"],
            "window_hints": [],
            "tools": ["remember_fact"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        # skills_memory triggered via prompt "remember" AND is_recording —
        # should only appear once
        result = tools.get_contextual_tool_schemas(
            "remember something", is_recording=True
        )
        count = result[1].count("skills_memory")
        assert count == 1


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: lexical rescue
# ---------------------------------------------------------------------------


def test_gcts_lexical_rescue_adds_new_tools(monkeypatch):
    """CB: lex_hits branch — lexical ranking adds tools not in selected_tools."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)

    # No prompt trigger matches, but lexical rescue should add write_file
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        with patch.object(tools, "_lexical_rank_tools", return_value=["write_file"]):
            with patch.object(tools, "_semantic_rank_tools", return_value=[]):
                result = tools.get_contextual_tool_schemas("unexpected phrasing")
                assert "lexical_top" in result[1]
                names = [t["function"]["name"] for t in result[0]]
                assert "write_file" in names


def test_gcts_lexical_rescue_no_new_tools(monkeypatch):
    """CB: lex_hits is non-empty but new_lex is empty — no domain added."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)

    # Lexical hit returns a tool already selected (get_system_stats via trigger)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        with patch.object(tools, "_lexical_rank_tools", return_value=["get_system_stats"]):
            with patch.object(tools, "_semantic_rank_tools", return_value=[]):
                result = tools.get_contextual_tool_schemas("system stats")
                assert "lexical_top" not in result[1]


def test_gcts_lexical_rescue_empty(monkeypatch):
    """CB: lex_hits is empty — lexical branch skipped entirely."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        with patch.object(tools, "_lexical_rank_tools", return_value=[]):
            with patch.object(tools, "_semantic_rank_tools", return_value=[]):
                result = tools.get_contextual_tool_schemas("system stats")
                assert "lexical_top" not in result[1]


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: semantic rescue
# ---------------------------------------------------------------------------


def test_gcts_semantic_rescue_adds_new_tools(monkeypatch):
    """CB: sem_hits branch — semantic ranking adds tools not in selected_tools."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        with patch.object(tools, "_lexical_rank_tools", return_value=[]):
            with patch.object(tools, "_semantic_rank_tools", return_value=["web_search"]):
                result = tools.get_contextual_tool_schemas("system stats")
                assert "semantic_top" in result[1]
                names = [t["function"]["name"] for t in result[0]]
                assert "web_search" in names


def test_gcts_semantic_rescue_no_new_tools(monkeypatch):
    """CB: sem_hits non-empty but new_sem empty — no domain added."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        with patch.object(tools, "_lexical_rank_tools", return_value=[]):
            with patch.object(tools, "_semantic_rank_tools", return_value=["get_system_stats"]):
                result = tools.get_contextual_tool_schemas("system stats")
                assert "semantic_top" not in result[1]


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: fast gear path vs normal
# ---------------------------------------------------------------------------


def test_gcts_gear_fast_core_fast_tools(monkeypatch):
    """CB: gear == 'fast' + no matched_non_core + core_fast_tools → fast path."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
            "core_fast_tools": ["read_file"],
        },
        "files_office": {
            "always_active": False,
            "triggers": ["write"],
            "window_hints": [],
            "tools": ["write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("hello", gear="fast")
        # No non-core domain matched; fast gear uses core_fast_tools only
        assert "os_hardware(fast)" in result[1]
        names = [t["function"]["name"] for t in result[0]]
        assert "read_file" in names
        assert "write_file" not in names


def test_gcts_gear_fast_with_matched_non_core(monkeypatch):
    """CB: gear == 'fast' but matched_non_core=True → normal os_hardware path."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
            "core_fast_tools": ["read_file"],
        },
        "files_office": {
            "always_active": False,
            "triggers": ["write"],
            "window_hints": [],
            "tools": ["write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("write file", gear="fast")
        # matched_non_core=True → normal path, includes all os_hardware tools
        assert "os_hardware" in result[1]
        names = [t["function"]["name"] for t in result[0]]
        assert "get_system_stats" in names


def test_gcts_gear_fast_no_core_fast_tools(monkeypatch):
    """CB: gear == 'fast' + no core_fast_tools → falls to else (normal path)."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("hello", gear="fast")
        assert "os_hardware" in result[1]
        names = [t["function"]["name"] for t in result[0]]
        assert "get_system_stats" in names


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: deep gear fallback
# ---------------------------------------------------------------------------


def test_gcts_gear_deep_fallback(monkeypatch):
    """CB: gear == 'deep' + not matched_non_core → adds fallback domains."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
        "files_office": {
            "always_active": False,
            "triggers": ["file"],
            "window_hints": [],
            "tools": ["write_file"],
        },
        "web_comms": {
            "always_active": False,
            "triggers": ["web"],
            "window_hints": [],
            "tools": ["web_search"],
        },
        "skills_memory": {
            "always_active": False,
            "triggers": ["remember"],
            "window_hints": [],
            "tools": ["remember_fact"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("hello", gear="deep")
        # No non-core domain matched → deep fallback adds files_office, web_comms, skills_memory
        assert "files_office" in result[1]
        assert "web_comms" in result[1]
        assert "skills_memory" in result[1]


def test_gcts_gear_deep_with_matched_non_core(monkeypatch):
    """CB: gear == 'deep' but matched_non_core=True → no deep fallback."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
        "files_office": {
            "always_active": False,
            "triggers": ["file"],
            "window_hints": [],
            "tools": ["write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("file write", gear="deep")
        assert "files_office" in result[1]  # matched via trigger, not deep fallback


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: capability boundary filter
# ---------------------------------------------------------------------------


def test_gcts_capability_boundary_filters_out(monkeypatch):
    """CB: cap_ok == False filters tool out of presentable list."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats", "write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    monkeypatch.setattr(config_mod, "TIER2_APPROVAL_TOOLS", set())

    # write_file blocked by capability, get_system_stats allowed
    def fake_check(name, is_tier2=False):
        return (name == "get_system_stats", "")

    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        with patch.object(tools.capabilities, "check_tool_execution_boundary", fake_check):
            result = tools.get_contextual_tool_schemas("system stats")
            names = [t["function"]["name"] for t in result[0]]
            assert "get_system_stats" in names
            assert "write_file" not in names


def test_gcts_capability_boundary_presentable_empty_keeps_pruned(monkeypatch):
    """CB: presentable empty → pruned NOT replaced, keeps original tools."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    monkeypatch.setattr(config_mod, "TIER2_APPROVAL_TOOLS", set())

    def fake_check_all_blocked(name, is_tier2=False):
        return (False, "blocked")

    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        with patch.object(tools.capabilities, "check_tool_execution_boundary", fake_check_all_blocked):
            result = tools.get_contextual_tool_schemas("system stats")
            # presentable empty → pruned NOT replaced → keeps get_system_stats
            names = [t["function"]["name"] for t in result[0]]
            assert "get_system_stats" in names


def test_gcts_capability_boundary_partial_block(monkeypatch):
    """CB: presentable non-empty with some blocked → only allowed tools remain."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats", "write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    monkeypatch.setattr(config_mod, "TIER2_APPROVAL_TOOLS", set())

    def fake_check_partial(name, is_tier2=False):
        return (name == "get_system_stats", "")

    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        with patch.object(tools.capabilities, "check_tool_execution_boundary", fake_check_partial):
            result = tools.get_contextual_tool_schemas("system stats")
            names = [t["function"]["name"] for t in result[0]]
            assert "get_system_stats" in names
            assert "write_file" not in names


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: presentable replaces pruned
# ---------------------------------------------------------------------------


def test_gcts_presentable_replaces_pruned(monkeypatch):
    """CB: presentable is non-empty → pruned = presentable."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats", "write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    monkeypatch.setattr(config_mod, "TIER2_APPROVAL_TOOLS", set())

    def fake_check_all_ok(name, is_tier2=False):
        return (True, "")

    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        with patch.object(tools.capabilities, "check_tool_execution_boundary", fake_check_all_ok):
            result = tools.get_contextual_tool_schemas("system stats")
            names = [t["function"]["name"] for t in result[0]]
            assert "get_system_stats" in names
            assert "write_file" in names


# ---------------------------------------------------------------------------
# get_contextual_tool_schemas: matched_non_core false with os_hardware
# ---------------------------------------------------------------------------


def test_gcts_matched_non_core_false_os_hardware(monkeypatch):
    """CB: no domain matched → matched_non_core=False → normal os_hardware path."""
    import celina_pkg.config as config_mod

    domains = {
        "os_hardware": {
            "always_active": False,
            "triggers": ["system"],
            "window_hints": [],
            "tools": ["get_system_stats"],
        },
        "files_office": {
            "always_active": False,
            "triggers": ["file"],
            "window_hints": [],
            "tools": ["write_file"],
        },
    }
    monkeypatch.setattr(config_mod, "TOOL_DOMAINS", domains)
    with patch.object(tools, "get_enabled_tool_schemas", return_value=ALL_SCHEMAS):
        result = tools.get_contextual_tool_schemas("system stats")
        assert "os_hardware" in result[1]
        names = [t["function"]["name"] for t in result[0]]
        assert "get_system_stats" in names
