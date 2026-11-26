#!/usr/bin/env python3
# ============================================================================
# celina_pkg/doctor.py — Celina Startup Contract Check (reads state.json)
#
# Reads /etc/macubuntu/state.json (written by macubuntu stage 99) and reports
# capability gaps. Never re-probes — the whole point of state.json is that
# Celina reads what macubuntu already verified.
#
# Usage:
#   python3 -m celina_pkg.doctor        # human-readable report, exit 0/1
#   python3 -c "from celina_pkg.doctor import run_doctor; print(run_doctor())"
# ============================================================================

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

STATE_FILE = Path("/etc/macubuntu/state.json")

# Security-critical gaps that make the status "degraded" (not just warnings)
SECURITY_GAP_KEYS = (
    "bwrap_userns",
    "landlock_abi",
    "apparmor_userns_restrict",
    "secure_boot",
    "ydotool_socket_acl",
)

# Services that must be "enabled" for Celina to function
REQUIRED_SERVICES = ("celina.service", "celina-eventd.service", "ollama.service")


def _load_state() -> dict[str, Any] | None:
    """Load state.json. Returns None if missing or unreadable."""
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _ollama_reachable() -> bool:
    """Best-effort check: can we reach the Ollama endpoint from state.json?"""
    state = _load_state()
    if state is None:
        return False
    endpoint = state.get("endpoints", {}).get("ollama")
    if not endpoint:
        return False
    try:
        import urllib.request

        req = urllib.request.Request(endpoint.rstrip("/") + "/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:  # nosec B310 — local endpoint health check, scheme fixed to http
            return resp.status == 200
    except Exception:
        return False


def run_doctor() -> dict[str, Any]:
    """Run the Celina contract check against state.json.

    Returns a dict with keys:
      status   — "ok", "degraded", or "critical"
      message  — human-readable one-line summary
      gaps     — list of capability gaps (degraded status)
      warnings — list of non-critical warnings
      state    — the raw state.json dict (or None)
    """
    state = _load_state()

    if state is None:
        return {
            "status": "critical",
            "message": "state.json missing — macubuntu not installed or corrupted",
            "gaps": ["state.json"],
            "warnings": [],
            "state": None,
        }

    gaps: list[str] = []
    warnings: list[str] = []

    # --- Security prerequisites (read from state.json, no re-probing) ---
    security = state.get("security", {})

    if security.get("bwrap_userns") != "ok":
        gaps.append("bwrap_userns: sandbox unavailable — bwrap missing or restricted")

    atspi = security.get("atspi_enabled", False)
    if not atspi:
        warnings.append("AT-SPI disabled — GUI automation tools unavailable")

    landlock = security.get("landlock_abi", 0)
    apparmor = security.get("apparmor_userns_restrict", False)
    if apparmor and landlock < 4:
        gaps.append(f"landlock_abi={landlock} < 4 with apparmor_userns_restrict=true")

    if not security.get("secure_boot", False):
        warnings.append("Secure Boot not enabled — disk encryption keys not hardware-bound")

    ydotool = security.get("ydotool_socket_acl", "unavailable")
    if ydotool != "0600":
        gaps.append(f"ydotool_socket_acl={ydotool} (expected 0600)")

    # --- Service health (read from state.json, no re-probing) ---
    services = state.get("services", {})
    for svc in REQUIRED_SERVICES:
        svc_status = services.get(svc, "unknown")
        if svc_status != "enabled":
            gaps.append(f"service:{svc} ({svc_status})")

    # --- Endpoint availability (check via state.json endpoint, light probe only) ---
    endpoints = state.get("endpoints", {})
    ollama_url = endpoints.get("ollama")
    if ollama_url and not _ollama_reachable():
        warnings.append(f"Ollama endpoint not reachable: {ollama_url}")

    # --- Stage degradation (read from state.json) ---
    stages = state.get("stages", {})
    for stage_num, stage_info in stages.items():
        stage_status = stage_info.get("status", "unknown")
        stage_failed = stage_info.get("failed", [])
        if stage_status != "ok":
            gap_detail = f" (failed: {', '.join(stage_failed)})" if stage_failed else ""
            gaps.append(f"stage:{stage_num} ({stage_status}{gap_detail})")

    # --- Hardware ---
    hardware = state.get("hardware", {})
    if not hardware.get("has_luks", False):
        warnings.append("No LUKS full-disk encryption detected")

    status = "ok" if not gaps else "degraded"
    if state.get("schema_version") != "1.0":
        status = "critical"
        gaps.insert(0, "state.json schema_version != 1.0")

    message = (
        f"Celina contract: {status}"
        + (f" — {len(gaps)} gap(s)" if gaps else " — all capabilities verified")
    )
    if warnings:
        message += f" | {len(warnings)} warning(s)"

    return {
        "status": status,
        "message": message,
        "gaps": gaps,
        "warnings": warnings,
        "state": state,
    }


def print_report(report: dict[str, Any]) -> None:
    """Print a human-readable doctor report."""
    status = report["status"]
    icon = {"ok": "✅", "degraded": "⚠️", "critical": "❌"}.get(status, "❓")

    print("============================================================")
    print("  🩺 Celina Doctor — Contract Check")
    print("============================================================")
    print(f"  {icon} Status: {status}")
    print(f"     {report['message']}")
    print()

    if report.get("state"):
        state = report["state"]
        print(f"  Installer version: {state.get('installer_version', 'unknown')}")
        print(f"  Celina version:    {state.get('celina_version', 'unknown')}")
        print(f"  Profile:           {state.get('profile', 'unknown')}")
        print(f"  Ubuntu:            {state.get('ubuntu', 'unknown')}")
        print(f"  Kernel:            {state.get('kernel', 'unknown')}")
        print(f"  Installed:         {state.get('install_timestamp', 'unknown')}")
        print()

    if report["gaps"]:
        print(f"  ❗ Capability gaps ({len(report['gaps'])}):")
        for gap in report["gaps"]:
            print(f"     • {gap}")
        print()

    if report["warnings"]:
        print(f"  ⚠️  Warnings ({len(report['warnings'])}):")
        for warn in report["warnings"]:
            print(f"     • {warn}")
        print()

    if not report["gaps"] and not report["warnings"]:
        print("  All capabilities verified. Celina is ready to operate.")
        print()

    print("============================================================")


def main() -> int:
    report = run_doctor()
    print_report(report)
    if report["status"] == "critical":
        return 2
    if report["status"] == "degraded":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
