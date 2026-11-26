#!/usr/bin/env bash
# ============================================================================
# celina-visual-intelligence-keybinding.sh — Install Super+Shift+V keybinding
# ============================================================================
# Binds Super+Shift+V to celina-visual-intelligence for Visual Intelligence
# screen analysis (macOS Visual Intelligence equivalent).
set -Eeuo pipefail

CUSTOM_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
BINDING="<Super><Shift>v"
COMMAND="/usr/local/bin/celina-visual-intelligence"
NAME="Celina Visual Intelligence"

# Read existing custom keybinding list
EXISTING_LIST="$(gsettings read org.gnome.settings-daemon.plugins.media-keys custom-keybindings 2>/dev/null || echo "[]")"

# Check if our path is already in the list
if echo "$EXISTING_LIST" | grep -q "$CUSTOM_PATH"; then
    echo "ℹ️  Custom keybinding path already registered"
else
    # Append our path to the list
    NEW_LIST="$(echo "$EXISTING_LIST" | sed "s/\]$/, '${CUSTOM_PATH}'/")"
    gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$NEW_LIST" 2>/dev/null || \
        echo "⚠️  Failed to update custom-keybindings list (may need manual dconf setup)"
fi

# Set binding properties
gsettings set "org.gnome.settings-daemon.plugins.media-keys.custom-keybindings:${CUSTOM_PATH}" binding "$BINDING" 2>/dev/null || true
gsettings set "org.gnome.settings-daemon.plugins.media-keys.custom-keybindings:${CUSTOM_PATH}" command "$COMMAND" 2>/dev/null || true
gsettings set "org.gnome.settings-daemon.plugins.media-keys.custom-keybindings:${CUSTOM_PATH}" name "$NAME" 2>/dev/null || true

echo "✅ Visual Intelligence keybinding installed: ${BINDING} → ${COMMAND}"
