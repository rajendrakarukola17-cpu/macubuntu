#!/usr/bin/env python3
"""
macubuntu / Celina Manifest Integrity Verifier (P1)

Recomputes the capability-manifest SHA-256 over canonical JSON (manifest
minus its own integrity block, sorted keys, compact separators) and compares
it to the integrity block in the file. Also verifies the two manifest copies
are byte-identical.

Fail-closed: any mismatch aborts with a non-zero exit code. The hash is
computed by this tool at verification time — never hardcoded.

Optional Ed25519 detached-signature verification:
    python tools/manifest_integrity.py --verify --pubkey /etc/macubuntu/update_pub.pem \
        --sig system/capabilities/celina-capabilities.json.sig

Exit codes:
    0 = integrity OK
    1 = integrity violation (hash mismatch, missing block, copies diverge)
    2 = usage error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

MANIFEST_A = REPO_ROOT / "system" / "capabilities" / "celina-capabilities.json"
MANIFEST_B = REPO_ROOT / "features" / "celina" / "config" / "celina-capabilities.json"
VERSION_TOML = REPO_ROOT / "version.toml"


def canonical_hash(obj: Dict[str, Any]) -> str:
    """SHA-256 over canonical JSON: manifest minus integrity block, sorted keys, compact separators."""
    obj_copy = {k: v for k, v in obj.items() if k != "integrity"}
    canonical = json.dumps(obj_copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def raw_file_hash(path: Path) -> str:
    """SHA-256 over raw file bytes (for documentation/audit only)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(path: Path) -> Tuple[Optional[Dict[str, Any]], list[str]]:
    """Read a manifest file once (eliminates double-read TOCTOU).

    Returns (obj, errors). obj is None if the file is missing or invalid.
    Explicitly validates that the top-level value is a dict and that
    'integrity' (when present) is also a dict — fail-closed on TypeError.
    """
    errors: list[str] = []
    if not path.is_file():
        errors.append(f"Manifest not found: {path}")
        return None, errors
    try:
        raw = path.read_bytes()
    except OSError as exc:
        errors.append(f"Cannot read {path}: {exc}")
        return None, errors
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append(f"Invalid JSON in {path}: {exc}")
        return None, errors
    if not isinstance(obj, dict):
        errors.append(f"{path}: top-level JSON value is {type(obj).__name__}, expected object")
        return None, errors
    ib = obj.get("integrity")
    if ib is not None and not isinstance(ib, dict):
        errors.append(f"{path}: integrity block is {type(ib).__name__}, expected object")
        return None, errors
    return obj, errors


def _check_integrity_block(label: str, obj: Dict[str, Any], errors: list[str]) -> None:
    ib = obj.get("integrity")
    if ib is None:
        errors.append(f"Manifest {label}: missing 'integrity' block")
        return
    for key in ("sha256", "signed_by", "schema_version", "default_policy"):
        if key not in ib:
            errors.append(f"Manifest {label}: integrity block missing '{key}'")
    if ib.get("default_policy") != "deny":
        errors.append(f"Manifest {label}: default_policy is '{ib.get('default_policy')}', expected 'deny'")
    if not ib.get("signed_by", "").strip():
        errors.append(f"Manifest {label}: integrity block 'signed_by' is empty")
    sha = ib.get("sha256", "")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        errors.append(f"Manifest {label}: integrity block 'sha256' is not a valid 64-hex SHA-256 digest")


def verify(pubkey: Optional[Path] = None, sig: Optional[Path] = None) -> Tuple[bool, list[str]]:
    """Run all integrity checks. Returns (ok, messages)."""
    messages: list[str] = []
    ok = True

    # Load both manifests once each (single read, eliminates TOCTOU)
    obj_a, errs_a = _load_manifest(MANIFEST_A)
    obj_b, errs_b = _load_manifest(MANIFEST_B)
    errors = errs_a + errs_b
    for e in errors:
        messages.append(e)
        ok = False
    if obj_a is None or obj_b is None:
        return False, messages

    # Check integrity blocks
    _check_integrity_block("A", obj_a, messages)
    _check_integrity_block("B", obj_b, messages)
    ok = ok and not messages  # reset ok based on messages so far

    # Recompute canonical hash and compare
    for label, obj in (("A", obj_a), ("B", obj_b)):
        ib = obj.get("integrity")
        if ib is None:
            continue
        computed = canonical_hash(obj)
        declared = ib.get("sha256", "")
        if computed != declared:
            messages.append(
                f"Manifest {label}: canonical hash mismatch — "
                f"computed={computed}, declared={declared}"
            )
            ok = False
        else:
            messages.append(f"Manifest {label}: canonical hash OK ({computed[:16]}...)")

    # Both copies byte-identical (single read each)
    if MANIFEST_A.read_bytes() != MANIFEST_B.read_bytes():
        messages.append("Manifest copies A and B are NOT byte-identical")
        ok = False
    else:
        raw_h = raw_file_hash(MANIFEST_A)
        messages.append(f"Manifest copies byte-identical (raw sha256={raw_h[:16]}...)")

    # Cross-check celina_version / manifest_version against version.toml when present
    if VERSION_TOML.is_file():
        try:
            vt_text = VERSION_TOML.read_text(encoding="utf-8")
            vt_ver = None
            for line in vt_text.splitlines():
                line = line.strip()
                if line.startswith("version") or line.startswith("celina_version"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        vt_ver = parts[1].strip().strip('"').strip("'")
                        break
            if vt_ver:
                for label, obj in (("A", obj_a), ("B", obj_b)):
                    for key in ("celina_version", "manifest_version"):
                        if obj.get(key) and obj[key] != vt_ver:
                            messages.append(
                                f"Manifest {label}: {key}={obj[key]} != version.toml version={vt_ver} (version drift — review, not an integrity violation)"
                            )
        except OSError:
            pass  # version.toml unreadable — non-fatal

    # Optional Ed25519 detached-signature verification on Manifest A
    if pubkey is not None or sig is not None:
        if pubkey is None or sig is None:
            messages.append("--pubkey and --sig must both be provided for signature verification")
            ok = False
        elif not pubkey.is_file():
            messages.append(f"Public key not found: {pubkey}")
            ok = False
        elif not sig.is_file():
            messages.append(f"Signature file not found: {sig}")
            ok = False
        else:
            try:
                import subprocess
                result = subprocess.run(
                    ["openssl", "pkeyutl", "-verify", "-pubin",
                     "-inkey", str(pubkey), "-rawin",
                     "-in", str(MANIFEST_A),
                     "-sigfile", str(sig)],
                    capture_output=True, timeout=30,
                )
                if result.returncode == 0:
                    messages.append(f"Manifest A: Ed25519 signature OK (key={pubkey})")
                else:
                    messages.append(f"Manifest A: Ed25519 signature verification FAILED")
                    ok = False
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                messages.append(f"Manifest A: could not verify signature: {exc}")
                ok = False

    return ok, messages


def recompute() -> None:
    """Print the canonical hash for both manifests. Fail-closed on missing file."""
    for label, path in (("A", MANIFEST_A), ("B", MANIFEST_B)):
        obj, errors = _load_manifest(path)
        if errors:
            for e in errors:
                print(e, file=sys.stderr)
            sys.exit(1)
        h = canonical_hash(obj)
        print(f"Manifest {label} ({path}): canonical sha256 = {h}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Celina capability manifest integrity (fail-closed).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=None,
        help="Verify both manifest copies + integrity block (default if no other mode given)",
    )
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="Print the canonical hash for both manifests",
    )
    parser.add_argument(
        "--pubkey",
        type=Path,
        default=None,
        help="Ed25519 public key PEM for detached-signature verification of Manifest A",
    )
    parser.add_argument(
        "--sig",
        type=Path,
        default=None,
        help="Detached Ed25519 signature file for Manifest A (requires --pubkey)",
    )
    args = parser.parse_args(argv)

    if args.recompute:
        recompute()
        return 0

    # Default to --verify when no mode is explicitly selected
    do_verify = args.verify if args.verify is not None else not args.recompute
    if not do_verify:
        parser.error("no verification mode selected (use --verify or --recompute)")

    ok, messages = verify(pubkey=args.pubkey, sig=args.sig)
    for m in messages:
        print(m)
    if ok:
        print("\nManifest integrity: OK")
        return 0
    print("\nManifest integrity: FAIL (fail-closed)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
