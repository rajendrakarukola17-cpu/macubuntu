# Copyright 2026 Celina contributors.
"""Immutable contracts for isolated self-improvement layers; no I/O."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Protocol

VerificationStatus = Literal["pass", "fail", "error", "skipped"]
AttemptStatus = Literal["pending", "running", "passed", "failed", "rejected"]


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        message = f"{name} must be a nonempty string"
        raise ValueError(message)


def _count(value: int, name: str, *, positive: bool = False) -> None:
    if type(value) is not int or value < (1 if positive else 0):
        bound = "greater than zero" if positive else "at least zero"
        message = f"{name} must be an integer {bound}"
        raise ValueError(message)


def _seconds(value: float, name: str, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        message = f"{name} must be finite"
        raise ValueError(message)
    if value < 0 or (positive and value == 0):
        message = f"{name} must be {'greater than zero' if positive else 'at least zero'}"
        raise ValueError(message)


def _score(value: float) -> None:
    _seconds(value, "score")
    if value > 1:
        message = "score must be at most one"
        raise ValueError(message)


def _status(value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        message = "invalid status"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """A bounded task identity and its goal."""

    task_id: str
    goal: str

    def __post_init__(self) -> None:
        """Validate contract values after construction."""
        _required(self.task_id, "task_id")
        _required(self.goal, "goal")


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    """Hard caps; zero file writes or subprocesses means read-only."""

    max_iterations: int
    max_tokens: int
    max_wall_seconds: float
    max_file_writes: int
    max_subprocesses: int

    def __post_init__(self) -> None:
        """Validate contract values after construction."""
        _count(self.max_iterations, "max_iterations", positive=True)
        _count(self.max_tokens, "max_tokens", positive=True)
        _seconds(self.max_wall_seconds, "max_wall_seconds", positive=True)
        _count(self.max_file_writes, "max_file_writes")
        _count(self.max_subprocesses, "max_subprocesses")


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """Usage and remaining caps; exhausted means at least one cap is reached."""

    iterations: int
    tokens: int
    wall_seconds: float
    file_writes: int
    subprocesses: int
    remaining_iterations: int
    remaining_tokens: int
    remaining_wall_seconds: float
    remaining_file_writes: int
    remaining_subprocesses: int
    exhausted: bool

    def __post_init__(self) -> None:
        """Validate contract values after construction."""
        for name in (
            "iterations", "tokens", "file_writes", "subprocesses",
            "remaining_iterations", "remaining_tokens",
            "remaining_file_writes", "remaining_subprocesses",
        ):
            _count(getattr(self, name), name)
        _seconds(self.wall_seconds, "wall_seconds")
        _seconds(self.remaining_wall_seconds, "remaining_wall_seconds")
        if type(self.exhausted) is not bool:
            message = "exhausted must be a bool"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class VerifierResult:
    """Result of a single verifier with a normalized score."""

    verifier_id: str
    status: VerificationStatus
    score: float
    detail: str = ""

    def __post_init__(self) -> None:
        """Validate contract values after construction."""
        _required(self.verifier_id, "verifier_id")
        _status(self.status, ("pass", "fail", "error", "skipped"))
        _score(self.score)


@dataclass(frozen=True, slots=True)
class CritiqueResult:
    """A task critique and normalized score."""

    task_id: str
    score: float
    feedback: str

    def __post_init__(self) -> None:
        """Validate contract values after construction."""
        _required(self.task_id, "task_id")
        _score(self.score)


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """A task attempt and recorded token use."""

    attempt_id: str
    task_id: str
    status: AttemptStatus
    tokens_used: int

    def __post_init__(self) -> None:
        """Validate contract values after construction."""
        _required(self.attempt_id, "attempt_id")
        _required(self.task_id, "task_id")
        _status(self.status, ("pending", "running", "passed", "failed", "rejected"))
        _count(self.tokens_used, "tokens_used")


@dataclass(frozen=True, slots=True)
class PatchProposal:
    """A proposal value; this type neither applies nor approves a patch."""

    proposal_id: str
    task_id: str
    patch: str

    def __post_init__(self) -> None:
        """Validate contract values after construction."""
        _required(self.proposal_id, "proposal_id")
        _required(self.task_id, "task_id")
        _required(self.patch, "patch")


@dataclass(frozen=True, slots=True)
class EvalReport:
    """A task evaluation aggregate."""

    task_id: str
    status: VerificationStatus
    score: float
    attempts: int

    def __post_init__(self) -> None:
        """Validate contract values after construction."""
        _required(self.task_id, "task_id")
        _status(self.status, ("pass", "fail", "error", "skipped"))
        _score(self.score)
        _count(self.attempts, "attempts")


class BudgetGuardProtocol(Protocol):
    """Operations available to budget consumers."""

    def consume_iteration(self) -> None: ...  # noqa: D102  # Interface is documented by its protocol.
    def consume_tokens(self, n: int) -> None: ...  # noqa: D102  # Interface is documented by its protocol.
    def consume_file_write(self, n: int = 1) -> None: ...  # noqa: D102  # Interface is documented by its protocol.
    def consume_subprocess(self, n: int = 1) -> None: ...  # noqa: D102  # Interface is documented by its protocol.
    def check_wall_clock(self) -> None: ...  # noqa: D102  # Interface is documented by its protocol.
    def snapshot(self) -> BudgetSnapshot: ...  # noqa: D102  # Interface is documented by its protocol.


class VerifierProtocol(Protocol):
    """Contract for an independent task verifier."""

    def verify(self, task: TaskSpec) -> VerifierResult: ...  # noqa: D102  # Interface is documented by its protocol.


class MemoryStoreProtocol(Protocol):
    """Contract for storage of task attempt records."""

    def store(self, record: AttemptRecord) -> None: ...  # noqa: D102  # Interface is documented by its protocol.
    def lookup(self, task_id: str) -> tuple[AttemptRecord, ...]: ...  # noqa: D102  # Interface is documented by its protocol.


class CriticProtocol(Protocol):
    """Contract for generating a task critique."""

    def critique(self, task: TaskSpec, attempts: tuple[AttemptRecord, ...]) -> CritiqueResult: ...  # noqa: D102  # Interface is documented by its protocol.
