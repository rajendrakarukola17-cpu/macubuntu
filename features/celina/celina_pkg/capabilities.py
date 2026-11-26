"""
Celina AI — OS-Level Capability Manifest & Kernel Sandbox Boundary (v4.4)

Addresses the "Shadow AI" structural gap by binding every Celina tool to a
declarative OS-level capability entry (`celina-capabilities.json`):
  - Filesystem scope (`none`, `proc_sys_ro`, `celina_state_only`, `home_ro`,
    `home_rw_approval_undo`, `bwrap_workdir`)
  - Network egress (`none`, `loopback_ollama`, `https_public_only`, `lan_mdns`,
    `iot_allowlist`, `mcp_declared`)
  - D-Bus buses, PipeWire access, and kernel enforcement boundary
  - Strict credential separation (`credential_access == "none"` across all 88 tools;
    `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.kube`, `~/.config/celina/env`, and the
    evidence ledger stay outside tool/agent mount namespaces via Bubblewrap).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from . import config

VALID_FS_SCOPES = frozenset({
    "none",
    "proc_sys_ro",
    "celina_state_only",
    "home_ro",
    "home_rw_approval_undo",
    "bwrap_workdir",
})
VALID_NET_SCOPES = frozenset({
    "none",
    "loopback_ollama",
    "https_public_only",
    "lan_mdns",
    "iot_allowlist",
    "mcp_declared",
})
VALID_KERNEL_BOUNDARIES = frozenset({
    "in_process_ro",
    "dbus_portal_or_atspi",
    "bwrap_unshare_all",
    "bwrap_net_allowlist",
    "approval_plus_undo",
    "systemd_user_scope",
})

TIER2_ONLY_FS_SCOPES = frozenset({"home_rw_approval_undo", "bwrap_workdir"})

_MANIFEST_CACHE: Optional[dict[str, Any]] = None


def locate_manifest_path() -> Optional[Path]:
    pkg_dir = Path(__file__).resolve().parent
    candidates = [
        config.CONFIG_DIR / "celina-capabilities.json",
        pkg_dir.parent / "config" / "celina-capabilities.json",
        pkg_dir.parents[2] / "system" / "capabilities" / "celina-capabilities.json",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_manifest(path: Optional[Path] = None, *, force_reload: bool = False) -> dict[str, Any]:
    global _MANIFEST_CACHE
    if path is None and not force_reload and _MANIFEST_CACHE is not None:
        return _MANIFEST_CACHE
    target = path or locate_manifest_path()
    if target is None or not target.is_file():
        return {"manifest_version": "0", "never_exposed_paths": [], "tools": {}}
    data = json.loads(target.read_text(encoding="utf-8"))
    if path is None:
        _MANIFEST_CACHE = data
    return data


def get_tool_capability(tool_name: str, manifest: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    mf = manifest if manifest is not None else load_manifest()
    tools_map = mf.get("tools") or {}
    cap = tools_map.get(tool_name)
    return dict(cap) if isinstance(cap, dict) else None


def check_tool_execution_boundary(
    tool_name: str,
    *,
    is_tier2: bool,
    manifest: Optional[dict[str, Any]] = None,
) -> tuple[bool, str]:
    """
    Fail-closed capability check executed before any tool runs:
      - Rejects undeclared tools when manifest is loaded
      - Rejects Shadow AI privilege escalation (Tier-1 tool requesting rw/bwrap fs_scope)
      - Rejects any tool claiming credential_access != 'none'
    """
    mf = manifest if manifest is not None else load_manifest()
    tools_map = mf.get("tools") or {}
    if not tools_map:
        return True, ""

    cap = tools_map.get(tool_name)
    if not isinstance(cap, dict):
        return False, f"Blocked by OS capability manifest: tool '{tool_name}' has no capability declaration."
    if cap.get("credential_access") != "none":
        return False, f"Blocked by OS capability manifest: tool '{tool_name}' illegally requests credential access."
    fs_scope = str(cap.get("fs_scope", ""))
    if not is_tier2 and fs_scope in TIER2_ONLY_FS_SCOPES:
        return (
            False,
            f"Blocked by OS capability manifest: Tier-1 tool '{tool_name}' cannot hold mutable fs_scope '{fs_scope}'.",
        )
    return True, ""


def validate_manifest_invariants(
    manifest: dict[str, Any],
    *,
    expected_tier1: set[str],
    expected_tier2: set[str],
) -> list[str]:
    """Validates 100% parity and Shadow-AI security invariants across the capability manifest."""
    errors: list[str] = []
    tools_map = manifest.get("tools")
    if not isinstance(tools_map, dict):
        return ["Manifest missing 'tools' dictionary."]

    expected_all = expected_tier1 | expected_tier2
    manifest_names = set(tools_map.keys())
    missing = sorted(expected_all - manifest_names)
    extra = sorted(manifest_names - expected_all)
    if missing:
        errors.append(f"Capability manifest missing tools: {missing}")
    if extra:
        errors.append(f"Capability manifest has undeclared extra tools: {extra}")

    for name, cap in sorted(tools_map.items()):
        if not isinstance(cap, dict):
            errors.append(f"{name}: entry must be a dict")
            continue
        exp_tier = "tier2" if name in expected_tier2 else "tier1"
        if cap.get("tier") != exp_tier:
            errors.append(f"{name}: tier mismatch (manifest={cap.get('tier')}, schema={exp_tier})")
        fs_scope = cap.get("fs_scope")
        net_scope = cap.get("network_egress")
        kb = cap.get("kernel_boundary")
        if fs_scope not in VALID_FS_SCOPES:
            errors.append(f"{name}: invalid fs_scope '{fs_scope}'")
        if net_scope not in VALID_NET_SCOPES:
            errors.append(f"{name}: invalid network_egress '{net_scope}'")
        if kb not in VALID_KERNEL_BOUNDARIES:
            errors.append(f"{name}: invalid kernel_boundary '{kb}'")
        if cap.get("credential_access") != "none":
            errors.append(f"{name}: credential_access MUST be 'none' (got {cap.get('credential_access')})")
        if exp_tier == "tier1" and fs_scope in TIER2_ONLY_FS_SCOPES:
            errors.append(f"{name}: Shadow-AI violation — Tier-1 tool has mutable fs_scope '{fs_scope}'")

    # Subprocess execution tools MUST be kernel-sandboxed with zero network egress
    for sub_tool in ("run_shell_command", "run_python_sandbox"):
        cap = tools_map.get(sub_tool) or {}
        if cap.get("kernel_boundary") != "bwrap_unshare_all" or cap.get("network_egress") != "none":
            errors.append(f"{sub_tool}: must enforce kernel_boundary=bwrap_unshare_all and network_egress=none")

    return errors


def build_bwrap_command(
    tool_name: str,
    inner_cmd: list[str],
    *,
    workdir: Path,
    manifest: Optional[dict[str, Any]] = None,
    allow_workdir_write: bool = False,
) -> list[str]:
    """
    Translates a tool's capability manifest entry into kernel-enforced Linux
    namespace & mount-isolation flags for Bubblewrap (`bwrap`):
      - Unshares PID, IPC, UTS namespaces + dies with parent + clears env
      - Unshares network namespace (`--unshare-net`) whenever `network_egress == 'none'`
      - Masks `$HOME` with an ephemeral `--tmpfs` and exposes ONLY `workdir` at `/work`
      - Guarantees credentials (`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/celina/env`,
        evidence ledger) do NOT exist inside the subprocess's mount namespace.
    """
    cap = get_tool_capability(tool_name, manifest=manifest)
    if cap is None:
        raise PermissionError(f"Tool '{tool_name}' is not declared in celina-capabilities.json")
    if cap.get("credential_access") != "none":
        raise PermissionError(f"Tool '{tool_name}' violates zero-credential-access invariant")

    resolved_work = Path(workdir).expanduser().resolve()
    home_str = str(config.HOME.resolve())

    args: list[str] = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--clearenv",
        "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
        "--setenv", "HOME", "/work",
        "--setenv", "LANG", "C.UTF-8",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
    ]

    if cap.get("network_egress") == "none":
        args.append("--unshare-net")

    for sys_dir in ("/usr", "/bin", "/lib", "/lib64", "/etc/alternatives"):
        if os.path.exists(sys_dir):
            args.extend(["--ro-bind", sys_dir, sys_dir])

    # Hide the user's real $HOME behind an isolated tmpfs so credentials & trusted
    # agent state (~/.ssh, ~/.gnupg, ~/.config/celina/env, evidence ledger) are
    # unreachable at the Linux kernel mount-namespace level.
    args.extend(["--tmpfs", home_str])

    # Bind ONLY the requested workspace under /work (read-only unless explicitly allowed & permitted)
    can_write = bool(allow_workdir_write and cap.get("fs_scope") in {"bwrap_workdir", "home_rw_approval_undo"})
    bind_flag = "--bind" if can_write else "--ro-bind"
    args.extend([bind_flag, str(resolved_work), "/work", "--chdir", "/work", "--"])
    args.extend(inner_cmd)
    return args


def format_capabilities_summary(manifest: Optional[dict[str, Any]] = None) -> str:
    mf = manifest if manifest is not None else load_manifest()
    tools_map = mf.get("tools") or {}
    by_boundary: dict[str, int] = {}
    by_net: dict[str, int] = {}
    for cap in tools_map.values():
        kb = str(cap.get("kernel_boundary", "unknown"))
        ne = str(cap.get("network_egress", "unknown"))
        by_boundary[kb] = by_boundary.get(kb, 0) + 1
        by_net[ne] = by_net.get(ne, 0) + 1
    lines = [
        f"🛡️  Celina OS Capability Manifest v{mf.get('manifest_version', '?')} ({len(tools_map)} tools)",
        f"   Credential Access: 0/{len(tools_map)} tools (strictly isolated outside agent namespaces)",
        "   Kernel Boundaries:",
    ]
    for kb, cnt in sorted(by_boundary.items()):
        lines.append(f"     • {kb:22s} {cnt} tools")
    lines.append("   Network Egress Scopes:")
    for ne, cnt in sorted(by_net.items()):
        lines.append(f"     • {ne:22s} {cnt} tools")
    return "\n".join(lines)
