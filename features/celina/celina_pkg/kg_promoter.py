"""Celina AI — Kicker Two-Layer Knowledge-Graph Promotion Pipeline (`kg.db`)

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
import datetime
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import config

KG_SCHEMA_VERSION = "4.4.0"


class KnowledgeGraphPromoter:
    """Thread-safe SQLite WAL two-layer event -> property-graph promoter."""

    def __init__(self, db_path: Path | None = None):
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
                """,
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
        meta: dict[str, Any] | None = None,
        ts: float | None = None,
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
                "SELECT id, ts, kind, entity, meta_json FROM raw_events WHERE promoted = 0 ORDER BY ts ASC, id ASC",
            ).fetchall()
            if not rows:
                return {"events_scanned": 0, "triples_promoted": 0}

            last_meeting: tuple[float, str] | None = None
            app_transitions: dict[tuple[str, str], int] = {}
            prev_app: tuple[float, str] | None = None

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

    def consolidate_memory(
        self,
        db_path: Path | None = None,
        *,
        stale_days: int = 30,
        decay_step: float = 0.15,
        prune_threshold: float = 0.30,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """Transactional Merge → Decay → Soft-Prune consolidator (Memory v2).

        Executes inside a single SQLite `BEGIN IMMEDIATE` transaction (or read-only
        counts when `dry_run=True`). Never hard-DELETEs rows — soft-prunes via
        `valid_until` so history stays auditable.

        Steps:
          1. Merge: deduplicate active kg_triples sharing normalized
             (lower(trim(subject)), lower(trim(predicate)), lower(trim(object)))
             — keep highest confidence + earliest valid_from, soft-invalidate dups.
          2. Decay: reduce confidence = max(0, confidence - decay_step) on active
             kg_triples/facts older than `stale_days`, NEVER decaying
             source_trust == 'user_direct' rows with category in ('preference','decision').
          3. Soft-Prune: set valid_until = now_utc on active non-pinned rows whose
             confidence < prune_threshold.

        Returns {"merged": int, "decayed": int, "soft_pruned": int}.
        """
        target = Path(db_path) if db_path is not None else config.DB_PATH
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        now_ts = datetime.datetime.now(datetime.UTC).timestamp()
        stale_cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=stale_days)
        stale_cutoff_iso = stale_cutoff.isoformat()
        stale_cutoff_ts = stale_cutoff.timestamp()

        merged = 0
        decayed = 0
        soft_pruned = 0

        # Use the promoter's existing connection when operating on its own db_path
        # to avoid WAL visibility issues between separate connections.
        _owns_conn = False
        if db_path is None:
            conn = self._conn
        else:
            conn = sqlite3.connect(str(target), timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            _owns_conn = True
        try:
            if dry_run:
                # Read-only counts — no writes
                # Merge count: duplicates beyond the canonical survivor
                dup_rows = conn.execute(
                    """
                    SELECT COUNT(*) AS dup_cnt FROM (
                        SELECT subject, predicate, object, COUNT(*) AS cnt
                        FROM kg_triples
                        WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                        GROUP BY lower(trim(subject)), lower(trim(predicate)), lower(trim(object))
                        HAVING cnt > 1
                    )
                    """,
                    (now_iso,),
                ).fetchone()
                merged = int(dup_rows["dup_cnt"]) if dup_rows else 0

                # Decay count: stale non-pinned rows that would be decayed
                decay_rows = conn.execute(
                    """
                    SELECT COUNT(*) AS dec_cnt FROM (
                        SELECT rowid FROM kg_triples
                        WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                          AND NOT (source_trust = 'user_direct' AND category IN ('preference', 'decision'))
                          AND (updated_at < ? OR datetime(valid_from) < ?)
                          AND confidence > 0.0
                    UNION ALL
                        SELECT rowid FROM facts
                        WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                          AND NOT (source_trust = 'user_direct' AND category IN ('preference', 'decision'))
                          AND (updated_at < ? OR datetime(valid_from) < ?)
                          AND confidence > 0.0
                    )
                    """,
                    (now_iso, stale_cutoff_ts, stale_cutoff_iso,
                     now_iso, stale_cutoff_ts, stale_cutoff_iso),
                ).fetchone()
                decayed = int(decay_rows["dec_cnt"]) if decay_rows else 0

                # Soft-prune count: active non-pinned rows below threshold
                prune_rows = conn.execute(
                    """
                    SELECT COUNT(*) AS prune_cnt FROM (
                        SELECT rowid FROM kg_triples
                        WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                          AND confidence < ?
                          AND NOT (source_trust = 'user_direct' AND category IN ('preference', 'decision'))
                    UNION ALL
                        SELECT rowid FROM facts
                        WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                          AND confidence < ?
                          AND NOT (source_trust = 'user_direct' AND category IN ('preference', 'decision'))
                    )
                    """,
                    (now_iso, prune_threshold, now_iso, prune_threshold),
                ).fetchone()
                soft_pruned = int(prune_rows["prune_cnt"]) if prune_rows else 0

                return {"merged": merged, "decayed": decayed, "soft_pruned": soft_pruned}

            with conn:
                conn.execute("BEGIN IMMEDIATE")
                # --- Step 1: Merge duplicates ---
                # Find canonical survivor per normalized (subject, predicate, object)
                dup_groups = conn.execute(
                    """
                    SELECT lower(trim(subject)) AS ns, lower(trim(predicate)) AS np,
                           lower(trim(object)) AS no, COUNT(*) AS cnt
                    FROM kg_triples
                    WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                    GROUP BY ns, np, no
                    HAVING cnt > 1
                    """,
                    (now_iso,),
                ).fetchall()
                for grp in dup_groups:
                    # All active dups for this normalized group
                    dups = conn.execute(
                        """
                        SELECT rowid, subject, predicate, object, confidence, valid_from, updated_at
                        FROM kg_triples
                        WHERE lower(trim(subject)) = ? AND lower(trim(predicate)) = ?
                          AND lower(trim(object)) = ?
                          AND (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                        ORDER BY confidence DESC, valid_from ASC, updated_at ASC
                        """,
                        (grp["ns"], grp["np"], grp["no"], now_iso),
                    ).fetchall()
                    if len(dups) <= 1:
                        continue
                    survivor = dups[0]
                    for dup in dups[1:]:
                        conn.execute(
                            "UPDATE kg_triples SET valid_until = ? WHERE rowid = ?",
                            (now_iso, dup["rowid"]),
                        )
                        merged += 1

                # --- Step 2: Decay ---
                # Decay kg_triples (updated_at is a Unix REAL timestamp; compare numerically)
                decay_rows = conn.execute(
                    """
                    SELECT rowid, confidence FROM kg_triples
                    WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                      AND NOT (source_trust = 'user_direct' AND category IN ('preference', 'decision'))
                      AND (updated_at < ? OR datetime(valid_from) < ?)
                      AND confidence > 0.0
                    """,
                    (now_iso, stale_cutoff_ts, stale_cutoff_iso),
                ).fetchall()
                for r in decay_rows:
                    new_conf = max(0.0, float(r["confidence"]) - decay_step)
                    conn.execute(
                        "UPDATE kg_triples SET confidence = ? WHERE rowid = ?",
                        (new_conf, r["rowid"]),
                    )
                    decayed += 1

                # Decay facts (updated_at is a Unix REAL timestamp; compare numerically)
                decay_rows = conn.execute(
                    """
                    SELECT rowid, confidence FROM facts
                    WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                      AND NOT (source_trust = 'user_direct' AND category IN ('preference', 'decision'))
                      AND (updated_at < ? OR datetime(valid_from) < ?)
                      AND confidence > 0.0
                    """,
                    (now_iso, stale_cutoff_ts, stale_cutoff_iso),
                ).fetchall()
                for r in decay_rows:
                    new_conf = max(0.0, float(r["confidence"]) - decay_step)
                    conn.execute(
                        "UPDATE facts SET confidence = ? WHERE rowid = ?",
                        (new_conf, r["rowid"]),
                    )
                    decayed += 1

                # --- Step 3: Soft-Prune ---
                prune_rows = conn.execute(
                    """
                    SELECT rowid FROM kg_triples
                    WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                      AND confidence < ?
                      AND NOT (source_trust = 'user_direct' AND category IN ('preference', 'decision'))
                    """,
                    (now_iso, prune_threshold),
                ).fetchall()
                for r in prune_rows:
                    conn.execute(
                        "UPDATE kg_triples SET valid_until = ? WHERE rowid = ?",
                        (now_iso, r["rowid"]),
                    )
                    soft_pruned += 1

                prune_rows = conn.execute(
                    """
                    SELECT rowid FROM facts
                    WHERE (valid_until IS NULL OR valid_until = '' OR valid_until > ?)
                      AND confidence < ?
                      AND NOT (source_trust = 'user_direct' AND category IN ('preference', 'decision'))
                    """,
                    (now_iso, prune_threshold),
                ).fetchall()
                for r in prune_rows:
                    conn.execute(
                        "UPDATE facts SET valid_until = ? WHERE rowid = ?",
                        (now_iso, r["rowid"]),
                    )
                    soft_pruned += 1

                conn.commit()
            return {"merged": merged, "decayed": decayed, "soft_pruned": soft_pruned}
        finally:
            if _owns_conn:
                conn.close()

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
    parser.add_argument("--consolidate-once", action="store_true", help="Run a single Memory v2 consolidation pass and exit")
    parser.add_argument("--daemon", action="store_true", help="Run periodic background promotion loop")
    parser.add_argument("--interval", type=float, default=30.0, help="Promotion interval seconds in daemon mode")
    args = parser.parse_args()

    promoter = KnowledgeGraphPromoter()
    if args.promote_once:
        print(json.dumps(promoter.promote_pending_events()))
        promoter.close()
        return 0
    if args.consolidate_once:
        print(json.dumps(promoter.consolidate_memory()))
        promoter.close()
        return 0
    if args.daemon:
        try:
            while True:
                promoter.promote_pending_events()
                # Memory v2: Merge → Decay → Soft-Prune each cycle
                promoter.consolidate_memory()
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
