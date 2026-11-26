# Copyright 2026 Celina contributors.
"""Default-off command line for explicitly configured self-improvement."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from celina_pkg.selfimprove.flags import load_flags

from .facade import SelfImproveRuntime

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Opt-in offline self-improvement")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    for name in ("eval", "optimize", "patch"):
        command = commands.add_parser(name)
        command.add_argument("--state-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    """Run status or an explicitly enabled operation with an explicit state path."""
    args = _arguments(argv)
    flags = load_flags(env=env)
    if args.command == "status":
        sys.stdout.write(json.dumps({"enabled": flags.enabled}) + "\n")
        return 0
    runtime = SelfImproveRuntime(flags, args.state_dir)
    if not runtime.enabled_for(args.command):
        sys.stderr.write(f"{args.command} blocked by feature flags\n")
        return 2
    if args.state_dir is None:
        sys.stderr.write("active commands require --state-dir\n")
        return 2
    if args.command != "eval":
        sys.stderr.write(f"{args.command} requires trusted host collaborators\n")
        return 2
    try:
        result = runtime.evaluate()
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"evaluation blocked: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":

    raise SystemExit(main())
