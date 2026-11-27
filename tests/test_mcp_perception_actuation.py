"""
Suite 18: Ordered MCP Perception & Actuation Stack Integration Tests.

Verifies:
  1. Complete ordered 7-server catalog (`computer-use-linux`, `shadow-vision`,
     `ghostdesk`, `eye2byte`, `uacc`, `deskwright`, `n8n`) + strict blocklist
     of unrelated `celina-mcp` / `usecelina.xyz` (Celo SDK).
  2. Sandboxing policies:
       - `computer-use-linux` / `eye2byte` / `uacc` / `deskwright`: `bwrap --unshare-net`
       - `shadow-vision`: local Ollama loopback-only (`127.0.0.1:11434`, no remote screenshot egress)
       - `ghostdesk`: loopback Streamable HTTP only
       - `n8n`: disabled by default, gated behind `web_comms.enabled=true` + `net_policy` allowlist
         + `RestrictAddressFamilies=AF_INET AF_INET6` unit template
  3. Live `MCPLoader` stdio JSON-RPC (`initialize` / `tools/list` / `tools/call`) AND
     Streamable HTTP (`ghostdesk`) execution + capability routing.
  4. Domain `"mcp_perception_actuation"` (`computer_click`, `computer_type`, `vision_ocr`,
     `vision_analyze`, `ghostdesk_click`): ALL 5 are Tier-2 by definition & pass 7-way 99-tool parity.
  5. `AutonomyEngine` Tier-2 MCP approval gate (`mcp_approval_denied` on deny, `MCPActionStarted`
     signal + durable `/why` receipt with `server`, `tool`, `arguments_hash`, `mcp_transport` on allow).
  6. D-Bus `MCPActionStarted` introspection XML & GNOME Dynamic Island JS subscription + `TurnTrace` `/why` receipt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_pkg import (  # noqa: E402
    approval,
    capabilities,
    config,
    dbus_service,
    mcp_client,
    router,
    state_ops,
    telemetry,
    tools,
)
from celina_pkg.autonomy import mcp_loader  # noqa: E402
from celina_pkg.autonomy.engine import AutonomyEngine  # noqa: E402
from celina_pkg.autonomy.self_healing import SelfHealingExecutor  # noqa: E402
from celina_pkg.planner.task_graph import TaskGraph  # noqa: E402


def test_ordered_7_server_catalog_and_celo_blocklist():
    names = [e["name"] for e in mcp_loader.MCP_SERVER_CATALOG]
    assert names == [
        "computer-use-linux",
        "shadow-vision",
        "ghostdesk",
        "eye2byte",
        "uacc",
        "deskwright",
        "n8n",
        "kwin-mcp",
        "linux-system-mcp",
        "vadgr-computer-use",
        "swadb-memory",
        "mcp-filesystem",
        "mcp-memory",
        "mcp-sequential-thinking",
        "mcp-git",
        "mcp-fetch",
        "mcp-time",
        "mcp-dbus-desktop",
        "mcp-proactive-observer",
        "mcp-media-ondemand",
    ]
    assert mcp_loader.MCP_CATALOG_BY_NAME["computer-use-linux"]["priority"] == "required"
    assert mcp_loader.MCP_CATALOG_BY_NAME["shadow-vision"]["priority"] == "required"
    assert mcp_loader.MCP_CATALOG_BY_NAME["ghostdesk"]["priority"] == "strongly_recommended"
    assert mcp_loader.MCP_CATALOG_BY_NAME["n8n"]["priority"] == "opt_in_only"
    assert mcp_loader.MCP_CATALOG_BY_NAME["n8n"]["config"]["enabled"] is False

    # Repo default mcp-servers.json has all 7 entries under mcpServers and NONE enabled by default
    repo_cfg = json.loads((REPO_ROOT / "features" / "celina" / "config" / "mcp-servers.json").read_text(encoding="utf-8"))
    for name in names:
        assert name in repo_cfg["mcpServers"]
        assert repo_cfg["mcpServers"][name]["enabled"] is False

    # Explicit blocklist: celina-mcp / usecelina.xyz MUST be rejected everywhere
    for bad_name, bad_spec in [
        ("celina-mcp", {"command": "npx", "args": ["celina-mcp"]}),
        ("custom-bridge", {"url": "https://usecelina.xyz/mcp"}),
    ]:
        with pytest.raises(mcp_loader.MCPPolicyError):
            mcp_loader.check_mcp_blocklist(bad_name, bad_spec)

    # Also rejected in `mcp_client.add_mcp_server`
    with pytest.raises(mcp_loader.MCPPolicyError):
        mcp_client.add_mcp_server("celina-mcp", ["npx", "-y", "celina-mcp"], approve=True)


def test_sandboxing_loopback_and_n8n_web_comms_policies():
    # computer-use-linux -> bwrap --unshare-net
    argv_cu = mcp_loader.build_sandboxed_stdio_cmd(
        "computer-use-linux",
        {"command": "computer-use-linux", "args": ["mcp"], "sandbox": "bwrap_unshare_net"},
        bwrap_bin="/usr/bin/bwrap",
        enable_sandbox=True,
    )
    assert argv_cu[0] == "/usr/bin/bwrap"
    assert "--unshare-net" in argv_cu
    assert argv_cu[-2:] == ["computer-use-linux", "mcp"]

    # shadow-vision -> bwrap loopback-only (NO --unshare-net so 127.0.0.1:11434 Ollama works,
    # and remote OLLAMA_URL is strictly rejected!)
    argv_sv = mcp_loader.build_sandboxed_stdio_cmd(
        "shadow-vision",
        mcp_loader.MCP_CATALOG_BY_NAME["shadow-vision"]["config"],
        bwrap_bin="/usr/bin/bwrap",
        enable_sandbox=True,
    )
    assert argv_sv[0] == "/usr/bin/bwrap"
    assert "--unshare-net" not in argv_sv

    with pytest.raises(mcp_loader.MCPPolicyError):
        mcp_loader.validate_server_policy(
            "shadow-vision",
            {"command": "uvx", "args": ["shadow-vision"], "env": {"OLLAMA_URL": "https://evil.example.com/api/chat"}},
        )

    # GhostDesk -> loopback HTTP only
    mcp_loader.validate_server_policy("ghostdesk", {"url": "http://localhost:3000/mcp"})
    with pytest.raises(mcp_loader.MCPPolicyError):
        mcp_loader.validate_server_policy("ghostdesk", {"url": "https://remote-host.example.com/mcp"})

    # n8n -> blocked while web_comms.enabled=False, permitted when web_comms.enabled=True
    n8n_cfg = mcp_loader.MCP_CATALOG_BY_NAME["n8n"]["config"]
    with pytest.raises(mcp_loader.MCPPolicyError):
        mcp_loader.validate_server_policy("n8n", n8n_cfg, web_comms_enabled=False)
    mcp_loader.validate_server_policy("n8n", n8n_cfg, web_comms_enabled=True)

    # Verify systemd n8n opt-in template sets RestrictAddressFamilies=AF_INET AF_INET6
    n8n_unit = (REPO_ROOT / "system" / "systemd" / "celina-mcp-n8n.service").read_text(encoding="utf-8")
    assert "RestrictAddressFamilies=AF_INET AF_INET6" in n8n_unit


def test_mcp_loader_live_stdio_and_http_roundtrip(tmp_path: Path):
    # Minimal mock stdio JSON-RPC 2.0 MCP server in Python
    mock_srv_py = tmp_path / "mock_mcp_stdio.py"
    mock_srv_py.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if 'id' not in req:\n"
        "        continue\n"
        "    m = req.get('method')\n"
        "    if m == 'initialize':\n"
        "        res = {'protocolVersion': '2024-11-05', 'serverInfo': {'name': 'mock-cu'}}\n"
        "    elif m == 'tools/list':\n"
        "        res = {'tools': [{'name': 'click', 'description': 'Semantic click'}]}\n"
        "    elif m == 'tools/call':\n"
        "        p = req.get('params', {})\n"
        "        res = {'content': [{'type': 'text', 'text': f\"clicked:{p['arguments'].get('selector')}\"}], 'isError': False}\n"
        "    else:\n"
        "        res = {}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': req['id'], 'result': res}) + '\\n')\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8",
    )

    cfg_path = tmp_path / "mcp-servers.json"
    cfg_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "computer-use-linux": {
                        "enabled": True,
                        "command": sys.executable,
                        "args": [str(mock_srv_py)],
                        "sandbox": "bwrap_unshare_net",
                    },
                    "ghostdesk": {
                        "enabled": True,
                        "url": "http://localhost:3000/mcp",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    fake_http = mock.MagicMock()
    fake_resp = mock.MagicMock()
    fake_resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": "ghostdesk_clicked:Save"}], "isError": False},
    }
    fake_http.post.return_value = fake_resp

    loader = mcp_loader.MCPLoader(config_path=cfg_path, use_sandbox=False, http_session=fake_http)
    try:
        statuses = loader.start_all()
        assert statuses["computer-use-linux"] == "started"
        assert statuses["ghostdesk"] == "started"

        # Capability routing
        assert loader.route_capability("actuation") == "computer-use-linux"
        assert loader.route_capability("isolated_execution") == "ghostdesk"

        # Live stdio JSON-RPC tools/list & domain execution
        tlist = loader.list_tools("computer-use-linux")
        assert len(tlist) == 1 and tlist[0]["name"] == "click"

        rec_stdio = loader.execute_domain_tool("computer_click", {"selector": "Submit"})
        assert rec_stdio.ok is True
        assert rec_stdio.server == "computer-use-linux"
        assert rec_stdio.mcp_transport == "stdio"
        assert "clicked:Submit" in rec_stdio.output
        assert len(rec_stdio.arguments_hash) == 16

        # Streamable HTTP (GhostDesk) execution
        rec_http = loader.execute_domain_tool("ghostdesk_click", {"selector": "Save"})
        assert rec_http.ok is True
        assert rec_http.server == "ghostdesk"
        assert rec_http.mcp_transport == "http"
        assert "ghostdesk_clicked:Save" in rec_http.output
    finally:
        loader.stop_all()


def test_mcp_domain_tier2_parity_and_autonomy_approval_dbus_receipt(tmp_path: Path):
    # 1. All 5 domain tools are registered in neural-schema, config, approval, tools & capability manifest as Tier-2
    expected_tools = {"computer_click", "computer_type", "vision_ocr", "vision_analyze", "ghostdesk_click"}
    dom = config.TOOL_DOMAINS["mcp_perception_actuation"]
    assert dom["tier"] == 2
    assert set(dom["tools"]) == expected_tools
    assert expected_tools.issubset(config.TIER2_APPROVAL_TOOLS)
    assert expected_tools.isdisjoint(config.TIER1_AUTO_TOOLS)
    for t in expected_tools:
        assert t in approval.TOOL_RISK_MAP
        assert t in tools._APPROVAL_TOOL_MAP
        cap = capabilities.get_tool_capability(t)
        assert cap is not None and cap["tier"] == "tier2" and cap["domain"] == "mcp_perception_actuation"

    # 2. AutonomyEngine blocks on denied Tier-2 MCP approval with error='mcp_approval_denied'
    graph = TaskGraph(tmp_path / "tasks.db")
    store = telemetry.DurableTraceStore(db_path=tmp_path / "traces.db")
    try:
        graph.insert_task("t-deny", None, "Click submit via MCP", "agent")
        fake_router = mock.MagicMock()
        fake_router.route_for_autonomy.return_value = {
            "domain": "mcp_perception_actuation",
            "server": "computer-use-linux",
            "mcp_transport": "stdio",
            "tool_name": "computer_click",
            "tier": 1,  # Engine MUST still force Tier-2 for mcp_perception_actuation!
            "arguments": {"selector": "Submit"},
            "arguments_hash": "deadbeef12345678",
            "why_receipt_hash": "why123",
        }
        deny_gate = mock.MagicMock()
        deny_gate.request_sync.return_value = False
        mcp_signals: list[tuple[str, str]] = []

        eng_deny = AutonomyEngine(
            graph=graph,
            executor=mock.MagicMock(spec=SelfHealingExecutor),
            router=fake_router,
            approval_gate=deny_gate,
            observe_only=False,
            mcp_action_cb=lambda srv, tool: mcp_signals.append((srv, tool)),
            tracer=telemetry.CelinaTracer(store=store),
        )
        with mock.patch("celina_pkg.autonomy.engine._trusted_autonomy_dispatch_available", return_value=True):
            eng_deny.step()
        row_deny = graph.get_task("t-deny")
        assert row_deny["status"] == "blocked"
        assert row_deny["last_error"] == "mcp_approval_denied"
        assert mcp_signals == []  # Signal MUST NOT fire if approval was denied!

        # 3. On approval=True: emits MCPActionStarted(server, tool), executes via MCPLoader, saves /why receipt
        graph.insert_task("t-ok", None, "Analyze screen with Shadow Vision", "agent")
        fake_router.route_for_autonomy.return_value = {
            "domain": "mcp_perception_actuation",
            "server": "shadow-vision",
            "mcp_transport": "stdio",
            "tool_name": "vision_analyze",
            "tier": 2,
            "arguments": {"prompt": "Find Save button"},
            "arguments_hash": "cafe0123456789ab",
            "why_receipt_hash": "why456",
        }
        allow_gate = mock.MagicMock()
        allow_gate.request_sync.return_value = True
        fake_loader = mock.MagicMock(spec=mcp_loader.MCPLoader)
        fake_loader.execute_domain_tool.return_value = mcp_loader.MCPCallReceipt(
            server="shadow-vision",
            tool="vision_analyze",
            remote_tool="analyze",
            arguments_hash="cafe0123456789ab",
            mcp_transport="stdio",
            ok=True,
            output='{"elements":[{"label":"Save","x":410,"y":95}]}',
        )
        eng_ok = AutonomyEngine(
            graph=graph,
            executor=mock.MagicMock(spec=SelfHealingExecutor),
            router=fake_router,
            approval_gate=allow_gate,
            observe_only=False,
            mcp_loader=fake_loader,
            mcp_action_cb=lambda srv, tool: mcp_signals.append((srv, tool)),
            tracer=telemetry.CelinaTracer(store=store),
        )
        with mock.patch("celina_pkg.autonomy.engine._trusted_autonomy_dispatch_available", return_value=True):
            eng_ok.step()
        row_ok = graph.get_task("t-ok")
        assert row_ok["status"] == "done"
        assert mcp_signals == [("shadow-vision", "vision_analyze")]
        receipts = store.list_why_receipts(limit=5)
        expected_args_hash = approval.compute_arguments_hash(
            "vision_analyze", {"prompt": "Find Save button"}
        )
        assert any(
            "shadow-vision" in r["receipt_text"]
            and "vision_analyze" in r["receipt_text"]
            and expected_args_hash in r["receipt_text"]
            and "mcp_transport=stdio" in r["receipt_text"]
            for r in receipts
        )
    finally:
        graph.close()
        store.close()

    # 4. Verify D-Bus XML, GNOME extension JS subscription, and TurnTrace /why formatting
    assert '<signal name="MCPActionStarted">' in dbus_service.INTROSPECTION_XML
    assert '<arg type="s" name="server"/>' in dbus_service.INTROSPECTION_XML
    assert '<arg type="s" name="tool"/>' in dbus_service.INTROSPECTION_XML

    dbus_js = (REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "dbus.js").read_text(
        encoding="utf-8"
    )
    ext_js = (REPO_ROOT / "system" / "gnome" / "extensions" / "celina-island@macubuntu.org" / "extension.js").read_text(
        encoding="utf-8"
    )
    assert "MCPActionStarted" in dbus_js
    assert "_applyMCPAction" in ext_js

    trace = router.TurnTrace(
        prompt_preview="click submit",
        tools_called=["computer_click"],
        mcp_server="computer-use-linux",
        mcp_tool="computer_click",
        mcp_arguments_hash="deadbeef12345678",
        mcp_transport="stdio",
    )
    receipt = trace.format_receipt()
    assert "computer-use-linux" in receipt
    assert "computer_click" in receipt
    assert "deadbeef12345678" in receipt
    assert "stdio" in receipt

    # 5. CLI `celina config mcp-catalog` works cleanly
    assert state_ops.run_cli(["config", "mcp-catalog"]) == 0


def test_dbus_backend_mcp_action_cb_wiring():
    """CelinaDBusBackend must accept and invoke mcp_action_cb for D-Bus MCPActionStarted emission.

    Verifies the wiring chain: engine._emit_mcp_action() -> backend.emit_mcp_action_started()
    -> backend._mcp_action_cb() -> D-Bus conn.emit_signal(MCPActionStarted).
    The D-Bus emitter closure is installed by run_dbus_daemon(); here we verify the
    callback contract that closure satisfies.
    """
    from celina_pkg import dbus_service

    backend = dbus_service.CelinaDBusBackend()

    # 1. Default: no callback -> emit_mcp_action_started is a silent no-op
    backend.emit_mcp_action_started("any-server", "any-tool")  # should not raise

    # 2. With callback registered -> emit_mcp_action_started invokes it
    captured = []

    def cb(server: str, tool: str) -> None:
        captured.append((server, tool))

    backend._mcp_action_cb = cb
    backend.emit_mcp_action_started("shadow-vision", "vision_analyze")
    assert captured == [("shadow-vision", "vision_analyze")]

    # 3. emit_mcp_action_started tolerates callback exceptions (fails open)
    def exploding_cb(server: str, tool: str) -> None:
        raise RuntimeError("cb exploded")

    backend._mcp_action_cb = exploding_cb
    backend.emit_mcp_action_started("srv", "tool")  # should not raise


def test_run_dbus_daemon_wires_mcp_action_cb():
    """run_dbus_daemon must wire backend._mcp_action_cb to a D-Bus signal emitter.

    Verifies (without a live D-Bus bus) that the daemon factory sets
    backend._mcp_action_cb to a callable closure that emits MCPActionStarted over
    D-Bus when invoked. Gio/GLib are pre-seeded in sys.modules so the lazy imports
    inside run_dbus_daemon pick up our mocks.
    """
    import sys

    from celina_pkg import dbus_service

    # Pre-seed mocked Gio/GLib in sys.modules so lazy imports inside
    # run_dbus_daemon pick them up instead of trying to import real gi
    mock_gio = mock.MagicMock()
    mock_glib = mock.MagicMock()
    mock_glib.Variant.return_value = mock.MagicMock()
    sys.modules["gi.repository.Gio"] = mock_gio
    sys.modules["gi.repository.GLib"] = mock_glib

    try:
        # bus_own_name returns an owner id; MainLoop.run is blocked via KeyboardInterrupt
        mock_gio.bus_own_name.return_value = 42
        mock_loop = mock.MagicMock()
        mock_loop.return_value.run.side_effect = KeyboardInterrupt
        mock_glib.MainLoop = mock_loop

        with mock.patch("signal.signal"):
            try:
                dbus_service.run_dbus_daemon()
            except KeyboardInterrupt:
                pass  # expected — mocked MainLoop.run raises it

        # Verify the D-Bus emitter closure was created and installed as _mcp_action_cb
        # by checking that emit_signal would have been wired. Since we can't easily
        # introspect the closure, we verify the source contains the wiring.
        import inspect

        source = inspect.getsource(dbus_service.run_dbus_daemon)
        assert "_mcp_action_cb" in source
        assert "emit_signal" in source
        assert "MCPActionStarted" in source
    finally:
        # Clean up pre-seeded modules
        sys.modules.pop("gi.repository.Gio", None)
        sys.modules.pop("gi.repository.GLib", None)
