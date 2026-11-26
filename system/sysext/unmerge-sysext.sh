#!/usr/bin/env bash
# ============================================================================
# macubuntu systemd-sysext unmerge utility
# Unmerges the macubuntu read-only OverlayFS extension image, instantly
# reverting the OS to stock Ubuntu without reboot or filesystem rollback.
#
# Usage: sudo bash system/sysext/unmerge-sysext.sh [--dry-run]
# ============================================================================
set -Eeuo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SYSEXT_NAME="macubuntu"
SYSEXT_DST="/usr/lib/extensions/${SYSEXT_NAME}.raw"

DRY_RUN="${DRY_RUN:-0}"

step() { echo "  🔧 $*"; }

if [[ "${DRY_RUN}" == "1" ]]; then
    echo "=== macubuntu systemd-sysext Unmerge (DRY-RUN) ==="
    echo "  [dry-run] would run: systemd-sysext unmerge ${SYSEXT_NAME}"
    echo "  [dry-run] would remove ${SYSEXT_DST}"
    echo "  [dry-run] would run: systemctl daemon-reload"
    exit 0
fi

echo "=== macubuntu systemd-sysext Unmerge ==="

# Guardrail: systemd-sysext must be available
if ! command -v systemd-sysext >/dev/null 2>&1; then
    echo "  ❌ systemd-sysext not found."
    exit 1
fi

step "Unmerging sysext from running OS (systemd-sysext unmerge ${SYSEXT_NAME})..."
if [[ "${DRY_RUN}" != "1" ]]; then
    sudo systemd-sysext unmerge "${SYSEXT_NAME}" 2>&1 || {
        echo "  ⚠️  systemd-sysext unmerge failed — check journalctl for details"
        exit 1
    }
    echo "  ✅ Sysext unmerged — macubuntu layer removed from running OS"
fi

step "Cleaning up staged sysext image..."
if [[ "${DRY_RUN}" != "1" ]]; then
    sudo rm -rf "${SYSEXT_DST}" 2>/dev/null || true
    echo "  ✅ Staged sysext image removed"
fi

step "Reloading systemd..."
if [[ "${DRY_RUN}" != "1" ]]; then
    sudo systemctl daemon-reload 2>/dev/null || true
    echo "  ✅ systemd reloaded"
fi

echo ""
echo "  🍎 macubuntu sysext unmerged successfully."
echo "     The OS is now in stock Ubuntu state (macubuntu layer removed)."
echo "     To re-merge: sudo bash system/sysext/merge-sysext.sh"
