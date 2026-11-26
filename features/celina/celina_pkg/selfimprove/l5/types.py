# Copyright 2026 Celina contributors.
"""Immutable orchestration contracts; proposal creation must not mutate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from celina_pkg.selfimprove.contracts import TaskSpec


_MAX_SUMMARY = 8192
_MAX_PATHS = 64
_MAX_PATH_LENGTH = 4096


@dataclass(frozen=True, slots=True)
class LoopConfig:
    """Consecutive equal failure signatures allowed before stopping."""

    stagnation_limit: int = 2

    def __post_init__(self) -> None:
        """Reject nonsensical stagnation thresholds."""
        if type(self.stagnation_limit) is not int or self.stagnation_limit < 1:
            message = "stagnation limit must be a positive integer"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ActorContext:
    """Task and previous verifier feedback; memory is explicitly untrusted."""

    task: TaskSpec
    iteration: int
    feedback: str
    memory_context: str


@dataclass(frozen=True, slots=True)
class ActorProposal:
    """Declared work; actor must not mutate during proposal generation."""

    summary: str
    tokens: int = 0
    file_writes: int = 0
    subprocesses: int = 0
    requires_mutation: bool = False
    target_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject malformed declarations before budget or approval decisions."""
        if not isinstance(self.summary, str) or len(self.summary) > _MAX_SUMMARY:
            message = "summary must be a bounded string"
            raise ValueError(message)
        if any(
            type(n) is not int or n < 0 for n in (self.tokens, self.file_writes, self.subprocesses)
        ):
            message = "resource counts must be nonnegative integers"
            raise ValueError(message)
        if type(self.requires_mutation) is not bool or not isinstance(self.target_paths, tuple):
            message = "invalid mutation declaration"
            raise ValueError(message)
        if len(self.target_paths) > _MAX_PATHS or any(
            not isinstance(p, str) or not p or len(p) > _MAX_PATH_LENGTH for p in self.target_paths
        ):
            message = "invalid target paths"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Host policy result; only explicit boolean approval permits mutation."""

    approved: bool
    reason: str = ""

    def __post_init__(self) -> None:
        """Require an actual boolean, never a truthy value."""
        if type(self.approved) is not bool or not isinstance(self.reason, str):
            message = "invalid approval decision"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class LoopOutcome:
    """Terminal outcome; completion is reserved for a successful L2 decision."""

    status: str
    iterations: int
    feedback: str = ""
    budget_dimension: str = ""


class ActorProtocol(Protocol):
    """Trusted host actor separates nonmutating planning from mutation."""

    def propose(self, context: ActorContext) -> ActorProposal:
        """Plan without mutation and declare usage."""

    def apply(self, proposal: ActorProposal) -> None:
        """Apply a previously approved mutating proposal."""


class ApprovalGateProtocol(Protocol):
    """Host-provided authorization boundary for a specific proposal."""

    def approve(self, task: TaskSpec, proposal: ActorProposal) -> ApprovalDecision:
        """Explicitly approve or deny the proposed mutation."""
