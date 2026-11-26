# Copyright 2026 Celina contributors.
"""Evidence bridge safety and isolation tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from celina_pkg.evidence import EvidenceIntegrityError, EvidenceLedger
from celina_pkg.selfimprove.contracts import VerifierResult
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.ledger_bridge import LedgerBridge


def make_ledger(tmp_path: Path) -> EvidenceLedger:
    """Create a ledger whose data and anchor are explicitly in the fixture."""
    return EvidenceLedger(tmp_path / "ledger.jsonl", anchor_path=tmp_path / "anchor.json")


def test_import_has_no_home_io(tmp_path: Path) -> None:
    """Bridge module import does not import the stateful protected core."""
    root = Path(__file__).resolve().parents[2]
    env = {
        "HOME": str(tmp_path),
        "PYTHONPATH": str(root / "features" / "celina"),
        "PATH": os.defpath,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import celina_pkg.selfimprove.ledger_bridge",
        ],  # Fixed interpreter and static code.
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_structured_events_are_hash_chained(tmp_path: Path) -> None:
    """Events and contract data are stored on the explicit evidence ledger."""
    ledger = make_ledger(tmp_path)
    bridge = LedgerBridge(ledger)
    bridge.record("selfimprove.verifier.run", VerifierResult("pytest", "pass", 1.0, "ok"))
    bridge.record("selfimprove.gate.decision", {"passed": True})
    ok, _, count = ledger.verify()
    assert ok
    assert count == len(ledger.list_entries())
    assert count > 1
    assert [entry["event_type"] for entry in ledger.list_entries()] == [
        "selfimprove.verifier.run",
        "selfimprove.gate.decision",
    ]


def test_missing_anchor_and_tampering_fail_closed(tmp_path: Path) -> None:
    """Recording never recreates a missing anchor or appends to a damaged chain."""
    ledger = make_ledger(tmp_path)
    bridge = LedgerBridge(ledger)
    bridge.record("selfimprove.verifier.run", {"status": "ok"})
    before = ledger.ledger_path.read_bytes()
    ledger.anchor_path.unlink()
    with pytest.raises(EvidenceIntegrityError):
        bridge.record("selfimprove.verifier.run", {"status": "bad"})
    assert ledger.ledger_path.read_bytes() == before
    assert not ledger.anchor_path.exists()
    with pytest.raises(EvidenceIntegrityError):
        make_ledger(tmp_path)


def test_payload_redaction_and_truncation(tmp_path: Path) -> None:
    """Secret-shaped data is redacted before deterministic preview truncation."""
    ledger = make_ledger(tmp_path)
    bridge = LedgerBridge(ledger)
    secret = "sample" + "-value-123456789"
    text = "password=" + secret + " " + "X" * 700
    bridge.record("selfimprove.verifier.run", {"api_key": secret, "output": text})
    raw = ledger.ledger_path.read_text(encoding="utf-8")
    assert secret not in raw
    preview = ledger.list_entries()[0]["result_preview"]
    assert "[TRUNCATED]" in preview
    assert len(preview) <= 240  # noqa: PLR2004  # EvidenceLedger preview limit.


def test_flags_and_event_validation(tmp_path: Path) -> None:
    """A disabled master records nothing, and non-self-improve names are rejected."""
    ledger = make_ledger(tmp_path)
    bridge = LedgerBridge(ledger)
    assert bridge.record("selfimprove.run", {}, flags=SelfImproveFlags()) is None
    assert not ledger.ledger_path.exists()
    with pytest.raises(ValueError, match="event"):
        bridge.record("outside.event", {})
    bridge.record("selfimprove.run", {}, flags=SelfImproveFlags(enabled=True))
    assert ledger.verify()[2] == 1


@pytest.mark.parametrize("prefix", ["Bearer ", "github_pat_", "sk-or-v1-"])
def test_redacts_recognizable_tokens(tmp_path: Path, prefix: str) -> None:
    """Token-like values cannot enter evidence previews."""
    ledger = make_ledger(tmp_path)
    bridge = LedgerBridge(ledger)
    value = prefix + "z" * 32
    bridge.record("selfimprove.run", {"output": value})
    assert value not in ledger.ledger_path.read_text(encoding="utf-8")


def test_redaction_happens_before_ledger_boundary() -> None:
    """The injected sink receives only sanitized payload text."""
    from celina_pkg.selfimprove.ledger_bridge import _preview  # noqa: PLC0415  # Verify preprocessing directly.

    value = "Bearer " + "z" * 32
    assert value not in _preview({"output": value, "token": value})
    assert "[REDACTED]" in _preview({"output": value, "token": value})


def test_nested_payloads_and_unsupported_objects(tmp_path: Path) -> None:
    """Nested sequences serialize; unsupported objects fail without a ledger write."""
    ledger = make_ledger(tmp_path)
    bridge = LedgerBridge(ledger)
    bridge.record("selfimprove.run", {"values": [1, None, True]})
    assert ledger.verify()[2] == 1
    before = ledger.ledger_path.read_bytes()
    with pytest.raises(ValueError, match="unsupported audit payload"):
        bridge.record("selfimprove.run", {"value": object()})
    assert ledger.ledger_path.read_bytes() == before
