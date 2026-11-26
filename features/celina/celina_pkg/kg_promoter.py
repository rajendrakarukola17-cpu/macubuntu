"""
Celina AI — Kicker Two-Layer Knowledge-Graph Promotion Pipeline (`kg.db`)

Architecture:
  - Layer 1 (`raw_events` in SQLite WAL): absorbs high-frequency OS/eBPF/user
    events (`meeting_ended`, `file_opened`, `app_launched`, `net_connect`).
  - Layer 2 (`kg_triples` in SQLite WAL): periodic promotion pass elevates
    semantically meaningful patterns (e.g., files opened shortly after a meeting,
    repeated app/tool workflows) into an embedded SQLite property/triple graph
    queried with recursive CTEs (zero extra native dependencies).
  - Driven cleanly in the background by `celina-memory.service` (AF_UNIX only).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import config

KG_SCHEMA_VERSION = "4.4.0"


class KnowledgeGraphPromoter:
    """Thread-safe SQLite WAL two-layer event -> property-graph promoter."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else (config.BASE_DIR / "kg.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        self._configure_and_init()

    def _configure_and_init(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA temp_store=MEMORY;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS raw_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    promoted INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_raw_events_promoted_ts ON raw_events (promoted, ts);

                CREATE TABLE IF NOT EXISTS kg_triples (
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    support_count INTEGER NOT NULL DEFAULT 1,
                    confidence REAL NOT NULL DEFAULT 0.6,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (subject, predicate, object)
                );
                CREATE INDEX IF NOT EXISTS idx_kg_triples_subj ON kg_triples (subject);
                CREATE INDEX IF NOT EXISTS idx_kg_triples_obj ON kg_triples (object);
                """
            )
            self._conn.execute(
                """
                INSERT INTO schema_version (id, version, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at
                """,
                (KG_SCHEMA_VERSION, time.time()),
            )
            self._conn.commit()

    def record_event(
        self,
        kind: str,
        entity: str,
        *,
        meta: Optional[dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> int:
        now = float(time.time() if ts is None else ts)
        payload = json.dumps(meta or {}, sort_keys=True)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO raw_events (ts, kind, entity, meta_json, promoted) VALUES (?, ?, ?, ?, 0)",
                (now, kind.strip(), entity.strip(), payload),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def _upsert_triple(self, subject: str, predicate: str, obj: str, *, boost: float = 0.15) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO kg_triples (subject, predicate, object, support_count, confidence, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(subject, predicate, object) DO UPDATE SET
                support_count = support_count + 1,
                confidence = MIN(0.99, confidence + ?),
                updated_at = excluded.updated_at
            """,
            (subject, predicate, obj, min(0.95, 0.5 + boost), now, boost),
        )

    def promote_pending_events(
        self,
        *,
        meeting_window_sec: float = 1800.0,
        workflow_window_sec: float = 600.0,
        min_workflow_repeats: int = 2,
    ) -> dict[str, int]:
        """Promote semantically significant raw events into `kg_triples`."""
        promoted_triples = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, kind, entity, meta_json FROM raw_events WHERE promoted = 0 ORDER BY ts ASC, id ASC"
            ).fetchall()
            if not rows:
                return {"events_scanned": 0, "triples_promoted": 0}

            last_meeting: Optional[tuple[float, str]] = None
            app_transitions: dict[tuple[str, str], int] = {}
            prev_app: Optional[tuple[float, str]] = None

            for r in rows:
                ts = float(r["ts"])
                kind = str(r["kind"])
                entity = str(r["entity"])

                if kind == "meeting_ended":
                    last_meeting = (ts, entity)
                elif kind == "file_opened" and last_meeting is not None:
                    if 0.0 <= (ts - last_meeting[0]) <= meeting_window_sec:
                        self._upsert_triple(entity, "opened_after_meeting", last_meeting[1], boost=0.2)
                        promoted_triples += 1
                elif kind == "app_launched":
                    if prev_app is not None and prev_app[1] != entity:
                        if 0.0 <= (ts - prev_app[0]) <= workflow_window_sec:
                            pair = (prev_app[1], entity)
                            app_transitions[pair] = app_transitions.get(pair, 0) + 1
                    prev_app = (ts, entity)

            for (src_app, dst_app), cnt in app_transitions.items():
                if cnt >= max(1, int(min_workflow_repeats)):
                    self._upsert_triple(src_app, "workflow_followed_by", dst_app, boost=0.1 * cnt)
                    promoted_triples += 1

            max_id = int(rows[-1]["id"])
            self._conn.execute("UPDATE raw_events SET promoted = 1 WHERE id <= ? AND promoted = 0", (max_id,))
            self._conn.commit()
            return {"events_scanned": len(rows), "triples_promoted": promoted_triples}

    def query_related_entities(self, start_entity: str, *, max_hops: int = 3) -> list[dict[str, Any]]:
        """Recursive CTE multi-hop traversal over `kg_triples` (bounded, cycle-safe)."""
        hops = max(1, min(int(max_hops), 6))
        with self._lock:
            rows = self._conn.execute(
                """
                WITH RECURSIVE walk(node, via_predicate, target, depth, confidence, path) AS (
                    SELECT subject, predicate, object, 1, confidence, '|' || subject || '|' || object || '|'
                    FROM kg_triples
                    WHERE subject = ?
                    UNION ALL
                    SELECT t.subject, t.predicate, t.object, w.depth + 1,
                           ROUND(w.confidence * t.confidence, 4),
                           w.path || t.object || '|'
                    FROM kg_triples t
                    JOIN walk w ON t.subject = w.target
                    WHERE w.depth < ?
                      AND instr(w.path, '|' || t.object || '|') = 0
                )
                SELECT node, via_predicate, target, depth, confidence
                FROM walk
                ORDER BY depth ASC, confidence DESC
                LIMIT 50
                """,
                (start_entity.strip(), hops),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_schema_version(self) -> str:
        with self._lock:
            row = self._conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return str(row["version"]) if row else "0.0.0"

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Celina Kicker Knowledge-Graph Promoter (kg.db)")
    parser.add_argument("--promote-once", action="store_true", help="Run a single promotion pass and exit")
    parser.add_argument("--daemon", action="store_true", help="Run periodic background promotion loop")
    parser.add_argument("--interval", type=float, default=30.0, help="Promotion interval seconds in daemon mode")
    args = parser.parse_args()

    promoter = KnowledgeGraphPromoter()
    if args.promote_once:
        print(json.dumps(promoter.promote_pending_events()))
        promoter.close()
        return 0
    if args.daemon:
        try:
            while True:
                promoter.promote_pending_events()
                time.sleep(max(5.0, float(args.interval)))
        except KeyboardInterrupt:
            pass
        finally:
            promoter.close()
        return 0
    parser.print_help()
    promoter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
