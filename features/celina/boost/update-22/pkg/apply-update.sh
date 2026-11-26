#!/usr/bin/env bash
#
# CELINA UPDATE 22 — Structure/Routing Installer
# Applies: _tier0_dispatch() extraction from router.py
# Usage: bash apply-update.sh [--dry-run] [--cleanup] [--checksum] [--rollback]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="${SCRIPT_DIR}"
CELINA_INSTALL_DIR="${CELINA_INSTALL_DIR:-/home/user/macubuntu/features/celina}"
CELINA_PKG="${CELINA_INSTALL_DIR}/celina_pkg"

DRY_RUN=false
CLEANUP=false
CHECKSUM=false
ROLLBACK=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --cleanup) CLEANUP=true ;;
        --checksum) CHECKSUM=true ;;
        --rollback) ROLLBACK=true ;;
        --help|-h)
            echo "Usage: $0 [--dry-run] [--cleanup] [--checksum] [--rollback]"
            exit 0
            ;;
    esac
done

log()  { echo "[update-22] $*"; }
fail() { echo "[update-22] ERROR: $*" >&2; exit 1; }

verify_checksums() {
    if [ "$CHECKSUM" = false ]; then return 0; fi
    log "Verifying SHA256 checksums..."
    if [ ! -f "${PKG_DIR}/SHA256SUMS" ]; then fail "SHA256SUMS not found"; fi
    cd "${PKG_DIR}"
    if ! sha256sum -c SHA256SUMS 2>/dev/null; then fail "SHA256SUMS verification failed"; fi
    log "SHA256SUMS verified OK"
    cd - >/dev/null
}

do_cleanup() {
    if [ "$CLEANUP" = false ]; then return 0; fi
    log "Cleaning __pycache__/*.pyc..."
    if [ "$DRY_RUN" = true ]; then
        log "[dry-run] Would remove __pycache__/*.pyc"
    else
        find "${CELINA_PKG}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
        find "${CELINA_PKG}" -name "*.pyc" -type f -delete 2>/dev/null || true
        log "Cleanup complete"
    fi
}

do_checkpoint() {
    if [ "$DRY_RUN" = true ] || [ "$ROLLBACK" = true ]; then return 0; fi
    local ckpt="${CELINA_INSTALL_DIR}/.celina/update-22-checkpoint"
    log "Creating checkpoint at ${ckpt}..."
    mkdir -p "${ckpt}/celina_pkg"
    cp -r "${CELINA_PKG}/"* "${ckpt}/celina_pkg/" 2>/dev/null || true
    log "Checkpoint created"
}

do_rollback() {
    if [ "$ROLLBACK" = false ]; then return 0; fi
    local ckpt="${CELINA_INSTALL_DIR}/.celina/update-22-checkpoint"
    if [ ! -d "${ckpt}" ]; then fail "No checkpoint found at ${ckpt}"; fi
    log "Rolling back from checkpoint..."
    if [ "$DRY_RUN" = true ]; then
        log "[dry-run] Would restore from ${ckpt}"
    else
        cp -r "${ckpt}/celina_pkg/"* "${CELINA_PKG}/" 2>/dev/null || true
        log "Rollback complete"
    fi
}

do_apply() {
    local src="${PKG_DIR}/celina_pkg/router.py"
    local dst="${CELINA_PKG}/router.py"
    if [ ! -f "${src}" ]; then fail "Source router.py not found"; fi
    log "Installing router.py to ${dst}..."
    if [ "$DRY_RUN" = true ]; then
        log "[dry-run] Would install router.py"
    else
        cp "${src}" "${dst}"
        log "Installed: router.py"
    fi
}

main() {
    echo "========================================"
    echo "CELINA UPDATE 22 — Structure/Routing"
    echo "========================================"
    echo ""
    verify_checksums
    do_cleanup
    do_checkpoint
    do_apply
    echo ""
    if [ "$DRY_RUN" = true ]; then
        echo "DRY-RUN COMPLETE — no changes were made"
    else
        echo "UPDATE 22 APPLIED SUCCESSFULLY"
    fi
}
main "$@"