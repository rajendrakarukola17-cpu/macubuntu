"""Tests for Item 4: opt-in MCP server loader (`celina config add-mcp / revoke-mcp / remove-mcp`)."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_pkg import config, mcp_client, state_ops  # noqa: E402


class _DummyProc:
    def __init__(self):
        self.stopped = False

    def is_alive(self) -> bool:
        return not self.stopped

    def stop(self) -> None:
        self.stopped = True


def test_mcp_add_approval_isolation_revoke_kill_switch_and_remove(tmp_path: Path):
    cfg_file = tmp_path / "mcp-servers.json"

    # 1. Fail-closed without explicit approval
    with pytest.raises(PermissionError, match="approval"):
        mcp_client.add_mcp_server("sqlite", "uvx mcp-server-sqlite", cfg_path=cfg_file)

    with pytest.raises(PermissionError, match="Cancelled"):
        mcp_client.add_mcp_server(
            "sqlite",
            "uvx mcp-server-sqlite",
            cfg_path=cfg_file,
            approval_cb=lambda _t, _s: False,
        )

    # 2. Shell injection metacharacters rejected even with approval
    with pytest.raises(ValueError, match="metacharacters"):
        mcp_client.add_mcp_server(
            "evil",
            "uvx mcp-server; rm -rf /",
            cfg_path=cfg_file,
            approve=True,
        )

    # 3. Approved opt-in add creates isolated 0600 entry with allowlist + kill switch
    res = mcp_client.add_mcp_server(
        "sqlite",
        "uvx mcp-server-sqlite --db-path /tmp/demo.db",
        cfg_path=cfg_file,
        approval_cb=lambda _t, _s: True,
        description="Local SQLite MCP",
    )
    assert res["ok"] is True
    assert stat.S_IMODE(os.stat(cfg_file).st_mode) == 0o600
    entry = res["entry"]
    assert entry["opt_in"] is True
    assert entry["isolated"] is True
    assert entry["kill_switch"] is False
    assert entry["enabled"] is True
    assert entry["allowlist_entry"]["command"] == ["uvx", "mcp-server-sqlite", "--db-path", "/tmp/demo.db"]
    assert entry["explicit_auto_approve_tools"] == []

    # 4. Revoke triggers kill switch: stops live subprocess + blocks all future calls
    mgr = mcp_client.MCPManager(cfg_path=cfg_file)
    dummy = _DummyProc()
    mgr._procs["sqlite"] = dummy  # type: ignore[assignment]

    rev = mcp_client.revoke_mcp_server("sqlite", cfg_path=cfg_file, manager=mgr)
    assert rev["ok"] is True
    assert rev["stopped_process"] is True
    assert dummy.stopped is True

    blocked_call = mgr.call_tool("sqlite", "query", {"sql": "SELECT 1"}, approval_cb=lambda _t, _d: True)
    assert "revoked" in blocked_call.lower()
    assert "REVOKED (kill-switch)" in mgr.list_servers()

    # 5. Remove deletes entry cleanly
    rem = mcp_client.remove_mcp_server("sqlite", cfg_path=cfg_file, manager=mgr)
    assert rem["ok"] is True
    loaded = mcp_client.load_mcp_config(cfg_file)
    assert "sqlite" not in loaded["servers"]


def test_celina_config_cli_add_revoke_remove_mcp(tmp_path: Path, monkeypatch, capsys):
    cfg_file = tmp_path / "mcp-servers.json"
    monkeypatch.setattr(config, "MCP_CONFIG_FILE", cfg_file)

    # Non-TTY without --yes fails closed
    rc_deny = state_ops.run_cli(["config", "add-mcp", "git", "uvx mcp-server-git"])
    assert rc_deny == 1
    assert "Approval required" in capsys.readouterr().err

    # With --yes adds isolated revocable MCP server
    rc_add = state_ops.run_cli(["config", "add-mcp", "git", "uvx mcp-server-git", "--yes"])
    assert rc_add == 0
    out_add = json.loads(capsys.readouterr().out)
    assert out_add["ok"] is True and out_add["name"] == "git"

    # list-mcp shows server
    assert state_ops.run_cli(["config", "list-mcp"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert "git" in listed and listed["git"]["enabled"] is True

    # revoke-mcp flips kill switch
    assert state_ops.run_cli(["config", "revoke-mcp", "git"]) == 0
    rev = json.loads(capsys.readouterr().out)
    assert rev["ok"] is True and rev["action"] == "revoked"

    # remove-mcp cleans up
    assert state_ops.run_cli(["config", "remove-mcp", "git"]) == 0
    rem = json.loads(capsys.readouterr().out)
    assert rem["ok"] is True and rem["action"] == "removed"
    assert mcp_client.load_mcp_config(cfg_file)["servers"] == {}
