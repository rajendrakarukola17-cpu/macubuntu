# Copyright 2026 Celina contributors.
"""Concrete tool argument observations over injectable actual Celina behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING, Any, cast

import pytest

from celina_pkg.selfimprove.wiring._eval_arguments import (
    _read_case,
    _validation,
    argument_observations,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def test_permissive_validator_and_escaping_reader_are_not_passes(tmp_path: Path) -> None:
    """Unrecognized or ill-typed arguments and a disclosed symlink target must fail."""
    def accepts(_name: str, _args: dict[str, Any], _schema: dict[str, Any]) -> tuple[bool, str]:
        return True, ""

    def reader(path: str) -> str:
        return "OUTSIDE_SECRET" if path.endswith("escape.txt") else "SAFE_CONTENT"

    out = argument_observations(accepts, reader, tmp_path / "workspace", tmp_path / "external")
    assert {key for key, item in out.items() if item.passed} == {"tool_args-13"}


def test_strict_validator_and_contained_reader_pass(tmp_path: Path) -> None:
    """Reject wrong, absent and unknown arguments while reading only the safe file."""
    def strict(_name: str, args: dict[str, Any], _schema: dict[str, Any]) -> tuple[bool, str]:
        allowed = {"path", "limit"}
        good = set(args) <= allowed and "path" in args and type(args.get("limit")) is int
        return good, "" if good else "invalid"

    def reader(path: str) -> str:
        return "Error: outside home" if path.endswith("escape.txt") else "SAFE_CONTENT"

    out = argument_observations(strict, reader, tmp_path / "workspace", tmp_path / "external")
    assert all(item.passed for item in out.values())


def test_real_guardrails_and_reader_in_fresh_process(tmp_path: Path) -> None:
    """The diagnostic vector reflects the actual isolated Celina validator and reader."""
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1", "DRY_RUN": "1", "HOME": str(home),
        "TARGET_HOME": str(target), "CELINA_STORAGE_DIR": str(target / "storage"),
        "TEST_ROOT": str(tmp_path),
    }
    script = (
        "import os\n"
        "from pathlib import Path\n"
        "from celina_pkg import guardrails, tools\n"
        "from celina_pkg.selfimprove.wiring._eval_arguments import argument_observations\n"
        "root = Path(os.environ['TEST_ROOT'])\n"
        "workspace = root / 'target' / 'workspace'\n"
        "workspace.mkdir()\n"
        "os.chdir(workspace)\n"
        "out = argument_observations(guardrails.validate_tool_arguments, tools.read_file, "
        "workspace, root / 'external')\n"
        "schema = {'read': {'parameters': {'properties': {'path': {'type': 'string'}, "
        "'limit': {'type': 'integer'}}, 'required': ['path', 'limit'], "
        "'additionalProperties': False}}}\n"
        "ok, _ = guardrails.validate_tool_arguments('read', "
        "{'path': 'safe.txt', 'limit': 1, 'extra': 1}, schema)\n"
        "assert out['tool_args-15'].passed is (not ok)\n"
        "assert out['tool_args-13'].passed\n"
        "assert out['tool_args-16'].passed\n"
    )
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and trusted inline probe.
        [sys.executable, "-c", script], env=env, timeout=30, check=False,
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list(home.iterdir())


def test_rejected_paths_and_malformed_callbacks_fail_closed() -> None:
    """A rejection avoids the reader, and malformed collaborator results are errors."""
    def denied(_name: str, _args: dict[str, Any], _schema: dict[str, Any]) -> tuple[bool, str]:
        return False, "denied"

    def unexpected(_path: str) -> str:
        message = "reader must not be called"
        raise AssertionError(message)

    assert not _read_case(denied, unexpected, "safe.txt", escape=False).passed
    assert _read_case(denied, unexpected, "escape.txt", escape=True).passed
    invalid = cast("Callable[..., tuple[bool, str]]", lambda *_args: (1, ""))
    with pytest.raises(ValueError, match="validator"):
        _validation(invalid, {}, expect_valid=False)
    with pytest.raises(ValueError, match="validator"):
        _read_case(invalid, unexpected, "safe.txt", escape=False)
    malformed = cast("Callable[[str], str]", lambda _path: None)
    allowed = cast("Callable[..., tuple[bool, str]]", lambda *_args: (True, ""))
    with pytest.raises(TypeError, match="reader"):
        _read_case(allowed, malformed, "safe.txt", escape=False)
