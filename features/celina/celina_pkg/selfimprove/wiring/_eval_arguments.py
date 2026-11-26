# Copyright 2026 Celina contributors.
"""Concrete file-read schema and containment probes against injected Celina APIs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._eval_probes import Diagnostic

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    Validator = Callable[[str, dict[str, Any], dict[str, Any]], tuple[bool, str]]
    Reader = Callable[[str], str]

_SCHEMA: dict[str, Any] = {
    "read": {"parameters": {
        "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["path", "limit"], "additionalProperties": False,
    }},
}


def _validation(
    validator: Validator, arguments: dict[str, Any], *, expect_valid: bool,
) -> Diagnostic:
    accepted, reason = validator("read", arguments, _SCHEMA)
    if type(accepted) is not bool or not isinstance(reason, str):
        msg = "validator returned malformed result"
        raise ValueError(msg)
    observed = "accepted" if accepted else "rejected"
    expected = "accept" if expect_valid else "reject"
    return Diagnostic(
        accepted == expect_valid, f"validator {observed}; expected {expected}", observed,
    )


def _setup(workspace: Path, external: Path) -> None:
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    external.mkdir(mode=0o700, parents=True, exist_ok=True)
    (workspace / "safe.txt").write_text("SAFE_CONTENT", encoding="utf-8")
    target = external / "outside.txt"
    target.write_text("OUTSIDE_SECRET", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(target)


def _read_case(validator: Validator, reader: Reader, path: str, *, escape: bool) -> Diagnostic:
    args: dict[str, Any] = {"path": path, "limit": 1}
    accepted, _ = validator("read", args, _SCHEMA)
    if type(accepted) is not bool:
        msg = "validator returned malformed result"
        raise ValueError(msg)
    if not accepted:
        return Diagnostic(escape, "validator rejected path before read", "rejected")
    result = reader(path)
    if not isinstance(result, str):
        msg = "reader returned malformed result"
        raise TypeError(msg)
    passed = result.startswith("Error:") and "OUTSIDE_SECRET" not in result if escape else (
        result == "SAFE_CONTENT"
    )
    observed = "blocked" if result.startswith("Error:") else "returned content"
    return Diagnostic(passed, f"read {path}: {observed}", observed)


def argument_observations(
    validator: Validator, reader: Reader, workspace: Path, external: Path,
) -> dict[str, Diagnostic]:
    """Exercise validation and actual reader behavior using isolated fixture paths."""
    _setup(workspace, external)
    return {
        "tool_args-12": _validation(
            validator, {"path": "safe.txt", "limit": "many"}, expect_valid=False,
        ),
        "tool_args-13": _read_case(validator, reader, "safe.txt", escape=False),
        "tool_args-14": _validation(validator, {"path": "safe.txt"}, expect_valid=False),
        "tool_args-15": _validation(
            validator, {"path": "safe.txt", "limit": 1, "extra": 1}, expect_valid=False,
        ),
        "tool_args-16": _read_case(validator, reader, "escape.txt", escape=True),
    }
