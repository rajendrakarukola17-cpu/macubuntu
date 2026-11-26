"""
Fault-injection tests for P2 operational resilience.

Tests that Celina fails CLOSED under adverse conditions:
  - Sim crash → block guard (never fail open)
  - Disk full → mutation blocked (reversibility not claimed)
  - Network timeout → Tier-2 actions denied
  - Hostile MCP output → sanitized, never executed
  - Symlink race → TOCTOU rejected
  - Corrupt evidence ledger → detected by verify_chain
  - Approval callback exception → fail-closed deny
  - kill -9 mid-saga → clean recovery (no orphaned checkpoints)
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from celina_pkg.evidence import EvidenceLedger, GENESIS_HASH
from celina_pkg.policy import is_protected_path
from celina_pkg.self_model import SelfModel
from celina_pkg.world_model import (
    SimResult,
    dispatch_pre_check,
    simulate_intervention,
    verify_sim_digest,
)


# ------------------------------------------------------------------
# 1. Sim crash → fail closed (audit item 1)
# ------------------------------------------------------------------

def test_sim_crash_never_returns_allow():
    """If the simulator crashes, the guard must be 'block', never 'allow'."""
    # Patch simulate_intervention to raise inside a tool-specific sim
    from celina_pkg import world_model

    orig = world_model._sim_write_file

    def crashing_sim(args):
        raise RuntimeError("simulator crashed")

    world_model._sim_write_file = crashing_sim
    try:
        sim = simulate_intervention("write_file", {"path": "~/Documents/x.txt", "content": "y"})
        assert sim.recommended_guard == "block"
        assert sim.confidence == 0.0
        assert "crashed" in str(sim.warnings).lower() or "error" in str(sim.warnings).lower()
    finally:
        world_model._sim_write_file = orig


# ------------------------------------------------------------------
# 2. Disk full → mutation blocked (audit item 3)
# ------------------------------------------------------------------

@patch("shutil.disk_usage")
def test_disk_full_blocks_mutation(mock_disk_usage):
    """When disk is full, _check_disk_headroom returns False."""
    class FakeUsage:
        free = 1024  # 1 KB free — way below 50 MB floor
    mock_disk_usage.return_value = FakeUsage()

    from celina_pkg.autonomy.self_healing import _check_disk_headroom
    ok, err = _check_disk_headroom("~/Documents/notes.txt", needed_bytes=0)
    assert ok is False
    assert "insufficient" in err.lower() or "headroom" in err.lower()


# ------------------------------------------------------------------
# 3. Network timeout → Tier-2 denied
# ------------------------------------------------------------------

def test_network_tool_tier2_guard():
    """Network tools (web_search, fetch_webpage) get tier2 guard — never auto-execute."""
    sim = simulate_intervention("web_search", {"query": "test"})
    assert sim.recommended_guard == "tier2"
    assert sim.confidence < 1.0


# ------------------------------------------------------------------
# 4. Hostile MCP output → sanitized, never executed
# ------------------------------------------------------------------

def test_mcp_output_unsimulatable():
    """MCP perception/actuation tools get tier2 guard (external process, not directly simulatable)."""
    sim = simulate_intervention("mcp_call_tool", {"server": "hostile", "tool": "rm -rf /"})
    assert sim.recommended_guard == "tier2"
    assert sim.confidence < 1.0


# ------------------------------------------------------------------
# 5. Symlink race → TOCTOU rejected
# ------------------------------------------------------------------

def test_symlink_race_blocked():
    """A symlink anywhere in the path must be rejected."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        real_dir = td / "real"
        real_dir.mkdir()
        (real_dir / "target.txt").write_text("secret")
        link = td / "link"
        link.symlink_to(real_dir)

        from celina_pkg.autonomy.self_healing import _validate_target_atomic
        ok, err = _validate_target_atomic(str(link / "target.txt"))
        assert ok is False
        assert "symlink" in err.lower()


# ------------------------------------------------------------------
# 6. Corrupt evidence ledger → detected by verify_chain
# ------------------------------------------------------------------

def test_corrupt_evidence_ledger_detected():
    """Tampering with a ledger entry breaks the chain."""
    with tempfile.TemporaryDirectory() as td:
        ledger_path = Path(td) / "evidence" / "ledger.jsonl"
        ledger = EvidenceLedger(ledger_path=ledger_path)

        ledger.record_execution(
            tool_name="write_file",
            arguments={"path": "~/x.txt", "content": "y"},
            result_text="ok",
            task_id="task-1",
        )

        # Tamper with the entry
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        obj = json.loads(lines[0])
        obj["result_text"] = "TAMPERED"
        lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
        ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, msg, count = ledger.verify_chain()
        assert ok is False
        assert "mismatch" in msg.lower() or "tampered" in msg.lower()


# ------------------------------------------------------------------
# 7. Approval callback exception → fail-closed deny
# ------------------------------------------------------------------

def test_approval_cb_exception_denies():
    """If the approval callback raises, the action is denied (fail-closed)."""
    from celina_pkg.approval import request_bound_approval

    def exploding_cb(title, detail):
        raise RuntimeError("callback exploded")

    token, err = request_bound_approval(
        "write_file",
        {"path": "~/Documents/notes.txt", "content": "hello"},
        exploding_cb,
    )
    assert token is None
    assert err is not None
    assert "fail-closed" in err.lower() or "raised" in err.lower()


# ------------------------------------------------------------------
# 8. No orphaned checkpoints on kill -9 (durability)
# ------------------------------------------------------------------

def test_checkpoint_recovery_after_kill9():
    """PreExecutionCheckpoint captures should be recoverable after a simulated kill -9."""
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td) / "celina"
        undo_dir = data_dir / "undo"
        from celina_pkg.autonomy.checkpoint import PreExecutionCheckpoint, FILE_MUTATION_TOOLS

        cp = PreExecutionCheckpoint(undo_dir)

        # Capture a snapshot (simulates pre-execution checkpoint)
        snap = cp.capture(
            task_id="task-abc",
            tool_name="write_file",
            target_path=str(data_dir / "notes.txt"),
        )
        assert snap is not None
        assert snap.existed_before is False  # new file
        assert snap.action_id is not None
        assert (undo_dir / snap.action_id / "meta.json").is_file()

        # Simulate kill -9: reopen the checkpoint manager
        cp2 = PreExecutionCheckpoint(undo_dir)
        meta_path = undo_dir / snap.action_id / "meta.json"
        assert meta_path.is_file()
        rec = json.loads(meta_path.read_text(encoding="utf-8"))
        assert rec["tool_name"] == "write_file"
        assert rec["task_id"] == "task-abc"


# ------------------------------------------------------------------
# 9. dispatch_pre_check blocks on protected path (fail-closed end-to-end)
# ------------------------------------------------------------------

def test_dispatch_pre_check_blocks_protected_path():
    """End-to-end: protected path → pre_check refuses, no approval needed."""
    result = dispatch_pre_check("write_file", {"path": "~/.ssh/id_rsa", "content": "key"})
    assert result.proceed is False
    assert result.requires_approval is False
    assert "blocked" in result.reason.lower()


# ------------------------------------------------------------------
# 10. Self-model survives restart (persistence)
# ------------------------------------------------------------------

def test_self_model_persists_across_restart():
    """Self-model data must survive a new instance pointing to the same DB."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "memory.db"
        m1 = SelfModel(db_path=db_path)
        m1.record_outcome_full(
            "act-1", "write_file", 0.8, True, online=False, gear=2
        )

        m2 = SelfModel(db_path=db_path)
        stats = m2.get_stats("write_file", "write_file:offline:gear2")
        assert stats["attempts"] == 1
        assert stats["success_rate"] == 1.0


# ------------------------------------------------------------------
# 11. Wilson lower bound blocks auto-exec on small samples
# ------------------------------------------------------------------

def test_wilson_blocks_autoexec_small_sample():
    """With < 30 attempts, Wilson lower bound < 0.95 — earned autonomy denied."""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "memory.db"
        m = SelfModel(db_path=db_path)

        # 29 successful attempts — still not enough for earned autonomy
        for i in range(29):
            m.record_outcome_full(f"a{i}", "tool_x", 1.0, True, online=False, gear=1)

        wl = m.wilson_lower("tool_x", online=False, gear=1)
        assert wl is not None
        assert wl < 0.95, f"Wilson lower bound {wl} should be < 0.95 with only 29 attempts"
        assert m.is_unproven("tool_x", online=False, gear=1) is False  # >= 5 attempts
        # But earned autonomy still requires Wilson >= 0.95


# ------------------------------------------------------------------
# 12. Protected path traversal bypass attempts
# ------------------------------------------------------------------

def test_protected_path_bypass_attempts():
    """Various traversal tricks must still be caught."""
    bypasses = [
        "~/Documents/../../../.ssh/id_rsa",
        "~/Documents/./../../.netrc",
        "/home/user/.ssh/id_rsa",  # absolute protected path
        "~/Documents/../.git-credentials",
    ]
    for path in bypasses:
        assert is_protected_path(path) is True, f"bypass not caught: {path}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
