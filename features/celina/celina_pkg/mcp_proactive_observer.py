"""Celina MCP Proactive Observer — stdio MCP server for anticipatory behavior.

Reads signals from:
  - ebpf_sensor (system events, process starts, resource pressure)
  - os_events (window focus, app launches, file changes)
  - Calendar events (via calendar_bridge or gsettings)
  - Idle time patterns

Surfaces silent, dismissible pills in celina-island:
  - "Meeting in 5m — open notes & mute notifications?"
  - "Heavy compile detected — switching to scx_bpfland"
  - "Ollama model loading — suggest pinning with vmtouch"

Uses only stdlib: json, sys, os, subprocess, time, datetime.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

SERVER_NAME = "mcp-proactive-observer"
SERVER_VERSION = "1.0.0"

TOOLS = [
    {
        "name": "proactive_get_signals",
        "description": "Read recent proactive signals from sensors",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "proactive_check_idle",
        "description": "Check if system is idle (no user input for N minutes)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold_minutes": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "proactive_suggest_scheduler",
        "description": "Suggest sched_ext profile based on current workload",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "proactive_surface_pill",
        "description": "Surface a dismissible pill in celina-island",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "actions": {"type": "array", "items": {"type": "string"}},
                "dismissible": {"type": "boolean", "default": True},
            },
            "required": ["title", "body"],
        },
    },
]


def _run_cmd(args: list[str], timeout: int = 10) -> dict[str, Any]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "returncode": result.returncode}
    except FileNotFoundError:
        return {"error": "command not found: " + args[0]}
    except subprocess.TimeoutExpired:
        return {"error": "command timed out: " + " ".join(args)}
    except Exception as exc:
        return {"error": str(exc)}


def _read_ebpf_events(limit: int) -> list:
    events = []
    ebpf_log = os.path.expanduser("~/.local/share/celina/ebpf-events.log")
    if os.path.isfile(ebpf_log):
        try:
            with open(ebpf_log, encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
            for line in lines:
                try:
                    events.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    events.append({"raw": line.strip()})
        except Exception:
            pass
    return events


def _read_os_events(limit: int) -> list:
    events = []
    os_log = os.path.expanduser("~/.local/share/celina/os-events.log")
    if os.path.isfile(os_log):
        try:
            with open(os_log, encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
            for line in lines:
                try:
                    events.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    events.append({"raw": line.strip()})
        except Exception:
            pass
    return events


def _get_idle_time():
    for cmd in (["xprintidle"], ["gnome-session-idle"]):
        result = _run_cmd(cmd, timeout=3)
        if result["returncode"] == 0 and result["stdout"].isdigit():
            val = int(result["stdout"])
            return val if cmd[0] == "xprintidle" else val * 1000
    return None


def _get_active_window():
    result = _run_cmd(["xdotool", "getactivewindow", "getwindowname"], timeout=3)
    if result["returncode"] == 0 and result["stdout"]:
        return result["stdout"]
    return None


def _get_upcoming_events():
    events = []
    result = _run_cmd(["celina", "calendar", "list", "--limit", "5"], timeout=5)
    if result["returncode"] == 0 and result["stdout"]:
        try:
            events = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return events


def handle_initialize():
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def handle_tools_list():
    return {"tools": TOOLS}


def handle_tools_call(name, arguments):
    if name == "proactive_get_signals":
        limit = arguments.get("limit", 20)
        signals = []
        signals.extend(_read_ebpf_events(limit // 2))
        signals.extend(_read_os_events(limit // 2))
        signals.sort(key=lambda e: e.get("ts", e.get("timestamp", "")), reverse=True)
        return {"content": [{"type": "text", "text": json.dumps({"signals": signals[:limit], "count": len(signals)})}]}

    if name == "proactive_check_idle":
        threshold_min = arguments.get("threshold_minutes", 5)
        idle_ms = _get_idle_time()
        is_idle = idle_ms is not None and idle_ms > threshold_min * 60 * 1000
        return {"content": [{"type": "text", "text": json.dumps({"idle": is_idle, "idle_ms": idle_ms, "threshold_minutes": threshold_min})}]}

    if name == "proactive_suggest_scheduler":
        idle_ms = _get_idle_time()
        active_win = _get_active_window()
        cpu_result = _run_cmd(["uptime"], timeout=3)
        load_str = cpu_result["stdout"] if cpu_result["returncode"] == 0 else "unknown"
        suggestion = "scx_bpfland"
        if idle_ms and idle_ms > 300000:
            suggestion = "scx_lavd"
        elif active_win and any(k in active_win.lower() for k in ("compile", "build", "make", "cargo", "npm")):
            suggestion = "scx_bpfland"
        return {"content": [{"type": "text", "text": json.dumps({"suggested_scheduler": suggestion, "load": load_str, "idle_ms": idle_ms, "active_window": active_win})}]}

    if name == "proactive_surface_pill":
        title = arguments["title"]
        body = arguments["body"]
        actions = arguments.get("actions", [])
        dismissible = arguments.get("dismissible", True)
        pill_msg = "PILL: " + title + " — " + body
        if actions:
            pill_msg += " | Actions: " + ", ".join(actions)
        try:
            subprocess.run(["celina-island", "--ask", pill_msg], capture_output=True, text=True, timeout=5)
        except Exception:
            pass
        return {"content": [{"type": "text", "text": json.dumps({"surfaced": True, "title": title, "body": body, "actions": actions, "dismissible": dismissible})}]}

    return {"content": [{"type": "text", "text": json.dumps({"error": "unknown tool: " + name})}]}


def _read_line():
    line = sys.stdin.readline()
    if not line:
        return None
    return line.strip()


def main():
    while True:
        line = _read_line()
        if line is None:
            break
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        params = msg.get("params", {}) or {}
        msg_id = msg.get("id")

        if method == "initialize":
            result = handle_initialize()
        elif method == "tools/list":
            result = handle_tools_list()
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {}) or {}
            result = handle_tools_call(tool_name, tool_args)
        elif method == "initialized":
            result = {}
        else:
            result = {"error": "unknown method: " + method}

        response = {"jsonrpc": "2.0"}
        if msg_id is not None:
            response["id"] = msg_id
        if "error" in result:
            response["error"] = result["error"]
        else:
            response["result"] = result

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
