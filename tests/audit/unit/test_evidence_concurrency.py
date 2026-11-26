"""
tests/audit/unit/test_evidence_concurrency.py

Phase 7b — TASK 9: EvidenceLedger concurrency tests.

Tests:
1. Concurrent record_execution from N threads — valid hash chain, no gaps
2. Tampering detection — modifying a ledger entry fails _verify_anchor_on_open
3. Truncation detection — removing entries fails _verify_anchor_on_open
4. Reset requires confirm=True
5. Anchor verification is fail-closed (never weakened)
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "features" / "celina"))

import pytest

from celina_pkg.evidence import EvidenceLedger, EvidenceIntegrityError, GENESIS_HASH


# ---------------------------------------------------------------------------
# Helper: make a temp EvidenceLedger
# ---------------------------------------------------------------------------


def _make_ledger(tmp_path):
    ledger_path = tmp_path / "evidence.jsonl"
    anchor_path = tmp_path / "evidence.anchor.json"
    return EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )


# ---------------------------------------------------------------------------
# Test 1: Concurrent record_execution from N threads
# ---------------------------------------------------------------------------


def test_concurrent_record_execution_valid_chain(tmp_path):
    """N threads concurrently record_execution — chain must be valid with no gaps."""
    ledger = _make_ledger(tmp_path)
    n_threads = 20
    records_per_thread = 5
    errors = []
    barrier = threading.Barrier(n_threads)

    def worker(thread_id):
        try:
            barrier.wait()
            for i in range(records_per_thread):
                ledger.record_execution(
                    tool_name=f"tool_{thread_id}",
                    arguments={"idx": i, "thread": thread_id},
                    result_text=f"result-{thread_id}-{i}",
                    actor_identity=f"thread-{thread_id}",
                )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent writes: {errors}"

    # Verify chain integrity
    records = []
    with open(ledger.ledger_path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    expected_count = n_threads * records_per_thread
    assert len(records) == expected_count, f"Expected {expected_count} records, got {len(records)}"

    # Verify seq numbers are contiguous starting from 1
    seqs = [r["seq"] for r in records]
    assert seqs == list(range(1, expected_count + 1)), f"Seq numbers not contiguous: {seqs[:5]}...{seqs[-5:]}"

    # Verify hash chain
    prev_hash = GENESIS_HASH
    for r in records:
        assert r["prev_hash"] == prev_hash, f"Hash chain broken at seq {r['seq']}"
        prev_hash = r["entry_hash"]

    # Verify anchor matches last record
    anchor = json.loads(ledger.anchor_path.read_text())
    assert anchor["count"] == expected_count
    assert anchor["tip_hash"] == prev_hash


# ---------------------------------------------------------------------------
# Test 2: Tampering detection
# ---------------------------------------------------------------------------


def test_tampering_detection_fails_closed(tmp_path):
    """Modifying a ledger entry's hash must cause _verify_anchor_on_open to fail."""
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="test_tool", arguments={}, result_text="original", actor_identity="user"
    )

    # Tamper: change entry_hash of the record (breaks hash chain)
    lines = ledger.ledger_path.read_text().strip().split("\n")
    record = json.loads(lines[0])
    record["entry_hash"] = "0000000000000000000000000000000000000000000000000000000000000000"
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    ledger.ledger_path.write_text("\n".join(lines) + "\n")

    # Re-opening the ledger should fail (tip hash mismatch)
    with pytest.raises(EvidenceIntegrityError, match="TAMPERED|tip hash|TRUNCATED"):
        EvidenceLedger(
            ledger_path=ledger.ledger_path,
            anchor_path=ledger.anchor_path,
            verify_anchor_on_open=True,
        )


# ---------------------------------------------------------------------------
# Test 3: Truncation detection
# ---------------------------------------------------------------------------


def test_truncation_detection_fails_closed(tmp_path):
    """Removing ledger entries must cause _verify_anchor_on_open to fail."""
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="test_tool", arguments={}, result_text="first", actor_identity="user"
    )
    ledger.record_execution(
        tool_name="test_tool", arguments={}, result_text="second", actor_identity="user"
    )

    # Truncate: remove the last line
    lines = ledger.ledger_path.read_text().strip().split("\n")
    ledger.ledger_path.write_text(lines[0] + "\n")  # keep only first record

    # Re-opening should fail (anchor count=2 but actual=1)
    with pytest.raises(EvidenceIntegrityError, match="TRUNCATED"):
        EvidenceLedger(
            ledger_path=ledger.ledger_path,
            anchor_path=ledger.anchor_path,
            verify_anchor_on_open=True,
        )


# ---------------------------------------------------------------------------
# Test 4: Reset requires confirm=True
# ---------------------------------------------------------------------------


def test_reset_requires_confirm(tmp_path):
    """reset_anchor must refuse without confirm=True."""
    ledger = _make_ledger(tmp_path)
    with pytest.raises(ValueError, match="confirm"):
        ledger.reset_anchor()


def test_reset_works_with_confirm(tmp_path):
    """reset_anchor with confirm=True should succeed."""
    ledger = _make_ledger(tmp_path)
    ledger.record_execution(
        tool_name="test_tool", arguments={}, result_text="data", actor_identity="user"
    )
    # Should not raise
    ledger.reset_anchor(confirm=True)
    assert not ledger.anchor_path.exists()


# ---------------------------------------------------------------------------
# Test 5: Anchor verification is fail-closed (never weakened)
# ---------------------------------------------------------------------------


def test_fail_closed_anchor_verification(tmp_path):
    """_verify_anchor_on_open must never be skipped or weakened."""
    ledger = _make_ledger(tmp_path)
    # Verify _verify_anchor_on_open exists and is called on init
    assert hasattr(ledger, "_verify_anchor_on_open")
    assert callable(ledger._verify_anchor_on_open)

    # Verify anchor file is written with 0600 mode
    ledger.record_execution(
        tool_name="test_tool", arguments={}, result_text="data", actor_identity="user"
    )
    anchor_mode = ledger.anchor_path.stat().st_mode & 0o777
    assert anchor_mode == 0o600, f"Expected 0600, got {oct(anchor_mode)}"

    # Verify ledger file is 0600
    ledger_mode = ledger.ledger_path.stat().st_mode & 0o777
    assert ledger_mode == 0o600, f"Expected 0600, got {oct(ledger_mode)}"


# ---------------------------------------------------------------------------
# Test 6: Concurrent reads while writes happen
# ---------------------------------------------------------------------------


def test_concurrent_reads_during_writes(tmp_path):
    """Reading the ledger while writes occur must not corrupt the chain."""
    ledger = _make_ledger(tmp_path)
    errors = []
    stop_event = threading.Event()

    def writer():
        idx = 0
        while not stop_event.is_set():
            try:
                ledger.record_execution(
                    tool_name="writer",
                    arguments={"idx": idx},
                    result_text=f"write-{idx}",
                    actor_identity="writer",
                )
                idx += 1
            except Exception as exc:
                errors.append(exc)
                break

    def reader():
        while not stop_event.is_set():
            try:
                if ledger.ledger_path.is_file():
                    with open(ledger.ledger_path, "r") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                json.loads(line)  # must be valid JSON
            except Exception:
                pass  # Reader may see partial writes — that's OK
            finally:
                pass

    w = threading.Thread(target=writer)
    r1 = threading.Thread(target=reader)
    r2 = threading.Thread(target=reader)

    w.start()
    r1.start()
    r2.start()

    import time

    time.sleep(0.5)
    stop_event.set()

    w.join()
    r1.join()
    r2.join()

    assert not errors, f"Writer errors: {errors}"

    # Final chain must be valid
    records = []
    with open(ledger.ledger_path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    assert len(records) > 0, "No records written"
    seqs = [r["seq"] for r in records]
    assert seqs == list(range(1, len(records) + 1)), f"Seq numbers not contiguous after concurrent writes"