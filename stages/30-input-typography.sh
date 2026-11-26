#!/usr/bin/env bash
# ============================================================================
# Stage 30: Input Physics & macOS-Grade Typography
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[30] Input Physics ($POINTER_ACCEL) & Typography ($FONT_AA)"

step "Applying MacBook-grade trackpad, pointer acceleration, window physics & text rendering..."
gset org.gnome.desktop.peripherals.mouse    accel-profile "'$POINTER_ACCEL'"
gset org.gnome.desktop.peripherals.touchpad accel-profile "'$POINTER_ACCEL'"
gset org.gnome.desktop.peripherals.touchpad click-method "'fingers'"
gset org.gnome.desktop.peripherals.touchpad two-finger-scrolling-enabled true
gset org.gnome.desktop.peripherals.touchpad edge-scrolling-enabled false
gset org.gnome.desktop.peripherals.touchpad tap-and-drag true
gset org.gnome.desktop.peripherals.touchpad send-events "'enabled'"
gset org.gnome.desktop.wm.preferences       resize-with-right-button true
gset org.gnome.mutter                       center-new-windows true
gset org.gnome.mutter                       attach-modal-dialogs true
gset org.gnome.desktop.interface text-hinting      "'slight'"
gset org.gnome.desktop.interface text-antialiasing "'$FONT_AA'"

step "Installing clean sans-serif, monospace, and emoji fonts..."
apt_install_each fonts-inter fonts-open-sans fonts-roboto fonts-firacode fonts-noto-core fonts-noto-color-emoji
