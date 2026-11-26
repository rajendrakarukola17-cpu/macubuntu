# Copyright 2026 Celina contributors.
"""Configuration benchmark observations use the real offline Celina checks."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.wiring._eval_config import (
    _duplicate,
    _file_mode,
    _missing_reference,
    config_observations,
)

if TYPE_CHECKING:
    from celina_pkg.selfimprove.wiring._token_store import TokenSpendStore

_ROOT = Path(__file__).resolve().parents[2]


def _pretend_clean(
    argv: list[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    assert cwd.is_dir()
    assert timeout > 0
    return subprocess.CompletedProcess(argv, 0, "all clear", "")


def test_disabled_does_not_import_celina_or_create_fixtures(tmp_path: Path) -> None:
    """Disabled config checks do not create state or invoke the executor."""
    def forbidden(
        _argv: list[str], *, cwd: Path, timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd is not None
        assert timeout > 0
        message = "executor must not run"
        raise AssertionError(message)

    assert config_observations(SelfImproveFlags(), tmp_path / "state", _ROOT, forbidden) == {}
    assert not (tmp_path / "state").exists()


def test_real_config_fixtures_and_degraded_checker(tmp_path: Path) -> None:
    """The real manifest checker must flag byte drift; a fake clean result must fail."""
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1", "DRY_RUN": "1", "HOME": str(home),
        "TARGET_HOME": str(target), "CELINA_STORAGE_DIR": str(target / "storage"),
        "TEST_ROOT": str(tmp_path), "REPOSITORY": str(_ROOT),
    }
    script = (
        "import os, subprocess\n"
        "from pathlib import Path\n"
        "from celina_pkg.selfimprove.flags import SelfImproveFlags\n"
        "from celina_pkg.selfimprove.wiring._eval_config import config_observations\n"
        "def run(argv, *, cwd, timeout):\n"
        "    return subprocess.run(argv, cwd=cwd, timeout=timeout, "
        "capture_output=True, text=True, check=False)\n"
        "def clean(argv, *, cwd, timeout):\n"
        "    return subprocess.CompletedProcess(argv, 0, 'all clear', '')\n"
        "flags = SelfImproveFlags(enabled=True, eval_harness=True)\n"
        "root = Path(os.environ['TEST_ROOT'])\n"
        "repository = Path(os.environ['REPOSITORY'])\n"
        "out = config_observations(flags, root / 'first', repository, run)\n"
        "assert set(out) == {'config_integrity-17', 'config_integrity-18', "
        "'config_integrity-19', 'config_integrity-20'}\n"
        "assert out['config_integrity-17'].passed\n"
        "assert out['config_integrity-19'].passed\n"
        "assert out['config_integrity-20'].passed\n"
        "bad = config_observations(flags, root / 'second', repository, clean)\n"
        "assert not bad['config_integrity-17'].passed\n"
    )
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and trusted inline probe.
        [sys.executable, "-c", script], env=env, timeout=30, check=False,
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list(home.iterdir())


def test_active_callbacks_reflect_degraded_manifest_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real config checks run with explicit isolated target state."""
    monkeypatch.setenv("TARGET_HOME", str(tmp_path / "target"))
    monkeypatch.setenv("CELINA_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("DRY_RUN", "1")
    flags = SelfImproveFlags(enabled=True, eval_harness=True)
    result = config_observations(flags, tmp_path / "fixtures", _ROOT, _pretend_clean)
    assert not result["config_integrity-17"].passed
    assert result["config_integrity-19"].passed
    assert result["config_integrity-20"].passed


def test_duplicate_and_missing_reference_degradation(tmp_path: Path) -> None:
    """Changing the actual collaborators changes the observations."""
    def deny(_path: Path) -> dict[str, object]:
        message = "duplicate"
        raise ValueError(message)

    assert _duplicate(tmp_path, deny).passed
    assert not _missing_reference(_ROOT, lambda *_args, **_kwargs: []).passed


def test_missing_fixture_approval_is_an_error(tmp_path: Path) -> None:
    """Failed issuance cannot be scored as an insecure-store rejection."""
    with pytest.raises(RuntimeError, match="approval"):
        _file_mode(
            tmp_path, lambda *_args: (None, None), cast("type[TokenSpendStore]", object),
        )
