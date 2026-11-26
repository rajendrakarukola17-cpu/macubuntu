#!/usr/bin/env bash
# ============================================================================
# macubuntu — Secure Boot MOK Enrollment Helper (v4.4)
#
# Ubuntu 26.04 ships with UEFI Secure Boot enabled by default.
# 12 of 13 macubuntu stages load ZERO unsigned kernel modules and are 100%
# Secure-Boot-safe out of the box. ONLY Stage 40's optional `v4l2loopback-dkms`
# (iPhone Continuity Camera -> /dev/video10) requires enrolling Ubuntu's local
# DKMS Machine Owner Key (MOK) — you NEVER need to disable Secure Boot or TPM.
#
# Usage:
#   ./tools/enroll-mok.sh --dry-run    # Inspect Secure Boot & MOK state (0 writes)
#   ./tools/enroll-mok.sh              # Import Ubuntu DKMS MOK via mokutil
#   ./tools/enroll-mok.sh --verify     # Post-reboot verification + modprobe test
# ============================================================================
set -Eeuo pipefail
umask 077

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../core/detect.sh
source "$REPO_DIR/core/detect.sh"

DRY_RUN=0
VERIFY_ONLY=0

usage() {
    cat <<'EOF'
Usage: ./tools/enroll-mok.sh [options]

Options:
  --dry-run   Check Secure Boot & DKMS MOK enrollment status with zero changes
  --verify    Post-reboot check: verify MOK is enrolled and v4l2loopback loads
  -h, --help  Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)  DRY_RUN=1; shift ;;
        --verify)   VERIFY_ONLY=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          echo "❌ Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

echo "================================================================"
echo "  🔐 macubuntu Secure Boot & MOK Enrollment Helper"
echo "================================================================"

if detect_secure_boot_enabled; then
    SB_STATE="enabled"
    echo "  Secure Boot: ENABLED (Ubuntu 26.04 default)"
else
    SB_STATE="disabled"
    echo "  Secure Boot: DISABLED / Legacy BIOS (no MOK enrollment needed)"
fi

MOK_DER="$(detect_mok_der_path 2>/dev/null || true)"
if [[ -n "$MOK_DER" ]]; then
    echo "  DKMS MOK:    $MOK_DER"
else
    echo "  DKMS MOK:    not generated yet (created automatically when v4l2loopback-dkms installs in Stage 40)"
fi

if [[ "$VERIFY_ONLY" -eq 1 ]]; then
    echo ""
    echo "=== Post-Reboot Secure Boot & v4l2loopback Verification ==="
    if [[ "$SB_STATE" == "enabled" ]]; then
        if [[ "$DRY_RUN" -eq 1 ]]; then
            if [[ -n "$MOK_DER" ]]; then
                echo "  [dry-run] would verify: detect_mok_enrolled $MOK_DER"
            else
                echo "  [dry-run] would verify: MOK enrollment (MOK_DER not found)"
            fi
        elif [[ -n "$MOK_DER" ]] && detect_mok_enrolled "$MOK_DER"; then
            echo "  ✅ DKMS MOK ($MOK_DER) is ENROLLED in UEFI firmware."
        else
            echo "  ❌ DKMS MOK is NOT enrolled yet. Run: ./tools/enroll-mok.sh and reboot."
            exit 1
        fi
    else
        echo "  ✅ Secure Boot is disabled; unsigned DKMS modules are permitted."
    fi

    if [[ -e /dev/video10 ]] || lsmod 2>/dev/null | grep -q '^v4l2loopback'; then
        echo "  ✅ v4l2loopback kernel module is loaded (/dev/video10 ready for Continuity Camera)."
        exit 0
    fi
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "  [dry-run] would test: sudo modprobe v4l2loopback video_nr=10 card_label=\"iPhone Continuity Camera\" exclusive_caps=1 max_buffers=2"
        exit 0
    fi
    if command -v modprobe >/dev/null 2>&1; then
        err_out="$(sudo modprobe v4l2loopback video_nr=10 card_label="iPhone Continuity Camera" exclusive_caps=1 max_buffers=2 2>&1 || true)"
        if [[ -e /dev/video10 ]] || lsmod 2>/dev/null | grep -q '^v4l2loopback'; then
            echo "  ✅ v4l2loopback loaded cleanly after MOK enrollment (/dev/video10 ready)."
            exit 0
        fi
        echo "  ❌ v4l2loopback failed to load: ${err_out:-unknown error}" >&2
        exit 1
    fi
    echo "  ⚠️  modprobe not available in this environment."
    exit 1
fi

if [[ "$SB_STATE" != "enabled" ]]; then
    echo ""
    echo "✅ Nothing to enroll — Secure Boot is not active on this host."
    exit 0
fi

if [[ -n "$MOK_DER" ]] && detect_mok_enrolled "$MOK_DER"; then
    echo ""
    echo "✅ Ubuntu DKMS MOK ($MOK_DER) is ALREADY enrolled in UEFI firmware."
    echo "   Stage 40 (v4l2loopback-dkms) and all other 12 stages are 100% Secure-Boot-ready!"
    exit 0
fi

echo ""
echo "ℹ️  Why this is needed:"
echo "   • 12 of 13 macubuntu stages load ZERO unsigned kernel modules."
echo "   • Only Stage 40's v4l2loopback-dkms (iPhone Continuity Camera) builds a local DKMS module."
echo "   • Tip: If Ubuntu provides a pre-signed 'linux-modules-v4l2loopback-oem-*' package for your"
echo "     kernel ('apt-cache search linux-modules-v4l2loopback'), no manual MOK enrollment is needed."
echo "   • Otherwise, enrolling Ubuntu's DKMS MOK keeps Secure Boot + TPM 2.0 + LUKS2 100% ENABLED."

if [[ -z "$MOK_DER" ]]; then
    echo ""
    echo "⚠️  DKMS MOK key not found yet. Install v4l2loopback-dkms first (or run Stage 40):"
    echo "   sudo apt-get install -y v4l2loopback-dkms mokutil"
    echo "   Then re-run: ./tools/enroll-mok.sh"
    [[ "$DRY_RUN" -eq 1 ]] && exit 0
    exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo ""
    echo "  [dry-run] would run: sudo mokutil --import \"$MOK_DER\""
    echo "  [dry-run] after reboot, select: Enroll MOK -> Continue -> Yes -> enter one-time password -> Reboot"
    echo "  [dry-run] then verify with: ./tools/enroll-mok.sh --verify"
    exit 0
fi

if ! command -v mokutil >/dev/null 2>&1; then
    echo "❌ 'mokutil' is not installed. Install it first: sudo apt-get install -y mokutil" >&2
    exit 1
fi

echo ""
echo "Step 1: Queuing DKMS MOK ($MOK_DER) for UEFI firmware enrollment..."
echo "        You will be asked to choose a short one-time enrollment password."
sudo mokutil --import "$MOK_DER"

cat <<'EOF'

================================================================
  ✅ MOK Enrollment Queued! Complete these 4 steps on next boot:
================================================================
  1. Reboot your laptop.
  2. On the blue UEFI MOK Management screen, press any key within 10s.
  3. Select:  Enroll MOK  ->  Continue  ->  Yes  ->  enter your one-time password.
  4. Select:  Reboot.

  After logging back into Ubuntu, confirm with:
      ./tools/enroll-mok.sh --verify

  If a manually compiled module still needs signing after enrollment:
      sudo /usr/lib/linux-kbuild-$(uname -r | cut -d. -f1-2)/scripts/sign-file \
        sha256 /var/lib/shim-signed/mok/MOK.priv /var/lib/shim-signed/mok/MOK.der \
        "$(modinfo -n v4l2loopback)"
================================================================
EOF
