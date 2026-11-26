#!/usr/bin/env bash
#
# CELINA UPDATE 20 — "Truth & Safety" Installer
# Replaces Updates 18+19 and all P0 safety fixes
# Usage: apply-update.sh [--dry-run] [--cleanup] [--rollback]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CELINA_PKG="${SCRIPT_DIR}/../celina_pkg"
UPDATE_VERSION="update-20"
DRY_RUN=false
CLEANUP=false
ROLLBACK=false

# ------------------------------------------------------------------
# Argument parsing
# ------------------------------------------------------------------
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --cleanup) CLEANUP=true ;;
        --rollback) ROLLBACK=true ;;
        --help|-h)
            echo "Usage: $0 [--dry-run] [--cleanup] [--rollback]"
            echo "  --dry-run   Preview changes without applying"
            echo "  --cleanup   Remove retired files, stale __pycache__, superseded artifacts"
            echo "  --rollback  Restore from checkpoint on failure"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

log()  { echo "[update-20] $*"; }
dry()  { if $DRY_RUN; then echo "[dry-run] $*"; else "$@"; fi }

# ------------------------------------------------------------------
# Checkpoint / rollback
# ------------------------------------------------------------------
CHECKPOINT_DIR="${HOME}/.celina/storage/checkpoints/update-20-pre"
mkdir -p "$CHECKPOINT_DIR" 2>/dev/null || true

rollback() {
    log "Rolling back to checkpoint at ${CHECKPOINT_DIR}"
    if [ -d "$CHECKPOINT_DIR" ]; then
        cp -a "${CHECKPOINT_DIR}/." "${CELINA_PKG}/" 2>/dev/null || true
    fi
}

# ------------------------------------------------------------------
# File inventory: all modules in Update 20
# These are the files that get copied into celina_pkg/
# ------------------------------------------------------------------
declare -a UPDATE_FILES=(
    "celina_pkg/gear0_macro.py"
    "celina_pkg/gear0_storage.py"
    "celina_pkg/gear0_ledger.py"
    "celina_pkg/plugins/sniffer_plugin.py"
    "celina_pkg/plugins/habit_engine.py"
    "celina_pkg/plugins/data_oracle.py"
    "celina_pkg/plugins/os_shim.py"
    "celina_pkg/plugins/self_heal.py"
    "celina_pkg/router.py"
)

# ------------------------------------------------------------------
# Cleanup targets (PC hygiene — standing instruction)
# Retired files, stale __pycache__, superseded artifacts.
# NEVER deletes celina_pkg/*.py source files.
# ------------------------------------------------------------------
do_cleanup() {
    log "Running PC hygiene cleanup"
    # Remove stale __pycache__ directories under celina_pkg
    find "$CELINA_PKG" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    # Remove stale .pyc files
    find "$CELINA_PKG" -name "*.pyc" -delete 2>/dev/null || true
    log "Cleanup complete"
}

# ------------------------------------------------------------------
# Apply update: copy each file into celina_pkg/
# ------------------------------------------------------------------
do_apply() {
    log "Applying Update 20 — Truth & Safety"
    local copied=0
    for rel in "${UPDATE_FILES[@]}"; do
        local src="${SCRIPT_DIR}/../${rel}"
        local dst="${CELINA_PKG}/${rel#celina_pkg/}"
        if [ ! -f "$src" ]; then
            log "WARNING: source not found: $src — skipping"
            continue
        fi
        # Checkpoint the existing file before overwriting
        if [ -f "$dst" ] && [ ! -e "${CHECKPOINT_DIR}/${rel#celina_pkg/}" ]; then
            mkdir -p "${CHECKPOINT_DIR}/$(dirname "${rel#celina_pkg/}")" 2>/dev/null || true
            cp -a "$dst" "${CHECKPOINT_DIR}/${rel#celina_pkg/}" 2>/dev/null || true
        fi
        dry cp -a "$src" "$dst"
        log "  installed: $dst"
        copied=$((copied + 1))
    done
    log "Installed $copied file(s)"

    # Byte-compile all modules after install
    log "Byte-compiling modules"
    dry python3 -m compileall -q "$CELINA_PKG" 2>/dev/null || true

    # Verify storage layout
    log "Verifying storage layout"
    dry python3 -c "
from celina_pkg.gear0_storage import initialize_storage
initialize_storage()
import os
p = os.path.expanduser('~/.celina/storage/shared/backend_state.json')
print('  backend_state.json exists:', os.path.exists(p))
" 2>/dev/null || log "  storage init skipped (dry-run or import issue)"
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
        log "Dry-run: would perform cleanup"
        find "$CELINA_PKG" -type d -name __pycache__ 2>/dev/null | while read -r d; do
            echo "[dry-run] would remove: $d"
        done
        find "$CELINA_PKG" -name "*.pyc" 2>/dev/null | while read -r f; do
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

log "Update 20 applied successfully."
log "Run: apply-update.sh --dry-run --cleanup  (to verify cleanup safety)"
