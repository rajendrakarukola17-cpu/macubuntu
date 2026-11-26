# Copyright 2026 Celina contributors.
"""Private L6 registry and immutable contract tests, without HOME access."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.l6.registry import (
    ArtifactIntegrityError,
    ArtifactRegistry,
    RegistryBlockedError,
)
from celina_pkg.selfimprove.l6.types import OptimizationConfig, PromptArtifact

if TYPE_CHECKING:
    from pathlib import Path

PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
INSECURE_MODE = 0o755

FLAGS = SelfImproveFlags(enabled=True, optimize=True)


@pytest.mark.parametrize("artifact_id", ["", "a..b", "../x", "a/b", "a\\b", "a b", "."])
def test_invalid_artifact_ids_fail(artifact_id: str) -> None:
    """Only bounded safe path components can identify artifacts."""
    with pytest.raises(ValueError, match="artifact_id"):
        PromptArtifact(artifact_id, "prompt", 1, "content")


def test_artifact_hash_validation_and_immutability() -> None:
    """Artifact identity includes the exact content digest."""
    artifact = PromptArtifact("abc_1", "skill", 1, "hello")
    assert artifact.content_hash == hashlib.sha256(b"hello").hexdigest()
    with pytest.raises(FrozenInstanceError):
        artifact.content = "changed"
    for kwargs in (
        {"artifact_id": "a", "kind": "bad", "version": 1, "content": "hello"},
        {"artifact_id": "a", "kind": "prompt", "version": 0, "content": "hello"},
        {"artifact_id": "a", "kind": "prompt", "version": 1, "content": ""},
        {"artifact_id": "a", "kind": "prompt", "version": 1, "content": "x" * 65537},
        {
            "artifact_id": "a",
            "kind": "prompt",
            "version": 1,
            "content": "hello",
            "content_hash": "0" * 64,
        },
    ):
        with pytest.raises(ValueError, match=r"artifact|content|version|kind|hash"):
            PromptArtifact(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_candidates": 0},
        {"alpha": 0},
        {"alpha": 1},
        {"alpha": float("nan")},
        {"bootstrap_resamples": 0},
    ],
)
def test_bad_optimization_config_is_rejected(kwargs: dict[str, object]) -> None:
    """Bounds and probability parameters fail closed."""
    with pytest.raises(ValueError, match=r"config|candidates|alpha|resamples"):
        OptimizationConfig(**kwargs)


@pytest.mark.parametrize("flags", [SelfImproveFlags(), SelfImproveFlags(enabled=True)])
def test_blocked_operations_never_touch_disk(tmp_path: Path, flags: SelfImproveFlags) -> None:
    """Disabled master/optimizer blocks every mutation before filesystem access."""
    root = tmp_path / "absent"
    registry = ArtifactRegistry(root, flags)
    for action in (
        lambda: registry.register("demo", "prompt", "x"),
        lambda: registry.activate("demo", 1),
        lambda: registry.rollback("demo"),
    ):
        with pytest.raises(RegistryBlockedError):
            action()
    assert not root.exists()


def test_versions_permissions_activation_and_rollback(tmp_path: Path) -> None:
    """Immutable versions and atomically switched active pointer support rollback."""
    root = tmp_path / "private"
    registry = ArtifactRegistry(root, FLAGS)
    assert registry.read("demo", 1) is None
    assert registry.active("demo") is None
    assert not root.exists()
    first = registry.register("demo", "prompt", "first")
    second = registry.register("demo", "prompt", "second")
    assert (first.version, second.version) == (1, 2)
    assert registry.read("demo", 1) == first
    registry.activate("demo", 1)
    registry.activate("demo", 2)
    assert registry.active("demo") == second
    assert registry.rollback("demo") == first
    assert registry.rollback("demo", target_version=2) == second
    assert stat.S_IMODE(root.stat().st_mode) == PRIVATE_DIR
    for path in root.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == PRIVATE_FILE
    assert (root / "demo.v1.json").read_text().find("first") >= 0


def test_tampered_artifact_pointer_and_json_fail_closed(tmp_path: Path) -> None:
    """Content and structure corruption must never be returned as trusted data."""
    registry = ArtifactRegistry(tmp_path / "private", FLAGS)
    registry.register("demo", "prompt", "first")
    registry.activate("demo", 1)
    path = registry.root_dir / "demo.v1.json"
    data = json.loads(path.read_text())
    data["content"] = "tampered"
    path.write_text(json.dumps(data))
    with pytest.raises(ArtifactIntegrityError):
        registry.read("demo", 1)
    with pytest.raises(ArtifactIntegrityError):
        registry.active("demo")
    path.write_text("{not json")
    with pytest.raises(ArtifactIntegrityError):
        registry.read("demo", 1)


def test_symlinks_and_insecure_permissions_are_rejected(tmp_path: Path) -> None:
    """Never follow symlinked roots, version files or active pointer files."""
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ArtifactIntegrityError):
        ArtifactRegistry(link, FLAGS).register("a", "prompt", "hello")
    registry = ArtifactRegistry(real, FLAGS)
    outside = tmp_path / "outside"
    outside.write_text("safe")
    (real / "a.v1.json").symlink_to(outside)
    with pytest.raises(ArtifactIntegrityError):
        registry.read("a", 1)
    with pytest.raises(ArtifactIntegrityError):
        registry.register("a", "prompt", "new")
    assert outside.read_text() == "safe"
    (real / "a.v1.json").unlink()
    registry.register("a", "prompt", "new")
    (real / "a.active.json").symlink_to(outside)
    with pytest.raises(ArtifactIntegrityError):
        registry.activate("a", 1)
    assert outside.read_text() == "safe"
    real.chmod(INSECURE_MODE)
    with pytest.raises(ArtifactIntegrityError):
        registry.read("a", 1)


@pytest.mark.parametrize("missing_hash", [None, ""])
def test_missing_hash_fails_closed(tmp_path: Path, missing_hash: str | None) -> None:
    """Missing or empty stored hashes must not be synthesized during reads."""
    registry = ArtifactRegistry(tmp_path / "private", FLAGS)
    registry.register("demo", "prompt", "first")
    path = registry.root_dir / "demo.v1.json"
    data = json.loads(path.read_text())
    if missing_hash is None:
        del data["content_hash"]
    else:
        data["content_hash"] = missing_hash
    path.write_text(json.dumps(data))
    with pytest.raises(ArtifactIntegrityError):
        registry.read("demo", 1)


def test_other_l6_dtos_reject_invalid_values() -> None:
    """All public DTOs reject malformed identities, bounds and status data."""
    from celina_pkg.selfimprove.budget import BudgetGuard  # noqa: PLC0415  # Validate DTOs.
    from celina_pkg.selfimprove.contracts import BudgetLimits  # noqa: PLC0415  # Validate DTOs.
    from celina_pkg.selfimprove.l6.types import (  # noqa: PLC0415  # Validate DTOs.
        CandidateEvaluation,
        GeneratedCandidate,
        OptimizationResult,
        PromotionApproval,
        PromotionOutcome,
        PromotionProposal,
    )

    snapshot = BudgetGuard(BudgetLimits(1, 1, 1, 0, 0)).snapshot()
    invalid = (
        lambda: GeneratedCandidate("content", tokens=-1),
        lambda: CandidateEvaluation("bad", "bad", "bad"),
        lambda: PromotionProposal("bad", "bad", "bad"),
        lambda: OptimizationResult("", None, None, (), snapshot),
        lambda: PromotionApproval(approved=True, proposal_hash="x" * 64, reason="reason"),
        lambda: PromotionApproval(approved=True, proposal_hash="a" * 64, reason=" "),
        lambda: PromotionOutcome(status="", promoted=False),
        lambda: PromotionOutcome(status="promoted", promoted=True, active_version=0),
    )
    for operation in invalid:
        with pytest.raises((ValueError, TypeError), match=r"invalid|must"):
            operation()


def test_bounded_unicode_round_trip(tmp_path: Path) -> None:
    """JSON escape expansion must not truncate valid bounded Unicode content."""
    registry = ArtifactRegistry(tmp_path / "private", FLAGS)
    artifact = registry.register("unicode", "skill", "🧪" * 40000)
    assert registry.read("unicode", 1) == artifact


def test_missing_current_version_blocks_switch_and_rollback(tmp_path: Path) -> None:
    """A corrupted current pointer must not be silently repaired by switching."""
    registry = ArtifactRegistry(tmp_path / "private", FLAGS)
    first = registry.register("demo", "prompt", "first")
    registry.register("demo", "prompt", "second")
    registry.activate("demo", first.version)
    (registry.root_dir / "demo.v1.json").unlink()
    with pytest.raises(ArtifactIntegrityError):
        registry.activate("demo", 2)
    with pytest.raises(ArtifactIntegrityError):
        registry.rollback("demo", target_version=2)


def test_inconsistent_result_dtos_are_rejected() -> None:
    """Success/failure status must agree with winner and promotion fields."""
    from celina_pkg.selfimprove.budget import BudgetGuard  # noqa: PLC0415  # DTO fixture.
    from celina_pkg.selfimprove.contracts import BudgetLimits  # noqa: PLC0415  # DTO fixture.
    from celina_pkg.selfimprove.l6.types import OptimizationResult, PromotionOutcome  # noqa: PLC0415  # DTO fixture.

    snapshot = BudgetGuard(BudgetLimits(1, 1, 1, 0, 0)).snapshot()
    with pytest.raises(ValueError, match="winner"):
        OptimizationResult("no_improvement", "invalid", None, (), snapshot)
    with pytest.raises(ValueError, match="promotion"):
        PromotionOutcome(status="promoted", promoted=False)


def test_removed_newest_version_is_not_reused(tmp_path: Path) -> None:
    """Deletion of a version cannot silently reuse its immutable sequence number."""
    registry = ArtifactRegistry(tmp_path / "private", FLAGS)
    registry.register("demo", "prompt", "first")
    registry.register("demo", "prompt", "second")
    (registry.root_dir / "demo.v2.json").unlink()
    with pytest.raises(ArtifactIntegrityError):
        registry.register("demo", "prompt", "replacement")
    assert not (registry.root_dir / "demo.v2.json").exists()


def test_excessive_head_fails_without_allocating_range(tmp_path: Path) -> None:
    """Corrupt sequence metadata must not provoke enormous allocations."""
    registry = ArtifactRegistry(tmp_path / "private", FLAGS)
    registry.register("demo", "prompt", "first")
    head = registry.root_dir / "demo.head.json"
    head.write_text(json.dumps({"latest": 10**100}))
    with pytest.raises(ArtifactIntegrityError):
        registry.register("demo", "prompt", "second")
