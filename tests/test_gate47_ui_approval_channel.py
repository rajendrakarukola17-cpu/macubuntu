"""UI-side protocol state tests with local sockets, not GTK or a security boundary."""
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg.approval_broker import ActionSpec, ApprovalBroker, ApprovalProtocolError
from celina_pkg.approval_transport import BrokerUIChannel, UIApprovalChannel, send_message


@contextmanager
def pair():
    host, ui = socket.socketpair()
    host.settimeout(1)
    ui.settimeout(1)
    try:
        yield host, ui
    finally:
        host.close()
        ui.close()


def make_broker(calls):
    action = ActionSpec(validate=lambda a: a, target=lambda _a: "fixture",
                        may_display=lambda _a: True, execute=lambda a: calls.append(a) or "acted",
                        risk="test", scope="fixture")
    return ApprovalBroker({"test": action})


def test_broker_ui_roundtrip_validates_and_binds_full_card():
    calls = []
    broker = make_broker(calls)
    card = broker.create_request("test", {"text": "x" * 900 + "\u202e"}, principal="fixture")
    with pair() as (host, ui), ThreadPoolExecutor(max_workers=1) as pool:
        responder = UIApprovalChannel(ui)
        future = pool.submit(BrokerUIChannel(host).request, broker, card)
        request = responder.receive()
        assert request.card == card
        assert card.payload_hash in request.display_text
        assert card.canonical_bytes.decode("ascii") in request.display_text
        assert "\\u202e" in request.display_text
        assert responder.respond(card.request_id, card.payload_hash, True)
        grant = future.result(timeout=2)
        assert broker.execute(grant, card.canonical_bytes, principal="fixture") == "acted"
        assert calls == [{"text": "x" * 900 + "\u202e"}]
        assert not responder.respond(card.request_id, card.payload_hash, True)


def test_bad_card_rejected_before_any_review():
    with pair() as (host, ui):
        responder = UIApprovalChannel(ui)
        send_message(host, {"kind": "approval_request", "request_id": "a" * 64,
                            "payload_hash": "0" * 64, "payload": "{}"})
        with pytest.raises(ApprovalProtocolError):
            responder.receive()
        assert not responder.respond("a" * 64, "0" * 64, True)
        assert host.recv(1) == b""


def test_mismatched_stale_or_nonboolean_decision_cannot_resolve_pending_request():
    calls = []
    broker = make_broker(calls)
    card = broker.create_request("test", {}, principal="fixture")
    with pair() as (host, ui), ThreadPoolExecutor(max_workers=1) as pool:
        responder = UIApprovalChannel(ui)
        future = pool.submit(BrokerUIChannel(host).request, broker, card)
        req = responder.receive()
        assert not responder.respond("0" * 64, req.card.payload_hash, True)
        assert not responder.respond(req.card.request_id, "0" * 64, True)
        assert not responder.respond(req.card.request_id, req.card.payload_hash, 1)
        assert responder.respond(req.card.request_id, req.card.payload_hash, False)
        assert future.result(timeout=2) is None
        assert not calls


def test_one_pending_request_and_single_decision_under_concurrency():
    calls = []
    broker = make_broker(calls)
    card = broker.create_request("test", {}, principal="fixture")
    with pair() as (host, ui), ThreadPoolExecutor(max_workers=10) as pool:
        responder = UIApprovalChannel(ui)
        future = pool.submit(BrokerUIChannel(host).request, broker, card)
        request = responder.receive()
        with pytest.raises(ApprovalProtocolError):
            responder.receive()
        start = threading.Barrier(8)
        def decide(_i):
            start.wait()
            return responder.respond(request.card.request_id, request.card.payload_hash, True)
        results = list(pool.map(decide, range(8)))
        assert results.count(True) == 1
        grant = future.result(timeout=2)
        assert broker.execute(grant, card.canonical_bytes, principal="fixture") == "acted"
        assert len(calls) == 1


def test_expired_ui_request_is_not_sent_and_channel_closes():
    clock = [10.0]
    card_calls = []
    broker = make_broker(card_calls)
    card = broker.create_request("test", {}, principal="fixture")
    with pair() as (host, ui), ThreadPoolExecutor(max_workers=1) as pool:
        responder = UIApprovalChannel(ui, clock=lambda: clock[0])
        future = pool.submit(BrokerUIChannel(host).request, broker, card)
        request = responder.receive()
        clock[0] += 1.1
        assert not responder.respond(request.card.request_id, request.card.payload_hash, True)
        assert future.result(timeout=2) is None
        assert not card_calls


def test_explicit_close_cancels_pending_without_response():
    calls = []
    broker = make_broker(calls)
    card = broker.create_request("test", {}, principal="fixture")
    with pair() as (host, ui), ThreadPoolExecutor(max_workers=1) as pool:
        responder = UIApprovalChannel(ui)
        future = pool.submit(BrokerUIChannel(host).request, broker, card)
        request = responder.receive()
        responder.close()
        assert not responder.respond(request.card.request_id, request.card.payload_hash, True)
        assert future.result(timeout=2) is None
        assert not calls


def test_delayed_prior_decision_does_not_resolve_new_card():
    calls = []
    broker = make_broker(calls)
    first = broker.create_request("test", {"text": "first"}, principal="fixture")
    second = broker.create_request("test", {"text": "second"}, principal="fixture")
    with pair() as (host, ui), ThreadPoolExecutor(max_workers=1) as pool:
        sender = BrokerUIChannel(host)
        responder = UIApprovalChannel(ui)
        future = pool.submit(sender.request, broker, first)
        responder.receive()
        assert responder.respond(first.request_id, first.payload_hash, False)
        assert future.result(timeout=2) is None
        future = pool.submit(sender.request, broker, second)
        request = responder.receive()
        assert request.card == second
        assert not responder.respond(first.request_id, first.payload_hash, True)
        assert responder.respond(second.request_id, second.payload_hash, False)
        assert future.result(timeout=2) is None
        assert not calls


def test_disconnected_broker_cannot_receive_or_reuse_approval():
    calls = []
    broker = make_broker(calls)
    card = broker.create_request("test", {}, principal="fixture")
    with pair() as (host, ui):
        responder = UIApprovalChannel(ui)
        send_message(host, {"kind": "approval_request", "request_id": card.request_id,
                            "payload_hash": card.payload_hash,
                            "payload": card.canonical_bytes.decode("ascii")})
        responder.receive()
        host.close()
        assert not responder.respond(card.request_id, card.payload_hash, True)
        assert not responder.respond(card.request_id, card.payload_hash, True)
        assert not calls


def test_close_interrupts_blocked_receive():
    with pair() as (_host, ui), ThreadPoolExecutor(max_workers=1) as pool:
        responder = UIApprovalChannel(ui)
        future = pool.submit(responder.receive)
        responder.close()
        with pytest.raises(ApprovalProtocolError):
            future.result(timeout=2)
