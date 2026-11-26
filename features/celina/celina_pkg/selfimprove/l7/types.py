# Copyright 2026 Celina contributors.
"""Immutable self-patcher contracts and trusted host interfaces."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from celina_pkg.selfimprove.contracts import BudgetSnapshot
from celina_pkg.selfimprove.l2.gate import GateDecision
from celina_pkg.selfimprove.l3.stats import EvalComparison

if TYPE_CHECKING:
    from subprocess import CompletedProcess

    from celina_pkg.selfimprove.budget import BudgetGuard
    from celina_pkg.selfimprove.l2.gate import VerificationGate
    from celina_pkg.selfimprove.l3.runner import EvalRunner

_MAX_DIFF_BYTES = 1_048_576
_MAX_SUMMARY = 4096


@dataclass(frozen=True, slots=True)
class PatchPolicyConfig:
    """Finite resource and parser limits for one patch proposal."""

    max_files: int = 10
    max_diff_bytes: int = 262144
    max_changed_lines: int = 2000
    git_timeout_sec: float = 30.0

    def __post_init__(self) -> None:
        """Reject invalid, nonpositive or excessive limits."""
        if any(
            type(value) is not int or value < 1
            for value in (self.max_files, self.max_diff_bytes, self.max_changed_lines)
        ):
            message = "config limits must be positive integers"
            raise ValueError(message)
        if self.max_diff_bytes > _MAX_DIFF_BYTES:
            message = "config diff limit is too large"
            raise ValueError(message)
        if (
            isinstance(self.git_timeout_sec, bool)
            or not isinstance(self.git_timeout_sec, (int, float))
            or not math.isfinite(self.git_timeout_sec)
            or self.git_timeout_sec <= 0
        ):
            message = "config timeout must be positive and finite"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PatchProposal:
    """A bounded textual diff; constructing it never applies a patch."""

    proposal_id: str
    unified_diff: str
    summary: str

    def __post_init__(self) -> None:
        """Reject unsafe IDs and malformed or excessive text."""
        if not isinstance(self.proposal_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}",
            self.proposal_id,
        ):
            message = "invalid proposal ID"
            raise ValueError(message)
        if (
            not isinstance(self.unified_diff, str)
            or not self.unified_diff.strip()
            or len(self.unified_diff.encode("utf-8")) > _MAX_DIFF_BYTES
        ):
            message = "invalid diff"
            raise ValueError(message)
        if (
            not isinstance(self.summary, str)
            or not self.summary.strip()
            or len(self.summary) > _MAX_SUMMARY
        ):
            message = "invalid summary"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PolicyCheckResult:
    """Canonical path set and count of added/removed lines."""

    allowed: bool
    touched_files: tuple[str, ...]
    changed_lines: int


@dataclass(frozen=True, slots=True)
class PatchApproval:
    """Human-gate result bound to the exact bundle digest."""

    approved: bool
    bundle_hash: str
    reason: str

    def __post_init__(self) -> None:
        """Reject invalid approval values."""
        if (
            type(self.approved) is not bool
            or not isinstance(self.bundle_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.bundle_hash)
            or not isinstance(self.reason, str)
            or not self.reason.strip()
        ):
            message = "invalid patch approval"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PatchBundle:
    """Explicit review bundle paths and content-bound digest."""

    patch_path: Path
    metadata_path: Path
    bundle_hash: str

    def __post_init__(self) -> None:
        """Require explicit absolute paths and a content digest."""
        if (
            not isinstance(self.patch_path, Path)
            or not self.patch_path.is_absolute()
            or not isinstance(self.metadata_path, Path)
            or not self.metadata_path.is_absolute()
            or not isinstance(self.bundle_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.bundle_hash)
        ):
            message = "invalid patch bundle"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PatchVerificationSummary:
    """Trusted L2 decision and optional L3 comparison."""

    gate: GateDecision
    comparison: EvalComparison | None = None

    def __post_init__(self) -> None:
        """Require an L2 decision and an optional L3 comparison."""
        if not isinstance(self.gate, GateDecision) or (
            self.comparison is not None and not isinstance(self.comparison, EvalComparison)
        ):
            message = "invalid verification summary"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PatchOutcome:
    """Fail-closed status, optional review bundle, and budget snapshot."""

    status: str
    approved: bool
    bundle: PatchBundle | None
    budget: BudgetSnapshot
    budget_dimension: str = ""

    def __post_init__(self) -> None:
        """Require consistent bundle, status and budget values."""
        if (
            not isinstance(self.status, str)
            or not self.status.strip()
            or type(self.approved) is not bool
            or not isinstance(self.budget, BudgetSnapshot)
            or not isinstance(self.budget_dimension, str)
        ):
            message = "invalid patch outcome"
            raise ValueError(message)
        if self.approved != (self.status == "bundle_ready") or self.approved != isinstance(
            self.bundle,
            PatchBundle,
        ):
            message = "inconsistent patch outcome"
            raise ValueError(message)
        if self.status == "failed_budget" and not self.budget_dimension.strip():
            message = "invalid budget outcome"
            raise ValueError(message)


class GitCommandExecutor(Protocol):
    """Trusted subprocess executor; caller supplies only allowlisted git commands."""

    def __call__(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        input_data: bytes | None,
        timeout: float,
    ) -> CompletedProcess[bytes]:
        """Execute an argv list without a shell."""


class WorktreeGateFactory(Protocol):
    """Construct host-trusted L2 verification bound to a specific worktree."""

    def create(self, worktree: Path, budget: BudgetGuard) -> VerificationGate:
        """Return a configured gate using the shared budget."""


class WorktreeEvalFactory(Protocol):
    """Construct host-trusted L3 evaluation bound to a specific worktree."""

    def create(self, worktree: Path, budget: BudgetGuard) -> EvalRunner:
        """Return a configured runner using the shared budget."""


class PatchApprovalProtocol(Protocol):
    """Host human-approval implementation, bound to a digest in production."""

    def approve(self, proposal: PatchProposal, bundle_hash: str) -> PatchApproval:
        """Approve or reject the exact patch bundle digest."""
