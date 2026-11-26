"""Celina AI — Capability #10: MCP-Native System Exposure (`celina-mcp-server`)

Wraps ALL Celina Linux OS tools (94 tools across 8 domains) as a standards-compliant
JSON-RPC 2.0 Model Context Protocol (MCP) Server over stdio (`python3 -m celina_pkg.mcp_server`)
with:
  - Deferred domain/lazy tool loading (`domain` filter or `summary_only` in `tools/list`)
  - Human-in-the-loop (HITL) Tier-2 approval + `UnifiedCapabilityBus` policy enforcement
  - Zero custom glue required per external MCP client (Claude Desktop, Cursor, local agents)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from typing import Any

from . import capability_bus, config, tools

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "celina-linux-os-mcp", "version": "4.4.0"}
ApprovalCallback = Callable[[str, str], bool]


class CelinaMCPServer:
    """JSON-RPC 2.0 MCP server exposing Celina's full OS toolset through UnifiedCapabilityBus."""

    def __init__(
        self,
        *,
        bus: capability_bus.UnifiedCapabilityBus | None = None,
        approval_cb: ApprovalCallback | None = None,
        caller_uid: int | None = None,
        deferred_loading: bool = True,
    ):
        self.bus = bus or capability_bus.get_capability_bus()
        self.approval_cb = approval_cb
        self.caller_uid = int(os.getuid() if caller_uid is None else caller_uid)
        self.deferred_loading = bool(deferred_loading)
        self._tool_cache: dict[str, dict[str, Any]] | None = None

    def _get_tool_catalog(self) -> dict[str, dict[str, Any]]:
        if self._tool_cache is None:
            catalog: dict[str, dict[str, Any]] = {}
            domain_of: dict[str, str] = {}
            for dname, dcfg in (config.TOOL_DOMAINS or {}).items():
                for tn in dcfg.get("tools") or ():
                    domain_of[str(tn)] = str(dname)
            for schema in tools.TOOL_SCHEMAS:
                fn = schema.get("function") or {}
                tname = str(fn.get("name", ""))
                if not tname:
                    continue
                is_t2 = tname in config.TIER2_APPROVAL_TOOLS
                catalog[tname] = {
                    "name": tname,
                    "description": str(fn.get("description", "")),
                    "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
                    "annotations": {
                        "tier": 2 if is_t2 else 1,
                        "domain": domain_of.get(tname, "os_hardware"),
                        "requiresApproval": is_t2,
                        "optIn": tname in tools.OPT_IN_TOOLS,
                    },
                }
            self._tool_cache = catalog
        return self._tool_cache

    def list_tools(
        self,
        *,
        domain: str | None = None,
        include_schemas: bool = True,
    ) -> list[dict[str, Any]]:
        """Lists exposed OS tools with optional deferred domain/schema loading."""
        catalog = self._get_tool_catalog()
        out: list[dict[str, Any]] = []
        for tname in sorted(catalog.keys()):
            entry = catalog[tname]
            if domain and entry["annotations"]["domain"] != domain:
                continue
            if not include_schemas:
                out.append(
                    {
                        "name": entry["name"],
                        "description": entry["description"][:140],
                        "annotations": entry["annotations"],
                    },
                )
            else:
                out.append(entry)
        return out

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handles a single JSON-RPC 2.0 MCP message and returns the JSON-RPC response dict (or None for notifications)."""
        req_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}

        # Notifications carry no id and expect no reply
        if method.startswith("notifications/"):
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "serverInfo": SERVER_INFO,
                    "capabilities": {
                        "tools": {"listChanged": False, "deferredLoading": self.deferred_loading},
                        "resources": {"subscribe": False},
                    },
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            domain = params.get("domain")
            deferred = bool(params.get("deferred", False))
            items = self.list_tools(
                domain=str(domain) if domain else None,
                include_schemas=not deferred,
            )
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": items, "total": len(items)}}

        if method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": [
                        {
                            "uri": "celina://capabilities/manifest",
                            "name": "Celina OS Capability Manifest",
                            "mimeType": "application/json",
                        },
                        {
                            "uri": "celina://autonomy/task-graph",
                            "name": "Celina v5 Autonomous TaskGraph",
                            "mimeType": "application/json",
                        },
                    ],
                },
            }

        if method == "tools/call":
            tname = str(params.get("name") or "").strip()
            _targs_raw = params.get("arguments")
            targs: dict[str, Any] = _targs_raw if isinstance(_targs_raw, dict) else {}
            if not tname or tname not in self._get_tool_catalog():
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Unknown Celina MCP tool: '{tname}'"},
                }
            bus_req = capability_bus.BusRequest(
                transport="mcp",
                tool_name=tname,
                arguments=targs,
                caller_uid=self.caller_uid,
                uid_provenance="in_process",
                root_goal_id=str(params.get("root_goal_id") or ""),
                task_id=str(params.get("task_id") or ""),
            )
            bus_resp = self.bus.dispatch(bus_req, approval_cb=self.approval_cb)
            text = bus_resp.result if bus_resp.ok else bus_resp.error
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": not bus_resp.ok,
                    "policyDecision": bus_resp.policy_decision,
                    "traceId": bus_resp.trace_id,
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: '{method}'"},
        }

    def serve_stdio(self, stdin: Any | None = None, stdout: Any | None = None) -> int:
        in_stream = stdin or sys.stdin
        out_stream = stdout or sys.stdout
        for raw_line in in_stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                resp = self.handle_request(msg)
            except json.JSONDecodeError as exc:
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
            if resp is not None:
                out_stream.write(json.dumps(resp, ensure_ascii=False) + "\n")
                out_stream.flush()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Celina Linux OS MCP Server (stdio JSON-RPC 2.0)")
    parser.add_argument("--list-tools", action="store_true", help="Print exposed MCP tool summary JSON and exit")
    parser.add_argument("--domain", default=None, help="Filter tools by domain")
    args = parser.parse_args()

    srv = CelinaMCPServer()
    if args.list_tools:
        print(json.dumps(srv.list_tools(domain=args.domain, include_schemas=False), indent=2))
        return 0
    return srv.serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
