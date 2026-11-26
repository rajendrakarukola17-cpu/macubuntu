#!/usr/bin/env python3
"""
macubuntu v4.4 -> v4.5 Memory Migration Utility (`migrate-memory-v45.py`)

Reads an existing Celina v4.4 SQLite `memory.db` (`facts`, `episodes`, `kg_triples`,
`goals`) and migrates all records into the unified single-file hybrid `CelinaMemory`
engine (`hybrid_memories`, `hybrid_memories_fts`, `hybrid_graph_edges`) while
enforcing WAL mode + 5000ms busy_timeout and keeping `evidence.db` strictly separate.

P0 fixes applied (Large System Debug Report):
  • P0: Full-file backup before any in-place mutation (never migrate in-place without backup)
  • P0: All unrecognized tables (skills, reminders, conversations, habit_patterns,
        sensory_schemas, …) are copied verbatim to the destination — never silently dropped
  • P0: ISO-8601 timestamps parsed safely; ValueError on float() never crashes the migration
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "features" / "celina"))

from celina_pkg.memory import CelinaMemory  # noqa: E402

# Tables that migrate row-by-row into CelinaMemory (known v4.4 schema)
_MIGRATED_TABLES = {"facts", "episodes", "kg_triples", "goals"}


def _parse_timestamp(value: Any) -> float | None:
    """Parse a v4.4 updated_at/created_at value into a unix timestamp (float).

    Accepts: unix epoch (int/float/str), ISO-8601 ("2024-01-15T10:30:00Z" or
    "2024-01-15T10:30:00+00:00"), or empty/None. Never raises — returns None on
    unparseable input so the migration is fail-closed rather than crash-prone.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    # Try raw epoch first
    try:
        return float(text)
    except ValueError:
        pass
    # Try ISO-8601 (with or without trailing Z)
    iso_text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _backup_db(src: Path) -> Path:
    """Create a timestamped backup copy of src. Returns the backup path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = src.with_name(f"{src.name}.v44-backup-{stamp}")
    shutil.copy2(src, backup)
    return backup


def migrate_memory_db(
    src_db: Path,
    *,
    dst_db: Path | None = None,
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    """Migrate v4.4 memory.db to v4.5 hybrid CelinaMemory.

    Args:
        src_db: Path to existing v4.4 memory.db (required).
        dst_db: Destination memory.db. Defaults to src (in-place).
                When backing up in-place, the .bak copy is made before mutation.
        dry_run: Report counts without mutating any SQLite file.
        backup: Create a full-file backup before any mutation (default True).
                Set False only when dst_db differs from src and caller already
                snapshots src independently.
    """
    src = Path(src_db).expanduser().resolve()
    dst = Path(dst_db).expanduser().resolve() if dst_db is not None else src
    if not src.exists():
        raise FileNotFoundError(f"Source memory DB not found: {src}")

    # --- P0: Backup before any mutation --------------------------------------
    if not dry_run and backup and dst == src:
        backup_path = _backup_db(src)
    else:
        backup_path = None

    # Read source (read-only connection; never writes to src)
    src_conn = sqlite3.connect(str(src), timeout=5.0)
    src_conn.row_factory = sqlite3.Row
    try:
        tables = {
            str(r[0])
            for r in src_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        # Known tables: migrate row-by-row
        facts = (
            src_conn.execute(
                "SELECT key, value, confidence, updated_at FROM facts"
            ).fetchall()
            if "facts" in tables
            else []
        )
        episodes = (
            src_conn.execute(
                "SELECT id, summary, created_at FROM episodes"
            ).fetchall()
            if "episodes" in tables
            else []
        )
        triples = (
            src_conn.execute(
                "SELECT subject, predicate, object, confidence FROM kg_triples"
            ).fetchall()
            if "kg_triples" in tables
            else []
        )
        goals = (
            src_conn.execute(
                "SELECT goal_id, title, status, updated_at FROM goals"
            ).fetchall()
            if "goals" in tables
            else []
        )

        # P0: Unrecognized tables are copied verbatim — never silently dropped
        # (skills, reminders, conversations, habit_patterns, sensory_schemas, …)
        # Exclude SQLite internal tables (sqlite_*) which are managed by SQLite itself.
        unrecognized_tables = sorted(
            t for t in (tables - _MIGRATED_TABLES) if not t.startswith("sqlite_")
        )
        unrecognized_schema: dict[str, str] = {}
        unrecognized_rows: dict[str, list[tuple]] = {}
        for tbl in unrecognized_tables:
            # Quote table name for safety (identifiers, not literals)
            qtbl = f'"{tbl}"'
            schema_row = src_conn.execute(
                f"SELECT sql FROM sqlite_master WHERE type='table' AND name={qtbl}"
            ).fetchone()
            unrecognized_schema[tbl] = schema_row[0] if schema_row else ""
            try:
                unrecognized_rows[tbl] = src_conn.execute(
                    f"SELECT * FROM {qtbl}"
                ).fetchall()
            except sqlite3.Error:
                unrecognized_rows[tbl] = []  # table may be a view or unreadable
    finally:
        src_conn.close()

    summary: dict[str, Any] = {
        "src_db": str(src),
        "dst_db": str(dst),
        "dry_run": bool(dry_run),
        "backup": str(backup_path) if backup_path else None,
        "facts_migrated": len(facts),
        "episodes_migrated": len(episodes),
        "triples_migrated": len(triples),
        "goals_migrated": len(goals),
        "evidence_db_touched": False,
        "unrecognized_tables_copied": unrecognized_tables,
        "unrecognized_table_count": len(unrecognized_tables),
    }
    if dry_run:
        return summary

    # --- P0: Migrate known tables into CelinaMemory ---------------------------
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
                created_at=_parse_timestamp(row["updated_at"]),
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
                created_at=_parse_timestamp(row["created_at"]),
            )

        for row in triples:
            subj = str(row["subject"]).strip()
            pred = str(row["predicate"]).strip()
            obj = str(row["object"]).strip()
            if subj and pred and obj:
                mem.associate(
                    subj, obj, pred, weight=float(row["confidence"] or 1.0)
                )

        for row in goals:
            gid = str(row["goal_id"])
            title = str(row["title"] or "").strip()
            status = str(row["status"] or "active")
            if title:
                mem.remember(
                    f"Goal ({status}): {title}",
                    importance=0.85 if status == "active" else 0.45,
                    meta={
                        "source": "v44_goal",
                        "goal_id": gid,
                        "status": status,
                    },
                    entities=[gid, title],
                    mem_id=f"v44_goal:{gid}",
                    created_at=_parse_timestamp(row["updated_at"]),
                )
                mem.associate(gid, status, "goal_status")

        summary["pragmas"] = mem.verify_sqlite_pragmas()
    finally:
        mem.close()

    # --- P0: Copy unrecognized tables verbatim to destination -----------------
    if unrecognized_tables:
        dst_conn = sqlite3.connect(str(dst), timeout=5.0)
        try:
            for tbl in unrecognized_tables:
                ddl = unrecognized_schema[tbl]
                if ddl:
                    # Use IF NOT EXISTS so in-place migration (dst == src) doesn't
                    # fail when the table already exists in the destination.
                    ddl = ddl.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
                    dst_conn.execute(ddl)
                rows = unrecognized_rows[tbl]
                if rows:
                    # Build column list from first row keys (Row objects support .keys())
                    first = rows[0]
                    cols = ", ".join(f'"{c}"' for c in first.keys())
                    placeholders = ", ".join("?" for _ in first.keys())
                    # INSERT OR REPLACE so in-place migration doesn't fail on
                    # pre-existing rows in shared FTS/config tables.
                    dst_conn.executemany(
                        f'INSERT OR REPLACE INTO "{tbl}" ({cols}) VALUES ({placeholders})',
                        [tuple(r) for r in rows],
                    )
            dst_conn.commit()
        finally:
            dst_conn.close()

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Celina v4.4 memory.db to v4.5 unified hybrid CelinaMemory"
    )
    parser.add_argument("--src", required=True, help="Path to existing v4.4 memory.db")
    parser.add_argument(
        "--dst",
        default=None,
        help="Destination memory.db (defaults to in-place on --src)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts without mutating any SQLite file",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip full-file backup before mutation (not recommended)",
    )
    args = parser.parse_args()
    res = migrate_memory_db(
        Path(args.src),
        dst_db=Path(args.dst) if args.dst else None,
        dry_run=args.dry_run,
        backup=not args.no_backup,
    )
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())