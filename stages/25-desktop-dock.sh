#!/usr/bin/env bash
# ============================================================================
# Stage 25: Desktop Extensions, Floating Dock, Desktop Icons, Polish & Shortcuts
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[25] Desktop Extensions, Dock ($DOCK_CHOICE), Polish & Shortcuts"

step "Installing core macOS-feel GNOME Shell extensions..."
install_ext "blur-my-shell@aunetx"                    "Blur My Shell"
install_ext "tiling-assistant@leleat-on-github"       "Tiling Assistant"
install_ext "just-perfection-desktop@just-perfection" "Just Perfection"
install_ext "gesture-improvements@gesture-extension"  "Gesture Improvements"

if is_true "$ENABLE_DYNAMIC_ISLAND_EXT" && ! is_true "$ENABLE_CELINA"; then
    install_ext "dynamic-island@basskibo" "Dynamic Island (experimental GNOME ext)"
else
    step "Skipping dynamic-island@basskibo (built-in Celina Dynamic Island owns top-center pill)."
fi

step "Configuring dock backend: $DOCK_CHOICE"
case "$DOCK_CHOICE" in
    ubuntu-dock)
        echo "      Keeping Ubuntu Dock with floating macOS bottom-dock settings." ;;
    dash-to-dock)
        apt_install gnome-shell-extension-dash-to-dock || record_fail "Dash to Dock"
        run gnome-extensions disable ubuntu-dock@ubuntu.com >/dev/null 2>&1 || true
        install_ext "dash-to-dock@micxgx.gmail.com" "Dash to Dock" ;;
    simple-taskbar)
        run gnome-extensions disable ubuntu-dock@ubuntu.com >/dev/null 2>&1 || true
        record_manual "Simple Taskbar: install from Extension Manager (verify GNOME Shell version support)" ;;
    none)
        echo "      No dock changes requested." ;;
    *)
        record_fail "Unknown DOCK_CHOICE=$DOCK_CHOICE" ;;
esac

if [[ "$DOCK_CHOICE" == "ubuntu-dock" || "$DOCK_CHOICE" == "dash-to-dock" ]]; then
    S="org.gnome.shell.extensions.dash-to-dock"
    gset "$S" dock-position "'BOTTOM'"
    gset "$S" dock-fixed false
    gset "$S" extend-height false
    gset "$S" autohide true
    gset "$S" intellihide true
    gset "$S" show-trash true
    gset "$S" show-mounts true
    gset "$S" transparency-mode "'FIXED'"
    gset "$S" background-opacity 0.35
    gset "$S" animation-time 0.16
    gset "$S" show-delay 0.08
    gset "$S" hide-delay 0.15
    gset "$S" pressure-threshold 60.0
    gset "$S" click-action "'minimize-or-previews'"
    gset "$S" scroll-action "'cycle-windows'"
fi

if is_true "$ENABLE_CLIPBOARD_HISTORY"; then
    install_ext "clipboard-indicator@tudmotu.com" "Clipboard History Indicator (macOS Tahoe Clipboard)"
    # Privacy-hardened defaults: keep history bounded & cache favorites only (no plaintext secrets across reboots)
    gset org.gnome.shell.extensions.clipboard-indicator history-size 25
    gset org.gnome.shell.extensions.clipboard-indicator cache-only-favorites true
fi

if is_true "$ENABLE_DESKTOP_ICONS"; then
    apt_install gnome-shell-extension-desktop-icons-ng || true
    install_ext "ding@rastersoft.com" "Desktop Icons NG"
fi

step "Applying macOS desktop behavior, persistent Spaces, Tiling Assistant & unified accent color..."
gset org.gnome.desktop.wm.preferences button-layout "'close,minimize,maximize:'"
gset org.gnome.desktop.wm.preferences audible-bell false
# Persistent macOS Spaces spatial memory (4 fixed workspaces instead of forgetting dynamic ones)
gset org.gnome.mutter dynamic-workspaces false
gset org.gnome.desktop.wm.preferences num-workspaces 4
# Mission Control keyboard contract (Ctrl+Up + Super+S)
gset org.gnome.desktop.wm.keybindings toggle-overview "['<Super>s', '<Control>Up']"
# Tiling Assistant predictability (active screen edges + restore size on unsnap)
gset org.gnome.shell.extensions.tiling-assistant active-screen-edges true
gset org.gnome.shell.extensions.tiling-assistant restore-window-size-on-unsnap true
gset org.gnome.shell.extensions.tiling-assistant enable-tiling-popup true
# GNOME 47+/48+/50 unified macOS blue accent color
gset org.gnome.desktop.interface accent-color "'blue'"
gset org.gnome.desktop.interface enable-hot-corners true
gset org.gnome.desktop.interface locate-pointer true
gset org.gnome.desktop.interface enable-animations true
gset org.gnome.desktop.interface color-scheme "'prefer-dark'"
gset org.gnome.desktop.peripherals.touchpad natural-scroll true
gset org.gnome.desktop.peripherals.mouse natural-scroll true
gset org.gnome.desktop.peripherals.touchpad tap-to-click true
gset org.gnome.desktop.peripherals.touchpad disable-while-typing true
gset org.gnome.settings-daemon.plugins.color night-light-enabled true
gset org.gnome.desktop.datetime automatic-timezone true
gset org.gnome.software download-updates true
record_manual "Optional macOS top-bar & window polish (GNOME 48+): Stage-Manager-for-Linux, Global Menu / AppMenu, Logo Menu (logomenu@aryan_k), Magic Lamp (compiz-alike-magic-lamp-effect)"

step "Configuring Ubuntu top bar style (TOP_PANEL_STYLE=${TOP_PANEL_STYLE}), snappy animations (ANIMATION_SPEED=${ANIMATION_SPEED}) & low-overdraw GPU blur..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] apply dconf Just Perfection + Blur My Shell settings for TOP_PANEL_STYLE=${TOP_PANEL_STYLE}, ANIMATION_SPEED=${ANIMATION_SPEED}"
elif command -v dconf >/dev/null 2>&1; then
    case "${TOP_PANEL_STYLE:-transparent}" in
        hidden)
            # Completely remove Ubuntu's top bar on the desktop (still visible in Super Overview)
            dconf write /org/gnome/shell/extensions/just-perfection/panel false || true
            dconf write /org/gnome/shell/extensions/just-perfection/panel-in-overview true || true
            dconf write /org/gnome/shell/extensions/just-perfection/panel-corner-size 1 || true
            dconf write /org/gnome/shell/extensions/blur-my-shell/panel/blur false || true
            ;;
        glass)
            # Ultra-light frosted glass bar with zero solid black maximized fill or border line
            dconf write /org/gnome/shell/extensions/just-perfection/panel true || true
            dconf write /org/gnome/shell/extensions/just-perfection/panel-corner-size 1 || true
            dconf write /org/gnome/shell/extensions/blur-my-shell/panel/blur true || true
            dconf write /org/gnome/shell/extensions/blur-my-shell/panel/override-background true || true
            dconf write /org/gnome/shell/extensions/blur-my-shell/panel/override-background-dynamically false || true
            dconf write /org/gnome/shell/extensions/blur-my-shell/panel/style-panel 0 || true
            ;;
        transparent|*)
            # Crystal 100% transparent top bar (floating clock/icons only, ZERO black strip or 1px line)
            dconf write /org/gnome/shell/extensions/just-perfection/panel true || true
            dconf write /org/gnome/shell/extensions/just-perfection/panel-corner-size 1 || true
            dconf write /org/gnome/shell/extensions/blur-my-shell/panel/blur false || true
            dconf write /org/gnome/shell/extensions/blur-my-shell/panel/override-background true || true
            dconf write /org/gnome/shell/extensions/blur-my-shell/panel/override-background-dynamically false || true
            dconf write /org/gnome/shell/extensions/blur-my-shell/panel/style-panel 0 || true
            ;;
    esac

    # Snappy, zero-lag GNOME animation cadence (Just Perfection verified enum: 0=off, 1=1.0x, 4=0.6x faster)
    case "${ANIMATION_SPEED:-fast}" in
        instant)
            dconf write /org/gnome/shell/extensions/just-perfection/animation 0 || true
            ;;
        normal)
            dconf write /org/gnome/shell/extensions/just-perfection/animation 1 || true
            ;;
        fast|*)
            dconf write /org/gnome/shell/extensions/just-perfection/animation 4 || true
            ;;
    esac
    # Eliminate GNOME's 150ms Alt+Tab switcher lag & boot straight to crisp desktop
    dconf write /org/gnome/shell/extensions/just-perfection/switcher-popup-delay false || true
    dconf write /org/gnome/shell/extensions/just-perfection/startup-status 0 || true

    # GPU Overdraw Protection for Blur My Shell (smooth 60-144 FPS on integrated Intel/AMD GPUs):
    # - Keep live per-window application blur OFF (biggest GNOME Shell FPS killer)
    # - Use GPU-cached static blur for the dock & unblur redundant layers in Overview
    # - Auto-pause blur in Power Saver mode; keep crisp frosted lock-screen blur enabled
    dconf write /org/gnome/shell/extensions/blur-my-shell/power-saver true || true
    dconf write /org/gnome/shell/extensions/blur-my-shell/applications/blur false || true
    dconf write /org/gnome/shell/extensions/blur-my-shell/dash-to-dock/static-blur true || true
    dconf write /org/gnome/shell/extensions/blur-my-shell/dash-to-dock/unblur-in-overview true || true
    dconf write /org/gnome/shell/extensions/blur-my-shell/panel/unblur-in-overview true || true
    dconf write /org/gnome/shell/extensions/blur-my-shell/lockscreen/blur true || true
fi

if is_true "$ENABLE_WAYLAND_FRACTIONAL_SCALING"; then
    step "Enabling crisp Wayland fractional scaling, native XWayland HiDPI & Variable Refresh Rate (VRR)..."
    gset org.gnome.mutter experimental-features "['scale-monitor-framebuffer', 'xwayland-native-scaling', 'variable-refresh-rate']"
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] install iio-sensor-proxy for ambient light auto-brightness when hardware supports it"
else
    apt_install iio-sensor-proxy || true
fi

step "Configuring Spotlight & Built-In Assistant shortcuts..."
if [[ "$LAUNCHER_CHOICE" == "celina-spotlight" || "$LAUNCHER_CHOICE" == "ulauncher" ]]; then
    gset org.gnome.desktop.wm.keybindings switch-input-source "[]"
fi

if [[ "$LAUNCHER_CHOICE" == "celina-spotlight" ]] && is_true "$ENABLE_CELINA"; then
    register_gnome_shortcut "spotlight" "Spotlight & ${CELINA_NAME}" "$HOME/.local/bin/celina-spotlight" "<Super>space"
elif [[ "$LAUNCHER_CHOICE" == "ulauncher" ]]; then
    register_gnome_shortcut "spotlight" "Ulauncher Spotlight" "ulauncher-toggle" "<Super>space"
fi

if is_true "$ENABLE_CELINA"; then
    register_gnome_shortcut "celina-island"        "Toggle ${CELINA_NAME} Island"    "$HOME/.local/bin/celina-island --toggle"           "<Super><Shift>c"
    register_gnome_shortcut "celina-voice"         "${CELINA_NAME} Voice Listen"     "$HOME/.local/bin/celina-island --voice"            "<Super><Alt>c"
    register_gnome_shortcut "celina-writing-tools" "${CELINA_NAME} Writing Tools"    "$HOME/.local/bin/celina-writing-tools"             "<Super><Shift>w"
    register_gnome_shortcut "celina-live-text"     "${CELINA_NAME} Live Text OCR"    "$HOME/.local/bin/celina-live-text"                 "<Super><Shift>t"
    register_gnome_shortcut "celina-autonomy"      "${CELINA_NAME} Autonomy Status"  "$HOME/.local/bin/celina-autonomy --status-notify"  "<Super><Shift>a"
fi
