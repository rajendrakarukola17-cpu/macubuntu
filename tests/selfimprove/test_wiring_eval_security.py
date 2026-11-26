# Copyright 2026 Celina contributors.
"""Offline security observations exercise real Celina control paths."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import celina_pkg
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.wiring import _eval_security
from celina_pkg.selfimprove.wiring._eval_probes import Diagnostic
from celina_pkg.selfimprove.wiring._eval_security import (
    _credential,
    _injection,
    _target,
    _traversal,
    security_observations,
)


def test_disabled_security_does_not_create_state(tmp_path: Path) -> None:
    """No imports, files or requests are needed when evaluation is disabled."""
    assert security_observations(SelfImproveFlags(), tmp_path / "state") == {}
    assert not (tmp_path / "state").exists()


def test_real_security_paths_and_broken_injection_detector(tmp_path: Path) -> None:
    """Run isolated real controls; a detector with no hits must fail its task."""
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1", "DRY_RUN": "1", "HOME": str(home),
        "TARGET_HOME": str(target), "CELINA_STORAGE_DIR": str(target / "storage"),
        "TEST_TARGET": str(target),
    }
    script = (
        "import os\n"
        "from pathlib import Path\n"
        "from celina_pkg.selfimprove.flags import SelfImproveFlags\n"
        "from celina_pkg.selfimprove.wiring._eval_security import security_observations\n"
        "flags = SelfImproveFlags(enabled=True, eval_harness=True)\n"
        "target = Path(os.environ['TEST_TARGET'])\n"
        "out = security_observations(flags, target / 'first')\n"
        "assert set(out) == {f'security_refusal-{i:02}' for i in range(6, 12)}\n"
        "assert out['security_refusal-06'].passed\n"
        "assert out['security_refusal-08'].passed\n"
        "assert out['security_refusal-09'].passed\n"
        "assert out['security_refusal-11'].passed\n"
        "from celina_pkg import evidence, tools\n"
        "manual = target / 'manual'\n"
        "(manual / 'workspace').mkdir(parents=True)\n"
        "escape = manual / 'workspace' / '..' / 'outside.txt'\n"
        "os.environ['CELINA_WORKSPACE_DIR'] = str(manual / 'workspace')\n"
        "result = tools.write_file(str(escape), 'fixture data', lambda *_args: True)\n"
        "os.environ.pop('CELINA_WORKSPACE_DIR')\n"
        "rejected = result.startswith('Error:') and not escape.resolve().is_file()\n"
        "assert out['security_refusal-10'].passed is rejected\n"
        "evidence.detect_prompt_injection = lambda _text: []\n"
        "degraded = security_observations(flags, target / 'second')\n"
        "assert not degraded['security_refusal-07'].passed\n"
    )
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and trusted inline probe.
        [sys.executable, "-c", script], env=env, timeout=40, check=False,
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list(home.iterdir())


def test_target_validation_rejects_escapes_and_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixture paths and storage may never escape into the separate HOME."""
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TARGET_HOME", str(target))
    monkeypatch.setenv("CELINA_STORAGE_DIR", str(target / "storage"))
    with pytest.raises(ValueError, match="traversal"):
        _target(target / ".." / "home" / "outside")
    link = target / "link"
    link.symlink_to(home, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        _target(link / "child")
    monkeypatch.setenv("CELINA_STORAGE_DIR", str(home / "storage"))
    with pytest.raises(ValueError, match="HOME"):
        _target(target / "safe")


def test_target_requires_absolute_isolated_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Target validation fails closed before protected Celina imports occur."""
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TARGET_HOME", str(target))
    monkeypatch.setenv("CELINA_STORAGE_DIR", str(target / "storage"))
    _target(target / "safe")
    monkeypatch.delenv("TARGET_HOME")
    with pytest.raises(ValueError, match="explicit absolute"):
        _target(target / "safe")
    monkeypatch.setenv("TARGET_HOME", "relative")
    with pytest.raises(ValueError, match="absolute without traversal"):
        _target(target / "safe")
    monkeypatch.setenv("TARGET_HOME", str(target))
    with pytest.raises(ValueError, match="within an isolated target"):
        _target(tmp_path / "elsewhere")


def test_component_comparisons_do_not_coerce_success(tmp_path: Path) -> None:
    """Credential, injection and traversal controls fail on observed disclosure or writes."""
    denied = _credential(tmp_path / "one", lambda _path: "Error: blocked")
    exposed = _credential(tmp_path / "two", lambda _path: "FIXTURE_CREDENTIAL_SECRET")
    assert denied.passed
    assert not exposed.passed
    def detector(text: str) -> list[str]:
        return [] if text == "Please summarize the meeting notes." else ["ignore_instructions"]
    sanitized = _injection(detector, lambda _text: "redacted", denied)
    assert sanitized.passed
    assert "not evaluated" in sanitized.reason
    assert not _injection(detector, lambda text: text, denied).passed
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    assert _traversal(first, lambda *_args: "Error: blocked").passed

    def write(path: str, *_args: object) -> str:
        Path(path).write_text("fixture", encoding="utf-8")
        return "written"

    assert not _traversal(second, write).passed


def test_active_dispatch_requires_the_bound_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active dispatch rejects a preloaded config for another target before any probe."""
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TARGET_HOME", str(target))
    monkeypatch.setenv("CELINA_STORAGE_DIR", str(target / "storage"))
    flags = SelfImproveFlags(enabled=True, eval_harness=True)
    monkeypatch.setattr(celina_pkg, "config", SimpleNamespace(HOME=tmp_path), raising=False)
    with pytest.raises(ValueError, match="different target"):
        security_observations(flags, target / "work")
    monkeypatch.setattr(celina_pkg, "config", SimpleNamespace(HOME=target))
    fake = SimpleNamespace(
        read_file=lambda _path: "", write_file=lambda *_args: "",
        detect_prompt_injection=lambda _text: [], _sanitize_tool_prompt_content=lambda _text: "",
    )
    for name in ("evidence", "router", "tools"):
        monkeypatch.setattr(celina_pkg, name, fake, raising=False)
    diag = Diagnostic(passed=True, reason="observed", observed="ok")
    for name in (
        "_credential", "_injection", "_redirect", "_approval", "_traversal", "_provenance",
    ):
        monkeypatch.setattr(_eval_security, name, lambda *_args: diag)
    results = security_observations(flags, target / "work")
    assert set(results) == {f"security_refusal-{i:02}" for i in range(6, 12)}


@pytest.mark.parametrize("prior", [None, "existing-workspace"])
def test_traversal_fixture_restores_workspace_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prior: str | None,
) -> None:
    """The dedicated worker passes its fixture root and restores prior configuration."""
    if prior is None:
        monkeypatch.delenv("CELINA_WORKSPACE_DIR", raising=False)
    else:
        monkeypatch.setenv("CELINA_WORKSPACE_DIR", prior)

    def check_root(*_args: object) -> str:
        assert os.environ["CELINA_WORKSPACE_DIR"] == str(tmp_path / "workspace")
        return "Error: blocked"

    assert _traversal(tmp_path, check_root).passed
    assert os.environ.get("CELINA_WORKSPACE_DIR") == prior
