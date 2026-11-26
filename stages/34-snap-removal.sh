#!/usr/bin/env bash
# ============================================================================
# Stage 34: Snap Removal — Replace Snaps with Native .deb / Flatpak
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[34] Snap Removal — Native .deb / Flatpak Replacement"

if ! is_true "$ENABLE_SNAP_REMOVAL"; then
    echo "    ℹ️  Snap removal skipped (ENABLE_SNAP_REMOVAL=false)"
    echo "      Enable with: ENABLE_SNAP_REMOVAL=true ./install.sh --only 34"
    exit 0
fi

step "Removing Snap packages (cold-start latency from squashfs loop mounts)..."

if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] would remove: snapd, snap-store, firefox (snap)"
    echo "      [dry-run] would install: firefox (.deb via Mozilla PPA or flatpak)"
    echo "      [dry-run] would purge snapd and block snap refs in /etc/apt/preferences.d/"
    exit 0
fi

# --- Remove snapd and snap packages ---
step "Removing snapd and snap packages..."
run sudo apt-get remove -y --purge snapd snap-store firefox 2>/dev/null || true
run sudo apt-get autoremove -y 2>/dev/null || true

# Block snapd from being reinstalled as a dependency
step "Blocking snapd reinstall via apt preferences..."
{
    echo "Package: snapd"
    echo "Pin: release *"
    echo "Pin-Priority: -1"
} | write_root_file /etc/apt/preferences.d/99-macubuntu-block-snapd 644

# --- Install native Firefox .deb via Mozilla PPA ---
step "Installing native Firefox (.deb) via Mozilla PPA..."
if ! command -v firefox >/dev/null 2>&1; then
    run sudo add-apt-repository -y ppa:mozillateam/ppa 2>/dev/null || true
    run sudo apt-get update || true
    apt_install firefox || record_fail "apt: firefox"
else
    echo "      ℹ️  Firefox already installed (native .deb or flatpak present)"
fi

# --- Offer Flatpak replacements for common Snap apps ---
step "Suggesting Flatpak replacements for common Snap apps..."
if command -v flatpak >/dev/null 2>&1; then
    run_as_user flatpak remote-add --user --if-not-exists flathub \
        https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
    echo "      ℹ️  Consider: flatpak install --user flathub org.mozilla.firefox"
    echo "              (or keep native .deb Firefox above)"
else
    echo "      ℹ️  Flatpak not installed — native .deb Firefox is the replacement"
fi

echo "      ✅ Snap removal complete. Reboot to fully clear snap mounts."
record_manual "Reboot after snapd removal to clear all squashfs loop mounts"
