# Copyright 2026 Celina contributors.
"""The evaluator worker must block networking without extra runtime packages."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from celina_pkg.selfimprove.wiring import _eval_offline as offline
from celina_pkg.selfimprove.wiring._eval_offline import _architecture

_ARM_AUDIT_ARCH = 0xC00000B7
_X86_AUDIT_ARCH = 0xC000003E

if TYPE_CHECKING:
    from pathlib import Path


def test_worker_filter_blocks_network_sockets_in_fresh_process(tmp_path: Path) -> None:
    """The irreversible filter is installed only in a fresh child process."""
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(home),
    }
    script = (
        "import socket\n"
        "from celina_pkg.selfimprove.wiring._eval_offline import install_network_filter\n"
        "s = socket.socket(socket.AF_UNIX); s.close()\n"
        "install_network_filter()\n"
        "for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):\n"
        "    try: socket.socket(family)\n"
        "    except PermissionError: pass\n"
        "    else: raise AssertionError('network socket was permitted')\n"
    )
    result = subprocess.run(  # noqa: S603  # Fixed interpreter and trusted inline probe.
        [sys.executable, "-c", script], env=env, timeout=10, check=False,
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert not list(home.iterdir())


def test_filter_architecture_and_failure_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported platforms and both prctl failures abort before execution."""
    monkeypatch.setattr(offline.sys, "platform", "other")
    with pytest.raises(OSError, match="requires Linux"):
        _architecture()
    monkeypatch.setattr(offline.sys, "platform", "linux")
    monkeypatch.setattr(offline.platform, "machine", lambda: "unknown")
    with pytest.raises(OSError, match="unsupported"):
        _architecture()
    monkeypatch.setattr(offline.platform, "machine", lambda: "aarch64")
    assert _architecture()[0] == _ARM_AUDIT_ARCH
    monkeypatch.setattr(offline.platform, "machine", lambda: "x86_64")
    assert _architecture()[0] == _X86_AUDIT_ARCH

    class Prctl:
        def __init__(self, outcomes: tuple[int, ...]) -> None:
            self.outcomes = iter(outcomes)
            self.argtypes: object = None
            self.restype: object = None

        def __call__(self, *_args: object) -> int:
            return next(self.outcomes)

    class Libc:
        def __init__(self, outcomes: tuple[int, ...]) -> None:
            self.prctl = Prctl(outcomes)

    monkeypatch.setattr(offline.ctypes, "CDLL", lambda *_args, **_kwargs: Libc((1,)))
    with pytest.raises(OSError, match="no-new-privileges"):
        offline.install_network_filter()
    monkeypatch.setattr(offline.ctypes, "CDLL", lambda *_args, **_kwargs: Libc((0, 1)))
    with pytest.raises(OSError, match="offline seccomp"):
        offline.install_network_filter()
    monkeypatch.setattr(offline.ctypes, "CDLL", lambda *_args, **_kwargs: Libc((0, 0)))
    offline.install_network_filter()
