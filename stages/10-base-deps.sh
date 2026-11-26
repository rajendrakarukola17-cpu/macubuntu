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
    pipx python3 python3-pip python3-venv python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
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
        gnome-screenshot gir1.2-atspi-2.0 \
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
if ! command -v gext >/dev/null 2>&1; then
    run pipx install gnome-extensions-cli >/dev/null 2>&1 \
        || record_manual "Install gext manually: pipx install gnome-extensions-cli"
    hash -r 2>/dev/null || true
fi
