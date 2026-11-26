#!/usr/bin/env bash
#
# CELINA UPDATE 26 Installer
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

log()  { echo "[update-26] $*"; }
fail() { echo "[update-26] ERROR: $*" >&2; exit 1; }

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
    local ckpt="${CELINA_INSTALL_DIR}/.celina/update-26-checkpoint"
    log "Creating checkpoint at ${ckpt}..."
    mkdir -p "${ckpt}/celina_pkg"
    cp -r "${CELINA_PKG}/"* "${ckpt}/celina_pkg/" 2>/dev/null || true
    log "Checkpoint created"
}

do_rollback() {
    if [ "$ROLLBACK" = false ]; then return 0; fi
    local ckpt="${CELINA_INSTALL_DIR}/.celina/update-26-checkpoint"
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
    log "Installing Update 26 files to ${CELINA_PKG}..."
    for f in celina_pkg/gear0_5_smolLM.py celina_pkg/config.py; do
        local src="${PKG_DIR}/${f}"
        local dst="${CELINA_PKG}/${f}"
        local dst_dir
        dst_dir="$(dirname "${dst}")"
        if [ ! -f "${src}" ]; then fail "Source file not found: ${src}"; fi
        if [ "$DRY_RUN" = true ]; then
            log "[dry-run] Would install ${f}"
        else
            mkdir -p "${dst_dir}"
            cp "${src}" "${dst}"
            log "Installed: ${f}"
        fi
    done
}

main() {
    echo "========================================"
    echo "CELINA UPDATE 26"
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
        echo "UPDATE 26 APPLIED SUCCESSFULLY"
    fi
}
main "$@"
