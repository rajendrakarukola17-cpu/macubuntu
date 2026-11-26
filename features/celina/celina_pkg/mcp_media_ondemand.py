"""Celina MCP Media On-Demand — stdio MCP server for local media generation.

Wraps:
  - Local quantized image generator (stable-diffusion.cpp / sd-cli) as an ephemeral subprocess
  - Zero RAM/VRAM until invoked — protected by the 8GB RAM Guard

Uses only stdlib: json, sys, os, subprocess, shutil.

Zero filesystem mutations at import time — directories and output paths are
created lazily at invocation time under ~/Pictures/Celina/ with 0600 perms.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .model_selector import get_ram_tier as _get_ram_tier
except Exception:
    _get_ram_tier = lambda: "8gb"  # fail-safe: lowest tier

SERVER_NAME = "mcp-media-ondemand"
SERVER_VERSION = "1.0.0"

# Output directory: strictly ~/Pictures/Celina/ with 0600 file perms
OUTPUT_DIR = Path.home() / "Pictures" / "Celina"

# Resolution caps by RAM tier (0 MB idle — subprocess exits, all RAM reclaimed)
TIER_RESOLUTION: dict[str, dict[str, int]] = {
    "8gb": {"width": 512, "height": 512},
    "16gb": {"width": 768, "height": 768},
    "32gb": {"width": 1024, "height": 1024},
}

TOOLS = [
    {
        "name": "media_generate_image",
        "description": "Generate an image from a text prompt using local stable-diffusion.cpp (ephemeral subprocess, 0 MB idle RAM)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "width": {"type": "integer", "default": 512, "minimum": 256, "maximum": 1024},
                "height": {"type": "integer", "default": 512, "minimum": 256, "maximum": 1024},
                "steps": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "media_model_status",
        "description": "Check if the stable-diffusion.cpp binary is available locally",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def _resolve_sd_binary() -> str | None:
    """Return the path to the stable-diffusion.cpp CLI binary, or None if not installed.

    Checks both `sd-cli` and `stable-diffusion` names. Never downloads anything.
    """
    for name in ("sd-cli", "stable-diffusion"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _get_tier_resolution() -> dict[str, int]:
    """Return (width, height) caps for the detected RAM tier."""
    try:
        tier = _get_ram_tier()
    except Exception:
        tier = "8gb"  # fail-safe: lowest tier
    return TIER_RESOLUTION.get(tier, TIER_RESOLUTION["8gb"])


def _ensure_output_dir() -> Path:
    """Create ~/Pictures/Celina/ with 0700 perms if missing. Lazily, at invocation time."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUTPUT_DIR, 0o700)
    return OUTPUT_DIR


def handle_initialize() -> dict[str, Any]:
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def handle_tools_list() -> dict[str, Any]:
    return {"tools": TOOLS}


def handle_tools_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "media_generate_image":
        prompt = (arguments.get("prompt") or "").strip()
        if not prompt:
            return {
                "content": [
                    {"type": "text", "text": json.dumps({"error": "prompt must not be empty"})},
                ],
            }

        # Fail-closed: sd binary must be present locally (never auto-download)
        sd_bin = _resolve_sd_binary()
        if sd_bin is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error": "stable-diffusion.cpp binary not found (sd-cli or stable-diffusion)",
                                "detail": "Install stable-diffusion.cpp locally; Celina never auto-downloads model weights.",
                            },
                        ),
                    },
                ],
            }

        # RAM-tier adapted resolution defaults
        tier_res = _get_tier_resolution()
        width = int(arguments.get("width", tier_res["width"]))
        height = int(arguments.get("height", tier_res["height"]))
        steps = int(arguments.get("steps", 20))

        # Clamp to tier cap
        width = min(width, tier_res["width"])
        height = min(height, tier_res["height"])

        # Write strictly to ~/Pictures/Celina/ with 0600 perms
        out_dir = _ensure_output_dir()
        output_path = out_dir / f"generated-{int(time.time())}.png"

        try:
            result = subprocess.run(
                [
                    sd_bin,
                    "--prompt",
                    prompt,
                    "--width",
                    str(width),
                    "--height",
                    str(height),
                    "--steps",
                    str(steps),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "error": "stable-diffusion invocation failed",
                                "detail": str(exc),
                                "image_path": None,
                            },
                        ),
                    },
                ],
            }

        # Set 0600 perms on the output file if it was created
        if output_path.is_file():
            try:
                os.chmod(output_path, 0o600)
            except OSError:
                pass

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "image_path": str(output_path) if result.returncode == 0 and output_path.is_file() else None,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                            "returncode": result.returncode,
                            "tier_resolution": tier_res,
                            "width": width,
                            "height": height,
                        },
                    ),
                },
            ],
        }

    if name == "media_model_status":
        sd_bin = _resolve_sd_binary()
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "sd_binary_available": sd_bin is not None,
                            "sd_binary": sd_bin,
                            "tier_resolution": _get_tier_resolution(),
                            "output_dir": str(OUTPUT_DIR),
                        },
                    ),
                },
            ],
        }

    return {"content": [{"type": "text", "text": json.dumps({"error": "unknown tool: " + name})}]}


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
