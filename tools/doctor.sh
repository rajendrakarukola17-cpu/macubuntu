#!/usr/bin/env bash
# ============================================================================
# tools/doctor.sh — Read-Only Fast-Path Diagnostics (`./install.sh --doctor`)
# Inspects the top 16 real-world macubuntu failure modes with ZERO writes,
# ZERO sudo prompts, and prints exact copy-pasteable fix commands.
# ============================================================================
set -Eeuo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"

OK_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

doc_ok()   { OK_COUNT=$((OK_COUNT + 1));     printf "  ✅ %-30s %s\n" "$1" "$2"; }
doc_warn() { WARN_COUNT=$((WARN_COUNT + 1)); printf "  ⚠️  %-30s %s\n     Fix: %s\n" "$1" "$2" "$3"; }
doc_fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); printf "  ❌ %-30s %s\n     Fix: %s\n" "$1" "$2" "$3"; }

echo "================================================================"
echo "  🩺 macubuntu Doctor — Read-Only Fast-Path Health Check"
echo "================================================================"

# 1. Wayland vs X11 session
SESS="$(detect_session_type)"
if [[ "$SESS" == "wayland" ]]; then
    doc_ok "Display Session" "Wayland (optimal)"
elif [[ "$SESS" == "x11" ]]; then
    doc_warn "Display Session" "X11 detected (Wayland required for Portal/wl-clipboard)" "Select 'Ubuntu on Wayland' at login (NVIDIA: add nvidia-drm.modeset=1)"
else
    doc_warn "Display Session" "Headless / unknown ($SESS)" "Run inside a GNOME Wayland desktop session for GUI checks"
fi

# 2. Supply-chain & GNOME extension lockfiles
if [[ -f "$LOCKFILE" && -f "$EXT_LOCKFILE" ]]; then
    doc_ok "Supply-Chain Lockfiles" "download-locks.sha256 + extensions.lock present"
else
    doc_fail "Supply-Chain Lockfiles" "Missing lockfile(s)" "Re-extract macubuntu release archive or verify system/ directory"
fi

# 3. Least-privilege input group
if id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx "input"; then
    doc_warn "Input Group Isolation" "User '$USER' is in broad 'input' group" "sudo gpasswd -d $USER input && reboot (unless --experimental-uinput opted in)"
else
    doc_ok "Input Group Isolation" "User not in broad 'input' group"
fi

# 4. ZRAM active
if grep -q '^/dev/zram' /proc/swaps 2>/dev/null; then
    doc_ok "ZRAM Compressed Swap" "Active ($(awk '/^\/dev\/zram/{printf "%d MiB", $3/1024}' /proc/swaps))"
else
    doc_warn "ZRAM Compressed Swap" "Not active yet" "./install.sh --only 35 && sudo systemctl restart systemd-zram-setup@zram0.service"
fi

# 5. PipeWire audio daemon
if command -v wpctl >/dev/null 2>&1 && wpctl status >/dev/null 2>&1; then
    doc_ok "PipeWire Audio" "Running (wpctl responsive)"
else
    doc_warn "PipeWire Audio" "PipeWire/wpctl not responsive" "systemctl --user restart pipewire wireplumber"
fi

# 6. AirPods AAC / PipeWire clock drop-ins
if [[ -f "$HOME/.config/wireplumber/wireplumber.conf.d/51-macubuntu-bluetooth.conf" && -f "$HOME/.config/pipewire/pipewire.conf.d/99-macubuntu-audio.conf" ]]; then
    doc_ok "Pro Audio & AirPods AAC" "WirePlumber + PipeWire drop-ins installed"
else
    doc_warn "Pro Audio & AirPods AAC" "User audio drop-ins missing" "./install.sh --only 40"
fi

# 7. Zero-conflict mDNS (Avahi vs systemd-resolved)
if [[ -f /etc/systemd/resolved.conf.d/99-macubuntu-mdns.conf ]]; then
    doc_ok "Zero-Conflict mDNS" "resolved MulticastDNS=no drop-in active"
else
    doc_warn "Zero-Conflict mDNS" "UDP 5353 conflict risk with Avahi" "./install.sh --only 40"
fi

# 8. Persistent Continuity Camera (/dev/video10) & Secure Boot MOK check
if detect_secure_boot_enabled; then
    MOK_DER="$(detect_mok_der_path 2>/dev/null || true)"
    if [[ -n "$MOK_DER" ]] && detect_mok_enrolled "$MOK_DER"; then
        doc_ok "Secure Boot & MOK" "Secure Boot enabled; DKMS MOK enrolled (v4l2loopback ready)"
    else
        doc_warn "Secure Boot & MOK" "Secure Boot enabled; DKMS MOK not enrolled yet (blocks v4l2loopback)" "./tools/enroll-mok.sh"
    fi
else
    doc_ok "Secure Boot & MOK" "Secure Boot disabled/legacy (all 13 stages load directly)"
fi

if [[ -f /etc/modprobe.d/99-macubuntu-v4l2loopback.conf ]]; then
    doc_ok "Continuity Camera Module" "Persistent /dev/video10 modprobe rule installed"
else
    doc_warn "Continuity Camera Module" "Persistent v4l2loopback config missing" "./install.sh --only 40"
fi

# 9. Safe Sleep & LUKS swap check
if detect_has_luks_swap && ! detect_luks_swap_resume_ready; then
    doc_warn "LUKS Swap Hibernate" "LUKS swap lacks verified crypttab/initramfs resume hook" "Safe Sleep stays on instant-suspend; configure /etc/initramfs-tools/conf.d/resume + update-initramfs -u"
elif [[ -f /etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf ]]; then
    doc_ok "Safe Sleep Policy" "/etc/systemd/sleep.conf.d/99-macubuntu-safesleep.conf installed"
else
    doc_warn "Safe Sleep Policy" "Sleep drop-in missing" "./install.sh --only 50"
fi

# 10. VA-API hardware decode
if detect_vaapi_decode_supported; then
    doc_ok "VA-API H.264/VP9 Decode" "Hardware VLD profiles verified via vainfo"
else
    doc_warn "VA-API H.264/VP9 Decode" "No hardware H.264/VP9 VLD detected" "python3 system/firefox/apply-safari-theme.py --disable-vaapi (prevents Firefox stalls)"
fi

# 11. Safari Firefox theme
if [[ -f "$HOME/.local/share/icons/hicolor/scalable/apps/macubuntu-safari.svg" ]]; then
    doc_ok "Safari Firefox Theme" "Compass icon & chrome deployed"
else
    doc_warn "Safari Firefox Theme" "Not installed yet" "./install.sh --only 20"
fi

# 12. GNOME Extensions enabled
if command -v gnome-extensions >/dev/null 2>&1; then
    EXT_CNT="$(gnome-extensions list --enabled 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${EXT_CNT:-0}" -gt 0 ]]; then
        doc_ok "GNOME Extensions" "$EXT_CNT extension(s) enabled"
    else
        doc_warn "GNOME Extensions" "0 extensions currently enabled" "./install.sh --only 25 (then log out & back in)"
    fi
else
    doc_warn "GNOME Extensions" "gnome-extensions CLI unavailable" "Run inside GNOME desktop session"
fi

# 13. Celina Private Permissions (0700 / 0600)
if [[ -d "$HOME/.config/celina" ]]; then
    CMODE="$(stat -c '%a' "$HOME/.config/celina" 2>/dev/null || echo '???')"
    if [[ "$CMODE" == "700" ]]; then
        doc_ok "Celina Private Dir" "~/.config/celina mode=700"
    else
        doc_fail "Celina Private Dir" "Insecure mode=$CMODE (expected 700)" "chmod 700 ~/.config/celina ~/.local/share/celina && chmod 600 ~/.config/celina/*"
    fi
else
    doc_warn "Celina Private Dir" "~/.config/celina not yet initialized" "./install.sh --only 70"
fi

# 14. Celina Event Daemon
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet celina-eventd.service 2>/dev/null; then
    doc_ok "Celina Event Daemon" "celina-eventd.service active"
else
    doc_warn "Celina Event Daemon" "celina-eventd.service inactive/not installed" "systemctl --user enable --now celina-eventd.service (or ./install.sh --only 70)"
fi

# 15. Ollama API responsiveness & models
if command -v curl >/dev/null 2>&1 && curl -fs --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    doc_ok "Ollama Local API" "Responding on 127.0.0.1:11434"
else
    doc_warn "Ollama Local API" "Not responding on 127.0.0.1:11434" "sudo systemctl restart ollama && ./install.sh --verify-models"
fi

# 16. Ollama Model Digest Lockfile
if [[ -f "$HOME/.config/celina/model-digests.sha256" ]]; then
    doc_ok "Ollama Model Digests" "Lockfile present (~/.config/celina/model-digests.sha256)"
else
    doc_warn "Ollama Model Digests" "No digest lock recorded yet" "./install.sh --only 70 (records SHA-256 digests after pull)"
fi

# 17. Celina D-Bus Control Plane (celina.service)
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet celina.service 2>/dev/null; then
    doc_ok "Celina Control Plane" "celina.service active (org.macubuntu.Celina)"
else
    doc_warn "Celina Control Plane" "celina.service inactive/not enabled" "systemctl --user enable --now celina.service"
fi

# 18. Native GNOME Shell Island Extension
if [[ -f "$HOME/.local/share/gnome-shell/extensions/celina-island@macubuntu.org/metadata.json" ]] || \
   [[ -f "/usr/share/gnome-shell/extensions/celina-island@macubuntu.org/metadata.json" ]]; then
    doc_ok "GNOME Island Ext" "celina-island@macubuntu.org installed"
else
    doc_warn "GNOME Island Ext" "celina-island@macubuntu.org not installed yet" "./install.sh --only 70 (or sudo apt install ./releases/macubuntu_4.4.0_all.deb)"
fi

# 19. CO-RE eBPF Kernel Readiness
if [[ -r /sys/kernel/btf/vmlinux ]]; then
    if grep -qw "bpf" /sys/kernel/security/lsm 2>/dev/null; then
        doc_ok "eBPF Kernel Sensor" "BTF + BPF LSM ready"
    else
        doc_ok "eBPF Kernel Sensor" "BTF ready (tracepoints-only; boot lsm=...,bpf for Focus LSM)"
    fi
else
    doc_warn "eBPF Kernel Sensor" "Kernel BTF absent; using safe D-Bus/inotify fallback" "./tools/build-ebpf.sh --check"
fi

# 20. NPU / GPU Compute Offload
if ls /dev/accel/accel* >/dev/null 2>&1; then
    doc_ok "Hardware Compute" "NPU (/dev/accel) detected -> decoupled celina-npu-inference.service (:11435)"
elif ls /dev/dri/renderD* >/dev/null 2>&1; then
    doc_ok "Hardware Compute" "GPU (/dev/dri/renderD*) detected -> Ollama GPU/Vulkan offload"
else
    doc_warn "Hardware Compute" "CPU-only host (no /dev/accel or /dev/dri render node)" "./tools/detect-compute.sh"
fi

# 21. sched_ext + scx_lavd / scx_rusty Dual-Scheduler
if [[ -r /sys/kernel/sched_ext/state ]] && [[ "$(cat /sys/kernel/sched_ext/state 2>/dev/null)" == "enabled" ]]; then
    scx_ops="$(cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo active)"
    doc_ok "sched_ext BPF Sched" "enabled (${scx_ops})"
elif [[ -r /sys/kernel/sched_ext/state ]]; then
    doc_warn "sched_ext BPF Sched" "kernel supports sched_ext but no scheduler active" "./tools/os-hardening-setup.sh"
else
    doc_warn "sched_ext BPF Sched" "sched_ext unavailable on current kernel (requires 6.12+)" "Upgrade to Ubuntu 26.04 kernel 6.12+"
fi

# 22. cgroup v2 local-inference.slice & PSI (/proc/pressure/memory)
if [[ -r /proc/pressure/memory ]]; then
    psi_full="$(awk '/^full / {for(i=2;i<=NF;i++) if($i ~ /^avg10=/) {sub("avg10=","",$i); print $i}}' /proc/pressure/memory 2>/dev/null || echo 0.00)"
    doc_ok "Memory PSI (kv-psi)" "/proc/pressure/memory readable (full avg10=${psi_full})"
else
    doc_warn "Memory PSI (kv-psi)" "/proc/pressure/memory unavailable" "Boot kernel with psi=1"
fi

# 23. Landlock LSM / SHarness
if grep -qw "landlock" /sys/kernel/security/lsm 2>/dev/null; then
    if command -v sharness-ai >/dev/null 2>&1; then
        doc_ok "Landlock / SHarness" "Landlock LSM active + sharness-ai installed"
    else
        doc_ok "Landlock / SHarness" "Landlock LSM active (using built-in Python ctypes enforcement)"
    fi
else
    doc_warn "Landlock / SHarness" "Landlock LSM not listed in /sys/kernel/security/lsm" "Boot with lsm=...,landlock"
fi

# 24. PREEMPT_RT (informational / optional)
if [[ -r /sys/kernel/realtime ]] && [[ "$(cat /sys/kernel/realtime 2>/dev/null)" == "1" ]]; then
    doc_ok "PREEMPT_RT Kernel" "Realtime kernel active (/sys/kernel/realtime=1)"
else
    doc_ok "PREEMPT_RT Kernel" "Standard generic kernel (opt-in ubuntu-realtime only if VAD jitter > 10us)"
fi

# 25. TPM 2.0 & io_uring Readiness
if [[ -e /dev/tpmrm0 ]] || [[ -e /dev/tpm0 ]]; then
    doc_ok "TPM 2.0 Measured" "TPM device present (ready for systemd-creds --with-key=tpm2)"
else
    doc_warn "TPM 2.0 Measured" "No /dev/tpmrm0 device detected (required before real Tier B secrets)" "Enable fTPM/dTPM in UEFI"
fi
if [[ -r /sys/kernel/io_uring_disabled ]] && [[ "$(cat /sys/kernel/io_uring_disabled 2>/dev/null)" == "0" ]]; then
    doc_ok "io_uring Kernel" "enabled (io_uring_disabled=0; SQLite WAL active, Turso v5 ready)"
else
    doc_warn "io_uring Kernel" "io_uring restricted or absent" "Verify /sys/kernel/io_uring_disabled == 0"
fi

# 26. GNOME Sushi >= 50.0 (Ubuntu 26.04 Resolute Nautilus 50 compatibility gate)
if command -v dpkg-query >/dev/null 2>&1 && dpkg-query -W -f='${Version}' gnome-sushi >/dev/null 2>&1; then
    sushi_ver="$(dpkg-query -W -f='${Version}' gnome-sushi 2>/dev/null || echo 0)"
    sushi_maj="${sushi_ver%%.*}"
    sushi_maj="${sushi_maj#*:}"
    if [[ "$sushi_maj" =~ ^[0-9]+$ ]] && (( sushi_maj >= 50 )); then
        doc_ok "GNOME Sushi QuickLook" "gnome-sushi ${sushi_ver} (>= 50.0 compatible with Nautilus 50)"
    else
        doc_warn "GNOME Sushi QuickLook" "gnome-sushi ${sushi_ver} < 50.0 (broken on Ubuntu 26.04 Nautilus 50)" "sudo apt upgrade gnome-sushi (or use FrostFinder Quick Look)"
    fi
fi

echo "================================================================"
echo "  Summary: ${OK_COUNT} healthy | ${WARN_COUNT} warnings | ${FAIL_COUNT} failures"
echo "  Deep audit: ./install.sh --verify | Model digests: ./install.sh --verify-models"
echo "================================================================"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    exit 1
fi
exit 0
