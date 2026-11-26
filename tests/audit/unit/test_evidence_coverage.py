"""
tests/audit/unit/test_evidence_coverage.py

Phase 7b — TASK 7: EvidenceLedger branch coverage tests (target 85%).

Covers: record_execution normal flow, anchor verify on open (valid/missing/corrupt),
ledger deletion/truncation/tampering fail-closed, reset with/without confirm,
hash chain integrity, file mode 0600.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg.evidence import EvidenceLedger, EvidenceIntegrityError, GENESIS_HASH


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_ledger(tmp_path, verify=True):
    ledger_path = tmp_path / "evidence.jsonl"
    anchor_path = tmp_path / "evidence.anchor.json"
    return EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=verify,
    )


# ---------------------------------------------------------------------------
# Test: normal record_execution flow
# ---------------------------------------------------------------------------


def test_record_execution_normal_flow(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_execution(
        tool_name="test_tool",
        arguments={"key": "value"},
        result_text="success result",
        why_reasoning="test reasoning",
        actor_identity="user1",
        policy_decision="allow",
        event_type="tool_execution",
    )
    assert record["seq"] == 1
    assert record["tool"] == "test_tool"
    assert record["outcome"] == "ok"
    assert record["prev_hash"] == GENESIS_HASH
    assert record["entry_hash"] != ""
    assert record["actor_identity"] == "user1"

    # Verify ledger file was written with 0600
    assert ledger.ledger_path.stat().st_mode & 0o777 == 0o600
    assert ledger.anchor_path.stat().st_mode & 0o777 == 0o600


def test_record_execution_denied_outcome(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_execution(
        tool_name="test_tool",
        arguments={},
        result_text="Denied: insufficient permissions",
        actor_identity="user1",
    )
    assert record["outcome"] == "denied"


def test_record_execution_blocked_outcome(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_execution(
        tool_name="test_tool",
        arguments={},
        result_text="Blocked: SSRF policy",
        actor_identity="user1",
    )
    assert record["outcome"] == "blocked"


def test_record_execution_error_outcome(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_execution(
        tool_name="test_tool",
        arguments={},
        result_text="Error: tool failed",
        actor_identity="user1",
    )
    assert record["outcome"] == "error"


def test_record_execution_hash_chain(tmp_path):
    ledger = _make_ledger(tmp_path)
    prev_hash = GENESIS_HASH
    for i in range(5):
        record = ledger.record_execution(
            tool_name=f"tool_{i}",
            arguments={"idx": i},
            result_text=f"result-{i}",
            actor_identity="user1",
        )
        assert record["prev_hash"] == prev_hash
        assert record["seq"] == i + 1
        prev_hash = record["entry_hash"]


# ---------------------------------------------------------------------------
# Test: anchor verify on open — valid anchor
# ---------------------------------------------------------------------------


def test_anchor_verify_valid(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="tool", arguments={}, result_text="data", actor_identity="user"
    )
    # Re-open with verification — should succeed
    EvidenceLedger(
        ledger_path=ledger.ledger_path,
        anchor_path=ledger.anchor_path,
        verify_anchor_on_open=True,
    )


# ---------------------------------------------------------------------------
# Test: anchor verify on open — missing anchor (first run)
# ---------------------------------------------------------------------------


def test_anchor_verify_missing_anchor_ok(tmp_path):
    """First run with no anchor file — should succeed."""
    ledger_path = tmp_path / "evidence.jsonl"
    anchor_path = tmp_path / "evidence.anchor.json"
    # Create ledger file without anchor
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("")
    ledger = EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )
    # Should not raise
    ledger.record_execution(
        tool_name="tool", arguments={}, result_text="data", actor_identity="user"
    )


# ---------------------------------------------------------------------------
# Test: anchor verify on open — corrupt anchor
# ---------------------------------------------------------------------------


def test_anchor_verify_corrupt_anchor_fails(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="tool", arguments={}, result_text="data", actor_identity="user"
    )
    # Corrupt the anchor
    ledger.anchor_path.write_text("NOT JSON AT ALL")
    with pytest.raises(EvidenceIntegrityError, match="CORRUPT"):
        EvidenceLedger(
            ledger_path=ledger.ledger_path,
            anchor_path=ledger.anchor_path,
            verify_anchor_on_open=True,
        )


# ---------------------------------------------------------------------------
# Test: ledger deletion detection (fail-closed)
# ---------------------------------------------------------------------------


def test_ledger_deletion_detected(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="tool", arguments={}, result_text="data", actor_identity="user"
    )
    # Delete the ledger file
    ledger.ledger_path.unlink()
    with pytest.raises(EvidenceIntegrityError, match="DELETED"):
        EvidenceLedger(
            ledger_path=ledger.ledger_path,
            anchor_path=ledger.anchor_path,
            verify_anchor_on_open=True,
        )


# ---------------------------------------------------------------------------
# Test: ledger truncation detection (fail-closed)
# ---------------------------------------------------------------------------


def test_ledger_truncation_detected(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="tool", arguments={}, result_text="first", actor_identity="user"
    )
    ledger.record_execution(
        tool_name="tool", arguments={}, result_text="second", actor_identity="user"
    )
    # Truncate: keep only first record
    lines = ledger.ledger_path.read_text().strip().split("\n")
    ledger.ledger_path.write_text(lines[0] + "\n")
    with pytest.raises(EvidenceIntegrityError, match="TRUNCATED"):
        EvidenceLedger(
            ledger_path=ledger.ledger_path,
            anchor_path=ledger.anchor_path,
            verify_anchor_on_open=True,
        )


# ---------------------------------------------------------------------------
# Test: tampering detection (fail-closed)
# ---------------------------------------------------------------------------


def test_ledger_tampering_detected(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="tool", arguments={}, result_text="original", actor_identity="user"
    )
    # Tamper: change entry_hash
    lines = ledger.ledger_path.read_text().strip().split("\n")
    record = json.loads(lines[0])
    record["entry_hash"] = "0" * 64
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    ledger.ledger_path.write_text("\n".join(lines) + "\n")
    with pytest.raises(EvidenceIntegrityError, match="TAMPERED|tip hash"):
        EvidenceLedger(
            ledger_path=ledger.ledger_path,
            anchor_path=ledger.anchor_path,
            verify_anchor_on_open=True,
        )


# ---------------------------------------------------------------------------
# Test: reset with confirm=True
# ---------------------------------------------------------------------------


def test_reset_with_confirm(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="tool", arguments={}, result_text="data", actor_identity="user"
    )
    ledger.reset_anchor(confirm=True)
    assert not ledger.anchor_path.exists()


# ---------------------------------------------------------------------------
# Test: reset without confirm raises
# ---------------------------------------------------------------------------


def test_reset_without_confirm_raises(tmp_path):
    ledger = _make_ledger(tmp_path)
    with pytest.raises(ValueError, match="confirm"):
        ledger.reset_anchor()


# ---------------------------------------------------------------------------
# Test: empty ledger with anchor count=0
# ---------------------------------------------------------------------------


def test_empty_ledger_anchor_count_zero(tmp_path):
    """Anchor with count=0 should not fail verification."""
    ledger_path = tmp_path / "evidence.jsonl"
    anchor_path = tmp_path / "evidence.anchor.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("")  # empty ledger
    # Write anchor with count=0
    anchor = json.dumps({"count": 0, "tip_hash": GENESIS_HASH})
    anchor_path.write_text(anchor)
    # Should not raise (count <= 0 returns early)
    EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )


# ---------------------------------------------------------------------------
# Test: verify_anchor_on_open=False skips verification
# ---------------------------------------------------------------------------


def test_skip_anchor_verify(tmp_path):
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="tool", arguments={}, result_text="data", actor_identity="user"
    )
    # Corrupt the ledger
    ledger.ledger_path.write_text("GARBAGE\n")
    # Should not raise when verify_anchor_on_open=False
    ledger2 = EvidenceLedger(
        ledger_path=ledger.ledger_path,
        anchor_path=ledger.anchor_path,
        verify_anchor_on_open=False,
    )
    assert ledger2 is not None


# ---------------------------------------------------------------------------
# Test: record_prediction, record_approval, record_verification, etc.
# ---------------------------------------------------------------------------


def test_record_prediction(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_prediction(
        tool_name="tool",
        arguments={"x": 1},
        predicted_success=0.85,
        sim_digest="abc123",
        actor_identity="user",
    )
    assert record["event_type"] == "prediction"
    assert record["policy_decision"] == "predict"
    assert record["seq"] == 1


def test_record_prediction_no_sim_digest(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_prediction(
        tool_name="tool",
        arguments={"x": 1},
        predicted_success=0.5,
        sim_digest="",
        actor_identity="user",
    )
    assert record["event_type"] == "prediction"


def test_record_approval(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_approval(
        tool_name="tool",
        arguments={"x": 1},
        sim_digest="abc123",
        decision="allow",
        actor_identity="user",
    )
    assert record["event_type"] == "approval"
    assert record["policy_decision"] == "allow"


def test_record_verification(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_verification(
        tool_name="tool",
        arguments={"x": 1},
        verified_success=True,
        actor_identity="user",
    )
    assert record["event_type"] == "verification"
    assert record["policy_decision"] == "verified"


def test_record_verification_false(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_verification(
        tool_name="tool",
        arguments={"x": 1},
        verified_success=False,
        actor_identity="user",
    )
    assert record["outcome"] == "ok"  # "verified_success=False" doesn't match error prefixes


def test_record_calibration(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_calibration(
        tool_name="tool",
        context_key="ctx",
        signed_bias=0.1,
        wilson_lower=0.05,
        is_unproven=False,
        actor_identity="user",
    )
    assert record["event_type"] == "calibration"


def test_record_security_event(tmp_path):
    ledger = _make_ledger(tmp_path)
    record = ledger.record_security_event(
        event_type="prompt_injection",
        detail="detected injection attempt",
        actor_identity="user",
    )
    assert record["event_type"] == "prompt_injection"
    assert record["policy_decision"] == "deny:prompt_injection"
    assert record["outcome"] == "blocked"


def test_list_entries_empty(tmp_path):
    ledger = _make_ledger(tmp_path)
    entries = ledger.list_entries()
    assert entries == []


def test_list_entries_with_data(tmp_path):
    ledger = _make_ledger(tmp_path)
    for i in range(5):
        ledger.record_execution(
            tool_name="tool", arguments={"i": i}, result_text=f"r{i}", actor_identity="user"
        )
    entries = ledger.list_entries(limit=3)
    assert len(entries) == 3


def test_verify_chain_valid(tmp_path):
    ledger = _make_ledger(tmp_path)
    for i in range(3):
        ledger.record_execution(
            tool_name="tool", arguments={"i": i}, result_text=f"r{i}", actor_identity="user"
        )
    ok, msg, count = ledger.verify_chain()
    assert ok is True
    assert count == 3


def test_verify_chain_empty(tmp_path):
    ledger_path = tmp_path / "evidence.jsonl"
    anchor_path = tmp_path / "evidence.anchor.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("")
    ledger = EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=False,
    )
    ok, msg, count = ledger.verify_chain()
    assert ok is True
    assert count == 0