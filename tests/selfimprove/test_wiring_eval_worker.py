# Copyright 2026 Celina contributors.
"""Worker boundaries and failure behavior under isolated fake collaborators."""

from __future__ import annotations

import socket
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

import celina_pkg
from celina_pkg.selfimprove.flags import SelfImproveFlags
from celina_pkg.selfimprove.wiring import _eval_worker as worker
from celina_pkg.selfimprove.wiring._eval_probes import Diagnostic

if TYPE_CHECKING:
    from pathlib import Path

from celina_pkg.selfimprove.wiring._eval_worker import (
    _collect,
    _manifest_executor,
    _offline_audit,
    _simulated_verifier,
)

_BLOCKED = 2


def test_python_audit_rejects_network_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Network events fail closed; unrelated and local events remain permitted."""
    with pytest.raises(PermissionError, match="lookup"):
        _offline_audit("socket.getaddrinfo", ())
    with pytest.raises(PermissionError, match="sockets"):
        _offline_audit("socket.__new__", (None, socket.AF_INET))
    _offline_audit("socket.__new__", (None, socket.AF_UNIX))
    _offline_audit("other", ())

    class Socket:
        family = socket.AF_INET

    monkeypatch.setattr(worker.socket, "socket", Socket)
    with pytest.raises(PermissionError, match="connection"):
        _offline_audit("socket.connect", (Socket(),))
    Socket.family = socket.AF_UNIX
    _offline_audit("socket.connect", (Socket(),))
    _offline_audit("socket.connect", (object(),))


def test_simulated_verifier_and_real_manifest_executor(tmp_path: Path) -> None:
    """Simulations report distinct outcomes; the manifest launcher uses fixed argv."""
    for folder in ("pytest-case", "bandit-case"):
        (tmp_path / folder).mkdir()
    assert _simulated_verifier(["pytest"], cwd=tmp_path / "pytest-case", timeout=1).returncode == 1
    assert _simulated_verifier(["pytest"], cwd=tmp_path / "bandit-case", timeout=1).returncode == 0
    assert _simulated_verifier(["mypy"], cwd=tmp_path, timeout=1).returncode == 1
    assert "MEDIUM" in _simulated_verifier(["bandit"], cwd=tmp_path, timeout=1).stdout
    with pytest.raises(ValueError, match="fixture"):
        _simulated_verifier(["pytest"], cwd=tmp_path, timeout=0)
    process = _manifest_executor([sys.executable, "-c", "print('ok')"], cwd=tmp_path, timeout=5)
    assert process.stdout.strip() == "ok"


def test_collection_rejects_wrong_loaded_target_and_dispatches_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixture composition requires the already-loaded Celina target to match."""
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(worker, "_target", lambda _work: None)
    monkeypatch.setattr(celina_pkg, "config", SimpleNamespace(HOME=tmp_path), raising=False)
    with pytest.raises(ValueError, match="different target"):
        _collect(target)
    monkeypatch.setattr(celina_pkg, "config", SimpleNamespace(HOME=target))
    def dummy(*_args: object, **_kwargs: object) -> None:
        pass
    monkeypatch.setattr(
        celina_pkg, "guardrails", SimpleNamespace(validate_tool_arguments=dummy), raising=False,
    )
    monkeypatch.setattr(celina_pkg, "tools", SimpleNamespace(read_file=dummy), raising=False)
    router = SimpleNamespace(CelinaRouter=lambda: SimpleNamespace(route_for_autonomy=dummy))
    monkeypatch.setattr(celina_pkg, "router", router, raising=False)
    diag = Diagnostic(passed=True, reason="fixture", observed="ok")
    for name in (
        "routing_observations", "security_observations", "argument_observations",
        "config_observations", "repair_observations",
    ):
        monkeypatch.setattr(worker, name, lambda *_args: {"fixture": diag})
    monkeypatch.setattr(worker.os, "chdir", lambda _path: None)
    assert _collect(target)


def test_worker_main_is_flag_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """Default-off worker does not install filters or call probes."""
    monkeypatch.setattr(worker, "load_flags", SelfImproveFlags)
    assert worker.main() == _BLOCKED
    monkeypatch.setattr(
        worker, "load_flags", lambda: SelfImproveFlags(enabled=True, eval_harness=True),
    )
    monkeypatch.setattr(worker, "install_network_filter", lambda: None)
    monkeypatch.setattr(worker.sys, "addaudithook", lambda _hook: None)
    monkeypatch.setenv("TARGET_HOME", str(tmp_path))
    monkeypatch.setattr(worker, "_collect", lambda _target: {
        "one": Diagnostic(passed=True, reason="fixture", observed="ok"),
    })
    assert worker.main() == 0
    assert '"one"' in capsys.readouterr().out
