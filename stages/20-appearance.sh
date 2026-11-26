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
import re, sys
from pathlib import Path
dst, src = Path(sys.argv[1]), Path(sys.argv[2])
text = dst.read_text(encoding="utf-8", errors="replace")
# Remove any prior delimited or legacy macubuntu panel block cleanly before appending latest CSS
text = re.sub(r"\n?/\* MACUBUNTU-CELINA-PANEL-START \*/[\s\S]*?/\* MACUBUNTU-CELINA-PANEL-END \*/\n?", "\n", text)
text = re.sub(r"\n?/\* MACUBUNTU-CELINA-PANEL \*/\n#panel\s*\{[^}]*\}\n?", "\n", text)
dst.write_text(text.rstrip() + "\n\n" + src.read_text(encoding="utf-8"), encoding="utf-8")
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
