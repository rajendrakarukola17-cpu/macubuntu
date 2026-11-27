"""
Suite 21: Unified Single-File Hybrid Memory (Ariadne-Compatible CelinaMemory),
MemWeave 3-Way Memory Router, Autonomy graph_walk() Pre-Planning, and v4.4->v4.5 Migration.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import time
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_pkg import evidence, memory, router  # noqa: E402
from celina_pkg.autonomy.engine import AutonomyEngine  # noqa: E402
from celina_pkg.autonomy.self_healing import ExecResult, SelfHealingExecutor  # noqa: E402
from celina_pkg.planner.task_graph import TaskGraph  # noqa: E402


def test_unified_hybrid_recall_returns_all_three_legs_and_rrf(tmp_path: Path):
    db_file = tmp_path / "memory.db"
    mem = memory.CelinaMemory(db_path=db_file, prefer_ariadne=False)
    try:
        # 1. Exact keyword memory
        m_kw = mem.remember(
            "Updated budget_q4.xlsx with final cloud expenditure figures",
            importance=0.8,
            entities=["budget_q4.xlsx"],
        )
        # 2. Vector-only fuzzy/morphological memory (shares char-trigrams/prefix with "forecasting expenditure", zero exact "budget_q4.xlsx")
        m_vec = mem.remember(
            "Quarterly expenditure forecasting models and fiscal projections",
            importance=0.7,
            entities=["fiscal_model"],
        )
        # 3. Graph-only 2-hop relational memory (zero word overlap with query; reachable ONLY via 2-hop graph walk!)
        m_graph = mem.remember(
            "CFO Sarah approved hiring freeze during off-site retreat",
            importance=0.9,
            entities=["board_retreat"],
        )
        mem.associate("budget_q4.xlsx", "finance_review", "opened_after")
        mem.associate("finance_review", "board_retreat", "triggered_by")

        # Hybrid RRF recall must fuse and return items from ALL THREE legs (keyword, vector, graph)!
        hits = mem.recall("budget_q4.xlsx forecasting expenditure", k=10, strategy="hybrid")
        hit_ids = {h["mem_id"]: h for h in hits}
        assert m_kw in hit_ids and "keyword" in hit_ids[m_kw]["legs"]
        assert m_vec in hit_ids and "vector" in hit_ids[m_vec]["legs"]
        assert m_graph in hit_ids and "graph" in hit_ids[m_graph]["legs"]

        # Verify all 3 leg names appear across returned results
        all_legs = {leg for h in hits for leg in h["legs"]}
        assert {"keyword", "vector", "graph"}.issubset(all_legs)

        # Verify individual leg isolation too
        kw_only = mem.recall("budget_q4.xlsx", k=5, strategy="keyword")
        assert kw_only and all(h["legs"] == ["keyword"] for h in kw_only)
        graph_only = mem.recall("budget_q4.xlsx", k=5, strategy="graph")
        assert any(h["mem_id"] == m_graph for h in graph_only)
    finally:
        mem.close()


def test_graph_walk_ebbinghaus_decay_minhash_dedupe_and_stability_guarantees(tmp_path: Path):
    db_file = tmp_path / "memory.db"
    ev_file = tmp_path / "evidence" / "ledger.jsonl"
    mem = memory.CelinaMemory(db_path=db_file, prefer_ariadne=False)
    try:
        # Stability Guarantee #1: WAL mode + busy_timeout >= 5000ms
        pragmas = mem.verify_sqlite_pragmas()
        assert pragmas["journal_mode"] == "wal"
        assert pragmas["busy_timeout"] >= 5000

        # Multi-hop WITH RECURSIVE CTE walk
        mem.associate("goal_deploy", "step_docker", "uses")
        mem.associate("step_docker", "old_dockerfile", "failed_attempt")
        mem.remember("Old Dockerfile lacked non-root user and failed CI", importance=0.85, entities=["old_dockerfile"])
        walk = mem.graph_walk("goal_deploy", hops=2)
        assert "old_dockerfile" in walk["nodes"]
        assert walk["node_depths"]["old_dockerfile"] == 2
        assert len(walk["failed_attempts"]) == 1
        assert any("Dockerfile" in m["text"] for m in walk["memories"])

        # Ebbinghaus retention decay + MinHash near-duplicate deduplication
        now = time.time()
        very_old = now - (400 * 86400)  # 400 days old -> retention < 0.05 -> archived
        mem.remember("Ancient ephemeral scratch note from last year", importance=0.3, created_at=very_old)
        mem.remember("Prefer dark theme and 8GB quantized local Ollama models on Ubuntu", importance=0.9, created_at=now)
        mem.remember("Prefer dark theme and 8GB quantized local Ollama models on Ubuntu!", importance=0.5, created_at=now - 10)

        maint = mem.decay_and_dedupe(now=now, min_retention=0.05, jaccard_threshold=0.75)
        assert maint["archived_decayed"] >= 1
        assert maint["deduped"] >= 1

        # Stability Guarantee #2: Single-file SQLite portability (celina-export copy)
        exported = tmp_path / "exported_memory.db"
        mem.close()
        shutil.copy2(db_file, exported)
        reopened = memory.CelinaMemory(db_path=exported, prefer_ariadne=False)
        try:
            walk2 = reopened.graph_walk("goal_deploy", hops=2)
            assert "old_dockerfile" in walk2["nodes"]
        finally:
            reopened.close()

        # Stability Guarantee #3: tamper-evident evidence ledger remains completely separate from memory.db
        ledger = evidence.EvidenceLedger(ledger_path=ev_file)
        ledger.record_security_event(event_type="audit_check", summary="separate evidence ledger")
        assert ev_file.exists() and ev_file.resolve() != db_file.resolve()
        ok, _msg, count = ledger.verify_chain()
        assert ok is True and count == 1
    finally:
        mem.close()


def test_memweave_router_and_autonomy_graph_walk_preplanning(tmp_path: Path):
    db = memory.MemoryDB(db_path=tmp_path / "memory.db")
    try:
        r = router.CelinaRouter(db=db)
        # MemWeave 3-way routing classification
        assert r._retrieval_strategy('what is my exact path "/etc/scx_loader/config.toml"') == "keyword"
        assert r._retrieval_strategy("something about roughly fuzzy audio tuning vibe") == "vector"
        assert r._retrieval_strategy("why did I open that report three weeks after that meeting") == "graph"
        assert r._retrieval_strategy("summarize my project priorities") == "hybrid"

        # Seed unified memory & verify retrieve_context tags /why TurnTrace
        r._memory.remember("Meeting notes with Priya about Q4 launch", importance=0.85, entities=["q4_launch"])
        r._memory.associate("launch_doc", "q4_launch", "opened_after")
        tr = router.TurnTrace(prompt_preview="why did I open launch_doc after that meeting")
        ctx_hits = r.retrieve_context("why did I open launch_doc after that meeting", k=5, trace=tr)
        assert tr.memweave_strategy == "graph"
        assert "MemWeave" in tr.format_receipt()
        assert any("Priya" in h["text"] for h in ctx_hits)

        # AutonomyEngine pre-planning calls graph_walk() & passes known_failed_attempts to router
        r._memory.associate("deploy", "legacy_ftp_upload", "failed_attempt")
        graph = TaskGraph(tmp_path / "tasks.db")
        try:
            graph.insert_task("t-plan", None, "deploy release artifacts safely", "agent")
            captured_contexts: list[dict] = []
            mock_router = mock.MagicMock()

            def _route_side_effect(goal, context=None):
                captured_contexts.append(dict(context or {}))
                return {
                    "tool_name": "bad_deploy_tool",
                    "tier": 1,
                    "domain": "files_office",
                    "arguments": {},
                    "arguments_hash": "h-deploy",
                    "why_receipt_hash": "w-deploy",
                }

            mock_router.route_for_autonomy.side_effect = _route_side_effect
            mock_exec = mock.MagicMock(spec=SelfHealingExecutor)
            mock_exec.execute.return_value = ExecResult(
                success=False, exit_code=1, summary="", error="connection refused"
            )

            eng = AutonomyEngine(
                graph=graph,
                executor=mock_exec,
                router=mock_router,
                approval_gate=mock.MagicMock(),
                observe_only=False,
                memory=r._memory,
            )
            with mock.patch("celina_pkg.autonomy.engine._trusted_autonomy_dispatch_available", return_value=True):
                eng.step()

            # Pre-planning surfaced prior failed attempt before routing!
            assert len(captured_contexts) == 1
            known_failed = captured_contexts[0].get("known_failed_attempts") or []
            assert any(e["dst"] == "legacy_ftp_upload" for e in known_failed)

            # New failure was automatically recorded in the memory graph for future runs!
            walk_after = r._memory.graph_walk("deploy", hops=1)
            assert any(e["dst"] == "bad_deploy_tool" and e["relation"] == "failed_attempt" for e in walk_after["edges"])
        finally:
            graph.close()
    finally:
        db.close()


def test_v44_to_v45_migration_script(tmp_path: Path):
    src_db = tmp_path / "v44_memory.db"
    dst_db = tmp_path / "v45_memory.db"

    legacy = memory.MemoryDB(db_path=src_db)
    try:
        legacy.set_fact("default_editor", "neovim")
        legacy.add_knowledge_triple("budget_doc", "opened_after", "design_review")
        legacy.upsert_goal("g-1", "Ship Celina v4.5 hybrid memory", status="active")
    finally:
        legacy.close()

    spec = importlib.util.spec_from_file_location(
        "migrate_memory_v45", REPO_ROOT / "tools" / "migrate-memory-v45.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Dry-run creates zero destination files
    dry = mod.migrate_memory_db(src_db, dst_db=dst_db, dry_run=True)
    assert dry["facts_migrated"] >= 1 and dry["triples_migrated"] >= 1 and dry["goals_migrated"] >= 1
    assert not dst_db.exists()

    # Live migration produces WAL-enabled single-file hybrid DB
    res = mod.migrate_memory_db(src_db, dst_db=dst_db, dry_run=False)
    assert res["evidence_db_touched"] is False
    assert res["pragmas"]["journal_mode"] == "wal"
    assert res["pragmas"]["busy_timeout"] >= 5000

    migrated = memory.CelinaMemory(db_path=dst_db, prefer_ariadne=False)
    try:
        hits = migrated.recall("neovim default_editor", k=5)
        assert any("neovim" in h["text"] for h in hits)
        walk = migrated.graph_walk("budget_doc", hops=2)
        assert "design_review" in walk["nodes"]
    finally:
        migrated.close()
