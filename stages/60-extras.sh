#!/usr/bin/env bash
# ============================================================================
# Stage 60: Extras — tiling-assistant dedup, flameshot,
#             gpu_screen_recorder, eyedropper
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[60] Extras (tiling, screenshot, recorder, color picker)"

# Note: gnome-sushi is owned solely by Stage 45 (apps-backups-search).
# It is NOT installed here to avoid duplicate installs.

# --- Ubuntu tiling-assistant dedup ---
# ENABLE_TILING (default true) already handles tiling via stage 25
# (tiling-assistant@leleat-on-github). Stage 60 dedups against it so the
# Ubuntu tiling-assistant package is never installed twice.
if is_true "$ENABLE_TILING"; then
    step "Checking Ubuntu tiling-assistant (dedup against stage 25 ENABLE_TILING)..."
    if apt_has gnome-shell-extension-ubuntu-tiling-assistant; then
        echo "      ℹ️  ubuntu-tiling-assistant available in APT but ENABLE_TILING already covers tiling via stage 25 — skipping to avoid duplicate tiling daemons."
        echo "         Install manually if desired: sudo apt install gnome-shell-extension-ubuntu-tiling-assistant"
    else
        echo "      ℹ️  ubuntu-tiling-assistant not in APT; ENABLE_TILING handles tiling via stage 25."
    fi
else
    echo "    ℹ️  Tiling disabled (ENABLE_TILING=false); skipping tiling-assistant."
fi

# --- flameshot: screenshot tool (sole ownership in Stage 60; removed from Stage 10) ---
if is_true "${ENABLE_FLAMESHOT:-false}"; then
    step "Installing flameshot (screenshot / annotation)..."
    apt_install flameshot || record_fail "apt: flameshot"
else
    echo "    ℹ️  flameshot skipped (ENABLE_FLAMESHOT=false; opt-in)"
fi

# --- gpu_screen_recorder: GPU-accelerated screen recording ---
if is_true "$ENABLE_SCREEN_RECORDER"; then
    step "Installing gpu_screen_recorder (GPU-accelerated screen recording)..."
    if apt_has gpu-screen-recorder; then
        apt_install gpu-screen-recorder || record_fail "apt: gpu-screen-recorder"
    else
        record_manual "gpu-screen-recorder not in APT — install from GitHub releases if needed"
    fi
else
    echo "    ℹ️  Screen recorder skipped (ENABLE_SCREEN_RECORDER=false; opt-in)"
fi

# --- eyedropper: Wayland-native color picker (replaces gpick, broken on Wayland) ---
if is_true "$ENABLE_COLOR_PICKER"; then
    step "Installing eyedropper (Wayland-native color picker)..."
    if apt_has eyedropper; then
        apt_install eyedropper || record_fail "apt: eyedropper"
    elif command -v flatpak >/dev/null 2>&1; then
        install_flatpak com.github.finefindus.eyedropper
    else
        record_manual "eyedropper not in APT — install via Flatpak: flatpak install flathub com.github.finefindus.eyedropper"
    fi
else
    echo "    ℹ️  Color picker skipped (ENABLE_COLOR_PICKER=false; opt-in)"
fi

# --- macOS Cmd key remapping (keyd) ---
# --macos-cmd-keys sets ENABLE_MACOS_CMD_KEYS=true in install.sh before stages run.
# Stage 60 reports the flag and installs keyd so --only 60 --macos-cmd-keys works.
if is_true "$ENABLE_MACOS_CMD_KEYS"; then
    step "Configuring macOS Cmd key remapping (keyd)..."
    echo "MACOS_CMD_KEYS=true"

    # T6: Harden keyd — only deploy if apt_install succeeded
    if ! apt_install keyd; then
        record_fail "apt: keyd — keyd deployment aborted (T6 root input daemon)"
    else
        # Ensure /etc/keyd exists with proper ownership
        run sudo install -d -m 755 /etc/keyd

        # Reject any command( shell-hook directives before copying keyd configs
        for conf in "$REPO_DIR/system/keyd/macubuntu.conf" "$REPO_DIR/system/keyd/app.conf"; do
            if [[ -f "$conf" ]] && grep -qE 'command\(' "$conf"; then
                record_fail "Rejected $conf: contains forbidden command() shell hook (T6)"
            fi
        done

        # Deploy keyd config via P5 atomic wrapper
        copy_root_file "$REPO_DIR/system/keyd/macubuntu.conf" /etc/keyd/macubuntu.conf 644
        if [[ -f "$REPO_DIR/system/keyd/app.conf" ]]; then
            copy_root_file "$REPO_DIR/system/keyd/app.conf" /etc/keyd/app.conf 644
        fi

        # Warn about conflict with Stage 45's gnome-macos-remap-wayland
        if [[ -f "$REPO_DIR/stages/45-apps-backups-search.sh" ]] && grep -q 'gnome-macos-remap-wayland' "$REPO_DIR/stages/45-apps-backups-search.sh" 2>/dev/null; then
            echo "      ⚠️  Stage 45 also provides gnome-macos-remap-wayland — keyd may conflict; ensure only one remapper is active"
        fi

        systemctl_enable keyd || record_fail "systemctl enable keyd"
    fi
else
    echo "    ℹ️  macOS Cmd keys skipped (ENABLE_MACOS_CMD_KEYS=false; pass --macos-cmd-keys to enable)"
fi

step "Extras stage complete."
