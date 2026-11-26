#!/usr/bin/env bash
# ============================================================================
# core/detect.sh — Read-only OS, Hardware, Display, LUKS & Network Introspection
# ============================================================================

detect_ubuntu_version() {
    lsb_release -rs 2>/dev/null \
        || awk -F= '/^VERSION_ID=/{gsub(/"/,"",$2); print $2}' /etc/os-release 2>/dev/null \
        || echo "unknown"
}

detect_ram_gb() {
    local ram_kb
    ram_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    echo $(( (ram_kb + 524288) / 1048576 ))
}

detect_disk_free_gb() {
    df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9' || echo 0
}

detect_session_type() {
    echo "${XDG_SESSION_TYPE:-unknown}"
}

detect_lan_cidr() {
    ip -o -4 route show scope link 2>/dev/null \
        | awk '$1 ~ /^(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)/ && $0 !~ /docker|virbr|br-|veth/ {print $1; exit}'
}

detect_has_luks() {
    lsblk -f 2>/dev/null | grep -q "crypto_LUKS"
}

detect_has_amd_gpu() {
    if command -v lspci >/dev/null 2>&1; then
        lspci 2>/dev/null | grep -qiE '(VGA|3D|Display).*(\bAMD\b|\bATI\b|Radeon)'
        return $?
    fi
    return 1
}

detect_cpu_vendor() {
    local vendor
    vendor="$(awk -F: '/^vendor_id/ {gsub(/[ \t]+/, "", $2); print $2; exit}' /proc/cpuinfo 2>/dev/null || true)"
    case "$vendor" in
        AuthenticAMD) echo "amd" ;;
        GenuineIntel) echo "intel" ;;
        *)            echo "other" ;;
    esac
}

detect_root_fstype() {
    findmnt -no FSTYPE / 2>/dev/null || df -T / 2>/dev/null | awk 'NR==2 {print $2}' || echo "unknown"
}

detect_is_laptop() {
    local bat
    for bat in /sys/class/power_supply/BAT*; do
        [[ -e "$bat" ]] && return 0
    done
    local ct
    ct="$(cat /sys/class/dmi/id/chassis_type 2>/dev/null || echo "")"
    [[ "$ct" == "8" || "$ct" == "9" || "$ct" == "10" || "$ct" == "14" || "$ct" == "31" || "$ct" == "32" ]]
}

check_hibernate_swap_capacity_kb() {
    # Hibernate + ZRAM safety invariant:
    # During suspend-then-hibernate, the kernel must decompress ZRAM pages into
    # real on-disk swap alongside active RAM. Require real_swap_kb >= ram_kb + zram_kb.
    local ram_kb="${1:-0}" zram_kb="${2:-0}" real_swap_kb="${3:-0}"
    (( real_swap_kb > 0 && real_swap_kb >= (ram_kb + zram_kb) ))
}

detect_has_luks_swap() {
    # True when an active swap device lives on /dev/mapper/ or crypttab defines a swap target
    if awk 'NR>1 && $1 ~ /^\/dev\/mapper\// {found=1} END {exit !found}' /proc/swaps 2>/dev/null; then
        return 0
    fi
    grep -qE '^[^#].*\bswap\b' /etc/crypttab 2>/dev/null
}

detect_luks_swap_resume_ready() {
    # On LUKS-encrypted swap, hibernate ONLY succeeds when:
    # 1) /etc/crypttab exists with a persistent (non-random-key) encrypted swap,
    # 2) /proc/cmdline contains resume=/dev/mapper/... (or resume=UUID=...), AND
    # 3) /etc/initramfs-tools/conf.d/resume specifies RESUME=...
    local cmdline_file="${1:-/proc/cmdline}"
    local crypttab_file="${2:-/etc/crypttab}"
    local resume_conf="${3:-/etc/initramfs-tools/conf.d/resume}"

    [[ -f "$crypttab_file" && -f "$resume_conf" ]] || return 1
    # Random-key swap (/dev/urandom) can NEVER resume from hibernate
    if grep -qE '^[^#].*swap.*/dev/urandom' "$crypttab_file" 2>/dev/null; then
        return 1
    fi
    grep -qE 'resume=(/dev/mapper/|UUID=)' "$cmdline_file" 2>/dev/null || return 1
    grep -qE '^RESUME=(/dev/mapper/|UUID=)' "$resume_conf" 2>/dev/null || return 1
    return 0
}

detect_dualboot_windows() {
    local efi_root="${1:-/boot/efi}"
    [[ -d "$efi_root/EFI/Microsoft" || -d "$efi_root/efi/Microsoft" ]]
}

detect_has_hibernate_resume() {
    local cmdline_file="${1:-/proc/cmdline}"
    local ram_kb zram_kb real_swap_kb
    ram_kb="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || echo 0)"
    zram_kb="$(awk 'NR>1 && $1 ~ /zram/ {sum += $3} END {print sum+0}' /proc/swaps 2>/dev/null || echo 0)"
    real_swap_kb="$(awk 'NR>1 && $1 !~ /zram/ {sum += $3} END {print sum+0}' /proc/swaps 2>/dev/null || echo 0)"

    if ! check_hibernate_swap_capacity_kb "$ram_kb" "$zram_kb" "$real_swap_kb"; then
        return 1
    fi
    if detect_has_luks_swap && ! detect_luks_swap_resume_ready "$cmdline_file"; then
        return 1
    fi
    grep -qE '(^|[[:space:]])resume=(/dev/|UUID=|PARTUUID=|LABEL=)' "$cmdline_file" 2>/dev/null
}

detect_vaapi_decode_supported() {
    # Checks vainfo for hardware H.264 or VP9 decode entrypoints (VAEntrypointVLD).
    if ! command -v vainfo >/dev/null 2>&1; then
        return 1
    fi
    local out
    out="$(vainfo 2>&1 || true)"
    echo "$out" | grep -qiE 'VAProfile(H264|VP9).*VAEntrypointVLD'
}

detect_shairport_audio_backend() {
    if command -v shairport-sync >/dev/null 2>&1; then
        if shairport-sync -h 2>&1 | grep -qiE '\bpw\b|pipewire'; then
            echo "pw"
            return 0
        fi
    fi
    echo "pa"
}

first_match() {
    local pattern="$1"; shift
    find "$@" -maxdepth 1 -name "$pattern" -printf '%f\n' 2>/dev/null | head -1 || true
}

# Hardware-RAM-aware local Ollama model selector (Dense & Sparse MoE)
# Sparse MoE models (gemma4:26b, qwen3.5:35b-a3b) activate only ~3-4B params/token
# for 3B-class CPU speed while delivering 26B-35B intelligence when RAM fits weights.
recommend_ollama_model() {
    local ram_gb="${1:-$(detect_ram_gb)}"
    if (( ram_gb >= 32 )); then
        echo "qwen3.5:35b-a3b"
    elif (( ram_gb >= 22 )); then
        echo "gemma4:26b"
    elif (( ram_gb >= 15 )); then
        echo "qwen3.5:9b"
    elif (( ram_gb >= 11 )); then
        echo "qwen3:4b"
    else
        # 8GB RAM laptops (OS + browser already use ~2.5-3GB): keep local AI at ~1.4GB (~1.8GB live RAM)
        # so disk/swap NEVER spikes to 100%. (Users can also try Sparse MoE 'granite3.1-moe:3b' [0.8B active].)
        echo "qwen3:1.7b"
    fi
}

resolve_ollama_model() {
    local requested="${1:-auto}"
    local ram_gb="${2:-$(detect_ram_gb)}"
    if [[ -z "$requested" || "$requested" == "auto" ]]; then
        recommend_ollama_model "$ram_gb"
        return 0
    fi
    if [[ ! "$requested" =~ ^[a-zA-Z0-9._:/-]+$ ]]; then
        echo "❌ Rejected invalid Ollama model tag: '$requested'" >&2
        return 1
    fi
    echo "$requested"
}

# Secure Boot & UEFI MOK helpers (Ubuntu 26.04 ships Secure Boot enabled by default)
detect_secure_boot_enabled() {
    local efivars_dir="${1:-${MACUBUNTU_EFIVARS_DIR:-/sys/firmware/efi/efivars}}"
    # Primary: query via mokutil (single invocation, parse once).
    if command -v mokutil >/dev/null 2>&1; then
        local sb_state
        sb_state="$(mokutil --sb-state 2>/dev/null || true)"
        if echo "$sb_state" | grep -qi "SecureBoot enabled"; then
            return 0
        elif echo "$sb_state" | grep -qi "SecureBoot disabled"; then
            return 1
        fi
    fi
    # Fallback: read the UEFI variable file directly.
    # UEFI variable file layout: 4-byte little-endian attributes + variable data.
    # SecureBoot data is a single UINT8 byte: 0x01 = enabled, 0x00 = disabled.
    # Read all hex bytes sequentially (no lseek dependency) and inspect the final data byte.
    local sb_var
    sb_var="$(find "$efivars_dir" -maxdepth 1 -name 'SecureBoot-*' 2>/dev/null | head -n1 || true)"
    if [[ -n "$sb_var" && -r "$sb_var" ]]; then
        local hex_bytes data_hex
        hex_bytes="$(od -An -tx1 "$sb_var" 2>/dev/null | tr -d ' \n' || true)"
        # Drop the first 4 bytes (8 hex chars) = attributes; last 2 hex chars = data byte.
        data_hex="${hex_bytes: -2}"
        [[ "$data_hex" == "01" ]] && return 0
    fi
    return 1
}

detect_mok_der_path() {
    if [[ -n "${MACUBUNTU_MOK_DER:-}" ]]; then
        [[ -f "$MACUBUNTU_MOK_DER" ]] && { echo "$MACUBUNTU_MOK_DER"; return 0; }
        return 1
    fi
    local cand
    for cand in \
        "/var/lib/shim-signed/mok/MOK.der" \
        "/var/lib/dkms/mok.pub"; do
        if [[ -n "$cand" && -f "$cand" ]]; then
            echo "$cand"
            return 0
        fi
    done
    return 1
}

detect_mok_enrolled() {
    local mok_der="${1:-$(detect_mok_der_path 2>/dev/null || true)}"
    [[ -n "$mok_der" && -f "$mok_der" ]] || return 1
    command -v mokutil >/dev/null 2>&1 || return 1
    # `mokutil --test-key` exits 0 when the key is ALREADY enrolled in UEFI MOK list
    mokutil --test-key "$mok_der" >/dev/null 2>&1
}

detect_mglru_supported() {
    [[ -f /sys/kernel/mm/lru_gen/enabled ]] && return 0 || return 1
}

detect_is_ssd() {
    local root_dev dev
    root_dev="$(findmnt -no SOURCE / 2>/dev/null | sed 's/\[.*\]//')"
    dev="$(lsblk -no PKNAME "$root_dev" 2>/dev/null | head -1)"
    [[ -n "$dev" ]] && [[ "$(cat /sys/block/$dev/queue/rotational 2>/dev/null)" == "0" ]]
}

detect_is_btrfs_root() {
    [[ "$(detect_root_fstype)" == "btrfs" ]]
}

detect_sleep_mode() {
    cat /sys/power/mem_sleep 2>/dev/null
}

detect_supports_s2idle() {
    detect_is_laptop && grep -q "s2idle" /sys/power/mem_sleep 2>/dev/null
}

# ============================================================================
# Apple-Style Hardware Tier & Capability Profiling (read-only, zero side effects)
# ============================================================================

detect_ram_total_mb() {
    awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0
}

detect_hw_tier() {
    local ram_mb
    ram_mb="$(detect_ram_total_mb)"
    if (( ram_mb <= 10240 )); then
        echo "CONSTRAINED"   # <= 10 GB / 8 GB class
    elif (( ram_mb <= 22528 )); then
        echo "BALANCED"      # 10-22 GB / 16 GB class
    else
        echo "HEADROOM"      # >= 23 GB / 32 GB+ class
    fi
}

detect_psi_supported() {
    [[ -r /proc/pressure/memory && -r /proc/pressure/cpu && -r /proc/pressure/io ]]
}

detect_gpu_vaapi_driver() {
    # Returns the exact VA-API driver or empty if unsupported/unknown (never blind-sets radeonsi)
    local pci_vga
    pci_vga="$(lspci -nn 2>/dev/null | grep -iE 'vga|3d|display' || true)"
    if grep -qiE 'AMD|ATI' <<<"$pci_vga"; then
        echo "radeonsi"
    elif grep -qi 'Intel' <<<"$pci_vga"; then
        # Gen8+ uses iHD (intel-media-driver); fallback checked at runtime
        if [[ -f /usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so ]]; then
            echo "iHD"
        elif [[ -f /usr/lib/x86_64-linux-gnu/dri/i965_drv_video.so ]]; then
            echo "i965"
        fi
    else
        echo ""
    fi
}

detect_scaling_driver() {
    cat /sys/devices/system/cpu/cpufreq/policy0/scaling_driver 2>/dev/null || echo "unknown"
}

detect_epp_supported() {
    [[ -f /sys/devices/system/cpu/cpufreq/policy0/energy_performance_preference ]]
}

detect_s0ix_supported() {
    # Verify s2idle is listed AND platform power controller exists (prevents battery drain bug)
    grep -qw "s2idle" /sys/power/mem_sleep 2>/dev/null &&         { [[ -d /sys/kernel/debug/amd_pmc ]] || [[ -d /sys/kernel/debug/pmc_core ]] || lsmod 2>/dev/null | grep -qE '^(amd_pmc|intel_pmc_core)'; }
}

detect_vmtouch_budget_mb() {
    local ram_mb tier pct
    ram_mb="$(detect_ram_total_mb)"
    tier="$(detect_hw_tier)"
    case "$tier" in
        CONSTRAINED) pct=10 ;;  # ~800 MB max on 8 GB (leaves room for Ollama + GNOME)
        BALANCED)    pct=15 ;;
        HEADROOM)    pct=20 ;;
    esac
    echo $(( ram_mb * pct / 100 ))
}

detect_psd_safe() {
    local tier ff_kb=0
    tier="$(detect_hw_tier)"
    if [[ "$tier" == "CONSTRAINED" ]]; then
        return 1  # Too risky on 8 GB with Ollama unless explicitly forced
    fi
    if [[ -d "$HOME/.mozilla/firefox" ]]; then
        ff_kb="$(du -sk "$HOME/.mozilla/firefox" 2>/dev/null | awk '{print $1}' || echo 0)"
        (( ff_kb < 716800 )) && return 0 || return 1  # Keep under 700 MB
    fi
    return 0
}

detect_p5_rollback_ready() {
    # Gate for R0 edits (fstab, GRUB): verifies snapshot_file is defined and real
    declare -F snapshot_file >/dev/null 2>&1 && declare -F rollback_all >/dev/null 2>&1
}


# ============================================================================
# Perceptual Choreography & SCX sched_ext Probes (read-only, zero side effects)
# ============================================================================

detect_sched_ext_supported() {
    # Returns 0 if kernel exposes sched_ext (/sys/kernel/sched_ext present)
    [[ -d /sys/kernel/sched_ext ]] && return 0 || return 1
}

detect_gnome_shell_major() {
    # Returns GNOME Shell major version as integer, or 0 if unavailable/undetectable
    local ver
    ver="$(gnome-shell --version 2>/dev/null | grep -oE '[0-9]+' | head -1 || true)"
    if [[ -n "$ver" ]]; then
        echo "$ver"
    else
        echo "0"
    fi
}
