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
        if [[ -f /etc/pam.d/common-auth ]] && grep -qE '^[[:space:]]*auth[[:space:]].*pam_unix\.so' /etc/pam.d/common-auth; then
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

# --- malcontent: Parental controls (app allowlists, OARS ratings, session time limits) ---
if is_true "$ENABLE_PARENTAL_CONTROLS"; then
    step "Deploying malcontent (GNOME parental controls — app allowlists, OARS ratings, time limits)..."
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] apt install malcontent-client flatpak-xdg-utils"
        echo "      [dry-run] configure per-user app allowlists & session time limits via gsettings"
    else
        apt_install_each malcontent malcontent-gui flatpak-xdg-utils || record_manual "Install malcontent manually for parental controls"
        echo "      ✅ malcontent installed — configure via malcontent-control GUI (AccountsService D-Bus, not GSettings)"
        record_manual "Set app allowlist via malcontent-control GUI or AccountsService D-Bus (org.freedesktop.Accounts)"
        record_manual "Note: malcontent uses AccountsService D-Bus — not gsettings set org.gnome.malcontent"
    fi
else
    echo "    ℹ️  Parental controls skipped (ENABLE_PARENTAL_CONTROLS=false)"
fi

# --- AdGuard Home: Local DNS filtering + web filtering ("Ask to Browse" workflow) ---
if is_true "$ENABLE_DNS_FILTER"; then
    step "Deploying AdGuard Home (local DNS filtering — web filtering, 'Ask to Browse' workflow)..."
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] download AdGuard Home binary, create systemd service, configure DNS on port 53"
        echo "      [dry-run] set systemd-resolved DNS to 127.0.0.1 for system-wide filtering"
    else
        ADGUARD_DIR="$HOME/.local/share/adguardhome"
        if [[ ! -f "$ADGUARD_DIR/AdGuardHome" ]]; then
            echo "      ⚠️  AdGuard Home binary not found — download from https://github.com/AdguardTeam/AdGuardHome/releases"
            echo "      ℹ️  Pinned release in system/download-locks.sha256"
            record_manual "After download: ./AdGuardHome -s work -c config.yaml (then access http://localhost:3000)"
        else
            echo "      ✅ AdGuard Home binary already present at $ADGUARD_DIR/AdGuardHome"
        fi
        # DNS filtering is advisory/manual until AdGuard Home binary is installed
        echo "      ℹ️  AdGuard Home DNS filtering is advisory until the binary is installed at $ADGUARD_DIR/AdGuardHome"
        echo "      ℹ️  To enable system-wide filtering: set systemd-resolved DNSStubListener=no and point to 127.0.0.1:53"
        echo "      ℹ️  AdGuard Home web UI: http://localhost:3000 (set up 'Ask to Browse' approval workflow)"
    fi
else
    echo "    ℹ️  DNS filtering skipped (ENABLE_DNS_FILTER=false)"
fi

# --- Phase A: vault_audit — offline passkey / password breach hygiene (HIBP k-anonymity) ---
if is_true "$ENABLE_VAULT_AUDIT"; then
    step "Deploying vault_audit (offline breach-hash audit via HIBP k-anonymity)..."
    VAULT_DIR="$HOME/.local/share/celina/vault"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] install vault_audit.py to $HOME/.local/share/celina/vault/vault_audit.py"
        echo "      [dry-run] prepare offline breach-suffix db at $VAULT_DIR/breach_suffixes.jsonl (opt-in download)"
    else
        run install -d -m 700 "$VAULT_DIR"
        echo "      ✅ vault_audit directory ready ($VAULT_DIR)"
        echo "      ℹ️  Usage: PYTHONPATH=\"$REPO_DIR/features/celina\" python3 -m celina_pkg.vault_audit (HIBP 5-char prefix k-anonymity, no plaintext leaves machine)"
    fi
else
    echo "    ℹ️  vault_audit skipped (ENABLE_VAULT_AUDIT=false)"
fi

# --- AppArmor bwrap profile for code_sandbox.py userns access ---
# Ubuntu 26.04 restricts unprivileged userns (kernel.apparmor_restrict_unprivileged_userns=1),
# which breaks bwrap unless an AppArmor profile grants userns permission.
if [[ "$DRY_RUN" != "1" ]] && command -v apparmor_parser >/dev/null 2>&1; then
    step "Deploying AppArmor profile for bwrap (code_sandbox.py userns access)..."
    APPARMOR_SRC="$REPO_DIR/system/apparmor/bwrap-celina"
    APPARMOR_DST="/etc/apparmor.d/bwrap-celina"
    if [[ -f "$APPARMOR_SRC" ]]; then
        copy_root_file "$APPARMOR_SRC" "$APPARMOR_DST" 644
        sudo apparmor_parser -r "$APPARMOR_DST" 2>/dev/null || true
        echo "      ✅ AppArmor bwrap profile deployed: $APPARMOR_DST"
    else
        record_manual "AppArmor bwrap profile not found at $APPARMOR_SRC — code_sandbox.py may degrade to Landlock-only fallback"
    fi
elif [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] would deploy AppArmor bwrap profile (system/apparmor/bwrap-celina)"
fi
