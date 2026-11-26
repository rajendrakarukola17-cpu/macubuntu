# Copyright 2026 Celina contributors.
"""Strict loading of the offline, curated evaluation task descriptions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.contracts import TaskSpec

if TYPE_CHECKING:
    from collections.abc import Sequence

CATEGORIES = frozenset(
    {"routing", "security_refusal", "tool_args", "config_integrity", "code_repair"},
)
_FIELDS = frozenset({"id", "category", "prompt", "expected", "security_critical"})


@dataclass(frozen=True, slots=True)
class EvalTask:
    """A task contract plus immutable benchmark classification and criterion."""

    spec: TaskSpec
    category: str
    expected: str
    security_critical: bool

    def __post_init__(self) -> None:
        """Reject invalid metadata even for caller-constructed tasks."""
        if not isinstance(self.spec, TaskSpec) or self.category not in CATEGORIES:
            message = "invalid task or category"
            raise ValueError(message)
        if not isinstance(self.expected, str) or not self.expected.strip():
            message = "expected criterion must be nonempty"
            raise ValueError(message)
        if type(self.security_critical) is not bool:
            message = "security_critical must be boolean"
            raise ValueError(message)


def _parse_task(item: object) -> EvalTask:
    if not isinstance(item, dict) or set(item) != _FIELDS:
        message = "invalid task fields"
        raise ValueError(message)
    return EvalTask(
        TaskSpec(item["id"], item["prompt"]),
        item["category"],
        item["expected"],
        item["security_critical"],
    )


def load_benchmark_suite(path: Path | None = None) -> tuple[EvalTask, ...]:
    """Read explicit JSON, or the bundled suite; invalid data raises ValueError."""
    source = Path(__file__).with_name("benchmark_tasks.json") if path is None else path
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        message = "cannot load benchmark suite"
        raise ValueError(message) from exc
    if not isinstance(data, list) or not data:
        message = "suite must be a nonempty array"
        raise ValueError(message)
    try:
        tasks = tuple(_parse_task(item) for item in data)
    except (TypeError, KeyError) as exc:
        message = "invalid task values"
        raise ValueError(message) from exc
    if len({task.spec.task_id for task in tasks}) != len(tasks):
        message = "duplicate task ID"
        raise ValueError(message)
    return tuple(sorted(tasks, key=lambda task: task.spec.task_id))


def filter_tasks(
    tasks: Sequence[EvalTask],
    *,
    category: str | None = None,
    security_critical: bool | None = None,
) -> tuple[EvalTask, ...]:
    """Select and sort tasks by identity, independently of input order."""
    if category is not None and category not in CATEGORIES:
        message = "unknown category"
        raise ValueError(message)
    if security_critical is not None and type(security_critical) is not bool:
        message = "security filter must be boolean"
        raise ValueError(message)
    return tuple(
        sorted(
            (
                task
                for task in tasks
                if (category is None or task.category == category)
                and (security_critical is None or task.security_critical == security_critical)
            ),
            key=lambda task: task.spec.task_id,
        ),
    )
