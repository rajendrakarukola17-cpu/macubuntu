#!/usr/bin/env bash
# ============================================================================
# Stage 46: Browser Tuning — Firefox performance user.js (8GB-optimized)
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[46] Browser Tuning (Firefox user.js, 3 profile locations)"

# --- Firefox user.js (performance-tuned for 8GB RAM) ---
FIREFOX_USER_JS="$REPO_DIR/system/firefox/user.js"
if ! is_true "$ENABLE_FIREFOX_TUNING"; then
    echo "    ℹ️  Firefox tuning skipped (ENABLE_FIREFOX_TUNING=false)"
    exit 0
fi

if [[ ! -f "$FIREFOX_USER_JS" ]]; then
    record_fail "firefox user.js not found at $FIREFOX_USER_JS"
    exit 1
fi

step "Detecting RAM tier for Firefox tuning..."
tier_46="$(detect_hw_tier)"
case "$tier_46" in
    CONSTRAINED) ff_low_commit="2048" ;;
    BALANCED)    ff_low_commit="3072" ;;
    HEADROOM)    ff_low_commit="4096" ;;
esac
log_info "Firefox tier=${tier_46} browser.low_commit_space_threshold_mb=${ff_low_commit}"

step "Deploying Firefox performance user.js to all profile locations..."

# Scan all 3 Firefox profile roots:
#   1. ~/.mozilla/firefox/                    (native .deb / flatpak-legacy)
#   2. ~/snap/firefox/common/.mozilla/firefox/ (Snap)
#   3. ~/.var/app/org.mozilla.firefox/.mozilla/firefox/ (Flatpak)
profile_roots=(
    "$HOME/.mozilla/firefox"
    "$HOME/snap/firefox/common/.mozilla/firefox"
    "$HOME/.var/app/org.mozilla.firefox/.mozilla/firefox"
)

snap_detected=false
profile_dirs=()

for root in "${profile_roots[@]}"; do
    if [[ ! -d "$root" ]]; then
        continue
    fi
    # Detect Snap Firefox
    if [[ "$root" == *"snap/firefox"* ]]; then
        snap_detected=true
    fi
    while IFS= read -r -d '' pdir; do
        profile_dirs+=("$pdir")
    done < <(find "$root" -maxdepth 1 -type d -name '*.default*' -print0 2>/dev/null || true)
done

if [[ ${#profile_dirs[@]} -eq 0 ]]; then
    # No Firefox profiles exist yet — do NOT create a fake profile directory
    # (Firefox ignores directories not listed in profiles.ini). Log a manual
    # step for the user to launch Firefox once and re-run --only 46.
    record_manual "No Firefox profile found — launch Firefox once to create a profile, then re-run: ./install.sh --only 46"
    echo "      Skipping user.js deployment (no profile directory)."
    exit 0
fi

# Extract the MACUBUNTU-PERF block from the template (between delimited markers)
perf_block="$(sed -n '/^\/\/ >>> MACUBUNTU-PERF-START$/,/^\/\/ <<< MACUBUNTU-PERF-END$/p' "$FIREFOX_USER_JS")"
# Tier-render the low_commit_space_threshold_mb value inside the block
perf_block="$(printf '%s\n' "$perf_block" | sed -E \
    -e "s/^(user_pref\(\\"browser\.low_commit_space_threshold_mb\\",)[[:space:]]*[0-9]+(\));.*$/\1 ${ff_low_commit}\2);/")"

for pdir in "${profile_dirs[@]}"; do
    run_as_user mkdir -p "$pdir"
    ujs="$pdir/user.js"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would update delimited MACUBUNTU-PERF block in $ujs (low_commit=${ff_low_commit})"
    else
        # Read existing user.js (if any)
        if [[ -f "$ujs" ]]; then
            existing="$(cat "$ujs")"
        else
            existing=""
        fi
        # Remove any existing MACUBUNTU-PERF block
        cleaned="$(printf '%s\n' "$existing" | sed '/^\/\/ >>> MACUBUNTU-PERF-START$/,/^\/\/ <<< MACUBUNTU-PERF-END$/d')"
        # Trim trailing blank lines before appending
        cleaned="$(printf '%s\n' "$cleaned" | sed -e :a -e '/^\s*$/d;N;ba')"
        # Append the new tier-rendered MACUBUNTU-PERF block
        if [[ -n "$cleaned" ]]; then
            final="${cleaned}\n\n${perf_block}"
        else
            final="${perf_block}"
        fi
        printf '%b\n' "$final" > "$ujs.tmp"
        copy_user_file "$ujs.tmp" "$ujs" 644
        rm -f "$ujs.tmp"
    fi
    echo "      → $pdir/user.js"
done

# Guardrail: never set MOZ_DISABLE_RDD_SANDBOX (breaks WebRTC/screenshare
# on Wayland and weakens GPU-process isolation).
if is_true "$DRY_RUN"; then
    echo "      [dry-run] would verify MOZ_DISABLE_RDD_SANDBOX is NOT in any env/profile file"
else
    found_rdd=false
    for root in "${profile_roots[@]}"; do
        [[ -d "$root" ]] || continue
        if grep -rl "MOZ_DISABLE_RDD_SANDBOX" "$root" 2>/dev/null | grep -q .; then
            found_rdd=true
        fi
    done
    if $found_rdd; then
        record_manual "Found MOZ_DISABLE_RDD_SANDBOX set somewhere — remove it (breaks WebRTC/screenshare + GPU isolation)"
    else
        echo "      ✅ MOZ_DISABLE_RDD_SANDBOX guardrail: not set anywhere (good)"
    fi
fi

# VA-API guardrail: don't force hardware video decode on GPUs that don't support it
# (Stage 50 may fall back to --disable-vaapi on unsupported Intel/NVIDIA GPUs).
if ! is_true "$DRY_RUN"; then
    if detect_vaapi_decode_supported; then
        echo "      ✅ VA-API decode supported — hardware video decoding enabled in Firefox user.js"
    else
        record_manual "VA-API decode not supported on this GPU — Firefox user.js may need --disable-vaapi fallback (see Stage 50)"
    fi
fi

# Snap Firefox note
if $snap_detected; then
    record_manual "Snap Firefox profile detected — user.js applied to Snap profile location; confirm Snap Firefox reads it (Snap confinement may block)"
fi

# --- Chrome/Brave Ozone Wayland (GPU-accelerated scrolling on Wayland) ---
if is_true "$ENABLE_CHROME_OZONE_WAYLAND"; then
    step "Deploying Chrome/Brave Ozone Wayland platform flags..."
    CHROME_FLAGS_DIR="$HOME/.config/chromium-flags.conf"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would write $CHROME_FLAGS_DIR with --ozone-platform=wayland --enable-features=UseOzonePlatform --ignore-gpu-blocklist"
    else
        {
            echo "# Chrome/Chromium/Brave — Wayland Ozone platform (macOS-like GPU compositing)"
            echo "--ozone-platform=wayland"
            echo "--enable-features=UseOzonePlatform"
            echo "--ignore-gpu-blocklist"
        } | write_user_file "$CHROME_FLAGS_DIR" 644
        echo "      ✅ Chrome/Brave Ozone Wayland flags written to $CHROME_FLAGS_DIR"
        echo "      (Relaunch Chrome/Brave for Wayland Ozone platform to take effect)"
    fi
else
    echo "    ℹ️  Chrome/Brave Ozone Wayland skipped (ENABLE_CHROME_OZONE_WAYLAND=false)"
fi

# --- Vendor-specific VA-API & Ozone flags (hardware.json driven) ---
if [[ -f /etc/macubuntu/hardware.json ]] && command -v jq >/dev/null 2>&1; then
    HW_GPU_VENDOR="$(jq -r '.gpu_vendor' /etc/macubuntu/hardware.json)"
else
    HW_GPU_VENDOR="cpu"
fi

step "Applying vendor-specific VA-API & Chromium flags (gpu=$HW_GPU_VENDOR)..."

VA_API_ENV_FILE="$HOME/.config/environment.d/99-macubuntu-vaapi.conf"
CHROME_ENV_FILE="$HOME/.config/environment.d/99-macubuntu-chrome-env.conf"

if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] would write $VA_API_ENV_FILE with LIBVA_DRIVER_NAME for $HW_GPU_VENDOR"
    echo "      [dry-run] would write vendor-specific Chromium flags"
else
    ensure_dir "$HOME/.config/environment.d"
    case "$HW_GPU_VENDOR" in
        intel)
            {
                echo "# macubuntu VA-API — Intel iHD driver"
                echo "LIBVA_DRIVER_NAME=iHD"
                echo "LIBVA_DRI3_DISABLE=1"
            } | write_user_file "$VA_API_ENV_FILE" 644
            echo "      ✅ Intel VA-API: LIBVA_DRIVER_NAME=iHD"
            ;;
        amd)
            {
                echo "# macubuntu VA-API — AMD radeonsi driver"
                echo "LIBVA_DRIVER_NAME=radeonsi"
                echo "VDPAU_DRIVER=radeonsi"
            } | write_user_file "$VA_API_ENV_FILE" 644
            echo "      ✅ AMD VA-API: LIBVA_DRIVER_NAME=radeonsi"
            ;;
        nvidia)
            {
                echo "# macubuntu VA-API — NVIDIA VDPAU/VA-API"
                echo "LIBVA_DRIVER_NAME=nvidia"
                echo "NVD_BACKEND=direct"
            } | write_user_file "$VA_API_ENV_FILE" 644
            echo "      ✅ NVIDIA VA-API: LIBVA_DRIVER_NAME=nvidia, NVD_BACKEND=direct"
            ;;
        *)
            echo "      ℹ️  No vendor-specific VA-API driver for gpu=$HW_GPU_VENDOR"
            ;;
    esac

    # Chromium vendor-specific flags
    {
        echo "# Chromium vendor-specific GPU flags (macubuntu)"
        echo "--ignore-gpu-blocklist"
    } | write_user_file "$CHROME_ENV_FILE" 644

    if [[ "$HW_GPU_VENDOR" == "nvidia" ]]; then
        {
            echo "# NVIDIA-specific Chromium flags"
            echo "--enable-features=VaapiVideoDecoder"
            echo "--disable-features=UseChromeOSDirectVideoDecoder"
        } >> "$CHROME_ENV_FILE"
        echo "      ✅ NVIDIA Chromium flags added"
    fi
    echo "      ✅ Chromium environment flags written to $CHROME_ENV_FILE"
fi

step "Firefox user.js deployed to ${#profile_dirs[@]} profile location(s) across ${#profile_roots[@]} root(s)."
