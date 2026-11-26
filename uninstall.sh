#!/usr/bin/env bash
# ============================================================================
# macubuntu Rollback & Uninstall Utility
# Safely reverts macubuntu system drop-ins, user services, autostart entries,
# launchers, and GNOME tweaks while preserving personal memory/config data
# unless --purge-data is explicitly supplied.
#
# Usage:
#   ./uninstall.sh --dry-run          # Preview rollback with zero mutations
#   ./uninstall.sh                    # Roll back system & app integration (keeps ~/.config/celina)
#   ./uninstall.sh --purge-data       # Full removal including ~/.config/celina & ~/.local/share/celina
# ============================================================================
set -Eeuo pipefail
umask 077

if [[ $EUID -eq 0 ]]; then
    echo "❌ Do NOT run as root. Execute as your normal desktop user." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_DIR
PURGE_DATA=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)    export DRY_RUN="1"; shift ;;
        --purge-data) PURGE_DATA=1; shift ;;
        -h|--help)
            cat <<'EOF'
Usage: ./uninstall.sh [--dry-run] [--purge-data]
  --dry-run      Preview rollback steps with zero modifications
  --purge-data   Also delete ~/.config/celina and ~/.local/share/celina (DBs, skills, voices, env)
EOF
            exit 0 ;;
        *) echo "❌ Unknown option: $1" >&2; exit 1 ;;
    esac
done

# shellcheck source=core/common.sh
source "$REPO_DIR/core/common.sh"
export DRY_RUN="${DRY_RUN:-0}"

section "macubuntu Rollback & Uninstall (DRY_RUN=$DRY_RUN | PURGE_DATA=$PURGE_DATA)"

step "Stopping & disabling macubuntu user services..."
for svc in shairport-sync-user.service uxplay-user.service celina-eventd.service ydotoold.service; do
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] systemctl --user disable --now $svc"
    elif command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now "$svc" >/dev/null 2>&1 || true
    fi
    run rm -f "$HOME/.config/systemd/user/$svc"
done
if [[ "$DRY_RUN" != "1" ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

step "Removing autostart entries, WirePlumber/PipeWire audio tuning, Nautilus Quick Actions & Celina launchers..."
for f in \
    "$HOME/.config/autostart/celina-island.desktop" \
    "$HOME/.config/autostart/org.localsend.localsend_app.desktop" \
    "$HOME/.config/environment.d/90-macubuntu-smooth.conf" \
    "$HOME/.config/keyd/app.conf" \
    "$HOME/.config/rofi-spotlight.rasi" \
    "$HOME/.config/wireplumber/wireplumber.conf.d/51-macubuntu-bluetooth.conf" \
    "$HOME/.config/pipewire/pipewire.conf.d/99-macubuntu-audio.conf" \
    "$HOME/.local/share/nautilus-python/extensions/celina_quick_actions.py" \
    "$HOME/.local/bin/celina-cli" \
    "$HOME/.local/bin/celina" \
    "$HOME/.local/bin/celly" \
    "$HOME/.local/bin/celina-island" \
    "$HOME/.local/bin/celina-voice" \
    "$HOME/.local/bin/celina-spotlight" \
    "$HOME/.local/bin/celina-writing-tools" \
    "$HOME/.local/bin/celina-live-text"; do
    run rm -f "$f"
done

step "Removing macubuntu system drop-ins (sysctl, zram, oomd, ollama, v4l2loopback, mDNS, safe-sleep, boot, udev)..."
HAD_GRUB_DROPIN=0
[[ -f /etc/default/grub.d/99-macubuntu-boot.cfg ]] && HAD_GRUB_DROPIN=1
for rf in \
    /etc/sysctl.d/99-macubuntu-vm.conf \
    /etc/systemd/zram-generator.conf \
    /etc/systemd/oomd.conf.d/99-macubuntu.conf \
    /etc/systemd/resolved.conf.d/99-macubuntu-mdns.conf \
    /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf \
    /etc/systemd/system/ollama.service.d/8gb-ram.conf \
    /etc/systemd/system/ollama.service.d/amd-vulkan.conf \
    /etc/modules-load.d/99-macubuntu-v4l2loopback.conf \
    /etc/modprobe.d/99-macubuntu-v4l2loopback.conf \
    /etc/default/grub.d/99-macubuntu-boot.cfg \
    /etc/udev/rules.d/60-macubuntu-io-scheduler.rules \
    /etc/udev/rules.d/90-battery-charge-limit.rules \
    /etc/udev/rules.d/80-celina-uinput.rules \
    /etc/keyd/macubuntu.conf; do
    run sudo rm -f "$rf"
done
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] restart keyd/systemd-resolved if active; update-grub if boot drop-in removed; restore /etc/fstab.macubuntu.bak if present, reload sysctl & systemd"
else
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet keyd.service 2>/dev/null; then
        sudo systemctl reload-or-restart keyd.service >/dev/null 2>&1 || true
    fi
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet systemd-resolved 2>/dev/null; then
        sudo systemctl reload-or-restart systemd-resolved >/dev/null 2>&1 || true
    fi
    if [[ "$HAD_GRUB_DROPIN" -eq 1 ]] && command -v update-grub >/dev/null 2>&1; then
        sudo update-grub >/dev/null 2>&1 || true
    fi
    if [[ -f /etc/fstab.macubuntu.bak ]]; then
        echo "      Found /etc/fstab.macubuntu.bak — restore manually if you wish to re-enable disk swap."
    fi
    sudo sysctl --system >/dev/null 2>&1 || true
    sudo systemctl daemon-reload >/dev/null 2>&1 || true
fi

step "Reverting Safari Firefox theme, compass icon & desktop overrides (if installed)..."
if [[ "$DRY_RUN" == "1" ]]; then
    python3 "$REPO_DIR/system/firefox/apply-safari-theme.py" --revert --dry-run || true
else
    python3 "$REPO_DIR/system/firefox/apply-safari-theme.py" --revert || true
fi

step "Resetting GNOME interface themes & keybindings to Ubuntu defaults..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] gsettings reset gtk-theme, icon-theme, cursor-theme, button-layout, switch-input-source"
    echo "      [dry-run] unmask Tracker3/LocalSearch user units"
else
    if command -v gsettings >/dev/null 2>&1; then
        gsettings reset org.gnome.desktop.interface gtk-theme 2>/dev/null || true
        gsettings reset org.gnome.desktop.interface icon-theme 2>/dev/null || true
        gsettings reset org.gnome.desktop.interface cursor-theme 2>/dev/null || true
        gsettings reset org.gnome.desktop.wm.preferences button-layout 2>/dev/null || true
        gsettings reset org.gnome.desktop.wm.preferences resize-with-right-button 2>/dev/null || true
        gsettings reset org.gnome.desktop.wm.keybindings switch-input-source 2>/dev/null || true
        gsettings reset org.gnome.desktop.peripherals.touchpad click-method 2>/dev/null || true
        gsettings reset org.gnome.mutter center-new-windows 2>/dev/null || true
        gsettings reset org.gnome.mutter experimental-features 2>/dev/null || true
    fi
    if command -v dconf >/dev/null 2>&1; then
        dconf reset -f /org/gnome/shell/extensions/just-perfection/ 2>/dev/null || true
        dconf reset -f /org/gnome/shell/extensions/blur-my-shell/ 2>/dev/null || true
    fi
    if command -v systemctl >/dev/null 2>&1; then
        for svc in localsearch-3.service localsearch-control-3.service \
                   tracker-miner-fs-3.service tracker-miner-fs-control-3.service \
                   tracker-extract-3.service; do
            systemctl --user unmask "$svc" >/dev/null 2>&1 || true
        done
    fi
fi

step "Removing Celina runtime code, research venv & theme cache..."
run rm -rf \
    "$HOME/.local/share/celina/app" \
    "$HOME/.local/share/celina/venv" \
    "$HOME/.local/share/celina/apple-research/venv" \
    "$HOME/.cache/macubuntu_themes"

if [[ "$PURGE_DATA" -eq 1 ]]; then
    step "Purging personal Celina configs, databases, skills, voices & Tier B research sandbox (--purge-data)..."
    run rm -rf "$HOME/.config/celina" "$HOME/.local/share/celina" "$HOME/.local/state/macubuntu"
else
    echo "      Preserved ~/.config/celina and ~/.local/share/celina (pass --purge-data to delete)."
fi

echo ""
echo "✅ macubuntu rollback complete (DRY_RUN=$DRY_RUN)."
