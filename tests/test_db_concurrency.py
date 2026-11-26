"""
Suite 12 — SQLite WAL Concurrency & Kicker Knowledge-Graph Promotion Tests (v4.4.0)

Verifies:
  1. All 4 Celina SQLite databases (`memory.db`, `cache.db`, `spotlight_index.db`,
     `kg.db`) run with PRAGMA journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL.
  2. 4 concurrent threads reading and writing each DB simultaneously produce ZERO
     `database is locked` errors.
  3. Kicker two-layer promotion pipeline (`kg_promoter.py`) promotes post-meeting
     file opens and repeated app workflows into `kg_triples` and supports cycle-safe
     recursive CTE multi-hop graph traversal.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg.cache import SmartCache  # noqa: E402
from celina_pkg.kg_promoter import KnowledgeGraphPromoter  # noqa: E402
from celina_pkg.memory import MemoryDB  # noqa: E402
from celina_pkg.spotlight_index import SpotlightIndex  # noqa: E402


def _assert_wal_pragmas(conn: sqlite3.Connection) -> None:
    mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
    timeout = int(conn.execute("PRAGMA busy_timeout;").fetchone()[0])
    sync = int(conn.execute("PRAGMA synchronous;").fetchone()[0])
    assert mode == "wal", f"Expected WAL mode, got {mode}"
    assert timeout >= 5000, f"Expected busy_timeout>=5000, got {timeout}"
    assert sync == 1, f"Expected synchronous=NORMAL (1), got {sync}"


def test_four_thread_concurrent_access_across_all_four_sqlite_dbs(tmp_path: Path):
    mem = MemoryDB(db_path=tmp_path / "memory.db")
    cache = SmartCache(db_path=tmp_path / "cache.db")
    spot = SpotlightIndex(db_path=tmp_path / "spotlight_index.db")
    kg = KnowledgeGraphPromoter(db_path=tmp_path / "kg.db")

    try:
        for db_obj in (mem, cache, spot, kg):
            _assert_wal_pragmas(db_obj._conn)

        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(25):
                    mem.set_fact(f"k_{tid}_{i}", f"val_{tid}_{i}")
                    _ = mem.recall_relevant(f"val_{tid}", max_facts=5)

                    cache.set("llm_fast", f"prompt_{tid}_{i}", f"reply_{tid}_{i}", ttl_seconds=60)
                    _ = cache.get("llm_fast", f"prompt_{tid}_{i}")

                    kg.record_event("app_launched", f"app_{tid}_{i % 3}")
                    if i % 5 == 0:
                        kg.promote_pending_events(min_workflow_repeats=1)

                    with spot._lock:
                        spot._conn.execute(
                            "INSERT OR REPLACE INTO indexed_files (path, name, ext, size_bytes, mtime) VALUES (?, ?, ?, ?, ?)",
                            (f"/tmp/doc_{tid}_{i}.txt", f"doc_{tid}_{i}.txt", ".txt", 128, time.time()),
                        )
                        spot._conn.commit()
                    _ = spot.search(f"doc_{tid}", limit=5)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"SQLite concurrency errors detected: {errors}"
    finally:
        mem.close()
        cache.close()
        spot.close()
        kg.close()


def test_kicker_kg_promotion_and_recursive_cte_traversal(tmp_path: Path):
    kg = KnowledgeGraphPromoter(db_path=tmp_path / "kg.db")
    try:
        assert kg.get_schema_version() == "4.4.0"
        t0 = time.time() - 500.0

        # Meeting followed shortly by opening Q4_Budget.xlsx
        kg.record_event("meeting_ended", "Q4_Finance_Sync", ts=t0)
        kg.record_event("file_opened", "Q4_Budget.xlsx", ts=t0 + 120.0)

        # Repeated workflow: vscode -> terminal -> firefox (2x)
        kg.record_event("app_launched", "vscode", ts=t0 + 200.0)
        kg.record_event("app_launched", "terminal", ts=t0 + 220.0)
        kg.record_event("app_launched", "firefox", ts=t0 + 240.0)
        kg.record_event("app_launched", "vscode", ts=t0 + 300.0)
        kg.record_event("app_launched", "terminal", ts=t0 + 320.0)
        kg.record_event("app_launched", "firefox", ts=t0 + 340.0)

        stats = kg.promote_pending_events(min_workflow_repeats=2)
        assert stats["events_scanned"] == 8
        assert stats["triples_promoted"] >= 3

        # Post-meeting file association promoted
        meeting_links = kg.query_related_entities("Q4_Budget.xlsx", max_hops=2)
        assert any(
            row["via_predicate"] == "opened_after_meeting" and row["target"] == "Q4_Finance_Sync"
            for row in meeting_links
        )

        # Multi-hop recursive CTE traversal: vscode -> terminal (hop 1) -> firefox (hop 2)
        workflow_hops = kg.query_related_entities("vscode", max_hops=3)
        targets_by_depth = {(row["target"], row["depth"]) for row in workflow_hops}
        assert ("terminal", 1) in targets_by_depth
        assert ("firefox", 2) in targets_by_depth

        # Idempotent second pass scans 0 already-promoted events
        second = kg.promote_pending_events()
        assert second["events_scanned"] == 0
    finally:
        kg.close()
