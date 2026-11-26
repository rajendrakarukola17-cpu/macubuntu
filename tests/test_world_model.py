"""
Regression tests for features/celina/celina_pkg/world_model.py (audit items 2, 10, 12).

Covers:
  - Tool-specific simulation (write_file, edit_file, move_file, trash_file)
  - Protected path blocking (fail-closed policy)
  - Structured Violation kinds (precondition/policy/risk) — not substring matching
  - Unsimulatable shell labeling (audit item 12)
  - Approval digest binding + re-simulation mismatch detection (audit item 2)
  - Fail-closed on unknown tools
"""

from __future__ import annotations

import pytest

from celina_pkg.world_model import (
    SimResult,
    Violation,
    simulate_intervention,
    verify_sim_digest,
)


# ------------------------------------------------------------------
# write_file simulation
# ------------------------------------------------------------------

def test_write_file_sim_allows_tier2():
    sim = simulate_intervention("write_file", {"path": "~/Documents/notes.txt", "content": "hello"})
    assert sim.recommended_guard == "tier2"
    assert sim.confidence > 0
    assert sim.digest
    assert sim.is_feasible()
    assert sim.is_policy_allowed()


def test_write_file_blocks_protected_path():
    sim = simulate_intervention("write_file", {"path": "~/.ssh/id_rsa", "content": "key"})
    assert sim.recommended_guard == "block"
    assert sim.confidence == 1.0
    assert not sim.is_policy_allowed()
    pol_v = [v for v in sim.violations if v.kind == "policy"]
    assert len(pol_v) == 1
    assert pol_v[0].path == "~/.ssh/id_rsa"


def test_write_file_blocks_missing_path():
    sim = simulate_intervention("write_file", {"content": "hello"})
    assert sim.recommended_guard == "block"
    assert not sim.is_feasible()
    pre_v = [v for v in sim.violations if v.kind == "precondition"]
    assert len(pre_v) == 1


# ------------------------------------------------------------------
# edit_file simulation
# ------------------------------------------------------------------

def test_edit_file_sim_allows_tier2():
    sim = simulate_intervention("edit_file", {"path": "~/Documents/notes.txt", "old_text": "foo", "new_text": "bar"})
    assert sim.recommended_guard == "tier2"
    assert sim.is_feasible()
    assert sim.is_policy_allowed()


def test_edit_file_blocks_protected_path():
    sim = simulate_intervention("edit_file", {"path": "~/.netrc", "old_text": "x", "new_text": "y"})
    assert sim.recommended_guard == "block"
    assert not sim.is_policy_allowed()


def test_edit_file_blocks_missing_old_text():
    sim = simulate_intervention("edit_file", {"path": "~/Documents/notes.txt"})
    assert sim.recommended_guard == "block"
    assert not sim.is_feasible()


# ------------------------------------------------------------------
# move_file simulation
# ------------------------------------------------------------------

def test_move_file_sim_allows_tier2():
    sim = simulate_intervention("move_file", {"src": "~/Documents/a.txt", "dst": "~/Desktop/b.txt"})
    assert sim.recommended_guard == "tier2"
    assert sim.is_feasible()
    assert sim.is_policy_allowed()


def test_move_file_blocks_protected_path():
    sim = simulate_intervention("move_file", {"src": "~/.ssh/id_rsa", "dst": "~/Desktop/b.txt"})
    assert sim.recommended_guard == "block"
    assert not sim.is_policy_allowed()


def test_move_file_blocks_missing_src_dst():
    sim = simulate_intervention("move_file", {"src": "~/Documents/a.txt"})
    assert sim.recommended_guard == "block"
    assert not sim.is_feasible()


# ------------------------------------------------------------------
# trash_file simulation
# ------------------------------------------------------------------

def test_trash_file_sim_allows_tier2():
    sim = simulate_intervention("trash_file", {"path": "~/Documents/notes.txt"})
    assert sim.recommended_guard == "tier2"
    assert sim.effects.get("reversible") is True


def test_trash_file_blocks_protected_path():
    sim = simulate_intervention("trash_file", {"path": "~/.git-credentials"})
    assert sim.recommended_guard == "block"
    assert not sim.is_policy_allowed()


# ------------------------------------------------------------------
# Shell is unsimulatable (audit item 12)
# ------------------------------------------------------------------

def test_shell_unsimulatable():
    sim = simulate_intervention("run_shell_command", {"command": "rm -rf /"})
    assert sim.unsimulatable is True
    assert sim.confidence == 0.0
    assert sim.recommended_guard == "tier2"
    assert "unsimulatable" in str(sim.warnings).lower()


def test_python_sandbox_sim():
    sim = simulate_intervention("run_python_sandbox", {"code": "print(1)"})
    assert sim.recommended_guard == "tier2"
    assert sim.effects.get("sandboxed") is True
    assert sim.effects.get("network") is False


# ------------------------------------------------------------------
# Unknown tool fail-closed (audit item 2)
# ------------------------------------------------------------------

def test_unknown_tool_tier2():
    sim = simulate_intervention("nonexistent_tool", {"foo": "bar"})
    assert sim.recommended_guard == "tier2"
    assert sim.confidence == 0.0


# ------------------------------------------------------------------
# Approval digest binding (audit item 2)
# ------------------------------------------------------------------

def test_digest_matches_on_resim():
    args = {"path": "~/Documents/notes.txt", "content": "hello v1"}
    sim1 = simulate_intervention("write_file", args)
    ok, err = verify_sim_digest("write_file", args, sim1.digest)
    assert ok is True
    assert err == ""


def test_digest_mismatch_on_arg_change():
    args1 = {"path": "~/Documents/notes.txt", "content": "hello v1"}
    args2 = {"path": "~/Documents/notes.txt", "content": "hello v2 - tampered"}
    sim1 = simulate_intervention("write_file", args1)
    ok, err = verify_sim_digest("write_file", args2, sim1.digest)
    assert ok is False
    assert "mismatch" in err.lower()
    assert "re-approval" in err.lower()


def test_digest_mismatch_on_tool_change():
    args = {"path": "~/Documents/notes.txt", "content": "hello"}
    sim_write = simulate_intervention("write_file", args)
    ok, err = verify_sim_digest("edit_file", args, sim_write.digest)
    assert ok is False


def test_digest_is_deterministic():
    args = {"path": "~/Documents/notes.txt", "content": "hello"}
    d1 = simulate_intervention("write_file", args).digest
    d2 = simulate_intervention("write_file", args).digest
    assert d1 == d2


# ------------------------------------------------------------------
# Structured Violation kinds (audit item 10)
# ------------------------------------------------------------------

def test_violation_kinds():
    # precondition violation
    sim = simulate_intervention("write_file", {"content": "no path"})
    assert not sim.is_feasible()
    pre_v = [v for v in sim.violations if v.kind == "precondition"]
    assert len(pre_v) >= 1

    # policy violation
    sim = simulate_intervention("write_file", {"path": "~/.ssh/id_rsa", "content": "key"})
    assert not sim.is_policy_allowed()
    pol_v = [v for v in sim.violations if v.kind == "policy"]
    assert len(pol_v) >= 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
