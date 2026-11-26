# Copyright 2026 Celina contributors.
"""Immutable hash-verified artifacts and atomic active-version pointers."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import TYPE_CHECKING, cast

from ._registry_io import (
    ArtifactIntegrityError,
    ArtifactRegistryError,
    RegistryBlockedError,
    RegistryStaleError,
    check_root,
    locked,
    read_json,
    write_json,
)
from .types import ArtifactKind, GeneratedCandidate, PromptArtifact, validate_id

if TYPE_CHECKING:
    from pathlib import Path

    from celina_pkg.selfimprove.flags import SelfImproveFlags

__all__ = (
    "ArtifactIntegrityError",
    "ArtifactRegistry",
    "ArtifactRegistryError",
    "RegistryBlockedError",
)


class ArtifactRegistry:
    """Private registry primitives; direct pointer writes are trusted host operations."""

    def __init__(self, root_dir: Path, flags: SelfImproveFlags) -> None:
        """Bind explicit configuration without performing filesystem I/O."""
        self.root_dir = root_dir
        self.flags = flags

    def _enabled(self) -> None:
        if not self.flags.enabled or not self.flags.optimize:
            message = "artifact registry is disabled"
            raise RegistryBlockedError(message)

    def _path(self, artifact_id: str, version: int) -> Path:
        validate_id(artifact_id)
        if type(version) is not int or version < 1:
            message = "invalid artifact version"
            raise ValueError(message)
        return self.root_dir / f"{artifact_id}.v{version}.json"

    def read(self, artifact_id: str, version: int) -> PromptArtifact | None:
        """Return an existing verified immutable version without creating files."""
        path = self._path(artifact_id, version)
        if not check_root(self.root_dir):
            return None
        data = read_json(path)
        if data is None:
            return None
        fields = {"artifact_id", "kind", "version", "content", "content_hash"}
        if (
            set(data) != fields
            or not isinstance(data["content_hash"], str)
            or not data["content_hash"]
        ):
            message = "artifact fields or content hash missing"
            raise ArtifactIntegrityError(message)
        try:
            artifact = PromptArtifact(
                cast("str", data["artifact_id"]),
                cast("ArtifactKind", data["kind"]),
                cast("int", data["version"]),
                cast("str", data["content"]),
                data["content_hash"],
            )
        except (TypeError, ValueError, UnicodeError) as exc:
            message = "invalid artifact data or hash"
            raise ArtifactIntegrityError(message) from exc
        if artifact.artifact_id != artifact_id or artifact.version != version:
            message = "artifact identity mismatch"
            raise ArtifactIntegrityError(message)
        return artifact

    def _pointer(self, artifact_id: str) -> tuple[int, int | None] | None:
        validate_id(artifact_id)
        if not check_root(self.root_dir):
            return None
        data = read_json(self.root_dir / f"{artifact_id}.active.json")
        if data is None:
            return None
        if (
            set(data) != {"active", "previous"}
            or type(data["active"]) is not int
            or data["active"] < 1
            or (
                data["previous"] is not None
                and (type(data["previous"]) is not int or data["previous"] < 1)
            )
        ):
            message = "invalid active pointer"
            raise ArtifactIntegrityError(message)
        return data["active"], data["previous"]

    def active(self, artifact_id: str) -> PromptArtifact | None:
        """Read and verify the currently selected version."""
        pointer = self._pointer(artifact_id)
        if pointer is None:
            return None
        artifact = self.read(artifact_id, pointer[0])
        if artifact is None:
            message = "active artifact is missing"
            raise ArtifactIntegrityError(message)
        return artifact

    def register(self, artifact_id: str, kind: ArtifactKind, content: str) -> PromptArtifact:
        """Publish a new monotonically numbered version without overwriting."""
        self._enabled()
        PromptArtifact(artifact_id, kind, 1, content)
        with locked(self.root_dir):
            return self._register(artifact_id, kind, content)

    def _versions(self, artifact_id: str) -> list[PromptArtifact]:
        pattern = re.compile(re.escape(artifact_id) + r"\.v([1-9][0-9]*)\.json\Z")
        versions: list[PromptArtifact] = []
        for path in self.root_dir.iterdir():
            match = pattern.fullmatch(path.name)
            if match:
                artifact = self.read(artifact_id, int(match.group(1)))
                if artifact is None:
                    message = "artifact disappeared during scan"
                    raise ArtifactIntegrityError(message)
                versions.append(artifact)
        return sorted(versions, key=lambda item: item.version)

    def _verified_versions(self, artifact_id: str) -> list[PromptArtifact]:
        versions = self._versions(artifact_id)
        head = read_json(self.root_dir / f"{artifact_id}.head.json")
        if head is None:
            if versions:
                message = "missing version head"
                raise ArtifactIntegrityError(message)
            return versions
        if set(head) != {"latest"} or type(head["latest"]) is not int or head["latest"] < 1:
            message = "invalid version head"
            raise ArtifactIntegrityError(message)
        if len(versions) != head["latest"] or any(
            item.version != index for index, item in enumerate(versions, start=1)
        ):
            message = "version history missing or inconsistent"
            raise ArtifactIntegrityError(message)
        return versions

    def _register(self, artifact_id: str, kind: ArtifactKind, content: str) -> PromptArtifact:
        versions = self._verified_versions(artifact_id)
        version = len(versions) + 1
        artifact = PromptArtifact(artifact_id, kind, version, content)
        write_json(self._path(artifact_id, version), asdict(artifact), replace=False)
        write_json(self.root_dir / f"{artifact_id}.head.json", {"latest": version}, replace=True)
        return artifact

    def promote(self, baseline: PromptArtifact, candidate: GeneratedCandidate) -> PromptArtifact:
        """Atomically compare the baseline and register/select an approved candidate.

        This is a trusted host primitive; the promoter enforces human approval.
        """
        self._enabled()
        with locked(self.root_dir):
            if self.active(baseline.artifact_id) != baseline:
                message = "active baseline changed"
                raise RegistryStaleError(message)
            versions = self._verified_versions(baseline.artifact_id)
            matches = [
                item
                for item in versions
                if item.kind == baseline.kind and item.content == candidate.content
            ]
            artifact = (
                matches[0]
                if matches
                else self._register(baseline.artifact_id, baseline.kind, candidate.content)
            )
            return self._activate(baseline.artifact_id, artifact.version)

    def activate(self, artifact_id: str, version: int) -> PromptArtifact:
        """Atomically select a verified version; intended for trusted promoter use."""
        self._enabled()
        with locked(self.root_dir):
            return self._activate(artifact_id, version)

    def _activate(self, artifact_id: str, version: int) -> PromptArtifact:
        artifact = self.read(artifact_id, version)
        if artifact is None:
            message = "version does not exist"
            raise ArtifactIntegrityError(message)
        pointer = self._pointer(artifact_id)
        previous = pointer[0] if pointer is not None else None
        if pointer is not None:
            self.active(artifact_id)
        write_json(
            self.root_dir / f"{artifact_id}.active.json",
            {"active": version, "previous": previous},
            replace=True,
        )
        return artifact

    def rollback(self, artifact_id: str, target_version: int | None = None) -> PromptArtifact:
        """Select the previous or explicitly requested verified artifact."""
        self._enabled()
        with locked(self.root_dir):
            pointer = self._pointer(artifact_id)
            target = (
                target_version if target_version is not None else (pointer[1] if pointer else None)
            )
            if target is None:
                message = "no rollback target"
                raise ArtifactIntegrityError(message)
            return self._activate(artifact_id, target)
