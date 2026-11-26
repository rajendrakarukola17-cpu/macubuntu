"""Approval wire and display-validation logic; no GTK rendering or actuator use."""
import hashlib
import socket
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg.approval_broker import ActionSpec, ApprovalBroker, ApprovalCard, ApprovalProtocolError
from celina_pkg.approval_transport import (
    BrokerUIChannel, card_from_message, read_message, send_message, validate_card_for_display,
)


@contextmanager
def socket_pair():
    a, b = socket.socketpair()
    try:
        yield a, b
    finally:
        a.close()
        b.close()


def spec(executor):
    return ActionSpec(validate=lambda args: args, target=lambda _: "fixture",
                      may_display=lambda _: True, execute=executor, risk="test", scope="fixture")


def valid_card():
    broker = ApprovalBroker({"test": spec(lambda _: None)})
    return broker.create_request("test", {"text": "x" * 900 + "\u202e"}, principal="unit-test")


def test_card_text_has_full_bytes_and_hash_not_a_preview():
    card = valid_card()
    display = validate_card_for_display(card)
    assert card.canonical_bytes.decode("ascii") in display
    assert card.payload_hash in display
    assert "x" * 900 in display
    assert "\\u202e" in display


def test_corrupt_payload_or_hash_or_request_id_is_rejected():
    card = valid_card()
    for bad in (
        ApprovalCard(card.request_id, card.canonical_bytes + b" ", card.payload_hash),
        ApprovalCard(card.request_id, card.canonical_bytes, "0" * 64),
        ApprovalCard("not-an-id", card.canonical_bytes, card.payload_hash),
    ):
        with pytest.raises(ApprovalProtocolError):
            validate_card_for_display(bad)


def test_duplicate_and_noncanonical_json_rejected_even_if_hash_matches():
    card = valid_card()
    for raw in (b'{"arguments":{},"arguments":{},"risk":"test","scope":"x","target":"x","tool":"test","version":1}',
                b'{ "arguments": {}, "risk":"test","scope":"x","target":"x","tool":"test","version":1}'):
        bad = ApprovalCard(card.request_id, raw, hashlib.sha256(raw).hexdigest())
        with pytest.raises(ApprovalProtocolError):
            validate_card_for_display(bad)


def test_bounded_socket_messages_round_trip_without_gui():
    with socket_pair() as (a, b):
        a.settimeout(1)
        b.settimeout(1)
        send_message(a, {"request_id": "a" * 64, "decision": False})
        assert read_message(b) == {"request_id": "a" * 64, "decision": False}
        with pytest.raises(ApprovalProtocolError):
            send_message(a, {"payload": "a" * 100_000})


def test_duplicate_message_keys_and_truncated_frame_rejected():
    with socket_pair() as (a, b):
        b.settimeout(1)
        raw = b'{"decision":true,"decision":false}'
        a.sendall(len(raw).to_bytes(4, "big") + raw)
        with pytest.raises(ApprovalProtocolError):
            read_message(b)
    with socket_pair() as (a, b):
        b.settimeout(1)
        a.sendall((10).to_bytes(4, "big") + b'{}')
        a.shutdown(socket.SHUT_WR)
        with pytest.raises(ApprovalProtocolError):
            read_message(b)


def test_private_channel_roundtrip_binds_response_and_broker_dispatch():
    calls = []
    broker = ApprovalBroker({"test": spec(lambda args: calls.append(args) or "acted")})
    card = broker.create_request("test", {"text": "x" * 800}, principal="trusted-fixture")
    with socket_pair() as (host, ui), ThreadPoolExecutor(max_workers=1) as pool:
        host.settimeout(1)
        ui.settimeout(1)
        channel = BrokerUIChannel(host)

        def ui_logic():
            shown = card_from_message(read_message(ui))
            assert shown == card
            assert shown.canonical_bytes.decode("ascii") in validate_card_for_display(shown)
            send_message(ui, {"request_id": shown.request_id,
                              "payload_hash": shown.payload_hash, "decision": True})

        future = pool.submit(ui_logic)
        grant = channel.request(broker, card)
        future.result(timeout=2)
        assert broker.execute(grant, card.canonical_bytes, principal="trusted-fixture") == "acted"
        assert calls == [{"text": "x" * 800}]
        assert broker.execute(grant, card.canonical_bytes, principal="trusted-fixture") is None


def test_wrong_response_does_not_approve_or_invoke_action():
    calls = []
    broker = ApprovalBroker({"test": spec(lambda args: calls.append(args))})
    card = broker.create_request("test", {}, principal="trusted-fixture")
    with socket_pair() as (host, ui), ThreadPoolExecutor(max_workers=1) as pool:
        host.settimeout(1)
        ui.settimeout(1)
        channel = BrokerUIChannel(host)

        def ui_logic():
            shown = card_from_message(read_message(ui))
            send_message(ui, {"request_id": shown.request_id,
                              "payload_hash": "0" * 64, "decision": True})

        future = pool.submit(ui_logic)
        assert channel.request(broker, card) is None
        future.result(timeout=2)
        assert broker.accept_ui_response(card.request_id, card.payload_hash, True) is None
        assert not calls
