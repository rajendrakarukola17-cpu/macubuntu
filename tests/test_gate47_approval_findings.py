"""Offline approval checks and explicit demonstrations of unresolved trust gaps."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import approval


def test_token_checks_execution_arguments_and_is_single_use():
    seen = []
    args = {"text": "hello"}
    token, error = approval.request_bound_approval("type_text", args, lambda title, detail: seen.append((title, detail)) or True)
    assert error is None
    assert token.arguments_hash == approval.compute_arguments_hash("type_text", args)
    assert token.arguments_hash[:16] in seen[0][1]
    assert token.arguments_hash not in seen[0][1]
    assert not approval.verify_and_consume(token, "type_text", {"text": "changed"})[0]
    assert approval.verify_and_consume(token, "type_text", args)[0]
    assert not approval.verify_and_consume(token, "type_text", args)[0]


def test_card_preview_can_omit_argument_content_and_hash():
    details = []
    args = {"text": "x" * 500}
    token, error = approval.request_bound_approval("type_text", args, lambda title, detail: details.append(detail) or True,
                                                   extra_warning="warning " * 100)
    assert error is None
    assert token.arguments_hash[:16] not in details[0][:500]
    assert "x" * 500 not in details[0]


def test_directly_constructed_unsigned_token_is_spendable_without_callback():
    args = {"text": "forged"}
    token = approval.ApprovalToken(tool="type_text", target="desktop",
        arguments_hash=approval.compute_arguments_hash("type_text", args),
        risk=approval.RiskLevel.SECURITY_SENSITIVE,
        expires_at=time.time() + 60, expires_at_monotonic=time.monotonic() + 60)
    assert approval.verify_and_consume(token, "type_text", args)[0]
