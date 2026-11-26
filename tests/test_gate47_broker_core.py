"""Pure broker-state tests; fake dispatch/UI are not host-isolation evidence."""
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg.approval_broker import ActionSpec, ApprovalBroker, ApprovalProtocolError, Grant


def spec(executor):
    return ActionSpec(validate=lambda args: args, target=lambda _: "safe fixture",
                      may_display=lambda _: True, execute=executor, risk="test", scope="fixture")


def broker():
    calls = []
    gate = ApprovalBroker({"demo": spec(lambda args: calls.append(args) or "done")})
    return gate, calls


def request(gate, args=None):
    return gate.create_request("demo", args or {"message": "hello"}, principal="fixture")


def approve(gate, card):
    return gate.accept_ui_response(card.request_id, card.payload_hash, True)


def test_card_is_complete_escaped_and_immutable():
    gate, _ = broker()
    text = "x" * 1000 + "\u202e" + "end"
    card = request(gate, {"message": text})
    assert hashlib.sha256(card.canonical_bytes).hexdigest() == card.payload_hash
    assert len(card.payload_hash) == 64
    assert json.loads(card.canonical_bytes)["arguments"]["message"] == text
    assert b"\\u202e" in card.canonical_bytes
    with pytest.raises(FrozenInstanceError):
        card.payload_hash = "0" * 64


def test_mismatch_and_forgery_never_dispatch():
    gate, calls = broker()
    card = request(gate)
    assert gate.accept_ui_response(card.request_id, "0" * 64, True) is None
    fake = Grant(handle="0" * 64)
    assert gate.execute(fake, card.canonical_bytes, principal="fixture") is None
    assert not calls


def test_bound_args_single_use_and_copied_from_approved_bytes():
    gate, calls = broker()
    args = {"nested": [1, "hello"]}
    card = request(gate, args)
    grant = approve(gate, card)
    args["nested"].append("changed")
    changed = card.canonical_bytes.replace(b'hello', b'world')
    assert gate.execute(grant, changed, principal="fixture") is None
    assert gate.execute(grant, card.canonical_bytes, principal="fixture") is None  # divergence burns grant
    assert not calls
    card = request(gate, {"nested": [1, "hello"]})
    grant = approve(gate, card)
    assert gate.execute(grant, card.canonical_bytes, principal="fixture") == "done"
    assert calls == [{"nested": [1, "hello"]}]
    assert gate.execute(grant, card.canonical_bytes, principal="fixture") is None
    assert len(calls) == 1


def test_different_authenticated_principal_cannot_spend_grant():
    gate, calls = broker()
    card = request(gate)
    grant = approve(gate, card)
    assert gate.execute(grant, card.canonical_bytes, principal="different") is None
    assert gate.execute(grant, card.canonical_bytes, principal="fixture") is None
    assert not calls


def test_concurrent_spend_executes_once():
    gate, calls = broker()
    card = request(gate)
    grant = approve(gate, card)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: gate.execute(grant, card.canonical_bytes, principal="fixture"), range(24)))
    assert results.count("done") == 1
    assert len(calls) == 1


def test_wrong_request_response_and_expiry_fail_closed():
    clock = [100.0]
    calls = []
    gate = ApprovalBroker({"demo": spec(lambda args: calls.append(args))}, clock=lambda: clock[0], ttl=2)
    a, b = request(gate), request(gate, {"message": "other"})
    assert gate.accept_ui_response(a.request_id, b.payload_hash, True) is None
    grant = approve(gate, b)
    clock[0] += 3
    assert gate.execute(grant, b.canonical_bytes, principal="fixture") is None
    assert not calls


def test_pending_response_is_single_use_and_restart_loses_grants():
    gate, calls = broker()
    card = request(gate)
    grant = approve(gate, card)
    assert approve(gate, card) is None
    fresh_gate, fresh_calls = broker()
    assert fresh_gate.execute(grant, card.canonical_bytes, principal="fixture") is None
    assert not calls and not fresh_calls


def test_concurrent_response_can_create_only_one_grant():
    gate, _ = broker()
    card = request(gate)
    with ThreadPoolExecutor(max_workers=8) as pool:
        grants = list(pool.map(lambda _: approve(gate, card), range(24)))
    assert sum(g is not None for g in grants) == 1


def test_rejection_and_response_divergence_are_final():
    gate, calls = broker()
    card = request(gate)
    assert gate.accept_ui_response(card.request_id, card.payload_hash, False) is None
    assert approve(gate, card) is None
    other = request(gate)
    assert gate.accept_ui_response(other.request_id, "0" * 64, True) is None
    assert approve(gate, other) is None
    assert not calls


def test_malformed_unicode_hash_is_rejected_without_dispatch():
    gate, calls = broker()
    card = request(gate)
    assert gate.accept_ui_response(card.request_id, "é" * 64, True) is None
    assert not calls


def test_rejects_unsupported_types_floats_oversize_and_unknown_tools():
    gate, _ = broker()
    for args in ({"x": float("nan")}, {"x": object()}, {"x": "a" * 100_000}, {1: "key"}):
        with pytest.raises(ApprovalProtocolError):
            request(gate, args)
    with pytest.raises(ApprovalProtocolError):
        gate.create_request("unknown", {}, principal="fixture")


def test_only_trusted_registered_action_can_choose_policy_and_validate_args():
    calls = []

    def validate(args):
        if set(args) != {"value"} or type(args["value"]) is not int:
            raise ValueError("schema error")
        return {"value": args["value"] + 1}

    action = ActionSpec(validate=validate, target=lambda _: "resolved by host",
                        may_display=lambda _: True, execute=lambda a: calls.append(a),
                        risk="host-risk", scope="host-scope")
    gate = ApprovalBroker({"demo": action})
    with pytest.raises(ApprovalProtocolError):
        gate.create_request("demo", {"value": 1, "extra": 2}, principal="fixture")
    card = gate.create_request("demo", {"value": 1}, principal="fixture")
    payload = json.loads(card.canonical_bytes)
    assert payload["risk"] == "host-risk" and payload["scope"] == "host-scope"
    assert payload["target"] == "resolved by host" and payload["arguments"] == {"value": 2}
    grant = approve(gate, card)
    gate.execute(grant, card.canonical_bytes, principal="fixture")
    assert calls == [{"value": 2}]


def test_undisplayable_or_invalid_validators_fail_without_dispatch():
    calls = []
    action = ActionSpec(validate=lambda args: args, target=lambda _: "fixture",
                        may_display=lambda _: False, execute=lambda a: calls.append(a),
                        risk="test", scope="fixture")
    gate = ApprovalBroker({"demo": action})
    with pytest.raises(ApprovalProtocolError):
        gate.create_request("demo", {"secret": "test"}, principal="fixture")
    with pytest.raises(ValueError):
        ApprovalBroker({"demo": lambda args: calls.append(args)})
    assert not calls


def test_changed_or_unresolvable_target_burns_grant_before_dispatch():
    target = ["window-one"]
    calls = []

    def resolve(_args):
        if target[0] is None:
            raise RuntimeError("target no longer resolvable")
        return target[0]

    action = ActionSpec(validate=lambda args: args, target=resolve,
                        may_display=lambda _: True, execute=lambda a: calls.append(a),
                        risk="test", scope="fixture")
    gate = ApprovalBroker({"demo": action})
    for replacement in ("window-two", None):
        target[0] = "window-one"
        card = request(gate)
        grant = approve(gate, card)
        target[0] = replacement
        assert gate.execute(grant, card.canonical_bytes, principal="fixture") is None
        target[0] = "window-one"
        assert gate.execute(grant, card.canonical_bytes, principal="fixture") is None
    assert not calls


def test_expiry_during_target_recheck_prevents_dispatch():
    clock = [10.0]
    calls = []
    resolves = []

    def resolve(_args):
        resolves.append(True)
        if len(resolves) > 1:
            clock[0] = 12.0
        return "window-one"

    action = ActionSpec(validate=lambda args: args, target=resolve,
                        may_display=lambda _: True, execute=lambda a: calls.append(a),
                        risk="test", scope="fixture")
    gate = ApprovalBroker({"demo": action}, clock=lambda: clock[0], ttl=2.0)
    card = request(gate)
    grant = approve(gate, card)
    assert gate.execute(grant, card.canonical_bytes, principal="fixture") is None
    assert not calls


def test_target_recheck_cannot_mutate_executed_arguments():
    calls = []
    resolves = []

    def resolve(args):
        resolves.append(True)
        if len(resolves) > 1:
            args["message"] = "changed during recheck"
        return "window-one"

    action = ActionSpec(validate=lambda args: args, target=resolve,
                        may_display=lambda _: True, execute=lambda a: calls.append(a),
                        risk="test", scope="fixture")
    gate = ApprovalBroker({"demo": action})
    card = request(gate)
    grant = approve(gate, card)
    gate.execute(grant, card.canonical_bytes, principal="fixture")
    assert len(resolves) == 2
    assert calls == [{"message": "hello"}]
