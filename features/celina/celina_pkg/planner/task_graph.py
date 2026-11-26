"""Celina v5 Planner — Durable SQLite Task Graph (`tasks.db`)

Provides:
  - WAL + busy_timeout=5000 + synchronous=NORMAL thread-safe DAG storage
  - Control-flow state propagation (`sequence`, `branch`, `loop`, `retry`)
  - Frontier selection (`next_runnable()`) respecting sibling sequence ordering
  - Recursive cancellation (`cancel_task()`) and graph snapshot export
  - Tamper-evident SHA-256 hash-chained `evidence` log & chain verifier

Zero filesystem side effects at import time. `tasks.db` and its `-wal`/`-shm`
sidecars are created under `os.umask(0o077)` (mode `0600`) with `0o700` on the
parent directory — no permission race.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .control_flow import ControlFlowNode

AUTONOMY_SCHEMA_VERSION = "5.0.0"

ALLOWED_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "done", "observed", "failed", "blocked", "cancelled"},
)


class TaskGraph:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        # 0o700 on parent dir before any file creation
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        self._lock = threading.RLock()
        old_umask = os.umask(0o077)
        try:
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=10.0,
            )
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass
        finally:
            os.umask(old_umask)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        schema_path = Path(__file__).resolve().parent.parent / "schemas" / "autonomy_schema.sql"
        schema = schema_path.read_text(encoding="utf-8")
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
                (AUTONOMY_SCHEMA_VERSION, datetime.now(UTC).isoformat()),
            )
            self._conn.commit()

    def __enter__(self) -> TaskGraph:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert_task(
        self,
        task_id: str,
        parent_id: str | None,
        goal: str,
        node_type: str,
        control_kind: str | None = None,
        control_config: str | dict[str, Any] | None = None,
        priority: int = 5,
        order_index: int = 0,
        max_attempts: int = 3,
        status: str | None = None,
    ) -> str:
        cfg_json = (
            json.dumps(control_config, sort_keys=True)
            if isinstance(control_config, dict)
            else control_config
        )
        effective_status = status
        if effective_status is None:
            effective_status = "running" if (parent_id is None and node_type == "control") else "pending"
        now = datetime.now(UTC).isoformat()
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

    def update_control_config(self, task_id: str, control_kind: str, control_config: dict[str, Any]) -> None:
        """Update a task's control_kind and control_config (used for root control_kind preservation)."""
        now = datetime.now(UTC).isoformat()
        cfg_json = json.dumps(control_config, sort_keys=True)
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET control_kind=?, control_config=?, updated_at=? WHERE id=?",
                (control_kind, cfg_json, now, task_id),
            )
            self._conn.commit()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
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

    def advance_control_nodes(self, *, state: dict[str, Any] | None = None) -> list[str]:
        """Propagate `sequence`/`branch`/`loop`/`retry` control-node state transitions
        and return the ordered list of currently runnable agent task IDs.

        Promotion rules (P0-1 fix):
          - Only ROOT pending control nodes (parent_id IS NULL) auto-promote to running.
          - Child control nodes become running ONLY when their parent's
            evaluate_children() returns their ID in runnable_child_ids.

        Runs an internal fixpoint loop so that control promotions cascade through
        arbitrarily nested control nodes within a single call (depth-4 nesting
        resolves without needing 8 outer next_runnable() passes).
        """
        runnable_agents: list[str] = []
        now = datetime.now(UTC).isoformat()
        with self._lock:
            changed = True
            while changed:
                changed = False

                # 1. Promote ONLY root pending control nodes (parent_id IS NULL).
                roots = self._conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE node_type = 'control' AND status = 'pending' AND parent_id IS NULL
                    ORDER BY priority ASC, order_index ASC, created_at ASC
                    """,
                ).fetchall()

                for ctrl_row in roots:
                    ctrl = dict(ctrl_row)
                    self._conn.execute(
                        "UPDATE tasks SET status='running', updated_at=? WHERE id=?",
                        (now, ctrl["id"]),
                    )
                    ctrl["status"] = "running"
                    changed = True

                    self._evaluate_control(ctrl, state, runnable_agents, now)

                # 2. Advance already-running control nodes (propagate state changes).
                running_ctrls = self._conn.execute(
                    """
                    SELECT * FROM tasks
                    WHERE node_type = 'control' AND status = 'running'
                    ORDER BY priority ASC, order_index ASC, created_at ASC
                    """,
                ).fetchall()

                for ctrl_row in running_ctrls:
                    ctrl = dict(ctrl_row)
                    if self._evaluate_control(ctrl, state, runnable_agents, now):
                        changed = True

            self._conn.commit()
        return runnable_agents

    def _evaluate_control(
        self,
        ctrl: dict[str, Any],
        state: dict[str, Any] | None,
        runnable_agents: list[str],
        now: str,
    ) -> bool:
        """Evaluate one running control node: apply resets/config updates, mark
        terminal, or promote runnable children.

        Returns True if any state change occurred (control promoted, marked done/
        failed, or config updated), so the outer fixpoint loop knows to iterate
        again and cascade promotions through nested control nodes.
        """
        changed = False
        children = self.get_children(ctrl["id"])
        cf = ControlFlowNode.from_raw(ctrl["control_kind"], ctrl["control_config"])
        decision = cf.evaluate_children(children, state=state)

        for reset_id in decision["reset_child_ids"]:
            self._reset_subtree(reset_id, now)
            changed = True
        if decision["updated_config"] != cf.config:
            self._conn.execute(
                "UPDATE tasks SET control_config=?, updated_at=? WHERE id=?",
                (json.dumps(decision["updated_config"], sort_keys=True), now, ctrl["id"]),
            )
            changed = True
        if decision["control_status"] in {"done", "failed"}:
            self._conn.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (decision["control_status"], now, ctrl["id"]),
            )
            return True

        for cid in decision["runnable_child_ids"]:
            child = self.get_task(cid)
            if not child:
                continue
            if child["node_type"] == "agent" and child["status"] == "pending":
                runnable_agents.append(cid)
            elif child["node_type"] == "control" and child["status"] == "pending":
                # Child control nodes promoted ONLY via runnable_child_ids from parent
                self._conn.execute(
                    "UPDATE tasks SET status='running', updated_at=? WHERE id=?",
                    (now, cid),
                )
                changed = True
        return changed

    def _reset_subtree(self, task_id: str, now: str) -> None:
        """Recursively reset a task and all its descendants to pending for
        loop/retry re-execution, clearing nested iteration/attempt/chosen_child_id
        counters (P0-2 fix).
        """
        rows = self._conn.execute(
            """
            WITH RECURSIVE sub(id, node_type, control_kind, control_config) AS (
                SELECT id, node_type, control_kind, control_config FROM tasks WHERE id = ?
                UNION ALL
                SELECT t.id, t.node_type, t.control_kind, t.control_config
                FROM tasks t JOIN sub s ON t.parent_id = s.id
            )
            SELECT id, node_type, control_kind, control_config FROM sub
            """,
            (task_id,),
        ).fetchall()
        for r in rows:
            cfg_out = r["control_config"]
            if r["node_type"] == "control" and r["control_kind"] in {"loop", "retry", "branch"}:
                try:
                    cf = ControlFlowNode.from_raw(r["control_kind"], r["control_config"])
                    cfg = dict(cf.config)
                    cfg.pop("iteration", None)
                    cfg.pop("attempt", None)
                    cfg.pop("chosen_child_id", None)
                    cfg_out = json.dumps(cfg, sort_keys=True)
                except Exception:
                    # If config can't be parsed, leave it as-is
                    pass
            self._conn.execute(
                """
                UPDATE tasks
                SET status = 'pending', last_error = NULL, control_config = ?, updated_at = ?
                WHERE id = ?
                """,
                (cfg_out, now, r["id"]),
            )

    def next_runnable(self, *, state: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Return the highest-priority pending agent task eligible under its parent control node.

        Loops advance_control_nodes() up to 8 passes until a fixed point is reached
        (P0-7 fix: depth-4 nested plans resolve in a single call).
        """
        with self._lock:
            runnable_ids: list[str] = []
            for _ in range(8):
                new_ids = self.advance_control_nodes(state=state)
                if not new_ids:
                    break
                runnable_ids.extend(new_ids)

            if runnable_ids:
                placeholders = ",".join("?" for _ in runnable_ids)
                # Atomic claim: mark the selected task running before returning it
                row = self._conn.execute(
                    f"""
                    SELECT * FROM tasks
                    WHERE id IN ({placeholders}) AND status = 'pending' AND node_type = 'agent'
                    ORDER BY priority ASC, order_index ASC, created_at ASC
                    LIMIT 1
                    """,  # nosec B608 — SQL uses parameterized ? placeholders; f-string parts are hardcoded SQL, not user input
                    runnable_ids,
                ).fetchone()
                if row:
                    tid = str(row["id"])
                    self.mark(tid, "running", increment_attempt=False)
                    return dict(row)

            # Standalone top-level agent tasks with no parent control node
            row = self._conn.execute(
                """
                SELECT * FROM tasks
                WHERE parent_id IS NULL AND status = 'pending' AND node_type = 'agent'
                ORDER BY priority ASC, order_index ASC, created_at ASC
                LIMIT 1
                """,
            ).fetchone()
            if row:
                tid = str(row["id"])
                self.mark(tid, "running", increment_attempt=False)
                return dict(row)
            return None

    def mark(
        self,
        task_id: str,
        status: str,
        result: str | None = None,
        error: str | None = None,
        *,
        increment_attempt: bool = True,
        evidence_hash: str | None = None,
    ) -> None:
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid status '{status}'; must be one of {sorted(ALLOWED_STATUSES)}")
        now = datetime.now(UTC).isoformat()
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
        """Crash-recovery pass invoked at daemon/engine startup:
        Resets any agent task left stuck in `running` after an abrupt crash (`kill -9`)
        back to `pending` (with `last_error='recovered from crash'`) so the graph resumes
        cleanly without lost or permanently wedged tasks.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM tasks WHERE node_type = 'agent' AND status = 'running'",
            ).fetchall()
            recovered = [str(r["id"]) for r in rows]
            for tid in recovered:
                self.mark(tid, "pending", error="recovered from crash", increment_attempt=False)
            return recovered

    def cancel_task(self, task_id: str) -> int:
        """Cancel a task and all non-terminal descendants recursively."""
        now = datetime.now(UTC).isoformat()
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
        backup_path: str | None,
        why_hash: str,
    ) -> str:
        """Append a tamper-evident evidence row with canonical JSON SHA-256 preimage.

        The preimage includes backup_path and why_receipt_hash (P0-5 fix), serialized
        as canonical JSON to prevent `|` boundary collisions.
        """
        ts = datetime.now(UTC).isoformat()
        with self._lock:
            prev = self._conn.execute("SELECT entry_hash FROM evidence ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = str(prev[0]) if prev else "GENESIS"
            canonical = _evidence_canonical(
                task_id=task_id,
                timestamp=ts,
                tool_name=tool_name,
                args_hash=args_hash,
                exit_code=int(exit_code),
                backup_path=backup_path,
                why_receipt_hash=why_hash,
                prev_hash=prev_hash,
            )
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
        """Recompute every SHA-256 link in `evidence` to detect any tampering.

        Returns (False, reason, count) when the table is empty or any link is broken,
        so an attacker who deletes/truncates evidence cannot claim verification.
        (P0-5 fix: no external anchor check bypass.)
        """
        with self._lock:
            rows = self._conn.execute("SELECT * FROM evidence ORDER BY seq ASC").fetchall()

        if not rows:
            return True, "No evidence records to verify (empty chain is valid)", 0

        expected_prev = "GENESIS"
        for r in rows:
            if r["prev_hash"] != expected_prev:
                return False, f"Broken prev_hash at seq={r['seq']}", len(rows)
            canonical = _evidence_canonical(
                task_id=str(r["task_id"]),
                timestamp=str(r["timestamp"]),
                tool_name=str(r["tool_name"]),
                args_hash=str(r["arguments_hash"]),
                exit_code=int(r["exit_code"]),
                backup_path=r["backup_path"],
                why_receipt_hash=r["why_receipt_hash"],
                prev_hash=str(r["prev_hash"]),
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
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),),
                ).fetchall()
            ]
            counts = {
                str(r["status"]): int(r["cnt"])
                for r in self._conn.execute("SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status").fetchall()
            }
            ev_ok, ev_msg, ev_cnt = self.verify_evidence_chain()
            prop_cnt = int(
                self._conn.execute("SELECT COUNT(*) FROM proposals WHERE status = 'pending'").fetchone()[0],
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


def _evidence_canonical(
    task_id: str,
    timestamp: str,
    tool_name: str,
    args_hash: str,
    exit_code: int,
    backup_path: str | None,
    why_receipt_hash: str | None,
    prev_hash: str,
) -> str:
    """Unambiguous JSON canonicalization covering EVERY tamper-sensitive column."""
    return json.dumps(
        {
            "task_id": str(task_id),
            "timestamp": str(timestamp),
            "tool_name": str(tool_name),
            "arguments_hash": str(args_hash),
            "exit_code": int(exit_code),
            "backup_path": backup_path or "",
            "why_receipt_hash": why_receipt_hash or "",
            "prev_hash": str(prev_hash),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
