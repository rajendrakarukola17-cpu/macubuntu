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

[[ "$UBUNTU_VER" != "26.04" ]] && echo "    ⚠️  Notice: Tuned for Ubuntu 26.04 LTS; continuing safely on $UBUNTU_VER."
(( RAM_GB < 8 )) && echo "    ⚠️  Notice: Under 8GB RAM — lightweight search + ZRAM + Celina lazy boot strongly recommended."
(( DISK_FREE_GB < 20 )) && echo "    ⚠️  Notice: Under 20GB free disk — Flatpaks, Ollama models, and backups need headroom."

# Platform Edge Case #8: X11 session notice (Ubuntu 26.04 Wayland preferred; NVIDIA X11 fallback notes)
if [[ "$SESSION_TYPE" == "x11" ]]; then
    echo "    ⚠️  X11 Session Detected: Wayland-specific tools (Portal.RemoteDesktop, wl-clipboard,"
    echo "       Wayland keyd app-mapper) require a Wayland session (NVIDIA: enable nvidia-drm.modeset=1)."
    echo "       Theme, dock, ZRAM, audio & Celina CLI remain safe; switch to Ubuntu on Wayland for full parity."
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
if is_true "$ENABLE_CELINA" && command -v python3 >/dev/null 2>&1; then
    step "Verifying hard 7-way Celina tool-security & OS-capability parity gate..."
    GATE_TMP_HOME="$(mktemp -d)"
    gate_rc=0
    HOME="$GATE_TMP_HOME" XDG_CONFIG_HOME="$GATE_TMP_HOME/.config" XDG_DATA_HOME="$GATE_TMP_HOME/.local/share" \
    PYTHONDONTWRITEBYTECODE=1 REPO_DIR="$REPO_DIR" python3 - <<'PY' || gate_rc=$?
import json
import os
import sys
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

if errors:
    for e in errors:
        print(f"    ❌ PARITY GATE FAILURE: {e}", file=sys.stderr)
    sys.exit(1)

print(
    f"    ✅ Hard 7-way parity gate PASSED: {len(schema_names)} tools across {len(disk['tool_domains'])} domains "
    f"({len(disk_t1)} Tier-1 auto, {len(disk_t2)} Tier-2 approval; OS capability manifest verified, 0 Shadow-AI violations)."
)
PY
    rm -rf "$GATE_TMP_HOME"
    if [[ "$gate_rc" -ne 0 ]]; then
        exit "$gate_rc"
    fi
fi
