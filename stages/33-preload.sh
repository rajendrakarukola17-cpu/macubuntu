#!/usr/bin/env bash
# ============================================================================
# Stage 33: Predictive App Preloading (preload) — macOS-Style Instant Launch
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[33] Predictive App Preloading (preload)"

if ! is_true "$ENABLE_PRELOAD"; then
    echo "    ℹ️  Preload skipped (ENABLE_PRELOAD=false)"
    echo "      Enable with: ENABLE_PRELOAD=true ./install.sh --only 33"
    exit 0
fi

# --- NVMe-aware preload gating (hardware.json driven) ---
# On NVMe drives, preload adds unnecessary background I/O (NVMe random reads are fast enough).
# Enable preload only on SATA SSDs or rotational disks where cold-start latency matters.
SKIP_PRELOAD="false"
if [[ -f /etc/macubuntu/hardware.json ]] && command -v jq >/dev/null 2>&1; then
    HW_DISK_TRAN="$(jq -r '.disk_transport' /etc/macubuntu/hardware.json)"
    HW_DISK_ROTA="$(jq -r '.disk_rotational' /etc/macubuntu/hardware.json)"
else
    # Fallback: probe directly
    ROOT_SRC="$(findmnt -no SOURCE / 2>/dev/null | sed 's/\[.*\]//' || echo "")"
    ROOT_DISK="$(lsblk -ndo PKNAME "$ROOT_SRC" 2>/dev/null | head -n1 || echo "")"
    ROOT_DISK="${ROOT_DISK:-$(basename "$ROOT_SRC" 2>/dev/null || echo root)}"
    HW_DISK_TRAN="$(lsblk -ndo TRAN "/dev/${ROOT_DISK}" 2>/dev/null || echo "unknown")"
    HW_DISK_ROTA="$(cat "/sys/block/${ROOT_DISK}/queue/rotational" 2>/dev/null || echo 1)"
fi

if [[ "$HW_DISK_TRAN" == "nvme" ]]; then
    SKIP_PRELOAD="true"
    echo "    ℹ️  NVMe detected — skipping preload (NVMe random reads are fast enough; preload wastes RAM/write endurance)"
elif [[ "$HW_DISK_ROTA" == "1" ]]; then
    echo "    ℹ️  Rotational disk detected — preload will help cold-start latency"
elif [[ "$HW_DISK_TRAN" == "sata" || "$HW_DISK_TRAN" == "usb" ]]; then
    echo "    ℹ️  SATA/USB disk detected — enabling preload for faster app launches"
else
    echo "    ℹ️  Unknown disk transport ($HW_DISK_TRAN) — enabling preload (conservative default)"
fi

if is_true "$SKIP_PRELOAD"; then
    echo "    ℹ️  Preload skipped (NVMe — not beneficial)"
    exit 0
fi

step "Installing preload (monitors app usage, pre-caches binaries into free RAM)..."

if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] would apt install -y preload"
    echo "      [dry-run] would systemctl enable --now preload"
    echo "      [dry-run] would record manual: tune /etc/preload.conf if desired"
    exit 0
fi

if apt_has preload; then
    apt_install preload || record_fail "apt: preload"
    systemctl_enable preload || record_fail "systemctl enable preload"
    echo "      ✅ preload installed and enabled — apps will launch faster over time"
    record_manual "Optional: tune /etc/preload.conf (model, mappath, etc.) for your workload"
else
    record_manual "preload not in APT; add pinned package to download-locks.sha256"
fi
