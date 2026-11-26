# Copyright 2026 Celina contributors.
"""One isolated, offline subprocess for concrete Celina benchmark fixtures."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.flags import load_flags

from ._eval_arguments import argument_observations
from ._eval_config import config_observations
from ._eval_offline import install_network_filter
from ._eval_repairs import repair_observations
from ._eval_routing import routing_observations
from ._eval_security import _target, security_observations

if TYPE_CHECKING:
    from ._eval_probes import Diagnostic


def _offline_audit(event: str, arguments: tuple[object, ...]) -> None:
    """Block Python socket networking and DNS; not an OS sandbox."""
    if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        msg = "network lookup disabled"
        raise PermissionError(msg)
    if event == "socket.__new__" and len(arguments) > 1 and arguments[1] != socket.AF_UNIX:
        msg = "network sockets disabled"
        raise PermissionError(msg)
    if event in {"socket.connect", "socket.connect_ex"} and arguments:
        sock = arguments[0]
        if isinstance(sock, socket.socket) and sock.family != socket.AF_UNIX:
            msg = "network connection disabled"
            raise PermissionError(msg)


def _manifest_executor(
    argv: list[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # Fixed Python and trusted copied manifest checker.
        argv, cwd=cwd, timeout=timeout, capture_output=True, text=True, check=False,
    )


def _simulated_verifier(
    argv: list[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    if timeout <= 0 or not cwd.is_dir():
        msg = "invalid verifier fixture"
        raise ValueError(msg)
    if argv[0] == "bandit":
        return subprocess.CompletedProcess(argv, 0, '{"results":[{"issue_severity":"MEDIUM"}]}', "")
    failed = argv[0] == "mypy" or (argv[0] == "pytest" and cwd.name == "pytest-case")
    return subprocess.CompletedProcess(argv, int(failed), "", "simulated verifier result")


def _collect(target: Path) -> dict[str, Diagnostic]:
    flags = load_flags()
    _target(target / "security")
    from celina_pkg import config  # noqa: PLC0415  # Enabled and paths checked before import.

    if target != config.HOME:
        msg = "Celina was configured with a different target"
        raise ValueError(msg)
    from celina_pkg import guardrails, router, tools  # noqa: PLC0415  # Opt-in and paths checked.

    repository = Path(__file__).resolve().parents[5]
    result = routing_observations(router.CelinaRouter().route_for_autonomy)
    result.update(security_observations(flags, target / "security"))
    workspace = target / "tool-args" / "workspace"
    workspace.mkdir(mode=0o700, parents=True)
    os.chdir(workspace)
    result.update(argument_observations(
        guardrails.validate_tool_arguments, tools.read_file, workspace, target.parent / "external",
    ))
    result.update(config_observations(
        flags, target / "config-fixtures", repository, _manifest_executor,
    ))
    result.update(repair_observations(target / "repair-fixtures", _simulated_verifier))
    return result


def main() -> int:
    """Run only under explicit flags with process-local offline auditing."""
    flags = load_flags()
    if not flags.enabled or not flags.eval_harness:
        return 2
    install_network_filter()
    sys.addaudithook(_offline_audit)
    target = Path(os.environ.get("TARGET_HOME", ""))
    observations = _collect(target)
    sys.stdout.write(json.dumps({key: asdict(value) for key, value in observations.items()}))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
