#!/usr/bin/env bash
# ============================================================================
# Stage 49: Raw CPU Speedup — Disable Spectre/Meltdown Mitigations
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[49] CPU Mitigations Off (mitigations=off via GRUB)"

if ! is_true "$ENABLE_CPU_MITIGATIONS_OFF"; then
    echo "    ℹ️  CPU mitigations off skipped (ENABLE_CPU_MITIGATIONS_OFF=false)"
    echo "      Enable with: ENABLE_CPU_MITIGATIONS_OFF=true ./install.sh --only 49"
    echo "      ⚠️  Only for trusted personal workstations — trades security for 5–15% raw speed"
    exit 0
fi

# Guardrail: warn about security tradeoff
echo ""
echo "    ⚠️  SECURITY TRADEOFF: Disabling Spectre/Meltdown mitigations"
echo "       trades hardware-level speculative execution patches for raw CPU speed."
echo "       Only safe on trusted personal machines (no untrusted code, no multi-user)."
echo ""

step "Applying mitigations=off to GRUB (P5 atomic R0 write)..."

if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] would add mitigations=off to GRUB_CMDLINE_LINUX_DEFAULT"
    echo "      [dry-run] would run sudo update-grub"
    echo "      [dry-run] would record manual: reboot to apply"
    exit 0
fi

GRUB_FILE="/etc/default/grub"

if [[ ! -f "$GRUB_FILE" ]]; then
    record_fail "GRUB config not found at $GRUB_FILE"
    exit 1
fi

# Snapshot before any mutation (P5 rollback contract)
snapshot_file "$GRUB_FILE"

# Idempotency: skip if already present
if grep -qE '^GRUB_CMDLINE_LINUX_DEFAULT="[^"]*mitigations=off' "$GRUB_FILE" 2>/dev/null; then
    log_info "GRUB already contains mitigations=off."
else
    tmp_grub="$(mktemp "$WORKDIR/grub.XXXXXX")"
    # EXIT trap cleans temp file on ALL exit paths (success, failure, signal)
    trap 'rm -f "$tmp_grub" 2>/dev/null' EXIT

    # Handle double-quoted, single-quoted, or missing GRUB_CMDLINE_LINUX_DEFAULT
    if grep -qE '^GRUB_CMDLINE_LINUX_DEFAULT="' "$GRUB_FILE"; then
        sed -E 's/^(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*)"/\1 mitigations=off"/' \
            "$GRUB_FILE" > "$tmp_grub"
    elif grep -qE "^GRUB_CMDLINE_LINUX_DEFAULT='" "$GRUB_FILE"; then
        sed -E "s/^(GRUB_CMDLINE_LINUX_DEFAULT='[^']*)'/\\1 mitigations=off'/" \
            "$GRUB_FILE" > "$tmp_grub"
    else
        # No GRUB_CMDLINE_LINUX_DEFAULT line — append one
        cat "$GRUB_FILE" > "$tmp_grub"
        echo 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash mitigations=off"' >> "$tmp_grub"
    fi

    # Verify the generated GRUB file before committing
    if ! grep -qE 'mitigations=off' "$tmp_grub"; then
        log_error "Generated /etc/default/grub missing mitigations=off; aborting."
        exit 1
    fi
    if ! bash -n "$tmp_grub"; then
        log_error "Generated /etc/default/grub failed shell syntax check; aborting."
        exit 1
    fi
    write_root_file "$GRUB_FILE" 644 < "$tmp_grub"
    echo "      ✅ mitigations=off added to $GRUB_FILE"
fi

if ! run sudo update-grub; then
    log_error "update-grub failed; run ./install.sh --rollback (exit 3) to restore snapshot."
    exit 1
fi

record_manual "Reboot to activate mitigations=off (verify: cat /proc/cpuinfo | grep bugs)"
echo "      ⚠️  Reboot required for mitigations=off to take effect"
