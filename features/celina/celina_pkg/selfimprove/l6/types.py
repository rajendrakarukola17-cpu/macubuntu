# Copyright 2026 Celina contributors.
"""Immutable offline optimization and approval contracts."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from celina_pkg.selfimprove.contracts import BudgetSnapshot
from celina_pkg.selfimprove.l3.stats import EvalComparison, EvalRun

if TYPE_CHECKING:
    from celina_pkg.selfimprove.contracts import EvalReport
    from celina_pkg.selfimprove.l3.suite import EvalTask

ArtifactKind = Literal["prompt", "skill"]
_MAX_CONTENT = 65536
_MAX_ID = 128
_MAX_CANDIDATES = 100
_MAX_RESAMPLES = 100000


def validate_id(value: str) -> None:
    """Reject unsafe artifact identifiers before touching storage."""
    if (
        not isinstance(value, str)
        or len(value) > _MAX_ID
        or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
    ):
        message = "invalid artifact_id"
        raise ValueError(message)


def content_digest(content: str) -> str:
    """Validate and hash bounded UTF-8 content."""
    if not isinstance(content, str) or not content.strip() or len(content) > _MAX_CONTENT:
        message = "invalid content"
        raise ValueError(message)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PromptArtifact:
    """Immutable version and SHA-256 content identity."""

    artifact_id: str
    kind: ArtifactKind
    version: int
    content: str
    content_hash: str = ""

    def __post_init__(self) -> None:
        """Check identity, version, content and supplied digest."""
        validate_id(self.artifact_id)
        if self.kind not in ("prompt", "skill"):
            message = "invalid artifact kind"
            raise ValueError(message)
        if type(self.version) is not int or self.version < 1:
            message = "invalid artifact version"
            raise ValueError(message)
        digest = content_digest(self.content)
        if self.content_hash and self.content_hash != digest:
            message = "invalid content hash"
            raise ValueError(message)
        object.__setattr__(self, "content_hash", digest)


@dataclass(frozen=True, slots=True)
class OptimizationConfig:
    """Bounded candidate count and paired-bootstrap configuration."""

    max_candidates: int = 5
    bootstrap_seed: int = 42
    bootstrap_resamples: int = 2000
    alpha: float = 0.05

    def __post_init__(self) -> None:
        """Reject invalid or excessive bootstrap parameters."""
        if type(self.max_candidates) is not int or not 1 <= self.max_candidates <= _MAX_CANDIDATES:
            message = "invalid max_candidates"
            raise ValueError(message)
        if (
            type(self.bootstrap_resamples) is not int
            or not 1 <= self.bootstrap_resamples <= _MAX_RESAMPLES
        ):
            message = "invalid bootstrap_resamples"
            raise ValueError(message)
        if type(self.bootstrap_seed) is not int:
            message = "invalid config seed"
            raise ValueError(message)
        if (
            isinstance(self.alpha, bool)
            or not isinstance(self.alpha, (int, float))
            or not math.isfinite(self.alpha)
            or not 0 < self.alpha < 1
        ):
            message = "invalid alpha"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class GeneratedCandidate:
    """Unregistered candidate and trusted generator's token declaration."""

    content: str
    tokens: int = 0

    def __post_init__(self) -> None:
        """Validate content and accounting declaration."""
        content_digest(self.content)
        if type(self.tokens) is not int or self.tokens < 0:
            message = "invalid token count"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """A candidate, its L3 run and paired comparison."""

    candidate: GeneratedCandidate
    run: EvalRun
    comparison: EvalComparison

    def __post_init__(self) -> None:
        """Require actual immutable L3 result types."""
        if (
            not isinstance(self.candidate, GeneratedCandidate)
            or not isinstance(self.run, EvalRun)
            or not isinstance(self.comparison, EvalComparison)
        ):
            message = "invalid candidate evaluation"
            raise TypeError(message)


@dataclass(frozen=True, slots=True)
class PromotionProposal:
    """An explicit baseline, candidate content and statistical comparison."""

    baseline: PromptArtifact
    candidate: GeneratedCandidate
    comparison: EvalComparison

    def __post_init__(self) -> None:
        """Require an explicit baseline, candidate and paired comparison."""
        if (
            not isinstance(self.baseline, PromptArtifact)
            or not isinstance(self.candidate, GeneratedCandidate)
            or not isinstance(self.comparison, EvalComparison)
        ):
            message = "invalid promotion proposal"
            raise TypeError(message)
        if content_digest(self.candidate.content) == self.baseline.content_hash:
            message = "invalid candidate; content matches baseline"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Optimizer outcome; no eligible winner is an explicit result."""

    status: str
    winner: CandidateEvaluation | None
    proposal: PromotionProposal | None
    evaluations: tuple[CandidateEvaluation, ...]
    budget: BudgetSnapshot
    budget_dimension: str = ""

    def __post_init__(self) -> None:
        """Reject malformed or inconsistent optimization results."""
        if self.status not in {
            "blocked_by_flag",
            "no_improvement",
            "failed_budget",
            "failed_error",
            "improved",
        } or not isinstance(self.budget, BudgetSnapshot):
            message = "invalid optimization result"
            raise ValueError(message)
        if not isinstance(self.evaluations, tuple) or any(
            not isinstance(x, CandidateEvaluation) for x in self.evaluations
        ):
            message = "invalid candidate evaluations"
            raise ValueError(message)
        if (
            self.status == "improved"
            and (
                not isinstance(self.winner, CandidateEvaluation)
                or not isinstance(self.proposal, PromotionProposal)
            )
        ) or (self.status != "improved" and (self.winner is not None or self.proposal is not None)):
            message = "invalid optimization winner"
            raise ValueError(message)
        if not isinstance(self.budget_dimension, str) or (
            self.status == "failed_budget" and not self.budget_dimension.strip()
        ):
            message = "invalid budget dimension"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PromotionApproval:
    """Approval must bind a proposal digest and carry a reason."""

    approved: bool
    proposal_hash: str
    reason: str

    def __post_init__(self) -> None:
        """Reject malformed host approval records."""
        if (
            type(self.approved) is not bool
            or not isinstance(self.proposal_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.proposal_hash)
            or not isinstance(self.reason, str)
            or not self.reason.strip()
        ):
            message = "invalid approval"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PromotionOutcome:
    """Promotion result and any verified active version."""

    status: str
    promoted: bool
    active_version: int | None = None

    def __post_init__(self) -> None:
        """Reject malformed statuses and active versions."""
        if (
            not isinstance(self.status, str)
            or not self.status.strip()
            or type(self.promoted) is not bool
        ):
            message = "invalid promotion outcome"
            raise ValueError(message)
        if self.active_version is not None and (
            type(self.active_version) is not int or self.active_version < 1
        ):
            message = "invalid active version"
            raise ValueError(message)
        if (self.status == "promoted") != self.promoted:
            message = "invalid promotion status"
            raise ValueError(message)
        if self.promoted and self.active_version is None:
            message = "promoted outcome must have active version"
            raise ValueError(message)


class CandidateGeneratorProtocol(Protocol):
    """Trusted offline generator; token declarations are host-supplied."""

    def generate(self, baseline: PromptArtifact, index: int) -> GeneratedCandidate:
        """Return one bounded candidate without modifying active behavior."""


class ArtifactEvaluatorProtocol(Protocol):
    """Trusted host evaluator returns an L0 report and declared token use."""

    def evaluate(self, task: EvalTask) -> tuple[EvalReport, int]:
        """Evaluate one offline task and report its token use."""


class ArtifactEvaluatorFactoryProtocol(Protocol):
    """Produce a trusted evaluator for an explicit artifact content."""

    def create(self, artifact: PromptArtifact) -> ArtifactEvaluatorProtocol:
        """Create an evaluator without network or implicit HOME access."""


class PromotionApprovalProtocol(Protocol):
    """Host approval boundary; production should bind a human approval flow."""

    def approve(self, proposal: PromotionProposal) -> PromotionApproval:
        """Approve this exact proposal digest or reject it."""
