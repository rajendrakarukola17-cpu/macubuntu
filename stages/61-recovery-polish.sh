#!/usr/bin/env bash
# ============================================================================
# Stage 61: Recovery & Polish — timeshift (btrfs-only), installer rollback
#           differentiation (exit 3), final polish
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[61] Recovery & Polish (timeshift btrfs-only, rollback distinction)"

# --- timeshift: btrfs-only system snapshots ---
# timeshift is installed only in btrfs mode (matches macOS Time Machine-style
# local snapshots). On non-btrfs filesystems it is skipped to avoid broken
# rsync-mode snapshots that give a false sense of safety.
step "Checking timeshift (btrfs-only system snapshots)..."

if ! is_true "$ENABLE_TIMESNAP"; then
    echo "    ℹ️  timeshift skipped (ENABLE_TIMESNAP=false; opt-in)"
elif is_true "$ENABLE_BTRFS_SNAPPER"; then
    echo "    ℹ️  timeshift skipped: ENABLE_BTRFS_SNAPPER=true (Snapper owns Btrfs snapshots — avoid competing snapshot managers)"
else
    if apt_has timeshift; then
        root_fstype="$(detect_root_fstype 2>/dev/null || echo unknown)"
        if [[ "$root_fstype" == "btrfs" ]]; then
            apt_install timeshift || record_fail "apt: timeshift"
            echo "      ✅ timeshift installed (btrfs root detected — btrfs mode active)"
            echo "      Configure: sudo timeshift --create --comments 'macubuntu baseline'"
        else
            echo "      ⏭️  timeshift skipped: root filesystem is ${root_fstype} (not btrfs)"
            echo "         timeshift btrfs mode requires a btrfs root. rsync mode is intentionally"
            echo "         NOT enabled (unreliable snapshots ≠ safety)."
            record_manual "To use timeshift, reinstall on btrfs or set ENABLE_TIMESNAP=false"
        fi
    else
        echo "      ℹ️  timeshift not in APT; skipping."
        record_manual "timeshift unavailable in APT; install from PPA if btrfs snapshots desired"
    fi
fi

# --- Snapper verification (btrfs APFS-style pre/post snapshots + GRUB rollback) ---
if is_true "$ENABLE_BTRFS_SNAPPER"; then
    step "Verifying Snapper + grub-btrfs bootable rollback..."
    ROOT_FS="$(detect_root_fstype 2>/dev/null || echo unknown)"
    if [[ "$ROOT_FS" != "btrfs" ]]; then
        echo "      ⏭️  Snapper skipped: root filesystem is ${ROOT_FS} (not btrfs)"
    elif command -v snapper >/dev/null 2>&1; then
        echo "      ✅ snapper installed"
        if [[ "$DRY_RUN" != "1" ]] && sudo snapper -c root list >/dev/null 2>&1; then
            SNAP_COUNT=$(sudo snapper -c root list 2>/dev/null | tail -n +4 | grep -c '.' || true)
            echo "      📸 Snapper snapshots: ${SNAP_COUNT}"
        elif [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] would run: sudo snapper -c root list"
        else
            echo "      ⚠️  snapper config 'root' not initialized — run: sudo snapper -c root create-config /"
        fi
        if command -v grub-btrfs-setup >/dev/null 2>&1 || dpkg-query -W -f='${Status}' grub-btrfs 2>/dev/null | grep -q "install ok installed"; then
            echo "      ✅ grub-btrfs installed — GRUB snapshot boot entries available"
        else
            echo "      ⚠️  grub-btrfs not installed — bootable snapshot recovery limited"
        fi
        if systemctl is-enabled snapper-timeline.timer >/dev/null 2>&1; then
            echo "      ✅ snapper-timeline.timer enabled (automated time-based snapshots)"
        else
            echo "      ⚠️  snapper-timeline.timer not enabled — run: sudo systemctl enable --now snapper-timeline.timer"
        fi
    else
        echo "      ⏭️  snapper not installed — ENABLE_BTRFS_SNAPPER=true but package missing"
    fi
else
    echo "    ℹ️  Snapper verification skipped (ENABLE_BTRFS_SNAPPER=false)"
fi

# --- systemd-sysext: instant unmerge to stock Ubuntu ---
# Provides macOS Sealed System Volume equivalence: removes the entire
# macubuntu OverlayFS layer in one operation, reverting to stock Ubuntu
# without reboot or filesystem rollback.
if command -v systemd-sysext >/dev/null 2>&1; then
    step "systemd-sysext status (macubuntu overlay layer)..."
    if [[ "$DRY_RUN" != "1" ]] && sudo systemd-sysext status 2>/dev/null | grep -q "macubuntu"; then
        echo "      ✅ macubuntu sysext is merged (active OverlayFS layer)"
        echo "      ℹ️  To revert to stock Ubuntu: sudo bash system/sysext/unmerge-sysext.sh"
    else
        echo "      ℹ️  macubuntu sysext not merged (install with --only 00 to activate)"
    fi
else
    echo "    ℹ️  systemd-sysext not available — sysext unmerge not supported"
fi

# --- Distinguish timeshift from ./install.sh --rollback (exit 3) ---
# The installer's rollback (./install.sh --rollback) restores P5 atomic R0
# snapshots (fstab, grub, etc.) via SNAPSHOT_ROOT manifest.json and exits 3
# on completion. timeshift is a SEPARATE, user-controlled btrfs snapshot tool:
#   • timeshift: btrfs subvolume snapshots, user-initiated, bootable recovery
#   • --rollback:  restores pre-change system files from installer snapshots
# These do not overlap — timeshift never touches the installer snapshot manifest.
echo ""
echo "────────────────────────────────────────────────────────────────"
echo "  Recovery mechanism distinction:"
echo "    timeshift (this stage)   → btrfs system snapshots (user-controlled)"
echo "    Snapper (stage 45/61)    → btrfs pre/post snapshots + apt + stage hooks"
echo "    ./install.sh --rollback   → P5 atomic R0 file restore, exit 3 on done"
echo "  They are independent. Use timeshift/Snapper for full-system rollback;"
echo "  use --rollback to undo a specific installer file change."
echo "────────────────────────────────────────────────────────────────"

# --- Phase A: self_heal — journald self-healing watcher (crash loops, PipeWire faults, GPU resets) ---
if is_true "$ENABLE_SELF_HEAL"; then
    step "Deploying self_heal journald watcher (crash loops, PipeWire faults, GPU resets)..."
    SELF_HEAL_DIR="$HOME/.local/share/celina"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would run: PYTHONPATH=\"$REPO_DIR/features/celina\" python3 -m celina_pkg.self_heal --once"
        echo "      [dry-run] would create ~/.config/systemd/user/celina-self-heal.service"
    else
        run_as_user install -d -m 700 "$SELF_HEAL_DIR"
        # Deploy real systemd user service for continuous self-heal watch
        cat <<SERVICE | write_user_file "${HOME}/.config/systemd/user/celina-self-heal.service" 644
[Unit]
Description=Celina Self-Heal journald watcher
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/env PYTHONPATH=$REPO_DIR/features/celina python3 -m celina_pkg.self_heal --once
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
SERVICE
        run_as_user systemctl --user daemon-reload 2>/dev/null || true
        echo "      ✅ celina-self-heal.service deployed ($HOME/.config/systemd/user/)"
        echo "      ℹ️  Run: systemctl --user start celina-self-heal.service  (or: PYTHONPATH=\"$REPO_DIR/features/celina\" python3 -m celina_pkg.self_heal --once)"
    fi
else
    echo "    ℹ️  self_heal skipped (ENABLE_SELF_HEAL=false)"
fi

# --- Final polish: refresh font cache, update desktop database, ldconfig ---
if [[ "$DRY_RUN" != "1" ]]; then
    step "Final polish (font cache, desktop DB, ldconfig)..."
    run fc-cache -f >/dev/null 2>&1 || true
    run_as_user update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
    run sudo ldconfig >/dev/null 2>&1 || true
fi

step "Recovery & polish stage complete."
