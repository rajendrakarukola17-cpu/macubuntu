# Copyright 2026 Celina contributors.
"""Fresh-process regression probes for Celina import-time storage behavior."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def test_real_router_uses_explicit_isolated_state(tmp_path: Path) -> None:
    """The actual router can run offline without writing the separate HOME."""
    home, target = tmp_path / "home", tmp_path / "target"
    home.mkdir()
    target.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(home),
        "TARGET_HOME": str(target),
        "CELINA_STORAGE_DIR": str(target / "storage"),
        "DRY_RUN": "1",
    }
    script = (
        "from celina_pkg.router import CelinaRouter\n"
        "r = CelinaRouter()\n"
        "d = r.route_for_autonomy('hello', "
        "{'online_probe': lambda: False, 'uno_probe': lambda: False})\n"
        "assert isinstance(d['tool_name'], str)\n"
    )
    completed = subprocess.run(  # noqa: S603  # Fixed interpreter and trusted inline probe.
        [sys.executable, "-c", script], env=env, timeout=30, check=False,
        text=True, capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not list(home.iterdir())
    assert (target / "storage" / "shared" / "backend_state.json").is_file()
