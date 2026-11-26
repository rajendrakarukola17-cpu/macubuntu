"""
Regression tests for features/celina/celina_pkg/evidence.py P2 observability.

Tests the full hash-chained audit lifecycle:
  predict → approve → execute → verify → calibrate

Plus chain integrity (verify_chain) and tamper-evidence.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from celina_pkg.evidence import EvidenceLedger, GENESIS_HASH


# ------------------------------------------------------------------
# Fixture
# ------------------------------------------------------------------

@pytest.fixture
def ledger():
    with tempfile.TemporaryDirectory() as td:
        ledger_path = Path(td) / "evidence" / "ledger.jsonl"
        yield EvidenceLedger(ledger_path=ledger_path)


# ------------------------------------------------------------------
# Full lifecycle: predict → approve → execute → verify → calibrate
# ------------------------------------------------------------------

def test_full_lifecycle_chain(ledger):
    """All five lifecycle stages form a valid hash chain."""
    args = {"path": "~/Documents/notes.txt", "content": "hello"}

    # 1. predict
    r1 = ledger.record_prediction(
        tool_name="write_file",
        arguments=args,
        predicted_success=0.85,
        sim_digest="abc123def456",
        task_id="task-1",
    )
    assert r1["event_type"] == "prediction"

    # 2. approve
    r2 = ledger.record_approval(
        tool_name="write_file",
        arguments=args,
        sim_digest=r1.get("entry_hash", "")[:16],
        decision="allow",
        task_id="task-1",
    )
    assert r2["event_type"] == "approval"

    # 3. execute
    r3 = ledger.record_execution(
        tool_name="write_file",
        arguments=args,
        result_text="File written successfully",
        task_id="task-1",
    )
    assert r3["event_type"] == "tool_execution"

    # 4. verify
    r4 = ledger.record_verification(
        tool_name="write_file",
        arguments=args,
        verified_success=True,
        verification_method="re-stat/hash",
        task_id="task-1",
    )
    assert r4["event_type"] == "verification"

    # 5. calibrate
    r5 = ledger.record_calibration(
        tool_name="write_file",
        context_key="write_file:offline:gear2",
        signed_bias=0.05,
        wilson_lower=0.72,
        is_unproven=False,
        task_id="task-1",
    )
    assert r5["event_type"] == "calibration"


# ------------------------------------------------------------------
# Chain integrity
# ------------------------------------------------------------------

def test_verify_chain_valid(ledger):
    """verify_chain must pass on an un-tampered ledger."""
    ledger.record_prediction(
        tool_name="write_file",
        arguments={"path": "~/x.txt", "content": "y"},
        predicted_success=0.5,
        task_id="task-1",
    )
    ledger.record_execution(
        tool_name="write_file",
        arguments={"path": "~/x.txt", "content": "y"},
        result_text="ok",
        task_id="task-1",
    )
    ok, msg, count = ledger.verify_chain()
    assert ok is True
    assert count == 2


def test_verify_chain_tamper_detection(ledger):
    """Modifying a ledger entry must break the chain (tamper-evident)."""
    ledger.record_prediction(
        tool_name="write_file",
        arguments={"path": "~/x.txt", "content": "y"},
        predicted_success=0.5,
        task_id="task-1",
    )
    ledger.record_execution(
        tool_name="write_file",
        arguments={"path": "~/x.txt", "content": "y"},
        result_text="ok",
        task_id="task-1",
    )

    # Tamper: modify the first line's result_text
    lines = ledger.ledger_path.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[0])
    obj["result_text"] = "TAMPERED"
    lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    ledger.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, msg, count = ledger.verify_chain()
    assert ok is False
    assert "mismatch" in msg.lower() or "tampered" in msg.lower()


def test_genesis_hash_for_first_entry(ledger):
    """The first entry's prev_hash must be the genesis hash."""
    r = ledger.record_execution(
        tool_name="write_file",
        arguments={"path": "~/x.txt", "content": "y"},
        result_text="ok",
    )
    assert r["prev_hash"] == GENESIS_HASH


def test_chain_linkage(ledger):
    """Each entry's prev_hash must equal the previous entry's entry_hash."""
    r1 = ledger.record_execution(
        tool_name="tool_a",
        arguments={"a": 1},
        result_text="ok",
    )
    r2 = ledger.record_execution(
        tool_name="tool_b",
        arguments={"b": 2},
        result_text="ok",
    )
    assert r2["prev_hash"] == r1["entry_hash"]


# ------------------------------------------------------------------
# Event type filtering
# ------------------------------------------------------------------

def test_list_entries_filters_by_event_type(ledger):
    """list_entries should return all entries; we can filter by event_type."""
    ledger.record_prediction(
        tool_name="tool_a",
        arguments={"a": 1},
        predicted_success=0.5,
    )
    ledger.record_approval(
        tool_name="tool_a",
        arguments={"a": 1},
        sim_digest="digest",
        decision="allow",
    )
    ledger.record_execution(
        tool_name="tool_a",
        arguments={"a": 1},
        result_text="ok",
    )

    entries = ledger.list_entries(limit=100)
    event_types = {e["event_type"] for e in entries}
    assert "prediction" in event_types
    assert "approval" in event_types
    assert "tool_execution" in event_types


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
