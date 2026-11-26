#!/usr/bin/env bash
# ============================================================================
# Stage 20: macOS Appearance (Pinned MacTahoe -> WhiteSur, Icons, Cursors, Glass Shell)
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[20] macOS Appearance: Pinned Themes, Icons, Cursors, Wallpapers & Glass Bar"

THEME_CACHE="$HOME/.cache/macubuntu_themes"
ensure_dir "$THEME_CACHE"

THEME_PREFIX="MacTahoe"
step "Installing pinned GTK & GNOME Shell theme (MacTahoe primary, WhiteSur fallback)..."
if clone_pinned_repo "mactahoe-gtk-theme" "$THEME_CACHE/mactahoe"; then
    if [[ "$DRY_RUN" != "1" ]]; then
        ( cd "$THEME_CACHE/mactahoe" && ./install.sh -l -c Dark >/dev/null 2>&1 ) \
            || ( cd "$THEME_CACHE/mactahoe" && ./install.sh -l >/dev/null 2>&1 ) \
            || record_fail "MacTahoe GTK theme install"
    else
        echo "      [dry-run] (cd $THEME_CACHE/mactahoe && ./install.sh -l -c Dark)"
    fi
    THEME_PREFIX="MacTahoe"
elif clone_pinned_repo "whitesur-gtk-theme" "$THEME_CACHE/whitesur"; then
    if [[ "$DRY_RUN" != "1" ]]; then
        ( cd "$THEME_CACHE/whitesur" && ./install.sh -l -c Dark -N glassy >/dev/null 2>&1 ) \
            || record_fail "WhiteSur GTK theme install"
    else
        echo "      [dry-run] (cd $THEME_CACHE/whitesur && ./install.sh -l -c Dark -N glassy)"
    fi
    THEME_PREFIX="WhiteSur"
else
    record_fail "Pinned GTK theme clone failed (verify network & system/download-locks.sha256)"
fi

step "Installing pinned WhiteSur icons, cursors, and wallpapers..."
if clone_pinned_repo "whitesur-icon-theme" "$THEME_CACHE/ws-icons"; then
    if [[ "$DRY_RUN" != "1" ]]; then
        ( cd "$THEME_CACHE/ws-icons" && ./install.sh -b >/dev/null 2>&1 ) || record_fail "WhiteSur icons"
    else
        echo "      [dry-run] (cd $THEME_CACHE/ws-icons && ./install.sh -b)"
    fi
fi
if clone_pinned_repo "whitesur-cursors" "$THEME_CACHE/ws-cursors"; then
    if [[ "$DRY_RUN" != "1" ]]; then
        ( cd "$THEME_CACHE/ws-cursors" && ./install.sh >/dev/null 2>&1 ) || record_fail "WhiteSur cursors"
    else
        echo "      [dry-run] (cd $THEME_CACHE/ws-cursors && ./install.sh)"
    fi
fi
if clone_pinned_repo "whitesur-wallpapers" "$THEME_CACHE/ws-walls"; then
    if [[ "$DRY_RUN" != "1" ]]; then
        ( cd "$THEME_CACHE/ws-walls" && ./install-wallpapers.sh >/dev/null 2>&1 ) || true
    else
        echo "      [dry-run] (cd $THEME_CACHE/ws-walls && ./install-wallpapers.sh)"
    fi
fi

# Enable GNOME Shell user-theme so the top bar & overview adopt the macOS shell theme
install_ext "user-theme@gnome-shell-extensions.gcampax.github.com" "User Themes"

THEME_NAME="$(first_match "${THEME_PREFIX}*Dark*" "$HOME/.themes" "$HOME/.local/share/themes")"
[[ -z "$THEME_NAME" ]] && THEME_NAME="$(first_match "WhiteSur*Dark*" "$HOME/.themes" "$HOME/.local/share/themes")"
ICON_NAME="$(first_match "WhiteSur*dark*" "$HOME/.icons" "$HOME/.local/share/icons")"
CURSOR_NAME="$(first_match "WhiteSur*cursors*" "$HOME/.icons" "$HOME/.local/share/icons")"

if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] apply gtk-theme (${THEME_PREFIX}-Dark), user-theme, icon-theme (WhiteSur-dark), cursor-theme"
    echo "      [dry-run] patch gnome-shell.css with $REPO_DIR/system/gnome/panel-glass.css"
else
    [[ -n "$THEME_NAME"  ]] && gset org.gnome.desktop.interface gtk-theme "'$THEME_NAME'"
    [[ -n "$ICON_NAME"   ]] && gset org.gnome.desktop.interface icon-theme "'$ICON_NAME'"
    [[ -n "$CURSOR_NAME" ]] && gset org.gnome.desktop.interface cursor-theme "'$CURSOR_NAME'"
    # Rebuild GTK icon caches so Nautilus, Settings & apps never stat thousands of SVGs on cold launch
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        for icon_dir in "$HOME/.icons/${ICON_NAME}" "$HOME/.local/share/icons/${ICON_NAME}"; do
            [[ -n "$ICON_NAME" && -d "$icon_dir" ]] && gtk-update-icon-cache -f -q -t "$icon_dir" >/dev/null 2>&1 || true
        done
    fi
    if [[ -n "$THEME_NAME" ]] && command -v dconf >/dev/null 2>&1; then
        dconf write /org/gnome/shell/extensions/user-theme/name "'$THEME_NAME'" || true
    fi

    for css_candidate in \
        "$HOME/.themes/${THEME_NAME}/gnome-shell/gnome-shell.css" \
        "$HOME/.local/share/themes/${THEME_NAME}/gnome-shell/gnome-shell.css"; do
        if [[ -n "$THEME_NAME" && -f "$css_candidate" ]]; then
            step "Patching GNOME Shell panel CSS (ultra-transparent, zero black bar/border line)..."
            python3 - "$css_candidate" "$REPO_DIR/system/gnome/panel-glass.css" <<'PY'
import os, re, sys, tempfile
from pathlib import Path
dst, src = Path(sys.argv[1]), Path(sys.argv[2])
text = dst.read_text(encoding="utf-8", errors="replace")
# Remove any prior delimited or legacy macubuntu panel block cleanly before appending latest CSS
text = re.sub(r"\n?/\* MACUBUNTU-CELINA-PANEL-START \*/[\s\S]*?/\* MACUBUNTU-CELINA-PANEL-END \*/\n?", "\n", text)
text = re.sub(r"\n?/\* MACUBUNTU-CELINA-PANEL \*/\n#panel\s*\{[^}]*\}\n?", "\n", text)
new_text = text.rstrip() + "\n\n" + src.read_text(encoding="utf-8")
tmp_fd, tmp_path = tempfile.mkstemp(dir=str(dst.parent), prefix=".gnome-shell.", suffix=".tmp")
try:
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
        f.write(new_text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, dst)
finally:
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)
PY
            break
        fi
    done
fi

if is_true "$ENABLE_SAFARI_FIREFOX"; then
    step "Styling Firefox as macOS Safari (UI Chrome, Traffic Lights, Pill URL Bar & Safari Compass Icon)..."
    if [[ "$DRY_RUN" == "1" ]]; then
        python3 "$REPO_DIR/system/firefox/apply-safari-theme.py" --dry-run || true
    else
        python3 "$REPO_DIR/system/firefox/apply-safari-theme.py" || record_fail "Safari Firefox theme"
        command -v gtk-update-icon-cache >/dev/null 2>&1 \
            && gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
        command -v update-desktop-database >/dev/null 2>&1 \
            && update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
    fi
fi

# --- Celina Dynamic Island: native GNOME Shell 46–51 extension ---
if is_true "$ENABLE_CELINA_ISLAND_SHELL"; then
    step "Deploying Celina Dynamic Island GNOME Shell extension (celina-island@macubuntu.org)..."
    ISLAND_SRC="$REPO_DIR/system/gnome/extensions/celina-island@macubuntu.org"
    ISLAND_DST="$HOME/.local/share/gnome-shell/extensions/celina-island@macubuntu.org"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] copy $ISLAND_SRC -> $ISLAND_DST"
        echo "      [dry-run] install extension schema & enable via gnome-extensions"
    else
        # Install extension files (user-scoped, no root needed)
        rm -rf "$ISLAND_DST"
        cp -a "$ISLAND_SRC" "$ISLAND_DST"
        chmod -R u+rwX,go+rX "$ISLAND_DST"
        # Compile GSettings schema if available
        if command -v glib-compile-schemas >/dev/null 2>&1; then
            run glib-compile-schemas "$ISLAND_DST/schemas" 2>/dev/null || true
        fi
        # Enable via gnome-extensions (files already deployed to $ISLAND_DST; no --install of a directory)
        if command -v gnome-extensions >/dev/null 2>&1; then
            run_as_user gnome-extensions enable celina-island@macubuntu.org 2>/dev/null || \
                record_manual "Enable celina-island@macubuntu.org in Extension Manager"
        else
            record_manual "gnome-extensions CLI unavailable; install celina-island@macubuntu.org manually"
        fi
        # Disable basskibo dynamic-island to prevent vertical stacking (guardrail #6)
        if command -v gnome-extensions >/dev/null 2>&1; then
            run_as_user gnome-extensions disable dynamic-island@basskibo 2>/dev/null || true
        fi
    fi
else
    echo "    ℹ️  Celina Island shell extension skipped (ENABLE_CELINA_ISLAND_SHELL=false)"
fi

# --- Native Wayland Blur: ext-background-effect-v1 (Mutter 51+) ---
# GTK CSS + Blur My Shell approximate glass; ext-background-effect-v1 enables
# true compositor-level frosted-glass blur per Wayland surface.
if is_true "$ENABLE_NATIVE_WAYLAND_BLUR"; then
    step "Preparing native Wayland blur (ext-background-effect-v1, Mutter 51+)..."
    SHELL_MAJOR="$(detect_gnome_shell_major)"
    if [[ "$SHELL_MAJOR" -lt 51 ]]; then
        log_info "Guardrail: GNOME Shell $SHELL_MAJOR < 51 — ext-background-effect-v1 unavailable"
        echo "      👉 Upgrade to Ubuntu 26.10+ or Mutter 51+ for native frosted-glass blur."
    elif [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would install libgtk-4-dev, gtk4-doc"
        echo "      [dry-run] would write GTK4 blur CSS + celina-island ext-background-effect-v1 patch"
    else
        # Native Wayland blur (ext-background-effect-v1) requires Mutter 51+.
        # No system-wide GTK4 CSS blur wildcard (invalid CSS properties cause parser errors on every app launch).
        echo "      ℹ️  Native blur requires Mutter 51+ (ext-background-effect-v1); GTK4 wildcard blur CSS not deployed."
        # Patch celina-island extension metadata to request native blur surface (atomic write)
        ISLAND_METADATA="$HOME/.local/share/gnome-shell/extensions/celina-island@macubuntu.org/metadata.json"
        if [[ -f "$ISLAND_METADATA" ]]; then
            python3 - "$ISLAND_METADATA" <<'PY'
import json, os, sys, tempfile
from pathlib import Path
path = sys.argv[1]
try:
    data = json.loads(open(path, encoding="utf-8").read())
except Exception:
    data = {}
data["blur-surface"] = True
data["blur-radius"] = 20
tmp_fd, tmp_path = tempfile.mkstemp(dir=str(Path(path).parent), prefix=".metadata.", suffix=".tmp")
try:
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
finally:
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)
PY
            echo "      ✅ celina-island metadata updated with blur-surface support"
        fi
        echo "      ✅ Native Wayland blur prepared (Mutter $SHELL_MAJOR)"
        record_manual "GTK4 apps requesting ext-background-effect-v1 will get native frosted-glass blur"
    fi
else
    echo "    ℹ️  Native Wayland Blur skipped (ENABLE_NATIVE_WAYLAND_BLUR=false)"
fi

# --- GPU-adaptive blur sigma & animation speed (hardware.json driven) ---
if [[ -f /etc/macubuntu/hardware.json ]] && command -v jq >/dev/null 2>&1; then
    HW_GPU_VENDOR="$(jq -r '.gpu_vendor // "cpu"' /etc/macubuntu/hardware.json)"
    HW_VRAM_MB="$(jq -r '.vram_mb // 0' /etc/macubuntu/hardware.json)"
    HW_VRAM_MB="${HW_VRAM_MB:-0}"
    HW_VRAM_MB="$(printf '%d' "$HW_VRAM_MB" 2>/dev/null || echo 0)"
    HW_TIER="$(jq -r '.tier // "balanced"' /etc/macubuntu/hardware.json 2>/dev/null || echo balanced)"
else
    HW_GPU_VENDOR="cpu"
    HW_VRAM_MB=0
    HW_TIER="balanced"
fi

step "Applying GPU-adaptive blur & animation tuning (vendor=$HW_GPU_VENDOR vram=${HW_VRAM_MB}MB tier=$HW_TIER)..."

# Determine blur sigma & animation speed based on GPU tier
if [[ "$HW_TIER" == "lite" ]] || [[ "$HW_GPU_VENDOR" == "intel" && "$HW_VRAM_MB" -lt 4096 ]]; then
    BLUR_SIGMA=15
    ANIMATION_SPEED="fast"
    DISABLE_PER_FRAME_BLUR="true"
    echo "    ℹ️  Lite/Intel iGPU detected — capping blur sigma=15, fast animations, disabling per-frame blur"
elif [[ "$HW_GPU_VENDOR" == "nvidia" || "$HW_GPU_VENDOR" == "amd" ]] && [[ "$HW_VRAM_MB" -ge 4096 ]]; then
    BLUR_SIGMA=25
    ANIMATION_SPEED="normal"
    DISABLE_PER_FRAME_BLUR="false"
    echo "    ℹ️  Discrete GPU with >=4GB VRAM — enabling full glass blur + normal animations"
else
    BLUR_SIGMA=20
    ANIMATION_SPEED="fast"
    DISABLE_PER_FRAME_BLUR="false"
    echo "    ℹ️  Balanced hardware — blur sigma=20, fast animations"
fi

# Note: Blur My Shell & Just Perfection dconf configuration is owned by Stage 25
# (Stage 25 installs the extensions and applies the settings). Stage 20 only
# computes the hardware-adaptive BLUR_SIGMA/ANIMATION_SPEED values above.

# --- Phase A: Retina / HiDPI font rendering (grayscale AA + hint=slight) ---
if is_true "$ENABLE_HIDPI_FONTS"; then
    step "Deploying macOS-style Retina font rendering (grayscale AA, hint=slight, rgba=none)..."
    HW_TIER="balanced"
    if [[ -f /etc/macubuntu/hardware.json ]] && command -v jq >/dev/null 2>&1; then
        HW_TIER="$(jq -r '.tier' /etc/macubuntu/hardware.json 2>/dev/null || echo balanced)"
    fi
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] detect display DPI; deploy 10-macubuntu-hidpi.conf (144 dpi) only if HiDPI detected"
        echo "      [dry-run] run fc-cache -fv"
    else
        # P1: Only force 144 DPI when a HiDPI display is actually detected;
        # do not oversize fonts on standard 96 DPI 1080p screens.
        DETECTED_DPI=0
        if command -v xdpyinfo >/dev/null 2>&1; then
            DETECTED_DPI="$(xdpyinfo 2>/dev/null | awk '/dimensions:/ {print $2}' | cut -d'x' -f1 | head -1)"
            DETECTED_DPI="${DETECTED_DPI:-0}"
        fi
        if [[ "$DETECTED_DPI" -ge 120 ]] || [[ "$HW_TIER" == "workstation" ]]; then
            run sudo install -d -m 755 /etc/fonts/conf.d
            copy_root_file "$REPO_DIR/system/fonts/10-macubuntu-hidpi.conf" /etc/fonts/conf.d/10-macubuntu-hidpi.conf 644
            echo "      ✅ HiDPI detected (${DETECTED_DPI} dpi) — 144 dpi font rendering deployed"
        else
            echo "      ℹ️  Standard DPI detected (${DETECTED_DPI} dpi) — skipping forced 144 dpi (avoids 1.5× font oversizing on 1080p screens)"
        fi
        # Rebuild font cache so GTK/Qt apps pick up grayscale AA immediately
        if command -v fc-cache >/dev/null 2>&1; then
            run sudo fc-cache -fv >/dev/null 2>&1 || true
            echo "      ✅ Font cache rebuilt (fc-cache -fv)"
        fi
        echo "      ✅ Retina font rendering deployed (grayscale AA + hintslight + rgba=none @ 144 dpi; tier=$HW_TIER)"
    fi
else
    echo "    ℹ️  Retina font rendering skipped (ENABLE_HIDPI_FONTS=false)"
fi
