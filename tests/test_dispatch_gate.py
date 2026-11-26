"""
Regression tests for features/celina/celina_pkg/world_model.py dispatch gate
(audit item 2: simulate → approve → re-verify → execute → verify → calibrate).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from celina_pkg.world_model import (
    DispatchGateOutcome,
    DispatchGateResult,
    dispatch_post_check,
    dispatch_pre_check,
    simulate_intervention,
    verify_sim_digest,
)


# ------------------------------------------------------------------
# dispatch_pre_check
# ------------------------------------------------------------------

def test_pre_check_blocks_protected_path():
    """Pre-check must refuse when sim recommends 'block' (fail-closed)."""
    result = dispatch_pre_check("write_file", {"path": "~/.ssh/id_rsa", "content": "key"})
    assert result.proceed is False
    assert result.sim.recommended_guard == "block"
    assert "blocked" in result.reason.lower()
    assert result.requires_approval is False


def test_pre_check_allows_tier2():
    """Pre-check must proceed when sim recommends 'tier2'."""
    result = dispatch_pre_check("write_file", {"path": "~/Documents/notes.txt", "content": "hello"})
    assert result.proceed is True
    assert result.sim.recommended_guard == "tier2"
    assert result.reason == ""
    # write_file has FILE_WRITE risk, not in ALWAYS_RECONFIRM, so no fresh confirmation needed
    assert result.requires_approval is False


def test_pre_check_always_reconfirm_requires_approval():
    """Tools in ALWAYS_RECONFIRM_TOOLS (e.g. move_file, trash_file) require approval."""
    result = dispatch_pre_check("move_file", {"src": "~/Documents/a.txt", "dst": "~/Desktop/b.txt"})
    assert result.proceed is True
    assert result.sim.recommended_guard == "tier2"
    assert result.requires_approval is True


def test_pre_check_unknown_tool_tier2():
    """Unknown tools fail-closed to tier2 and require approval."""
    result = dispatch_pre_check("nonexistent_tool", {"foo": "bar"})
    assert result.proceed is True
    assert result.sim.recommended_guard == "tier2"
    assert result.requires_approval is True


def test_pre_check_readonly_no_approval():
    """Read-only tools should not require fresh confirmation."""
    result = dispatch_pre_check("list_files", {"path": "~/Documents"})
    assert result.proceed is True
    assert result.requires_approval is False


# ------------------------------------------------------------------
# dispatch_post_check
# ------------------------------------------------------------------

def test_post_check_matching_digest(ledger_model):
    """Post-check succeeds when the digest matches."""
    args = {"path": "~/Documents/notes.txt", "content": "hello"}
    sim = simulate_intervention("write_file", args)
    outcome = dispatch_post_check(
        tool="write_file",
        arguments=args,
        approved_digest=sim.digest,
        verified_success=True,
        action_id="act-1",
        online=False,
        gear=2,
        self_model_instance=ledger_model,
    )
    assert outcome.verified is True
    assert outcome.action_id == "act-1"
    assert outcome.reason == ""


def test_post_check_digest_mismatch(ledger_model):
    """Post-check must reject when arguments changed after approval."""
    args_approved = {"path": "~/Documents/notes.txt", "content": "original"}
    args_swapped = {"path": "~/Documents/notes.txt", "content": "tampered"}
    sim = simulate_intervention("write_file", args_approved)
    outcome = dispatch_post_check(
        tool="write_file",
        arguments=args_swapped,
        approved_digest=sim.digest,
        verified_success=True,
        action_id="act-1",
        online=False,
        gear=2,
        self_model_instance=ledger_model,
    )
    assert outcome.verified is False
    assert "mismatch" in outcome.reason.lower()


def test_post_check_records_outcome(ledger_model):
    """Post-check must record the outcome in the self-model."""
    args = {"path": "~/Documents/notes.txt", "content": "hello"}
    sim = simulate_intervention("write_file", args)
    dispatch_post_check(
        tool="write_file",
        arguments=args,
        approved_digest=sim.digest,
        verified_success=True,
        action_id="act-cal",
        online=False,
        gear=2,
        self_model_instance=ledger_model,
    )
    stats = ledger_model.get_stats("write_file", "write_file:offline:gear2")
    assert stats["attempts"] == 1
    assert stats["success_rate"] == 1.0


def test_post_check_records_failed_outcome(ledger_model):
    """Post-check must record failed outcomes too."""
    args = {"path": "~/Documents/notes.txt", "content": "hello"}
    sim = simulate_intervention("write_file", args)
    dispatch_post_check(
        tool="write_file",
        arguments=args,
        approved_digest=sim.digest,
        verified_success=False,
        action_id="act-fail",
        online=False,
        gear=2,
        self_model_instance=ledger_model,
    )
    stats = ledger_model.get_stats("write_file", "write_file:offline:gear2")
    assert stats["attempts"] == 1
    assert stats["success_rate"] == 0.0


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------

@pytest.fixture
def ledger_model():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "memory.db"
        from celina_pkg.self_model import SelfModel
        yield SelfModel(db_path=db_path)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
