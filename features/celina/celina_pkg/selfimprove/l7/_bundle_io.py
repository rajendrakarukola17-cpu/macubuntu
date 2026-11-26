# Copyright 2026 Celina contributors.
"""Hash-bound review bundles under a trusted, private host directory."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .types import PatchBundle

if TYPE_CHECKING:
    from .types import PatchProposal, PolicyCheckResult

_PRIVATE_DIR = 0o700


def bundle_metadata(
    proposal: PatchProposal,
    commit: str,
    policy: PolicyCheckResult,
) -> dict[str, object]:
    """Bind the full patch digest, base commit and review metadata."""
    return {
        "version": 1,
        "proposal_id": proposal.proposal_id,
        "summary": proposal.summary,
        "base_commit": commit,
        "touched_files": list(policy.touched_files),
        "patch_sha256": hashlib.sha256(proposal.unified_diff.encode("utf-8")).hexdigest(),
    }


def bundle_hash(metadata: dict[str, object]) -> str:
    """Hash canonical metadata including the exact patch digest."""
    raw = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _directory(directory: Path, repo: Path) -> None:
    if (
        not isinstance(directory, Path)
        or not directory.is_absolute()
        or ".." in directory.parts
        or directory == repo
        or directory.is_relative_to(repo)
        or any(path.is_symlink() for path in (directory, *directory.parents))
    ):
        message = "unsafe bundle directory"
        raise ValueError(message)
    directory.mkdir(mode=_PRIVATE_DIR, exist_ok=True)
    if not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != _PRIVATE_DIR:
        message = "bundle directory must be private"
        raise ValueError(message)


def _publish(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".bundle-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def export_bundle(
    directory: Path,
    repo: Path,
    proposal: PatchProposal,
    metadata: dict[str, object],
    digest: str,
) -> PatchBundle:
    """Publish two individually atomic 0600 files without overwriting prior bundles."""
    _directory(directory, repo)
    stem = f"{proposal.proposal_id}-{digest}"
    patch_path, json_path = directory / f"{stem}.patch", directory / f"{stem}.json"
    review = {**metadata, "bundle_hash": digest}
    _publish(patch_path, proposal.unified_diff.encode("utf-8"))
    try:
        _publish(json_path, (json.dumps(review, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    except BaseException:
        patch_path.unlink(missing_ok=True)
        raise
    return PatchBundle(patch_path, json_path, digest)


def discard_bundle(bundle: PatchBundle) -> None:
    """Remove files from an export whose final audit step failed."""
    bundle.patch_path.unlink(missing_ok=True)
    bundle.metadata_path.unlink(missing_ok=True)
