#!/usr/bin/env bash
# ============================================================================
# Stage 60: Optional Community Extras & Rust Utilities (Strict Opt-In, Pinned Only)
# Policy: NEVER fetch unpinned "releases/latest" or execute unverified remote scripts!
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[60] Optional Extras (THIRDPARTY=$INSTALL_THIRDPARTY | RUST=$INSTALL_RUST_TOOLS | MACOS_CMD_KEYS=$ENABLE_MACOS_CMD_KEYS)"

if is_true "$ENABLE_MACOS_CMD_KEYS"; then
    step "Deploying opt-in Wayland-native terminal-aware macOS Cmd key remapping (keyd)..."
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] apt install keyd, install /etc/keyd/macubuntu.conf & ~/.config/keyd/app.conf, enable keyd.service"
    elif apt_has keyd; then
        apt_install keyd || record_fail "keyd"
        copy_root_file "$REPO_DIR/system/keyd/macubuntu.conf" "/etc/keyd/macubuntu.conf" 644
        ensure_dir "$HOME/.config/keyd"
        copy_user_file "$REPO_DIR/system/keyd/app.conf" "$HOME/.config/keyd/app.conf" 644
        run sudo systemctl enable --now keyd.service || record_fail "keyd.service"
        echo "      Installed /etc/keyd/macubuntu.conf (Cmd+C/V/Q) + ~/.config/keyd/app.conf (terminal Ctrl+C preserved)."
    else
        record_manual "keyd not in APT repository — install keyd manually and copy system/keyd/macubuntu.conf to /etc/keyd/"
    fi
else
    step "Skipping opt-in macOS Cmd key remapping (pass --macos-cmd-keys or ENABLE_MACOS_CMD_KEYS=true to opt in)."
fi

if is_true "$INSTALL_THIRDPARTY"; then
    step "Installing opt-in PINNED community macOS-style extras (zero floating latest URLs)..."

    # Unpinned third-party .debs & remote install scripts are refused automatically unless
    # explicitly pinned with verified SHA-256 digests in system/download-locks.sha256.
    record_manual "Optional third-party .debs (Ubuntu-Launchpad, TwinDesk, FrostFinder, NeoHtop, Veronica): verify upstream SHA-256 checksums manually or pin them in system/download-locks.sha256 before installing"

    if clone_pinned_repo "stage-manager-linux" "$WORKDIR/stage-mgr"; then
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] inspect & run pinned $WORKDIR/stage-mgr/install-stage-manager.sh"
        else
            echo "      Pinned Stage Manager checked out at $WORKDIR/stage-mgr/install-stage-manager.sh"
            ( cd "$WORKDIR/stage-mgr" && bash install-stage-manager.sh ) || record_fail "Stage Manager"
        fi
    else
        record_manual "Stage Manager: https://github.com/byQuesters/Stage-Manager-for-Linux"
    fi

    if clone_pinned_repo "hotshot" "$WORKDIR/hotshot"; then
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] inspect & run pinned $WORKDIR/hotshot/install.sh"
        else
            ( cd "$WORKDIR/hotshot" && ./install.sh ) || record_fail "hotshot"
        fi
    fi

    record_manual "Tether (SMS): https://github.com/zackb/tether/releases"
else
    step "Skipping opt-in third-party extras (pass --thirdparty or INSTALL_THIRDPARTY=true to opt in)."
fi

if is_true "$INSTALL_RUST_TOOLS"; then
    step "Building opt-in PINNED Rust utilities (Lan Mouse v0.11.0, Clipp)..."
    apt_install_each cargo build-essential pkg-config libgtk-4-dev libadwaita-1-dev libx11-dev libxtst-dev
    if clone_pinned_repo "lan-mouse" "$WORKDIR/lan-mouse"; then
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] (cd $WORKDIR/lan-mouse && cargo build --release --locked && sudo install -m 755 target/release/lan-mouse /usr/local/bin/lan-mouse)"
        else
            ( cd "$WORKDIR/lan-mouse" && cargo build --release --locked && sudo install -m 755 target/release/lan-mouse /usr/local/bin/lan-mouse ) || record_fail "Lan Mouse"
        fi
    fi
    if clone_pinned_repo "clipp" "$WORKDIR/clipp"; then
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] (cd $WORKDIR/clipp && cargo build --release --locked && sudo install -m 755 target/release/clipp /usr/local/bin/clipp)"
        else
            ( cd "$WORKDIR/clipp" && cargo build --release --locked && sudo install -m 755 target/release/clipp /usr/local/bin/clipp ) || record_fail "Clipp"
        fi
    fi
else
    step "Skipping Rust cargo builds (pass --rust-tools or INSTALL_RUST_TOOLS=true to opt in)."
fi

if is_true "$ENABLE_ACCESSIBILITY"; then
    step "Installing Accessibility stack (Orca VoiceOver-equivalent screen reader, KMagnifier & AT-SPI2)..."
    apt_install_each orca kmag at-spi2-core speech-dispatcher || true
    gset org.gnome.desktop.interface toolkit-accessibility "true"
    echo "      Accessibility ready: toggle Screen Reader (Alt+Super+S) or Zoom Magnifier (Alt+Super+8)."
else
    step "Skipping optional Accessibility extras (pass --accessibility or ENABLE_ACCESSIBILITY=true to opt in)."
fi
