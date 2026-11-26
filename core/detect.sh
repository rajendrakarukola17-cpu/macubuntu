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
    # When testing with an explicit mock efivars dir, read EFI variable directly
    if [[ "$efivars_dir" == "/sys/firmware/efi/efivars" ]] && command -v mokutil >/dev/null 2>&1; then
        mokutil --sb-state 2>/dev/null | grep -qi "SecureBoot enabled" && return 0
        mokutil --sb-state 2>/dev/null | grep -qi "SecureBoot disabled" && return 1
    fi
    local sb_var
    sb_var="$(find "$efivars_dir" -maxdepth 1 -name 'SecureBoot-*' 2>/dev/null | head -n1 || true)"
    if [[ -n "$sb_var" && -r "$sb_var" ]]; then
        # Last byte of UEFI SecureBoot variable is 0x01 when enabled, 0x00 when disabled
        local last_hex
        last_hex="$(tail -c 1 "$sb_var" 2>/dev/null | od -An -tx1 | tr -d ' \n' || true)"
        [[ "$last_hex" == "01" ]] && return 0
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

