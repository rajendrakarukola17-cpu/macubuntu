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

# Root guard: never touch the system as root. Read-only / zero-side-effect
# modes (--dry-run, --help, -h) are exempt so CI / test harnesses running
# inside containers as root can exercise argument validation.  These flags
# are parsed later, so pre-scan argv here.
_readonly_mode=0
for _arg in "$@"; do
    case "$_arg" in
        --dry-run|-h|--help) _readonly_mode=1; break ;;
    esac
done
if [[ $EUID -eq 0 && $_readonly_mode -ne 1 ]]; then
    echo "❌ Do NOT run as root. Execute as your normal desktop user." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_DIR
PURGE_DATA=0
FORCE_YES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)    export DRY_RUN="1"; shift ;;
        --purge-data) PURGE_DATA=1; shift ;;
        --yes)        FORCE_YES=1; shift ;;
        -h|--help)
            cat <<'EOF'
Usage: ./uninstall.sh [--dry-run] [--purge-data] [--yes]
  --dry-run      Preview rollback steps with zero modifications
  --purge-data   Also delete ~/.config/celina, ~/.local/share/celina, ~/.local/state/celina, ~/.cache/celina, ~/.ai-env
  --yes          Skip interactive confirmation for --purge-data
EOF
            exit 0 ;;
        *) echo "❌ Unknown option: $1" >&2; exit 2 ;;
    esac
done

# shellcheck source=core/common.sh
source "$REPO_DIR/core/common.sh"
export DRY_RUN="${DRY_RUN:-0}"

section "macubuntu Rollback & Uninstall (DRY_RUN=$DRY_RUN | PURGE_DATA=$PURGE_DATA)"

step "Stopping & disabling macubuntu user services..."
# Stop/disable ALL macubuntu/Celina user units, timers, slices & drop-ins
for svc in \
    shairport-sync-user.service uxplay-user.service celina-eventd.service ydotool.service \
    celina.service celina-daemon.service celina-router.service celina-voice.service \
    celina-memory.service celina-npu-inference.service celina-autonomy.service \
    celina-proactive.service celina-self-improve.service celina-self-improve.timer \
    celina-auto-theme.service celina-auto-theme.timer \
    celina-borg-backup.service celina-borg-backup.timer \
    celina-vmtouch.service celina-vmtouch.timer \
    celina-mcp-n8n.service deskflow-user.service \
    celina-ai.slice celina-sandbox.slice local-inference.slice; do
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] systemctl --user disable --now $svc"
    elif command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now "$svc" >/dev/null 2>&1 || true
    fi
    run rm -f "$HOME/.config/systemd/user/$svc"
done
# Remove any remaining *.service.d/ drop-in dirs for Celina units
for dropin in "$HOME/.config/systemd/user/"*.service.d; do
    if [[ -d "$dropin" ]] && [[ "$(basename "$dropin")" == *.service.d ]]; then
        svc_name="$(basename "$dropin" .service.d)"
        case "$svc_name" in
            celina-*|macubuntu-*|scx*|ollama*)
                if [[ "$DRY_RUN" == "1" ]]; then
                    echo "      [dry-run] rm -rf $dropin"
                else
                    run rm -rf "$dropin"
                fi
                ;;
        esac
    fi
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
    "$HOME/.local/bin/celina-dbus" \
    "$HOME/.local/bin/celina-train" \
    "$HOME/.local/bin/celina-turbo" \
    "$HOME/.local/bin/celina-login-save" \
    "$HOME/.local/bin/celina-autonomy" \
    "$HOME/.local/bin/celina-vmtouch-ctl" \
    "$HOME/.local/bin/celina-visual-intelligence" \
    "$HOME/.local/bin/celina-island" \
    "$HOME/.local/bin/celina-voice" \
    "$HOME/.local/bin/celina-spotlight" \
    "$HOME/.local/bin/celina-writing-tools" \
    "$HOME/.local/bin/celina-live-text"; do
    run rm -f "$f"
done

# Remove GNOME extensions installed by macubuntu
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] gnome-extensions uninstall celina-island@macubuntu.org (if installed)"
    echo "      [dry-run] gnome-extensions enable tiling-assistant@ubuntu.com ubuntu-dock@ubuntu.com (if present)"
else
    if command -v gnome-extensions >/dev/null 2>&1; then
        gnome-extensions uninstall celina-island@macubuntu.org >/dev/null 2>&1 || true
        # Re-enable stock Ubuntu extensions if installed
        gnome-extensions enable tiling-assistant@ubuntu.com >/dev/null 2>&1 || true
        gnome-extensions enable ubuntu-dock@ubuntu.com >/dev/null 2>&1 || true
    fi
fi

step "Removing macubuntu system drop-ins (sysctl, zram, oomd, ollama, v4l2loopback, mDNS, safe-sleep, boot, udev)..."
HAD_GRUB_DROPIN=0
[[ -f /etc/default/grub.d/99-macubuntu-boot.cfg ]] && HAD_GRUB_DROPIN=1
[[ -f /etc/default/grub.d/99-celina-rt.cfg ]] && HAD_GRUB_DROPIN=1
for rf in \
    /etc/sysctl.d/99-macubuntu-vm.conf \
    /etc/sysctl.d/99-macubuntu-net.conf \
    /etc/sysctl.d/99-rt.conf \
    /etc/systemd/zram-generator.conf \
    /etc/systemd/oomd.conf.d/99-macubuntu.conf \
    /etc/systemd/journald.conf.d/90-macubuntu.conf \
    /etc/systemd/resolved.conf.d/99-macubuntu-mdns.conf \
    /etc/systemd/resolved.conf.d/90-macubuntu-dnscache.conf \
    /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf \
    /etc/systemd/sleep.conf.d/99-macubuntu-s0ix.conf \
    /etc/systemd/sleep.conf.d/99-macubuntu-lid.conf \
    /etc/systemd/system/ollama.service.d/90-macubuntu-qos.conf \
    /etc/systemd/system/ollama.service.d/hardening.conf \
    /etc/modules-load.d/99-macubuntu-v4l2loopback.conf \
    /etc/modprobe.d/99-macubuntu-v4l2loopback.conf \
    /etc/default/grub.d/99-macubuntu-boot.cfg \
    /etc/default/grub.d/99-celina-rt.cfg \
    /etc/udev/rules.d/60-macubuntu-io-scheduler.rules \
    /etc/udev/rules.d/90-macubuntu-wifi.conf \
    /etc/udev/rules.d/91-macubuntu-epp.rules \
    /etc/udev/rules.d/90-battery-charge-limit.rules \
    /etc/udev/rules.d/80-uinput.rules \
    /etc/keyd/macubuntu.conf \
    /etc/keyd/app.conf \
    /etc/apparmor.d/bwrap-celina \
    /etc/polkit-1/rules.d/99-scx-loader.rules \
    /etc/dbus-1/system.d/org.scx.Loader.conf \
    /etc/scx_loader/config.toml \
    /etc/apt/preferences.d/99-macubuntu-block-snapd \
    /etc/apt/preferences.d/99-macubuntu-mozilla-firefox \
    /etc/apt/preferences.d/99-macubuntu-snapper \
    /etc/macubuntu/ \
    /etc/systemd/system/macubuntu-hw-probe.service \
    /etc/systemd/system/celina-ebpf-sense.service; do
    run sudo rm -f "$rf"
done
# Remove macubuntu sysext if active
if [[ "$DRY_RUN" != "1" ]] && command -v systemd-sysext >/dev/null 2>&1; then
    if systemd-sysext status 2>/dev/null | grep -q "macubuntu"; then
        echo "      ℹ️  Unmerging macubuntu sysext (revert to stock Ubuntu)..."
        sudo bash "$REPO_DIR/system/sysext/unmerge-sysext.sh" 2>/dev/null || true
    fi
fi
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] restart keyd/systemd-resolved if active; update-grub if boot drop-in removed; reload sysctl & systemd"
else
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet keyd.service 2>/dev/null; then
        sudo systemctl reload-or-restart keyd.service >/dev/null 2>&1 || true
    fi
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet systemd-resolved 2>/dev/null; then
        sudo systemctl reload-or-restart systemd-resolved >/dev/null 2>&1 || true
    fi
    if [[ "$HAD_GRUB_DROPIN" -eq 1 ]] && command -v update-grub >/dev/null 2>&1; then
        if ! sudo update-grub >/dev/null 2>&1; then
            echo "⚠️  update-grub failed after removing GRUB drop-in(s) — manual fix may be needed" >&2
        fi
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

# Strip MACUBUNTU-PERF block from Firefox user.js
for ff_uc in "$HOME"/.mozilla/firefox/*/user.js; do
    if [[ -f "$ff_uc" ]] && grep -q "MACUBUNTU-PERF" "$ff_uc" 2>/dev/null; then
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] would strip MACUBUNTU-PERF block from $ff_uc"
        else
            python3 - "$ff_uc" <<'PY'
import sys, re
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
text = re.sub(r"\n?// --- MACUBUNTU-PERF ---[\s\S]*?// --- END MACUBUNTU-PERF ---\n?", "\n", text)
open(path, "w", encoding="utf-8").write(text)
PY
            echo "      ✅ Stripped MACUBUNTU-PERF block from $ff_uc"
        fi
    fi
done
run sudo rm -f /etc/firefox/syspref.js/99-macubuntu-vaapi.conf /etc/chromium-browser/customizations/00-chromium-flags.conf 2>/dev/null || true
run rm -f "$HOME/.local/share/applications/libreoffice-uno.desktop"

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

# Require confirmation for --purge-data (unless --yes or DRY_RUN=1)
if [[ "$PURGE_DATA" -eq 1 ]]; then
    if [[ "$FORCE_YES" -ne 1 && "${DRY_RUN:-0}" != "1" ]]; then
        echo ""
        echo "⚠️  WARNING: --purge-data will PERMANENTLY DELETE:"
        echo "     ~/.config/celina      (Celina configs, model digests, secrets)"
        echo "     ~/.local/share/celina (skills, voices, vault, DBs)"
        echo "     ~/.local/state/celina (Celina runtime state)"
        echo "     ~/.cache/celina       (multi-GB model caches)"
        echo "     ~/.ai-env             (headless API key fallback)"
        echo ""
        read -r -p "Type YES to confirm: " confirm
        if [[ "$confirm" != "YES" ]]; then
            echo "❌ Purge aborted by user."
            exit 0
        fi
    fi
    step "Purging personal Celina configs, databases, skills, voices & Tier B research sandbox (--purge-data)..."
    run rm -rf "$HOME/.config/celina" "$HOME/.local/share/celina" "$HOME/.local/state/celina" "$HOME/.cache/celina" "$HOME/.ai-env"
else
    echo "      Preserved ~/.config/celina and ~/.local/share/celina (pass --purge-data to delete)."
fi

echo ""
echo "✅ macubuntu rollback complete (DRY_RUN=$DRY_RUN)."
