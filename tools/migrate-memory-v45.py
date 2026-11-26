#!/usr/bin/env python3
"""
macubuntu v4.4 -> v4.5 Memory Migration Utility (`migrate-memory-v45.py`)

Reads an existing Celina v4.4 SQLite `memory.db` (`facts`, `episodes`, `kg_triples`,
`goals`) and migrates all records into the unified single-file hybrid `CelinaMemory`
engine (`hybrid_memories`, `hybrid_memories_fts`, `hybrid_graph_edges`) while
enforcing WAL mode + 5000ms busy_timeout and keeping `evidence.db` strictly separate.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_pkg.memory import CelinaMemory  # noqa: E402


def migrate_memory_db(
    src_db: Path,
    *,
    dst_db: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    src = Path(src_db).expanduser().resolve()
    dst = Path(dst_db).expanduser().resolve() if dst_db is not None else src
    if not src.exists():
        raise FileNotFoundError(f"Source memory DB not found: {src}")

    conn = sqlite3.connect(str(src), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        facts = conn.execute("SELECT key, value, confidence, updated_at FROM facts").fetchall() if "facts" in tables else []
        episodes = (
            conn.execute("SELECT id, summary, created_at FROM episodes").fetchall()
            if "episodes" in tables
            else []
        )
        triples = (
            conn.execute("SELECT subject, predicate, object, confidence FROM kg_triples").fetchall()
            if "kg_triples" in tables
            else []
        )
        goals = (
            conn.execute("SELECT goal_id, title, status, updated_at FROM goals").fetchall()
            if "goals" in tables
            else []
        )
    finally:
        conn.close()

    summary: dict[str, Any] = {
        "src_db": str(src),
        "dst_db": str(dst),
        "dry_run": bool(dry_run),
        "facts_migrated": len(facts),
        "episodes_migrated": len(episodes),
        "triples_migrated": len(triples),
        "goals_migrated": len(goals),
        "evidence_db_touched": False,
    }
    if dry_run:
        return summary

    mem = CelinaMemory(db_path=dst, prefer_ariadne=False)
    try:
        for row in facts:
            k = str(row["key"])
            v = str(row["value"])
            imp = min(1.0, max(0.2, float(row["confidence"] or 0.7)))
            mem.remember(
                f"{k}: {v}",
                importance=imp,
                meta={"source": "v44_fact", "key": k},
                entities=[k],
                mem_id=f"v44_fact:{k}",
                created_at=float(row["updated_at"] or 0.0) or None,
            )
            mem.associate(k, v[:80], "has_fact")

        for row in episodes:
            eid = str(row["id"])
            summ = str(row["summary"] or "").strip()
            if not summ:
                continue
            mem.remember(
                summ,
                importance=0.65,
                meta={"source": "v44_episode", "episode_id": eid},
                mem_id=f"v44_episode:{eid}",
                created_at=float(row["created_at"] or 0.0) or None,
            )

        for row in triples:
            subj = str(row["subject"]).strip()
            pred = str(row["predicate"]).strip()
            obj = str(row["object"]).strip()
            if subj and pred and obj:
                mem.associate(subj, obj, pred, weight=float(row["confidence"] or 1.0))

        for row in goals:
            gid = str(row["goal_id"])
            title = str(row["title"] or "").strip()
            status = str(row["status"] or "active")
            if title:
                mem.remember(
                    f"Goal ({status}): {title}",
                    importance=0.85 if status == "active" else 0.45,
                    meta={"source": "v44_goal", "goal_id": gid, "status": status},
                    entities=[gid, title],
                    mem_id=f"v44_goal:{gid}",
                    created_at=float(row["updated_at"] or 0.0) or None,
                )
                mem.associate(gid, status, "goal_status")

        summary["pragmas"] = mem.verify_sqlite_pragmas()
    finally:
        mem.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate Celina v4.4 memory.db to v4.5 unified hybrid CelinaMemory")
    parser.add_argument("--src", required=True, help="Path to existing v4.4 memory.db")
    parser.add_argument("--dst", default=None, help="Destination memory.db (defaults to in-place on --src)")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without mutating any SQLite file")
    args = parser.parse_args()
    res = migrate_memory_db(Path(args.src), dst_db=Path(args.dst) if args.dst else None, dry_run=args.dry_run)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
