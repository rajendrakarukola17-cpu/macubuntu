"""Celina v5 Autonomy MCP Client Loader (`autonomy/mcp_loader.py`)

Spawns sandboxed stdio MCP servers, connects to loopback/allowlisted Streamable
HTTP MCP servers, routes GUI perception/actuation capabilities, and enforces:
  1. Complete ordered 11-server catalog (priority-staged for 8 GB+ hosts):
       1. computer-use-linux (@agent-sh/computer-use-linux, stdio, Required)
       2. shadow-vision      (uvx shadow-vision + local Ollama, stdio, Required)
       3. ghostdesk          (Docker virtual desktop, HTTP loopback, Strongly recommended)
       4. eye2byte           (post-action diff verification, stdio, Recommended)
       5. uacc               (68-tool fallback actuator, stdio, Optional)
       6. deskwright         (virtual monitor headless desktop, stdio, Optional)
       7. n8n                (external SaaS workflow bridge, HTTP, Opt-in only)
  2. Explicit blocklist rejecting `celina-mcp` / `usecelina.xyz` (unrelated Celo SDK).
  3. Sandboxing:
       - `computer-use-linux` / `eye2byte` / `uacc` / `deskwright`: `bwrap --unshare-net`
         when bubblewrap is available (zero network egress).
       - `shadow-vision`: local Ollama loopback-only (`127.0.0.1:11434`, AF_UNIX + loopback).
       - `ghostdesk`: loopback HTTP (`localhost`/`127.0.0.1`) ONLY (Docker isolated).
       - `n8n`: NEVER enabled by default; gated behind `web_comms.enabled = true` and
         `net_policy.py` destination allowlist + `RestrictAddressFamilies=AF_INET AF_INET6`.
  4. Every MCP call is Tier-2 by definition (external process outside Celina's Python core).
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .. import config, net_policy

# Explicitly forbidden unrelated package/domain (Celo blockchain SDK)
BLOCKLISTED_MCP_IDENTIFIERS: tuple[str, ...] = (
    "celina-mcp",
    "usecelina.xyz",
    "usecelina",
)

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# --- P4 D4: Verified local cache for uvx / npx packages ---
# Cache directory for verified MCP packages (offline-first, hash-verified)
MCP_CACHE_DIR = Path.home() / ".celina" / "cache" / "mcp"
UV_CACHE_DIR = Path.home() / ".celina" / "cache" / "uv"
NPM_CACHE_DIR = Path.home() / ".celina" / "cache" / "npm"

# --- P4 D5: MCP schema-hash pinning (MCP03 tool-poisoning defense) ---
# Lock file mapping {server}__{tool} -> expected SHA-256 of the tool's schema.
# Populated after human review of each MCP server's tool definitions.
MCP_TOOLS_LOCK_PATH = (
    Path(__file__).resolve().parents[4] / "system" / "capabilities" / "mcp_tools.lock.json"
)

# MCP protocol version negotiation order: try 2026-07-28 (server/discover) first,
# fall back to 2024-11-05 (initialize) for legacy servers.
MCP_PROTOCOL_VERSIONS = ("2026-07-28", "2024-11-05")


def ensure_mcp_cache_dirs() -> None:
    """Create MCP cache directories if they don't exist."""
    for d in (MCP_CACHE_DIR, UV_CACHE_DIR, NPM_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)


def resolve_cached_uvx_command(
    package: str,
    version: str,
    expected_sha256: str | None = None,
) -> list[str] | None:
    """Resolve a uvx command to use a hash-verified cached package.

    On first call: downloads the wheel, verifies SHA-256 against the lockfile,
    caches it under MCP_CACHE_DIR. On subsequent calls: launches from cache
    with zero network access.

    Returns the modified command argv (e.g. ["uvx", "--from", "/path/to/cached/pkg==ver", "pkg"])
    or None if resolution fails (fail closed).
    """
    ensure_mcp_cache_dirs()

    # Check if already cached
    cache_marker = MCP_CACHE_DIR / f"uvx_{package}=={version}.verified"
    if cache_marker.is_file():
        # Already verified — use uvx with --from pointing to the cached wheel
        cached_wheel = MCP_CACHE_DIR / f"{package}-{version}.whl"
        if cached_wheel.is_file():
            return ["uvx", "--from", str(cached_wheel), package]

    # Not cached yet — download and verify
    if expected_sha256 is None:
        return None  # fail closed: no hash to verify against

    import urllib.request

    # Try PyPI JSON API to get the wheel URL
    pypi_url = f"https://pypi.org/pypi/{package}/json"
    try:
        with urllib.request.urlopen(pypi_url, timeout=10) as resp:  # nosec B310 — PyPI JSON API, HTTPS only
            pypi_data = json.loads(resp.read())
    except Exception:
        return None

    # Find the wheel for this version
    releases = pypi_data.get("releases", {})
    version_files = releases.get(version, [])
    wheel_url = None
    for file_info in version_files:
        if file_info.get("packagetype") == "bdist_wheel":
            wheel_url = file_info.get("url")
            file_hash = file_info.get("digests", {}).get("sha256", "")
            break

    if wheel_url is None:
        return None

    # Download and verify
    try:
        cached_wheel = MCP_CACHE_DIR / f"{package}-{version}.whl"
        with urllib.request.urlopen(wheel_url, timeout=30) as resp:  # nosec B310 — PyPI wheel URL, HTTPS only
            data = resp.read()
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != expected_sha256:
            return None  # hash mismatch — fail closed
        cached_wheel.write_bytes(data)
        os.chmod(cached_wheel, 0o600)
        # Write verification marker
        cache_marker.write_text(expected_sha256, encoding="utf-8")
        os.chmod(cache_marker, 0o600)
    except Exception:
        return None

    return ["uvx", "--from", str(cached_wheel), package]


def resolve_cached_npx_command(
    package: str,
    version: str,
    expected_sha256: str | None = None,
) -> list[str] | None:
    """Resolve an npx command to use a hash-verified cached package.

    Similar to resolve_cached_uvx_command but for npm packages.
    """
    ensure_mcp_cache_dirs()

    cache_marker = MCP_CACHE_DIR / f"npm_{package}@{version}.verified"
    if cache_marker.is_file():
        cached_tar = MCP_CACHE_DIR / f"{package}@{version}.tgz"
        if cached_tar.is_file():
            return ["npx", "--no-install", "--prefix", str(MCP_CACHE_DIR), f"{package}@{version}"]

    if expected_sha256 is None:
        return None

    # Download tarball from npm registry
    npm_url = f"https://registry.npmjs.org/{package.replace('@', '%40')}/-/{package.rsplit('/', maxsplit=1)[-1]}-{version}.tgz"
    try:
        import urllib.request

        with urllib.request.urlopen(npm_url, timeout=30) as resp:  # nosec B310 — npm registry URL, HTTPS only
            data = resp.read()
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != expected_sha256:
            return None
        cached_tar = MCP_CACHE_DIR / f"{package}@{version}.tgz"
        cached_tar.write_bytes(data)
        os.chmod(cached_tar, 0o600)
        cache_marker.write_text(expected_sha256, encoding="utf-8")
        os.chmod(cache_marker, 0o600)
    except Exception:
        return None

    return ["npx", "--no-install", "--prefix", str(MCP_CACHE_DIR), f"{package}@{version}"]

# Complete ordered 7-server catalog (priority order matching 8 GB rollout discipline)
MCP_SERVER_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "order": 1,
        "name": "computer-use-linux",
        "package": "@agent-sh/computer-use-linux",
        "role": "actuation",
        "description": "Primary actuation — Wayland-native click/type/scroll with semantic selectors",
        "transport": "stdio",
        "priority": "required",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "npm install -g @agent-sh/computer-use-linux && computer-use-linux doctor",
        "config": {
            "enabled": False,
            "command": "computer-use-linux",
            "args": ["mcp"],
            "sandbox": "bwrap_unshare_net",
            "role": "actuation",
            "priority": "required",
        },
    },
    {
        "order": 2,
        "name": "shadow-vision",
        "package": "shadow-vision",
        "role": "perception",
        "description": "Primary perception — local OCR + VLM analysis via Ollama (no screenshot leaves host)",
        "transport": "stdio",
        "priority": "required",
        "sandbox": "bwrap_loopback_only",
        "install_cmd": "ollama pull qwen3-vl:2b-instruct && pipx install uv",
        "config": {
            "enabled": False,
            "command": "uvx",
            "args": ["--from", "shadow-vision==0.1.0", "shadow-vision"],
            "integrity": "sha256:2384ab65ea7c597fcd637699df99e76c798b1d0d3270b844132bea4c27ac3781",
            "env": {
                "VISION_BACKEND": "ollama",
                "VISION_MODEL": "qwen3-vl:2b-instruct",
                "OLLAMA_URL": "http://127.0.0.1:11434/api/chat",
            },
            "sandbox": "bwrap_loopback_only",
            "role": "perception",
            "priority": "required",
        },
    },
    {
        "order": 3,
        "name": "ghostdesk",
        "package": "ghcr.io/yv17labs/ghostdesk:latest",
        "role": "isolated_execution",
        "description": "Isolated execution — full Linux desktop in Docker for risky tasks (Streamable HTTP)",
        "transport": "http",
        "priority": "strongly_recommended",
        "sandbox": "docker_loopback_http",
        "install_cmd": (
            "docker run -d --name ghostdesk --shm-size 2g "
            "-p 127.0.0.1:3000:3000 -p 127.0.0.1:6080:6080 ghcr.io/yv17labs/ghostdesk:latest"
        ),
        "config": {
            "enabled": False,
            "url": "http://localhost:3000/mcp",
            "sandbox": "docker_loopback_http",
            "role": "isolated_execution",
            "priority": "strongly_recommended",
        },
    },
    {
        "order": 4,
        "name": "eye2byte",
        "package": "eye2byte",
        "role": "verification",
        "description": "Verification layer — post-action screenshot diff + confidence scoring",
        "transport": "stdio",
        "priority": "recommended",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "Install per Eye2byte README after computer-use-linux and shadow-vision are stable",
        "config": {
            "enabled": False,
            "command": "eye2byte",
            "args": ["mcp"],
            "sandbox": "bwrap_unshare_net",
            "role": "verification",
            "priority": "recommended",
        },
    },
    {
        "order": 5,
        "name": "uacc",
        "package": "uacc",
        "role": "fallback_actuation",
        "description": "Fallback actuator — 68 tools, vision-optional, self-healing retries",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "Install per UACC README (optional fallback actuator)",
        "config": {
            "enabled": False,
            "command": "uacc",
            "args": ["mcp"],
            "sandbox": "bwrap_unshare_net",
            "role": "fallback_actuation",
            "priority": "optional",
        },
    },
    {
        "order": 6,
        "name": "deskwright",
        "package": "deskwright",
        "role": "headless_desktop",
        "description": "Headless desktop — dedicated virtual monitor for background agent sessions",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "Install per Deskwright README (optional headless desktop)",
        "config": {
            "enabled": False,
            "command": "deskwright",
            "args": ["mcp"],
            "sandbox": "bwrap_unshare_net",
            "role": "headless_desktop",
            "priority": "optional",
        },
    },
    {
        "order": 7,
        "name": "n8n",
        "package": "n8n-mcp",
        "role": "cloud_workflow",
        "description": "Cloud workflow bridge — opt-in external SaaS workflows (gated behind web_comms.enabled)",
        "transport": "http",
        "priority": "opt_in_only",
        "sandbox": "opt_in_net_allowlist",
        "install_cmd": "Opt-in only: enable web_comms.enabled=true and configure N8N_API_KEY + net_policy allowlist",
        "config": {
            "enabled": False,
            "opt_in_only": True,
            "requires_web_comms": True,
            "url": "http://localhost:5678/mcp",
            "env": {"N8N_API_KEY": "${N8N_API_KEY}"},
            "allowed_saas_hosts": [],
            "sandbox": "opt_in_net_allowlist",
            "role": "cloud_workflow",
            "priority": "opt_in_only",
        },
    },
    {
        "order": 8,
        "name": "kwin-mcp",
        "package": "kwin-mcp",
        "role": "isolated_kde_desktop",
        "description": "Isolated KDE Plasma 6 Wayland virtual KWin session (kwin_wayland --virtual + EIS + AT-SPI2)",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "uvx --from kwin-mcp==0.12.0 kwin-mcp",
        "config": {
            "enabled": False,
            "command": "uvx",
            "args": ["--from", "kwin-mcp==0.12.0", "kwin-mcp"],
            "integrity": "sha256:105d14641a423d1f613861397eba78757b6efa6e237e498fb92b9b6b2d061976",
            "sandbox": "bwrap_unshare_net",
            "role": "isolated_kde_desktop",
            "priority": "optional",
        },
    },
    {
        "order": 9,
        "name": "linux-system-mcp",
        "package": "linux-system-mcp",
        "role": "system_bridge",
        "description": "Linux system bridge — GUI dialogs, notifications, timeout shell, GUI sudo, and mid-execution ask_user confirmation",
        "transport": "stdio",
        "priority": "recommended",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "npx -y linux-system-mcp@1.2.1",
        "config": {
            "enabled": False,
            "command": "npx",
            "args": ["-y", "linux-system-mcp@1.2.1"],
            "integrity": "sha256:86de583a95390b99cc78e308ac7408d77e87a7e09e1e5803bd37f5409dfefc4e",
            "sandbox": "bwrap_unshare_net",
            "role": "system_bridge",
            "priority": "recommended",
        },
    },
    {
        "order": 10,
        "name": "vadgr-computer-use",
        "package": "vadgr-computer-use",
        "role": "accessibility_fallback",
        "description": "Accessibility-first CPU-friendly Linux automation (UIA/AT-SPI/AX across GNOME, KDE, wlroots, X11, Wayland)",
        "transport": "stdio",
        "priority": "recommended",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "Install locally from GitHub (vadgr-computer-use) for accessibility-first fallback",
        "config": {
            "enabled": False,
            "command": "vadgr-computer-use",
            "args": ["mcp"],
            "sandbox": "bwrap_unshare_net",
            "role": "accessibility_fallback",
            "priority": "recommended",
        },
    },
    {
        "order": 11,
        "name": "swadb-memory",
        "package": "celina-memory-mcp",
        "role": "memory_graph",
        "description": "Local-first single-file SQLite demand-constructed memory MCP server (swadb/Engrama: 10 typed edges, 9-stage retrieval, 9 memory tools)",
        "transport": "stdio",
        "priority": "recommended",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "Built into Celina: celina-memory-mcp (python3 -m celina_pkg.memory_mcp_server)",
        "config": {
            "enabled": False,
            "command": "celina-memory-mcp",
            "args": [],
            "sandbox": "bwrap_unshare_net",
            "role": "memory_graph",
            "priority": "recommended",
        },
    },
    # --- Official MCP servers (P1 MCP Core) ---
    # stdio servers via npx, all opt-in (enabled=False), sandboxed with bwrap_unshare_net
    {
        "order": 12,
        "name": "mcp-filesystem",
        "package": "@modelcontextprotocol/server-filesystem",
        "role": "file_access",
        "description": "Scoped, allow-listed directory access (reads, moves, searches) with explicit approval boundaries",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "npx -y @modelcontextprotocol/server-filesystem@latest",
        "config": {
            "enabled": False,
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem@latest"],
            "integrity": "sha256:placeholder-verify-before-enable",
            "sandbox": "bwrap_unshare_net",
            "role": "file_access",
            "priority": "optional",
        },
    },
    {
        "order": 13,
        "name": "mcp-memory",
        "package": "@modelcontextprotocol/server-memory",
        "role": "knowledge_graph",
        "description": "Persistent knowledge-graph memory paired with Celina's 17 SQLite tables",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "npx -y @modelcontextprotocol/server-memory@latest",
        "config": {
            "enabled": False,
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory@latest"],
            "integrity": "sha256:placeholder-verify-before-enable",
            "sandbox": "bwrap_unshare_net",
            "role": "knowledge_graph",
            "priority": "optional",
        },
    },
    {
        "order": 14,
        "name": "mcp-sequential-thinking",
        "package": "@modelcontextprotocol/server-sequential-thinking",
        "role": "reasoning",
        "description": "Dynamic, reflective step-by-step reasoning for TreePlanner and cross-app workflows",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "npx -y @modelcontextprotocol/server-sequential-thinking@latest",
        "config": {
            "enabled": False,
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-sequential-thinking@latest"],
            "integrity": "sha256:placeholder-verify-before-enable",
            "sandbox": "bwrap_unshare_net",
            "role": "reasoning",
            "priority": "optional",
        },
    },
    {
        "order": 15,
        "name": "mcp-git",
        "package": "mcp-server-git",
        "role": "version_control",
        "description": "Repository inspection — git log, diff, blame, status within sandboxed worktrees",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "npx -y mcp-server-git@latest",
        "config": {
            "enabled": False,
            "command": "npx",
            "args": ["-y", "mcp-server-git@latest"],
            "integrity": "sha256:placeholder-verify-before-enable",
            "sandbox": "bwrap_unshare_net",
            "role": "version_control",
            "priority": "optional",
        },
    },
    {
        "order": 16,
        "name": "mcp-fetch",
        "package": "mcp-server-fetch",
        "role": "web_access",
        "description": "Web-to-Markdown conversion — read-only fetch of public URLs for research",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "npx -y mcp-server-fetch@latest",
        "config": {
            "enabled": False,
            "command": "npx",
            "args": ["-y", "mcp-server-fetch@latest"],
            "integrity": "sha256:placeholder-verify-before-enable",
            "sandbox": "bwrap_unshare_net",
            "role": "web_access",
            "priority": "optional",
        },
    },
    {
        "order": 17,
        "name": "mcp-time",
        "package": "mcp-server-time",
        "role": "scheduling",
        "description": "Timezone-aware scheduling — current time, conversions, calendar math",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "npx -y mcp-server-time@latest",
        "config": {
            "enabled": False,
            "command": "npx",
            "args": ["-y", "mcp-server-time@latest"],
            "integrity": "sha256:placeholder-verify-before-enable",
            "sandbox": "bwrap_unshare_net",
            "role": "scheduling",
            "priority": "optional",
        },
    },
    # --- Custom local OS MCP subsystems (stdlib-only Python) ---
    {
        "order": 18,
        "name": "mcp-dbus-desktop",
        "package": "celina-mcp-dbus-desktop",
        "role": "desktop_bridge",
        "description": "Exposes D-Bus services to Celina — scx_loader (CPU scheduler), MPRIS media, notifications (Apple Intelligence-style summarization), GNOME window/workspace placement",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "Built into Celina: python3 -m celina_pkg.mcp_dbus_desktop",
        "config": {
            "enabled": False,
            "command": "celina-mcp-dbus-desktop",
            "args": [],
            "sandbox": "bwrap_unshare_net",
            "role": "desktop_bridge",
            "priority": "optional",
        },
    },
    {
        "order": 19,
        "name": "mcp-proactive-observer",
        "package": "celina-mcp-proactive-observer",
        "role": "anticipatory",
        "description": "Anticipatory layer — ebpf_sensor, os_events, window focus, calendar events → rule + local-LLM scoring loop → silent dismissible pills in celina-island",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "Built into Celina: python3 -m celina_pkg.mcp_proactive_observer",
        "config": {
            "enabled": True,
            "command": "celina-mcp-proactive-observer",
            "args": [],
            "sandbox": "bwrap_unshare_net",
            "role": "anticipatory",
            "priority": "optional",
        },
    },
    {
        "order": 20,
        "name": "mcp-media-ondemand",
        "package": "celina-mcp-media-ondemand",
        "role": "media_on_demand",
        "description": "Local quantized image generator (stable-diffusion.cpp) + Whisper/NLLB live-translation pipeline behind systemd socket activation — zero RAM/VRAM until invoked",
        "transport": "stdio",
        "priority": "optional",
        "sandbox": "bwrap_unshare_net",
        "install_cmd": "Built into Celina: python3 -m celina_pkg.mcp_media_ondemand",
        "config": {
            "enabled": False,
            "command": "celina-mcp-media-ondemand",
            "args": [],
            "sandbox": "bwrap_unshare_net",
            "role": "media_on_demand",
            "priority": "optional",
        },
    },
)

MCP_CATALOG_BY_NAME: dict[str, dict[str, Any]] = {entry["name"]: entry for entry in MCP_SERVER_CATALOG}

# Exact 5 Tier-2 tools exposed in domain `mcp_perception_actuation`
MCP_TOOL_ROUTING: dict[str, dict[str, str]] = {
    "computer_click": {
        "server": "computer-use-linux",
        "remote_tool": "click",
        "transport": "stdio",
        "role": "actuation",
    },
    "computer_type": {
        "server": "computer-use-linux",
        "remote_tool": "type",
        "transport": "stdio",
        "role": "actuation",
    },
    "vision_ocr": {
        "server": "shadow-vision",
        "remote_tool": "ocr",
        "transport": "stdio",
        "role": "perception",
    },
    "vision_analyze": {
        "server": "shadow-vision",
        "remote_tool": "analyze",
        "transport": "stdio",
        "role": "perception",
    },
    "ghostdesk_click": {
        "server": "ghostdesk",
        "remote_tool": "click",
        "transport": "http",
        "role": "isolated_execution",
    },
}

MCP_PERCEPTION_ACTUATION_TOOLS = frozenset(MCP_TOOL_ROUTING.keys())

# Role -> ordered preference of MCP servers
ROLE_SERVER_ORDER: dict[str, tuple[str, ...]] = {
    "perception": ("shadow-vision", "eye2byte"),
    "actuation": ("computer-use-linux", "vadgr-computer-use", "uacc"),
    "isolated_execution": ("ghostdesk", "kwin-mcp", "deskwright"),
    "isolated_kde_desktop": ("kwin-mcp", "ghostdesk"),
    "accessibility_fallback": ("vadgr-computer-use", "computer-use-linux", "uacc"),
    "system_bridge": ("linux-system-mcp",),
    "memory_graph": ("swadb-memory",),
    "verification": ("eye2byte", "shadow-vision"),
    "cloud_workflow": ("n8n",),
}


class MCPPolicyError(PermissionError):
    """Raised when an MCP server violates blocklist, sandbox, loopback, or opt-in web_comms policy."""


def check_mcp_blocklist(server_name: str, spec: dict[str, Any] | None = None) -> None:
    """Fail-closed guard against accidentally installing/linking `celina-mcp` from
    `usecelina.xyz` (an unrelated Celo blockchain SDK).
    """
    haystack_parts = [str(server_name or "")]
    if isinstance(spec, dict):
        haystack_parts.append(str(spec.get("command") or ""))
        haystack_parts.append(" ".join(str(a) for a in (spec.get("args") or [])))
        haystack_parts.append(str(spec.get("url") or ""))
        haystack_parts.append(str(spec.get("package") or ""))
    joined = " ".join(haystack_parts).lower()
    for bad in BLOCKLISTED_MCP_IDENTIFIERS:
        if bad in joined:
            raise MCPPolicyError(
                f"Blocked MCP server '{server_name}': '{bad}' is an unrelated Celo blockchain SDK "
                f"(usecelina.xyz) and must NOT be added to macubuntu Celina.",
            )


def _is_loopback_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    return host in LOOPBACK_HOSTS


def validate_server_policy(
    server_name: str,
    spec: dict[str, Any],
    *,
    web_comms_enabled: bool | None = None,
) -> None:
    """Enforces blocklist, loopback-only, and n8n opt-in web_comms policy fail-closed."""
    check_mcp_blocklist(server_name, spec)
    sname = server_name.strip().lower()

    # Shadow Vision: NEVER allow screenshots to leave loopback Ollama
    if sname == "shadow-vision":
        env_map = spec.get("env") if isinstance(spec.get("env"), dict) else {}
        ollama_url = str(env_map.get("OLLAMA_URL") or "http://127.0.0.1:11434/api/chat")
        if not _is_loopback_url(ollama_url):
            raise MCPPolicyError(
                f"Shadow Vision OLLAMA_URL '{ollama_url}' must stay on loopback (127.0.0.1/localhost) "
                "so screenshots never leave the machine.",
            )

    # GhostDesk: loopback Streamable HTTP only (isolated Docker container)
    if sname == "ghostdesk" and spec.get("url"):
        if not _is_loopback_url(str(spec["url"])):
            raise MCPPolicyError(
                f"GhostDesk URL '{spec['url']}' must be loopback HTTP (http://localhost:3000/mcp).",
            )

    # n8n: opt-in cloud bridge — NEVER allowed unless web_comms.enabled=true AND allowlist valid
    if sname == "n8n" or spec.get("requires_web_comms"):
        wc_on = config.is_web_comms_enabled() if web_comms_enabled is None else bool(web_comms_enabled)
        if not wc_on:
            raise MCPPolicyError(
                "n8n MCP bridge is opt-in only and blocked while web_comms.enabled=false. "
                "Enable web_comms.enabled=true in neural-schema.json only if external SaaS orchestration is needed.",
            )
        url = str(spec.get("url") or "").strip()
        if url and not _is_loopback_url(url):
            allowed = spec.get("allowed_saas_hosts") or config.get_web_comms_allowed_hosts()
            net_policy.validate_web_comms_host(url, allowed_hosts=allowed)



def compute_tool_schema_hash(tool_def: dict[str, Any]) -> str:
    """Canonical SHA-256 over a single MCP tool definition (blocks MCP03 rug-pull/tool poisoning).

    Hashes name + description + inputSchema deterministically so any unauthorized
    schema change (added parameter, changed description, new property) is detected.
    """
    canonical = json.dumps(
        {
            "name": tool_def.get("name", ""),
            "description": tool_def.get("description", ""),
            "inputSchema": tool_def.get("inputSchema", {}),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def discover_pinned_tools(
    loader: MCPLoader,
    server_name: str,
    *,
    lock_path: Path | None = None,
    allow_unpinned: bool = False,
) -> list[dict[str, Any]]:
    """Dynamically discovers tools from an ALREADY-ENABLED, policy-validated MCP server,
    verifies each tool's schema SHA-256 against mcp_tools.lock.json (fail-closed),
    and returns OpenAI/Ollama-compatible ``{server}__{tool}`` function schemas.

    Raises MCPPolicyError on schema hash mismatch (MCP03 tool-poisoning alert).
    Skips unpinned tools unless allow_unpinned=True.
    """
    lpath = lock_path or MCP_TOOLS_LOCK_PATH
    pins: dict[str, str] = {}
    if lpath.is_file():
        try:
            raw = json.loads(lpath.read_text(encoding="utf-8"))
            pins = dict(raw.get("tools", raw) if isinstance(raw, dict) else {})
        except (OSError, json.JSONDecodeError) as exc:
            raise MCPPolicyError(f"Corrupt mcp_tools.lock.json: {exc}") from exc

    discovered: list[dict[str, Any]] = []
    for tdef in loader.list_tools(server_name):
        if not isinstance(tdef, dict) or not tdef.get("name"):
            continue
        remote_name = str(tdef["name"]).strip()
        ns_name = f"{server_name}__{remote_name}"
        actual_sha = compute_tool_schema_hash(tdef)
        expected_sha = pins.get(ns_name)
        if expected_sha:
            if actual_sha != expected_sha:
                raise MCPPolicyError(
                    f"MCP03 tool-poisoning alert: schema hash mismatch for '{ns_name}' "
                    f"(expected={expected_sha[:16]}, actual={actual_sha[:16]})",
                )
        elif not allow_unpinned:
            # Fail-closed: unpinned external MCP tools stay quarantined until pinned
            continue

        discovered.append(
            {
                "type": "function",
                "function": {
                    "name": ns_name,
                    "description": f"[MCP:{server_name} | Tier-2] {str(tdef.get('description') or '')[:300]}",
                    "parameters": tdef.get("inputSchema") or {"type": "object", "properties": {}},
                },
            },
        )
    return discovered


def build_sandboxed_stdio_cmd(
    server_name: str,
    spec: dict[str, Any],
    *,
    bwrap_bin: str | None = None,
    enable_sandbox: bool = True,
) -> list[str]:
    """Constructs the command argv for a stdio MCP server.
    - `computer-use-linux`, `eye2byte`, `uacc`, `deskwright`: wrapped in `bwrap --unshare-net`
      (plus `--die-with-parent`, `--new-session`) when bubblewrap is present.
    - `shadow-vision`: wrapped in `bwrap` WITHOUT `--unshare-net` so it can reach
      local Ollama on 127.0.0.1:11434 + Wayland AF_UNIX socket while isolating sensitive paths.
    """
    cmd_val = spec.get("command")
    if isinstance(cmd_val, list):
        raw_argv = [str(x) for x in cmd_val if str(x)]
    elif isinstance(cmd_val, str) and cmd_val.strip():
        raw_argv = [cmd_val.strip(), *[str(a) for a in (spec.get("args") or [])]]
    else:
        raise ValueError(f"stdio MCP server '{server_name}' is missing 'command'.")

    if not enable_sandbox:
        return raw_argv

    bwrap = bwrap_bin if bwrap_bin is not None else shutil.which("bwrap")
    if not bwrap:
        return raw_argv

    sandbox_mode = str(spec.get("sandbox") or MCP_CATALOG_BY_NAME.get(server_name, {}).get("sandbox", "bwrap_unshare_net"))
    home = str(Path.home())
    xdg_rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

    argv: list[str] = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind-try",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/bin",
        "/bin",
        "--ro-bind-try",
        "/etc",
        "/etc",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",  # nosec B108 — bwrap sandbox mount point, not temp file creation
        "--ro-bind-try",
        xdg_rt,
        xdg_rt,
        "--ro-bind-try",
        f"{home}/.local",
        f"{home}/.local",
        "--tmpfs",
        f"{home}/.local/share/celina",
        "--tmpfs",
        f"{home}/.config/celina",
        "--tmpfs",
        f"{home}/.aws",
        "--tmpfs",
        f"{home}/.kube",
        "--tmpfs",
        f"{home}/.ssh",
        "--tmpfs",
        f"{home}/.gnupg",
    ]
    if sandbox_mode == "bwrap_unshare_net":
        argv.append("--unshare-net")
    # Note: `bwrap_loopback_only` omits `--unshare-net` so Shadow Vision can reach 127.0.0.1:11434 Ollama
    # while `validate_server_policy` strictly forces `OLLAMA_URL` to loopback.
    argv.append("--")
    argv.extend(raw_argv)
    return argv


@dataclass(frozen=True)
class MCPCallReceipt:
    server: str
    tool: str
    remote_tool: str
    arguments_hash: str
    mcp_transport: str
    ok: bool
    output: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "tool": self.tool,
            "remote_tool": self.remote_tool,
            "arguments_hash": self.arguments_hash,
            "mcp_transport": self.mcp_transport,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
        }


def hash_mcp_arguments(arguments: dict[str, Any] | None) -> str:
    canonical = json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


class MCPLoader:
    """Autonomy-layer MCP client loader for Celina v5.
    Reads `mcpServers` (and legacy `servers`) from `mcp-servers.json`, spawns sandboxed
    stdio processes, connects Streamable HTTP servers, and exposes deterministic capability
    routing + JSON-RPC 2.0 `tools/list` & `tools/call`.
    """

    def __init__(
        self,
        config_path: Path | None = None,
        *,
        use_sandbox: bool = True,
        web_comms_enabled: bool | None = None,
        http_session: requests.Session | None = None,
    ):
        self.config_path = Path(config_path) if config_path is not None else config.MCP_CONFIG_FILE
        self.use_sandbox = bool(use_sandbox)
        self._web_comms_override = web_comms_enabled
        self._http = http_session or requests.Session()
        self._lock = threading.RLock()
        self._rpc_id = 0
        self.config: dict[str, Any] = self._load_config()
        self.servers: dict[str, dict[str, Any]] = {}

    def _load_config(self) -> dict[str, Any]:
        if self.config_path.is_file():
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except (OSError, json.JSONDecodeError):
                pass
        return {"mcpServers": {e["name"]: dict(e["config"]) for e in MCP_SERVER_CATALOG}}

    def reload_config(self) -> dict[str, Any]:
        with self._lock:
            self.config = self._load_config()
            return self.config

    def configured_servers(self) -> dict[str, dict[str, Any]]:
        """Merges `mcpServers` (preferred) and legacy `servers` dictionaries cleanly."""
        merged: dict[str, dict[str, Any]] = {}
        legacy = self.config.get("servers")
        if isinstance(legacy, dict):
            for k, v in legacy.items():
                if isinstance(v, dict):
                    merged[str(k)] = dict(v)
        modern = self.config.get("mcpServers")
        if isinstance(modern, dict):
            for k, v in modern.items():
                if isinstance(v, dict):
                    merged[str(k)] = dict(v)
        return merged

    def _next_id(self) -> int:
        with self._lock:
            self._rpc_id += 1
            return self._rpc_id

    def _expand_env(self, extra_env: dict[str, Any] | None) -> dict[str, str]:
        env = dict(os.environ)
        if not isinstance(extra_env, dict):
            return env
        for k, v in extra_env.items():
            s = str(v)
            if s.startswith("${") and s.endswith("}"):
                env[str(k)] = os.environ.get(s[2:-1], "")
            else:
                env[str(k)] = s
        return env

    def start_server(self, server_name: str, *, force_enable: bool = False) -> dict[str, Any]:
        """Starts one configured stdio server or registers one Streamable HTTP server."""
        with self._lock:
            all_cfg = self.configured_servers()
            spec = all_cfg.get(server_name)
            if spec is None:
                # Fall back to catalog definition if explicitly forced
                cat = MCP_CATALOG_BY_NAME.get(server_name)
                if cat is None or not force_enable:
                    raise KeyError(f"MCP server '{server_name}' is not configured.")
                spec = dict(cat["config"])

            validate_server_policy(server_name, spec, web_comms_enabled=self._web_comms_override)
            if not force_enable and spec.get("enabled") is False:
                raise MCPPolicyError(f"MCP server '{server_name}' is disabled in {self.config_path}.")

            existing = self.servers.get(server_name)
            if existing is not None:
                if existing["type"] == "http":
                    return existing
                proc = existing.get("proc")
                if proc is not None and proc.poll() is None:
                    return existing

            if "command" in spec:
                # P4 D4: Resolve uvx/npx commands to hash-verified cached versions
                cmd_val = spec.get("command")
                args = spec.get("args") or []
                cached_cmd = None
                if isinstance(cmd_val, str) and cmd_val.strip() == "uvx" and args:
                    # uvx --from <pkg>==<ver> <pkg>
                    pkg_idx = None
                    for i, a in enumerate(args):
                        if a.startswith("--from"):
                            pkg_idx = i
                            break
                    if pkg_idx is not None and pkg_idx + 1 < len(args):
                        from_spec = args[pkg_idx + 1]
                        if "==" in from_spec:
                            package, version = from_spec.split("==", 1)
                            integrity = spec.get("integrity", "")
                            expected_sha = None
                            if integrity.startswith("sha256:"):
                                expected_sha = integrity.split(":", 1)[1]
                            cached_cmd = resolve_cached_uvx_command(
                                package, version, expected_sha,
                            )
                elif isinstance(cmd_val, str) and cmd_val.strip() == "npx" and args:
                    # npx -y <pkg>@<ver>
                    for i, a in enumerate(args):
                        if a.startswith("-y") and i + 1 < len(args):
                            pkg_spec = args[i + 1]
                            if "@" in pkg_spec:
                                package, version = pkg_spec.rsplit("@", 1)
                                integrity = spec.get("integrity", "")
                                expected_sha = None
                                if integrity.startswith("sha256:"):
                                    expected_sha = integrity.split(":", 1)[1]
                                cached_cmd = resolve_cached_npx_command(
                                    package, version, expected_sha,
                                )
                            break

                if cached_cmd is not None:
                    # Replace command/args with cached version
                    spec = dict(spec)
                    spec["command"] = cached_cmd[0]
                    spec["args"] = cached_cmd[1:]

                argv = build_sandboxed_stdio_cmd(server_name, spec, enable_sandbox=self.use_sandbox)
                env = self._expand_env(spec.get("env"))
                # Point uvx/npm at persistent cache directories (offline-first)
                env["UV_CACHE_DIR"] = str(UV_CACHE_DIR)
                env["npm_config_cache"] = str(NPM_CACHE_DIR)
                proc = subprocess.Popen(
                    argv,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                )
                entry = {
                    "type": "stdio",
                    "proc": proc,
                    "argv": argv,
                    "spec": spec,
                    "initialized": False,
                    "_rx_buf": bytearray(),
                }
                self.servers[server_name] = entry
                return entry

            if "url" in spec:
                entry = {
                    "type": "http",
                    "url": str(spec["url"]).rstrip("/"),
                    "headers": dict(spec.get("headers") or {}),
                    "spec": spec,
                }
                self.servers[server_name] = entry
                return entry

            raise ValueError(f"MCP server '{server_name}' must declare either 'command' (stdio) or 'url' (http).")

    def start_all(self, *, only_enabled: bool = True) -> dict[str, str]:
        """Starts all configured MCP servers that are enabled (skipping disabled/opt-in servers
        like `n8n` cleanly unless enabled and permitted by policy).
        Returns `{server_name: status}`.
        """
        statuses: dict[str, str] = {}
        for name, spec in self.configured_servers().items():
            try:
                check_mcp_blocklist(name, spec)
            except MCPPolicyError as exc:
                statuses[name] = f"blocked: {exc}"
                continue
            if only_enabled and not bool(spec.get("enabled", False)):
                statuses[name] = "disabled"
                continue
            try:
                self.start_server(name)
                statuses[name] = "started"
            except Exception as exc:
                statuses[name] = f"error: {exc}"
        return statuses

    def stop_server(self, server_name: str, *, timeout: float = 2.0) -> bool:
        with self._lock:
            srv = self.servers.pop(server_name, None)
            if not srv:
                return False
            proc = srv.get("proc")
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except OSError:
                    pass
            return True

    def stop_all(self) -> int:
        with self._lock:
            names = list(self.servers.keys())
        stopped = 0
        for name in names:
            if self.stop_server(name):
                stopped += 1
        return stopped

    def _readline_with_timeout(
        self,
        srv: dict[str, Any],
        server_name: str,
        *,
        timeout: float = 15.0,
    ) -> str:
        proc: subprocess.Popen[bytes] = srv["proc"]
        if proc.stdout is None:
            raise RuntimeError(f"stdio MCP server '{server_name}' has no stdout pipe.")
        rx_buf: bytearray = srv.setdefault("_rx_buf", bytearray())
        deadline = time.monotonic() + max(0.05, float(timeout))

        while True:
            nl_idx = rx_buf.find(b"\n")
            if nl_idx != -1:
                raw_line = bytes(rx_buf[: nl_idx + 1])
                del rx_buf[: nl_idx + 1]
                return raw_line.decode("utf-8", errors="replace")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"stdio MCP server '{server_name}' timed out after {timeout:.1f}s waiting for response.",
                )

            fd = proc.stdout.fileno()
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise TimeoutError(
                    f"stdio MCP server '{server_name}' timed out after {timeout:.1f}s waiting for response.",
                )

            chunk = os.read(fd, 4096)
            if not chunk:
                if rx_buf:
                    raw_line = bytes(rx_buf)
                    rx_buf.clear()
                    return raw_line.decode("utf-8", errors="replace")
                raise RuntimeError(f"stdio MCP server '{server_name}' closed stdout without response.")
            rx_buf.extend(chunk)

    def _read_jsonrpc_response(
        self,
        srv: dict[str, Any],
        server_name: str,
        expected_id: int,
        *,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.05, float(timeout))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"stdio MCP server '{server_name}' timed out after {timeout:.1f}s waiting for id={expected_id}.",
                )
            line = self._readline_with_timeout(srv, server_name, timeout=remaining).strip()
            if not line:
                continue
            msg = json.loads(line)
            if not isinstance(msg, dict):
                continue
            # Skip server-initiated notifications (no 'id') until our matching response arrives
            if "id" not in msg:
                continue
            if msg.get("id") == expected_id:
                return msg

    @staticmethod
    def _write_stdio_frame(proc: subprocess.Popen[bytes], payload: dict[str, Any]) -> None:
        if proc.stdin is None:
            raise RuntimeError("stdio MCP server stdin is closed.")
        proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def _stdio_rpc(
        self,
        server_name: str,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        with self._lock:
            srv = self.servers.get(server_name)
            if srv is None:
                srv = self.start_server(server_name)
            proc: subprocess.Popen[bytes] = srv["proc"]
            if proc.poll() is not None or proc.stdin is None or proc.stdout is None:
                raise RuntimeError(f"stdio MCP server '{server_name}' is not running (exit={proc.poll()}).")

            # Send MCP handshake: probe server/discover (2026-07-28) first,
            # fall back to initialize (2024-11-05) for legacy servers.
            if not srv.get("initialized") and method != "initialize":
                negotiated_version = None
                for proto_ver in MCP_PROTOCOL_VERSIONS:
                    handshake_method = "server/discover" if proto_ver == "2026-07-28" else "initialize"
                    handshake_id = self._next_id()
                    handshake_req = {
                        "jsonrpc": "2.0",
                        "id": handshake_id,
                        "method": handshake_method,
                        "params": {
                            "protocolVersion": proto_ver,
                            "capabilities": {},
                            "clientInfo": {"name": "celina-autonomy", "version": "4.4.0"},
                        },
                    }
                    self._write_stdio_frame(proc, handshake_req)
                    handshake_resp = self._read_jsonrpc_response(
                        srv, server_name, handshake_id, timeout=timeout,
                    )
                    if handshake_resp.get("error") is None:
                        negotiated_version = proto_ver
                        break
                if negotiated_version is None:
                    raise RuntimeError(
                        f"MCP handshake failed for all protocol versions {MCP_PROTOCOL_VERSIONS} "
                        f"({server_name})",
                    )
                srv["protocol_version"] = negotiated_version
                self._write_stdio_frame(
                    proc,
                    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                )
                srv["initialized"] = True

            req_id = self._next_id()
            req = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params,
            }
            self._write_stdio_frame(proc, req)
            resp = self._read_jsonrpc_response(srv, server_name, req_id, timeout=timeout)
            if "error" in resp and resp["error"] is not None:
                raise RuntimeError(f"MCP RPC error ({server_name}.{method}): {resp['error']}")
            return dict(resp.get("result") or {})

    def _http_rpc(self, server_name: str, method: str, params: dict[str, Any], *, timeout: float = 15.0) -> dict[str, Any]:
        srv = self.servers.get(server_name)
        if srv is None:
            srv = self.start_server(server_name)
        url = str(srv["url"])
        spec = srv.get("spec") or {}
        validate_server_policy(server_name, spec, web_comms_enabled=self._web_comms_override)

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        for hk, hv in (srv.get("headers") or {}).items():
            s = str(hv)
            if "${" in s and "}" in s:
                # Expand ${VAR} placeholders from environment
                start = s.find("${")
                end = s.find("}", start)
                if end > start:
                    var_name = s[start + 2 : end]
                    s = s[:start] + os.environ.get(var_name, "") + s[end + 1 :]
            headers[str(hk)] = s

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        resp = self._http.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("error") is not None:
            raise RuntimeError(f"MCP HTTP RPC error ({server_name}.{method}): {body['error']}")
        if isinstance(body, dict) and "result" in body:
            return dict(body["result"] or {})
        return dict(body) if isinstance(body, dict) else {"result": body}

    def list_tools(self, server_name: str) -> list[dict[str, Any]]:
        srv = self.servers.get(server_name) or self.start_server(server_name)
        if srv["type"] == "stdio":
            res = self._stdio_rpc(server_name, "tools/list", {})
        else:
            res = self._http_rpc(server_name, "tools/list", {})
        return list(res.get("tools") or [])

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        celina_tool_name: str | None = None,
    ) -> MCPCallReceipt:
        """Invokes a remote MCP tool and returns a structured receipt for evidence & /why."""
        args = dict(arguments or {})
        args_hash = hash_mcp_arguments(args)
        logical_tool = celina_tool_name or tool_name
        try:
            srv = self.servers.get(server_name) or self.start_server(server_name)
            transport = str(srv["type"])
            if transport == "stdio":
                res = self._stdio_rpc(server_name, "tools/call", {"name": tool_name, "arguments": args})
            else:
                res = self._http_rpc(server_name, "tools/call", {"name": tool_name, "arguments": args})
            # Extract MCP content blocks cleanly
            content = res.get("content")
            if isinstance(content, list):
                parts = [str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content]
                out_text = "\n".join(parts).strip() or json.dumps(res, sort_keys=True)
            else:
                out_text = json.dumps(res, sort_keys=True)
            is_err = bool(res.get("isError", False))
            return MCPCallReceipt(
                server=server_name,
                tool=logical_tool,
                remote_tool=tool_name,
                arguments_hash=args_hash,
                mcp_transport=transport,
                ok=not is_err,
                output=out_text,
                error=out_text if is_err else "",
            )
        except Exception as exc:
            spec = self.configured_servers().get(server_name) or MCP_CATALOG_BY_NAME.get(server_name, {}).get("config", {})
            transport = "http" if "url" in spec else "stdio"
            return MCPCallReceipt(
                server=server_name,
                tool=logical_tool,
                remote_tool=tool_name,
                arguments_hash=args_hash,
                mcp_transport=transport,
                ok=False,
                output="",
                error=str(exc),
            )

    def route_capability(self, role: str) -> str | None:
        """Returns the highest-priority enabled MCP server fulfilling `role`."""
        cfg = self.configured_servers()
        candidates = ROLE_SERVER_ORDER.get(role, ())
        for name in candidates:
            spec = cfg.get(name)
            if spec and bool(spec.get("enabled", False)):
                try:
                    validate_server_policy(name, spec, web_comms_enabled=self._web_comms_override)
                    return name
                except MCPPolicyError:
                    continue
        return None

    def execute_domain_tool(
        self,
        celina_tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> MCPCallReceipt:
        """Executes one of the 5 `mcp_perception_actuation` domain tools via its linked server."""
        route = MCP_TOOL_ROUTING.get(celina_tool_name)
        if not route:
            return MCPCallReceipt(
                server="unknown",
                tool=celina_tool_name,
                remote_tool=celina_tool_name,
                arguments_hash=hash_mcp_arguments(arguments),
                mcp_transport="unknown",
                ok=False,
                output="",
                error=f"Unknown MCP perception/actuation tool '{celina_tool_name}'.",
            )
        return self.call_tool(
            route["server"],
            route["remote_tool"],
            arguments or {},
            celina_tool_name=celina_tool_name,
        )

    def status(self) -> dict[str, Any]:
        cfg = self.configured_servers()
        entries = []
        for item in MCP_SERVER_CATALOG:
            name = item["name"]
            scfg = cfg.get(name) or item["config"]
            active = name in self.servers
            entries.append(
                {
                    "order": item["order"],
                    "name": name,
                    "role": item["role"],
                    "transport": item["transport"],
                    "priority": item["priority"],
                    "sandbox": item["sandbox"],
                    "enabled": bool(scfg.get("enabled", False)),
                    "running": active,
                },
            )
        return {
            "config_path": str(self.config_path),
            "web_comms_enabled": (
                config.is_web_comms_enabled() if self._web_comms_override is None else bool(self._web_comms_override)
            ),
            "servers": entries,
        }


def get_catalog_preset(server_name: str) -> dict[str, Any]:
    """Returns a deep copy of a curated MCP server config entry (rejecting blocklisted entries)."""
    check_mcp_blocklist(server_name)
    entry = MCP_CATALOG_BY_NAME.get(server_name.strip().lower())
    if entry is None:
        valid = ", ".join(e["name"] for e in MCP_SERVER_CATALOG)
        raise ValueError(f"Unknown MCP catalog preset '{server_name}'. Valid presets: {valid}")
    return json.loads(json.dumps(entry["config"]))


_DEFAULT_LOADER: MCPLoader | None = None


def get_mcp_loader() -> MCPLoader:
    global _DEFAULT_LOADER
    if _DEFAULT_LOADER is None:
        _DEFAULT_LOADER = MCPLoader()
    return _DEFAULT_LOADER
