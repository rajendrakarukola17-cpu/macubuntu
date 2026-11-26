#!/usr/bin/env bash
# ============================================================================
# Stage 00: Pre-flight System Introspection & Safety Checks
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[00] Pre-flight System Checks"

UBUNTU_VER="$(detect_ubuntu_version)"
RAM_GB="$(detect_ram_gb)"
DISK_FREE_GB="$(detect_disk_free_gb)"
SESSION_TYPE="$(detect_session_type)"

echo "    Profile:      $PROFILE"
echo "    Mode:         DRY_RUN=$DRY_RUN | Built-in Celina=$ENABLE_CELINA (${CELINA_NAME}/${CELINA_NICKNAME})"
echo "    Desktop:      DOCK=$DOCK_CHOICE | LAUNCHER=$LAUNCHER_CHOICE | SEARCH=$SEARCH_BACKEND | POWER=$POWER_BACKEND"
echo "    System:       Ubuntu $UBUNTU_VER | Session=$SESSION_TYPE | RAM ~${RAM_GB}GB | Free Disk ${DISK_FREE_GB}GB"
if is_true "$ENABLE_CELINA" && command -v python3 >/dev/null 2>&1; then
    step "Verifying hard 7-way Celina tool-security & OS-capability parity gate..."
    GATE_TMP_HOME="$(mktemp -d)"
    gate_rc=0
    # P1: Cleanup trap for GATE_TMP_HOME on EXIT/INT/TERM
    cleanup_gate_tmp() {
        rm -rf "$GATE_TMP_HOME" 2>/dev/null || true
    }
    trap cleanup_gate_tmp EXIT INT TERM
    HOME="$GATE_TMP_HOME" XDG_CONFIG_HOME="$GATE_TMP_HOME/.config" XDG_DATA_HOME="$GATE_TMP_HOME/.local/share" \
    PYTHONDONTWRITEBYTECODE=1 REPO_DIR="$REPO_DIR" python3 - <<'PY' || gate_rc=$?
import hashlib
import json
import os
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

repo = Path(os.environ["REPO_DIR"])
celina_root = repo / "features" / "celina"
sys.path.insert(0, str(celina_root))

from celina_pkg import approval, capabilities, config, tools  # noqa: E402

schema_file = celina_root / "config" / "neural-schema.json"
disk = json.loads(schema_file.read_text(encoding="utf-8"))
tp = disk["security_policy"]

schema_names = {t["function"]["name"] for t in tools.TOOL_SCHEMAS}
impl_names = set(tools._AUTO_TOOL_MAP) | set(tools._APPROVAL_TOOL_MAP) | {"run_skill", "autonomous_gui_task"}
cfg_names = config.TIER1_AUTO_TOOLS | config.TIER2_APPROVAL_TOOLS
disk_t1 = set(tp["tier1_auto_tools"])
disk_t2 = set(tp["tier2_approval_tools"])
disk_names = disk_t1 | disk_t2
dom_names = {n for d in disk["tool_domains"].values() for n in d.get("tools", [])}
risk_names = set(approval.TOOL_RISK_MAP.keys())

errors = []
for label, candidate in (
    ("handlers", impl_names),
    ("config.TIER1|TIER2", cfg_names),
    ("neural-schema.json", disk_names),
    ("tool_domains", dom_names),
):
    missing = sorted(schema_names - candidate)
    extra = sorted(candidate - schema_names)
    if missing or extra:
        errors.append(f"{label} mismatch: missing={missing}, extra={extra}")

missing_risk = sorted(schema_names - risk_names)
if missing_risk:
    errors.append(f"approval.TOOL_RISK_MAP missing tools: {missing_risk}")

# 7th check: OS-level capability manifest parity & Shadow-AI anti-bypass invariants
cap_path = repo / "system" / "capabilities" / "celina-capabilities.json"
cap_manifest = {}
if not cap_path.is_file():
    errors.append(f"Missing OS capability manifest: {cap_path}")
else:
    cap_manifest = capabilities.load_manifest(cap_path, force_reload=True)
    errors.extend(
        capabilities.validate_manifest_invariants(
            cap_manifest, expected_tier1=disk_t1, expected_tier2=disk_t2
        )
    )

# Every files_office mutation tool MUST remain Tier-2 approval-gated
required_tier2_file_mutations = {
    "write_file",
    "edit_file",
    "move_file",
    "trash_file",
    "create_document",
    "edit_document",
    "convert_document",
    "libreoffice_edit",
}
not_t2_disk = sorted(required_tier2_file_mutations - disk_t2)
not_t2_cfg = sorted(required_tier2_file_mutations - config.TIER2_APPROVAL_TOOLS)
if not_t2_disk or not_t2_cfg:
    errors.append(
        f"files_office mutation tools MUST be Tier-2! disk_missing={not_t2_disk}, cfg_missing={not_t2_cfg}"
    )

# ============================================================================
# D5 — Additional parity & integrity checks
# ============================================================================

# 8. Manifest copies byte-identical
cap_manifest_b = celina_root / "config" / "celina-capabilities.json"
if cap_manifest_b.is_file():
    if cap_path.read_bytes() != cap_manifest_b.read_bytes():
        errors.append("Manifest copies diverge: system/capabilities/celina-capabilities.json != features/celina/config/celina-capabilities.json")
else:
    errors.append(f"Missing second manifest copy: {cap_manifest_b}")

# Tool count parity: manifest tier lists must match manifest tool count
manifest_tools = set(cap_manifest.get("tools", {}).keys()) if isinstance(cap_manifest.get("tools"), dict) else set()
manifest_count = len(manifest_tools)
schema_tier_count = len(disk_t1 | disk_t2)
if manifest_count != schema_tier_count:
    errors.append(f"Tool count parity: manifest has {manifest_count} tools, schema tier1+tier2 has {schema_tier_count}")

# 9. Tier string parity: no trailing spaces, no overlap, no duplicates
for label, tlist in (("tier1_auto_tools", tp["tier1_auto_tools"]), ("tier2_approval_tools", tp["tier2_approval_tools"])):
    ws_entries = [t for t in tlist if not isinstance(t, str) or t != t.strip()]
    if ws_entries:
        errors.append(f"Tier string parity: {label} has trailing/leading whitespace entries: {ws_entries}")
    dups = [k for k, v in Counter(tlist).items() if v > 1]
    if dups:
        errors.append(f"Tier string parity: {label} has duplicate entries: {dups}")

overlap = disk_t1 & disk_t2
if overlap:
    errors.append(f"Tier string parity: tools in BOTH tier1 and tier2: {sorted(overlap)}")

# Every manifest tool must appear in exactly one tier list
in_tiers = disk_t1 | disk_t2
manifest_not_tier = manifest_tools - in_tiers
tier_not_manifest = in_tiers - manifest_tools
if manifest_not_tier:
    errors.append(f"Tier string parity: manifest tools not in any tier: {sorted(manifest_not_tier)}")
if tier_not_manifest:
    errors.append(f"Tier string parity: tier tools not in manifest: {sorted(tier_not_manifest)}")

# 10. fs_scope vocabulary parity: no proc_sys_ro, all values valid
VALID_FS_SCOPES = capabilities.VALID_FS_SCOPES
VALID_NET_SCOPES = capabilities.VALID_NET_SCOPES
for name, cap in sorted(cap_manifest.get("tools", {}).items()):
    fs_scope = cap.get("fs_scope", "")
    if fs_scope == "proc_sys_ro":
        errors.append(f"fs_scope vocabulary: {name} uses removed 'proc_sys_ro' scope")
    if fs_scope not in VALID_FS_SCOPES:
        errors.append(f"fs_scope vocabulary: {name} invalid fs_scope '{fs_scope}'")
    net_scope = cap.get("network_egress", "")
    if net_scope not in VALID_NET_SCOPES:
        errors.append(f"fs_scope vocabulary: {name} invalid network_egress '{net_scope}'")

# 11. auth_operation coverage: every tool has valid auth_operation
VALID_AUTH_OPERATIONS = capabilities.VALID_AUTH_OPERATIONS
for name, cap in sorted(cap_manifest.get("tools", {}).items()):
    auth_op = cap.get("auth_operation")
    if auth_op is None:
        errors.append(f"auth_operation coverage: {name} missing auth_operation")
    elif auth_op not in VALID_AUTH_OPERATIONS:
        errors.append(f"auth_operation coverage: {name} unknown auth_operation '{auth_op}'")

# 12. Integrity hash verification
integrity = cap_manifest.get("integrity")
if not isinstance(integrity, dict):
    errors.append("Integrity hash: manifest integrity block missing")
else:
    expected_sha = integrity.get("sha256", "")
    canonical = {k: v for k, v in cap_manifest.items() if k != "integrity"}
    canonical_json = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    actual_sha = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    if actual_sha != expected_sha:
        errors.append(f"Integrity hash: SHA-256 mismatch (expected={expected_sha}, actual={actual_sha})")
    if integrity.get("default_policy") != "deny":
        errors.append(f"Integrity hash: default_policy must be 'deny' (got {integrity.get('default_policy')!r})")

# 13. Whitespace integrity in structural fields
STRUCTURAL_KEYS = {"tier", "fs_scope", "network_egress", "kernel_boundary", "credential_access", "auth_operation", "domain", "mcp_server", "mcp_transport"}
def _has_ws(v):
    if isinstance(v, str):
        return v != v.strip()
    return False
for name, cap in sorted(cap_manifest.get("tools", {}).items()):
    for sk in STRUCTURAL_KEYS:
        if sk in cap and _has_ws(cap[sk]):
            errors.append(f"Whitespace integrity: {name} field '{sk}' has leading/trailing whitespace: {cap[sk]!r}")

# 14. Version consistency: version.toml vs manifest vs schema
version_toml = repo / "version.toml"
if version_toml.is_file() and cap_manifest:
    vdata = tomllib.loads(version_toml.read_text(encoding="utf-8"))
    if vdata.get("celina", {}).get("version") != str(cap_manifest.get("celina_version", "")):
        errors.append("Version mismatch: version.toml celina != manifest celina_version")
    if vdata.get("manifest", {}).get("version") != str(cap_manifest.get("manifest_version", "")):
        errors.append("Version mismatch: version.toml manifest != manifest manifest_version")
    if vdata.get("schema", {}).get("version") != str(disk.get("schema_version", "")):
        errors.append("Version mismatch: version.toml schema != neural-schema schema_version")

if errors:
    for e in errors:
        print(f"    ❌ PARITY GATE FAILURE: {e}", file=sys.stderr)
    sys.exit(1)

print(
    f"    ✅ Hard 7-way parity gate PASSED: {len(schema_names)} tools across {len(disk['tool_domains'])} domains "
    f"({len(disk_t1)} Tier-1 auto, {len(disk_t2)} Tier-2 approval; OS capability manifest verified, 0 Shadow-AI violations)."
)
PY
    if [[ "$gate_rc" -ne 0 ]]; then
        exit "$gate_rc"
    fi

# --- Shared Hardware Contract: probe once, write /etc/macubuntu/hardware.json ---
step "Probing hardware contract (/etc/macubuntu/hardware.json)..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] would install lib/hw_detect.sh -> /usr/lib/macubuntu/lib/hw_detect.sh"
    echo "      [dry-run] would install systemd oneshot macubuntu-hw-probe.service"
    echo "      [dry-run] would run hw_detect.sh to write hardware.json"
else
    # Install probe library + systemd oneshot for boot-time re-probing
    run sudo install -d -m 755 /usr/lib/macubuntu/lib
    run sudo install -m 755 "$REPO_DIR/lib/hw_detect.sh" /usr/lib/macubuntu/lib/hw_detect.sh
    run sudo install -d -m 755 /etc/systemd/system
    run sudo install -m 644 "$REPO_DIR/system/systemd/macubuntu-hw-probe.service" /etc/systemd/system/macubuntu-hw-probe.service
    run sudo systemctl daemon-reload 2>/dev/null || true
    run sudo systemctl enable macubuntu-hw-probe.service 2>/dev/null || true

    # Run probe now so all subsequent stages can read hardware.json
    run sudo bash /usr/lib/macubuntu/lib/hw_detect.sh

    # Export hardware values for use in this and subsequent stages
    if command -v jq >/dev/null 2>&1 && [[ -f /etc/macubuntu/hardware.json ]]; then
        export HW_TIER="$(jq -r '.tier' /etc/macubuntu/hardware.json)"
        export HW_RAM_MB="$(jq -r '.ram_mb' /etc/macubuntu/hardware.json)"
        export HW_CPU_CORES="$(jq -r '.cpu_cores' /etc/macubuntu/hardware.json)"
        export HW_AVX2="$(jq -r '.avx2' /etc/macubuntu/hardware.json)"
        export HW_HAS_BATTERY="$(jq -r '.has_battery' /etc/macubuntu/hardware.json)"
        export HW_DISK_TRAN="$(jq -r '.disk_transport' /etc/macubuntu/hardware.json)"
        export HW_DISK_ROTA="$(jq -r '.disk_rotational' /etc/macubuntu/hardware.json)"
        export HW_GPU_VENDOR="$(jq -r '.gpu_vendor' /etc/macubuntu/hardware.json)"
        export HW_VRAM_MB="$(jq -r '.vram_mb' /etc/macubuntu/hardware.json)"
        echo "    📋 Hardware: tier=$HW_TIER ram=${HW_RAM_MB}MB cpu=$HW_CPU_CORES gpu=$HW_GPU_VENDOR vram=${HW_VRAM_MB}MB disk=$HW_DISK_TRAN battery=$HW_HAS_BATTERY avx2=$HW_AVX2"
    else
        echo "    ⚠️  jq not available or hardware.json missing — hardware-adaptive tuning disabled"
    fi
fi

# --- systemd-sysext: merge macubuntu as read-only OverlayFS layer ---
# Instead of mutating /usr and /etc in place, all macubuntu system files are
# staged into system/sysext/macubuntu.raw/ and merged via systemd-sysext.
# This provides macOS Sealed System Volume equivalence: instant unmerge
# reverts to stock Ubuntu without reboot or filesystem rollback.
if command -v systemd-sysext >/dev/null 2>&1; then
    step "Staging macubuntu systemd-sysext image..."
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would stage files into system/sysext/macubuntu.raw/"
        echo "      [dry-run] would run: systemd-sysext merge macubuntu"
    else
        # Stage /usr files into a runtime sysext image under $WORKDIR (never inside $REPO_DIR)
        SYSEXT_ROOT="$WORKDIR/sysext/macubuntu.raw"
        rm -rf "$SYSEXT_ROOT"
        mkdir -p "$SYSEXT_ROOT/usr/lib/macubuntu/lib"
        install -m 755 "$REPO_DIR/lib/hw_detect.sh" "$SYSEXT_ROOT/usr/lib/macubuntu/lib/hw_detect.sh"

        # systemd-sysext only merges /usr and /opt — /etc units go via confext, not sysext
        sudo install -d -m 755 /etc/systemd/system
        sudo install -m 644 "$REPO_DIR/system/systemd/macubuntu-hw-probe.service" /etc/systemd/system/macubuntu-hw-probe.service

        # Merge the sysext into the running OS
        bash "$REPO_DIR/system/sysext/merge-sysext.sh"
        echo "      ✅ macubuntu sysext merged (read-only OverlayFS layer active)"
    fi
else
    echo "    ℹ️  systemd-sysext not available — files installed directly to /usr and /etc"
fi

[[ "$UBUNTU_VER" != "26.04" ]] && echo "    ⚠️  Notice: Tuned for Ubuntu 26.04 LTS; continuing safely on $UBUNTU_VER."
(( RAM_GB < 8 )) && echo "    ⚠️  Notice: Under 8GB RAM — lightweight search + ZRAM + Celina lazy boot strongly recommended."
(( DISK_FREE_GB < 20 )) && echo "    ⚠️  Notice: Under 20GB free disk — Flatpaks, Ollama models, and backups need headroom."

# Platform Edge Case #8: Wayland session verification (Ubuntu 26.04 Wayland preferred)
if [[ "$SESSION_TYPE" == "x11" ]]; then
    echo ""
    echo "    ⚠️  WAYLAND SESSION RECOMMENDED — X11 session detected."
    echo "       macubuntu tunes for Wayland (GPU compositing, fractional scaling, VA-API video,"
    echo "       Chrome/Brave Ozone Wayland, Firefox Wayland flags). X11 will work but you will"
    echo "       miss the Mac-like, lag-free UI smoothness this installer targets."
    echo ""
    echo "    👉  To switch: log out, click the gear icon at the login screen, select"
    echo "        \"Ubuntu\" (Wayland), then log back in and re-run this installer."
    echo ""
    # --- Existing X11 notice (supplementary) ---
    echo "    ℹ️  X11 Session (supplementary): Wayland-specific tools (Portal.RemoteDesktop,"
    echo "       wl-clipboard, Wayland keyd app-mapper) require a Wayland session."
    echo "       (NVIDIA: enable nvidia-drm.modeset=1 in GRUB for Wayland support.)"
    echo "       Theme, dock, ZRAM, audio & Celina CLI remain safe on X11."
fi

# Concurrency / Multi-User #7: Single-user desktop design notice
if command -v who >/dev/null 2>&1; then
    ACTIVE_USERS="$(who 2>/dev/null | awk '{print $1}' | sort -u | wc -l | tr -d ' ')"
    if [[ "${ACTIVE_USERS:-0}" -gt 1 ]]; then
        echo "    ⚠️  Multi-user host detected ($ACTIVE_USERS active users): macubuntu is designed for"
        echo "       single-user workstations. Use --system-only (once) + --user-only (per user) to avoid conflicts."
    fi
fi

if detect_has_luks; then
    echo "    🔒 LUKS full-disk encryption detected."
else
    echo "    ⚠️  Notice: No LUKS full-disk encryption detected (check: sudo cryptsetup status /dev/mapper/*)."
fi

# Secure Boot & MOK pre-flight check (Ubuntu 26.04 enables Secure Boot by default)
if detect_secure_boot_enabled; then
    MOK_DER_PRE="$(detect_mok_der_path 2>/dev/null || true)"
    if [[ -n "$MOK_DER_PRE" ]] && detect_mok_enrolled "$MOK_DER_PRE"; then
        echo "    🔐 Secure Boot: ENABLED (DKMS MOK enrolled — all 13 stages ready, including Stage 40 v4l2loopback)."
    else
        echo "    🔐 Secure Boot: ENABLED (12/13 stages are 100% Secure-Boot-safe; NEVER disable Secure Boot or TPM)."
        echo "       ⚠️  Notice: Stage 40 Continuity Camera (v4l2loopback-dkms) requires enrolling Ubuntu's local DKMS MOK."
        echo "          After Stage 40 installs v4l2loopback-dkms, run: ./tools/enroll-mok.sh  (then reboot & run --verify)"
    fi
else
    echo "    🔐 Secure Boot: disabled / legacy BIOS (all 13 stages + v4l2loopback-dkms load directly)."
fi

# Accessibility & Internationalization Locale Audit (AT-SPI2 .mo gap on Ubuntu 24.04/26.04/26.10)
run_locale_audit() {
    local eff_locale="${LC_ALL:-${LC_MESSAGES:-${LANG:-C.UTF-8}}}"
    local base_loc="${eff_locale%%.*}"
    base_loc="${base_loc%%@*}"
    local lang_short="${base_loc%%_*}"
    lang_short="${lang_short,,}"
    local locale_root="${MACUBUNTU_LOCALE_DIR:-/usr/share/locale}"

    echo "    🌐 Locale Audit: active=${eff_locale} (lang=${lang_short:-c})"
    if [[ -z "$lang_short" || "$lang_short" == "c" || "$lang_short" == "posix" || "$lang_short" == "en" ]]; then
        echo "       ✅ English/POSIX locale — AT-SPI2 canonical role names & Piper EN TTS natively supported."
    else
        local has_atspi_mo=0 has_gtk4_mo=0
        for cand in "$base_loc" "$lang_short"; do
            [[ -f "$locale_root/$cand/LC_MESSAGES/at-spi2-core.mo" ]] && has_atspi_mo=1
            [[ -f "$locale_root/$cand/LC_MESSAGES/gtk40.mo" ]] && has_gtk4_mo=1
        done

        if (( has_atspi_mo == 0 )); then
            echo "       ⚠️  AT-SPI2 Localization Gap: missing 'at-spi2-core.mo' for '${base_loc}' in ${locale_root}."
            echo "          (Known Ubuntu 24.04/26.04 packaging gap: Orca & GTK4 localized role names fall back to English.)"
            echo "          Fallback active: Celina wayland_gui uses canonical AT-SPI2 Role enums so GUI automation stays intact."
        else
            echo "       ✅ at-spi2-core.mo translation catalog present for '${base_loc}'."
        fi

        if (( has_gtk4_mo == 0 )); then
            echo "       ⚠️  Notice: GTK 4 catalog (gtk40.mo) not found for '${base_loc}'; some accelerator labels may appear in English."
        fi

        case "$lang_short" in
            te|hi)
                echo "       ✅ Voice TTS: Native offline Piper neural voice available for '${lang_short}' (+ Romanized Tenglish/Hinglish)."
                ;;
            *)
                echo "       ⚠️  Voice TTS: Piper ships pinned neural models for EN/TE/HI; '${lang_short}' falls back to espeak-ng/English (see COMPATIBILITY.md)."
                ;;
        esac
    fi
}
run_locale_audit

if [[ "${LOCALE_AUDIT_ONLY:-0}" == "1" ]]; then
    exit 0
fi

# Hard Pre-Flight Security Gate: 7-way 88-tool parity, Tier-2 files_office invariant & OS capability manifest
# Runs strictly read-only (PYTHONDONTWRITEBYTECODE=1, zero HOME mutations). Aborts install on any drift.
fi
