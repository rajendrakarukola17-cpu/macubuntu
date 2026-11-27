"""
Suite 22: 20% Speed / 10% Stability / 30% Efficiency / 50% Smarter Upgrades.

Verifies all 7 research-backed techniques end-to-end:
  1. Speed: Offline knowledge compilation + SQLite FTS5 BM25 confidence-gated routing ("Brain That Goes Quiet")
  2. Speed: `autotune` middleware (exact num_ctx 4-8x KV savings, num_keep pinning, keep_alive=-1m, FA + q8_0)
  3. Stability: AgentFuse `@reliable` (Fuse + Sentinel + Medic + Budget) + `GuardLoop` per-tool 3x breaker + evidence log
  4. Stability: `AutonomyEngine._execute_one` wrapped with `@reliable(sentinel_schema=TaskResult, fuse_limit=5, max_seconds=30)`
  5. Efficiency: SecondThought confidence-gated reasoning (`mean_logprob < 0.82`)
  6. Smarts: Tier-based hybrid tool presentation (8 detailed JSON schemas + rest name-only, >80% token reduction)
  7. Smarts: MARS single-cycle metacognitive reflection (Principle + Procedural) persisted into `CelinaMemory`
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_pkg import (  # noqa: E402
    evidence,
    inference_adaptive,
    memory,
    router,
    telemetry,
    tools,
)
from celina_pkg.autonomy import reliability  # noqa: E402
from celina_pkg.autonomy.engine import AutonomyEngine  # noqa: E402
from celina_pkg.autonomy.self_healing import ExecResult, SelfHealingExecutor  # noqa: E402
from celina_pkg.learning.self_improve import SelfImprover  # noqa: E402
from celina_pkg.planner.task_graph import TaskGraph  # noqa: E402


def test_quiet_brain_offline_compilation_bm25_routing_and_autotune(tmp_path: Path):
    db = memory.MemoryDB(db_path=tmp_path / "memory.db")
    graph = TaskGraph(tmp_path / "tasks.db")
    try:
        r = router.CelinaRouter(db=db)
        improver = SelfImprover(graph, llm=None)

        # 1. Offline big-model compilation during idle self-improvement window
        compiled = improver.compile_recurring_queries(
            r._memory,
            [
                "how to check ollama cgroup memory pressure on ubuntu",
                "how to switch sched_ext scheduler to scx_lavd lowlatency",
            ],
            big_model_fn=lambda q: f"Verified offline answer for: {q}",
            source_model="qwen3:14b-offline",
            confidence=0.96,
        )
        assert len(compiled) == 2

        # 2. Runtime BM25 router answers recurring query directly without invoking big model!
        tr = router.TurnTrace(prompt_preview="check ollama cgroup memory pressure ubuntu")
        hit = r.route_with_quiet_brain(
            "check ollama cgroup memory pressure ubuntu",
            confidence_threshold=0.82,
            trace=tr,
        )
        assert hit["hit"] is True and hit["should_escalate"] is False
        assert "Verified offline answer" in hit["answer"]
        assert tr.quiet_brain_hit is True
        assert "Quiet Brain BM25" in tr.format_receipt()

        # Unseen low-confidence query escalates cleanly
        miss = r.route_with_quiet_brain("quantum entanglement photon polarization calculus", confidence_threshold=0.82)
        assert miss["hit"] is False and miss["should_escalate"] is True

        # 3. autotune middleware: exact compact num_ctx (4x+ KV savings), num_keep, keep_alive=-1m, FA + q8_0
        msgs = [
            {"role": "system", "content": "You are Celina, an Ubuntu Wayland chief-of-staff."},
            {"role": "user", "content": "Check battery percentage"},
        ]
        tuned = inference_adaptive.autotune_ollama_request(
            msgs, max_output_tokens=256, baseline_ctx=4096, ram_gb=8, avail_ram_mb=3200, pin_in_ram=True, psi_full_avg10=0.0
        )
        assert tuned.num_ctx <= 1024
        assert tuned.kv_reduction_factor >= 4.0
        assert tuned.num_keep > 0
        assert tuned.keep_alive == "-1m"
        assert tuned.to_env_vars() == {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"}
        assert inference_adaptive.DEFAULT_SMALL_RUNTIME_MODEL == "qwen3:1.7b"
    finally:
        graph.close()
        db.close()


def test_agentfuse_reliable_decorator_guardloop_and_engine_wiring(tmp_path: Path):
    # 1. @reliable: Fuse trips after 3 identical inputs
    @reliability.reliable(sentinel_schema=reliability.TaskResult, fuse_limit=3, max_seconds=5.0)
    def _step(task_id: str) -> reliability.TaskResult:
        return reliability.TaskResult(task_id=task_id, status="done", tool_name="cache_status")

    assert _step("t1").status == "done"
    assert _step("t1").status == "done"
    with pytest.raises(reliability.CircuitBreakerTripped):
        _step("t1")

    # 2. @reliable: Sentinel + Medic auto-repair up to 2x, then propagate if repair fails
    repaired_calls = {"n": 0}

    def _medic(bad_val, exc):
        repaired_calls["n"] += 1
        return reliability.TaskResult(task_id="t-med", status="repaired", tool_name="edit_file")

    @reliability.reliable(sentinel_schema=reliability.TaskResult, fuse_limit=5, medic_repair_fn=_medic, max_medic_repairs=2)
    def _bad_schema_fn(x: str):
        return {"broken": x}

    fixed = _bad_schema_fn("payload")
    assert fixed.status == "repaired" and repaired_calls["n"] == 1

    @reliability.reliable(
        sentinel_schema=reliability.TaskResult,
        fuse_limit=5,
        medic_repair_fn=lambda _v, _e: {"still_bad": True},
        max_medic_repairs=2,
    )
    def _unrepairable(x: str):
        return {"broken": x}

    with pytest.raises(ValueError):
        _unrepairable("fail")

    # 3. GuardLoop: 3 consecutive identical errors disables tool for session, logs to EvidenceLedger & OTel!
    ev_path = tmp_path / "evidence.jsonl"
    ledger = evidence.EvidenceLedger(ledger_path=ev_path)
    tr_store = telemetry.DurableTraceStore(db_path=tmp_path / "traces.db")
    tracer = telemetry.CelinaTracer(store=tr_store)
    try:
        gl = reliability.GuardLoop(
            max_tool_calls=10,
            max_seconds=30.0,
            max_cost_usd=0.50,
            same_error_threshold=3,
            evidence_ledger=ledger,
            tracer=tracer,
        )

        def _flaky():
            return ExecResult(success=False, exit_code=1, summary="", error="ECONNREFUSED 127.0.0.1")

        for _ in range(3):
            gl.call_tool("web_fetch", _flaky)
        assert gl.is_tool_disabled("web_fetch") is True
        with pytest.raises(reliability.CircuitBreakerTripped):
            gl.call_tool("web_fetch", _flaky)

        # Tamper-evident evidence chain recorded the breaker event!
        ok, _msg, cnt = ledger.verify_chain()
        assert ok is True and cnt >= 1
        assert "guardloop_tool_circuit_open" in ev_path.read_text(encoding="utf-8")
        # OTel spans were recorded for each guarded tool call!
        assert len(tr_store.list_traces(limit=10)) >= 3

        # 4. AutonomyEngine._execute_one returns validated TaskResult via @reliable
        graph = TaskGraph(tmp_path / "engine_tasks.db")
        try:
            graph.insert_task("t-ok", None, "Check system stats", "agent")
            m_router = mock.MagicMock()
            m_router.route_for_autonomy.return_value = {
                "tool_name": "cache_status",
                "tier": 1,
                "domain": "os_hardware",
                "arguments": {},
                "arguments_hash": "h-ok",
                "why_receipt_hash": "w-ok",
            }
            m_exec = mock.MagicMock(spec=SelfHealingExecutor)
            m_exec.execute.return_value = ExecResult(success=True, exit_code=0, summary="stats ok")
            eng = AutonomyEngine(
                graph=graph,
                executor=m_exec,
                router=m_router,
                approval_gate=mock.MagicMock(),
                observe_only=False,
                guard_loop=gl,
            )
            task = graph.next_runnable()
            with mock.patch("celina_pkg.autonomy.engine._trusted_autonomy_dispatch_available", return_value=True):
                res = eng._execute_one(task)
            assert isinstance(res, reliability.TaskResult)
            assert res.task_id == "t-ok" and res.status == "done"
        finally:
            graph.close()
    finally:
        tr_store.close()


def test_second_thought_tiered_tools_and_mars_reflection(tmp_path: Path):
    db = memory.MemoryDB(db_path=tmp_path / "memory.db")
    graph = TaskGraph(tmp_path / "tasks.db")
    try:
        r = router.CelinaRouter(db=db)

        # 1. SecondThought confidence gate: skips thinking when mean_logprob >= 0.82!
        think_calls = {"n": 0}

        def _think(_goal: str):
            think_calls["n"] += 1
            return {"content": "deep reasoned answer", "tool_calls": []}

        # High confidence (logprob -0.08 -> exp(-0.08) ~= 0.923 >= 0.82) -> NO thinking
        tr_fast = router.TurnTrace(prompt_preview="set brightness to 70")
        out_fast = r.call_with_second_thought(
            "set brightness to 70",
            no_think_fn=lambda _g: {"content": "brightness set", "logprobs": [-0.08, -0.06, -0.09]},
            think_fn=_think,
            threshold=0.82,
            trace=tr_fast,
        )
        assert out_fast["used_thinking"] is False and think_calls["n"] == 0
        assert out_fast["mean_logprob"] >= 0.82 and tr_fast.second_thought_escalated is False

        # Low confidence (logprob -0.45 -> exp(-0.45) ~= 0.638 < 0.82) -> escalates to thinking!
        tr_deep = router.TurnTrace(prompt_preview="refactor distributed consensus protocol")
        out_deep = r.call_with_second_thought(
            "refactor distributed consensus protocol",
            no_think_fn=lambda _g: {"content": "unsure", "logprobs": [-0.45, -0.50, -0.40]},
            think_fn=_think,
            threshold=0.82,
            trace=tr_deep,
        )
        assert out_deep["used_thinking"] is True and think_calls["n"] == 1
        assert tr_deep.second_thought_escalated is True
        assert "SecondThought Gate" in tr_deep.format_receipt()

        # 2. Tier-based hybrid tool presentation: 8 detailed + rest name-only (>80% token savings!)
        pres = tools.build_tiered_tool_presentation(
            "edit LibreOffice budget document and export PDF",
            gear="tactical",
            detailed_k=8,
        )
        assert len(pres["detailed_schemas"]) == 8
        assert len(pres["name_only_tools"]) == pres["total_enabled_tools"] - 8
        assert pres["estimated_token_savings_pct"] >= 80.0
        assert "Other available tools (name-only):" in pres["name_only_prompt_block"]

        # Verify neural-schema.json encodes both SecondThought & 8-detailed tiered presentation
        schema = json.loads((REPO_ROOT / "features/celina/config/neural-schema.json").read_text(encoding="utf-8"))
        assert schema["cognition"]["tool_presentation"]["detailed_top_k"] == 8
        assert schema["cognition"]["second_thought_confidence_gate"]["min_mean_logprob"] == pytest.approx(0.82)

        # 3. MARS single-cycle metacognitive reflection (Principle + Procedural) persisted into CelinaMemory
        improver = SelfImprover(graph, llm=None)
        mars = improver.run_mars_reflection(
            failures=[
                {"id": "f1", "goal": "edit report.docx", "last_error": "FileNotFoundError: missing report.docx"},
                {"id": "f2", "goal": "click submit button", "last_error": "TimeoutError: window unfocused"},
            ],
            memory_engine=r._memory,
        )
        assert mars["cycle"] == "mars_single_pass"
        assert len(mars["principles"]) >= 2
        assert len(mars["procedures"]) >= 2
        assert mars["stored_in_memory"] >= 4
        # Principles & procedures are immediately recallable via CelinaMemory!
        recalled = r._memory.recall("MARS Principle FileNotFoundError report.docx", k=5)
        assert any("MARS" in h["text"] for h in recalled)
    finally:
        graph.close()
        db.close()
