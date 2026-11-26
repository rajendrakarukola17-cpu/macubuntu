"""
Celina v5 Planner — Durable SQLite Task Graph (`tasks.db`)

Provides:
  - WAL + busy_timeout=5000 + synchronous=NORMAL thread-safe DAG storage
  - Control-flow state propagation (`sequence`, `branch`, `loop`, `retry`)
  - Frontier selection (`next_runnable()`) respecting sibling sequence ordering
  - Recursive cancellation (`cancel_task()`) and graph snapshot export
  - Tamper-evident SHA-256 hash-chained `evidence` log & chain verifier
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .control_flow import ControlFlowNode

AUTONOMY_SCHEMA_VERSION = "5.0.0"


class TaskGraph:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        self._init_schema()

    def _init_schema(self) -> None:
        schema = (Path(__file__).resolve().parent.parent / "schemas" / "autonomy_schema.sql").read_text(encoding="utf-8")
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(schema)
            self._conn.execute(
                """
                INSERT INTO schema_version (id, version, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at
                """,
                (AUTONOMY_SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()

    def insert_task(
        self,
        task_id: str,
        parent_id: Optional[str],
        goal: str,
        node_type: str,
        control_kind: Optional[str] = None,
        control_config: Optional[str | dict[str, Any]] = None,
        priority: int = 5,
        order_index: int = 0,
        max_attempts: int = 3,
        status: Optional[str] = None,
    ) -> str:
        cfg_json = (
            json.dumps(control_config, sort_keys=True)
            if isinstance(control_config, dict)
            else control_config
        )
        # Root control nodes activate as 'running' immediately so children become eligible
        effective_status = status
        if effective_status is None:
            effective_status = "running" if (parent_id is None and node_type == "control") else "pending"
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    id, parent_id, goal, status, node_type, control_kind,
                    control_config, priority, order_index, max_attempts, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    parent_id,
                    goal,
                    effective_status,
                    node_type,
                    control_kind,
                    cfg_json,
                    int(priority),
                    int(order_index),
                    max(1, int(max_attempts)),
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return task_id

    def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def get_children(self, parent_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE parent_id = ? ORDER BY order_index ASC, created_at ASC",
                (parent_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def advance_control_nodes(self, *, state: Optional[dict[str, Any]] = None) -> list[str]:
        """
        Propagate `sequence`/`branch`/`loop`/`retry` control-node state transitions
        and return the ordered list of currently runnable agent task IDs.
        """
        runnable_agents: list[str] = []
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            # 1. Promote pending control nodes whose parent is already running
            roots_or_active = self._conn.execute(
                """
                SELECT * FROM tasks
                WHERE node_type = 'control' AND status IN ('pending', 'running')
                ORDER BY priority ASC, order_index ASC, created_at ASC
                """
            ).fetchall()

            for ctrl_row in roots_or_active:
                ctrl = dict(ctrl_row)
                if ctrl["status"] == "pending":
                    parent_id = ctrl["parent_id"]
                    if parent_id is not None:
                        parent = self.get_task(parent_id)
                        if not parent or parent["status"] != "running":
                            continue
                    self._conn.execute(
                        "UPDATE tasks SET status='running', updated_at=? WHERE id=?",
                        (now, ctrl["id"]),
                    )
                    ctrl["status"] = "running"

                children = self.get_children(ctrl["id"])
                cf = ControlFlowNode.from_raw(ctrl["control_kind"], ctrl["control_config"])
                decision = cf.evaluate_children(children, state=state)

                for reset_id in decision["reset_child_ids"]:
                    self._conn.execute(
                        "UPDATE tasks SET status='pending', updated_at=? WHERE id=?",
                        (now, reset_id),
                    )
                if decision["updated_config"] != cf.config:
                    self._conn.execute(
                        "UPDATE tasks SET control_config=?, updated_at=? WHERE id=?",
                        (json.dumps(decision["updated_config"], sort_keys=True), now, ctrl["id"]),
                    )
                if decision["control_status"] in {"done", "failed"}:
                    self._conn.execute(
                        "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                        (decision["control_status"], now, ctrl["id"]),
                    )
                    continue

                for cid in decision["runnable_child_ids"]:
                    child = self.get_task(cid)
                    if not child:
                        continue
                    if child["node_type"] == "agent" and child["status"] == "pending":
                        runnable_agents.append(cid)
                    elif child["node_type"] == "control" and child["status"] == "pending":
                        self._conn.execute(
                            "UPDATE tasks SET status='running', updated_at=? WHERE id=?",
                            (now, cid),
                        )

            self._conn.commit()
        return runnable_agents

    def next_runnable(self, *, state: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        """Return the highest-priority pending agent task eligible under its parent control node."""
        with self._lock:
            # Run two passes so nested control nodes activate and expose their first leaf
            runnable_ids = self.advance_control_nodes(state=state)
            if not runnable_ids:
                runnable_ids = self.advance_control_nodes(state=state)

            if runnable_ids:
                placeholders = ",".join("?" for _ in runnable_ids)
                row = self._conn.execute(
                    f"""
                    SELECT * FROM tasks
                    WHERE id IN ({placeholders}) AND status = 'pending' AND node_type = 'agent'
                    ORDER BY priority ASC, order_index ASC, created_at ASC
                    LIMIT 1
                    """,
                    runnable_ids,
                ).fetchone()
                if row:
                    return dict(row)

            # Standalone top-level agent tasks with no parent control node
            row = self._conn.execute(
                """
                SELECT * FROM tasks
                WHERE parent_id IS NULL AND status = 'pending' AND node_type = 'agent'
                ORDER BY priority ASC, order_index ASC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row else None

    def mark(
        self,
        task_id: str,
        status: str,
        result: Optional[str] = None,
        error: Optional[str] = None,
        *,
        increment_attempt: bool = True,
        evidence_hash: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            if increment_attempt:
                self._conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, result_summary = COALESCE(?, result_summary),
                        last_error = ?, evidence_hash = COALESCE(?, evidence_hash),
                        updated_at = ?, attempts = attempts + 1
                    WHERE id = ?
                    """,
                    (status, result, error, evidence_hash, now, task_id),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, result_summary = COALESCE(?, result_summary),
                        last_error = ?, evidence_hash = COALESCE(?, evidence_hash),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (status, result, error, evidence_hash, now, task_id),
                )
            self._conn.commit()
            try:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
            except sqlite3.OperationalError:
                pass

    def recover_on_startup(self) -> list[str]:
        """
        Crash-recovery pass invoked at daemon/engine startup:
        Resets any agent task left stuck in `running` after an abrupt crash (`kill -9`)
        back to `pending` (with `last_error='recovered from crash'`) so the graph resumes
        cleanly without lost or permanently wedged tasks.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM tasks WHERE node_type = 'agent' AND status = 'running'"
            ).fetchall()
            recovered = [str(r["id"]) for r in rows]
            for tid in recovered:
                self.mark(tid, "pending", error="recovered from crash", increment_attempt=False)
            return recovered

    def cancel_task(self, task_id: str) -> int:
        """Cancel a task and all non-terminal descendants recursively."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            before = self._conn.total_changes
            self._conn.execute(
                """
                WITH RECURSIVE sub(id) AS (
                    SELECT id FROM tasks WHERE id = ?
                    UNION ALL
                    SELECT t.id FROM tasks t JOIN sub s ON t.parent_id = s.id
                )
                UPDATE tasks
                SET status = 'cancelled', last_error = 'cancelled_by_user', updated_at = ?
                WHERE id IN (SELECT id FROM sub) AND status NOT IN ('done', 'observed')
                """,
                (task_id, now),
            )
            self._conn.commit()
            return max(0, int(self._conn.total_changes - before))

    def append_evidence(
        self,
        task_id: str,
        tool_name: str,
        args_hash: str,
        exit_code: int,
        backup_path: Optional[str],
        why_hash: str,
    ) -> str:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            prev = self._conn.execute("SELECT entry_hash FROM evidence ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = str(prev[0]) if prev else "GENESIS"
            canonical = f"{task_id}|{ts}|{tool_name}|{args_hash}|{int(exit_code)}|{prev_hash}"
            entry_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            self._conn.execute(
                """
                INSERT INTO evidence (
                    task_id, timestamp, tool_name, arguments_hash,
                    exit_code, backup_path, why_receipt_hash, prev_hash, entry_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, ts, tool_name, args_hash, int(exit_code), backup_path, why_hash, prev_hash, entry_hash),
            )
            self._conn.commit()
            return entry_hash

    def verify_evidence_chain(self) -> tuple[bool, str, int]:
        """Recompute every SHA-256 link in `evidence` to detect any tampering."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM evidence ORDER BY seq ASC").fetchall()
        expected_prev = "GENESIS"
        for r in rows:
            if r["prev_hash"] != expected_prev:
                return False, f"Broken prev_hash at seq={r['seq']}", len(rows)
            canonical = (
                f"{r['task_id']}|{r['timestamp']}|{r['tool_name']}|"
                f"{r['arguments_hash']}|{int(r['exit_code'])}|{r['prev_hash']}"
            )
            recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if recomputed != r["entry_hash"]:
                return False, f"Tampered entry_hash at seq={r['seq']}", len(rows)
            expected_prev = r["entry_hash"]
        return True, f"Verified {len(rows)} hash-chained autonomy evidence record(s).", len(rows)

    def export_graph(self, *, limit: int = 100) -> dict[str, Any]:
        with self._lock:
            tasks = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)
                ).fetchall()
            ]
            counts = {
                str(r["status"]): int(r["cnt"])
                for r in self._conn.execute("SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status").fetchall()
            }
            ev_ok, ev_msg, ev_cnt = self.verify_evidence_chain()
            prop_cnt = int(
                self._conn.execute("SELECT COUNT(*) FROM proposals WHERE status = 'pending'").fetchone()[0]
            )
        return {
            "schema_version": AUTONOMY_SCHEMA_VERSION,
            "counts": counts,
            "evidence_ok": ev_ok,
            "evidence_records": ev_cnt,
            "evidence_message": ev_msg,
            "pending_proposals": prop_cnt,
            "tasks": tasks,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
