#!/usr/bin/env bash
# ============================================================================
# Stage 50: Power Management, Optimized Battery Charging (80%) & GPU Video Offload
# Exclusive PPD vs TLP — Never Both!
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[50] Power Management ($POWER_BACKEND), 80% Charge Limit & VA-API Decode"

apt_install powertop || true

case "$POWER_BACKEND" in
    ppd)
        step "Enabling power-profiles-daemon (and disabling conflicting TLP)..."
        apt_install power-profiles-daemon || true
        systemctl_disable tlp
        run sudo systemctl unmask power-profiles-daemon 2>/dev/null || true
        systemctl_enable power-profiles-daemon || record_fail "systemctl enable power-profiles-daemon" ;;
    tlp)
        step "Enabling TLP (and masking conflicting power-profiles-daemon)..."
        apt_install tlp || record_fail "tlp"
        run sudo systemctl mask --now power-profiles-daemon 2>/dev/null || true
        systemctl_enable tlp || record_fail "systemctl enable tlp" ;;
    *)
        record_fail "Unknown POWER_BACKEND=$POWER_BACKEND (expected ppd|tlp)" ;;
esac

if is_true "$ENABLE_BATTERY_CHARGE_LIMIT"; then
    step "Checking laptop firmware support for Optimized Battery Charging (80% threshold)..."
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] if /sys/class/power_supply/BAT*/charge_control_end_threshold exists, install /etc/udev/rules.d/90-battery-charge-limit.rules"
    else
        HAS_BAT_LIMIT=0
        for f in /sys/class/power_supply/BAT*/charge_control_end_threshold; do
            if [[ -e "$f" ]]; then
                HAS_BAT_LIMIT=1
                break
            fi
        done
        if [[ "$HAS_BAT_LIMIT" -eq 1 ]]; then
            copy_root_file "$REPO_DIR/system/udev/90-battery-charge-limit.rules" /etc/udev/rules.d/90-battery-charge-limit.rules
            run sudo udevadm control --reload-rules || true
            run sudo udevadm trigger --subsystem-match=power_supply || true
            echo "      Optimized Battery Charging (80% threshold) activated via udev."
        else
            echo "      Hardware firmware does not expose charge_control_end_threshold; skipped safely."
        fi
    fi
fi

if is_true "$ENABLE_VAAPI_GPU_DECODE"; then
    step "Installing hardware-accelerated VA-API video decode & Mesa Vulkan drivers (Mac-grade video + AMD Radeon AI offload)..."
    apt_install_each vainfo mesa-va-drivers mesa-vulkan-drivers vulkan-tools || true
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] detect Intel/AMD GPU and install vendor acceleration drivers when present"
        echo "      [dry-run] probe vainfo for H.264/VP9 VLD decode; disable Firefox VA-API pref if unsupported"
    else
        if command -v lspci >/dev/null 2>&1 && lspci 2>/dev/null | grep -qiE 'VGA|Display' && lspci 2>/dev/null | grep -qi 'Intel'; then
            apt_install intel-media-va-driver || true
        fi
        if detect_vaapi_decode_supported; then
            echo "      VA-API hardware H.264/VP9 decode verified via vainfo."
        else
            echo "      ⚠️  vainfo did not report H.264/VP9 VAEntrypointVLD (e.g. NVIDIA Wayland / headless / unsupported iGPU)."
            if is_true "$ENABLE_SAFARI_FIREFOX" && [[ -f "$REPO_DIR/system/firefox/apply-safari-theme.py" ]]; then
                echo "      Applying safe Firefox VA-API fallback (--disable-vaapi) to prevent silent decode stalls..."
                run python3 "$REPO_DIR/system/firefox/apply-safari-theme.py" --disable-vaapi || true
            fi
        fi
    fi
fi

if is_true "$ENABLE_SAFE_SLEEP"; then
    step "Configuring macOS-style Safe Sleep / instant-wake laptop sleep policy..."
    if [[ "$DRY_RUN" == "1" ]]; then
        if detect_has_luks_swap && ! detect_luks_swap_resume_ready; then
            echo "      [dry-run] LUKS swap detected without verified initramfs resume hook — hibernate disabled safely"
            echo "      [dry-run] deploy /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf (instant-wake suspend guard)"
        elif detect_has_hibernate_resume; then
            echo "      [dry-run] deploy /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf (HibernateDelaySec=3h; real_swap >= RAM + ZRAM verified)"
        else
            echo "      [dry-run] deploy /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf (pure-ZRAM/undersized-swap instant-wake suspend guard)"
        fi
    elif detect_has_luks_swap && ! detect_luks_swap_resume_ready; then
        copy_root_file "$REPO_DIR/system/systemd/sleep-instant-suspend.conf" /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf
        echo "      ⚠️  LUKS swap detected without verified crypttab + initramfs resume hook — hibernate disabled safely."
    elif detect_has_hibernate_resume; then
        copy_root_file "$REPO_DIR/system/systemd/sleep-safesleep.conf" /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf
        echo "      Safe Sleep active: instant suspend -> automatic hibernate after 3h on battery (real_swap >= RAM + ZRAM verified)."
    else
        copy_root_file "$REPO_DIR/system/systemd/sleep-instant-suspend.conf" /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf
        echo "      Pure-ZRAM or undersized swap detected (< RAM + ZRAM): enabled instant-wake suspend guard (add swap >= RAM+ZRAM + resume= for Safe Sleep)."
    fi
fi

if is_true "$ENABLE_SILENT_BOOT"; then
    step "Configuring opt-in flicker-free GRUB boot drop-in & active CPU P-States (--silent-boot)..."
    CPU_VENDOR="$(detect_cpu_vendor)"
    PSTATE_ARG=""
    case "$CPU_VENDOR" in
        amd)   PSTATE_ARG="amd_pstate=active" ;;
        intel) PSTATE_ARG="intel_pstate=active" ;;
    esac
    DUALBOOT_WIN=0
    detect_dualboot_windows && DUALBOOT_WIN=1
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] render /etc/default/grub.d/99-macubuntu-boot.cfg (cpu=$CPU_VENDOR, arg='${PSTATE_ARG:-none}', dualboot_windows=$DUALBOOT_WIN) & run update-grub"
    elif [[ -d /etc/default/grub.d ]] && command -v update-grub >/dev/null 2>&1; then
        run sudo install -d -m 755 /etc/default/grub.d
        {
            sed "s/@CPU_PSTATE_ARG@/${PSTATE_ARG}/g" "$REPO_DIR/system/grub/99-macubuntu-boot.cfg"
            if [[ "$DUALBOOT_WIN" -eq 1 ]]; then
                echo ""
                echo "# Dual-boot Windows EFI detected — preserve Windows Boot Manager entry"
                echo "GRUB_DISABLE_OS_PROBER=false"
            fi
        } | sudo tee /etc/default/grub.d/99-macubuntu-boot.cfg >/dev/null
        sudo chmod 644 /etc/default/grub.d/99-macubuntu-boot.cfg
        if [[ "$DUALBOOT_WIN" -eq 1 ]]; then
            apt_install os-prober || true
            echo "      ✅ Windows dual-boot entry preserved (GRUB_DISABLE_OS_PROBER=false + os-prober)."
        fi
        run sudo update-grub || record_manual "Run 'sudo update-grub' to activate flicker-free boot drop-in"
    else
        record_manual "GRUB drop-in directory (/etc/default/grub.d) or update-grub not found; skipped silent boot"
    fi
fi
