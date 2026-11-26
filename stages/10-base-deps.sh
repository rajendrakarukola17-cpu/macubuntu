#!/usr/bin/env bash
# ============================================================================
# Stage 10: Base Dependencies, Flathub, Extension CLI & Wayland Runtime
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[10] Base Dependencies & Runtime Foundations"

step "Updating APT package indices..."
run sudo apt-get update || record_fail "apt-get update (continuing with available package indices)"

step "Installing core desktop, theme-build, and tooling packages..."
apt_install_each \
    git curl wget jq unzip zstd ca-certificates gnupg build-essential \
    pipx python3 python3-pip python3-venv python3-gi python3-gi-cairo \
    gir1.2-gtk-3.0 gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1 \
    python3-yaml python3-jsonschema python3-httpx python3-defusedxml \
    flatpak gnome-tweaks gnome-shell-extension-manager \
    sassc optipng inkscape imagemagick libglib2.0-dev-bin iproute2 \
    libnotify-bin zenity xdg-desktop-portal xdg-desktop-portal-gnome

if is_true "$ENABLE_CELINA"; then
    step "Installing built-in Celina Wayland perception, audio, sandbox & search utilities..."
    # Note: ydotool is NOT installed by default; Portal + AT-SPI2 + scoped broker are preferred.
    apt_install_each \
        rofi wl-clipboard playerctl brightnessctl \
        espeak-ng libportaudio2 portaudio19-dev \
        fd-find ripgrep bubblewrap python3-uno \
        gir1.2-atspi-2.0 \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-tel tesseract-ocr-hin
    if is_true "$ENABLE_EXPERIMENTAL_UINPUT"; then
        step "Installing opt-in experimental ydotool fallback (ENABLE_EXPERIMENTAL_UINPUT=true)..."
        apt_install ydotool || record_fail "apt: ydotool"
    fi
fi

step "Ensuring user-scoped Flathub remote..."
run flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || true

step "Ensuring gnome-extensions-cli (gext) via pipx..."
run pipx ensurepath >/dev/null 2>&1 || true
if [[ "$DRY_RUN" != "1" ]] && ! command -v gext >/dev/null 2>&1; then
    run pipx install gnome-extensions-cli==1.4.0 >/dev/null 2>&1 \
        || record_manual "Install gext manually: pipx install gnome-extensions-cli==1.4.0"
    hash -r 2>/dev/null || true
fi

# Note: vmtouch and ananicy-cpp are owned by Stage 35 (memory-zram) to avoid
# duplicate installs. They are NOT installed here.

# Note: gnome-sushi is owned solely by Stage 45 (apps-backups-search).
# It is NOT installed here to avoid duplicate installs.

# Note: flameshot is owned solely by Stage 60 (extras).
# It is NOT installed here to avoid duplicate installs.
if is_true "$ENABLE_SCREEN_RECORDER"; then
    echo "    ℹ️  Screen recorder flag set; flameshot will be installed by Stage 60."
fi
