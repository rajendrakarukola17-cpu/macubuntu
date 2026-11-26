"""Celina Graph Store — rustworkx DAG + SQLite/sqlite-vec temporal KG + Markov transitions.

Provides:
  (1) rustworkx DAG engine for TaskGraph/TreePlanner + saga rollback checkpoints
  (2) SQLite + sqlite-vec temporal knowledge graph (optional LadybugDB on balanced/workstation)
  (3) Lightweight behavioral Markov transition graph fed by ebpf_sensor/ActivityWatch

Uses only stdlib + rustworkx + sqlite-vec (optional).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

try:
    import rustworkx as rx
    HAS_RUSTWORKX = True
except ImportError:
    HAS_RUSTWORKX = False

try:
    import sqlite_vec
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False

log = logging.getLogger("celina.graph_store")

try:
    from .runtime.tier_policy import CelinaTierPolicy
except Exception:  # pragma: no cover - runtime package import fallback
    CelinaTierPolicy = None  # type: ignore


class TaskGraphDag:
    """rustworkx-based DAG for TaskGraph/TreePlanner with saga rollback checkpoints.

    Validates and topologically sorts TaskGraph steps, detecting cycles.
    """

    def __init__(self) -> None:
        if not HAS_RUSTWORKX:
            raise RuntimeError(
                "rustworkx is required for TaskGraphDag. Install with: pip install rustworkx",
            )
        self._dag: rx.PyDiGraph[dict[str, Any], None] | None = None
        self._idx_map: dict[str, int] = {}

    def build(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate and topologically sort TaskGraph steps.

        Parameters
        ----------
        steps : list[dict]
            Each step: {"id": str, "depends_on": [str, ...], ...}

        Returns
        -------
        list[dict]
            Steps in topological order.

        Raises
        ------
        ValueError
            If a cycle is detected or a dependency is missing.

        """
        if not HAS_RUSTWORKX:
            raise RuntimeError("rustworkx is not installed")

        self._dag = rx.PyDiGraph(check_cycle=True)
        self._idx_map = {}

        for step in steps:
            step_id = step["id"]
            self._idx_map[step_id] = self._dag.add_node(step)

        for step in steps:
            step_id = step["id"]
            for dep in step.get("depends_on", []):
                if dep not in self._idx_map:
                    raise ValueError(f"Missing dependency '{dep}' for step '{step_id}'")
                try:
                    self._dag.add_edge(self._idx_map[dep], self._idx_map[step_id], None)
                except rx.DAGWouldCycle as exc:
                    raise ValueError(
                        f"Cycle detected involving {dep} -> {step_id}",
                    ) from exc

        return [self._dag[i] for i in rx.topological_sort(self._dag)]

    def rollback_checkpoint(self, step_id: str) -> dict[str, Any] | None:
        """Return the step data for a rollback checkpoint."""
        if self._dag is None or step_id not in self._idx_map:
            return None
        return self._dag[self._idx_map[step_id]]

    def downstream_steps(self, step_id: str) -> list[dict[str, Any]]:
        """Return all steps that depend (transitively) on step_id."""
        if self._dag is None or step_id not in self._idx_map:
            return []
        start_idx = self._idx_map[step_id]
        reachable = rx.dijkstra(self._dag, start_idx, target=None)
        result: list[dict[str, Any]] = []
        for idx, _dist in reachable.items():
            if idx != start_idx:
                result.append(self._dag[idx])
        return result


class TemporalKgStore:
    """SQLite + sqlite-vec temporal knowledge graph.

    Nodes and edges with timestamps; supports optional vector search via sqlite-vec.
    Optional LadybugDB backend on balanced/workstation tiers.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        use_vec: bool = False,
        tier_policy: Any = None,
    ) -> None:
        self.db_path = Path(db_path)
        # tier_policy.enable_ladybug_graph opts into the vector KG backend
        ladybug = bool(getattr(tier_policy, "enable_ladybug_graph", False)) if tier_policy is not None else False
        self.use_vec = (use_vec or ladybug) and HAS_SQLITE_VEC
        self.tier_policy = tier_policy
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kg_nodes (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    properties TEXT NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kg_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src TEXT NOT NULL,
                    rel TEXT NOT NULL,
                    dst TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    properties TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_kg_edges_src ON kg_edges(src);
                CREATE INDEX IF NOT EXISTS idx_kg_edges_dst ON kg_edges(dst);
                """,
            )
            if self.use_vec:
                try:
                    conn.execute("LOAD sqlite_vec;")
                    conn.execute(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS kg_node_vecs USING vec0(
                            id TEXT PRIMARY KEY,
                            embedding FLOAT[384]
                        )
                        """,
                    )
                except sqlite3.OperationalError:
                    log.warning("sqlite-vec not available; falling back to plaintext KG")
                    self.use_vec = False
            conn.commit()

    def upsert_node(self, node_id: str, kind: str, label: str, properties: dict[str, Any] | None = None) -> None:
        """Insert or update a knowledge graph node."""
        now = _now_ts()
        props = json.dumps(properties or {})
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                INSERT INTO kg_nodes (id, kind, label, properties, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    label=excluded.label,
                    properties=excluded.properties,
                    updated_at=excluded.updated_at
                """,
                (node_id, kind, label, props, now),
            )
            if self.use_vec:
                # Embedding would be added by caller via add_node_vector
                pass
            conn.commit()

    def add_edge(self, src: str, rel: str, dst: str, weight: float = 1.0,
                 properties: dict[str, Any] | None = None) -> None:
        """Add a knowledge graph edge."""
        now = _now_ts()
        props = json.dumps(properties or {})
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                INSERT INTO kg_edges (src, rel, dst, weight, properties, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (src, rel, dst, weight, props, now, now),
            )
            conn.commit()

    def get_neighbors(self, node_id: str, rel: str | None = None) -> list[dict[str, Any]]:
        """Return outgoing edges (neighbors) for a node."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            if rel:
                rows = conn.execute(
                    "SELECT src, rel, dst, weight, properties, updated_at FROM kg_edges WHERE src=? AND rel=?",
                    (node_id, rel),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT src, rel, dst, weight, properties, updated_at FROM kg_edges WHERE src=?",
                    (node_id,),
                ).fetchall()
        return [
            {"src": r[0], "rel": r[1], "dst": r[2], "weight": r[3],
             "properties": json.loads(r[4] or "{}"), "updated_at": r[5]}
            for r in rows
        ]

    def add_node_vector(self, node_id: str, embedding: list[float]) -> None:
        """Add a vector embedding for a node (requires sqlite-vec)."""
        if not self.use_vec:
            return
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("LOAD sqlite_vec;")
            conn.execute(
                "INSERT OR REPLACE INTO kg_node_vecs (id, embedding) VALUES (?, ?)",
                (node_id, sqlite_vec.serialize_float32(embedding)),
            )
            conn.commit()

    def vector_search(self, query_embedding: list[float], k: int = 5) -> list[tuple[str, float]]:
        """Search nodes by vector similarity (requires sqlite-vec)."""
        if not self.use_vec:
            return []
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("LOAD sqlite_vec;")
            rows = conn.execute(
                """
                SELECT id, distance FROM kg_node_vecs
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (sqlite_vec.serialize_float32(query_embedding), k),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]


class MarkovTransitionGraph:
    """Lightweight behavioral Markov transition graph.

    Records (prev_context -> next_action) transitions with hit counts,
    fed by ebpf_sensor/ActivityWatch for proactive/daemon.py.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        tier_policy: Any = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.tier_policy = tier_policy
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS behavior_transitions (
                    prev_context TEXT NOT NULL,
                    next_action TEXT NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 1,
                    last_seen INTEGER NOT NULL,
                    PRIMARY KEY (prev_context, next_action)
                )
                """,
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_behavior_prev ON behavior_transitions(prev_context)",
            )
            conn.commit()

    def record_transition(self, prev_context: str, next_action: str, ts: int) -> None:
        """Record or increment a behavior transition."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                INSERT INTO behavior_transitions (prev_context, next_action, hits, last_seen)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(prev_context, next_action)
                DO UPDATE SET hits = hits + 1, last_seen = excluded.last_seen
                """,
                (prev_context, next_action, ts),
            )
            conn.commit()

    def predict_next_actions(self, context: str, min_hits: int = 3, limit: int = 3) -> list[tuple[str, int]]:
        """Predict likely next actions for a given context."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            rows = conn.execute(
                """
                SELECT next_action, hits FROM behavior_transitions
                WHERE prev_context = ? AND hits >= ?
                ORDER BY hits DESC LIMIT ?
                """,
                (context, min_hits, limit),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]


def _now_ts() -> int:
    import time
    return int(time.time())


__all__ = ["HAS_RUSTWORKX", "HAS_SQLITE_VEC", "MarkovTransitionGraph", "TaskGraphDag", "TemporalKgStore"]
