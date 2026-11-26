"""Typed CLI must not trust coarse subcommand labels for real execution."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "features/celina"))
from celina_pkg import cli_bridge


@pytest.mark.parametrize("tool,sub,args", [
    ("git", "status", []),
    ("git", "branch", ["-D", "fixture"]),
    ("git", "commit", ["-m", "fixture"]),
    ("gh", "pr", ["create"]),
    ("docker", "stop", ["fixture"]),
    ("systemctl", "restart", ["fixture.service"]),
])
def test_typed_cli_denies_before_lookup_or_execution_without_broker(monkeypatch, tool, sub, args):
    calls = []
    monkeypatch.setattr(cli_bridge.shutil, "which", lambda *a: calls.append(("which", a)) or "/fixture")
    monkeypatch.setattr(cli_bridge.command_runner, "run_command", lambda *a, **k: calls.append(("run", a)))
    callback = lambda *_args: calls.append("approval") or True
    result = cli_bridge.run_typed_cli(tool, sub, args, approval_cb=callback)
    assert result.startswith("Denied:") and "broker" in result.lower()
    assert not calls


def test_invalid_tool_still_rejected_without_execution(monkeypatch):
    monkeypatch.setattr(cli_bridge.command_runner, "run_command", lambda *_a, **_k: pytest.fail("unexpected run"))
    assert cli_bridge.run_typed_cli("not_a_tool", "run").startswith("Blocked:")
