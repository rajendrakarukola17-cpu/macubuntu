#!/usr/bin/env bash
# ============================================================================
# lib/npu_detect.sh — Passive NPU (Neural Processing Unit) Detection
#
# Detects NPUs passively via /sys/class/accel/*/device/driver — NEVER by
# opening /dev/accel/accel0 directly. Intel intel_vpu on Ubuntu 26.04 caps
# concurrent device contexts at 64 (ivpu_open(): -16); repeated shell probes
# would exhaust contexts and flood journald.
#
# Outputs a single JSON line on stdout:
#   {"npu_vendor":"intel|amd|none","npu_driver":"intel_vpu|amdxdna|none","npu_device":"/dev/accel/accel0|","npu_backend":"openvino|onnxruntime|none"}
#
# Intended to be sourced by lib/hw_detect.sh and Stage 00/35/55.
# Permission configuration (render group + udev rule) is handled separately
# by configure_npu_permissions() — called as root during Stage 35/70.
# ============================================================================
set -euo pipefail

# --- Passive NPU detection via sysfs ---------------------------------------
# Inspect /sys/class/accel/accel*/device/driver symlinks only. No /dev/accel
# file descriptors are opened, so no context slots are consumed.
detect_npu_json() {
    local npu_vendor="none"
    local npu_driver="none"
    local npu_device=""
    local npu_backend="none"

    local accel
    for accel in /sys/class/accel/accel*; do
        [[ -e "$accel" ]] || continue
        local drv_link="${accel}/device/driver"
        if [[ -L "$drv_link" ]]; then
            npu_driver="$(basename "$(readlink -f "$drv_link")")"
            npu_device="/dev/accel/$(basename "$accel")"
            case "$npu_driver" in
                intel_vpu)
                    npu_vendor="intel"
                    if python3 -c "import openvino" 2>/dev/null || [[ -d /opt/intel/openvino ]]; then
                        npu_backend="openvino"
                    fi
                    ;;
                amdxdna)
                    npu_vendor="amd"
                    if python3 -c "import onnxruntime" 2>/dev/null || command -v xrt-smi >/dev/null 2>&1; then
                        npu_backend="onnxruntime"
                    fi
                    ;;
                *)
                    npu_vendor="generic"
                    ;;
            esac
            break
        fi
    done

    # Escape for safe JSON embedding
    local vendor_esc driver_esc device_esc backend_esc
    vendor_esc="$(printf '%s' "$npu_vendor" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    driver_esc="$(printf '%s' "$npu_driver" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    device_esc="$(printf '%s' "$npu_device" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    backend_esc="$(printf '%s' "$npu_backend" | sed 's/\\/\\\\/g; s/"/\\"/g')"

    printf '{"npu_vendor":"%s","npu_driver":"%s","npu_device":"%s","npu_backend":"%s"}\n' \
        "$vendor_esc" "$driver_esc" "$device_esc" "$backend_esc"
}

# --- NPU permission configuration (root-only, Stage 35/70) -----------------
# Adds user to render group and installs udev rule so /dev/accel/accel0 is
# accessible without root. Called as root; no-op when run as non-root.
configure_npu_permissions() {
    local target_user="${1:-${SUDO_USER:-$USER}}"

    # Only configure when an NPU device node actually exists
    if [[ ! -e /dev/accel/accel0 ]]; then
        return 0
    fi

    if [[ "$(id -u)" -ne 0 ]]; then
        echo "⚠️  configure_npu_permissions: must run as root; skipping render-group/udev setup" >&2
        return 0
    fi

    usermod -aG render "$target_user" 2>/dev/null || true

    cat > /etc/udev/rules.d/70-macubuntu-npu.rules <<'EOF'
SUBSYSTEM=="accel", KERNEL=="accel*", GROUP="render", MODE="0660"
EOF
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=accel 2>/dev/null || true
    echo "✅ NPU permissions configured: user=$target_user render group + 70-macubuntu-npu.rules"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    detect_npu_json
fi
