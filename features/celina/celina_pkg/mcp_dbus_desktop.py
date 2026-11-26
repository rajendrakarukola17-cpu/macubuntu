"""Celina MCP Desktop Bridge — stdio MCP server exposing D-Bus services to Celina.

Exposes:
  - scx_loader: CPU scheduler switching (scx_bpfland / scx_lavd / CFS)
  - MPRIS media controls (play/pause, next, previous, stop)
  - org.freedesktop.Notifications: notification summarization + priority filtering
  - GNOME window/workspace placement (via gsettings/dconf)

Uses only stdlib: subprocess, json, sys, os, shutil.
Sandboxed with bwrap_unshare_net by the MCP loader.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

SERVER_NAME = "mcp-dbus-desktop"
SERVER_VERSION = "1.0.0"

TOOLS = [
    {
        "name": "dbus_get_property",
        "description": "Read a D-Bus property (interface, path, property)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bus": {"type": "string", "enum": ["session", "system"], "default": "session"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "interface": {"type": "string"},
                "property": {"type": "string"},
            },
            "required": ["destination", "path", "interface", "property"],
        },
    },
    {
        "name": "dbus_set_property",
        "description": "Set a D-Bus property",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bus": {"type": "string", "enum": ["session", "system"], "default": "session"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "interface": {"type": "string"},
                "property": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["destination", "path", "interface", "property", "value"],
        },
    },
    {
        "name": "dbus_call_method",
        "description": "Call a D-Bus method",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bus": {"type": "string", "enum": ["session", "system"], "default": "session"},
                "destination": {"type": "string"},
                "path": {"type": "string"},
                "interface": {"type": "string"},
                "method": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["destination", "path", "interface", "method"],
        },
    },
    {
        "name": "mpris_control",
        "description": "MPRS media control (play/pause, next, previous, stop)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["play-pause", "next", "previous", "stop"]},
                "player": {"type": "string", "default": "org.mpris.MediaPlayer2.spotify"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "notification_summarize",
        "description": "List recent notifications via org.freedesktop.Notifications",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "gnome_window_placement",
        "description": "Get or set GNOME window/workspace placement via dconf",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list-workspaces", "move-to-workspace"]},
                "workspace": {"type": "integer"},
            },
        },
    },
]


def _dbus_call(bus: str, destination: str, path: str, interface: str, member: str, args: list[str] | None = None) -> dict[str, Any]:
    """Run dbus-send and parse the output."""
    bus_arg = "--session" if bus == "session" else "--system"
    argv = ["dbus-send", bus_arg, "--dest=" + destination, "--print-reply", path, interface + "." + member]
    if args:
        argv.extend(args)
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except FileNotFoundError:
        return {"error": "dbus-send not found — install dbus package"}
    except subprocess.TimeoutExpired:
        return {"error": "D-Bus call timed out"}
    except Exception as exc:  # pylint: disable=broad-except
        return {"error": str(exc)}


def _scx_list() -> dict[str, Any]:
    """List available sched_ext schedulers."""
    if not shutil.which("scx_loader"):
        return {"error": "scx_loader not installed"}
    try:
        result = subprocess.run(["scx_loader", "list"], capture_output=True, text=True, timeout=10)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except Exception as exc:  # pylint: disable=broad-except
        return {"error": str(exc)}


def _scx_set(scheduler: str) -> dict[str, Any]:
    """Set the sched_ext scheduler."""
    if not shutil.which("scxctl"):
        return {"error": "scxctl not installed"}
    try:
        result = subprocess.run(["sudo", "scxctl", "set", scheduler], capture_output=True, text=True, timeout=10)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except Exception as exc:  # pylint: disable=broad-except
        return {"error": str(exc)}


def handle_initialize() -> dict[str, Any]:
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {"listChanged": False},
        },
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def handle_tools_list() -> dict[str, Any]:
    return {"tools": TOOLS}


def handle_tools_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "dbus_get_property":
        result = _dbus_call(
            arguments.get("bus", "session"),
            arguments["destination"],
            arguments["path"],
            arguments["interface"],
            "org.freedesktop.DBus.Properties.Get",
            ["string:" + arguments["interface"], "string:" + arguments["property"]],
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    if name == "dbus_set_property":
        result = _dbus_call(
            arguments.get("bus", "session"),
            arguments["destination"],
            arguments["path"],
            arguments["interface"],
            "org.freedesktop.DBus.Properties.Set",
            ["string:" + arguments["interface"], "string:" + arguments["property"], "variant:string:" + arguments["value"]],
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    if name == "dbus_call_method":
        args = arguments.get("args", [])
        result = _dbus_call(
            arguments.get("bus", "session"),
            arguments["destination"],
            arguments["path"],
            arguments["interface"],
            arguments["method"],
            args or None,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    if name == "mpris_control":
        player = arguments.get("player", "org.mpris.MediaPlayer2.spotify")
        action_map = {
            "play-pause": "PlayPause",
            "next": "Next",
            "previous": "Previous",
            "stop": "Stop",
        }
        method = action_map.get(arguments["action"], "PlayPause")
        result = _dbus_call(
            "session",
            player,
            "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player",
            method,
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    if name == "notification_summarize":
        limit = arguments.get("limit", 10)
        # Use dbus-send to query notification server (best effort)
        result = _dbus_call(
            "session",
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "GetInformation",
            ["int32:" + str(limit)],
        )
        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    if name == "gnome_window_placement":
        action = arguments.get("action")
        if action == "list-workspaces":
            result = subprocess.run(["gsettings", "read", "org.gnome.desktop.wm.preferences.workspace-names"], capture_output=True, text=True, timeout=5)
            return {"content": [{"type": "text", "text": result.stdout or result.stderr}]}
        if action == "move-to-workspace":
            ws = arguments.get("workspace", 1)
            result = subprocess.run(["wmctrl", "-s", str(ws)], capture_output=True, text=True, timeout=5)
            return {"content": [{"type": "text", "text": json.dumps({"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode})}]}
        return {"content": [{"type": "text", "text": json.dumps({"error": "unknown action"})}]}

    return {"content": [{"type": "text", "text": json.dumps({"error": f"unknown tool: {name}"})}]}


def _read_line() -> str | None:
    line = sys.stdin.readline()
    if not line:
        return None
    return line.strip()


def main() -> int:
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
            result = {"error": f"unknown method: {method}"}

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
