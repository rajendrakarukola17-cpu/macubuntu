#!/usr/bin/env bash
# ============================================================================
# Stage 35: Unified ZRAM Swap & 8GB-Tuned VM Sysctl (Single Authoritative Stack)
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[35] Unified ZRAM & Virtual Memory Tuning"

if apt_has systemd-zram-generator; then
    step "Configuring native systemd-zram-generator (zstd, 100% RAM)..."
    if [[ "$DRY_RUN" != "1" ]] && dpkg -s zram-tools >/dev/null 2>&1; then
        systemctl_disable zramswap
    fi
    apt_install systemd-zram-generator || record_fail "systemd-zram-generator"
    copy_root_file "$REPO_DIR/system/zram/zram-generator.conf" /etc/systemd/zram-generator.conf
    run sudo systemctl daemon-reload || true
    run sudo systemctl restart systemd-zram-setup@zram0.service 2>/dev/null || true
else
    step "systemd-zram-generator unavailable; configuring zram-tools fallback..."
    apt_install zram-tools || record_fail "zram-tools"
    copy_root_file "$REPO_DIR/system/zram/zramswap.default" /etc/default/zramswap
    systemctl_enable zramswap || record_fail "systemctl enable zramswap"
    run sudo systemctl restart zramswap 2>/dev/null || true
fi

step "Deploying single authoritative VM/Network/IO sysctl (/etc/sysctl.d/99-macubuntu-vm.conf)..."
run sudo rm -f /etc/sysctl.d/99-mac-vm.conf /etc/sysctl.d/99-macos-vm.conf /etc/sysctl.d/99-zram.conf
copy_root_file "$REPO_DIR/system/sysctl/99-macubuntu-vm.conf" /etc/sysctl.d/99-macubuntu-vm.conf
run sudo sysctl --system >/dev/null || true

step "Deploying low-latency NVMe/SSD I/O scheduler rules, weekly TRIM & native Wayland app environment..."
copy_root_file "$REPO_DIR/system/udev/60-macubuntu-io-scheduler.rules" /etc/udev/rules.d/60-macubuntu-io-scheduler.rules
if [[ "$DRY_RUN" != "1" ]]; then
    run sudo udevadm control --reload-rules >/dev/null 2>&1 || true
    run sudo udevadm trigger --subsystem-match=block >/dev/null 2>&1 || true
fi
systemctl_enable fstrim.timer || true
if [[ "$(nproc 2>/dev/null || echo 2)" -ge 4 ]]; then
    apt_install irqbalance || true
    systemctl_enable irqbalance || true
fi
ensure_dir "$HOME/.config/environment.d"
copy_user_file "$REPO_DIR/system/environment.d/90-macubuntu-smooth.conf" "$HOME/.config/environment.d/90-macubuntu-smooth.conf" 644

step "Deploying Jetsam-style PSI memory protection (systemd-oomd drop-in)..."
apt_install systemd-oomd || true
copy_root_file "$REPO_DIR/system/systemd/oomd-macubuntu.conf" /etc/systemd/oomd.conf.d/99-macubuntu.conf
systemctl_enable systemd-oomd || true

if grep -qE '^[^#].*\sswap\s' /etc/fstab 2>/dev/null; then
    if is_true "$REPLACE_DISK_SWAP"; then
        step "Backing up /etc/fstab and retiring disk swap in favor of ZRAM (--replace-swap)..."
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] backup /etc/fstab -> /etc/fstab.macubuntu.bak, comment disk swap & swapoff"
        else
            run sudo cp -n /etc/fstab /etc/fstab.macubuntu.bak || true
            run sudo sed -i -E '/^[^#].*\sswap\s/s/^/# /' /etc/fstab || true
            run sudo swapoff -a 2>/dev/null || true
            run sudo systemctl restart systemd-zram-setup@zram0.service 2>/dev/null \
                || run sudo systemctl restart zramswap 2>/dev/null || true
        fi
    else
        echo "    ℹ️  Existing disk swap detected in /etc/fstab — preserved safely (keeps Safe Sleep hibernate"
        echo "       viable while ZRAM takes priority via Priority=100). Pass --replace-swap to retire disk swap."
    fi
fi

if [[ "$DRY_RUN" != "1" ]] && ! grep -q '^/dev/zram' /proc/swaps 2>/dev/null; then
    record_manual "ZRAM activates cleanly after reboot (verify with: zramctl || swapon --show)"
fi
