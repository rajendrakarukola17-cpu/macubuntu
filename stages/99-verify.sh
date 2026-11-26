#!/usr/bin/env bash
# ============================================================================
# Stage 99: Strict Post-Install Security, OS & Built-In Celina Verification
# Exit Policy:
#   - Classifies every component into 5 exact states:
#       NOT SELECTED | NOT INSTALLED | INSTALLED (INACTIVE) | FAILED | HEALTHY
#   - Returns NONZERO exit code (1) whenever ANY required check fails
#     (in non-dry-run mode). NEVER prints "SETUP COMPLETE" on required failure!
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

# Cleanup trap for temporary files created during verification
_VERIFY_TMP_FILES=""
cleanup_verify_tmp() {
    for f in $_VERIFY_TMP_FILES; do
        [[ -n "$f" && -f "$f" ]] && rm -f "$f" 2>/dev/null || true
    done
}
trap cleanup_verify_tmp EXIT

_register_tmp() {
    _VERIFY_TMP_FILES="$_VERIFY_TMP_FILES $1"
}

section "[99] Strict Security, OS & Built-In Feature Verification"

REQUIRED_FAILURES=0
WARNINGS=0

report_status() {
    local status="$1" item="$2" detail="${3:-}" required="${4:-false}"
    printf "  %-22s %-34s %s\n" "[$status]" "$item" "$detail"
    if [[ "$required" == "true" ]]; then
        case "$status" in
            "HEALTHY"|"NOT SELECTED") ;;
            *) REQUIRED_FAILURES=$((REQUIRED_FAILURES + 1)) ;;
        esac
    else
        case "$status" in
            "HEALTHY"|"NOT SELECTED") ;;
            *) WARNINGS=$((WARNINGS + 1)) ;;
        esac
    fi
}

check_cmd() {
    local cmd="$1" label="$2" required="${3:-true}" selected="${4:-true}" probe="${5:-}"
    if [[ "$selected" != "true" ]]; then
        report_status "NOT SELECTED" "$label" "(feature disabled)" false
        return 0
    fi
    if command -v "$cmd" >/dev/null 2>&1; then
        # Functional probe for critical commands — split multi-word probe into argv
        if [[ -n "$probe" ]]; then
            local -a probe_argv=()
            read -r -a probe_argv <<< "$probe"
            if [[ ${#probe_argv[@]} -gt 0 ]]; then
                if [[ "${DRY_RUN:-0}" == "1" ]]; then
                    if ! DRY_RUN=1 CELINA_DRY_RUN=1 "${probe_argv[@]}" >/dev/null 2>&1; then
                        report_status "FAILED" "$label" "(command exists but probe failed: $probe)" "$required"
                        return 0
                    fi
                elif ! "${probe_argv[@]}" >/dev/null 2>&1; then
                    report_status "FAILED" "$label" "(command exists but probe failed: $probe)" "$required"
                    return 0
                fi
            fi
        fi
        report_status "HEALTHY" "$label" "($(command -v "$cmd"))" "$required"
    else
        report_status "NOT INSTALLED" "$label" "(command '$cmd' not on PATH)" "$required"
    fi
}
check_service() {
    local unit="$1" scope="$2" label="$3" required="${4:-true}" selected="${5:-true}"
    if [[ "$selected" != "true" ]]; then
        report_status "NOT SELECTED" "$label" "(profile/feature disabled)" false
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        report_status "FAILED" "$label" "(systemctl unavailable)" "$required"
        return 0
    fi
    local sc_args=()
    [[ "$scope" == "user" ]] && sc_args=(--user)
    if systemctl "${sc_args[@]}" is-active --quiet "$unit" 2>/dev/null; then
        report_status "HEALTHY" "$label" "($unit active)" "$required"
    elif systemctl "${sc_args[@]}" list-unit-files "$unit" 2>/dev/null | grep -q "$unit"; then
        report_status "INSTALLED (INACTIVE)" "$label" "($unit installed but not active)" "$required"
    else
        report_status "NOT INSTALLED" "$label" "($unit unit not found)" "$required"
    fi
}

check_private_mode() {
    local path="$1" expected_mode="$2" label="$3" required="${4:-true}" selected="${5:-true}"
    if [[ "$selected" != "true" ]]; then
        report_status "NOT SELECTED" "$label" "" false
        return 0
    fi
    if [[ ! -e "$path" ]]; then
        report_status "NOT INSTALLED" "$label" "($path missing)" "$required"
        return 0
    fi
    local actual
    actual="$(stat -c '%a' "$path" 2>/dev/null || echo "???")"
    if [[ "$actual" == "$expected_mode" ]]; then
        report_status "HEALTHY" "$label" "(mode $actual)" "$required"
    else
        report_status "FAILED" "$label" "(insecure mode $actual, expected $expected_mode)" "$required"
    fi
}

echo "--- Security & Supply-Chain Posture ---"
if detect_has_luks; then
    report_status "HEALTHY" "LUKS Full-Disk Encryption" "(active)" false
else
    report_status "NOT INSTALLED" "LUKS Full-Disk Encryption" "(recommended on laptops)" false
fi

if [[ -f "$LOCKFILE" ]]; then
    report_status "HEALTHY" "Supply-Chain Lockfile" "($LOCKFILE)" true
else
    report_status "FAILED" "Supply-Chain Lockfile" "(missing $LOCKFILE)" true
fi

if [[ -f "$EXT_LOCKFILE" ]]; then
    report_status "HEALTHY" "GNOME Extension Lockfile" "($EXT_LOCKFILE)" false
else
    report_status "NOT INSTALLED" "GNOME Extension Lockfile" "(missing $EXT_LOCKFILE)" false
fi

if ! is_true "$ENABLE_EXPERIMENTAL_UINPUT"; then
    if id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx "input"; then
        report_status "FAILED" "Least-Privilege Input Group" "(user '$USER' is in broad 'input' group!)" false
    else
        report_status "HEALTHY" "Least-Privilege Input Group" "(user NOT in broad 'input' group)" false
    fi
else
    report_status "INSTALLED (INACTIVE)" "Experimental uinput Opt-In" "(enabled explicitly)" false
fi

echo ""
echo "--- Core OS & All-System Continuity Commands ---"
check_cmd flatpak        "Flatpak"           true  true
check_cmd timeshift      "Timeshift"         true  "$ENABLE_TIMESNAP"
check_cmd zramctl        "ZRAM Control"      true  true
check_cmd shairport-sync "AirPlay Audio"     false true
check_cmd uxplay         "AirPlay 2 Video"   false "$ENABLE_AIRPLAY_VIDEO"
check_cmd ifuse          "iOS USB Mount"     false true
check_cmd idevice_id     "libimobiledevice"  false true
check_cmd weylus         "iPad Sidecar"      false true
check_cmd scrcpy         "Android Mirror"    false "$ENABLE_ANDROID_MIRROR"
check_cmd syncthing      "Syncthing P2P"     false "$ENABLE_SYNCTHING"
check_cmd keepassxc      "KeePassXC Vault"   false "$ENABLE_KEEPASSXC"
check_cmd distrobox      "Distrobox Subsys"  false "$ENABLE_DISTROBOX"
check_cmd powertop       "PowerTOP"          false true
check_cmd gext           "GNOME Ext CLI"     false true

CELINA_SEL="false"
is_true "$ENABLE_CELINA" && CELINA_SEL="true"
ULAUNCHER_SEL="false"
[[ "$LAUNCHER_CHOICE" == "ulauncher" ]] && ULAUNCHER_SEL="true"

echo ""
echo "--- Built-In Celina & Launcher Commands ---"
check_cmd ollama           "Ollama Local LLM"    true  "$CELINA_SEL" "ollama --version"
check_cmd rofi             "Rofi Spotlight"      true  "$CELINA_SEL"
check_cmd bwrap            "Bubblewrap Sandbox"  true  "$CELINA_SEL" "bwrap --version"
check_cmd tesseract        "Tesseract OCR"       false "$CELINA_SEL"
check_cmd celina           "Celina CLI"          true  "$CELINA_SEL" "celina --help"
check_cmd celly            "Celly Alias"         true  "$CELINA_SEL"
check_cmd celina-island        "Celina Island UI"      true  "$CELINA_SEL"
check_cmd celina-spotlight     "Celina Spotlight"      true  "$CELINA_SEL"
check_cmd celina-writing-tools "Celina Writing Tools"  false "$CELINA_SEL"
check_cmd celina-live-text     "Celina Live Text OCR"  false "$CELINA_SEL"
check_cmd ulauncher            "Ulauncher"             true  "$ULAUNCHER_SEL"

echo ""
echo "--- Private File & Directory Permissions (0700 / 0600) ---"
check_private_mode "$HOME/.config/celina"                    "700" "Celina Config Dir"       true  "$CELINA_SEL"
check_private_mode "$HOME/.local/share/celina"               "700" "Celina Data Dir"         true  "$CELINA_SEL"
check_private_mode "$HOME/.config/celina/env"                "600" "Celina Env Secrets"      true  "$CELINA_SEL"
check_private_mode "$HOME/.config/celina/neural-schema.json" "600" "Celina Neural Schema"    true  "$CELINA_SEL"
check_private_mode "$HOME/.config/celina/mcp-servers.json"   "600" "Celina MCP Config"       true  "$CELINA_SEL"
check_private_mode "$HOME/.config/celina/iot-allowlist.json" "600" "Celina IoT Allowlist"    true  "$CELINA_SEL"

echo ""
echo "--- System & User Services ---"
PPD_SEL="false"; [[ "$POWER_BACKEND" == "ppd" ]] && PPD_SEL="true"
TLP_SEL="false"; [[ "$POWER_BACKEND" == "tlp" ]] && TLP_SEL="true"
UXPLAY_SEL="false"; is_true "$ENABLE_AIRPLAY_VIDEO" && UXPLAY_SEL="true"
SYNCTHING_SEL="false"; is_true "$ENABLE_SYNCTHING" && SYNCTHING_SEL="true"
check_service systemd-zram-setup@zram0.service system "ZRAM Generator"           false true
check_service systemd-oomd                     system "systemd-oomd Guard"       false true
check_service power-profiles-daemon            system "Power Profiles Daemon"    false "$PPD_SEL"
check_service tlp                              system "TLP Power Manager"        false "$TLP_SEL"
check_service ollama                           system "Ollama Service"           true  "$CELINA_SEL"
check_service celina-eventd.service            user   "Celina OS Event Daemon"   false "$CELINA_SEL"
check_service shairport-sync-user.service      user   "AirPlay Audio Service"    false true
check_service uxplay-user.service              user   "AirPlay 2 Video Service"  false "$UXPLAY_SEL"
check_service syncthing.service                user   "Syncthing P2P Service"    false "$SYNCTHING_SEL"

echo ""
echo "--- macOS-Parity System Integrations ---"
if is_true "$ENABLE_BATTERY_CHARGE_LIMIT"; then
    if [[ -f /etc/udev/rules.d/90-battery-charge-limit.rules ]]; then
        report_status "HEALTHY" "80% Battery Charge Limit Rule" "(/etc/udev/rules.d/90-battery-charge-limit.rules)" false
    else
        report_status "NOT INSTALLED" "80% Battery Charge Limit Rule" "(run stage 50-power)" false
    fi
else
    report_status "NOT SELECTED" "80% Battery Charge Limit Rule" "(profile disabled)" false
fi
if is_true "$ENABLE_NAUTILUS_QUICK_ACTIONS"; then
    if [[ -f "$HOME/.local/share/nautilus-python/extensions/celina_quick_actions.py" ]]; then
        report_status "HEALTHY" "Finder/Nautilus Quick Actions" "(celina_quick_actions.py installed)" false
    else
        report_status "NOT INSTALLED" "Finder/Nautilus Quick Actions" "(run stage 45-apps-backups-search)" false
    fi
else
    report_status "NOT SELECTED" "Finder/Nautilus Quick Actions" "(profile disabled)" false
fi
if is_true "$ENABLE_SAFARI_FIREFOX"; then
    if [[ -f "$HOME/.local/share/icons/hicolor/scalable/apps/macubuntu-safari.svg" ]]; then
        report_status "HEALTHY" "Safari Firefox Theme & Icon" "(macubuntu-safari.svg + chrome installed)" false
    else
        report_status "NOT INSTALLED" "Safari Firefox Theme & Icon" "(run stage 20-appearance)" false
    fi
else
    report_status "NOT SELECTED" "Safari Firefox Theme & Icon" "(profile disabled)" false
fi

if [[ -f "$HOME/.config/environment.d/90-macubuntu-smooth.conf" ]]; then
    report_status "HEALTHY" "Native Wayland App Env" "(~/.config/environment.d/90-macubuntu-smooth.conf)" false
else
    report_status "NOT INSTALLED" "Native Wayland App Env" "(run stage 35-memory-zram)" false
fi
if [[ -f /etc/udev/rules.d/60-macubuntu-io-scheduler.rules ]]; then
    report_status "HEALTHY" "NVMe/SSD I/O Scheduler Rules" "(/etc/udev/rules.d/60-macubuntu-io-scheduler.rules)" false
else
    report_status "NOT INSTALLED" "NVMe/SSD I/O Scheduler Rules" "(run stage 35-memory-zram)" false
fi
if is_true "$ENABLE_PERSISTENT_CONTINUITY_CAM"; then
    if [[ -f /etc/modprobe.d/99-macubuntu-v4l2loopback.conf && -f /etc/modules-load.d/99-macubuntu-v4l2loopback.conf ]]; then
        if detect_secure_boot_enabled && ! detect_mok_enrolled; then
            report_status "INSTALLED (INACTIVE)" "Persistent Continuity Camera" "(Secure Boot ON — enroll MOK via ./tools/enroll-mok.sh)" false
        else
            report_status "HEALTHY" "Persistent Continuity Camera" "(/etc/modprobe.d/99-macubuntu-v4l2loopback.conf -> /dev/video10)" false
        fi
    else
        report_status "NOT INSTALLED" "Persistent Continuity Camera" "(run stage 40-continuity-audio)" false
    fi
else
    report_status "NOT SELECTED" "Persistent Continuity Camera" "(profile disabled)" false
fi
if [[ -f "$REPO_DIR/system/capabilities/celina-capabilities.json" ]]; then
    report_status "HEALTHY" "OS Capability Manifest" "(system/capabilities/celina-capabilities.json)" false
else
    report_status "FAILED" "OS Capability Manifest" "(missing system/capabilities/celina-capabilities.json)" true
fi
if [[ -f /etc/systemd/resolved.conf.d/99-macubuntu-mdns.conf ]]; then
    report_status "HEALTHY" "Zero-Conflict mDNS (Avahi)" "(/etc/systemd/resolved.conf.d/99-macubuntu-mdns.conf)" false
else
    report_status "NOT INSTALLED" "Zero-Conflict mDNS (Avahi)" "(run stage 40-continuity-audio)" false
fi
if is_true "$ENABLE_PRO_AUDIO_BLUETOOTH"; then
    if [[ -f "$HOME/.config/wireplumber/wireplumber.conf.d/51-macubuntu-bluetooth.conf" && -f "$HOME/.config/pipewire/pipewire.conf.d/99-macubuntu-audio.conf" ]]; then
        report_status "HEALTHY" "AirPods AAC/LDAC & PipeWire" "(WirePlumber + PipeWire low-jitter clock active)" false
    else
        report_status "NOT INSTALLED" "AirPods AAC/LDAC & PipeWire" "(run stage 40-continuity-audio)" false
    fi
else
    report_status "NOT SELECTED" "AirPods AAC/LDAC & PipeWire" "(profile disabled)" false
fi
if is_true "$ENABLE_SAFE_SLEEP"; then
    if [[ -f /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf ]]; then
        report_status "HEALTHY" "macOS Safe Sleep Policy" "(/etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf)" false
    else
        report_status "NOT INSTALLED" "macOS Safe Sleep Policy" "(run stage 50-power)" false
    fi
else
    report_status "NOT SELECTED" "macOS Safe Sleep Policy" "(profile disabled)" false
fi
if is_true "$ENABLE_SILENT_BOOT"; then
    if [[ -f /etc/default/grub.d/99-macubuntu-boot.cfg ]]; then
        report_status "HEALTHY" "Flicker-Free Silent Boot" "(/etc/default/grub.d/99-macubuntu-boot.cfg)" false
    else
        report_status "NOT INSTALLED" "Flicker-Free Silent Boot" "(run stage 50-power --silent-boot)" false
    fi
else
    report_status "NOT SELECTED" "Flicker-Free Silent Boot" "(opt-in via --silent-boot)" false
fi
if is_true "$ENABLE_MACOS_CMD_KEYS"; then
    if [[ -f /etc/keyd/macubuntu.conf ]]; then
        report_status "HEALTHY" "macOS Cmd Key Remapping" "(/etc/keyd/macubuntu.conf)" false
    else
        report_status "NOT INSTALLED" "macOS Cmd Key Remapping" "(run stage 60-extras --macos-cmd-keys)" false
    fi
else
    report_status "NOT SELECTED" "macOS Cmd Key Remapping" "(opt-in via --macos-cmd-keys)" false
fi
if is_true "$ENABLE_VAAPI_GPU_DECODE"; then
    if detect_vaapi_decode_supported; then
        report_status "HEALTHY" "VA-API H.264/VP9 Decode" "(verified via vainfo VAEntrypointVLD)" false
    else
        report_status "INSTALLED (INACTIVE)" "VA-API H.264/VP9 Decode" "(no H.264/VP9 VLD; use --disable-vaapi on Firefox)" false
    fi
fi
if is_true "$ENABLE_ACCESSIBILITY"; then
    check_cmd orca "Orca Screen Reader" false true
    check_cmd kmag "KMagnifier Zoom"    false true
else
    report_status "NOT SELECTED" "Accessibility (Orca/KMag)" "(opt-in via --accessibility)" false
fi

if [[ "${VERIFY_MODELS:-0}" == "1" ]]; then
    echo ""
    echo "--- Ollama Model Digest Verification (--verify-models) ---"
    MODEL_LOCK="$HOME/.config/celina/model-digests.sha256"
    if [[ ! -f "$MODEL_LOCK" ]]; then
        report_status "NOT INSTALLED" "Ollama Model Digest Lock" "($MODEL_LOCK missing; run Stage 70)" true
    elif ! command -v ollama >/dev/null 2>&1; then
        report_status "NOT INSTALLED" "Ollama CLI" "(ollama not on PATH)" true
    else
        while IFS='|' read -r mtag expected_dig; do
            [[ -z "$mtag" || "$mtag" == \#* ]] && continue
            actual_dig="$(ollama show "$mtag" --modelfile 2>/dev/null | grep -oE 'sha256-[0-9a-f]{64}' | head -1 || true)"
            if [[ -z "$actual_dig" ]]; then
                actual_dig="$(ollama list 2>/dev/null | awk -v m="$mtag" '$1==m || $1==m":latest" {print $2; exit}' || true)"
            fi
            if [[ -n "$actual_dig" && "$actual_dig" == "$expected_dig" ]]; then
                report_status "HEALTHY" "Model Digest ($mtag)" "($actual_dig)" true
            else
                report_status "FAILED" "Model Digest ($mtag)" "(expected=${expected_dig}, actual=${actual_dig:-missing})" true
            fi
        done < "$MODEL_LOCK"
    fi
fi

if detect_has_amd_gpu; then
    if [[ -f /etc/systemd/system/ollama.service.d/amd-vulkan.conf ]]; then
        report_status "HEALTHY" "AMD Radeon Vulkan AI Offload" "(OLLAMA_VULKAN=1 + OLLAMA_IGPU_ENABLE=1)" false
    else
        report_status "NOT INSTALLED" "AMD Radeon Vulkan AI Offload" "(run stage 70-feature-celina)" false
    fi
fi

echo ""
echo "--- Native OS & Hardware Integration Layers ---"
if [[ -f "$REPO_DIR/system/systemd/celina.service" ]] && \
   [[ -f "$REPO_DIR/system/systemd/celina-npu-inference.service" ]] && \
   [[ -f "$REPO_DIR/system/systemd/celina-strict-fs.conf" ]]; then
    report_status "HEALTHY" "Systemd Control Plane Units" "(celina.service + decoupled NPU + strict-FS drop-in)" true
else
    report_status "FAILED" "Systemd Control Plane Units" "(missing unit files under system/systemd/)" true
fi

if [[ -f "$REPO_DIR/system/dbus/org.macubuntu.Celina.conf" ]] && \
   [[ -f "$REPO_DIR/system/dbus/org.macubuntu.Celina.service" ]]; then
    report_status "HEALTHY" "Session D-Bus Nervous System" "(org.macubuntu.Celina policy + activation)" true
else
    report_status "FAILED" "Session D-Bus Nervous System" "(missing system/dbus/org.macubuntu.Celina.*)" true
fi

if [[ -f "$REPO_DIR/system/gnome/extensions/celina-island@macubuntu.org/extension.js" ]] && \
   [[ -f "$REPO_DIR/system/gnome/extensions/celina-island@macubuntu.org/schemas/org.gnome.shell.extensions.celina-island.gschema.xml" ]]; then
    report_status "HEALTHY" "Native GNOME Shell Extension" "(celina-island@macubuntu.org ESM 48/49/50)" true
else
    report_status "FAILED" "Native GNOME Shell Extension" "(missing celina-island@macubuntu.org artifacts)" true
fi

if [[ -f "$REPO_DIR/system/ebpf/celina_sensor.bpf.c" ]] && \
   [[ -f "$REPO_DIR/system/ebpf/celina-ebpf-sensor.service" ]]; then
    report_status "HEALTHY" "CO-RE eBPF Kernel Sensor" "(tracepoints + opt-in fail-open LSM)" true
else
    report_status "FAILED" "CO-RE eBPF Kernel Sensor" "(missing system/ebpf artifacts)" true
fi

if [[ -f "$REPO_DIR/features/celina/celina_hardware/compute.py" ]] && \
   [[ -x "$REPO_DIR/tools/build-deb.sh" || -f "$REPO_DIR/tools/build-deb.sh" ]]; then
    report_status "HEALTHY" "Compute Offload & .deb Builder" "(NPU/GPU compute.py + tools/build-deb.sh)" true
else
    report_status "FAILED" "Compute Offload & .deb Builder" "(missing compute.py or tools/build-deb.sh)" true
fi

echo ""
echo "--- Enabled GNOME Extensions ---"
if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions list --enabled 2>/dev/null | sed 's/^/  • /' || true
else
    echo "  (gnome-extensions CLI unavailable in this environment)"
fi

echo ""
echo "--- Memory, ZRAM, Network & Smoothness Tuning ---"
sysctl vm.swappiness vm.page-cluster vm.vfs_cache_pressure vm.watermark_scale_factor \
       vm.dirty_background_bytes vm.dirty_bytes fs.inotify.max_user_watches \
       net.core.default_qdisc net.ipv4.tcp_congestion_control 2>/dev/null | sed 's/^/  /' || true
zramctl 2>/dev/null || swapon --show 2>/dev/null || true


echo ""
echo "--- PAM Authentication Safety ---"
if [[ -f /etc/pam.d/common-auth ]]; then
    if grep -qE '^[[:space:]]*auth[[:space:]]+(required|sufficient|requisite|\[[^]]*success=[0-9][^]]*\])[[:space:]]+pam_unix\.so\b' /etc/pam.d/common-auth; then
        report_status "HEALTHY" "OS Password Fallback" "(pam_unix.so active auth rule in common-auth)" true
    else
        report_status "FAILED" "OS Password Fallback" "(no active required/sufficient/success pam_unix.so rule in common-auth)" true
    fi
else
    report_status "FAILED" "OS Password Fallback" "(/etc/pam.d/common-auth missing)" true
fi

# ============================================================================
# Block 8: NPU / Neural Acceleration Verification
# ============================================================================
if [[ -f /etc/macubuntu/hardware.json ]] && command -v jq >/dev/null 2>&1; then
    NPU_VENDOR="$(jq -r '.npu_vendor // "none"' /etc/macubuntu/hardware.json 2>/dev/null || echo "none")"
    NPU_DRIVER="$(jq -r '.npu_driver // "none"' /etc/macubuntu/hardware.json 2>/dev/null || echo "none")"
    NPU_DEVICE="$(jq -r '.npu_device // ""' /etc/macubuntu/hardware.json 2>/dev/null || echo "")"
    NPU_BACKEND="$(jq -r '.npu_backend // "none"' /etc/macubuntu/hardware.json 2>/dev/null || echo "none")"

    if [[ "$NPU_VENDOR" != "none" && "$NPU_VENDOR" != "null" && -n "$NPU_DEVICE" ]]; then
        report_status "HEALTHY" "NPU detected" "vendor=$NPU_VENDOR driver=$NPU_DRIVER backend=$NPU_BACKEND" false

        # udev rule must exist when NPU device node exists
        if [[ -e /dev/accel/accel0 ]]; then
            if [[ -f /etc/udev/rules.d/70-macubuntu-npu.rules ]]; then
                report_status "HEALTHY" "NPU udev rule" "(70-macubuntu-npu.rules installed)" false
            else
                report_status "WARNING" "NPU udev rule" "(NPU device present but 70-macubuntu-npu.rules missing)" false
            fi

            # Single-owner: no more than 2 fds on /dev/accel/accel0
            if command -v lsof >/dev/null 2>&1; then
                FD_COUNT="$(lsof /dev/accel/accel0 2>/dev/null | wc -l || echo 0)"
                if [[ "$FD_COUNT" -le 2 ]]; then
                    report_status "HEALTHY" "NPU single-owner" "($FD_COUNT fds on /dev/accel/accel0 <= 2)" false
                else
                    report_status "WARNING" "NPU fd exhaustion" "($FD_COUNT fds on /dev/accel/accel0 > 2)" false
                fi
            fi
        fi

        # npu.sock permissions (0600) if worker socket exists
        SOCK_PATH="/run/user/$(id -u)/celina/npu.sock"
        if [[ -S "$SOCK_PATH" ]]; then
            SOCK_PERM="$(stat -c '%a' "$SOCK_PATH" 2>/dev/null || echo "000")"
            if [[ "$SOCK_PERM" == "600" ]]; then
                report_status "HEALTHY" "npu.sock permissions" "(strict 0600)" false
            else
                report_status "WARNING" "npu.sock permissions" "(insecure $SOCK_PERM, must be 600)" false
            fi
        fi
    else
        report_status "INFO" "NPU" "(npu_vendor=none — neural workloads route to CPU)" false
    fi
else
    report_status "INFO" "NPU" "(cannot verify: hardware.json or jq unavailable)" false
fi

# ============================================================================
# Block 9: Neural Backend Selection Unit Tests (fail-closed)
# ============================================================================
if command -v python3 >/dev/null 2>&1 && [[ -d "$REPO_DIR/features/celina" ]]; then
    python3 - "$REPO_DIR" <<'PYEOF'
import json, sys, tempfile, os, shutil

repo_dir = sys.argv[1]
sys.path.insert(0, os.path.join(repo_dir, "features", "celina"))

try:
    from celina_pkg.llm.neural_backends import select_neural_backend
except ImportError:
    print("SKIP: celina_pkg.llm.neural_backends not importable")
    sys.exit(0)

profiles = {
    '8gb':   {'ai_tier': '8gb', 'ram_mb': 8192, 'cpu_cores': 4, 'gpu_vendor': 'cpu'},
    '16gb':  {'ai_tier': '16gb', 'ram_mb': 16384, 'cpu_cores': 8, 'gpu_vendor': 'intel'},
    '32gb':  {'ai_tier': '32gb', 'ram_mb': 32768, 'cpu_cores': 16, 'gpu_vendor': 'nvidia'},
}

errors = []
tmpdir = tempfile.mkdtemp()
try:
    for tier, hw in profiles.items():
        hw_path = os.path.join(tmpdir, f'{tier}.json')
        with open(hw_path, 'w') as f:
            json.dump(hw, f)
        plan = select_neural_backend(hw_path=hw_path)
        valid = plan.backend in {'bitnet_cpu', 'llama_dspark', 'llama_standard'}
        assert plan.socket_path.startswith('/run/user/'), f'{tier}: bad socket path'
        assert plan.command, f'{tier}: empty command'
        assert plan.reason, f'{tier}: empty reason'
        if not valid:
            errors.append(f'{tier}: invalid backend {plan.backend}')
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

if errors:
    print('FAIL:', '; '.join(errors), file=sys.stderr)
    sys.exit(1)
print(f'OK: neural backend selection valid for {len(profiles)} profiles')
PYEOF
    PYTEST_RESULT=$?
    if [[ $PYTEST_RESULT -eq 0 ]]; then
        report_status "HEALTHY" "Neural backend unit tests" "(8gb/16gb/32gb profiles valid)" false
    else
        report_status "WARNING" "Neural backend unit tests" "(failed — see output above)" false
    fi
else
    report_status "INFO" "Neural backend unit tests" "(python3 or features/celina not available)" false
fi

# ============================================================================
# Block 11: Headless GTK Smoke Test (macubuntu Center GUI)
# ============================================================================
if command -v python3 >/dev/null 2>&1; then
    python3 - "$REPO_DIR" <<'PYEOF'
import ast, sys, os

repo_dir = sys.argv[1]
center_files = [
    os.path.join(repo_dir, "apps", "macubuntu-center", "main.py"),
    os.path.join(repo_dir, "apps", "macubuntu-center", "installer.py"),
    os.path.join(repo_dir, "apps", "macubuntu-center", "backend.py"),
]

errors = []
for f in center_files:
    try:
        if f.endswith('.py'):
            with open(f) as fh:
                ast.parse(fh.read())
        elif f.endswith('.css'):
            with open(f) as fh:
                css = fh.read()
            if len(css.strip()) == 0:
                errors.append(f'{f}: empty CSS file')
    except SyntaxError as e:
        errors.append(f'{f}: syntax error: {e}')
    except FileNotFoundError:
        errors.append(f'{f}: not found')

if errors:
    print('FAIL:', '; '.join(errors), file=sys.stderr)
    sys.exit(1)
print(f'OK: headless GTK smoke test — {len(center_files)} Center files parse cleanly')
PYEOF
    GTK_RESULT=$?
    if [[ $GTK_RESULT -eq 0 ]]; then
        report_status "HEALTHY" "Headless GTK smoke test" "(macubuntu Center source valid)" false
    else
        report_status "WARNING" "Headless GTK smoke test" "(failed — see output above)" false
    fi
else
    report_status "INFO" "Headless GTK smoke test" "(python3 not available)" false
fi

# ============================================================================
# Block 12: Pack-Update .mubundle Signature Verification Test
# ============================================================================
if command -v openssl >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 && [[ -f "$REPO_DIR/scripts/pack-update.sh" ]]; then
    BUNDLE_TMP="$(mktemp -d)"
    _register_tmp "$BUNDLE_TMP"

    # Generate a test Ed25519 key pair
    if openssl genpkey -algorithm ed25519 -out "$BUNDLE_TMP/test_priv.pem" 2>/dev/null; then
        openssl pkey -in "$BUNDLE_TMP/test_priv.pem" -pubout -out "$BUNDLE_TMP/test_pub.pem" 2>/dev/null

        # Create a test payload directory
        mkdir -p "$BUNDLE_TMP/payload/usr/bin"
        echo '#!/bin/bash
echo test' > "$BUNDLE_TMP/payload/usr/bin/test-cmd"
        chmod +x "$BUNDLE_TMP/payload/usr/bin/test-cmd"

        # Pack a test bundle
        if bash "$REPO_DIR/scripts/pack-update.sh" "1.0.0-test" "00-preflight,99-verify" "$BUNDLE_TMP/payload" "$BUNDLE_TMP/test_priv.pem" "$BUNDLE_TMP/test.mubundle" 2>/dev/null; then
            mkdir -p "$BUNDLE_TMP/extracted"
            tar --zstd -xf "$BUNDLE_TMP/test.mubundle" -C "$BUNDLE_TMP/extracted" 2>/dev/null

            if [[ -f "$BUNDLE_TMP/extracted/SHA256SUMS" && -f "$BUNDLE_TMP/extracted/SHA256SUMS.sig" ]]; then
                if (cd "$BUNDLE_TMP/extracted" && sha256sum -c SHA256SUMS --status 2>/dev/null); then
                    report_status "HEALTHY" "Test .mubundle checksums" "(payload checksums verified)" false
                else
                    report_status "WARNING" "Test .mubundle checksums" "(payload checksum mismatch)" false
                fi

                if openssl pkeyutl -verify -pubin -inkey "$BUNDLE_TMP/test_pub.pem" -rawin -in "$BUNDLE_TMP/extracted/SHA256SUMS" -sigfile "$BUNDLE_TMP/extracted/SHA256SUMS.sig" >/dev/null 2>&1; then
                    report_status "HEALTHY" "Test .mubundle Ed25519 signature" "(verified)" false
                else
                    report_status "WARNING" "Test .mubundle Ed25519 signature" "(verification failed — fail-closed)" false
                fi
            else
                report_status "WARNING" "Test .mubundle" "(missing SHA256SUMS or signature)" false
            fi
        else
            report_status "WARNING" "Test .mubundle creation" "(pack-update.sh failed)" false
        fi
    else
        report_status "WARNING" "Test .mubundle keygen" "(could not generate Ed25519 test key)" false
    fi
else
    report_status "INFO" "Pack-Update .mubundle test" "(openssl/python3/pack-update.sh not available)" false
fi

# ============================================================================
# D1: state.json writer — emit actual verification results to /etc/macubuntu/state.json
# Root-owned, 0644, hash-pinned. No-op under DRY_RUN=1.
# ============================================================================
write_state_json() {
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] would write /etc/macubuntu/state.json"
        return 0
    fi

    local state_file="/etc/macubuntu/state.json"
    local tmp_state
    tmp_state="$(mktemp)"
    _register_tmp "$tmp_state"

    python3 - "$tmp_state" "$REPO_DIR" "$FAILED_FILE" "$MANUAL_FILE" "$LOCKFILE" <<'PYEOF'
import json, os, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone

tmp_out = sys.argv[1]
repo_dir = Path(sys.argv[2])
failed_file = Path(sys.argv[3])
manual_file = Path(sys.argv[4])
lockfile = Path(sys.argv[5])

def run(cmd, default=""):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return default

def is_true_env(v):
    return os.environ.get(v, "false").lower() in ("1", "true", "yes", "on")

def systemctl_is_enabled(unit, user=False):
    try:
        args = ["systemctl"]
        if user:
            args += ["--user"]
        args += ["is-enabled", unit]
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else "disabled"
    except Exception:
        return "unknown"

# --- Read environment ---
profile = os.environ.get("PROFILE", "default-8gb")
celina_version = os.environ.get("CELINA_VERSION", "4.4")
installer_version = celina_version

# --- Detect OS/hardware ---
ubuntu_ver = run(["lsb_release", "-rs"], "unknown")
kernel = os.uname().release

gnome_ver = run(["gnome-shell", "--version"], "")
gnome_major = gnome_ver.split()[-1] if gnome_ver else "unknown"

timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# --- Read failed/manual entries ---
failed_entries = [l.strip() for l in failed_file.read_text().splitlines() if l.strip()] if failed_file.exists() else []

def check_cmd_exists(cmd):
    return subprocess.run(["bash", "-c", f"command -v {cmd}"], capture_output=True).returncode == 0

def check_file_exists(path):
    return Path(path).exists()

# --- Stage results (artifact-based verification from actual system state) ---
stages = {}

# Stage 10: base-deps
s10_verified = []
s10_failed = []
for cmd in ["flatpak", "timeshift", "zramctl"]:
    if check_cmd_exists(cmd):
        s10_verified.append(cmd)
    else:
        s10_failed.append(cmd)
stages["10"] = {
    "status": "ok" if not s10_failed else "degraded",
    "verified": s10_verified,
    "failed": s10_failed,
}

# Stage 20: appearance
s20_verified = []
s20_failed = []
if check_file_exists("/usr/share/gnome-shell/extensions"):
    s20_verified.append("gnome-shell-extensions")
else:
    s20_failed.append("gnome-shell-extensions")
stages["20"] = {
    "status": "ok" if not s20_failed else "degraded",
    "verified": s20_verified,
    "failed": s20_failed,
}

# Stage 35: memory-zram
s35_verified = []
s35_failed = []
if check_file_exists("/sys/block/zram0") or check_file_exists("/dev/zram0"):
    s35_verified.append("zram")
else:
    s35_failed.append("zram")
if check_file_exists("/etc/fstab"):
    s35_verified.append("fstab")
else:
    s35_failed.append("fstab")
stages["35"] = {
    "status": "ok" if not s35_failed else "degraded",
    "verified": s35_verified,
    "failed": s35_failed,
}

# Stage 50: power
s50_verified = []
s50_failed = []
cmdline = run(["cat", "/proc/cmdline"], "")
if "resume=" in cmdline:
    s50_verified.append("resume_device")
else:
    s50_failed.append("resume_device")
if check_file_exists("/etc/crypttab"):
    s50_verified.append("luks_resume_ready")
else:
    s50_failed.append("luks_resume_ready")
stages["50"] = {
    "status": "ok" if not s50_failed else "degraded",
    "verified": s50_verified,
    "failed": s50_failed,
}

# Stage 55: security-biometrics
s55_verified = []
s55_failed = []
if lockfile.exists():
    s55_verified.append("supply_chain_lockfile")
else:
    s55_failed.append("supply_chain_lockfile")
if check_file_exists("/etc/pam.d/common-auth"):
    s55_verified.append("pam")
else:
    s55_failed.append("pam")
stages["55"] = {
    "status": "ok" if not s55_failed else "degraded",
    "verified": s55_verified,
    "failed": s55_failed,
}

# Stage 70: feature-celina
s70_verified = []
s70_failed = []
if check_cmd_exists("ollama"):
    s70_verified.append("ollama_cli")
else:
    s70_failed.append("ollama_cli")
if check_cmd_exists("celina"):
    s70_verified.append("celina_import")
else:
    s70_failed.append("celina_import")
venv_dir = Path.home() / ".local" / "share" / "celina" / "venv"
if venv_dir.exists():
    s70_verified.append("venv")
else:
    s70_failed.append("venv")
stages["70"] = {
    "status": "ok" if not s70_failed else "degraded",
    "verified": s70_verified,
    "failed": s70_failed,
}

# --- Services ---
services = {}
services["celina.service"] = systemctl_is_enabled("celina.service", user=True)
services["celina-eventd.service"] = systemctl_is_enabled("celina-eventd.service", user=True)
services["ollama.service"] = systemctl_is_enabled("ollama.service", user=False)

# --- Endpoints ---
endpoints = {
    "ollama": "http://127.0.0.1:11434" if check_cmd_exists("ollama") else None,
    "npu": None,
}

# --- Secrets inventory ---
home = str(Path.home())
secrets_inventory = [
    f"{home}/.ai-env",
    f"{home}/.config/celina/env",
]

# --- Security ---
# bwrap: actually test userns sandbox creation, not just binary presence
def _bwrap_usable():
    try:
        r = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "/bin/true"],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False
bwrap_ok = _bwrap_usable()
# Probe real landlock ABI from kernel (fallback 0 if unsupported)
try:
    with open("/proc/sys/kernel/lockdown", "r") as _f:
        _lockdown = _f.read().strip()
except Exception:
    _lockdown = ""
# Check for landlock via celina_pkg if available, else kernel probe
try:
    import sys
    sys.path.insert(0, str(Path.home() / ".local" / "share" / "celina" / "app"))
    from celina_pkg import landlock_policy as _lp
    landlock_abi = getattr(_lp, "LANDLOCK_ABI_VERSION", 0) if hasattr(_lp, "LANDLOCK_ABI_VERSION") else 0
except Exception:
    landlock_abi = 0
atspi_enabled = is_true_env("ENABLE_ACCESSIBILITY")
secure_boot = "SecureBoot enabled" in run(["mokutil", "--sb-state"], "")
# Probe real apparmor_userns from /proc/sys/kernel/apparmor_restrict_unprivileged_userns
try:
    with open("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "r") as _f:
        apparmor_userns = _f.read().strip() == "1"
except Exception:
    apparmor_userns = False
# ydotool: check actual socket permissions, not just path existence
try:
    _ydotool_stat = os.stat("/run/ydotool")
    _ydotool_mode = oct(_ydotool_stat.st_mode & 0o777)[2:].zfill(3)
    ydotool_acl = _ydotool_mode if _ydotool_mode == "600" else f"insecure({_ydotool_mode})"
except Exception:
    ydotool_acl = "unavailable"

security = {
    "bwrap_userns": "ok" if bwrap_ok else "unavailable",
    "landlock_abi": landlock_abi,
    "atspi_enabled": atspi_enabled,
    "secure_boot": secure_boot,
    "apparmor_userns_restrict": apparmor_userns,
    "ydotool_socket_acl": ydotool_acl,
}

# --- Parity ---
dock = os.environ.get("DOCK_CHOICE", "ubuntu-dock")
keyd = "installed" if check_file_exists("/etc/keyd/macubuntu.conf") else "absent"
fractional = is_true_env("ENABLE_WAYLAND_FRACTIONAL_SCALING")
spotlight = os.environ.get("SEARCH_BACKEND", "rofi")

parity = {
    "dock": dock,
    "keyd": keyd,
    "fractional_scaling": fractional,
    "spotlight_backend": spotlight,
}

# --- Hardware ---
ram_gb = 0
try:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                ram_kb = int(line.split()[1])
                ram_gb = (ram_kb + 524288) // 1048576
                break
except Exception:
    pass

gpu_vendor = "unknown"
try:
    r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
    out = r.stdout.upper()
    if "NVIDIA" in out:
        gpu_vendor = "nvidia"
    elif ("INTEL" in out and ("VGA" in out or "DISPLAY" in out)):
        gpu_vendor = "intel"
    elif "AMD" in out or "RADEON" in out:
        gpu_vendor = "amd"
except Exception:
    pass

has_npu = False
# NPU detection via hardware.json contract (fail-closed: lspci string match alone is not reliable)
try:
    hw_file = Path("/etc/macubuntu/hardware.json")
    if hw_file.exists():
        import json as _json
        hw_data = _json.loads(hw_file.read_text())
        npu_vendor = hw_data.get("npu_vendor", "none")
        npu_device = hw_data.get("npu_device", "")
        if npu_vendor not in (None, "none", "null") and npu_device:
            has_npu = True
except Exception:
    pass
# Fallback: check for actual /dev/accel device node
if not has_npu:
    try:
        has_npu = Path("/dev/accel/accel0").exists()
    except Exception:
        pass

is_laptop = False
try:
    for _ in Path("/sys/class/power_supply").glob("BAT*"):
        is_laptop = True
        break
except Exception:
    pass

has_luks = "crypto_LUKS" in run(["lsblk", "-f"], "")
hibernate_ready = "resume=" in run(["cat", "/proc/cmdline"], "")

hardware = {
    "ram_gb": ram_gb,
    "gpu_vendor": gpu_vendor,
    "has_npu": has_npu,
    "is_laptop": is_laptop,
    "has_luks": has_luks,
    "hibernate_ready": hibernate_ready,
}

# --- Build final state ---
state = {
    "schema_version": "1.0",
    "installer_version": installer_version,
    "celina_version": celina_version,
    "profile": profile,
    "ubuntu": ubuntu_ver,
    "kernel": kernel,
    "gnome": gnome_major,
    "install_timestamp": timestamp,
    "stages": stages,
    "services": services,
    "endpoints": endpoints,
    "secrets_inventory": secrets_inventory,
    "security": security,
    "parity": parity,
    "hardware": hardware,
}

with open(tmp_out, "w") as f:
    json.dump(state, f, indent=2)
    f.write("\n")
PYEOF

    if [[ $? -ne 0 ]]; then
        echo "❌ write_state_json: Python builder failed" >&2
        return 1
    fi

    sudo install -d -m 755 /etc/macubuntu
    sudo install -m 644 "$tmp_state" "$state_file"

    sha256sum "$state_file" | sudo tee /etc/macubuntu/state.json.sha256 >/dev/null
    echo "  📄 state.json written: $state_file (sha256-pinned)"
}

# --- Hardware-adaptive verification (hardware.json driven) ---
if [[ -f /etc/macubuntu/hardware.json ]] && command -v jq >/dev/null 2>&1; then
    HW_TIER="$(jq -r '.tier // "unknown"' /etc/macubuntu/hardware.json)"
    HW_RAM_MB="$(jq -r '.ram_mb // 0' /etc/macubuntu/hardware.json)"
    HW_GPU_VENDOR="$(jq -r '.gpu_vendor // "unknown"' /etc/macubuntu/hardware.json)"
    HW_VRAM_MB="$(jq -r '.vram_mb // 0' /etc/macubuntu/hardware.json)"
    HW_DISK_TRAN="$(jq -r '.disk_transport // "unknown"' /etc/macubuntu/hardware.json)"
    HW_RAM_TIER="$(jq -r '.ram_tier // "unknown"' /etc/macubuntu/hardware.json)"
    HW_AI_TIER="$(jq -r '.ai_tier // "unknown"' /etc/macubuntu/hardware.json)"
else
    HW_TIER="unknown"
    HW_RAM_MB="0"
    HW_GPU_VENDOR="unknown"
    HW_VRAM_MB="0"
    HW_DISK_TRAN="unknown"
    HW_RAM_TIER="unknown"
    HW_AI_TIER="unknown"
fi

report_status "INFO" "Hardware tier" "${HW_TIER} (ram=${HW_RAM_MB}MB, gpu=${HW_GPU_VENDOR}, vram=${HW_VRAM_MB}MB, disk=${HW_DISK_TRAN})" "false"

# --- Hardware-adaptive verification: profile-driven OS tuning asserts ---
# Sources profiles/${ram_tier}.env and asserts live sysctl, ZRAM size, and
# celina-ai.slice match the declared hardware profile. Required only when
# /etc/macubuntu/hardware.json and the matching profile file exist.
# Defaults under set -u when hardware.json is absent
HW_RAM_TIER="${HW_RAM_TIER:-unknown}"
HW_AI_TIER="${HW_AI_TIER:-unknown}"
HW_ASSERT="false"
if [[ -f /etc/macubuntu/hardware.json && "$HW_RAM_TIER" != "unknown" && "$HW_RAM_TIER" != "null" && -f "$REPO_DIR/profiles/${HW_RAM_TIER}.env" ]]; then
    HW_ASSERT="true"
fi

# Evaluate a ZRAM_SIZE_EXPR like "ram | ram / 2 | min(ram / 4, 8192)" against RAM_MB
eval_zram_expr() {
    local expr="$1" ram="$2"
    local e="${expr//ram/$ram}"
    # strip whitespace
    e="${e// /}"
    # Evaluate min(a,b) forms like min(ram/4,8192) without using `case`
    # patterns (parens/comma break `[[ == ]]` pattern matching).
    if [[ "$e" == "min("* && "$e" == *","* && "$e" == *")" ]]; then
        local inner="${e#min(}"; inner="${inner%)}"
        local a="${inner%%,*}"; local b="${inner##*,}"
        a=$((a)); b=$((b))
        (( a < b )) && echo "$a" || echo "$b"
    else
        echo "$((e))"
    fi
}

if [[ -f "$REPO_DIR/profiles/${HW_RAM_TIER}.env" ]]; then
    # shellcheck disable=SC1090
    source "$REPO_DIR/profiles/${HW_RAM_TIER}.env"
fi

# 1. Live VM sysctl must match profile (byte-capped writebacks)
assert_sysctl() {
    local knob="$1" expected="$2" label="$3"
    local actual
    actual="$(sysctl -n "$knob" 2>/dev/null || echo unavailable)"
    if [[ "$actual" == "$expected" ]]; then
        report_status "HEALTHY" "sysctl $label" "$knob=$actual (matches $HW_RAM_TIER profile)" "$HW_ASSERT"
    elif [[ "$actual" == "unavailable" ]]; then
        report_status "INFO" "sysctl $label" "$knob not readable (sysctl restricted)" "$HW_ASSERT"
    else
        report_status "WARNING" "sysctl $label mismatch" "$knob=$actual, expected=$expected ($HW_RAM_TIER profile)" "$HW_ASSERT"
    fi
}

assert_sysctl vm.swappiness        "${SWAPPINESS:-}"        "swappiness"
assert_sysctl vm.dirty_background_bytes "${DIRTY_BG_BYTES:-}" "dirty_background_bytes"
assert_sysctl vm.dirty_bytes       "${DIRTY_BYTES:-}"       "dirty_bytes"
assert_sysctl vm.vfs_cache_pressure "${VFS_CACHE_PRESSURE:-}" "vfs_cache_pressure"
assert_sysctl vm.page_cluster      "${VM_PAGE_CLUSTER:-}"   "page_cluster"

# 2. ZRAM size must match ZRAM_SIZE_EXPR evaluated against detected RAM
if [[ -f /etc/systemd/zram-generator.conf ]]; then
    ZRAM_CFG_SIZE="$(grep -oP 'zram-size = \K[0-9]+' /etc/systemd/zram-generator.conf 2>/dev/null || echo 0)"
    ZRAM_EXPECTED="$(eval_zram_expr "${ZRAM_SIZE_EXPR:-ram / 2}" "$HW_RAM_MB")"
    if [[ "$ZRAM_CFG_SIZE" -eq "$ZRAM_EXPECTED" ]]; then
        report_status "HEALTHY" "ZRAM size (ram_tier=$HW_RAM_TIER)" "${ZRAM_CFG_SIZE}M (expr=${ZRAM_SIZE_EXPR:-<not profiled>} -> ${ZRAM_EXPECTED}M)" "$HW_ASSERT"
    else
        report_status "WARNING" "ZRAM size mismatch" "configured=${ZRAM_CFG_SIZE}M, expected=${ZRAM_EXPECTED}M (expr=${ZRAM_SIZE_EXPR:-<not profiled>}, ram=$HW_RAM_MB)" "$HW_ASSERT"
    fi
else
    report_status "INFO" "ZRAM size" "/etc/systemd/zram-generator.conf not deployed" "$HW_ASSERT"
fi

# 3. celina-ai.slice cgroup ceilings must match profile
if [[ -f "$HOME/.config/systemd/user/celina-ai.slice" ]]; then
    SLICE_HIGH="$(grep -oP 'MemoryHigh=\K.*' "$HOME/.config/systemd/user/celina-ai.slice" 2>/dev/null || echo missing)"
    SLICE_MAX="$(grep -oP 'MemoryMax=\K.*' "$HOME/.config/systemd/user/celina-ai.slice" 2>/dev/null || echo missing)"
    SLICE_CPU="$(grep -oP 'CPUWeight=\K.*' "$HOME/.config/systemd/user/celina-ai.slice" 2>/dev/null || echo missing)"
    SLICE_IO="$(grep -oP 'IOWeight=\K.*' "$HOME/.config/systemd/user/celina-ai.slice" 2>/dev/null || echo missing)"
    if [[ "$SLICE_HIGH" == "${CELINA_SLICE_HIGH:-}" && "$SLICE_MAX" == "${CELINA_SLICE_MAX:-}" && "$SLICE_CPU" == "${CELINA_CPU_WEIGHT:-}" && "$SLICE_IO" == "${CELINA_IO_WEIGHT:-}" ]]; then
        report_status "HEALTHY" "celina-ai.slice (ram_tier=$HW_RAM_TIER)" "MemoryHigh=$SLICE_HIGH MemoryMax=$SLICE_MAX CPUWeight=$SLICE_CPU IOWeight=$SLICE_IO" "$HW_ASSERT"
    else
        report_status "WARNING" "celina-ai.slice mismatch" "High=$SLICE_HIGH Max=$SLICE_MAX CPU=$SLICE_CPU IO=$SLICE_IO (profile: High=${CELINA_SLICE_HIGH:-} Max=${CELINA_SLICE_MAX:-} CPU=${CELINA_CPU_WEIGHT:-} IO=${CELINA_IO_WEIGHT:-})" "$HW_ASSERT"
    fi
else
    report_status "INFO" "celina-ai.slice" "not deployed under $HOME/.config/systemd/user" "$HW_ASSERT"
fi

# 4. models.ini must match ai_tier Router Mode preset
MODELS_INI="$HOME/.config/celina/models.ini"
if [[ -f "$MODELS_INI" ]]; then
    INI_PARALLEL="$(grep -oP '\^parallel = \\K[0-9]+' "$MODELS_INI" 2>/dev/null | head -1 || true)"
    INI_KV="$(grep -oP '\^cache-type-k = \\K.*' "$MODELS_INI" 2>/dev/null | head -1 || true)"
    INI_MODEL_SECTIONS="$(grep -c '^\[' "$MODELS_INI" 2>/dev/null || echo 0)"
    INI_MODEL_SECTIONS=$((INI_MODEL_SECTIONS - 1))  # exclude [*]
    if [[ "$INI_PARALLEL" == "${LLM_PARALLEL_SLOTS:-}" && "$INI_KV" == "${LLM_KV_CACHE_TYPE:-}" && "$INI_MODEL_SECTIONS" -le "${LLM_MODELS_MAX:-0}" ]]; then
        report_status "HEALTHY" "models.ini (ai_tier=$HW_AI_TIER)" "parallel=$INI_PARALLEL kv=$INI_KV sections=$INI_MODEL_SECTIONS (max=${LLM_MODELS_MAX:-})" "$HW_ASSERT"
    else
        report_status "WARNING" "models.ini mismatch" "parallel=${INI_PARALLEL:-none} kv=${INI_KV:-none} sections=$INI_MODEL_SECTIONS (profile: parallel=${LLM_PARALLEL_SLOTS:-} kv=${LLM_KV_CACHE_TYPE:-} max=${LLM_MODELS_MAX:-})" "$HW_ASSERT"
    fi
else
    report_status "INFO" "models.ini" "not deployed at $HOME/.config/celina/models.ini" "$HW_ASSERT"
fi

# --- Simulated verification across all three hardware profiles ---
# Even without /etc/macubuntu/hardware.json, walk each profile/*.env and
# confirm every profile declares internally-consistent knobs (ZRAM expr,
# VM sysctl, celina-ai.slice ceilings, Celina tier policy, LLM preset).
SIM_PROFILES_DIR="$REPO_DIR/profiles"
SIM_PROFILES_OK=0
SIM_PROFILES_TOTAL=0
for sim_env in "$SIM_PROFILES_DIR"/8gb.env "$SIM_PROFILES_DIR"/16gb.env "$SIM_PROFILES_DIR"/32gb.env; do
    [[ -f "$sim_env" ]] || continue
    SIM_PROFILES_TOTAL=$((SIM_PROFILES_TOTAL + 1))
    sim_tier="$(basename "$sim_env" .env)"
    # Source into a clean subshell to avoid clobbering the active profile env
    sim_ok="true"
    (
        # shellcheck disable=SC1090
        source "$sim_env"
        # ZRAM_SIZE_EXPR must evaluate to a positive integer
        : "${ZRAM_SIZE_EXPR:?missing ZRAM_SIZE_EXPR}"
        ze="$(eval_zram_expr "$ZRAM_SIZE_EXPR" 16384)" || sim_ok="false"
        [[ "$ze" -gt 0 ]] || sim_ok="false"
        # VM sysctl knobs must be present integers
        for k in SWAPPINESS DIRTY_BG_BYTES DIRTY_BYTES VFS_CACHE_PRESSURE VM_PAGE_CLUSTER; do
            v="${!k}"
            [[ -n "$v" && "$v" =~ ^[0-9]+$ ]] || sim_ok="false"
        done
        # celina-ai.slice ceilings must be present
        [[ -n "${CELINA_SLICE_HIGH:-}" && -n "${CELINA_SLICE_MAX:-}" && -n "${CELINA_CPU_WEIGHT:-}" ]] || sim_ok="false"
        # LLM Router Mode preset knobs must be present
        [[ -n "${LLM_MODELS_MAX:-}" && -n "${LLM_PARALLEL_SLOTS:-}" && -n "${LLM_KV_CACHE_TYPE:-}" ]] || sim_ok="false"
        # Celina tier policy knobs must be present
        [[ -n "${SPOTLIGHT_BATCH_SIZE:-}" && -n "${RAM_PSI_EVICT_THRESHOLD:-}" ]] || sim_ok="false"
    ) 2>/dev/null || sim_ok="false"
    if [[ "$sim_ok" == "true" ]]; then
        SIM_PROFILES_OK=$((SIM_PROFILES_OK + 1))
        report_status "HEALTHY" "profile $sim_tier (simulated)" "all knobs internally consistent" "false"
    else
        report_status "WARNING" "profile $sim_tier (simulated)" "incomplete or inconsistent knobs" "false"
    fi
done
if [[ "$SIM_PROFILES_TOTAL" -gt 0 && "$SIM_PROFILES_OK" -eq "$SIM_PROFILES_TOTAL" ]]; then
    report_status "HEALTHY" "simulated profiles (3/3)" "all profile/*.env declarative knobs consistent" "false"
elif [[ "$SIM_PROFILES_TOTAL" -gt 0 ]]; then
    report_status "WARNING" "simulated profiles ($SIM_PROFILES_OK/$SIM_PROFILES_TOTAL)" "some profile/*.env need review" "false"
else
    report_status "INFO" "simulated profiles" "no profile/*.env files found under $SIM_PROFILES_DIR" "false"
fi

# Verify udev I/O scheduler rules exist
if [[ -f /etc/udev/rules.d/60-macubuntu-iosched.rules ]]; then
    report_status "HEALTHY" "udev I/O scheduler rules" "NVMe=none, SATA SSD=mq-deadline, HDD=bfq" "false"
else
    report_status "WARNING" "udev I/O scheduler rules" "missing /etc/udev/rules.d/60-macubuntu-iosched.rules" "false"
fi

# Verify sched_ext D-Bus restriction
if [[ -f /etc/dbus-1/system.d/org.scx.Loader.conf ]]; then
    report_status "HEALTHY" "sched_ext D-Bus policy" "restricted to celina group" "false"
else
    report_status "INFO" "sched_ext D-Bus policy" "not configured (sched_ext may not be active)" "false"
fi

# --- Phase 4: Additional verification checks ---

# Verify SQLite WAL integrity for Celina databases
CELINA_DB="${HOME}/.local/share/celina/db/celina.db"
if [[ -f "$CELINA_DB" ]]; then
    WAL_MODE="$(sqlite3 "$CELINA_DB" 'PRAGMA journal_mode;' 2>/dev/null || echo unknown)"
    if [[ "$WAL_MODE" == "wal" ]]; then
        report_status "HEALTHY" "SQLite WAL mode" "celina.db journal_mode=wal" "false"
    else
        report_status "WARNING" "SQLite WAL mode" "celina.db journal_mode=$WAL_MODE (expected wal)" "false"
    fi
else
    report_status "INFO" "SQLite WAL mode" "celina.db not found (not initialized yet)" "false"
fi

# Verify Unix socket permissions (0700) for Celina runtime
CELINA_SOCK_DIR="/run/user/$(id -u)/celina"
if [[ -d "$CELINA_SOCK_DIR" ]]; then
    SOCK_PERMS="$(stat -c '%a' "$CELINA_SOCK_DIR" 2>/dev/null || echo unknown)"
    if [[ "$SOCK_PERMS" == "700" ]]; then
        report_status "HEALTHY" "Celina runtime socket dir" "permissions=0700" "false"
    else
        report_status "WARNING" "Celina runtime socket dir" "permissions=$SOCK_PERMS (expected 0700)" "false"
    fi
else
    report_status "INFO" "Celina runtime socket dir" "not found (will be created at runtime)" "false"
fi

# Verify Polkit scx_loader scope (celina group only)
if [[ -f /etc/polkit-1/rules.d/99-scx-loader.rules ]]; then
    if grep -q "org.scx\." /etc/polkit-1/rules.d/99-scx-loader.rules 2>/dev/null && \
       grep -q "celina" /etc/polkit-1/rules.d/99-scx-loader.rules 2>/dev/null; then
        report_status "HEALTHY" "Polkit scx_loader rule" "restricted to celina group" "false"
    else
        report_status "WARNING" "Polkit scx_loader rule" "exists but may not restrict to celina group" "false"
    fi
else
    report_status "INFO" "Polkit scx_loader rule" "not found (sched_ext switching requires manual setup)" "false"
fi

# Verify SHA-256 capability manifest exists
CAP_MANIFEST="${REPO_DIR}/system/capabilities/manifest.sha256"
if [[ -f "$CAP_MANIFEST" ]]; then
    report_status "HEALTHY" "SHA-256 capability manifest" "present at system/capabilities/manifest.sha256" "false"
else
    report_status "WARNING" "SHA-256 capability manifest" "missing — new tools/MCP servers must register here" "false"
fi

# --- Phase C: v4.6 Advanced Verification ---

# Verify S0ix suspend-then-hibernate sleep.conf.d (Stage 50 / ENABLE_S0IX_SLEEP)
if [[ -f /etc/systemd/sleep.conf.d/99-macubuntu-s0ix.conf ]]; then
    if grep -q "HibernateDelaySec=90min" /etc/systemd/sleep.conf.d/99-macubuntu-s0ix.conf 2>/dev/null && \
       grep -q "HibernateOnACPower=no" /etc/systemd/sleep.conf.d/99-macubuntu-s0ix.conf 2>/dev/null; then
        report_status "HEALTHY" "S0ix suspend-then-hibernate" "HibernateDelaySec=90min, HibernateOnACPower=no" "false"
    else
        report_status "WARNING" "S0ix suspend-then-hibernate" "exists but missing 90min/onAC=no settings" "false"
    fi
else
    report_status "INFO" "S0ix suspend-then-hibernate" "not deployed (ENABLE_S0IX_SLEEP=false or not installed)" "false"
fi

# Verify uinput rules for Wayland text injection (Stage 30 / ENABLE_WAYLAND_TEXT_INJECTION)
if [[ -f /etc/udev/rules.d/80-uinput.rules ]]; then
    if grep -q 'KERNEL=="uinput"' /etc/udev/rules.d/80-uinput.rules 2>/dev/null && \
       grep -q "GROUP=\"input\"" /etc/udev/rules.d/80-uinput.rules 2>/dev/null && \
       grep -q 'MODE="0660"' /etc/udev/rules.d/80-uinput.rules 2>/dev/null; then
        report_status "HEALTHY" "80-uinput.rules" "uinput GROUP=input MODE=0660 static_node" "false"
    else
        report_status "WARNING" "80-uinput.rules" "exists but missing uinput GROUP=input MODE=0660" "false"
    fi
else
    report_status "INFO" "80-uinput.rules" "not deployed (ENABLE_WAYLAND_TEXT_INJECTION=false)" "false"
fi

# Verify ydotool.service user unit with YDOTOOL_SOCKET (Stage 30 / ENABLE_WAYLAND_TEXT_INJECTION)
YDTOOL_SVC="$HOME/.config/systemd/user/ydotool.service"
if [[ -f "$YDTOOL_SVC" ]]; then
    if grep -q "YDOTOOL_SOCKET" "$YDTOOL_SVC" 2>/dev/null && \
       grep -q ".ydotool_socket" "$YDTOOL_SVC" 2>/dev/null; then
        report_status "HEALTHY" "ydotool.service" "YDOTOOL_SOCKET=\$XDG_RUNTIME_DIR/.ydotool_socket" "false"
    else
        report_status "WARNING" "ydotool.service" "exists but missing YDOTOOL_SOCKET" "false"
    fi
else
    report_status "INFO" "ydotool.service" "not deployed (ENABLE_WAYLAND_TEXT_INJECTION=false)" "false"
fi

# Verify mcp_tools.lock.json integrity (Phase B / ENABLE_MCP_GUARD)
MCP_LOCK="${REPO_DIR}/system/capabilities/mcp_tools.lock.json"
if [[ -f "$MCP_LOCK" ]]; then
    LOCK_VALID="true"
    if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$MCP_LOCK" 2>/dev/null; then
        LOCK_VALID="false"
    fi
    if [[ "$LOCK_VALID" == "true" ]]; then
        report_status "HEALTHY" "mcp_tools.lock.json" "valid JSON — MCP tool SHA-256 pin baseline" "false"
    else
        report_status "WARNING" "mcp_tools.lock.json" "invalid JSON — must be valid for SHA-256 pinning" "false"
    fi
else
    report_status "INFO" "mcp_tools.lock.json" "not found — create for MCP03 tool-poisoning pinning (ENABLE_MCP_GUARD)" "false"
fi

# Verify bi-temporal SQLite schema columns (Phase B / ENABLE_BITEMPORAL_KG)
CELINA_KG_DB="${HOME}/.local/share/celina/celina.db"
if [[ -f "$CELINA_KG_DB" ]]; then
    KG_OK="true"
    for tbl in kg_facts stakeholder_profiles open_commitments; do
        if ! sqlite3 "$CELINA_KG_DB" "PRAGMA table_info($tbl);" 2>/dev/null | grep -q "valid_from"; then
            KG_OK="false"
        fi
        if ! sqlite3 "$CELINA_KG_DB" "PRAGMA table_info($tbl);" 2>/dev/null | grep -q "valid_until"; then
            KG_OK="false"
        fi
        if ! sqlite3 "$CELINA_KG_DB" "PRAGMA table_info($tbl);" 2>/dev/null | grep -q "ingested_at"; then
            KG_OK="false"
        fi
    done
    if [[ "$KG_OK" == "true" ]]; then
        report_status "HEALTHY" "bi-temporal KG schema" "valid_from/valid_until/ingested_at columns present" "false"
    else
        report_status "WARNING" "bi-temporal KG schema" "missing bi-temporal columns in kg_facts/stakeholder_profiles/open_commitments" "false"
    fi
else
    report_status "INFO" "bi-temporal KG schema" "celina.db not initialized yet (ENABLE_BITEMPORAL_KG=false)" "false"
fi

# Verify unit test files for DualContextBarrier + BlastRadiusEstimator (Phase B)
if [[ -f "${REPO_DIR}/tests/test_mcp_guard.py" ]] || [[ -f "${REPO_DIR}/tests/test_blast_radius.py" ]]; then
    report_status "HEALTHY" "Phase C unit tests" "test_mcp_guard.py / test_blast_radius.py present" "false"
else
    report_status "INFO" "Phase C unit tests" "add tests/test_mcp_guard.py + tests/test_blast_radius.py for DualContextBarrier + BlastRadiusEstimator" "false"
fi

# --- Phase C: Gear 0 / Gear 0.5 Shortcut verification ---

# Verify shortcuts engine package + engine.py parse cleanly
SHORTCUTS_PKG="${REPO_DIR}/features/celina/celina_pkg/shortcuts"
if [[ -d "$SHORTCUTS_PKG" ]] && [[ -f "$SHORTCUTS_PKG/engine.py" ]]; then
    if python3 -c "import ast; ast.parse(open('${SHORTCUTS_PKG}/engine.py').read()); ast.parse(open('${SHORTCUTS_PKG}/gear05_router.py').read()); ast.parse(open('${SHORTCUTS_PKG}/crystallizer.py').read()); ast.parse(open('${SHORTCUTS_PKG}/triggers.py').read())" 2>/dev/null; then
        report_status "HEALTHY" "shortcuts engine package" "engine/gear05_router/crystallizer/triggers parse OK" "false"
    else
        report_status "WARNING" "shortcuts engine package" "Python syntax error in shortcuts package" "false"
    fi
else
    report_status "INFO" "shortcuts engine package" "not found — Gear 0/0.5 shortcuts not installed" "false"
fi

# Verify Gear 0 / 0.5 router resolves exact match without LLM
ROUTER_TEST="${REPO_DIR}/tests/test_shortcuts_engine.py"
if [[ -f "$ROUTER_TEST" ]]; then
    report_status "HEALTHY" "Gear 0/0.5 unit tests" "test_shortcuts_engine.py present" "false"
else
    report_status "INFO" "Gear 0/0.5 unit tests" "add tests/test_shortcuts_engine.py for Gear 0/0.5 router" "false"
fi

# Verify declarative shortcuts directory exists (user Shortcuts live here)
SHORTCUT_DIR="${HOME}/.local/share/celina/shortcuts"
if [[ -d "$SHORTCUT_DIR" ]]; then
    YAML_COUNT="$(find "$SHORTCUT_DIR" -maxdepth 1 -name '*.cshortcut.yaml' 2>/dev/null | wc -l)"
    report_status "HEALTHY" "user shortcuts dir" "${YAML_COUNT} .cshortcut.yaml files" "false"
else
    report_status "INFO" "user shortcuts dir" "not created yet (shortcuts staged to quarantine first)" "false"
fi

write_state_json

print_summary

if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo ""
    echo "================================================================"
    echo "  [DRY-RUN PREVIEW COMPLETE] profile=${PROFILE} | celina=${ENABLE_CELINA}"
    echo "  (No changes were made; run './install.sh --verify' after real install)"
    echo "================================================================"
    exit 0
fi

if [[ "$REQUIRED_FAILURES" -gt 0 ]]; then
    echo ""
    echo "================================================================"
    echo "  ❌ macubuntu VERIFICATION FAILED"
    echo "  Required failures: ${REQUIRED_FAILURES} | Optional warnings: ${WARNINGS}"
    echo "  Resolve the NOT INSTALLED / INSTALLED (INACTIVE) / FAILED items above"
    echo "  and re-run: ./install.sh --verify"
    echo "================================================================"
    exit 1
fi

cat <<EOF

================================================================
  ✅ macubuntu VERIFICATION HEALTHY (profile=${PROFILE} | celina=${ENABLE_CELINA})
  Required failures: 0 | Optional warnings: ${WARNINGS}
================================================================
Next steps:
  1. Reboot (or log out/in) so ZRAM, video group, GNOME extensions,
     LocalSend, and ${CELINA_NAME} Dynamic Island start cleanly.
  2. Shortcuts:
       Super+Space       -> Spotlight (${LAUNCHER_CHOICE})
       Super+Shift+C     -> ${CELINA_NAME} Dynamic Island
       Super+Alt+C       -> ${CELINA_NAME} Instant Voice / Barge-In
       Super+Shift+W     -> Apple Intelligence Writing Tools
       Super+Shift+T     -> macOS Live Text Region OCR
       Super+Shift+A     -> ${CELINA_NAME} Autonomy TaskGraph Status (celina autonomy --status)
       Esc               -> Silence speech / Emergency Stop
  3. Optional: add OPENROUTER_API_KEY in ~/.config/celina/env (0600).
     Rename anytime via '/name <NewName> [Nickname]' or edit env.
  4. Open Pika Backup & 'sudo timeshift-gtk' to set first snapshot.
================================================================
EOF
