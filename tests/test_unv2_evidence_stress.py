"""UNV-2: EvidenceLedger 100+ concurrent writer stress & fail-closed tamper tests."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.evidence import EvidenceIntegrityError, EvidenceLedger  # noqa: E402


def test_evidence_ledger_100_concurrent_writers_and_tamper_fail_closed(tmp_path: Path) -> None:
    """Verify UNV-2: 120 concurrent writers keep valid hash chain and fail closed on tampering."""
    ledger_path = tmp_path / "stress_evidence.jsonl"
    anchor_path = tmp_path / "stress_evidence.anchor.json"
    ledger = EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )

    n_threads = 120
    records_per_thread = 3
    errors: list[BaseException] = []
    barrier = threading.Barrier(n_threads)

    def _worker(tid: int) -> None:
        try:
            barrier.wait(timeout=15.0)
            for idx in range(records_per_thread):
                ledger.record_execution(
                    tool_name="read_file",
                    arguments={"tid": tid, "idx": idx},
                    result_text=f"ok:{tid}:{idx}",
                    actor_identity=f"thread-{tid}",
                )
        except BaseException as exc:  # pragma: no cover - captured for assertion
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
        assert not t.is_alive(), "Worker thread timed out under contention"

    assert not errors, f"Concurrent writer errors: {errors!r}"
    valid, reason, count = ledger.verify()
    assert valid, f"Hash chain invalid after 120-thread contention: {reason}"
    assert count == n_threads * records_per_thread

    # Re-open with fail-closed anchor verification -> must pass
    reopened = EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )
    assert reopened.verify()[0]

    # Tamper one middle record payload -> full chain verify() MUST detect tampering
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    mid = len(lines) // 2
    rec = json.loads(lines[mid])
    rec["result_summary"] = "tampered"
    lines[mid] = json.dumps(rec, sort_keys=True)
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    tampered_open = EvidenceLedger(
        ledger_path=ledger_path,
        anchor_path=anchor_path,
        verify_anchor_on_open=True,
    )
    with pytest.raises(EvidenceIntegrityError, match="entry_hash mismatch"):
        tampered_open.verify()

    # Tamper tip entry_hash -> opening MUST fail closed with EvidenceIntegrityError
    tip = json.loads(lines[-1])
    tip["entry_hash"] = "0" * 64
    lines[-1] = json.dumps(tip, sort_keys=True, separators=(",", ":"))
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceIntegrityError):
        EvidenceLedger(
            ledger_path=ledger_path,
            anchor_path=anchor_path,
            verify_anchor_on_open=True,
        )
