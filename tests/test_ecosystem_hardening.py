"""
Suite 19: Open-Source Ecosystem Hardening Integration Tests.

Covers:
  1. Advanced MCP Servers (`kwin-mcp`, `linux-system-mcp`, `vadgr-computer-use`, `swadb-memory`)
  2. Reliability & Stability (`AgentSRE` OWASP ASI07/ASI08 SLOs/cost/kill-switch,
     `AgentFuse` 4-rung recovery ladder for loops/drift/traps/spend,
     `EditableStateMachineRun` agent6-style pause/edit/replay & hard USD budget)
  3. Kernel-Level Sandboxing (`SHarness` policy JSON + SHA-256 hash, `ai-jail`/`safeshell`,
     `bwrap` + read-only `.git` rebind + Landlock LSM in `SelfHealingExecutor`)
  4. Observability (`@agent_span` agentlog-ai/Traccia decorator + loopback-enforced `OTLPJsonExporter`)
  5. Inference & Memory Optimization (`Octuma` pre-download RAM-fit check & INT4 GGUF savings,
     `AdapTQ` CPU KV-cache quantization, and 9-tool `CelinaMemoryMCPServer` swadb/Engrama server)
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
    inference_adaptive,
    landlock_policy,
    memory_mcp_server,
    telemetry,
)
from celina_pkg.autonomy import mcp_loader, reliability  # noqa: E402
from celina_pkg.autonomy.engine import AutonomyEngine  # noqa: E402
from celina_pkg.autonomy.self_healing import ExecResult, SelfHealingExecutor  # noqa: E402
from celina_pkg.planner.task_graph import TaskGraph  # noqa: E402


def test_advanced_mcp_servers_catalog_and_role_routing(tmp_path: Path):
    for name in ("kwin-mcp", "linux-system-mcp", "vadgr-computer-use", "swadb-memory"):
        assert name in mcp_loader.MCP_CATALOG_BY_NAME
        preset = mcp_loader.get_catalog_preset(name)
        assert preset["enabled"] is False
        argv = mcp_loader.build_sandboxed_stdio_cmd(name, preset, bwrap_bin="/usr/bin/bwrap", enable_sandbox=True)
        assert argv[0] == "/usr/bin/bwrap" and "--unshare-net" in argv

    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "vadgr-computer-use": {"enabled": True, "command": "vadgr-computer-use", "args": ["mcp"]},
                    "kwin-mcp": {"enabled": True, "command": "uvx", "args": ["kwin-mcp"]},
                    "linux-system-mcp": {"enabled": True, "command": "npx", "args": ["-y", "linux-system-mcp"]},
                    "swadb-memory": {"enabled": True, "command": "celina-memory-mcp", "args": []},
                }
            }
        ),
        encoding="utf-8",
    )
    loader = mcp_loader.MCPLoader(config_path=cfg_path, use_sandbox=False)
    assert loader.route_capability("actuation") == "vadgr-computer-use"
    assert loader.route_capability("isolated_execution") == "kwin-mcp"
    assert loader.route_capability("system_bridge") == "linux-system-mcp"
    assert loader.route_capability("memory_graph") == "swadb-memory"


def test_agent_sre_agentfuse_and_editable_state_machine(tmp_path: Path):
    # 1. AgentSRE: Tier-2 SLO latency violation, OWASP ASI07 cost cap, ASI08 cascading kill switch
    pol = reliability.AgentSLOPolicy(
        tier2_max_latency_sec=30.0,
        max_task_cost_usd=0.25,
        max_session_cost_usd=0.50,
        max_consecutive_failures=3,
        max_errors_in_window=3,
    )
    sre = reliability.AgentSRE(policy=pol)
    assert sre.check_allowed("t1", estimated_cost_usd=0.10).allowed is True
    # Exceeding per-task cost cap is denied (OWASP ASI07)
    deny_cost = sre.check_allowed("t1", estimated_cost_usd=0.30)
    assert deny_cost.allowed is False and "ASI07" in deny_cost.reason

    # Slow Tier-2 records SLO violation
    rep_slow = sre.record_outcome("t1", success=True, latency_sec=42.5, cost_usd=0.05, tier=2)
    assert any("SLO violation" in v for v in rep_slow["slo_violations"])

    # 3 consecutive failures auto-trip cascading failure kill switch (OWASP ASI08)
    for i in range(3):
        sre.record_outcome(f"fail-{i}", success=False, latency_sec=1.0, tier=2)
    assert sre.is_killed() is True
    assert sre.check_allowed("t-next").allowed is False

    # 2. AgentFuse: 4-rung Recovery Ladder on tool loop, goal drift, logic trap, runaway spend
    fuse = reliability.AgentFuse(max_loop_repeats=2, max_drift_streak=2, max_logic_trap_errors=2, max_spend_usd=0.50)
    d0 = fuse.monitor({"goal": "edit report", "tool_name": "edit_file", "arguments_hash": "h1"})
    assert d0.tripped is False
    # Repeat -> Rung 1 RESTATE_GOAL
    d1 = fuse.monitor({"goal": "edit report", "tool_name": "edit_file", "arguments_hash": "h1"})
    assert d1.tripped is True and d1.failure_mode == "tool_loop"
    assert d1.recovery_rung == reliability.RecoveryRung.RESTATE_GOAL
    # Next repeat -> Rung 2 FORBID_ACTION
    d2 = fuse.monitor({"goal": "edit report", "tool_name": "edit_file", "arguments_hash": "h1"})
    assert d2.recovery_rung == reliability.RecoveryRung.FORBID_ACTION
    assert "edit_file" in d2.forbidden_tools
    # Next -> Rung 3 FALLBACK_CAPABILITY
    d3 = fuse.monitor({"goal": "edit report", "tool_name": "edit_file", "arguments_hash": "h1"})
    assert d3.recovery_rung == reliability.RecoveryRung.FALLBACK_CAPABILITY
    # Next -> Rung 4 PAUSE_FOR_HUMAN
    d4 = fuse.monitor({"goal": "edit report", "tool_name": "edit_file", "arguments_hash": "h1"})
    assert d4.recovery_rung == reliability.RecoveryRung.PAUSE_FOR_HUMAN

    # Goal drift & runaway spend detection
    fuse_drift = reliability.AgentFuse(max_drift_streak=2, max_spend_usd=0.20)
    fuse_drift.monitor({"goal": "fix python sqlite migration", "tool_name": "play_music", "action_summary": "spotify jazz", "check_drift": True})
    dd = fuse_drift.monitor({"goal": "fix python sqlite migration", "tool_name": "set_volume", "action_summary": "loudspeaker", "check_drift": True})
    assert dd.tripped is True and dd.failure_mode == "goal_drift"
    ds = fuse_drift.monitor({"goal": "fix python sqlite migration", "tool_name": "read_file", "cost_usd": 0.25})
    assert ds.tripped is True and ds.failure_mode == "runaway_spend"

    # 3. EditableStateMachineRun (agent6): pause -> edit -> reload -> replay -> hard budget block
    run = reliability.EditableStateMachineRun(
        "run-1",
        "Refactor module",
        [{"tool": "read_file", "path": "a.py"}, {"tool": "edit_file", "path": "a.py"}],
        state_dir=tmp_path / "runs",
        budget_usd=0.10,
    )
    snap0 = run.advance_step(step_cost_usd=0.04, output="read ok")
    assert snap0["step_index"] == 0
    run.pause()
    run.edit_step(1, {"path": "b.py"})
    reloaded = reliability.EditableStateMachineRun.load(run.state_path)
    assert reloaded.steps[1]["path"] == "b.py" and reloaded.status == "paused"
    reloaded.replay_from(1)
    with pytest.raises(reliability.AgentSREError):
        reloaded.advance_step(step_cost_usd=0.09)  # 0.04 + 0.09 > 0.10 budget!

    # 4. Live AutonomyEngine integration: AgentSRE kill switch & AgentFuse trip block runaway retries
    graph = TaskGraph(tmp_path / "tasks.db")
    try:
        graph.insert_task("t-sre", None, "Run shell task", "agent")
        fake_router = mock.MagicMock()
        fake_router.route_for_autonomy.return_value = {
            "tool_name": "cache_status",
            "tier": 1,
            "arguments": {},
            "arguments_hash": "h1",
            "why_receipt_hash": "w1",
        }
        sre_killed = reliability.AgentSRE()
        sre_killed.trip_kill_switch("emergency_stop")
        eng = AutonomyEngine(
            graph=graph,
            executor=mock.MagicMock(spec=SelfHealingExecutor),
            router=fake_router,
            approval_gate=mock.MagicMock(),
            observe_only=False,
            sre=sre_killed,
        )
        with mock.patch("celina_pkg.autonomy.engine._trusted_autonomy_dispatch_available", return_value=True):
            eng.step()
        row = graph.get_task("t-sre")
        assert row["status"] == "blocked" and "agent_sre_blocked" in str(row["last_error"])
    finally:
        graph.close()


def test_sharness_ai_jail_bwrap_ro_git_and_self_healing_executor(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    prof = landlock_policy.profile_for_tool("write_file", workdir=tmp_path)

    # 1. SHarness mode wraps with --policy <json> --policy-hash <sha256>
    cmd_sh, drv_sh, h_sh = landlock_policy.build_kernel_sandbox_cmd(
        ["echo", "hi"],
        prof,
        policy_dir=tmp_path / "policies",
        sharness_bin="/usr/local/bin/sharness-ai",
        workdir=tmp_path,
    )
    assert drv_sh == "sharness-ai"
    assert cmd_sh[0] == "/usr/local/bin/sharness-ai" and "--policy-hash" in cmd_sh and h_sh == prof.policy_hash

    # 2. ai-jail / safeshell mode binds .git read-only
    cmd_jail, drv_jail, _ = landlock_policy.build_kernel_sandbox_cmd(
        ["echo", "hi"],
        prof,
        sharness_bin="",
        ai_jail_bin="/usr/local/bin/ai-jail",
        workdir=tmp_path,
        protect_dot_git=True,
    )
    assert drv_jail == "ai-jail" and "--ro" in cmd_jail

    # 3. bwrap fallback rebinds .git read-only (agent6 safety invariant) + --unshare-net
    cmd_bw, drv_bw, _ = landlock_policy.build_kernel_sandbox_cmd(
        ["echo", "hi"],
        prof,
        sharness_bin="",
        ai_jail_bin="",
        bwrap_bin="/usr/bin/bwrap",
        workdir=tmp_path,
        protect_dot_git=True,
    )
    assert drv_bw == "bwrap+landlock"
    assert "--unshare-net" in cmd_bw
    assert "--ro-bind" in cmd_bw and str((tmp_path / ".git").resolve()) in cmd_bw

    # 4. SelfHealingExecutor runs native & sandboxed subprocess with policy_hash attached
    ex = SelfHealingExecutor(data_dir=tmp_path / "data", tool_handlers={"cache_status": lambda: "ok"})
    r_native = ex.execute({"tool_name": "cache_status", "domain": "mcp_system", "arguments": {}}, task_id="t1")
    assert r_native.success is True and len(r_native.policy_hash) == 64

    r_sub = ex.run_sandboxed_command("cache_status", [sys.executable, "-c", "print('jailed_ok')"], workdir=tmp_path)
    if not r_sub.success and "bwrap:" in str(r_sub.error):
        pytest.skip(f"Live bwrap unsupported in container sandbox: {r_sub.error}")
    assert r_sub.success is True and "jailed_ok" in r_sub.summary and len(r_sub.policy_hash) == 64


def test_agentlog_traccia_otel_otlp_and_octuma_adaptq(tmp_path: Path):
    # 1. @agent_span decorator + OTLPJsonExporter (loopback-only enforcement)
    store = telemetry.DurableTraceStore(db_path=tmp_path / "otel.db")
    tr = telemetry.CelinaTracer(store=store)
    try:
        @telemetry.agent_span("planner", tracer_getter=lambda: tr, governance_tier="tier1")
        def _plan_step(x: int) -> int:
            return x * 2

        assert _plan_step(21) == 42
        traces = store.list_traces(limit=5)
        assert len(traces) == 1
        full = store.get_trace(traces[0]["trace_id"])
        assert full and full["spans"][0]["name"] == "agent.planner"

        # OTLP exporter blocks non-loopback endpoints fail-closed!
        with pytest.raises(ValueError):
            telemetry.OTLPJsonExporter("https://external-collector.example.com/v1/traces")

        exp = telemetry.OTLPJsonExporter("http://127.0.0.1:4318/v1/traces")
        fake_sess = mock.MagicMock()
        fake_sess.post.return_value = mock.MagicMock(status_code=200)
        sp = telemetry.SpanRecord(
            trace_id=traces[0]["trace_id"],
            span_id="1234567890abcdef",
            parent_span_id=None,
            name="agent.planner",
            kind="agent",
            start_ns=1_700_000_000_000_000_000,
        )
        assert exp.export_span(sp, session=fake_sess) is True
        payload = exp.format_otlp_payload(sp)
        assert payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "agent.planner"
    finally:
        store.close()

    # 2. Octuma pre-download RAM-fit check (3B: ~6.0 GB FP16 -> ~2.4 GB INT4 GGUF on 8 GB host)
    rep_3b = inference_adaptive.estimate_model_ram_fit("qwen2.5:3b", ram_gb=8)
    assert rep_3b.fp16_gb == 6.0 and rep_3b.int4_gguf_gb == 2.4
    assert rep_3b.fits_fp16 is False and rep_3b.fits_int4_gguf is True
    assert rep_3b.recommended_quant == "Q4_K_M" and "octuma quantize" in rep_3b.octuma_command

    rep_14b = inference_adaptive.estimate_model_ram_fit("deepseek-r1:14b", ram_gb=8)
    assert rep_14b.fits_int4_gguf is False and rep_14b.recommended_quant == "TOO_LARGE" and rep_14b.warning

    # 3. AdapTQ CPU KV-cache quantization env config
    aq = inference_adaptive.build_adaptq_config(ram_gb=8, avail_ram_mb=2000)
    env_vars = aq.to_env_vars()
    assert env_vars["OLLAMA_FLASH_ATTENTION"] == "1"
    assert env_vars["OLLAMA_KV_CACHE_TYPE"] in {"q4_0", "q8_0"}


def test_swadb_engrama_9tool_memory_mcp_server(tmp_path: Path):
    srv = memory_mcp_server.CelinaMemoryMCPServer(base_dir=tmp_path)
    try:
        init_res = srv.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init_res["result"]["serverInfo"]["name"] == "celina-swadb-memory-mcp"

        # Exposes all 9 swadb/Engrama memory tools!
        list_res = srv.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tool_names = [t["name"] for t in list_res["result"]["tools"]]
        assert len(tool_names) == 9

        def _call(rid: int, name: str, args: dict) -> dict:
            r = srv.handle_request({"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": args}})
            assert r["result"]["isError"] is False
            return json.loads(r["result"]["content"][0]["text"])

        assert _call(3, "memory_store_fact", {"key": "ram_budget", "value": "8GB laptop using Q4_K_M + q8_0 KV"})["ok"] is True
        assert _call(4, "memory_upsert_entity", {"node_id": "ent:octuma", "kind": "tool", "label": "Octuma", "content": "INT4 GGUF quantizer"})["ok"] is True
        assert _call(5, "memory_upsert_entity", {"node_id": "ent:adaptq", "kind": "tool", "label": "AdapTQ", "content": "CPU KV cache quantizer"})["ok"] is True
        assert _call(6, "memory_link_entities", {"src_id": "ent:octuma", "edge_type": "RELATED_TO", "dst_id": "ent:adaptq"})["ok"] is True

        trav = _call(7, "memory_traverse_graph", {"start_node_id": "ent:octuma", "max_hops": 2})
        assert len(trav["edges"]) >= 1 and any(n["node_id"] == "ent:adaptq" for n in trav["nodes"])

        ret = _call(8, "memory_retrieve_9stage", {"query": "Octuma INT4 GGUF AdapTQ KV cache"})
        assert len(ret["items"]) >= 1 and len(ret["stage_stats"]) == 9

        ctx = _call(9, "memory_build_demand_context", {"query": "How does Octuma fit 8GB RAM?", "session_id": "s1", "token_budget": 250})
        assert ctx["token_estimate"] <= 250 and "Octuma" in ctx["rendered_text"]

        _call(10, "memory_working_set", {"session_id": "s1", "action": "put", "key": "phase", "value": "verify"})
        assert _call(11, "memory_working_set", {"session_id": "s1", "action": "get", "key": "phase"})["value"] == "verify"

        stats = _call(12, "memory_stats", {})
        assert stats["tool_count"] == 9 and len(stats["typed_edge_types"]) == 10 and stats["kg_nodes"] >= 3
    finally:
        srv.close()
