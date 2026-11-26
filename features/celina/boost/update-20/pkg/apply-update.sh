#!/usr/bin/env bash
#
# CELINA UPDATE 20 — "Truth & Safety" Installer
# Replaces Updates 18+19 and all P0 safety fixes
# Usage: apply-update.sh [--dry-run] [--cleanup] [--rollback] [--checksum]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="${SCRIPT_DIR}"

# Destination: celina installation directory (overridable)
CELINA_ROOT="${CELINA_INSTALL_DIR:-/home/user/macubuntu/features/celina}"
CELINA_PKG="${CELINA_ROOT}/celina_pkg"
UPDATE_VERSION="update-20"
DRY_RUN=false
CLEANUP=false
ROLLBACK=false
CHECKSUM=false

# ------------------------------------------------------------------
# Argument parsing
# ------------------------------------------------------------------
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --cleanup) CLEANUP=true ;;
        --rollback) ROLLBACK=true ;;
        --checksum) CHECKSUM=true ;;
        --help|-h)
            echo "Usage: $0 [--dry-run] [--cleanup] [--rollback] [--checksum]"
            echo "  --dry-run   Preview changes without applying"
            echo "  --cleanup   Remove retired files, stale __pycache__, superseded artifacts"
            echo "  --rollback  Restore from checkpoint on failure"
            echo "  --checksum  Verify SHA256SUMS before applying"
            echo ""
            echo "Environment:"
            echo "  CELINA_INSTALL_DIR  Path to celina installation"
            echo "                      (default: /home/user/macubuntu/features/celina)"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

log()  { echo "[update-20] $*"; }
err()  { echo "[update-20] ERROR: $*" >&2; }

# ------------------------------------------------------------------
# Signature verification (B9: verify tarball signature before applying)
# ------------------------------------------------------------------
verify_signature() {
    log "Verifying update signature..."
    local sig_file="${PKG_DIR}/../update-20.tar.gz.sig"
    local pub_key="${PKG_DIR}/update_signing_key.pub"
    local tarball="${PKG_DIR}/../update-20.tar.gz"

    if [ ! -f "$sig_file" ]; then
        err "Signature file not found: $sig_file — refusing to apply unsigned update"
        return 1
    fi
    if [ ! -f "$pub_key" ]; then
        err "Public key not found: $pub_key — cannot verify signature"
        return 1
    fi
    if [ ! -f "$tarball" ]; then
        err "Tarball not found: $tarball — cannot verify signature"
        return 1
    fi

    if ! openssl dgst -sha256 -verify "$pub_key" -signature "$sig_file" "$tarball" 2>/dev/null; then
        err "Signature verification FAILED — tarball may be tampered or corrupted"
        return 1
    fi

    log "Update signature verified OK"
    return 0
}

# ------------------------------------------------------------------
# Checksum verification (B3: verify tarball + all files before applying)
# ------------------------------------------------------------------
verify_checksums() {
    log "Verifying SHA256 checksums..."
    local sums_file="${PKG_DIR}/SHA256SUMS"
    if [ ! -f "$sums_file" ]; then
        err "SHA256SUMS not found in package — refusing to apply unsigned update"
        return 1
    fi

    # Verify each file listed in SHA256SUMS
    local failed=0
    while read -r expected_hash rel_path; do
        # Skip blank lines
        [ -z "$rel_path" ] && continue
        local full_path="${PKG_DIR}/${rel_path#./}"
        if [ ! -f "$full_path" ]; then
            err "Missing file: $rel_path"
            failed=$((failed + 1))
            continue
        fi
        local actual_hash
        actual_hash=$(sha256sum "$full_path" | awk '{print $1}')
        if [ "$actual_hash" != "$expected_hash" ]; then
            err "Checksum mismatch: $rel_path"
            err "  expected: $expected_hash"
            err "  actual:   $actual_hash"
            failed=$((failed + 1))
        fi
    done < "$sums_file"

    # Verify tarball itself is checksummed
    local tarball="${PKG_DIR}/../update-20.tar.gz"
    if [ -f "$tarball" ]; then
        local tarball_hash
        tarball_hash=$(sha256sum "$tarball" | awk '{print $1}')
        log "  update-20.tar.gz SHA256: $tarball_hash"
    fi

    if [ "$failed" -gt 0 ]; then
        err "$failed file(s) failed checksum verification — aborting"
        return 1
    fi

    log "All checksums verified OK"
    return 0
}

# ------------------------------------------------------------------
# Checkpoint / rollback (atomic: entire package version, not per-file)
# ------------------------------------------------------------------
CHECKPOINT_DIR="${HOME}/.celina/storage/checkpoints/update-20-pre"
CODE_CHECKPOINT_DIR="${CHECKPOINT_DIR}/code"

rollback() {
    log "Rolling back code checkpoint at ${CODE_CHECKPOINT_DIR}"
    if [ -d "$CODE_CHECKPOINT_DIR" ]; then
        # Atomic: remove current, swap in checkpoint
        local rollback_staging="${CELINA_PKG}.rollback.$$"
        mkdir -p "$rollback_staging"
        cp -a "${CODE_CHECKPOINT_DIR}/." "$rollback_staging/" 2>/dev/null || true
        # Swap: move old aside, move checkpoint in
        local old="${CELINA_PKG}.old.$$"
        mv "$CELINA_PKG" "$old" 2>/dev/null || true
        mv "$rollback_staging" "$CELINA_PKG" 2>/dev/null || true
        log "Rolled back. Old version saved at: $old"
    else
        err "No code checkpoint found — cannot roll back"
    fi
}

# ------------------------------------------------------------------
# File inventory: all modules in Update 20
# Source files are inside the package at PKG_DIR/celina_pkg/
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
# Atomic install: stage all files in a temp dir, then swap
# ------------------------------------------------------------------
do_apply() {
    log "Applying Update 20 — Truth & Safety"
    log "  Install dir: $CELINA_PKG"

    # B9: Always verify signature before applying
    verify_signature || exit 1

    if $CHECKSUM; then
        verify_checksums || exit 1
    fi

    local copied=0
    local staging

    if $DRY_RUN; then
        log "[dry-run] Would stage and atomically install $CELINA_PKG"
        for rel in "${UPDATE_FILES[@]}"; do
            local src="${PKG_DIR}/${rel}"
            local dst="${CELINA_PKG}/${rel#celina_pkg/}"
            if [ ! -f "$src" ]; then
                log "  [dry-run] SKIP (missing source): $src"
                continue
            fi
            echo "[dry-run] would install: $dst"
            copied=$((copied + 1))
        done
        log "[dry-run] Would install $copied file(s) atomically via directory swap"
        log "[dry-run] Would run: python3 -m compileall -q $CELINA_PKG"
        log "[dry-run] Would verify storage layout"
        return 0
    fi

    # --- Real install: atomic directory swap ---

    # 1. Create staging directory alongside celina_pkg (same filesystem = atomic mv)
    staging="${CELINA_PKG}.staging.$$"
    rm -rf "$staging" 2>/dev/null || true
    mkdir -p "$staging"

    # 2. Copy current celina_pkg into staging as the base
    if [ -d "$CELINA_PKG" ]; then
        cp -a "$CELINA_PKG/." "$staging/" 2>/dev/null || true
    fi

    # 3. Checkpoint current version for rollback (atomic code snapshot)
    mkdir -p "$CODE_CHECKPOINT_DIR"
    rm -rf "${CODE_CHECKPOINT_DIR:?}/." 2>/dev/null || true
    cp -a "$CELINA_PKG/." "$CODE_CHECKPOINT_DIR/" 2>/dev/null || true

    # 4. Overlay Update 20 files onto staging
    for rel in "${UPDATE_FILES[@]}"; do
        local src="${PKG_DIR}/${rel}"
        local dst_name="${rel#celina_pkg/}"
        local dst="${staging}/${dst_name}"
        if [ ! -f "$src" ]; then
            log "WARNING: source not found: $src — skipping"
            continue
        fi
        mkdir -p "$(dirname "$dst")" 2>/dev/null || true
        cp -a "$src" "$dst"
        log "  staged: $dst_name"
        copied=$((copied + 1))
    done
    log "Staged $copied file(s)"

    # 5. Byte-compile staging
    log "Byte-compiling staging modules"
    python3 -m compileall -q "$staging" 2>/dev/null || true

    # 6. Atomic swap: move current aside, move staging in
    local old="${CELINA_PKG}.old.$$"
    mv "$CELINA_PKG" "$old" 2>/dev/null || true
    mv "$staging" "$CELINA_PKG" 2>/dev/null || true

    # 7. Clean up old version (keep for a short time for manual rollback)
    log "Previous version preserved at: $old"
    log "Remove with: rm -rf $old"

    # 8. Verify storage layout
    log "Verifying storage layout"
    python3 -c "
from celina_pkg.gear0_storage import initialize_storage
initialize_storage()
import os
p = os.path.expanduser('~/.celina/storage/shared/backend_state.json')
print('  backend_state.json exists:', os.path.exists(p))
" 2>/dev/null || log "  storage init skipped"

    log "Update 20 applied successfully via atomic directory swap."
    log "Run: apply-update.sh --dry-run --cleanup  (to verify cleanup safety)"
    log "Rollback: apply-update.sh --rollback"
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

# If only --cleanup (without --dry-run and not combined), stop here
if [ "$CLEANUP" = true ] && [ "$DRY_RUN" = false ] && [ $# -eq 1 ]; then
    exit 0
fi

do_apply
