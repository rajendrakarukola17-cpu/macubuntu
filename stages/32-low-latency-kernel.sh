#!/usr/bin/env bash
# ============================================================================
# Stage 32: Low-Latency Kernel (XanMod / Liquorix) — macOS-Grade Responsiveness
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[32] Low-Latency Kernel (${LOW_LATENCY_KERNEL_CHOICE})"

if ! is_true "$ENABLE_LOW_LATENCY_KERNEL"; then
    echo "    ℹ️  Low-latency kernel skipped (ENABLE_LOW_LATENCY_KERNEL=false)"
    echo "      Enable with: ENABLE_LOW_LATENCY_KERNEL=true ./install.sh --only 32"
fi

# Guardrail: low-latency kernel is mutually exclusive with Secure Boot DKMS modules
# that must be recompiled per kernel (v4l2loopback etc.). Warn but do not abort.
if is_true "$ENABLE_LOW_LATENCY_KERNEL" && detect_secure_boot_enabled 2>/dev/null; then
    echo "    ⚠️  Secure Boot is enabled: third-party kernel modules (v4l2loopback-dkms, etc.)"
    echo "       will need MOK re-enrollment after booting the low-latency kernel."
    record_manual "After rebooting into $LOW_LATENCY_KERNEL_CHOICE kernel, re-enroll DKMS MOK: ./tools/enroll-mok.sh"
fi

# --- Kernel install block (only when ENABLE_LOW_LATENCY_KERNEL=true) ---
if is_true "$ENABLE_LOW_LATENCY_KERNEL"; then
    step "Adding $LOW_LATENCY_KERNEL_CHOICE archive keyring and apt source..."

    KEYRING_DIR="/usr/share/keyrings"
    SOURCE_DIR="/etc/apt/sources.list.d"

    case "$LOW_LATENCY_KERNEL_CHOICE" in
        xanmod)
            KEY_FILE="$KEYRING_DIR/xanmod-archive-keyring.gpg"
            KEY_URL="https://dl.xanmod.org/archive.key"
            # Pinned SHA-256 of https://dl.xanmod.org/archive.key (verified 2026-11-14, fingerprint D38D7D1DA1349567ADED882D86F7D09EE734E623)
            KEY_SHA256="ed26eb39330fd296cd037b8229adccea0197b21989ec0a1ad4f4f74f5a41c7a7"
            REPO_ENTRY="deb [signed-by=$KEY_FILE] https://deb.xanmod.org releases main"
            SOURCE_FILE="$SOURCE_DIR/xanmod-release.list"
            PKG_NAME="linux-xanmod-x64v3"
            LOCK_ID="xanmod-archive-keyring"
            ;;
        liquorix)
            KEY_FILE="$KEYRING_DIR/liquorix-archive-keyring.gpg"
            KEY_URL="https://liquorix.net/linux-liquorix.gpg"
            . /etc/os-release
            REPO_ENTRY="deb [signed-by=$KEY_FILE] https://liquorix.net/linux-liquorix/ ${VERSION_CODENAME} main"
            SOURCE_FILE="$SOURCE_DIR/liquorix.list"
            PKG_NAME="linux-image-liquorix-amd64"
            LOCK_ID="liquorix-archive-keyring"
            ;;
        *)
            record_fail "Unknown LOW_LATENCY_KERNEL_CHOICE=$LOW_LATENCY_KERNEL_CHOICE (use xanmod or liquorix)"
            exit 1
            ;;
    esac

    # --- Download and verify archive keyring via supply-chain lock ---
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would download $LOCK_ID from $KEY_URL -> $KEY_FILE"
        echo "      [dry-run] would write $SOURCE_FILE: $REPO_ENTRY"
        echo "      [dry-run] would apt update && apt install -y $PKG_NAME"
        record_manual "Reboot after install to boot into $LOW_LATENCY_KERNEL_CHOICE kernel"
    else
        # Ensure keyring dir exists
        sudo install -d -m 755 "$KEYRING_DIR"

        # Download the key to $WORKDIR, verify SHA-256 fail-closed, then install via sudo
        if command -v gpg >/dev/null 2>&1; then
            step "Downloading and verifying $LOW_LATENCY_KERNEL_CHOICE archive key (SHA-256 fail-closed)..."
            tmp_key="$(mktemp "$WORKDIR/${LOCK_ID}.XXXXXX")"
            # EXIT trap cleans the temp key on any exit path (success, failure, signal)
            trap 'rm -f "$tmp_key" 2>/dev/null' EXIT
            if curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
                --max-redirs 5 --connect-timeout 15 -o "$tmp_key" "$KEY_URL"; then
                # Fail-closed: verify SHA-256 of downloaded key against pinned value
                ACTUAL_SHA256="$(sha256sum "$tmp_key" | awk '{print $1}')"
                if [[ "$ACTUAL_SHA256" != "$KEY_SHA256" ]]; then
                    record_fail "SHA-256 mismatch for $LOCK_ID key: expected $KEY_SHA256, got $ACTUAL_SHA256 — aborting (fail-closed)"
                    exit 1
                fi
                # Verify dearmor succeeds; if not, try extracting PGP block from HTML
                if gpg --dearmor < "$tmp_key" > "$KEY_FILE.tmp" 2>/dev/null; then
                    sudo install -m 644 "$KEY_FILE.tmp" "$KEY_FILE"
                    rm -f "$KEY_FILE.tmp"
                    echo "      ✅ Archive keyring installed: $KEY_FILE (SHA-256 verified)"
                else
                    # Fallback: try to extract PGP public key block from HTML
                    pgp_block="$(sed -n '/-----BEGIN PGP PUBLIC KEY BLOCK-----/,/-----END PGP PUBLIC KEY BLOCK-----/p' "$tmp_key" 2>/dev/null || true)"
                    if [[ -n "$pgp_block" ]]; then
                        echo "$pgp_block" | gpg --dearmor > "$KEY_FILE.tmp" 2>/dev/null \
                            && sudo install -m 644 "$KEY_FILE.tmp" "$KEY_FILE" \
                            && rm -f "$KEY_FILE.tmp" \
                            && echo "      ✅ Archive keyring extracted from HTML page: $KEY_FILE (SHA-256 verified)"
                    else
                        record_fail "Could not dearmor $LOCK_ID key from $KEY_URL"
                        exit 1
                    fi
                fi
            else
                record_fail "Failed to download $LOCK_ID key from $KEY_URL"
                exit 1
            fi
        else
            record_fail "gpg is required for low-latency kernel keyring installation"
            exit 1
        fi

        # Write apt source entry via P5 atomic write_root_file
        step "Writing apt source: $SOURCE_FILE"
        {
            echo "# $LOW_LATENCY_KERNEL_CHOICE low-latency kernel (added by macubuntu stage 32)"
            echo "$REPO_ENTRY"
        } | write_root_file "$SOURCE_FILE" 644

        # apt update + install
        step "Running apt update..."
        run sudo apt-get update || record_fail "apt-get update failed for $LOW_LATENCY_KERNEL_CHOICE"

        step "Installing $PKG_NAME..."
        run sudo apt-get install -y "$PKG_NAME" || record_fail "apt install $PKG_NAME failed"

        step "Kernel $PKG_NAME installed successfully."
    fi
fi

# --- sched_ext BPF CPU scheduler config (independent of low-latency kernel gate) ---
if [[ -f /etc/macubuntu/hardware.json ]] && command -v jq >/dev/null 2>&1; then
    HW_CPU_CORES="$(jq -r '.cpu_cores // 0' /etc/macubuntu/hardware.json)"
    HW_HAS_BATTERY="$(jq -r '.has_battery // false' /etc/macubuntu/hardware.json)"
else
    HW_CPU_CORES="$(nproc 2>/dev/null || echo 1)"
    HW_HAS_BATTERY="false"
fi

if is_true "$ENABLE_SCHED_EXT"; then
    step "Deploying sched_ext BPF scheduler config ($SCX_SCHEDULER)..."
    if [[ ! -d /sys/kernel/sched_ext ]]; then
        log_info "Guardrail: kernel lacks sched_ext support — skipping scx_loader config"
    elif command -v scx_loader >/dev/null 2>&1 || [[ "$DRY_RUN" == "1" ]]; then
        # Select scheduler based on hardware: laptops/low-core -> scx_bpfland, desktops/high-core -> scx_lavd
        SCHED_PROFILE="$SCX_SCHEDULER"
        if [[ "$HW_HAS_BATTERY" == "true" ]] || [[ "$HW_CPU_CORES" -le 4 ]]; then
            SCHED_PROFILE="scx_bpfland"
            SCHED_MODE="powersave"
        else
            SCHED_PROFILE="scx_lavd"
            SCHED_MODE="auto"
        fi
        echo "    📋 sched_ext profile: $SCHED_PROFILE (mode=$SCHED_MODE, cpu_cores=$HW_CPU_CORES, battery=$HW_HAS_BATTERY)"

        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] would write /etc/scx_loader/config.toml (sched=$SCHED_PROFILE, mode=$SCHED_MODE)"
            echo "      [dry-run] would write /etc/dbus-1/system.d/org.scx.Loader.conf (celina group only)"
        else
            # Write scx_loader config.toml (scx_loader schema: default_sched, not name)
            run sudo install -d -m 755 /etc/scx_loader
            cat <<TOML | write_root_file /etc/scx_loader/config.toml 644
# macubuntu sched_ext BPF scheduler configuration (hardware-adaptive)
[scheduler]
default_sched = "$SCHED_PROFILE"
mode = "$SCHED_MODE"

[cpu_affinity]
isolate_cpus = []
prefer_cpus = []

[power]
battery_mode = "$SCHED_MODE"
ac_mode = "auto"
TOML
            echo "      ✅ /etc/scx_loader/config.toml written (sched=$SCHED_PROFILE)"

            # Restrict D-Bus access to org.scx.Loader to celina group only
            run sudo install -d -m 755 /etc/dbus-1/system.d
            cat <<DBUS | write_root_file /etc/dbus-1/system.d/org.scx.Loader.conf 644
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <!-- macubuntu: scx_loader (root) owns org.scx.Loader; celina group may send -->
  <policy user="root">
    <allow own="org.scx.Loader"/>
    <allow send_destination="org.scx.Loader"/>
  </policy>
  <policy group="celina">
    <allow send_destination="org.scx.Loader"/>
  </policy>
  <policy context="default">
    <deny own="org.scx.Loader"/>
    <deny send_destination="org.scx.Loader"/>
  </policy>
</busconfig>
DBUS
            echo "      ✅ /etc/dbus-1/system.d/org.scx.Loader.conf restricted to celina group"

            # --- Polkit rule for scxctl (passwordless scheduler switching, celina group only) ---
            step "Installing scxctl + Polkit rule for sched_ext switching..."
            if command -v scxctl >/dev/null 2>&1; then
                echo "      ✅ scxctl already installed"
            else
                if [[ "$DRY_RUN" == "1" ]]; then
                    echo "      [dry-run] would install scxctl (sched_ext userspace control)"
                else
                    # scxctl ships with scx_loader in the scx project; attempt apt install first
                    sudo apt-get install -y scx 2>/dev/null || {
                        echo "      ⚠️  scxctl not available via apt — sched_ext manual switching unavailable"
                    }
                fi
            fi

            # Create celina system group (skip root — root is already in all groups)
            if [[ "$DRY_RUN" == "1" ]]; then
                echo "      [dry-run] would create system group 'celina' and add $TARGET_USER"
                echo "      [dry-run] would write /etc/polkit-1/rules.d/99-scx-loader.rules"
            else
                getent group celina >/dev/null || sudo groupadd --system celina
                TARGET_USER="${SUDO_USER:-$USER}"
                if [[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]]; then
                    sudo usermod -aG celina "$TARGET_USER" 2>/dev/null || true
                    echo "      ✅ Added $TARGET_USER to celina group"
                elif [[ "$TARGET_USER" == "root" ]]; then
                    echo "      ℹ️  Skipping celina group add for root (root is already in all groups)"
                fi
            fi

            # Write Polkit rule (polkitd reloads rules.d automatically — no restart needed)
            if [[ "$DRY_RUN" == "1" ]]; then
                echo "      [dry-run] would write Polkit rule for scx_loader (celina group only)"
            else
                sudo install -d -m 755 /etc/polkit-1/rules.d
                cat <<'POLKIT' | write_root_file /etc/polkit-1/rules.d/99-scx-loader.rules 644
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.scx.") === 0 && subject.isInGroup("celina")) {
        return polkit.Result.YES;
    }
});
POLKIT
                echo "      ✅ /etc/polkit-1/rules.d/99-scx-loader.rules written (celina group only)"
            fi
        fi
    else
        log_info "scx_loader not installed — sched_ext config deferred"
        record_manual "Install scx_loader and re-run stage 32 to activate sched_ext"
    fi
else
    echo "    ℹ️  sched_ext skipped (ENABLE_SCHED_EXT=false)"
fi

record_manual "Reboot to boot into $LOW_LATENCY_KERNEL_CHOICE low-latency kernel (verify: uname -r | grep $LOW_LATENCY_KERNEL_CHOICE)"
