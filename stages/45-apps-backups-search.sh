#!/usr/bin/env bash
# ============================================================================
# Stage 45: Launcher, Finder Tools, Backups, Key Remap, Accessibility & Search
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[45] Launcher ($LAUNCHER_CHOICE), Finder Tools, Backups & Search ($SEARCH_BACKEND)"

step "Configuring desktop launcher ($LAUNCHER_CHOICE)..."
case "$LAUNCHER_CHOICE" in
    celina-spotlight)
        echo "      Unified Spotlight bridge (Super+Space) deployed by features/celina." ;;
    ulauncher)
        if apt_has ulauncher; then
            apt_install ulauncher || record_fail "Ulauncher"
        elif is_true "$INSTALL_THIRDPARTY"; then
            run sudo add-apt-repository -y ppa:agornostal/ulauncher
            run sudo apt-get update
            apt_install ulauncher || record_fail "Ulauncher (PPA)"
        else
            record_manual "Ulauncher not in default Ubuntu repo; re-run with --thirdparty for its PPA, or use LAUNCHER_CHOICE=celina-spotlight"
        fi ;;
    gnome)
        echo "      Keeping native GNOME Overview search." ;;
    *)
        record_fail "Unknown LAUNCHER_CHOICE=$LAUNCHER_CHOICE" ;;
esac

step "Installing Quick Look (gnome-sushi + PDF/HEIC/WebP/video thumbnailers), Timeshift, BorgBackup, Mission Center & Pika Backup..."
apt_install_each gnome-sushi evince-thumbnailer ffmpegthumbnailer heif-gdk-pixbuf heif-thumbnailer webp-pixbuf-loader borgbackup
# timeshift is owned solely by Stage 61 (recovery-polish) — installed there btrfs-only.
install_flatpak io.missioncenter.MissionCenter
install_flatpak org.gnome.World.PikaBackup
if ! command -v frostfinder >/dev/null 2>&1; then
    record_manual "Optional Finder Column-View upgrade: install FrostFinder (Tauri column view, F3 dual-pane, Quick Look, xattr+SQLite tags) — Celina FinderTagStore shares user.xdg.tags + SQLite fallback"
fi

if is_true "$ENABLE_FLATSEAL"; then
    step "Installing Flatseal (tchx84/Flatseal GPL-3.0 — macOS TCC-style per-app privacy controls)..."
    install_flatpak com.github.tchx84.Flatseal
fi

if is_true "$ENABLE_KEEPASSXC"; then
    step "Installing KeePassXC (FIDO2 + Secret Service + Browser Passkeys local vault)..."
    apt_install keepassxc || true
fi

if is_true "$ENABLE_DISTROBOX"; then
    step "Installing Distrobox (89luca89/distrobox GPL-3.0 — seamless multi-distro dev containers)..."
    apt_install_each podman distrobox || true
fi

if is_true "$ENABLE_NAUTILUS_QUICK_ACTIONS"; then
    step "Installing Finder-style Nautilus Quick Actions (Summarize / Convert to PDF / LocalSend)..."
    apt_install python3-nautilus || true
    copy_user_file \
        "$REPO_DIR/system/nautilus/celina_quick_actions.py" \
        "$HOME/.local/share/nautilus-python/extensions/celina_quick_actions.py" \
        644
fi

if is_true "$ENABLE_CELINA"; then
    step "Configuring LibreOffice headless UNO bridge autostart (~/.config/autostart/libreoffice-uno.desktop)..."
    write_user_file "$HOME/.config/autostart/libreoffice-uno.desktop" 644 <<'EOF'
[Desktop Entry]
Type=Application
Name=LibreOffice UNO Bridge (Celina)
Comment=Headless LibreOffice UNO socket on localhost:2002 for structured document editing
Exec=libreoffice --headless --accept="socket,host=localhost,port=2002;urp;"
OnlyShowIn=GNOME;
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] sudo timeshift --check (root fs: $(detect_root_fstype); installs grub-btrfs if root is btrfs)"
else
    ROOT_FS="$(detect_root_fstype)"
    if [[ "$ROOT_FS" == "btrfs" ]]; then
        step "Btrfs root filesystem detected — enabling APFS-style instant snapshot boot integration (grub-btrfs)..."
        if apt_has grub-btrfs; then
            apt_install grub-btrfs || true
        fi
        record_manual "Btrfs APFS-style snapshots ready: open Timeshift -> select BTRFS mode for instant zero-space OS rollback snapshots"
    fi
    if command -v timeshift >/dev/null 2>&1; then
        run sudo timeshift --check >/dev/null 2>&1 || true
    fi
fi

# Snapper: APFS-style btrfs pre/post snapshots with GRUB bootable rollback
if is_true "$ENABLE_BTRFS_SNAPPER"; then
    step "Deploying Snapper (APFS-style btrfs pre/post snapshots + GRUB rollback)..."
    ROOT_FS="$(detect_root_fstype)"
    if [[ "$ROOT_FS" != "btrfs" ]]; then
        log_info "Snapper requires btrfs root; current root is ${ROOT_FS}. Skipping."
        record_manual "To enable Snapper: reinstall on btrfs root with subvolumes @, @home, @var_log, @var_cache"
    elif apt_has snapper; then
        apt_install snapper || record_fail "apt: snapper"
        # Create snapper config directory and copy default config
        run sudo mkdir -p /etc/snapper/configs
        copy_root_file "$REPO_DIR/system/snapper/configs/root" /etc/snapper/configs/root 644
        # Install apt dpkg pre/post hook for automatic snapshots around package changes
        copy_root_file "$REPO_DIR/system/snapper/apt-dpkg-hook" /etc/apt/apt.conf.d/99-macubuntu-snapper 644
        # Install macubuntu stage wrapper for pre/post snapshots around stage installs
        run sudo install -Dm 755 "$REPO_DIR/system/snapper/pre-post-hooks/macubuntu-stage-wrapper" \
            /usr/local/bin/macubuntu-stage-wrapper
        # Enable snapper-timeline for automated time-based snapshots
        if [[ "$DRY_RUN" != "1" ]]; then
            sudo systemctl enable --now snapper-timeline.timer 2>/dev/null || \
                record_manual "Enable snapper-timeline.timer manually: sudo systemctl enable --now snapper-timeline.timer"
        fi
        echo "      ✅ Snapper installed — pre/post snapshots around apt + stage installs"
        record_manual "Create first snapshot: sudo snapper create --description 'macubuntu baseline' --cleanup-algorithm number"
        record_manual "List snapshots: sudo snapper list | rollback: sudo snapper rollback <number>"
    else
        record_manual "snapper not in APT; add pinned package to download-locks.sha256"
    fi

    # --- Btrfs subvolume detection (@, @home, @var_log, @var_cache) ---
    step "Detecting btrfs subvolumes for Snapper layout..."
    if [[ "$ROOT_FS" == "btrfs" ]]; then
        for subvol in @ @home @var_log @var_cache; do
            if btrfs subvolume show "/$subvol" >/dev/null 2>&1; then
                echo "      ✅ Btrfs subvolume found: /$subvol"
            else
                echo "      ℹ️  Btrfs subvolume not found: /$subvol (Snapper will use default subvolume layout)"
            fi
        done
    fi

    # --- chattr +C (nodatacow) on ~/.cache/celina/models ---
    step "Applying nodatacow (+C) to Celina model cache directory..."
    if [[ "$ROOT_FS" == "btrfs" ]]; then
        MODEL_CACHE_DIR="$HOME/.cache/celina/models"
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] would chattr +C $MODEL_CACHE_DIR"
        else
            mkdir -p "$MODEL_CACHE_DIR"
            if chattr +C "$MODEL_CACHE_DIR" 2>/dev/null; then
                echo "      ✅ nodatacow set on $MODEL_CACHE_DIR"
            else
                echo "      ⚠️  chattr +C failed on $MODEL_CACHE_DIR (may already be set or unsupported)"
            fi
        fi
    else
        echo "      ℹ️  chattr +C skipped (not on btrfs root)"
    fi
else
    echo "    ℹ️  Snapper skipped (ENABLE_BTRFS_SNAPPER=false)"
fi

step "Installing pinned macOS keyboard remap repo & accessibility tools..."
apt_install_each autokey-gtk orca speech-dispatcher kmag
REMAP_DIR="$HOME/.local/src/gnome-macos-remap-wayland"
ensure_dir "$HOME/.local/src"
clone_pinned_repo "gnome-macos-remap-wayland" "$REMAP_DIR" \
    || record_manual "Keyboard remap: https://github.com/petrstepanov/gnome-macos-remap-wayland"
record_manual "Optional Cmd-style keyboard remap: review $REMAP_DIR/README.md before applying its dconf load"
record_manual "Note: AutoKey applies to X11/XWayland apps, not native Wayland surfaces"

step "Configuring search backend: $SEARCH_BACKEND"
case "$SEARCH_BACKEND" in
    lightweight)
        apt_install_each fd-find ripgrep
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] mask Tracker3 / LocalSearch indexers to keep idle RAM low on 8GB machines"
        elif command -v systemctl >/dev/null 2>&1; then
            for svc in localsearch-3.service localsearch-control-3.service \
                       tracker-miner-fs-3.service tracker-miner-fs-control-3.service \
                       tracker-extract-3.service; do
                systemctl --user mask --now "$svc" >/dev/null 2>&1 || true
            done
        fi ;;
    gnome-indexer)
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] unmask Tracker3 / LocalSearch & install localsearch + recoll"
        elif command -v systemctl >/dev/null 2>&1; then
            for svc in localsearch-3.service localsearch-control-3.service \
                       tracker-miner-fs-3.service tracker-miner-fs-control-3.service; do
                systemctl --user unmask "$svc" >/dev/null 2>&1 || true
            done
        fi
        apt_install_first "GNOME Search Indexer" localsearch tracker-miner-fs
        apt_install recoll || true ;;
    *)
        record_fail "Unknown SEARCH_BACKEND=$SEARCH_BACKEND (expected lightweight|gnome-indexer)" ;;
esac
