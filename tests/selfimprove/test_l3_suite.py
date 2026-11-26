# Copyright 2026 Celina contributors.
"""Test the curated L3 suite and its strict, deterministic loader."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from celina_pkg.selfimprove.contracts import TaskSpec
from celina_pkg.selfimprove.l3.suite import EvalTask, filter_tasks, load_benchmark_suite

SUITE_SIZE = 24
MIN_SECURITY_TASKS = 6


def test_curated_suite() -> None:
    """The offline suite has unique identities and meaningful metadata."""
    tasks = load_benchmark_suite()
    assert len(tasks) == SUITE_SIZE
    assert len({task.spec.task_id for task in tasks}) == SUITE_SIZE
    assert {task.category for task in tasks} == {
        "routing",
        "security_refusal",
        "tool_args",
        "config_integrity",
        "code_repair",
    }
    assert sum(task.security_critical for task in tasks) >= MIN_SECURITY_TASKS
    assert all(isinstance(task.spec, TaskSpec) and task.expected for task in tasks)
    with pytest.raises(FrozenInstanceError):
        tasks[0].category = "changed"


@pytest.mark.parametrize(
    "document",
    [
        [],
        [{"id": "x"}],
        [
            {
                "id": "x",
                "category": "unknown",
                "prompt": "p",
                "expected": "e",
                "security_critical": False,
            },
        ],
        [
            {
                "id": "x",
                "category": "routing",
                "prompt": "p",
                "expected": "e",
                "security_critical": "false",
            },
        ],
        [
            {
                "id": "x",
                "category": "routing",
                "prompt": "p",
                "expected": "e",
                "security_critical": False,
            },
        ]
        * 2,
        {},
    ],
)
def test_invalid_suites_fail_closed(tmp_path: Path, document: object) -> None:
    """Malformed schemas, duplicates and empty collections are not accepted."""
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match=r"."):
        load_benchmark_suite(path)


def test_invalid_json_fails_closed(tmp_path: Path) -> None:
    """Invalid JSON fails predictably."""
    path = tmp_path / "suite.json"
    path.write_text("[")
    with pytest.raises(ValueError, match=r"."):
        load_benchmark_suite(path)


def test_filter_is_sorted_and_deterministic() -> None:
    """Filtering has stable task order, independent of input order."""
    tasks = load_benchmark_suite()
    first = filter_tasks(
        tuple(reversed(tasks)), category="security_refusal", security_critical=True,
    )
    assert first
    assert first == filter_tasks(tasks, category="security_refusal", security_critical=True)
    assert [item.spec.task_id for item in first] == sorted(item.spec.task_id for item in first)
    with pytest.raises(ValueError, match=r"."):
        filter_tasks(tasks, category="not-a-category")


def test_suite_additional_invalid_values(tmp_path: Path) -> None:
    """Invalid types, text and inaccessible suite paths fail closed."""
    with pytest.raises(ValueError, match="category"):
        EvalTask(TaskSpec("id", "goal"), "invalid", "criterion", security_critical=False)
    with pytest.raises(ValueError, match="criterion"):
        EvalTask(TaskSpec("id", "goal"), "routing", "", security_critical=False)
    with pytest.raises(ValueError, match="boolean"):
        filter_tasks(load_benchmark_suite(), security_critical="yes")
    with pytest.raises(ValueError, match="cannot load"):
        load_benchmark_suite(tmp_path / "missing")
    path = tmp_path / "invalid.json"
    path.write_text('{"irrelevant": []}')
    with pytest.raises(ValueError, match="nonempty"):
        load_benchmark_suite(path)
    path.write_text('[{"id":"x","category":[],"prompt":"x","expected":"y","security_critical":false}]')
    with pytest.raises(ValueError, match="invalid task"):
        load_benchmark_suite(path)
