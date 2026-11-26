"""
Celina Local-First Demand-Constructed Memory MCP Server (`memory_mcp_server.py`)

Inspired by `swadb` (single-file SQLite, demand-constructed per-turn context,
10 typed KG edges, 9-stage retrieval, 9 MCP memory tools) and `Engrama`
(local-first entity/observation/relationship graph traversal).

Exposes **9 memory tools** over JSON-RPC 2.0 stdio so Celina (or any local MCP
client) can query, update, and traverse its sovereign SQLite memory graph
without bloating the conversation context window:
  1. `memory_store_fact`
  2. `memory_store_episode`
  3. `memory_upsert_entity`
  4. `memory_link_entities`
  5. `memory_traverse_graph`
  6. `memory_retrieve_9stage`
  7. `memory_build_demand_context`
  8. `memory_working_set`
  9. `memory_stats`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from . import config, memory, memory_tiers

MEMORY_MCP_PROTOCOL_VERSION = "2024-11-05"

MEMORY_MCP_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "memory_store_fact",
        "description": "Store a sanitized core key/value fact in Tier-2 Core Fact KV + Priority KG index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "priority": {"type": "number"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "memory_store_episode",
        "description": "Store an episodic session learning in Tier-3 memory + Priority KG index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "episode_id": {"type": "string"},
                "summary": {"type": "string"},
                "priority": {"type": "number"},
            },
            "required": ["episode_id", "summary"],
        },
    },
    {
        "name": "memory_upsert_entity",
        "description": "Upsert an entity/observation node in the local SQLite Priority Knowledge Graph (Engrama/swadb).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "kind": {"type": "string"},
                "label": {"type": "string"},
                "content": {"type": "string"},
                "priority": {"type": "number"},
            },
            "required": ["node_id", "kind", "label", "content"],
        },
    },
    {
        "name": "memory_link_entities",
        "description": "Create a typed edge (one of 10 typed edge types) between two knowledge graph nodes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src_id": {"type": "string"},
                "edge_type": {"type": "string", "enum": list(memory_tiers.TYPED_EDGE_TYPES)},
                "dst_id": {"type": "string"},
                "weight": {"type": "number"},
            },
            "required": ["src_id", "edge_type", "dst_id"],
        },
    },
    {
        "name": "memory_traverse_graph",
        "description": "Traverse entity relationships up to N hops (Engrama-style graph reasoning).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_node_id": {"type": "string"},
                "max_hops": {"type": "integer"},
            },
            "required": ["start_node_id"],
        },
    },
    {
        "name": "memory_retrieve_9stage",
        "description": "Run the 9-stage priority retrieval pipeline (BM25 + semantic + 2-hop KG + RRF + temporal + goal boost + rerank + sanitize).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
                "active_goal_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_build_demand_context",
        "description": "Construct a fresh, token-budgeted demand-constructed turn context from all 4 memory tiers (swadb model).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "session_id": {"type": "string"},
                "token_budget": {"type": "integer"},
                "active_window": {"type": "string"},
                "active_goal_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_working_set",
        "description": "Read or write Tier-1 Working Memory scratchpad/checkpoint state for a session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "action": {"type": "string", "enum": ["get", "put", "snapshot", "clear"]},
                "key": {"type": "string"},
                "value": {},
            },
            "required": ["session_id", "action"],
        },
    },
    {
        "name": "memory_stats",
        "description": "Return local single-file SQLite memory tier counts, edge types, and DB paths.",
        "inputSchema": {"type": "object", "properties": {}},
    },
)


class CelinaMemoryMCPServer:
    """JSON-RPC 2.0 MCP server exposing Celina's 9 sovereign memory & graph tools."""

    def __init__(self, *, base_dir: Optional[Path] = None, memory_db: Optional[memory.MemoryDB] = None):
        self._owns_db = memory_db is None
        if memory_db is not None:
            self.mem_db = memory_db
        else:
            root = Path(base_dir) if base_dir is not None else config.BASE_DIR
            self.mem_db = memory.MemoryDB(db_path=root / "memory.db")
        self.four_tier = self.mem_db.get_four_tier_memory()

    def close(self) -> None:
        if self._owns_db:
            self.mem_db.close()

    def _traverse_graph(self, start_node_id: str, max_hops: int = 2) -> dict[str, Any]:
        hops = max(1, min(4, int(max_hops)))
        idx = self.four_tier.priority_index
        visited: set[str] = {start_node_id}
        frontier: set[str] = {start_node_id}
        edges_found: list[dict[str, Any]] = []
        nodes_found: dict[str, dict[str, Any]] = {}

        with idx._lock:
            for _ in range(hops):
                if not frontier:
                    break
                ph = ",".join("?" for _ in frontier)
                rows = idx._conn.execute(
                    f"SELECT src_id, edge_type, dst_id, weight FROM kg_priority_edges "
                    f"WHERE src_id IN ({ph}) OR dst_id IN ({ph})",
                    tuple(frontier) + tuple(frontier),
                ).fetchall()
                next_frontier: set[str] = set()
                for r in rows:
                    rec = {
                        "src_id": str(r["src_id"]),
                        "edge_type": str(r["edge_type"]),
                        "dst_id": str(r["dst_id"]),
                        "weight": float(r["weight"]),
                    }
                    edges_found.append(rec)
                    for nid in (rec["src_id"], rec["dst_id"]):
                        if nid not in visited:
                            visited.add(nid)
                            next_frontier.add(nid)
                frontier = next_frontier

            if visited:
                ph_v = ",".join("?" for _ in visited)
                for nr in idx._conn.execute(
                    f"SELECT node_id, kind, label, content, priority FROM kg_nodes WHERE node_id IN ({ph_v})",
                    tuple(visited),
                ).fetchall():
                    nodes_found[str(nr["node_id"])] = {
                        "node_id": str(nr["node_id"]),
                        "kind": str(nr["kind"]),
                        "label": str(nr["label"]),
                        "content": memory_tiers.sanitize_memory_snippet(str(nr["content"])),
                        "priority": float(nr["priority"]),
                    }
        return {"start_node_id": start_node_id, "nodes": list(nodes_found.values()), "edges": edges_found}

    def _dispatch_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        ft = self.four_tier
        idx = ft.priority_index

        if name == "memory_store_fact":
            nid = ft.store_fact(str(args["key"]), str(args["value"]), priority=float(args.get("priority", 1.0)))
            return {"ok": True, "node_id": nid}

        if name == "memory_store_episode":
            nid = ft.store_episode(
                str(args["episode_id"]),
                str(args["summary"]),
                priority=float(args.get("priority", 0.9)),
            )
            return {"ok": True, "node_id": nid}

        if name == "memory_upsert_entity":
            nid = idx.upsert_node(
                str(args["node_id"]),
                kind=str(args["kind"]),
                label=str(args["label"]),
                content=str(args["content"]),
                priority=float(args.get("priority", 1.0)),
            )
            return {"ok": True, "node_id": nid}

        if name == "memory_link_entities":
            ft.link(
                str(args["src_id"]),
                str(args["edge_type"]),
                str(args["dst_id"]),
                weight=float(args.get("weight", 1.0)),
            )
            return {"ok": True, "edge_type": str(args["edge_type"])}

        if name == "memory_traverse_graph":
            return {"ok": True, **self._traverse_graph(str(args["start_node_id"]), int(args.get("max_hops", 2)))}

        if name == "memory_retrieve_9stage":
            items, stats = idx.retrieve_9stage(
                str(args["query"]),
                top_k=int(args.get("top_k", 8)),
                active_goal_ids=list(args.get("active_goal_ids") or []),
            )
            return {
                "ok": True,
                "items": [
                    {"item_id": it.item_id, "kind": it.kind, "text": it.text, "score": it.score}
                    for it in items
                ],
                "stage_stats": stats,
            }

        if name == "memory_build_demand_context":
            payload = self.mem_db.build_demand_context(
                str(args["query"]),
                session_id=str(args.get("session_id", "default")),
                token_budget=int(args.get("token_budget", 800)),
                active_window=str(args.get("active_window", "")),
                active_goal_ids=list(args.get("active_goal_ids") or []),
            )
            return {
                "ok": True,
                "rendered_text": payload.rendered_text,
                "token_estimate": payload.token_estimate,
                "stage_stats": payload.stage_stats,
            }

        if name == "memory_working_set":
            sid = str(args["session_id"])
            act = str(args["action"])
            if act == "put":
                ft.working.put(sid, str(args.get("key", "note")), args.get("value"))
                return {"ok": True, "action": "put"}
            if act == "get":
                return {"ok": True, "value": ft.working.get(sid, str(args.get("key", "")))}
            if act == "snapshot":
                return {"ok": True, "snapshot": ft.working.snapshot(sid)}
            if act == "clear":
                ft.working.clear_session(sid)
                return {"ok": True, "action": "clear"}
            return {"ok": False, "error": f"Unknown working_set action '{act}'"}

        if name == "memory_stats":
            with idx._lock:
                n_cnt = idx._conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
                e_cnt = idx._conn.execute("SELECT COUNT(*) FROM kg_priority_edges").fetchone()[0]
            return {
                "ok": True,
                "memory_db": str(self.mem_db.db_path),
                "typed_edge_types": list(memory_tiers.TYPED_EDGE_TYPES),
                "kg_nodes": int(n_cnt),
                "kg_edges": int(e_cnt),
                "tool_count": len(MEMORY_MCP_TOOLS),
            }

        return {"ok": False, "error": f"Unknown memory MCP tool '{name}'"}

    def handle_request(self, request: dict[str, Any]) -> Optional[dict[str, Any]]:
        req_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}

        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MEMORY_MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "celina-swadb-memory-mcp", "version": "4.4.0"},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": list(MEMORY_MCP_TOOLS)}}
        if method == "tools/call":
            tname = str(params.get("name") or "")
            targs = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            try:
                out = self._dispatch_tool(tname, targs)
                is_err = not bool(out.get("ok", True))
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(out, sort_keys=True)}],
                        "isError": is_err,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"ok": False, "error": str(exc)})}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def serve_stdio(self, stdin=None, stdout=None) -> int:
        in_s = stdin or sys.stdin
        out_s = stdout or sys.stdout
        try:
            for raw_line in in_s:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except json.JSONDecodeError as exc:
                    err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
                    out_s.write(json.dumps(err) + "\n")
                    out_s.flush()
                    continue
                resp = self.handle_request(req)
                if resp is not None:
                    out_s.write(json.dumps(resp) + "\n")
                    out_s.flush()
        finally:
            self.close()
        return 0


def main() -> int:
    return CelinaMemoryMCPServer().serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
