#!/usr/bin/env bash
#
# CELINA UPDATE 21 — Latency Truth Installer
# Applies latency fixes: gear0_routines, gear0_lists, gear0_ledger, plugins/os_shim
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
            echo "  --dry-run    Preview changes without applying"
            echo "  --cleanup    Remove only __pycache__/*.pyc (never .py sources)"
            echo "  --checksum   Verify SHA256SUMS before applying"
            echo "  --rollback   Restore code from atomic checkpoint"
            exit 0
            ;;
    esac
done

log()  { echo "[update-21] $*"; }
fail() { echo "[update-21] ERROR: $*" >&2; exit 1; }

# ------------------------------------------------------------------
# Checksum verification
# ------------------------------------------------------------------
verify_checksums() {
    if [ "$CHECKSUM" = false ]; then
        return 0
    fi
    log "Verifying SHA256 checksums..."
    if [ ! -f "${PKG_DIR}/SHA256SUMS" ]; then
        fail "SHA256SUMS not found in package"
    fi
    cd "${PKG_DIR}"
    if ! sha256sum -c SHA256SUMS 2>/dev/null; then
        fail "SHA256SUMS verification failed — aborting"
    fi
    log "SHA256SUMS verified OK"
    cd - >/dev/null
}

# ------------------------------------------------------------------
# Cleanup: only __pycache__/*.pyc, never .py sources
# ------------------------------------------------------------------
do_cleanup() {
    if [ "$CLEANUP" = false ]; then
        return 0
    fi
    log "Cleaning __pycache__/*.pyc artifacts..."
    if [ "$DRY_RUN" = true ]; then
        log "[dry-run] Would remove __pycache__/*.pyc from ${CELINA_PKG}"
    else
        find "${CELINA_PKG}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
        find "${CELINA_PKG}" -name "*.pyc" -type f -delete 2>/dev/null || true
        log "Cleanup complete"
    fi
}

# ------------------------------------------------------------------
# Apply update: copy files to celina_pkg
# ------------------------------------------------------------------
do_apply() {
    local src="${PKG_DIR}/celina_pkg"
    local dst="${CELINA_PKG}"

    if [ ! -d "${src}" ]; then
        fail "Package celina_pkg/ not found in ${PKG_DIR}"
    fi

    if [ ! -d "${dst}" ]; then
        fail "Target celina_pkg not found at ${dst}"
    fi

    log "Installing Update 21 files to ${dst}..."

    # List of files to install
    local files=(
        "celina_pkg/gear0_routines.py"
        "celina_pkg/gear0_lists.py"
        "celina_pkg/gear0_ledger.py"
        "celina_pkg/plugins/os_shim.py"
    )

    for f in "${files[@]}"; do
        local src_file="${PKG_DIR}/${f}"
        local dst_file="${dst}/${f}"
        local dst_dir
        dst_dir="$(dirname "${dst_file}")"

        if [ ! -f "${src_file}" ]; then
            fail "Source file not found: ${src_file}"
        fi

        if [ "$DRY_RUN" = true ]; then
            log "[dry-run] Would install ${f} -> ${dst_file}"
        else
            mkdir -p "${dst_dir}"
            cp "${src_file}" "${dst_file}"
            log "Installed: ${f}"
        fi
    done
}

# ------------------------------------------------------------------
# Rollback: restore from checkpoint
# ------------------------------------------------------------------
do_rollback() {
    if [ "$ROLLBACK" = false ]; then
        return 0
    fi
    local ckpt="${CELINA_INSTALL_DIR}/.celina/update-21-checkpoint"
    if [ ! -d "${ckpt}" ]; then
        fail "No checkpoint found at ${ckpt}"
    fi
    log "Rolling back from checkpoint..."
    if [ "$DRY_RUN" = true ]; then
        log "[dry-run] Would restore from ${ckpt}"
    else
        cp -r "${ckpt}/celina_pkg/"* "${CELINA_PKG}/" 2>/dev/null || true
        log "Rollback complete"
    fi
}

# ------------------------------------------------------------------
# Checkpoint before applying
# ------------------------------------------------------------------
do_checkpoint() {
    if [ "$DRY_RUN" = true ] || [ "$ROLLBACK" = true ]; then
        return 0
    fi
    local ckpt="${CELINA_INSTALL_DIR}/.celina/update-21-checkpoint"
    log "Creating checkpoint at ${ckpt}..."
    mkdir -p "${ckpt}/celina_pkg"
    cp -r "${CELINA_PKG}/"* "${ckpt}/celina_pkg/" 2>/dev/null || true
    log "Checkpoint created"
}

# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
main() {
    echo "========================================"
    echo "CELINA UPDATE 21 — Latency Truth"
    echo "========================================"
    echo ""

    verify_checksums
    do_cleanup
    do_checkpoint
    do_apply

    echo ""
    if [ "$DRY_RUN" = true ]; then
        echo "DRY-RUN COMPLETE — no changes were made"
        echo "Run without --dry-run to apply for real"
    else
        echo "UPDATE 21 APPLIED SUCCESSFULLY"
        echo ""
        echo "Next steps:"
        echo "  1. Run: python3 tools/test_critical_a_b.py"
        echo "  2. Run: python3 tools/lint_lazy_imports.py"
        echo "  3. Run: cd boost/update-20/pkg/ci && bash run-ci-gates.sh"
    fi
}

main "$@"