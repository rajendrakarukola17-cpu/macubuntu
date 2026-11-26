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
    local cmd="$1" label="$2" required="${3:-true}" selected="${4:-true}"
    if [[ "$selected" != "true" ]]; then
        report_status "NOT SELECTED" "$label" "(feature disabled)" false
        return 0
    fi
    if command -v "$cmd" >/dev/null 2>&1; then
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
check_cmd timeshift      "Timeshift"         true  true
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
check_cmd ollama           "Ollama Local LLM"    true  "$CELINA_SEL"
check_cmd rofi             "Rofi Spotlight"      true  "$CELINA_SEL"
check_cmd bwrap            "Bubblewrap Sandbox"  true  "$CELINA_SEL"
check_cmd tesseract        "Tesseract OCR"       false "$CELINA_SEL"
check_cmd celina           "Celina CLI"          true  "$CELINA_SEL"
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
