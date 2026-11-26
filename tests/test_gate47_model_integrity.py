"""Signed-manifest and model loading tests with ephemeral test keys and no models."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from celina_pkg import model_integrity


@pytest.fixture
def signed_manifest(tmp_path, monkeypatch):
    """Create an Ed25519 fixture key; it is never used as a deployment root."""
    private = tmp_path / "test-private.pem"
    public = tmp_path / "test-public.pem"
    manifest = tmp_path / "model-digests.json"
    signature = tmp_path / "model-digests.sig"
    subprocess.run(["/usr/bin/openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True, capture_output=True)
    subprocess.run(["/usr/bin/openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True, capture_output=True)
    monkeypatch.setattr(model_integrity, "MODEL_MANIFEST", manifest)
    monkeypatch.setattr(model_integrity, "MODEL_SIGNATURE", signature)
    monkeypatch.setattr(model_integrity, "PINNED_MODEL_PUBLIC_KEY", public.read_bytes())
    def sign(entries):
        manifest.write_text(json.dumps(entries, sort_keys=True))
        subprocess.run(["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey", str(private), "-rawin", "-in", str(manifest), "-out", str(signature)], check=True, capture_output=True)
    return sign, manifest, signature


def test_empty_signed_mapping_raises(signed_manifest, tmp_path):
    sign, _, _ = signed_manifest
    sign({})
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    with pytest.raises(model_integrity.ModelIntegrityError):
        model_integrity.verify_model_hash(model, lock_id="smollm-135m-q4")


def test_environment_cannot_supply_unlisted_digest(signed_manifest, tmp_path, monkeypatch):
    sign, _, _ = signed_manifest
    sign({})
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setenv("CELINA_SMOLLM_135M_SHA256", hashlib.sha256(b"fixture").hexdigest())
    with pytest.raises(model_integrity.ModelIntegrityError):
        model_integrity.verify_model_hash(model, lock_id="smollm-135m-q4", env_var="CELINA_SMOLLM_135M_SHA256")


def test_valid_corrupt_signature_mismatch_and_symlink(signed_manifest, tmp_path):
    sign, manifest, _ = signed_manifest
    data = b"fixture"
    digest = hashlib.sha256(data).hexdigest()
    sign({"smollm-135m-q4": digest})
    model = tmp_path / "model"
    model.write_bytes(data)
    assert model_integrity.verify_model_hash(model, lock_id="smollm-135m-q4") is True
    with pytest.raises(model_integrity.ModelIntegrityError):
        model_integrity.verify_model_hash(model, lock_id="smollm-135m-q4", expected_sha256="0" * 64)
    link = tmp_path / "link"
    link.symlink_to(model)
    with pytest.raises(model_integrity.ModelIntegrityError):
        model_integrity.verify_model_hash(link, lock_id="smollm-135m-q4")
    model.write_bytes(b"corrupt")
    with pytest.raises(model_integrity.ModelIntegrityError):
        model_integrity.verify_model_hash(model, lock_id="smollm-135m-q4")
    model.write_bytes(data)
    manifest.write_text(json.dumps({"smollm-135m-q4": "0" * 64}))
    with pytest.raises(model_integrity.ModelIntegrityError):
        model_integrity.verify_model_hash(model, lock_id="smollm-135m-q4")


@pytest.mark.parametrize("backend,module", [("fasttext", "fasttext"), ("smollm", "ctransformers")])
def test_absent_pin_raises_before_any_model_loader(tmp_path, monkeypatch, signed_manifest, backend, module):
    from celina_pkg import gear0_5_micro
    signed_manifest[0]({})
    path = tmp_path / "model"
    path.write_bytes(b"fixture")
    calls = []
    def load(*args, **kwargs):
        calls.append(True)
        return object()
    fake = SimpleNamespace(load_model=load, AutoModelForCausalLM=SimpleNamespace(from_pretrained=load))
    monkeypatch.setitem(sys.modules, module, fake)
    with pytest.raises(model_integrity.ModelIntegrityError):
        gear0_5_micro.Gear05MicroRouter(backend=backend, model_path=path).initialize()
    assert not calls


def test_unprovisioned_production_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(model_integrity, "PINNED_MODEL_PUBLIC_KEY", None)
    model = tmp_path / "weights"
    model.write_bytes(b"fixture")
    with pytest.raises(model_integrity.ModelIntegrityError, match="not provisioned"):
        model_integrity.verify_model_hash(model, lock_id="smollm-135m-q4")


def test_signed_digest_env_only_narrows(signed_manifest, tmp_path, monkeypatch):
    sign, _, _ = signed_manifest
    data = b"fixture"
    digest = hashlib.sha256(data).hexdigest()
    sign({"smollm-135m-q4": digest})
    path = tmp_path / "model"
    path.write_bytes(data)
    monkeypatch.setenv("CELINA_SMOLLM_135M_SHA256", digest)
    assert model_integrity.verify_model_hash(path, lock_id="smollm-135m-q4", env_var="CELINA_SMOLLM_135M_SHA256")
    monkeypatch.setenv("CELINA_SMOLLM_135M_SHA256", "0" * 64)
    with pytest.raises(model_integrity.ModelIntegrityError, match="restriction"):
        model_integrity.verify_model_hash(path, lock_id="smollm-135m-q4", env_var="CELINA_SMOLLM_135M_SHA256")


@pytest.mark.parametrize("backend,module,lock_id", [("fasttext", "fasttext", "intent-fasttext"), ("smollm", "ctransformers", "smollm-135m-q4")])
def test_signed_matching_digest_reaches_only_fake_loader(tmp_path, monkeypatch, signed_manifest, backend, module, lock_id):
    from celina_pkg import gear0_5_micro
    data = b"fixture"
    signed_manifest[0]({lock_id: hashlib.sha256(data).hexdigest()})
    path = tmp_path / "model"
    path.write_bytes(data)
    calls = []
    def load(*args, **kwargs):
        calls.append(True)
        return object()
    fake = SimpleNamespace(load_model=load, AutoModelForCausalLM=SimpleNamespace(from_pretrained=load))
    monkeypatch.setitem(sys.modules, module, fake)
    assert gear0_5_micro.Gear05MicroRouter(backend=backend, model_path=path).initialize()
    assert calls == [True]


def test_smollm_runner_requires_signed_pin_before_verify(tmp_path, signed_manifest):
    from celina_pkg import gear0_5_smolLM
    path = tmp_path / "model"
    path.write_bytes(b"fixture")
    runner = object.__new__(gear0_5_smolLM.SmolLMRunner)
    signed_manifest[0]({})
    with pytest.raises(model_integrity.ModelIntegrityError):
        runner._verify_model_hash(path)
    signed_manifest[0]({"smollm-135m-q4": hashlib.sha256(b"fixture").hexdigest()})
    assert runner._verify_model_hash(path)
