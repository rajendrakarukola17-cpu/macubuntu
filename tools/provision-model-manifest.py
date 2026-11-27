#!/usr/bin/env python3
"""Operator helper to build, sign, and verify Celina's Ed25519 model-digest manifest.

Keeps ``features/celina/celina_pkg/model_integrity.py`` fail-closed by default
(``PINNED_MODEL_PUBLIC_KEY = None``) in Git while giving operators a deterministic
command to hash model files, sign ``system/model-digests.json`` -> ``.sig``, and
verify signatures with OpenSSL before pinning their public key at deployment time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

MODEL_EXTENSIONS = frozenset({".gguf", ".onnx", ".bin", ".safetensors", ".pt", ".pth"})
_OPENSSL = "/usr/bin/openssl"


def build_manifest(models_dir: Path, *, extensions: frozenset[str] = MODEL_EXTENSIONS) -> dict[str, str]:
    """Return sorted relative-path -> SHA-256 hex digest mapping for regular model files."""
    if not models_dir.is_dir():
        raise FileNotFoundError(f"models directory not found: {models_dir}")
    digests: dict[str, str] = {}
    for item in sorted(models_dir.rglob("*")):
        if item.is_symlink() or not item.is_file():
            continue
        if item.suffix.lower() not in extensions:
            continue
        rel = item.relative_to(models_dir).as_posix()
        h = hashlib.sha256()
        with item.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        digests[rel] = h.hexdigest()
    return digests


def sign_manifest(manifest_path: Path, sig_path: Path, private_key_path: Path) -> None:
    """Sign ``manifest_path`` with an Ed25519 private key using OpenSSL pkeyutl -rawin."""
    res = subprocess.run(  # noqa: S603  # fixed /usr/bin/openssl argv, shell=False
        [
            _OPENSSL, "pkeyutl", "-sign", "-rawin",
            "-inkey", str(private_key_path),
            "-in", str(manifest_path),
            "-out", str(sig_path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"openssl sign failed: {res.stderr.strip()}")


def verify_manifest(manifest_path: Path, sig_path: Path, public_key_path: Path) -> bool:
    """Verify ``manifest_path`` against ``sig_path`` and ``public_key_path``."""
    res = subprocess.run(  # noqa: S603  # fixed /usr/bin/openssl argv, shell=False
        [
            _OPENSSL, "pkeyutl", "-verify", "-pubin",
            "-inkey", str(public_key_path),
            "-rawin",
            "-in", str(manifest_path),
            "-sigfile", str(sig_path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return res.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build, sign, and verify system/model-digests.json")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Hash model files into a JSON manifest")
    p_build.add_argument("--models-dir", type=Path, required=True)
    p_build.add_argument("--out", type=Path, default=Path("system/model-digests.json"))

    p_sign = sub.add_parser("sign", help="Sign a model-digests.json manifest with an Ed25519 private key")
    p_sign.add_argument("--manifest", type=Path, default=Path("system/model-digests.json"))
    p_sign.add_argument("--sig", type=Path, default=Path("system/model-digests.sig"))
    p_sign.add_argument("--private-key", type=Path, required=True)

    p_ver = sub.add_parser("verify", help="Verify model-digests.json + .sig with an Ed25519 public key")
    p_ver.add_argument("--manifest", type=Path, default=Path("system/model-digests.json"))
    p_ver.add_argument("--sig", type=Path, default=Path("system/model-digests.sig"))
    p_ver.add_argument("--public-key", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.cmd == "build":
        digests = build_manifest(args.models_dir)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(digests, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {args.out} ({len(digests)} model digest(s))")
        return 0
    if args.cmd == "sign":
        sign_manifest(args.manifest, args.sig, args.private_key)
        print(f"Signed {args.manifest} -> {args.sig}")
        return 0
    if args.cmd == "verify":
        ok = verify_manifest(args.manifest, args.sig, args.public_key)
        print("Manifest signature: OK" if ok else "Manifest signature: INVALID")
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
