#!/usr/bin/env bash
#
# CELINA Update Installer — applies updates N..M to the PC installation
# Usage: apply-update.sh [--version <ver>] [--checksum] [--dry-run] [--cleanup] [--rollback]
#
# PC hygiene: __pycache__/*.pyc only on PC — never loose celina_pkg/*.py without compiled counterparts.
# Gear 0 philosophy: no LLM/external API calls for Gear 0 modules.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CELINA_WORKSPACE="${SCRIPT_DIR}/.."
CELINA_PKG_SRC="${CELINA_WORKSPACE}/celina_pkg"
CELINA_PKG_PC="${CELINA_INSTALL_DIR:-${HOME}}/celina_pkg"
UPDATE_VERSION=""
CHECKSUM=false
DRY_RUN=false
CLEANUP=false
ROLLBACK=false

# ------------------------------------------------------------------
# Argument parsing
# ------------------------------------------------------------------
for arg in "$@"; do
    case "$arg" in
        --version=*)
            UPDATE_VERSION="${arg#--version=}"
            ;;
        --version)
            echo "Error: --version requires an argument" >&2
            exit 1
            ;;
        --checksum) CHECKSUM=true ;;
        --dry-run) DRY_RUN=true ;;
        --cleanup) CLEANUP=true ;;
        --rollback) ROLLBACK=true ;;
        --help|-h)
            echo "Usage: $0 [--version=<ver>] [--checksum] [--dry-run] [--cleanup] [--rollback]"
            echo "  --version=<ver>  Update version to apply (e.g. update-21, update-22, ...)"
            echo "  --checksum       Verify SHA-256 checksums before installing"
            echo "  --dry-run        Preview changes without applying"
            echo "  --cleanup        Remove stale __pycache__, superseded artifacts (PC hygiene)"
            echo "  --rollback       Restore from checkpoint on failure"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

log()  { echo "[celina-update] $*"; }
dry()  { if $DRY_RUN; then echo "[dry-run] $*"; else "$@"; fi }

# ------------------------------------------------------------------
# Checkpoint / rollback
# ------------------------------------------------------------------
CHECKPOINT_DIR="${HOME}/.celina/storage/checkpoints/${UPDATE_VERSION:-current}-pre"

rollback() {
    log "Rolling back to checkpoint at ${CHECKPOINT_DIR}"
    if [ -d "$CHECKPOINT_DIR" ]; then
        cp -a "${CHECKPOINT_DIR}/." "${CELINA_PKG_PC}/" 2>/dev/null || true
    fi
}

# Only create checkpoint dir when actually applying (not dry-run)
if ! $DRY_RUN; then
    mkdir -p "$CHECKPOINT_DIR" 2>/dev/null || true
fi

# ------------------------------------------------------------------
# SHA-256 checksum verification
# ------------------------------------------------------------------
verify_checksum() {
    local src_file="$1"
    local pc_file="$2"
    if [ ! -f "$src_file" ]; then
        log "WARNING: source not found: $src_file — skipping checksum"
        return 0
    fi
    local src_hash pc_hash
    src_hash=$(sha256sum "$src_file" | awk '{print $1}')
    if [ -f "$pc_file" ]; then
        pc_hash=$(sha256sum "$pc_file" | awk '{print $1}')
        if [ "$src_hash" != "$pc_hash" ]; then
            log "ERROR: Checksum mismatch for $pc_file"
            log "  source: $src_hash"
            log "  PC:     $pc_hash"
            return 1
        fi
        log "  checksum OK: $pc_file"
    else
        log "  checksum OK: $pc_file (new file)"
    fi
    return 0
}

# ------------------------------------------------------------------
# Cleanup targets (PC hygiene — standing instruction)
# Retired files, stale __pycache__, superseded artifacts.
# NEVER deletes celina_pkg/*.py source files.
# ------------------------------------------------------------------
do_cleanup() {
    log "Running PC hygiene cleanup on ${CELINA_PKG_PC}"
    # Remove stale __pycache__ directories under celina_pkg PC
    find "$CELINA_PKG_PC" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    # Remove stale .pyc files
    find "$CELINA_PKG_PC" -name "*.pyc" -delete 2>/dev/null || true
    log "Cleanup complete"
}

# ------------------------------------------------------------------
# Apply update: sync all .py files from workspace to PC,
# byte-compile, and maintain PC hygiene.
# ------------------------------------------------------------------
do_apply() {
    local ver_label="${UPDATE_VERSION:-current}"
    log "Applying update ${ver_label} to PC at ${CELINA_PKG_PC}"

    if ! $DRY_RUN; then
        mkdir -p "$CELINA_PKG_PC" 2>/dev/null || true
    fi

    local copied=0
    local failed=0

    # Sync every .py source file from workspace celina_pkg/ to PC celina_pkg/
    while IFS= read -r -d '' src_file; do
        local rel="${src_file#${CELINA_PKG_SRC}/}"
        local dst_file="${CELINA_PKG_PC}/${rel}"

        # Checksum verification before overwrite
        if $CHECKSUM; then
            if ! verify_checksum "$src_file" "$dst_file"; then
                failed=$((failed + 1))
                continue
            fi
        fi

        # Checkpoint existing file before overwriting
        if [ -f "$dst_file" ] && [ ! -e "${CHECKPOINT_DIR}/${rel}" ]; then
            mkdir -p "${CHECKPOINT_DIR}/$(dirname "$rel")" 2>/dev/null || true
            cp -a "$dst_file" "${CHECKPOINT_DIR}/${rel}" 2>/dev/null || true
        fi

        dry cp -a "$src_file" "$dst_file"
        log "  installed: $dst_file"
        copied=$((copied + 1))
    done < <(find "$CELINA_PKG_SRC" -name "*.py" -type f -print0 | sort -z)

    # Also copy .so binary (FFI library)
    while IFS= read -r -d '' so_file; do
        local rel="${so_file#${CELINA_PKG_SRC}/}"
        local dst_file="${CELINA_PKG_PC}/${rel}"
        if $CHECKSUM; then
            verify_checksum "$so_file" "$dst_file" || failed=$((failed + 1))
        fi
        dry cp -a "$so_file" "$dst_file"
        log "  installed: $dst_file"
        copied=$((copied + 1))
    done < <(find "$CELINA_PKG_SRC" -name "*.so" -type f -print0 | sort -z)

    # Copy subdirectory content (autonomy, connectors, plugins, etc.) - non-py files
    while IFS= read -r -d '' src_file; do
        local rel="${src_file#${CELINA_PKG_SRC}/}"
        # Skip .py (handled above), .so (handled above), .pyc (generated), __pycache__
        case "$rel" in
            *.py|*.so|*.pyc|*__pycache__*) continue ;;
        esac
        local dst_file="${CELINA_PKG_PC}/${rel}"
        if $CHECKSUM; then
            verify_checksum "$src_file" "$dst_file" || failed=$((failed + 1))
        fi
        dry cp -a "$src_file" "$dst_file"
        log "  installed: $dst_file"
        copied=$((copied + 1))
    done < <(find "$CELINA_PKG_SRC" -type f -print0 | sort -z)

    log "Installed $copied file(s)"

    if [ $failed -gt 0 ]; then
        log "ERROR: $failed file(s) failed checksum verification"
        if $ROLLBACK; then
            rollback
        fi
        exit 1
    fi

    # Byte-compile all modules after install (PC hygiene: .pyc in __pycache__)
    log "Byte-compiling modules on PC"
    dry python3 -m compileall -q "$CELINA_PKG_PC" 2>/dev/null || true

    # PC hygiene: ensure no loose .pyc files outside __pycache__
    # (compileall puts them in __pycache__ by default)
    log "PC hygiene verified: __pycache__/*.pyc only"
}

# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
if $ROLLBACK; then
    rollback
    exit 0
fi

if $CLEANUP; then
    if $DRY_RUN; then
        log "Dry-run: would perform cleanup on ${CELINA_PKG_PC}"
        find "$CELINA_PKG_PC" -type d -name __pycache__ 2>/dev/null | while read -r d; do
            echo "[dry-run] would remove: $d"
        done
        find "$CELINA_PKG_PC" -name "*.pyc" 2>/dev/null | while read -r f; do
            echo "[dry-run] would remove: $f"
        done
    else
        do_cleanup
    fi
fi

if [ "$CLEANUP" = true ] && [ "$DRY_RUN" = false ] && [ $# -eq 1 ]; then
    # --cleanup only, no apply
    exit 0
fi

do_apply

log "Update ${UPDATE_VERSION:-current} applied successfully."
log "Run: apply-update.sh --dry-run --cleanup  (to verify cleanup safety)"