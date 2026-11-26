# Copyright 2026 Celina contributors.
"""Fail-closed unified-diff policy and immutable proposal contract tests."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from celina_pkg.selfimprove.l7.policy import PolicyViolationError, validate_patch_proposal
from celina_pkg.selfimprove.l7.types import PatchPolicyConfig, PatchProposal

if TYPE_CHECKING:
    from pathlib import Path


EXPECTED_CHANGED_LINES = 2


def diff(path: str) -> str:
    """Build a simple textual patch with one removal and one addition."""
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n"


def manifest(tmp_path: Path) -> Path:
    """Write an explicit synthetic protected manifest without HOME access."""
    p = tmp_path / "protected_hashes.json"
    p.write_text(json.dumps({"custom/locked.py": "a" * 64}))
    return p


def proposal(patch: str) -> PatchProposal:
    """Build a valid L7 proposal."""
    return PatchProposal("test_1", patch, "fix a test")


def test_proposal_validation_and_frozen_contract() -> None:
    """Reject unsafe IDs, blank data, oversized bytes, and nonpositive caps."""
    valid = proposal(diff("src/file.py"))
    with pytest.raises(FrozenInstanceError):
        valid.summary = "changed"
    for args in (("bad/id", "diff", "x"), ("", "diff", "x"), ("a", "", "x"), ("a", "diff", " ")):
        with pytest.raises(ValueError, match=r"proposal|diff|summary"):
            PatchProposal(*args)
    with pytest.raises(ValueError, match="diff"):
        PatchProposal("a", "x" * 1048577, "x")
    for kwargs in (
        {"max_files": 0},
        {"max_diff_bytes": 0},
        {"max_changed_lines": -1},
        {"git_timeout_sec": 0},
    ):
        with pytest.raises(ValueError, match=r"config|positive"):
            PatchPolicyConfig(**kwargs)


def test_valid_patch_exposes_all_touched_paths(tmp_path: Path) -> None:
    """Ordinary bounded text changes are accepted without filesystem changes."""
    result = validate_patch_proposal(
        proposal(diff("src/file.py")),
        manifest(tmp_path),
        PatchPolicyConfig(),
    )
    assert result.allowed
    assert result.touched_files == ("src/file.py",)
    assert result.changed_lines == EXPECTED_CHANGED_LINES


@pytest.mark.parametrize(
    "path",
    [
        "features/celina/celina_pkg/approval.py",
        "features/celina/celina_pkg/evidence.py",
        "features/celina/celina_pkg/config.py",
        "features/celina/celina_pkg/net_policy.py",
        "features/celina/celina_pkg/landlock_policy.py",
        "features/celina/celina_pkg/connectors/base.py",
        "selfimprove/protected_hashes.json",
        "features/celina/celina_pkg/selfimprove/protected_hashes.json",
        ".github/CODEOWNERS",
        "tests/selfimprove/test_protected_core.py",
        "tests/selfimprove/test_layering.py",
        "custom/locked.py",
    ],
)
def test_protected_paths_are_never_accepted(tmp_path: Path, path: str) -> None:
    """Explicit manifest entries and hardcoded defense-in-depth are protected."""
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(diff(path)), manifest(tmp_path), PatchPolicyConfig())


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/etc/passwd",
        "./approval.py",
        "src/../approval.py",
        "features/celina/celina_pkg/./approval.py",
        "C:/temp/file",
        "dir\\file",
        ".git/config",
        "sub/.git/config",
        "src/file\x00.py",
        "with space.py",
    ],
)
def test_traversal_and_ambiguous_paths_fail_closed(tmp_path: Path, path: str) -> None:
    """Only canonical repository-relative paths are accepted."""
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(diff(path)), manifest(tmp_path), PatchPolicyConfig())


def test_rename_and_copy_cannot_hide_protected_destination(tmp_path: Path) -> None:
    """All diff metadata paths must agree and are checked against protection."""
    for operation in ("rename", "copy"):
        patch = (
            "diff --git a/safe.py b/safe.py\n"
            "similarity index 100%\n"
            f"{operation} from safe.py\n"
            f"{operation} to features/celina/celina_pkg/approval.py\n"
        )
        with pytest.raises(PolicyViolationError):
            validate_patch_proposal(proposal(patch), manifest(tmp_path), PatchPolicyConfig())


@pytest.mark.parametrize(
    "patch",
    [
        "not a patch\n",
        "diff --git a/a b/a\nGIT binary patch\n",
        "diff --git a/a b/a\nBinary files a/a and b/a differ\n",
        "diff --git a/a b/a\nnew file mode 120000\n--- /dev/null\n+++ b/a\n@@ -0,0 +1 @@\n+link\n",
        "diff --git a/a b/a\n--- a/a\n+++ b/other\n@@ -1 +1 @@\n-a\n+b\n",
    ],
)
def test_binary_symlink_and_malformed_diffs_fail(tmp_path: Path, patch: str) -> None:
    """Binary, symlink and inconsistent patches never reach Git."""
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(patch), manifest(tmp_path), PatchPolicyConfig())


def test_file_and_line_bounds_and_manifest_failure(tmp_path: Path) -> None:
    """Limits and unreadable/invalid policy state fail closed."""
    m = manifest(tmp_path)
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(diff("x") + diff("y")), m, PatchPolicyConfig(max_files=1))
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(diff("x")), m, PatchPolicyConfig(max_changed_lines=1))
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(diff("x")), m, PatchPolicyConfig(max_diff_bytes=10))
    m.write_text("{}")
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(diff("x")), m, PatchPolicyConfig())
    m.unlink()
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(diff("x")), m, PatchPolicyConfig())


def test_duplicate_file_headers_cannot_hide_a_protected_path(tmp_path: Path) -> None:
    """A later safe marker must not conceal any earlier protected marker."""
    patch = (
        "diff --git a/safe.py b/safe.py\n"
        "--- a/features/celina/celina_pkg/approval.py\n"
        "+++ b/features/celina/celina_pkg/approval.py\n"
        "--- a/safe.py\n+++ b/safe.py\n@@ -1 +1 @@\n-old\n+new\n"
    )
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(patch), manifest(tmp_path), PatchPolicyConfig())


def test_invalid_manifest_path_entry_fails_closed(tmp_path: Path) -> None:
    """Manifest paths must be canonical or they cannot silently lose protection."""
    m = manifest(tmp_path)
    m.write_text(json.dumps({"safe/../protected.py": "a" * 64}))
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(diff("src/file.py")), m, PatchPolicyConfig())


def test_other_frozen_dtos_require_strict_types(tmp_path: Path) -> None:
    """Bundle, approval, verification, and outcome values reject malformed fields."""
    from celina_pkg.selfimprove.budget import BudgetGuard  # noqa: PLC0415  # DTO fixtures.
    from celina_pkg.selfimprove.contracts import BudgetLimits  # noqa: PLC0415  # DTO fixtures.
    from celina_pkg.selfimprove.l7.types import (  # noqa: PLC0415  # DTO fixtures.
        PatchApproval,
        PatchBundle,
        PatchOutcome,
        PatchVerificationSummary,
    )

    snapshot = BudgetGuard(BudgetLimits(1, 1, 1, 0, 0)).snapshot()
    with pytest.raises(ValueError, match="approval"):
        PatchApproval(approved=1, bundle_hash="a" * 64, reason="x")
    with pytest.raises((ValueError, TypeError), match="bundle"):
        PatchBundle(tmp_path / "x", tmp_path / "y", "bad")
    with pytest.raises((ValueError, TypeError), match="verification"):
        PatchVerificationSummary("not a gate")
    with pytest.raises((ValueError, TypeError), match="outcome"):
        PatchOutcome(status="bundle_ready", approved=True, bundle=None, budget=snapshot)


@pytest.mark.parametrize(
    "patch",
    [
        "diff --git a/a b/a\n--- c/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n",
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n",
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n?bad\n",
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -0,0 +1 @@\n-old\n",
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n same\n",
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n",
        "diff --git a/a b/b\n--- a/a\n+++ b/b\n@@ -1 +1 @@\n-old\n+new\n",
        "diff --git a/a b/a\n--- a/a\n@@ -1 +1 @@\n-old\n+new\n",
        "diff --git a/a b/a\n@@ -1 +1 @@\n-old\n+new\n",
        "diff --git a/a b/a\nrename from a\n",
        "diff --git a/a b/a\n--- /dev/null\n+++ /dev/null\n@@ -1 +1 @@\n-old\n+new\n",
    ],
)
def test_additional_ambiguous_hunks_and_metadata_are_rejected(tmp_path: Path, patch: str) -> None:
    """Malformed counts, metadata and path headers always fail closed."""
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(patch), manifest(tmp_path), PatchPolicyConfig())


def test_rename_copy_context_and_no_newline_are_supported(tmp_path: Path) -> None:
    """Supported Git metadata still accounts for both source and destination."""
    for kind in ("rename", "copy"):
        patch = f"diff --git a/old b/new\nsimilarity index 100%\n{kind} from old\n{kind} to new\n"
        result = validate_patch_proposal(proposal(patch), manifest(tmp_path), PatchPolicyConfig())
        assert result.touched_files == ("new", "old")
    text = (
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1,2 +1,2 @@\n"
        " same\n-old\n+new\n\\ No newline at end of file\n"
    )
    assert (
        validate_patch_proposal(
            proposal(text),
            manifest(tmp_path),
            PatchPolicyConfig(),
        ).changed_lines
        == EXPECTED_CHANGED_LINES
    )


def test_invalid_arguments_and_unsafe_manifest_rejected(tmp_path: Path) -> None:
    """Policy rejects incorrect argument types and symlinked manifest paths."""
    m = manifest(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(m)
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal(proposal(diff("x")), link, PatchPolicyConfig())
    with pytest.raises(PolicyViolationError):
        validate_patch_proposal("not a proposal", m, PatchPolicyConfig())


def test_more_dto_failure_and_success_paths() -> None:
    """A too-large policy cap and malformed outcomes are rejected explicitly."""
    from celina_pkg.selfimprove.budget import BudgetGuard  # noqa: PLC0415  # DTO fixtures.
    from celina_pkg.selfimprove.contracts import BudgetLimits  # noqa: PLC0415  # DTO fixtures.
    from celina_pkg.selfimprove.l2.gate import GateDecision  # noqa: PLC0415  # DTO fixtures.
    from celina_pkg.selfimprove.l7.types import PatchOutcome, PatchVerificationSummary  # noqa: PLC0415  # DTO fixtures.

    with pytest.raises(ValueError, match="large"):
        PatchPolicyConfig(max_diff_bytes=1048577)
    assert (
        PatchVerificationSummary(
            GateDecision(passed=True, blocked_reason="", results=()),
        ).comparison
        is None
    )
    snapshot = BudgetGuard(BudgetLimits(1, 1, 1, 0, 0)).snapshot()
    with pytest.raises(ValueError, match="outcome"):
        PatchOutcome(status="", approved=False, bundle=None, budget=snapshot)
    with pytest.raises(ValueError, match="budget"):
        PatchOutcome(status="failed_budget", approved=False, bundle=None, budget=snapshot)
