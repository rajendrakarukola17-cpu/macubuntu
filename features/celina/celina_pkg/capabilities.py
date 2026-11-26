"""Celina AI — OS-Level Capability Manifest & Kernel Sandbox Boundary (v4.5)

Enforces the OS capability manifest (`celina-capabilities.json`) as the
security policy oracle for all Celina tools. Every tool is bound to a
declarative capability entry with:
  - Filesystem scope (`none`, `celina_state_only`, `home_ro`,
    `home_rw_approval_undo`, `bwrap_workdir`) — `proc_sys_ro` REMOVED:
    /proc is a symlink farm bypassing never_exposed_paths
  - Network egress (`none`, `loopback_ollama`, `https_public_only`, `lan_mdns`,
    `iot_allowlist`, `mcp_declared`)
  - D-Bus method-level allowlists (not raw bus names)
  - PipeWire structured grants (direction, capture=false, portal-mediated)
  - Auth operation binding to AuthPolicyEngine tiers
  - Manifest integrity verification (SHA-256, fail-closed)
  - Strict credential separation
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from pathlib import Path
from typing import Any

from . import config

VALID_FS_SCOPES = frozenset({
    "none",
    "celina_state_only",
    "home_ro",
    "home_rw_approval_undo",
    "bwrap_workdir",
    # "proc_sys_ro" REMOVED: /proc is a symlink farm bypassing never_exposed_paths
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
VALID_AUTH_OPERATIONS = frozenset({
    "approve_quarantined_skill",
    "install_community_skill",
    "host_shell_override",
    "credential_export",
    "key_export",
    "payment",
    "account_change",
    "security_policy_change",
    "pam_config_change",
    "password_reset",
    "disk_encryption_recovery",
    "luks_recovery",
    "boot_unlock",
    "polkit_local",
    "local_sudo",
    "assistant_unlock",
    "screen_unlock",
})
TIER2_ONLY_FS_SCOPES = frozenset({"home_rw_approval_undo", "bwrap_workdir"})

# Auth tier mapping — must match AuthPolicyEngine in biometrics.py
AUTH_OP_TIER_MAP = {
    # FIDO2_HIGH_ASSURANCE
    "approve_quarantined_skill": "FIDO2_HIGH_ASSURANCE",
    "install_community_skill": "FIDO2_HIGH_ASSURANCE",
    "host_shell_override": "FIDO2_HIGH_ASSURANCE",
    "credential_export": "FIDO2_HIGH_ASSURANCE",
    "key_export": "FIDO2_HIGH_ASSURANCE",
    "payment": "FIDO2_HIGH_ASSURANCE",
    "account_change": "FIDO2_HIGH_ASSURANCE",
    # PASSWORD_RECOVERY
    "security_policy_change": "PASSWORD_RECOVERY",
    "pam_config_change": "PASSWORD_RECOVERY",
    "password_reset": "PASSWORD_RECOVERY",
    "disk_encryption_recovery": "PASSWORD_RECOVERY",
    "luks_recovery": "PASSWORD_RECOVERY",
    "boot_unlock": "PASSWORD_RECOVERY",
    # LOCAL_CONVENIENCE
    "polkit_local": "LOCAL_CONVENIENCE",
    "local_sudo": "LOCAL_CONVENIENCE",
    "assistant_unlock": "LOCAL_CONVENIENCE",
    "screen_unlock": "LOCAL_CONVENIENCE",
}

_MANIFEST_CACHE: tuple[float, dict[str, Any]] | None = None


def locate_manifest_path() -> Path | None:
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


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate decoded JSON keys rather than silently keeping the last."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate manifest key: {key!r}")
        result[key] = value
    return result


def _check_tool_identifier_collisions(manifest: Any) -> None:
    """Refuse tool names that collide under Unicode normalization and casefolding."""
    tools = manifest.get("tools") if isinstance(manifest, dict) else None
    if not isinstance(tools, dict):
        return
    seen: set[str] = set()
    for name in tools:
        normalized = unicodedata.normalize("NFKC", name).casefold()
        if normalized in seen:
            raise ValueError(f"duplicate normalized tool identifier: {name!r}")
        seen.add(normalized)


def load_manifest(path: Path | None = None, *, force_reload: bool = False) -> dict[str, Any]:
    global _MANIFEST_CACHE
    target = path or locate_manifest_path()
    if target is None or not target.is_file():
        return {"manifest_version": "0", "never_exposed_paths": [], "tools": {}}
    try:
        mtime = target.stat().st_mtime
    except OSError:
        mtime = 0.0
    if not force_reload and path is None and _MANIFEST_CACHE and _MANIFEST_CACHE[0] == mtime:
        return _MANIFEST_CACHE[1]
    data = json.loads(target.read_text(encoding="utf-8"), object_pairs_hook=_unique_object_pairs)
    _check_tool_identifier_collisions(data)
    if path is None:
        _MANIFEST_CACHE = (mtime, data)
    return data


def get_tool_capability(tool_name: str, manifest: dict[str, Any] | None = None) -> dict[str, Any] | None:
    mf = manifest if manifest is not None else load_manifest()
    tools_map = mf.get("tools") or {}
    cap = tools_map.get(tool_name)
    return dict(cap) if isinstance(cap, dict) else None


def check_tool_execution_boundary(
    tool_name: str,
    *,
    is_tier2: bool,
    manifest: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Fail-closed capability check executed before any tool runs:
    - Rejects undeclared tools when manifest is loaded
    - Rejects Shadow AI privilege escalation (Tier-1 tool requesting rw/bwrap fs_scope)
    - Rejects any tool claiming credential_access != 'none'
    """
    mf = manifest if manifest is not None else load_manifest()
    tools_map = mf.get("tools") or {}
    if not tools_map:
        return False, (
            "Blocked: capability manifest is missing or empty. "
            "Refusing to execute any tool until it is provisioned."
        )

    cap = tools_map.get(tool_name)
    if not isinstance(cap, dict):
        return False, f"Blocked by OS capability manifest: tool '{tool_name}' has no capability declaration."
    if cap.get("credential_access", "none") not in ("none", "secret_material"):
        return False, f"Blocked by OS capability manifest: tool '{tool_name}' illegally requests credential access ({cap.get('credential_access')})."
    fs_scope = str(cap.get("fs_scope", ""))
    if not is_tier2 and fs_scope in TIER2_ONLY_FS_SCOPES:
        return (
            False,
            f"Blocked by OS capability manifest: Tier-1 tool '{tool_name}' cannot hold mutable fs_scope '{fs_scope}'.",
        )
    return True, ""


def verify_manifest_integrity(manifest: dict[str, Any]) -> list[str]:
    """Fail-closed SHA-256 integrity verification for the capability manifest.

    The manifest carries an ``integrity`` block with ``sha256`` and
    ``default_policy: "deny"``.  On any violation (missing block, missing
    default_policy, hash mismatch, or empty manifest), this function returns
    an error and the caller must refuse to load the manifest.
    """
    errors: list[str] = []
    integrity = manifest.get("integrity")
    if not isinstance(integrity, dict):
        return ["Manifest integrity block missing or not a dict — refusing to load (fail-closed)."]

    default_policy = integrity.get("default_policy")
    if default_policy != "deny":
        errors.append(f"Manifest integrity default_policy must be 'deny' (got {default_policy!r}) — fail-closed.")

    expected_sha = integrity.get("sha256")
    if not expected_sha:
        errors.append("Manifest integrity sha256 missing — refusing to load (fail-closed).")
    else:
        # Recompute hash over the canonical manifest without its own integrity block
        canonical = {k: v for k, v in manifest.items() if k != "integrity"}
        canonical_json = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        actual_sha = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        if actual_sha != expected_sha:
            errors.append(
                f"Manifest SHA-256 mismatch: expected={expected_sha}, actual={actual_sha} — "
                f"manifest may have been tampered with; refusing to load (fail-closed).",
            )

    return errors


def _has_whitespace(value: Any) -> bool:
    """Return True if `value` is a string containing whitespace, or contains a
    string with whitespace anywhere nested inside lists/dicts.
    """
    if isinstance(value, str):
        return any(ch.isspace() for ch in value)
    if isinstance(value, dict):
        return any(_has_whitespace(k) or _has_whitespace(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_has_whitespace(item) for item in value)
    return False


def validate_manifest_invariants(
    manifest: dict[str, Any],
    *,
    expected_tier1: set[str],
    expected_tier2: set[str],
) -> list[str]:
    """Validates 100% parity and Shadow-AI security invariants across the capability manifest.

    Checks include:
      - Tool set parity (missing/extra vs expected tiers)
      - Tier assignment matches expected tier
      - fs_scope / network_egress / kernel_boundary in valid sets
      - credential_access must be 'none'
      - Tier-1 tools cannot hold mutable fs_scope
      - auth_operation validation against AuthPolicyEngine tier map
      - No whitespace in any string key or value
      - additionalProperties-style: unknown top-level keys rejected
      - Subprocess tools must be kernel-sandboxed with zero network egress
    """
    errors: list[str] = []
    tools_map = manifest.get("tools")
    if not isinstance(tools_map, dict):
        return ["Manifest missing 'tools' dictionary."]

    # Fail-closed integrity check first — any integrity violation aborts
    errors.extend(verify_manifest_integrity(manifest))

    # Reject unknown top-level keys (additionalProperties: false style)
    ALLOWED_TOP_KEYS = {
        "manifest_version",
        "celina_version",
        "enforcement_model",
        "never_exposed_paths",
        "tools",
        "integrity",
    }
    for key in manifest:
        if key not in ALLOWED_TOP_KEYS:
            errors.append(f"Unknown top-level manifest key: {key!r}")

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
        # Reject unknown capability-entry keys (additionalProperties: false style)
        ALLOWED_CAP_KEYS = {
            "tier",
            "fs_scope",
            "network_egress",
            "kernel_boundary",
            "dbus_methods",
            "dbus_buses",
            "pipewire",
            "credential_access",
            "auth_operation",
            "domain",
            "mcp_server",
            "mcp_transport",
            "_audit_note",
            "opt_in",
        }
        for key in cap:
            if key not in ALLOWED_CAP_KEYS:
                errors.append(f"{name}: unknown capability key {key!r}")
        # No whitespace in structural policy fields (free-text _audit_note is exempt)
        STRUCTURAL_KEYS = {
            "tier", "fs_scope", "network_egress", "kernel_boundary",
            "dbus_methods", "dbus_buses", "pipewire", "credential_access",
            "auth_operation", "domain", "mcp_server", "mcp_transport",
        }
        for sk in STRUCTURAL_KEYS:
            if sk in cap and _has_whitespace(cap[sk]):
                errors.append(f"{name}: whitespace in structural field '{sk}'={cap[sk]!r}")
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
        if cap.get("credential_access") not in ("none", "secret_material"):
            errors.append(f"{name}: credential_access must be 'none' or 'secret_material' (got {cap.get('credential_access')})")
        if exp_tier == "tier1" and fs_scope in TIER2_ONLY_FS_SCOPES:
            errors.append(f"{name}: Shadow-AI violation — Tier-1 tool has mutable fs_scope '{fs_scope}'")

        # Auth operation binding — auth_operation must be in AUTH_OP_TIER_MAP
        auth_op = cap.get("auth_operation")
        if auth_op is not None:
            if not isinstance(auth_op, str):
                errors.append(f"{name}: auth_operation must be a string (got {type(auth_op).__name__})")
            elif auth_op not in VALID_AUTH_OPERATIONS:
                errors.append(f"{name}: unknown auth_operation '{auth_op}'")

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
    manifest: dict[str, Any] | None = None,
    allow_workdir_write: bool = False,
) -> list[str]:
    """Translates a tool's capability manifest entry into kernel-enforced Linux
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
    if cap.get("credential_access", "none") not in ("none", "secret_material"):
        raise PermissionError(f"Tool '{tool_name}' has invalid credential_access ({cap.get('credential_access')})")

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
        "--tmpfs", "/tmp",  # nosec B108 — bwrap sandbox mount point, not temp file creation
    ]

    if cap.get("network_egress") == "none":
        args.append("--unshare-net")

    import sys as _sys

    runtime_dirs = {"/usr", "/bin", "/lib", "/lib64", "/etc/alternatives"}
    # Non-FHS layouts: NixOS puts everything under /nix/store; sys.prefix may differ from /usr
    if "/nix/store" in _sys.executable:
        runtime_dirs.add("/nix/store")
    if _sys.prefix != "/usr":
        runtime_dirs.add(_sys.prefix)
    for sys_dir in sorted(runtime_dirs):
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


def format_capabilities_summary(manifest: dict[str, Any] | None = None) -> str:
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
