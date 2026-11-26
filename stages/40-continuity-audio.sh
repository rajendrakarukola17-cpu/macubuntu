#!/usr/bin/env bash
# ============================================================================
# Stage 40: All-System Continuity & Audio Hub
# Covers:
#   - iPhone / iPad / Mac : LocalSend (AirDrop), Shairport-Sync (AirPlay Audio),
#                           UxPlay (AirPlay 2 Video+Audio Screen Mirroring)
#   - Android             : GSConnect (Notifications/SMS/Clipboard/Battery) + scrcpy (60fps Mirror)
#   - Windows / Linux/Mac : Deskflow (Wayland libei/TLS Universal Control) + Syncthing (E2EE Sync)
#   - Sidecar 2nd Display : GNOME Remote Desktop (RDP/Virtual Monitor) capability check
#   - Security            : LAN-only UFW firewall rules (never exposed to WAN)
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[40] All-System Continuity Hub (iOS/Mac, Android, Windows, Linux & Audio)"

install_flatpak org.freedesktop.Helvum
install_flatpak org.localsend.localsend_app

step "Configuring LocalSend autostart for cross-OS AirDrop-style receiving..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] add org.localsend.localsend_app.desktop to ~/.config/autostart/"
else
    ensure_dir "$HOME/.config/autostart"
    for candidate in \
        "$HOME/.local/share/flatpak/exports/share/applications/org.localsend.localsend_app.desktop" \
        "/var/lib/flatpak/exports/share/applications/org.localsend.localsend_app.desktop"; do
        if [[ -f "$candidate" ]]; then
            copy_user_file "$candidate" "$HOME/.config/autostart/org.localsend.localsend_app.desktop" 644
            echo "      LocalSend added to ~/.config/autostart/."
            break
        fi
    done
fi

step "Installing GSConnect (Android + KDE Connect iOS Clipboard, Trackpad, Notifications & Battery)..."
apt_install gnome-shell-extension-gsconnect || true
install_ext "gsconnect@andyholmes.github.io" "GSConnect"
echo "      Tip: Install the open-source 'KDE Connect' iOS app to pair iPhone/iPad with GSConnect!"

if is_true "$ENABLE_ANDROID_MIRROR"; then
    step "Installing scrcpy + adb (Genymobile Apache-2.0 — 60fps USB/Wireless Android Mirroring)..."
    apt_install_each scrcpy adb || record_manual "Install scrcpy manually (apt install scrcpy adb)"
fi

step "Configuring per-user Shairport-Sync AirPlay Audio receiver (PipeWire/PA auto-detect)..."
apt_install shairport-sync || record_manual "Install shairport-sync manually"
systemctl_disable shairport-sync

AUDIO_BACKEND="$(detect_shairport_audio_backend)"
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] deploy ~/.config/systemd/user/shairport-sync-user.service (-o $AUDIO_BACKEND) and enable"
else
    ensure_dir "$HOME/.config/systemd/user"
    sed "s/@AUDIO_BACKEND@/${AUDIO_BACKEND}/g" \
        "$REPO_DIR/system/systemd/shairport-sync-user.service" \
        | write_user_file "$HOME/.config/systemd/user/shairport-sync-user.service" 644
    systemctl_user_enable shairport-sync-user.service \
        || record_manual "Start AirPlay Audio after login: systemctl --user enable --now shairport-sync-user"
fi

if is_true "$ENABLE_AIRPLAY_VIDEO"; then
    step "Installing UxPlay (FDH2/UxPlay GPL-3.0 — AirPlay 2 Video+Audio Screen Mirroring)..."
    if apt_has uxplay; then
        apt_install uxplay avahi-daemon || record_manual "Install uxplay manually for AirPlay Video mirroring"
        copy_user_file "$REPO_DIR/system/systemd/uxplay-user.service" "$HOME/.config/systemd/user/uxplay-user.service" 644
        echo "      Installed uxplay-user.service (start on demand: systemctl --user start uxplay-user, or via Celina)."
    else
        record_manual "UxPlay not in APT; pinned source available in system/download-locks.sha256 (FDH2/UxPlay v1.73.7)"
    fi
fi

if is_true "$ENABLE_SYNCTHING"; then
    step "Installing Syncthing (MPL-2.0 — E2EE Cross-OS File Sync across Linux/Android/iOS/Windows/macOS)..."
    apt_install syncthing || record_manual "Install syncthing manually"
    systemctl_user_enable syncthing.service \
        || record_manual "Enable Syncthing after login: systemctl --user enable --now syncthing"
fi

if is_true "$ENABLE_UNIVERSAL_CONTROL"; then
    step "Installing Deskflow (GPL-2.0 — Universal Control across Linux/Windows/macOS over Wayland libei/TLS)..."
    install_flatpak org.deskflow.deskflow
fi

step "Installing GNOME Remote Desktop (Sidecar), AirScan, USB iPhone Sync (libimobiledevice/ifuse) & Continuity Camera (v4l2loopback)..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] ensure gnome-remote-desktop, sane-airscan, usbmuxd, libimobiledevice-utils, ifuse, gvfs-backends, v4l2loopback-dkms, v4l2loopback-utils & mokutil are installed"
else
    apt_install_each \
        gnome-remote-desktop sane-airscan \
        usbmuxd libimobiledevice-1.0-6 libimobiledevice6 libimobiledevice-utils \
        ifuse gvfs-backends v4l2loopback-dkms v4l2loopback-utils mokutil || true
fi

if is_true "$ENABLE_PERSISTENT_CONTINUITY_CAM"; then
    step "Configuring persistent /dev/video10 v4l2loopback kernel module for zero-setup iPhone Continuity Camera..."
    copy_root_file "$REPO_DIR/system/modprobe/v4l2loopback-load.conf" /etc/modules-load.d/99-macubuntu-v4l2loopback.conf
    copy_root_file "$REPO_DIR/system/modprobe/v4l2loopback.conf" /etc/modprobe.d/99-macubuntu-v4l2loopback.conf
    if [[ "$DRY_RUN" != "1" ]] && [[ ! -e /dev/video10 ]] && modinfo v4l2loopback >/dev/null 2>&1; then
        mod_err="$(sudo modprobe v4l2loopback video_nr=10 card_label="iPhone Continuity Camera" exclusive_caps=1 max_buffers=2 2>&1 || true)"
        if [[ ! -e /dev/video10 ]] && grep -qiE "Key was rejected|Required key not available|Operation not permitted" <<<"$mod_err"; then
            echo "    ⚠️  Secure Boot blocked unsigned/unenrolled v4l2loopback DKMS module ($mod_err)."
            echo "       Keep Secure Boot ENABLED and enroll Ubuntu's DKMS MOK once:"
            echo "         ./tools/enroll-mok.sh"
            echo "       Then reboot, accept 'Enroll MOK', and verify with: ./tools/enroll-mok.sh --verify"
        fi
    fi
fi

step "Eliminating mDNS UDP 5353 conflicts (systemd-resolved MulticastDNS=no -> Avahi owns .local cleanly)..."
copy_root_file "$REPO_DIR/system/systemd/resolved-mdns.conf" /etc/systemd/resolved.conf.d/99-macubuntu-mdns.conf
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] reload systemd-resolved & ensure avahi-daemon is enabled for AirPlay/LocalSend/AirScan"
else
    systemctl_enable avahi-daemon || true
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet systemd-resolved 2>/dev/null; then
        run sudo systemctl reload-or-restart systemd-resolved 2>/dev/null || true
    fi
fi

if is_true "$ENABLE_PRO_AUDIO_BLUETOOTH"; then
    step "Deploying WirePlumber AirPods/Bluetooth AAC/LDAC/SBC-XQ & CoreAudio-style low-jitter PipeWire clock..."
    ensure_dir "$HOME/.config/wireplumber/wireplumber.conf.d" "$HOME/.config/pipewire/pipewire.conf.d"
    copy_user_file \
        "$REPO_DIR/system/wireplumber/51-macubuntu-bluetooth.conf" \
        "$HOME/.config/wireplumber/wireplumber.conf.d/51-macubuntu-bluetooth.conf" 644
    copy_user_file \
        "$REPO_DIR/system/pipewire/99-macubuntu-audio.conf" \
        "$HOME/.config/pipewire/pipewire.conf.d/99-macubuntu-audio.conf" 644
    if [[ "$DRY_RUN" != "1" ]] && command -v systemctl >/dev/null 2>&1; then
        systemctl --user try-restart pipewire.service wireplumber.service >/dev/null 2>&1 || true
    fi
fi

step "Checking UFW firewall (LAN-only continuity rules — never exposed to WAN)..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] if UFW active, allow GSConnect/AirPlay/UxPlay/LocalSend/Syncthing/Deskflow/Weylus strictly from LAN CIDR"
elif command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
    CIDR="$(detect_lan_cidr || true)"
    if [[ -n "$CIDR" ]]; then
        echo "      Applying UFW continuity rules scoped strictly to $CIDR"
        for rule in \
            "1716:1764 proto tcp" "1716 proto udp" \
            "1701:1702 proto tcp" \
            "5000 proto tcp" "7000:7001 proto tcp" "7011 proto udp" \
            "6000:6011 proto udp" "5353 proto udp" \
            "53317 proto tcp" "53317 proto udp" \
            "22000 proto tcp" "22000 proto udp" "21027 proto udp" \
            "24800 proto tcp"; do
            # shellcheck disable=SC2086
            run sudo ufw allow from "$CIDR" to any port $rule || record_fail "ufw $rule"
        done
    else
        record_manual "UFW is active but no private LAN subnet was detected; add LAN continuity rules manually"
    fi
else
    echo "      UFW not active; skipping firewall rules."
fi
