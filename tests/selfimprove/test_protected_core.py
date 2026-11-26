# Copyright 2026 Celina contributors.
"""Fail-closed checks for the frozen protected-core hashes."""

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "selfimprove" / "protected_hashes.json"
SHA256_HEX_LENGTH = 64
PROTECTED = (
    "features/celina/celina_pkg/approval.py",
    "features/celina/celina_pkg/evidence.py",
    "features/celina/celina_pkg/net_policy.py",
    "features/celina/celina_pkg/landlock_policy.py",
    "features/celina/celina_pkg/config.py",
    "features/celina/celina_pkg/connectors/base.py",
)


def verify_hashes(
    root: Path, manifest: dict[str, str], *, expected: set[str] | None = None,
) -> None:
    """Fail closed on an unexpected entry, path, symlink, or digest."""
    expected = set(PROTECTED) if expected is None else set(expected)
    assert isinstance(manifest, dict), "protected entry set mismatch"
    assert set(manifest) == expected, "protected entry set mismatch"
    for name, digest in manifest.items():
        relative = Path(name)
        assert not relative.is_absolute(), f"invalid path: {name}"
        assert ".." not in relative.parts, f"invalid path: {name}"
        assert isinstance(digest, str), f"invalid digest: {name}"
        assert len(digest) == SHA256_HEX_LENGTH, f"invalid digest: {name}"
        assert all(c in "0123456789abcdef" for c in digest), f"invalid digest: {name}"
        target = root / relative
        for parent in (target, *target.parents):
            if parent == root.parent:
                break
            assert not parent.is_symlink(), f"symlink in protected path: {name}"
        assert target.is_file(), f"missing protected file: {name}"
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest, f"hash mismatch: {name}"


def test_protected_core_matches_frozen_hashes() -> None:
    """Verify protected core matches frozen hashes."""
    verify_hashes(ROOT, json.loads(MANIFEST.read_text(encoding="utf-8")))


def test_one_byte_tamper_is_rejected(tmp_path: Path) -> None:
    """Verify one byte tamper is rejected."""
    p = tmp_path / "core.py"
    p.write_bytes(b"original")
    manifest = {"core.py": hashlib.sha256(p.read_bytes()).hexdigest()}
    verify_hashes(tmp_path, manifest, expected={"core.py"})
    p.write_bytes(b"Original")
    with pytest.raises(AssertionError, match="hash mismatch"):
        verify_hashes(tmp_path, manifest, expected={"core.py"})


def test_missing_file_and_symlink_are_rejected(tmp_path: Path) -> None:
    """Verify missing file and symlink are rejected."""
    manifest = {"core.py": hashlib.sha256(b"x").hexdigest()}
    with pytest.raises(AssertionError, match="missing"):
        verify_hashes(tmp_path, manifest, expected={"core.py"})
    target = tmp_path / "target.py"
    target.write_bytes(b"x")
    (tmp_path / "core.py").symlink_to(target)
    with pytest.raises(AssertionError, match="symlink"):
        verify_hashes(tmp_path, manifest, expected={"core.py"})


def test_manifest_cannot_omit_or_add_protected_entries(tmp_path: Path) -> None:
    """Verify manifest cannot omit or add protected entries."""
    with pytest.raises(AssertionError, match="entry set"):
        verify_hashes(tmp_path, {}, expected={"core.py"})
    with pytest.raises(AssertionError, match="entry set"):
        verify_hashes(tmp_path, {"../outside": "0" * 64}, expected={"core.py"})
