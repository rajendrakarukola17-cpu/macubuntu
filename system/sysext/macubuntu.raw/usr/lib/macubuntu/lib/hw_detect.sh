#!/usr/bin/env bash
# ============================================================================
# lib/hw_detect.sh — macubuntu Hardware Probe Library
# Probes system hardware once and writes /etc/macubuntu/hardware.json
# Used by Stage 00 (pre-flight), macubuntu-hw-probe.service (boot-time),
# and every adaptive stage (20, 25, 32, 33, 35, 46, 50, 70, 99).
# ============================================================================
set -euo pipefail

HW_DIR="/etc/macubuntu"
HW_FILE="${HW_DIR}/hardware.json"

# --- Probe basic hardware ---------------------------------------------------
# RAM in MiB (int)
RAM_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
# CPU cores
CPU_CORES="$(nproc 2>/dev/null || echo 1)"
# AVX2 support
if grep -qw avx2 /proc/cpuinfo 2>/dev/null; then
    HAS_AVX2="true"
else
    HAS_AVX2="false"
fi
# Battery present?
if compgen -G "/sys/class/power_supply/BAT*" >/dev/null 2>&1; then
    HAS_BATTERY="true"
else
    HAS_BATTERY="false"
fi

# --- Root storage transport & rotational flag ------------------------------
ROOT_SRC="$(findmnt -no SOURCE / 2>/dev/null | sed 's/\[.*\]//' || echo "")"
ROOT_DISK=""
if [[ -n "$ROOT_SRC" ]]; then
    ROOT_DISK="$(lsblk -ndo PKNAME "$ROOT_SRC" 2>/dev/null | head -n1 || echo "")"
fi
ROOT_DISK="${ROOT_DISK:-$(basename "$ROOT_SRC" 2>/dev/null || echo root)}"
DISK_TRAN="$(lsblk -ndo TRAN "/dev/${ROOT_DISK}" 2>/dev/null || echo "unknown")"
DISK_ROTA="$(cat "/sys/block/${ROOT_DISK}/queue/rotational" 2>/dev/null || echo 1)"

# --- GPU vendor & dedicated VRAM (MB) -------------------------------------
GPU_VENDOR="cpu"
VRAM_MB=0

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    GPU_VENDOR="nvidia"
    VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -nr | head -n1 || echo 0)"
elif compgen -G "/sys/class/drm/card*/device/mem_info_vram_total" >/dev/null 2>&1; then
    GPU_VENDOR="amd"
    VRAM_BYTES="$(cat /sys/class/drm/card*/device/mem_info_vram_total 2>/dev/null | sort -nr | head -n1 || echo 0)"
    VRAM_MB="$(( VRAM_BYTES / 1024 / 1024 ))"
elif lspci 2>/dev/null | grep -qiE "VGA.*Intel|Display.*Intel|3D.*Intel"; then
    GPU_VENDOR="intel"
fi

# --- Classify System RAM Tier (explicit 8 / 16 / 32 GB bands) ---------------
# Accounts for firmware-reserved RAM: a "16 GB" laptop often reports
# ~15,200 MB in /proc/meminfo, a "32 GB" box ~31,000 MB.
if (( RAM_MB < 12000 )); then
    RAM_TIER="8gb"
elif (( RAM_MB < 24000 )); then
    RAM_TIER="16gb"
else
    RAM_TIER="32gb"
fi

# --- Classify Dedicated GPU AI Tier -----------------------------------------
# A 16 GB RAM + 16 GB VRAM rig can run 32 GB-class models; an iGPU-only
# 16 GB machine cannot. VRAM is the dominant signal for AI capacity.
if (( VRAM_MB >= 20000 )) || (( RAM_MB >= 24000 )); then
    AI_TIER="32gb"
elif (( VRAM_MB >= 10000 )) || (( RAM_MB >= 12000 )); then
    AI_TIER="16gb"
else
    AI_TIER="8gb"
fi

# Backward-compatible legacy tier (lite/balanced/workstation) for consumers
# not yet migrated to ram_tier/ai_tier.
if [[ "$RAM_TIER" == "8gb" ]]; then
    HW_TIER="lite"
elif [[ "$RAM_TIER" == "16gb" ]]; then
    HW_TIER="balanced"
else
    HW_TIER="workstation"
fi

# --- Write hardware.json ---------------------------------------------------
install -d -m 755 "$HW_DIR"

cat > "${HW_FILE}.tmp" <<EOF
{
  "ram_tier": "${RAM_TIER}",
  "ai_tier": "${AI_TIER}",
  "tier": "${HW_TIER}",
  "ram_mb": ${RAM_MB},
  "cpu_cores": ${CPU_CORES},
  "avx2": ${HAS_AVX2},
  "has_battery": ${HAS_BATTERY},
  "disk_transport": "${DISK_TRAN:-unknown}",
  "disk_rotational": ${DISK_ROTA:-1},
  "gpu_vendor": "${GPU_VENDOR}",
  "vram_mb": ${VRAM_MB},
  "probed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
}
EOF

# Atomic install (P5-style: write temp, then move)
install -m 644 "${HW_FILE}.tmp" "$HW_FILE"
rm -f "${HW_FILE}.tmp"

echo "🔧 Hardware profile: tier=${HW_TIER} ram=${RAM_MB}MB cpu=${CPU_CORES} gpu=${GPU_VENDOR} vram=${VRAM_MB}MB disk=${DISK_TRAN:-unknown} rot=${DISK_ROTA:-1} avx2=${HAS_AVX2} battery=${HAS_BATTERY}"
