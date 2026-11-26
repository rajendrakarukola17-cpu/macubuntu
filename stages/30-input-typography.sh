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

# --- Phase A: XWayland crispness (scale-monitor-framebuffer + xwayland-native-scaling) ---
if is_true "$ENABLE_HIDPI_FONTS"; then
    step "Enabling XWayland crispness on HiDPI (scale-monitor-framebuffer + xwayland-native-scaling)..."
    # Idempotent merge: preserve features set by Stage 25 (e.g. variable-refresh-rate) instead of clobbering
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] merge org.gnome.mutter experimental-features with ['scale-monitor-framebuffer','xwayland-native-scaling']"
    else
        run_as_user gsettings set org.gnome.mutter experimental-features \
            "$(python3 - "scale-monitor-framebuffer" "xwayland-native-scaling" <<'PY'
import ast, sys, subprocess
raw = "[]"
try:
    raw = subprocess.check_output(["gsettings", "get", "org.gnome.mutter", "experimental-features"], text=True).strip()
except Exception:
    pass
if raw.startswith("@as "):
    raw = raw[4:].strip()
try:
    vals = list(ast.literal_eval(raw))
except Exception:
    vals = []
for feat in sys.argv[1:]:
    if feat not in vals:
        vals.append(feat)
print(repr(vals))
PY
)" 2>/dev/null || record_manual "Set org.gnome.mutter experimental-features via dconf-editor"
        echo "      ✅ XWayland crispness enabled (scale-monitor-framebuffer + xwayland-native-scaling)"
    fi
else
    echo "    ℹ️  XWayland crispness skipped (ENABLE_HIDPI_FONTS=false)"
fi

# --- Phase A: Wayland system-wide text injection (ydotool / uinput) ---
# P0: uinput rules (/dev/uinput GROUP=input) are NEVER deployed by default — closes T6.
# Gate behind ENABLE_EXPERIMENTAL_UINPUT=true (opt-in only; aligns with Stage 10).
if is_true "$ENABLE_EXPERIMENTAL_UINPUT" && is_true "$ENABLE_WAYLAND_TEXT_INJECTION"; then
    step "Deploying Wayland system-wide text injection (80-uinput.rules + ydotoold.socket)..."
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] deploy /etc/udev/rules.d/80-uinput.rules (KERNEL==uinput GROUP=input MODE=0660 static_node=uinput)"
        echo "      [dry-run] deploy $HOME/.config/systemd/user/ydotool.service (YDOTOOL_SOCKET=\${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/.ydotool_socket)"
        echo "      [dry-run] udevadm trigger; systemctl --user daemon-reload; systemctl --user enable --now ydotool.service"
    else
        # uinput device node rules so ydotoold can inject keystrokes without root
        run sudo install -d -m 755 /etc/udev/rules.d
        copy_root_file "$REPO_DIR/system/udev/80-uinput.rules" /etc/udev/rules.d/80-uinput.rules 644
        run sudo udevadm control --reload-rules 2>/dev/null || true
        run sudo udevadm trigger --subsystem-match=input 2>/dev/null || true

        # ydotool user service with YDOTOOL_SOCKET=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/.ydotool_socket
        RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
        run_as_user install -d -m 755 "$HOME/.config/systemd/user"
        install -m 644 "$REPO_DIR/system/systemd/ydotoold.service" "$HOME/.config/systemd/user/ydotool.service"
        run_as_user systemctl --user daemon-reload 2>/dev/null || true
        run_as_user systemctl --user enable --now ydotool.service 2>/dev/null || \
            record_manual "Enable ydotool.service user unit (YDOTOOL_SOCKET=$RUNTIME_DIR/.ydotool_socket)"

        # uinput rules grant GROUP=input access to /dev/uinput (fail-closed: log, don't block).
        # Adding the user to the input group is a manual admin step (not done by installer).
        echo "      ✅ Wayland text injection deployed (ydotool.service @ YDOTOOL_SOCKET=$RUNTIME_DIR/.ydotool_socket)"
    fi
else
    echo "    ℹ️  Wayland text injection skipped (ENABLE_EXPERIMENTAL_UINPUT or ENABLE_WAYLAND_TEXT_INJECTION not enabled)"
fi
