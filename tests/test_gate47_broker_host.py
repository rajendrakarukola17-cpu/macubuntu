"""Tests for celina_pkg.broker_host and tools/provision-model-manifest.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.approval_broker import ActionSpec  # noqa: E402
from celina_pkg.broker_host import BrokerHost, create_ui_socketpair  # noqa: E402


def _load_provision_tool():
    spec = importlib.util.spec_from_file_location(
        "provision_model_manifest",
        REPO_ROOT / "tools" / "provision-model-manifest.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_broker_host_socketpair_approve_and_deny() -> None:
    executed: list[dict] = []

    def _run_ui(ui_chan, *, decision: bool, tamper_hash: bool = False) -> None:
        req = ui_chan.receive()
        assert "Full request SHA-256:" in req.display_text
        h = ("0" * 64) if tamper_hash else req.card.payload_hash
        ui_chan.respond(req.card.request_id, h, decision)

    actions = {
        "write_note": ActionSpec(
            validate=lambda args: {"path": str(args["path"])},
            target=lambda args: args["path"],
            may_display=lambda _args: True,
            execute=lambda args: executed.append(args) or "ok",
            risk="local_mutation",
            scope="notes",
        ),
    }

    # 1. Approved -> executes exactly once with validated args
    broker_ui, ui_chan = create_ui_socketpair(timeout=5.0)
    host = BrokerHost(actions, broker_ui, ttl=10.0)
    t = threading.Thread(target=_run_ui, args=(ui_chan,), kwargs={"decision": True}, daemon=True)
    t.start()
    res_ok = host.request_and_execute("write_note", {"path": "/tmp/note.txt"}, principal="uid:1000")
    t.join(timeout=5.0)
    assert res_ok.approved is True and res_ok.executed is True and res_ok.output == "ok"
    assert executed == [{"path": "/tmp/note.txt"}]

    # 2. Denied -> never invokes actuator
    executed.clear()
    broker_ui2, ui_chan2 = create_ui_socketpair(timeout=5.0)
    host2 = BrokerHost(actions, broker_ui2, ttl=10.0)
    t2 = threading.Thread(target=_run_ui, args=(ui_chan2,), kwargs={"decision": False}, daemon=True)
    t2.start()
    res_deny = host2.request_and_execute("write_note", {"path": "/tmp/note.txt"}, principal="uid:1000")
    t2.join(timeout=5.0)
    assert res_deny.approved is False and res_deny.executed is False
    assert executed == []

    # 3. Mismatched hash from UI -> fail-closed
    broker_ui3, ui_chan3 = create_ui_socketpair(timeout=5.0)
    host3 = BrokerHost(actions, broker_ui3, ttl=10.0)
    t3 = threading.Thread(target=_run_ui, args=(ui_chan3,), kwargs={"decision": True, "tamper_hash": True}, daemon=True)
    t3.start()
    res_bad = host3.request_and_execute("write_note", {"path": "/tmp/note.txt"}, principal="uid:1000")
    t3.join(timeout=5.0)
    assert res_bad.approved is False and res_bad.executed is False
    assert executed == []


def test_provision_model_manifest_build_sign_verify(tmp_path: Path) -> None:
    mod = _load_provision_tool()
    models = tmp_path / "models"
    models.mkdir()
    (models / "smollm.gguf").write_bytes(b"synthetic-gguf-payload")
    (models / "ignore.txt").write_text("not a model", encoding="utf-8")

    priv = tmp_path / "priv.pem"
    pub = tmp_path / "pub.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(priv)], check=True)
    subprocess.run(["openssl", "pkey", "-in", str(priv), "-pubout", "-out", str(pub)], check=True)

    manifest = tmp_path / "model-digests.json"
    sig = tmp_path / "model-digests.sig"
    assert mod.main(["build", "--models-dir", str(models), "--out", str(manifest)]) == 0
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert list(data.keys()) == ["smollm.gguf"]

    assert mod.main(["sign", "--manifest", str(manifest), "--sig", str(sig), "--private-key", str(priv)]) == 0
    assert mod.main(["verify", "--manifest", str(manifest), "--sig", str(sig), "--public-key", str(pub)]) == 0

    # Tamper manifest -> verify fails closed
    manifest.write_text('{"smollm.gguf": "0"}\n', encoding="utf-8")
    assert mod.main(["verify", "--manifest", str(manifest), "--sig", str(sig), "--public-key", str(pub)]) == 1
