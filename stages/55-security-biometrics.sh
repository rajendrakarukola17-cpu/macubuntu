#!/usr/bin/env bash
# ============================================================================
# Stage 55: Hardware Security, TPM 2.0 Advisory & Capability-Gated Biometric PAM
# Strict Invariants:
#   - NEVER removes or weakens OS password authentication (pam_unix.so mandatory!)
#   - NEVER modifies LUKS/TPM disk encryption destructively; emits exact safe enrollment command
#   - Biometric packages (fprintd / FIDO2) are installed ONLY when opt-in or hardware detected
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[55] Hardware Security, TPM 2.0 LUKS Advisor & Capability-Gated Biometrics"

step "Checking TPM 2.0 & LUKS2 encryption readiness (read-only advisor)..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] inspect /dev/tpmrm0 & LUKS2; never modifies disk encryption automatically"
else
    HAS_TPM=0
    [[ -c /dev/tpmrm0 ]] && HAS_TPM=1
    if [[ "$HAS_TPM" -eq 1 ]] && detect_has_luks; then
        echo "      🔒 TPM 2.0 (/dev/tpmrm0) + LUKS detected!"
        record_manual "Optional FileVault-style TPM2+PIN auto-unlock: first create a recovery key ('sudo systemd-cryptenroll --recovery-key /dev/<luks-part>'), then bind TPM2+PIN ('sudo systemd-cryptenroll --tpm2-device=auto --tpm2-with-pin=yes /dev/<luks-part>')"
    elif [[ "$HAS_TPM" -eq 1 ]]; then
        echo "      ℹ️  TPM 2.0 detected, but root volume is not LUKS-encrypted."
    else
        echo "      ℹ️  No TPM 2.0 device detected (/dev/tpmrm0 absent)."
    fi
fi

step "Checking capability-gated Fingerprint (fprintd/PAM) & FIDO2 (libpam-u2f)..."
if is_true "$ENABLE_BIOMETRICS"; then
    apt_install_each fprintd libpam-fprintd fido2-tools libpam-u2f || record_manual "Some biometric/FIDO2 packages unavailable"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] verify /etc/pam.d/common-auth preserves pam_unix.so password fallback"
    else
        if [[ -f /etc/pam.d/common-auth ]] && grep -q 'pam_unix\.so' /etc/pam.d/common-auth; then
            echo "      ✅ Verified OS password fallback (pam_unix.so) is present in /etc/pam.d/common-auth."
        else
            record_fail "PAM password fallback (pam_unix.so) not detected; refusing biometric activation!"
        fi
        record_manual "Enroll Touch ID safely: run 'fprintd-enroll', then enable in 'sudo pam-auth-update' (keep Unix password checked!)"
        record_manual "Enroll FIDO2 key: run 'mkdir -p ~/.config/Yubico && pamu2fcfg > ~/.config/Yubico/u2f_keys && chmod 600 ~/.config/Yubico/u2f_keys'"
    fi
else
    echo "      Biometric PAM setup not selected (set ENABLE_BIOMETRICS=true to install fprintd + FIDO2 PAM)."
fi

step "Checking Btrfs bootable OS snapshot readiness (macOS SSV rollback equivalent)..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] check if root filesystem is btrfs for snapper/timeshift-btrfs boot snapshots"
elif findmnt -n -o FSTYPE / 2>/dev/null | grep -qi '^btrfs$'; then
    echo "      ✅ Btrfs root filesystem detected — instant pre-upgrade bootable snapshots supported!"
    record_manual "Enable automatic bootable snapshots: sudo apt install snapper grub-btrfs"
else
    echo "      ℹ️  Root filesystem is not Btrfs (Timeshift RSYNC + PikaBackup cover file/OS recovery)."
fi
