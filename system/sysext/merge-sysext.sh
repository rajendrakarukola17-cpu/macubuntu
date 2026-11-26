#!/usr/bin/env bash
# ============================================================================
# macubuntu systemd-sysext merge utility
# Merges the macubuntu read-only OverlayFS extension image into the running OS.
# Provides macOS Sealed System Volume equivalent: instant unmerge reverts to
# stock Ubuntu without reboot or filesystem rollback.
#
# Usage: sudo bash system/sysext/merge-sysext.sh [--dry-run]
# ============================================================================
set -Eeuo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SYSEXT_NAME="macubuntu"
SYSEXT_SRC="${REPO_DIR}/system/sysext/${SYSEXT_NAME}.raw"
SYSEXT_DST="/usr/lib/extensions/${SYSEXT_NAME}.raw"

DRY_RUN="${DRY_RUN:-0}"

step() { echo "  🔧 $*"; }

if [[ "${DRY_RUN}" == "1" ]]; then
    echo "=== macubuntu systemd-sysext Merge (DRY-RUN) ==="
    echo "  Source: ${SYSEXT_SRC}"
    echo "  Destination: ${SYSEXT_DST}"
    echo "  [dry-run] would copy sysext image to /usr/lib/extensions/"
    echo "  [dry-run] would run: systemd-sysext merge ${SYSEXT_NAME}"
    echo "  [dry-run] would run: systemctl daemon-reload"
    exit 0
fi

echo "=== macubuntu systemd-sysext Merge ==="

# Guardrail: systemd-sysext must be available
if ! command -v systemd-sysext >/dev/null 2>&1; then
    echo "  ❌ systemd-sysext not found. Install: sudo apt install systemd-sysext"
    exit 1
fi

# Guardrail: sysext source must exist
if [[ ! -d "${SYSEXT_SRC}" ]]; then
    echo "  ❌ Sysext source not found: ${SYSEXT_SRC}"
    echo "     Run stage 00 first to populate the sysext image."
    exit 1
fi

step "Copying sysext image to /usr/lib/extensions/..."
if [[ "${DRY_RUN}" != "1" ]]; then
    sudo install -d -m 755 /usr/lib/extensions
    sudo cp -a "${SYSEXT_SRC}/." "${SYSEXT_DST}/"
    echo "  ✅ Sysext image staged at ${SYSEXT_DST}"
fi

step "Merging sysext into running OS (systemd-sysext merge ${SYSEXT_NAME})..."
if [[ "${DRY_RUN}" != "1" ]]; then
    sudo systemd-sysext merge "${SYSEXT_NAME}" 2>&1 || {
        echo "  ⚠️  systemd-sysext merge failed — check journalctl for details"
        exit 1
    }
    echo "  ✅ Sysext merged — macubuntu layer is now active (read-only OverlayFS)"
fi

step "Reloading systemd..."
if [[ "${DRY_RUN}" != "1" ]]; then
    sudo systemctl daemon-reload 2>/dev/null || true
    echo "  ✅ systemd reloaded"
fi

echo ""
echo "  🍎 macubuntu sysext merged successfully."
echo "     To unmerge: sudo bash system/sysext/unmerge-sysext.sh"
echo "     To verify:  systemd-sysext status"
