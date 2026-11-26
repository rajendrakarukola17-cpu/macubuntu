"""
tests/acceptance/test_evidence_ledger_fail_closed.py

Phase 9 A3 — Evidence ledger fail-closed acceptance tests.

Tests that evidence ledger deletion, truncation, and tampering each
fail closed. Reset requires explicit confirmation.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "features" / "celina"))

from celina_pkg import evidence, config
from celina_pkg.evidence import EvidenceIntegrityError
import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Evidence ledger deleted → fail closed (constructor raises)
# ---------------------------------------------------------------------------

def test_ledger_deleted_constructor_raises(tmp_path, monkeypatch):
    """When the evidence ledger is deleted but anchor expects entries, constructor raises EvidenceIntegrityError."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DRY_RUN", "1")

    ledger_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.jsonl"
    anchor_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.anchor.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    # Create a ledger first to establish an anchor
    ledger = evidence.EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )
    ledger.record_security_event(event_type="test_event", detail="original")
    # Close to flush anchor
    if hasattr(ledger, 'close'):
        ledger.close()

    # Delete the ledger file but keep the anchor
    ledger_path.unlink()

    # Re-opening should fail closed (EvidenceIntegrityError)
    with pytest.raises(EvidenceIntegrityError) as exc_info:
        evidence.EvidenceLedger(
            ledger_path=ledger_path,
            anchor_path=anchor_path,
            verify_anchor_on_open=True,
        )
    assert "DELETED" in str(exc_info.value) or "deleted" in str(exc_info.value).upper()


# ---------------------------------------------------------------------------
# Evidence ledger truncated → fail closed
# ---------------------------------------------------------------------------

def test_ledger_truncated_verify_chain_fails(tmp_path, monkeypatch):
    """When the evidence ledger is truncated, verify_chain fails closed."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DRY_RUN", "1")

    ledger_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.jsonl"
    anchor_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.anchor.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    ledger = evidence.EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )
    ledger.record_security_event(event_type="event1", detail="a")
    ledger.record_security_event(event_type="event2", detail="b")

    # Truncate: remove the last line from the ledger
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    if len(lines) > 1:
        ledger_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    # verify_chain should detect truncation
    ok, msg, count = ledger.verify_chain()
    assert ok is False, f"Truncated ledger should fail closed: {msg}"
    assert "truncat" in msg.lower()


# ---------------------------------------------------------------------------
# Evidence ledger tampered → fail closed
# ---------------------------------------------------------------------------

def test_ledger_tampered_verify_chain_fails(tmp_path, monkeypatch):
    """When the evidence ledger is tampered, verify_chain fails closed."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DRY_RUN", "1")

    ledger_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.jsonl"
    anchor_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.anchor.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    ledger = evidence.EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )
    ledger.record_security_event(event_type="test_event", detail="original_value")

    # Tamper: modify the content in the ledger file
    content = ledger_path.read_text(encoding="utf-8")
    tampered = content.replace("original_value", "TAMPERED_BY_ATTACKER")
    ledger_path.write_text(tampered, encoding="utf-8")

    # verify_chain should detect tampering (entry_hash mismatch)
    ok, msg, count = ledger.verify_chain()
    assert ok is False, f"Tampered ledger should fail closed: {msg}"
    assert "tamper" in msg.lower() or "mismatch" in msg.lower()


# ---------------------------------------------------------------------------
# Reset requires confirm
# ---------------------------------------------------------------------------

def test_ledger_reset_without_confirm_fails(tmp_path, monkeypatch):
    """reset_anchor without confirm should fail."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DRY_RUN", "1")

    ledger_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.jsonl"
    anchor_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.anchor.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    ledger = evidence.EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )
    ledger.record_security_event(event_type="test_event", detail="test")

    # Reset without confirm should raise
    with pytest.raises((ValueError, TypeError, AssertionError)):
        ledger.reset_anchor(confirm=False)


def test_ledger_reset_with_confirm_succeeds(tmp_path, monkeypatch):
    """reset_anchor with confirm=True should succeed."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DRY_RUN", "1")

    ledger_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.jsonl"
    anchor_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.anchor.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    ledger = evidence.EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )
    ledger.record_security_event(event_type="test_event", detail="test")

    # Reset with confirm should succeed
    ledger.reset_anchor(confirm=True)
    # Reset cannot silently trust existing ledger bytes without an anchor.
    with pytest.raises(EvidenceIntegrityError, match="ANCHOR MISSING"):
        evidence.EvidenceLedger(
            ledger_path=ledger_path,
            anchor_path=anchor_path,
            verify_anchor_on_open=True,
        )


# ---------------------------------------------------------------------------
# Anchor verification on open
# ---------------------------------------------------------------------------

def test_anchor_verify_on_open_detects_corrupt_anchor(tmp_path, monkeypatch):
    """_verify_anchor_on_open detects when the anchor file itself is corrupted."""
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DRY_RUN", "1")

    ledger_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.jsonl"
    anchor_path = tmp_path / ".local" / "share" / "celina" / "evidence" / "ledger.anchor.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    # Write a corrupt anchor file
    anchor_path.write_text("{not valid json", encoding="utf-8")
    ledger_path.write_text("{}", encoding="utf-8")

    # Opening should fail closed
    with pytest.raises(EvidenceIntegrityError):
        evidence.EvidenceLedger(
            ledger_path=ledger_path,
            anchor_path=anchor_path,
            verify_anchor_on_open=True,
        )

@pytest.mark.parametrize("state", ["absent", "empty"])
def test_unanchored_brand_new_ledger_is_usable(tmp_path, state):
    """A fresh absent or empty ledger can be opened, verified and initialized."""
    ledger_path = tmp_path / "ledger.jsonl"
    anchor_path = tmp_path / "anchor.json"
    if state == "empty":
        ledger_path.write_text("", encoding="utf-8")
    ledger = evidence.EvidenceLedger(ledger_path, anchor_path=anchor_path)
    assert ledger.verify_chain()[0] is True
    assert ledger.verify()[0] is True
    ledger.record_security_event(event_type="test.fresh", detail="synthetic")
    assert anchor_path.is_file()
    assert ledger.verify()[0] is True


def test_missing_anchor_fails_open_verify_and_append(tmp_path):
    """A deleted anchor cannot be silently recreated for a non-empty ledger."""
    ledger_path = tmp_path / "ledger.jsonl"
    anchor_path = tmp_path / "anchor.json"
    ledger = evidence.EvidenceLedger(ledger_path, anchor_path=anchor_path)
    ledger.record_security_event(event_type="test.seed", detail="synthetic")
    original = ledger_path.read_bytes()
    anchor_path.unlink()

    with pytest.raises(EvidenceIntegrityError, match="ANCHOR MISSING"):
        evidence.EvidenceLedger(ledger_path, anchor_path=anchor_path)
    ok, _, _ = ledger.verify_chain()
    assert ok is False
    with pytest.raises(EvidenceIntegrityError, match="ANCHOR MISSING"):
        ledger.verify()
    with pytest.raises(EvidenceIntegrityError, match="ANCHOR MISSING"):
        ledger.record_security_event(event_type="test.next", detail="synthetic")
    assert ledger_path.read_bytes() == original
    assert not anchor_path.exists()


@pytest.mark.parametrize("damage", ["deleted", "truncated"])
def test_anchored_ledger_loss_fails_open_verify_and_append(tmp_path, damage):
    """An existing anchor prevents deletion or truncation from being accepted."""
    ledger_path = tmp_path / "ledger.jsonl"
    anchor_path = tmp_path / "anchor.json"
    ledger = evidence.EvidenceLedger(ledger_path, anchor_path=anchor_path)
    ledger.record_security_event(event_type="test.one", detail="synthetic")
    ledger.record_security_event(event_type="test.two", detail="synthetic")
    original_anchor = anchor_path.read_bytes()
    if damage == "deleted":
        ledger_path.unlink()
    else:
        ledger_path.write_text(ledger_path.read_text(encoding="utf-8").splitlines()[0] + "\n")
    damaged = ledger_path.read_bytes() if ledger_path.exists() else None

    with pytest.raises(EvidenceIntegrityError):
        evidence.EvidenceLedger(ledger_path, anchor_path=anchor_path)
    ok, _, _ = ledger.verify_chain()
    assert ok is False
    with pytest.raises(EvidenceIntegrityError):
        ledger.verify()
    with pytest.raises(EvidenceIntegrityError):
        ledger.record_security_event(event_type="test.next", detail="synthetic")
    assert (ledger_path.read_bytes() if ledger_path.exists() else None) == damaged
    assert anchor_path.read_bytes() == original_anchor
