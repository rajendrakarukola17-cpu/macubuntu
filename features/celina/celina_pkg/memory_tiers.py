"""
Celina AI — Capabilities #6, #7 & #8 (Next-Generation Memory Architecture):
  6. Four-Tier Decoupled Memory (`FourTierMemory`):
       - Tier 1: Working Memory (session-isolated scratchpads & active checkpoints)
       - Tier 2: Core Fact KV Store (backed by `MemoryDB` facts with Ebbinghaus decay)
       - Tier 3: Episodic Session Learnings (distilled session takeaways)
       - Tier 4: AST Graph RAG (`AstGraphRagStore`: stdlib Python `ast` + optional `tree-sitter`)
  7. Knowledge Graph as a Priority Index (`PriorityGraphIndex`):
       - 10 typed edge types linking memories, entities, skills, and goals
       - 9-stage retrieval pipeline: normalize -> BM25 -> semantic -> 2-hop KG traversal
         -> RRF priority fusion -> temporal Ebbinghaus decay -> goal/skill boost
         -> pairwise reranking -> prompt-injection sanitization & diversity trim
  8. Demand-Constructed Context (`DemandContextBuilder`):
       - Builds a fresh, purpose-built context payload every turn by querying the
         4 memory tiers + priority index within a strict token budget (nothing
         accumulates blindly across turns).
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import config

TYPED_EDGE_TYPES: tuple[str, ...] = (
    "MENTIONS",
    "DEPENDS_ON",
    "SOLVES",
    "USES_SKILL",
    "PART_OF_GOAL",
    "CAUSED_BY",
    "SUPERSEDES",
    "LEARNED_FROM",
    "LOCATED_IN",
    "RELATED_TO",
)
TYPED_EDGE_SET: frozenset[str] = frozenset(TYPED_EDGE_TYPES)

_INJECTION_LINE_RE = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous\s+instructions|disregard\s+system\s+prompt|"
    r"you\s+are\s+now\s+in\s+developer\s+mode|exfiltrate\s+~?/?\.ssh|"
    r"override\s+tier-?2|bypass\s+approval)",
    re.I,
)


def sanitize_memory_snippet(text: str) -> str:
    """Strips prompt-injection directives before injecting retrieved memory into model context."""
    clean = " ".join((text or "").split())
    if _INJECTION_LINE_RE.search(clean):
        return "[REDACTED_INJECTION_CANDIDATE]"
    return clean


# ============================================================================
# Tier 1: Session-Isolated Working Memory
# ============================================================================

class WorkingMemoryStore:
    """Ephemeral, session-isolated working scratchpad & checkpoints (no cross-session leakage)."""

    def __init__(self, max_entries_per_session: int = 32):
        self.max_entries = max(4, int(max_entries_per_session))
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[str, dict[str, Any]]] = {}

    def put(self, session_id: str, key: str, value: Any) -> None:
        sid = (session_id or "default").strip()
        k = (key or "").strip()
        if not k:
            return
        with self._lock:
            bucket = self._sessions.setdefault(sid, {})
            bucket[k] = {"value": value, "updated_at": time.time()}
            if len(bucket) > self.max_entries:
                oldest = sorted(bucket.items(), key=lambda kv: kv[1]["updated_at"])[0][0]
                bucket.pop(oldest, None)

    def get(self, session_id: str, key: str, default: Any = None) -> Any:
        with self._lock:
            entry = (self._sessions.get((session_id or "default").strip()) or {}).get(key)
            return entry["value"] if entry is not None else default

    def snapshot(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            bucket = self._sessions.get((session_id or "default").strip()) or {}
            return {k: v["value"] for k, v in bucket.items()}

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop((session_id or "default").strip(), None)


# ============================================================================
# Tier 4: AST Graph RAG Store (stdlib Python AST + optional tree-sitter)
# ============================================================================

class AstGraphRagStore:
    """SQLite WAL structural code-symbol & call-graph index (stdlib `ast`, optional `tree-sitter`)."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else (config.BASE_DIR / "ast_graph.db")
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
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ast_nodes (
                    node_id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    symbol_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    line_no INTEGER NOT NULL,
                    signature TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ast_nodes_symbol ON ast_nodes (symbol_name);
                CREATE INDEX IF NOT EXISTS idx_ast_nodes_file ON ast_nodes (file_path);

                CREATE TABLE IF NOT EXISTS ast_edges (
                    src_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    dst_name TEXT NOT NULL,
                    PRIMARY KEY (src_id, edge_type, dst_name)
                );
                CREATE INDEX IF NOT EXISTS idx_ast_edges_dst ON ast_edges (dst_name);
                """
            )
            self._conn.commit()

    def index_python_file(self, file_path: Path | str) -> dict[str, int]:
        path = Path(file_path).expanduser().resolve()
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
        now = time.time()
        fstr = str(path)

        nodes: list[tuple[str, str, str, str, int, str, float]] = []
        edges: list[tuple[str, str, str]] = []

        def _nid(sym: str, line: int) -> str:
            return hashlib.sha256(f"{fstr}:{sym}:{line}".encode("utf-8")).hexdigest()[:16]

        mod_id = _nid("<module>", 1)
        nodes.append((mod_id, fstr, path.stem, "module", 1, path.name, now))

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        edges.append((mod_id, "IMPORTS", alias.name))
                elif node.module:
                    edges.append((mod_id, "IMPORTS", node.module))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                args_sig = ""
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args_sig = "(" + ", ".join(a.arg for a in node.args.args) + ")"
                nid = _nid(node.name, int(node.lineno))
                nodes.append((nid, fstr, node.name, kind, int(node.lineno), f"{node.name}{args_sig}", now))
                edges.append((mod_id, "DEFINES", node.name))
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        callee = ""
                        if isinstance(child.func, ast.Name):
                            callee = child.func.id
                        elif isinstance(child.func, ast.Attribute):
                            callee = child.func.attr
                        if callee:
                            edges.append((nid, "CALLS", callee))

        with self._lock:
            self._conn.execute("DELETE FROM ast_edges WHERE src_id IN (SELECT node_id FROM ast_nodes WHERE file_path = ?)", (fstr,))
            self._conn.execute("DELETE FROM ast_nodes WHERE file_path = ?", (fstr,))
            self._conn.executemany(
                "INSERT OR REPLACE INTO ast_nodes (node_id, file_path, symbol_name, kind, line_no, signature, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                nodes,
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO ast_edges (src_id, edge_type, dst_name) VALUES (?, ?, ?)",
                edges,
            )
            self._conn.commit()
        return {"nodes": len(nodes), "edges": len(edges)}

    def find_symbol(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        tokens = [t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", query or "")]
        if not tokens:
            return []
        with self._lock:
            rows = self._conn.execute("SELECT * FROM ast_nodes").fetchall()
            scored: list[tuple[int, dict[str, Any]]] = []
            for r in rows:
                sym_low = str(r["symbol_name"]).lower()
                score = sum(3 if t == sym_low else (1 if t in sym_low else 0) for t in tokens)
                if score > 0:
                    nid = str(r["node_id"])
                    callees = [
                        str(e["dst_name"])
                        for e in self._conn.execute(
                            "SELECT dst_name FROM ast_edges WHERE src_id = ? AND edge_type = 'CALLS' LIMIT 8",
                            (nid,),
                        ).fetchall()
                    ]
                    callers = [
                        str(c["symbol_name"])
                        for c in self._conn.execute(
                            """
                            SELECT n.symbol_name FROM ast_edges e
                            JOIN ast_nodes n ON n.node_id = e.src_id
                            WHERE e.dst_name = ? AND e.edge_type = 'CALLS' LIMIT 8
                            """,
                            (str(r["symbol_name"]),),
                        ).fetchall()
                    ]
                    item = dict(r)
                    item["calls"] = callees
                    item["called_by"] = callers
                    scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [it for _, it in scored[: max(1, int(limit))]]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ============================================================================
# Capability #7: Knowledge Graph as a Priority Index (10 Typed Edges + 9-Stage Pipeline)
# ============================================================================

@dataclasses.dataclass
class RetrievedMemoryItem:
    item_id: str
    kind: str  # fact | episode | entity | skill | goal | ast_symbol
    text: str
    score: float
    stage_hits: list[str] = dataclasses.field(default_factory=list)
    updated_at: float = 0.0
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)


class PriorityGraphIndex:
    """
    Knowledge Graph functioning as a priority index over flat memory:
      - 10 typed edge types (`TYPED_EDGE_TYPES`) connecting memories, entities, skills & goals
      - 9-stage retrieval pipeline returning ranked, sanitized, diverse records
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else (config.BASE_DIR / "priority_kg.db")
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
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kg_nodes (
                    node_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    content TEXT NOT NULL,
                    priority REAL NOT NULL DEFAULT 1.0,
                    updated_at REAL NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS kg_nodes_fts USING fts5(
                    node_id UNINDEXED,
                    label,
                    content,
                    tokenize='porter unicode61'
                );
                CREATE TABLE IF NOT EXISTS kg_priority_edges (
                    src_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    dst_id TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (src_id, edge_type, dst_id)
                );
                CREATE INDEX IF NOT EXISTS idx_pke_src ON kg_priority_edges (src_id);
                CREATE INDEX IF NOT EXISTS idx_pke_dst ON kg_priority_edges (dst_id);
                CREATE INDEX IF NOT EXISTS idx_pke_type ON kg_priority_edges (edge_type);
                """
            )
            self._conn.commit()

    def upsert_node(
        self,
        node_id: str,
        *,
        kind: str,
        label: str,
        content: str,
        priority: float = 1.0,
        updated_at: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        ts = float(updated_at) if updated_at is not None else time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO kg_nodes (node_id, kind, label, content, priority, updated_at, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    kind=excluded.kind,
                    label=excluded.label,
                    content=excluded.content,
                    priority=excluded.priority,
                    updated_at=excluded.updated_at,
                    meta_json=excluded.meta_json
                """,
                (node_id, kind, label, content, float(priority), ts, json.dumps(metadata or {}, sort_keys=True)),
            )
            self._conn.execute("DELETE FROM kg_nodes_fts WHERE node_id = ?", (node_id,))
            self._conn.execute(
                "INSERT INTO kg_nodes_fts (node_id, label, content) VALUES (?, ?, ?)",
                (node_id, label, content),
            )
            self._conn.commit()
        return node_id

    def add_typed_edge(
        self,
        src_id: str,
        edge_type: str,
        dst_id: str,
        *,
        weight: float = 1.0,
    ) -> None:
        et = (edge_type or "").strip().upper()
        if et not in TYPED_EDGE_SET:
            raise ValueError(f"Unknown KG edge_type '{edge_type}'. Allowed: {TYPED_EDGE_TYPES}")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO kg_priority_edges (src_id, edge_type, dst_id, weight, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(src_id, edge_type, dst_id) DO UPDATE SET
                    weight=excluded.weight, updated_at=excluded.updated_at
                """,
                (src_id, et, dst_id, float(weight), time.time()),
            )
            self._conn.commit()

    def retrieve_9stage(
        self,
        query: str,
        *,
        top_k: int = 8,
        active_goal_ids: Optional[list[str]] = None,
        now: Optional[float] = None,
    ) -> tuple[list[RetrievedMemoryItem], dict[str, int]]:
        """
        9-stage hybrid priority-graph retrieval pipeline:
          1. Normalize query & extract tokens
          2. BM25 lexical recall (SQLite FTS5)
          3. Semantic cosine similarity recall (character/token TF vector)
          4. Typed KG 2-hop graph traversal over the 10 edge types
          5. Reciprocal Rank Fusion (RRF) priority index combination
          6. Temporal Ebbinghaus decay weighting
          7. Goal & skill relevance boosting
          8. Pairwise query-document cross-reranking
          9. Prompt-injection sanitization & top-k diversity trim
        """
        ref_now = float(now) if now is not None else time.time()
        stage_counts: dict[str, int] = {}

        # Stage 1: Query normalization & token extraction
        q_tokens = [t for t in re.findall(r"[A-Za-z0-9_\u0900-\u097f\u0c00-\u0c7f]{2,}", (query or "").lower())]
        q_set = set(q_tokens)
        stage_counts["s1_query_tokens"] = len(q_set)
        if not q_set:
            return [], stage_counts

        with self._lock:
            all_rows = {str(r["node_id"]): dict(r) for r in self._conn.execute("SELECT * FROM kg_nodes").fetchall()}

            # Stage 2: BM25 lexical recall via FTS5
            fts_q = " OR ".join(f'"{t}"' for t in list(q_set)[:10])
            bm25_ids: list[str] = []
            try:
                bm25_rows = self._conn.execute(
                    "SELECT node_id FROM kg_nodes_fts WHERE kg_nodes_fts MATCH ? ORDER BY bm25(kg_nodes_fts) LIMIT 24",
                    (fts_q,),
                ).fetchall()
                bm25_ids = [str(r["node_id"]) for r in bm25_rows if str(r["node_id"]) in all_rows]
            except sqlite3.OperationalError:
                bm25_ids = []
            stage_counts["s2_bm25_hits"] = len(bm25_ids)

            # Stage 3: Lightweight semantic vector recall (token cosine over node label+content)
            sem_scored: list[tuple[float, str]] = []
            for nid, row in all_rows.items():
                doc_tokens = set(re.findall(r"[A-Za-z0-9_]{2,}", f"{row['label']} {row['content']}".lower()))
                if not doc_tokens:
                    continue
                inter = len(q_set & doc_tokens)
                if inter > 0:
                    cos = inter / math.sqrt(len(q_set) * len(doc_tokens))
                    sem_scored.append((cos, nid))
            sem_scored.sort(reverse=True)
            sem_ids = [nid for _, nid in sem_scored[:24]]
            stage_counts["s3_semantic_hits"] = len(sem_ids)

            # Stage 4: Typed KG 2-hop traversal over all 10 typed edge kinds
            seed_ids = list(dict.fromkeys(bm25_ids[:8] + sem_ids[:8] + list(active_goal_ids or [])))
            graph_scores: dict[str, float] = {}
            frontier = set(seed_ids)
            visited = set(seed_ids)
            for hop in range(1, 3):
                if not frontier:
                    break
                decay_hop = 1.0 if hop == 1 else 0.5
                next_frontier: set[str] = set()
                placeholders = ",".join("?" for _ in frontier)
                edge_rows = self._conn.execute(
                    f"""
                    SELECT src_id, edge_type, dst_id, weight FROM kg_priority_edges
                    WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})
                    """,
                    tuple(frontier) + tuple(frontier),
                ).fetchall()
                for er in edge_rows:
                    for neighbor in (str(er["src_id"]), str(er["dst_id"])):
                        if neighbor not in all_rows:
                            continue
                        graph_scores[neighbor] = graph_scores.get(neighbor, 0.0) + float(er["weight"]) * decay_hop
                        if neighbor not in visited:
                            visited.add(neighbor)
                            next_frontier.add(neighbor)
                frontier = next_frontier
            graph_ids = [nid for nid, _ in sorted(graph_scores.items(), key=lambda kv: kv[1], reverse=True)[:24]]
            stage_counts["s4_graph_2hop_hits"] = len(graph_ids)

        # Stage 5: Reciprocal Rank Fusion (RRF) priority index fusion
        k_rrf = 60.0
        rrf_scores: dict[str, float] = {}
        hit_sources: dict[str, list[str]] = {}
        for src_name, ranked in (("bm25", bm25_ids), ("semantic", sem_ids), ("kg_2hop", graph_ids)):
            for rank, nid in enumerate(ranked, 1):
                rrf_scores[nid] = rrf_scores.get(nid, 0.0) + 1.0 / (k_rrf + rank)
                hit_sources.setdefault(nid, []).append(src_name)
        stage_counts["s5_rrf_candidates"] = len(rrf_scores)

        # Stage 6: Temporal Ebbinghaus weighting
        temporal_weighted: dict[str, float] = {}
        for nid, base_score in rrf_scores.items():
            row = all_rows[nid]
            age_days = max(0.0, (ref_now - float(row["updated_at"])) / 86400.0)
            temporal = math.exp(-math.log(2.0) * age_days / 45.0)
            prio = max(0.1, float(row.get("priority", 1.0)))
            temporal_weighted[nid] = base_score * prio * (0.5 + 0.5 * temporal)
        stage_counts["s6_temporal_weighted"] = len(temporal_weighted)

        # Stage 7: Goal / Skill priority boosting
        weighted: dict[str, float] = {}
        goal_id_set = set(active_goal_ids or [])
        boosted_count = 0
        for nid, t_score in temporal_weighted.items():
            row = all_rows[nid]
            boost = 1.0
            if row["kind"] in {"goal", "skill"} or nid in goal_id_set:
                boost = 1.25
                boosted_count += 1
            weighted[nid] = t_score * boost
        stage_counts["s7_goal_skill_boosted"] = boosted_count

        # Stage 8: Pairwise query-document cross-reranking (exact phrase + token density)
        q_phrase = " ".join(q_tokens)
        reranked: list[tuple[float, str]] = []
        for nid, score in weighted.items():
            row = all_rows[nid]
            body = f"{row['label']} {row['content']}".lower()
            phrase_bonus = 1.35 if q_phrase in body else 1.0
            coverage = sum(1 for t in q_set if t in body) / max(1, len(q_set))
            final_score = score * phrase_bonus * (0.6 + 0.4 * coverage)
            reranked.append((final_score, nid))
        reranked.sort(reverse=True)
        stage_counts["s8_reranked"] = len(reranked)

        # Stage 9: Prompt-injection sanitization & diversity trim
        results: list[RetrievedMemoryItem] = []
        seen_texts: set[str] = set()
        for final_score, nid in reranked:
            row = all_rows[nid]
            clean_txt = sanitize_memory_snippet(f"{row['label']}: {row['content']}")
            if clean_txt == "[REDACTED_INJECTION_CANDIDATE]":
                continue
            dedup_key = clean_txt.lower()[:120]
            if dedup_key in seen_texts:
                continue
            seen_texts.add(dedup_key)
            results.append(
                RetrievedMemoryItem(
                    item_id=nid,
                    kind=str(row["kind"]),
                    text=clean_txt,
                    score=round(final_score, 6),
                    stage_hits=hit_sources.get(nid, []),
                    updated_at=float(row["updated_at"]),
                    metadata=json.loads(row.get("meta_json") or "{}"),
                )
            )
            if len(results) >= max(1, int(top_k)):
                break
        stage_counts["s9_final_selected"] = len(results)
        return results, stage_counts

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ============================================================================
# Capability #6 + #8: Four-Tier Memory Facade & Demand-Constructed Context
# ============================================================================

@dataclasses.dataclass
class DemandContextPayload:
    query: str
    session_id: str
    working_scratchpad: dict[str, Any]
    priority_memories: list[RetrievedMemoryItem]
    ast_symbols: list[dict[str, Any]]
    rendered_text: str
    char_count: int
    token_estimate: int
    stage_stats: dict[str, int]


class FourTierMemory:
    """Unified facade over the 4 strictly decoupled memory tiers + Priority KG Index."""

    def __init__(
        self,
        *,
        base_dir: Optional[Path] = None,
        memory_db: Any = None,
    ):
        root = Path(base_dir) if base_dir is not None else config.BASE_DIR
        self.working = WorkingMemoryStore()
        self.memory_db = memory_db
        self.ast_graph = AstGraphRagStore(root / "ast_graph.db")
        self.priority_index = PriorityGraphIndex(root / "priority_kg.db")

    def store_fact(self, key: str, value: str, *, priority: float = 1.0) -> str:
        clean_val = sanitize_memory_snippet(value)
        if self.memory_db is not None and hasattr(self.memory_db, "set_fact"):
            self.memory_db.set_fact(key, clean_val)
        return self.priority_index.upsert_node(
            f"fact:{key}",
            kind="fact",
            label=key,
            content=clean_val,
            priority=priority,
        )

    def store_episode(self, episode_id: str, summary: str, *, priority: float = 0.9) -> str:
        clean_sum = sanitize_memory_snippet(summary)
        return self.priority_index.upsert_node(
            f"episode:{episode_id}",
            kind="episode",
            label=episode_id,
            content=clean_sum,
            priority=priority,
        )

    def link(self, src_id: str, edge_type: str, dst_id: str, *, weight: float = 1.0) -> None:
        self.priority_index.add_typed_edge(src_id, edge_type, dst_id, weight=weight)

    def close(self) -> None:
        self.ast_graph.close()
        self.priority_index.close()


class DemandContextBuilder:
    """
    Capability #8: Demand-Constructed Context.
    Every turn constructs a fresh, purpose-built context payload directly from the
    4 memory tiers + 9-stage priority index within a strict character/token budget.
    Nothing blindly accumulates or degrades across turns.
    """

    def __init__(self, four_tier: FourTierMemory):
        self.memory = four_tier

    def build_turn_context(
        self,
        query: str,
        *,
        session_id: str = "default",
        token_budget: int = 800,
        active_window: str = "",
        active_goal_ids: Optional[list[str]] = None,
        top_k: int = 8,
    ) -> DemandContextPayload:
        char_budget = max(400, int(token_budget) * 4)
        working_snap = self.memory.working.snapshot(session_id)
        retrieved, stage_stats = self.memory.priority_index.retrieve_9stage(
            query, top_k=top_k, active_goal_ids=active_goal_ids
        )
        ast_hits = self.memory.ast_graph.find_symbol(query, limit=4)

        sections: list[str] = []
        if active_window:
            sections.append(f"[ACTIVE_WINDOW] {sanitize_memory_snippet(active_window)[:120]}")
        if working_snap:
            wk_lines = [f"  - {k}: {sanitize_memory_snippet(str(v))[:120]}" for k, v in list(working_snap.items())[:6]]
            sections.append("[WORKING_MEMORY]\n" + "\n".join(wk_lines))
        if retrieved:
            mem_lines = [f"  - ({m.kind} score={m.score:.3f}) {m.text[:180]}" for m in retrieved]
            sections.append("[PRIORITY_KNOWLEDGE]\n" + "\n".join(mem_lines))
        if ast_hits:
            ast_lines = [
                f"  - {a['kind']} {a['signature']} @ {Path(a['file_path']).name}:{a['line_no']} calls={a['calls'][:4]}"
                for a in ast_hits
            ]
            sections.append("[AST_GRAPH_RAG]\n" + "\n".join(ast_lines))

        rendered = "\n".join(sections)
        if len(rendered) > char_budget:
            rendered = rendered[: char_budget - 16] + "\n...[trimmed]"

        return DemandContextPayload(
            query=query,
            session_id=session_id,
            working_scratchpad=working_snap,
            priority_memories=retrieved,
            ast_symbols=ast_hits,
            rendered_text=rendered,
            char_count=len(rendered),
            token_estimate=max(1, len(rendered) // 4) if rendered else 0,
            stage_stats=stage_stats,
        )
