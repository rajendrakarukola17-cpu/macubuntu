"""Fail-closed signed model-digest verification.

The deployment signing key has not been provisioned: the default key is None,
so no production model is accepted. A test key is not a release trust root.
This protects digest selection only; same-UID code modification and races
between verification and a third-party path-based loader remain out of scope.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_MANIFEST = REPO_ROOT / "system" / "model-digests.json"
MODEL_SIGNATURE = REPO_ROOT / "system" / "model-digests.sig"
# Deployment must pin a public key supplied by an independently trusted signer.
# None deliberately disables model loading until that happens.
PINNED_MODEL_PUBLIC_KEY: bytes | None = None
_OPENSSL = "/usr/bin/openssl"
_ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")


class ModelIntegrityError(RuntimeError):
    """A model, signed digest, or configured trust root failed verification."""


def _read_regular(path: Path, limit: int | None = None) -> bytes:
    """Read without following a final symlink; reject symlinked ancestors."""
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ModelIntegrityError("symlinked path rejected")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ModelIntegrityError("model input must be a regular file")
            data = stream.read() if limit is None else stream.read(limit + 1)
            if limit is not None and len(data) > limit:
                raise ModelIntegrityError("signed input exceeds size limit")
            return data
    except OSError as exc:
        raise ModelIntegrityError("cannot read model input") from exc


def _valid_key(key: bytes | None) -> bool:
    if key is None:
        return False
    try:
        lines = key.strip().splitlines()
        if lines[0] != b"-----BEGIN PUBLIC KEY-----" or lines[-1] != b"-----END PUBLIC KEY-----":
            return False
        der = base64.b64decode(b"".join(lines[1:-1]), validate=True)
        return len(der) == len(_ED25519_SPKI) + 32 and der.startswith(_ED25519_SPKI)
    except (ValueError, IndexError):
        return False


def _verify_signature(payload: bytes, signature: bytes, key: bytes) -> None:
    """Use the updater's OpenSSL Ed25519 pkeyutl -verify -rawin mechanism."""
    descriptors: list[int] = []
    try:
        for data in (payload, signature, key):
            fd = os.memfd_create("celina-model-integrity", os.MFD_CLOEXEC)
            descriptors.append(fd)
            with os.fdopen(os.dup(fd), "wb") as stream:
                stream.write(data)
            os.lseek(fd, 0, os.SEEK_SET)
        paths = [f"/proc/self/fd/{fd}" for fd in descriptors]
        result = subprocess.run(
            [_OPENSSL, "pkeyutl", "-verify", "-pubin", "-inkey", paths[2],
             "-rawin", "-in", paths[0], "-sigfile", paths[1]],
            pass_fds=tuple(descriptors), capture_output=True, timeout=10,
            env={"PATH": "/usr/bin:/bin", "LANG": "C"}, check=False,
        )
        if result.returncode:
            raise ModelIntegrityError("invalid manifest signature")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ModelIntegrityError("signature verifier unavailable") from exc
    finally:
        for fd in descriptors:
            os.close(fd)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ModelIntegrityError("duplicate manifest entry")
        result[key] = value
    return result


def _signed_digests() -> dict[str, object]:
    key = PINNED_MODEL_PUBLIC_KEY
    if not _valid_key(key) or key is None:
        raise ModelIntegrityError("model signing public key not provisioned")
    payload = _read_regular(MODEL_MANIFEST, 1024 * 1024)
    signature = _read_regular(MODEL_SIGNATURE, 64)
    if len(signature) != 64:
        raise ModelIntegrityError("invalid signature length")
    _verify_signature(payload, signature, key)
    try:
        data = json.loads(payload, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise ModelIntegrityError("invalid signed manifest") from exc
    if not isinstance(data, dict) or any(
        not isinstance(k, str) or not isinstance(v, str)
        or not re.fullmatch(r"[0-9a-f]{64}", v) for k, v in data.items()
    ):
        raise ModelIntegrityError("invalid digest entries")
    return data


def _hash_regular(path: Path) -> str:
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ModelIntegrityError("symlinked path rejected")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ModelIntegrityError("model must be a regular file")
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError as exc:
        raise ModelIntegrityError("cannot read model") from exc


def verify_model_hash(
    model_path: str | Path, *, lock_id: str | None = None,
    expected_sha256: str | None = None, env_var: str | None = None,
) -> bool:
    """Require signed membership and a matching file; caller/env may only narrow."""
    digests = _signed_digests()
    expected = digests.get(lock_id) if lock_id is not None else None
    if not isinstance(expected, str):
        raise ModelIntegrityError("expected model digest absent from signed manifest")
    restrictions = (expected_sha256, os.environ.get(env_var) if env_var else None)
    if any(value not in (None, "", expected) for value in restrictions):
        raise ModelIntegrityError("digest restriction conflicts with signed manifest")
    if _hash_regular(Path(model_path)) != expected:
        raise ModelIntegrityError("model digest mismatch")
    return True


def verify_model_from_lockfile(lock_id: str, model_path: str | Path) -> bool:
    """Verify using an ID in the signed manifest, not an unsigned lockfile."""
    return verify_model_hash(model_path, lock_id=lock_id)
