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

# Tier-based ZRAM sizing (CONSTRAINED=ram, BALANCED=ram*3/4, HEADROOM=min(ram/2,16384))
_zram_size_mb() {
    local tier ram_mb size
    tier="$(detect_hw_tier)"
    ram_mb="$(detect_ram_total_mb)"
    case "$tier" in
        CONSTRAINED) size=$ram_mb ;;
        BALANCED)    size=$(( ram_mb * 3 / 4 )) ;;
        HEADROOM)    size=$(( ram_mb / 2 )); (( size > 16384 )) && size=16384 ;;
    esac
    echo "$size"
}

if apt_has systemd-zram-generator; then
    step "Configuring native systemd-zram-generator (zstd, tier-scaled RAM)..."
    if [[ "$DRY_RUN" != "1" ]] && dpkg -s zram-tools >/dev/null 2>&1; then
        systemctl_disable zramswap
    fi
    apt_install systemd-zram-generator || record_fail "systemd-zram-generator"
    zram_sz="$(_zram_size_mb)"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would write /etc/systemd/zram-generator.conf zram-size=${zram_sz}M tier=$(detect_hw_tier)"
    else
        {
            echo "[zram0]"
            echo "zram-size = ${zram_sz}M"
            echo "compression-algorithm = zstd"
            echo "swap-priority = 100"
            echo "fs-type = swap"
        } | write_root_file /etc/systemd/zram-generator.conf 644
    fi
    run sudo systemctl daemon-reload || true
    run sudo systemctl restart systemd-zram-setup@zram0.service 2>/dev/null || true
else
    step "systemd-zram-generator unavailable; configuring zram-tools fallback..."
    apt_install zram-tools || record_fail "zram-tools"
    local zram_sz; zram_sz="$(_zram_size_mb)"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would write /etc/default/zramswap zram-size=${zram_sz}M"
    else
        {
            echo "# Tier-scaled ZRAM size ($(detect_hw_tier))"
            echo "ZRAM_SIZE=${zram_sz}M"
            echo "COMPRESSION_ALGORITHM=zstd"
            echo "SWAP_PRIORITY=100"
        } | write_root_file /etc/default/zramswap 644
    fi
    systemctl_enable zramswap || record_fail "systemctl enable zramswap"
    run sudo systemctl restart zramswap 2>/dev/null || true
fi

# --- Hardware-adaptive profile sourcing (ram_tier -> profiles/*.env) ---
# Falls back to detect_hw_tier naming when hardware.json is absent.
if [[ -f /etc/macubuntu/hardware.json ]] && command -v jq >/dev/null 2>&1; then
    HW_RAM_TIER="$(jq -r '.ram_tier' /etc/macubuntu/hardware.json)"
    HW_AI_TIER="$(jq -r '.ai_tier' /etc/macubuntu/hardware.json)"
    HW_RAM_MB="$(jq -r '.ram_mb' /etc/macubuntu/hardware.json)"
    HW_DISK_TRAN="$(jq -r '.disk_transport' /etc/macubuntu/hardware.json 2>/dev/null || echo unknown)"
else
    # Backward-compat: map legacy tier name to ram_tier
    case "$(detect_hw_tier | tr '[:upper:]' '[:lower:]')" in
        lite|constrained) HW_RAM_TIER="8gb" ;;
        balanced)         HW_RAM_TIER="16gb" ;;
        workstation|headroom|*) HW_RAM_TIER="32gb" ;;
    esac
    HW_AI_TIER="$HW_RAM_TIER"
    HW_RAM_MB="$(detect_ram_total_mb)"
    HW_DISK_TRAN="unknown"
fi

# Source the declarative profile for the detected RAM tier
PROFILE_FILE="$REPO_DIR/profiles/${HW_RAM_TIER}.env"
if [[ -f "$PROFILE_FILE" ]]; then
    # shellcheck source=../profiles/8gb.env
    source "$PROFILE_FILE"
else
    record_manual "Profile $PROFILE_FILE not found — using 8gb defaults"
    source "$REPO_DIR/profiles/8gb.env"
fi

# Legacy alias for consumers still reading HW_TIER
case "$HW_RAM_TIER" in
    8gb)   HW_TIER="lite"     ;;
    16gb)  HW_TIER="balanced" ;;
    32gb)  HW_TIER="workstation" ;;
    *)     HW_TIER="balanced" ;;
esac

step "Applying hardware-adaptive ZRAM sizing (tier=$HW_RAM_TIER, ZRAM_SIZE_EXPR=$ZRAM_SIZE_EXPR, ${HW_RAM_MB}MB RAM)..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] would source profile $PROFILE_FILE and set ZRAM_SIZE_EXPR=$ZRAM_SIZE_EXPR"
else
    # Evaluate ZRAM_SIZE_EXPR against RAM_MB: "ram" | "ram / 2" | "min(ram / 4, 8192)"
    case "$ZRAM_SIZE_EXPR" in
        ram)           ZRAM_SIZE_MB="$HW_RAM_MB" ;;
        "ram / 2")     ZRAM_SIZE_MB="$(( HW_RAM_MB / 2 ))" ;;
        "min(ram / 4, 8192)")
            ZRAM_SIZE_MB="$(( HW_RAM_MB / 4 ))"
            (( ZRAM_SIZE_MB > 8192 )) && ZRAM_SIZE_MB=8192
            ;;
        *)             ZRAM_SIZE_MB="$(( HW_RAM_MB / 2 ))" ;;
    esac
    if [[ -f /etc/systemd/zram-generator.conf ]]; then
        ZRAM_CONF_MOD="$(sed "s/^zram-size = .*M/zram-size = ${ZRAM_SIZE_MB}M/" /etc/systemd/zram-generator.conf)"
        printf '%s\n' "$ZRAM_CONF_MOD" | write_root_file /etc/systemd/zram-generator.conf 644
        run sudo systemctl daemon-reload 2>/dev/null || true
        run sudo systemctl restart systemd-zram-setup@zram0.service 2>/dev/null || true
    fi
    if [[ -f /etc/default/zramswap ]]; then
        ZRAMSWAP_MOD="$(sed "s/^ZRAM_SIZE=.*M/ZRAM_SIZE=${ZRAM_SIZE_MB}M/" /etc/default/zramswap)"
        printf '%s\n' "$ZRAMSWAP_MOD" | write_root_file /etc/default/zramswap 644
        run sudo systemctl restart zramswap 2>/dev/null || true
    fi
    echo "      ✅ ZRAM sized at ${ZRAM_SIZE_MB}M (ram_tier=$HW_RAM_TIER, expr=$ZRAM_SIZE_EXPR)"
fi

# Scale celina-ai.slice memory ceilings per hardware tier (profile-driven)
if [[ -f /etc/systemd/system/celina-ai.slice ]]; then
    step "Scaling celina-ai.slice cgroup ceilings (ram_tier=$HW_RAM_TIER)..."
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would set MemoryHigh=$CELINA_SLICE_HIGH MemoryMax=$CELINA_SLICE_MAX CPUWeight=$CELINA_CPU_WEIGHT IOWeight=$CELINA_IO_WEIGHT in celina-ai.slice"
    else
        SLICE_MOD="$(sed -e "s/^MemoryHigh=.*/MemoryHigh=$CELINA_SLICE_HIGH/" -e "s/^MemoryMax=.*/MemoryMax=$CELINA_SLICE_MAX/" -e "s/^CPUWeight=.*/CPUWeight=$CELINA_CPU_WEIGHT/" -e "s/^IOWeight=.*/IOWeight=$CELINA_IO_WEIGHT/" /etc/systemd/system/celina-ai.slice)"
        printf '%s\n' "$SLICE_MOD" | write_root_file /etc/systemd/system/celina-ai.slice 644
        run sudo systemctl daemon-reload 2>/dev/null || true
        echo "      ✅ celina-ai.slice: MemoryHigh=$CELINA_SLICE_HIGH MemoryMax=$CELINA_SLICE_MAX CPUWeight=$CELINA_CPU_WEIGHT IOWeight=$CELINA_IO_WEIGHT"
    fi
fi

# Storage-aware udev I/O scheduler rules
step "Deploying storage-aware udev I/O scheduler rules..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] would write /etc/udev/rules.d/60-macubuntu-iosched.rules"
else
    cat <<'EOF' | write_root_file /etc/udev/rules.d/60-macubuntu-iosched.rules 644
# macubuntu storage-aware I/O scheduler (hardware-adaptive)
# NVMe: no scheduler (passthrough); SATA SSD: mq-deadline; HDD: bfq
ACTION=="add|change", KERNEL=="nvme[0-9]n[0-9]", ATTR{queue/scheduler}="none"
ACTION=="add|change", KERNEL=="sd[a-z]", ATTR{queue/rotational}=="0", ATTR{queue/scheduler}="mq-deadline"
ACTION=="add|change", KERNEL=="sd[a-z]", ATTR{queue/rotational}=="1", ATTR{queue/scheduler}="bfq"
EOF
    run sudo udevadm control --reload-rules 2>/dev/null || true
    run sudo udevadm trigger --subsystem-match=block 2>/dev/null || true
    echo "      ✅ udev I/O scheduler rules installed"
fi

step "Deploying single authoritative VM/Network/IO sysctl (/etc/sysctl.d/99-macubuntu-vm.conf)..."
run sudo rm -f /etc/sysctl.d/99-mac-vm.conf /etc/sysctl.d/99-macos-vm.conf /etc/sysctl.d/99-zram.conf
copy_root_file "$REPO_DIR/system/sysctl/99-macubuntu-vm.conf" /etc/sysctl.d/99-macubuntu-vm.conf
run sudo sysctl --system >/dev/null || true

# --- Wave A: Performance fusion tuning (ENABLE_VM_TUNING, MGLRU, shader cache, fstrim, Celina QoS, ananicy, PSD, EPP) ---
stage_perf_fusion_wave_ab() {
    local tier vaapi_drv
    tier="$HW_RAM_TIER"
    vaapi_drv="$(detect_gpu_vaapi_driver)"
    log_info() { echo "  [perf] $*"; }
    log_info "Hardware profile detected: ram_tier=${tier}, ai_tier=${HW_AI_TIER}, ram=$(detect_ram_total_mb)MB, cpu_drv=$(detect_scaling_driver), vaapi=${vaapi_drv:-none}"

    # 1. Unified VM sysctl (profile-scaled per ram_tier)
    if is_true "$ENABLE_VM_TUNING"; then
        # Profile-scaled values (sourced from profiles/${HW_RAM_TIER}.env)
        local swappiness="$SWAPPINESS" dirty_bg="$DIRTY_BG_BYTES" dirty_max="$DIRTY_BYTES"
        local vfs_cache="$VFS_CACHE_PRESSURE" page_cluster="$VM_PAGE_CLUSTER"
        step "Deploying profile-scaled VM sysctl (ram_tier=${HW_RAM_TIER}, swappiness=${swappiness}, dirty_bg=${dirty_bg}, dirty_max=${dirty_max})..."
        # Render the full sysctl file from the authoritative source, applying tier values
        # while preserving all base keys (fs.inotify, kernel.*, net.*, etc.).
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] would render tier-scaled vm.sysctl and write /etc/sysctl.d/99-macubuntu-vm.conf"
        else
            local vm_tmp; vm_tmp="$(mktemp "$WORKDIR/vm-sysctl.XXXXXX")"
            trap 'rm -f "$vm_tmp" 2>/dev/null' RETURN
            # Start from source file (which has base keys + yama.ptrace_scope), then overlay tier values
            sed -E \
                -e "s/^(vm\.swappiness\s*=).*$/\1 ${swappiness}/" \
                -e "s/^(vm\.dirty_background_bytes\s*=).*$/\1 ${dirty_bg}/" \
                -e "s/^(vm\.dirty_bytes\s*=).*$/\1 ${dirty_max}/" \
                -e "s/^(vm\.vfs_cache_pressure\s*=).*$/\1 ${vfs_cache}/" \
                -e "s/^(vm\.page-cluster\s*=).*$/\1 ${page_cluster}/" \
                "$REPO_DIR/system/sysctl/99-macubuntu-vm.conf" > "$vm_tmp"
            # Ensure kernel.yama.ptrace_scope = 1 is present (T4 kernel same-user hardening)
            if ! grep -qE '^kernel\.yama\.ptrace_scope\s*=' "$vm_tmp"; then
                echo "kernel.yama.ptrace_scope=1" >> "$vm_tmp"
            fi
            write_root_file /etc/sysctl.d/99-macubuntu-vm.conf 644 < "$vm_tmp"
            rm -f "$vm_tmp"
            trap - RETURN
        fi
        run sudo sysctl --system >/dev/null || true
    fi

    # 2. MGLRU + THP madvise (only if kernel supports MGLRU)
    if is_true "$ENABLE_MGLRU" && detect_mglru_supported; then
        step "Deploying MGLRU + THP madvise (kernel MGLRU supported)..."
        copy_root_file "$REPO_DIR/system/tmpfiles.d/macubuntu-mm.conf" /etc/tmpfiles.d/macubuntu-mm.conf 644
        run sudo systemd-tmpfiles --create /etc/tmpfiles.d/macubuntu-mm.conf 2>/dev/null || true
    fi

    # 3. GPU Shader Cache + Hardware-Matched VA-API (never blind radeonsi)
    if is_true "$ENABLE_SHADER_CACHE"; then
        local cache_size="2G"
        [[ "$HW_RAM_TIER" == "32gb" ]] && cache_size="4G"
        step "Deploying shader cache (${cache_size}) + VA-API driver (${vaapi_drv:-none})..."
        local smooth_file="$HOME/.config/environment.d/90-macubuntu-smooth.conf"
        local existing=""
        [[ -f "$smooth_file" ]] && existing="$(cat "$smooth_file")"
        # Merge with existing Wayland env settings (don't overwrite)
        {
            echo "$existing" | grep -vE '^(MESA_SHADER_CACHE_MAX_SIZE|MESA_GLSL_CACHE_DISABLE|LIBVA_DRIVER_NAME)=' || true
            echo "MESA_SHADER_CACHE_MAX_SIZE=${cache_size}"
            echo "MESA_GLSL_CACHE_DISABLE=false"
            [[ -n "$vaapi_drv" ]] && echo "LIBVA_DRIVER_NAME=${vaapi_drv}"
        } | write_user_file "$smooth_file" 644 || true
    fi

    # 4. fstrim (SSD/NVMe only)
    if is_true "$ENABLE_FSTRIM" && detect_is_ssd; then
        step "Enabling fstrim.timer (SSD/NVMe detected)..."
        systemctl_enable fstrim.timer || true
    fi

    # 5. Celina QoS + Exemptions (always before ananicy)
    if is_true "$ENABLE_CELINA_QOS"; then
        step "Deploying Celina QoS slices & Ollama QoS cap..."
        # Profile-rendered Ollama QoS drop-in via P5 atomic write
        local ollama_mem_high ollama_mem_max ollama_keep_alive ollama_max_models ollama_num_parallel ollama_kv_type
        case "$HW_RAM_TIER" in
            8gb)
                ollama_mem_high="62%"; ollama_mem_max="80%"; ollama_keep_alive="45s"
                ollama_max_models="${LLM_MODELS_MAX:-1}"; ollama_num_parallel="${LLM_PARALLEL_SLOTS:-1}"; ollama_kv_type="${LLM_KV_CACHE_TYPE:-q8_0}" ;;
            16gb)
                ollama_mem_high="70%"; ollama_mem_max="85%"; ollama_keep_alive="5m"
                ollama_max_models="${LLM_MODELS_MAX:-2}"; ollama_num_parallel="${LLM_PARALLEL_SLOTS:-2}"; ollama_kv_type="${LLM_KV_CACHE_TYPE:-q8_0}" ;;
            32gb)
                ollama_mem_high="75%"; ollama_mem_max="88%"; ollama_keep_alive="15m"
                ollama_max_models="${LLM_MODELS_MAX:-3}"; ollama_num_parallel="${LLM_PARALLEL_SLOTS:-4}"; ollama_kv_type="${LLM_KV_CACHE_TYPE:-q8_0}" ;;
        esac
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] would write tier-scaled ollama QoS (tier=${tier}, MemoryHigh=${ollama_mem_high}, MemoryMax=${ollama_mem_max})"
        else
            run sudo mkdir -p /etc/systemd/system/ollama.service.d
            {
                echo "[Service]"
                echo "CPUWeight=75"
                echo "IOWeight=50"
                echo "MemoryHigh=${ollama_mem_high}"
                echo "MemoryMax=${ollama_mem_max}"
                echo "OOMScoreAdjust=300"
                echo "ManagedOOMMemoryPressure=kill"
                echo "ManagedOOMMemoryPressureLimit=85%"
                echo "Environment=OLLAMA_KEEP_ALIVE=${ollama_keep_alive}"
                echo "Environment=OLLAMA_MAX_LOADED_MODELS=${ollama_max_models}"
                echo "Environment=OLLAMA_NUM_PARALLEL=${ollama_num_parallel}"
                echo "Environment=OLLAMA_KV_CACHE_TYPE=${ollama_kv_type}"
            } | write_root_file /etc/systemd/system/ollama.service.d/90-macubuntu-qos.conf 644
        fi
        run_as_user install -d -m 755 "$HOME/.config/systemd/user/celina-daemon.service.d"
        run_as_user install -Dm644 "$REPO_DIR/system/systemd/celina-daemon.service.d/90-macubuntu-qos.conf" \
            "$HOME/.config/systemd/user/celina-daemon.service.d/90-macubuntu-qos.conf"
        # Profile-rendered celina-sandbox.slice via P5 atomic write
        local slice_mem_high slice_mem_max slice_cpu_quota
        case "$HW_RAM_TIER" in
            8gb)
                slice_mem_high="1024M"; slice_mem_max="1536M"; slice_cpu_quota="75%" ;;
            16gb)
                slice_mem_high="2560M"; slice_mem_max="4096M"; slice_cpu_quota="150%" ;;
            32gb)
                slice_mem_high="6144M"; slice_mem_max="8192M"; slice_cpu_quota="300%" ;;
        esac
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] would write profile-scaled celina-sandbox.slice (ram_tier=${HW_RAM_TIER}, MemoryHigh=${slice_mem_high}, MemoryMax=${slice_mem_max}, CPUQuota=${slice_cpu_quota})"
        else
            run_as_user mkdir -p "$HOME/.config/systemd/user"
            {
                echo "[Unit]"
                echo "Description=Clamped QoS slice for Celina zero-trust bwrap sandboxes (tier=${tier})"
                echo ""
                echo "[Slice]"
                echo "CPUWeight=25"
                echo "IOWeight=20"
                echo "CPUQuota=${slice_cpu_quota}"
                echo "MemoryHigh=${slice_mem_high}"
                echo "MemoryMax=${slice_mem_max}"
                echo "ManagedOOMMemoryPressure=kill"
            } | write_user_file "$HOME/.config/systemd/user/celina-sandbox.slice" 644
        fi
        run sudo systemctl daemon-reload 2>/dev/null || true
        run_as_user systemctl --user daemon-reload 2>/dev/null || true
    fi

    # 5.5. systemd-resolved DNS cache drop-in (gated by ENABLE_DNS_CACHE)
    if is_true "$ENABLE_DNS_CACHE"; then
        step "Deploying systemd-resolved DNS cache drop-in..."
        run sudo install -d -m 755 /etc/systemd/resolved.conf.d
        copy_root_file "$REPO_DIR/system/systemd/resolved.conf.d/90-macubuntu-dnscache.conf" /etc/systemd/resolved.conf.d/90-macubuntu-dnscache.conf 644
        run sudo systemctl daemon-reload 2>/dev/null || true
        run sudo systemctl restart systemd-resolved 2>/dev/null || true
    fi

    # 5.6. systemd-journald rate-limit drop-in (gated by ENABLE_JOURNALD_LITE)
    if is_true "$ENABLE_JOURNALD_LITE"; then
        step "Deploying systemd-journald rate-limit drop-in..."
        run sudo install -d -m 755 /etc/systemd/journald.conf.d
        copy_root_file "$REPO_DIR/system/systemd/journald.conf.d/90-macubuntu.conf" /etc/systemd/journald.conf.d/90-macubuntu.conf 644
        run sudo systemctl daemon-reload 2>/dev/null || true
        run sudo systemctl restart systemd-journald 2>/dev/null || true
    fi

    # 6. ananicy-cpp (with Celina exemption rules installed BEFORE starting service)
    if is_true "$ENABLE_ANANICY"; then
        step "Installing ananicy-cpp with Celina exemption rules..."
        if apt_has ananicy-cpp; then
            apt_install ananicy-cpp || record_fail "apt: ananicy-cpp"
            copy_root_file "$REPO_DIR/system/ananicy.d/99-celina-macubuntu.rules" /etc/ananicy.d/99-celina-macubuntu.rules 644
            systemctl_enable ananicy-cpp || record_fail "systemctl enable ananicy-cpp"
        else
            record_manual "ananicy-cpp unavailable in APT; add pinned package to download-locks.sha256"
        fi
    fi

    # 7. PSD intelligent gate (auto-skips on CONSTRAINED tier)
    if [[ "$ENABLE_PSD" == "true" ]] || { [[ "$ENABLE_PSD" == "auto" ]] && detect_psd_safe; }; then
        step "Installing profile-sync-daemon (PSD)..."
        apt_install profile-sync-daemon || record_fail "apt: profile-sync-daemon"
        run_as_user systemctl --user enable --now psd 2>/dev/null || \
            record_manual "Verify 'psd preview' before enabling psd.service"
    elif [[ "$ENABLE_PSD" == "auto" ]]; then
        log_info "Guardrail: RAM tier=${tier}; skipping psd to protect Ollama/Celina RAM budget."
    fi

    # 8. Dynamic AC/Battery EPP (coexists with ppd)
    if is_true "$ENABLE_EPP_DYNAMIC" && detect_is_laptop && detect_epp_supported; then
        step "Deploying dynamic AC/Battery EPP udev rule..."
        copy_root_file "$REPO_DIR/system/udev/91-macubuntu-epp.rules" /etc/udev/rules.d/91-macubuntu-epp.rules 644
        run sudo udevadm control --reload-rules 2>/dev/null || true
        run sudo udevadm trigger --subsystem-match=power_supply 2>/dev/null || true
    fi

    # 8.5. PSI-guarded vmtouch controller (install binary + systemd user units)
    if is_true "$ENABLE_VMTOUCH"; then
        step "Installing vmtouch + celina-vmtouch-ctl (PSI-aware, touch-only RAM warmer)..."
        apt_install vmtouch || record_fail "apt: vmtouch"
        # Install the executable to ~/.local/bin
        run_as_user install -Dm 755 "$REPO_DIR/tools/celina-vmtouch-ctl" \
            "$HOME/.local/bin/celina-vmtouch-ctl"
        # Install systemd user service + timer
        run_as_user install -Dm 644 "$REPO_DIR/system/systemd/user/celina-vmtouch.service" \
            "$HOME/.config/systemd/user/celina-vmtouch.service"
        run_as_user install -Dm 644 "$REPO_DIR/system/systemd/user/celina-vmtouch.timer" \
            "$HOME/.config/systemd/user/celina-vmtouch.timer"
        run_as_user systemctl --user daemon-reload 2>/dev/null || true
        run_as_user systemctl --user enable celina-vmtouch.timer 2>/dev/null || \
            record_manual "Enable celina-vmtouch.timer manually: systemctl --user enable celina-vmtouch.timer"
        record_manual "Celina /turbo should call: celina-vmtouch-ctl evict (before loading larger Ollama model)"
    else
        echo "    ℹ️  celina-vmtouch-ctl skipped (ENABLE_VMTOUCH=false; opt-in)"
    fi

    # 9. P5 Atomic R0: /etc/fstab noatime (gated by ENABLE_NOATIME, default false)
    if is_true "$ENABLE_NOATIME"; then
        step "Applying noatime to root filesystem in /etc/fstab (P5 atomic R0 write)..."
        local fstype tmp_fstab
        fstype="$(detect_root_fstype)"
        if [[ "$fstype" != "ext4" && "$fstype" != "btrfs" ]]; then
            log_info "Skipping noatime: unsupported root fstype (${fstype})"
        else
            # Idempotency: skip if '/' already has noatime
            if awk '$1 !~ /^#/ && $2 == "/" && $4 ~ /(^|,)noatime(,|$)/ {found=1} END {exit !found}' /etc/fstab 2>/dev/null; then
                log_info "Root filesystem already mounted with noatime."
            else
                tmp_fstab="$(mktemp)"
                # Replace relatime -> noatime if present, otherwise append ,noatime
                awk '
                    $1 !~ /^#/ && $2 == "/" {
                        if ($4 ~ /(^|,)relatime(,|$)/) {
                            gsub(/relatime/, "noatime", $4)
                        } else {
                            $4 = $4 ",noatime"
                        }
                    }
                    { print }
                ' /etc/fstab > "$tmp_fstab"
                # Validate fstab syntax BEFORE atomic commit
                if ! findmnt --verify --tab-file "$tmp_fstab" >/dev/null 2>&1; then
                    log_error "Generated fstab failed findmnt --verify; aborting write."
                    rm -f "$tmp_fstab"
                else
                    write_root_file /etc/fstab 644 < "$tmp_fstab"
                    rm -f "$tmp_fstab"
                    record_manual "Reboot to apply noatime on / (rollback via ./install.sh --rollback, exit 3)"
                fi
            fi
        fi
    fi

    # 10. P5 Atomic R0: GRUB s2idle (gated by ENABLE_S2IDLE, default false; S0ix hardware-verified)
    if is_true "$ENABLE_S2IDLE"; then
        step "Applying mem_sleep_default=s2idle to GRUB (P5 atomic R0 write, S0ix-gated)..."
        if ! detect_is_laptop || ! detect_s0ix_supported; then
            log_info "Guardrail: hardware S0ix/PMC not detected; skipping s2idle to prevent battery drain."
        elif grep -qE '^GRUB_CMDLINE_LINUX_DEFAULT="[^"]*mem_sleep_default=s2idle' /etc/default/grub 2>/dev/null; then
            log_info "GRUB already contains mem_sleep_default=s2idle."
        else
            tmp_grub="$(mktemp)"
            sed -E 's/^(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*)"/\1 mem_sleep_default=s2idle"/' \
                /etc/default/grub > "$tmp_grub"
            # /etc/default/grub is sourced as shell — verify syntax before atomic commit
            if ! bash -n "$tmp_grub"; then
                log_error "Generated /etc/default/grub failed shell syntax check; aborting."
                rm -f "$tmp_grub"
            else
                write_root_file /etc/default/grub 644 < "$tmp_grub"
                rm -f "$tmp_grub"
                if ! run sudo update-grub; then
                    log_error "update-grub failed; run ./install.sh --rollback (exit 3) to restore snapshot."
                fi
                record_manual "Reboot to activate s2idle instant-wake"
            fi
        fi
    fi
}

# 11. P5 Atomic R0: celina-ai.slice — cgroup isolation for Celina AI workloads (Ollama + Celina daemon)
if is_true "$ENABLE_CELINA_AI_SLICE"; then
    step "Deploying celina-ai.slice (CGroup isolation for Celina AI workloads)..."
    copy_root_file "$REPO_DIR/system/systemd/celina-ai.slice" /etc/systemd/system/celina-ai.slice 644
    if [[ "$DRY_RUN" != "1" ]]; then
        sudo systemctl daemon-reload 2>/dev/null || true
    fi
    echo "      ✅ celina-ai.slice installed (CPUWeight=50, IOWeight=50, CPUQuota=200%, MemoryHigh=4G)"
    record_manual "Assign Celina services to celina-ai.slice: systemctl set-property <service> Slice=celina-ai.slice"
else
    echo "    ℹ️  celina-ai.slice skipped (ENABLE_CELINA_AI_SLICE=false)"
fi

# 12. sched_ext BPF CPU scheduler (gated by ENABLE_SCHED_EXT, requires Linux 6.12+)
if is_true "$ENABLE_SCHED_EXT"; then
    step "Loading sched_ext BPF CPU scheduler ($SCX_SCHEDULER)..."
    if [[ ! -d /sys/kernel/sched_ext ]]; then
        log_info "Guardrail: kernel lacks sched_ext support (/sys/kernel/sched_ext missing); skipping."
    elif ! command -v scx_loader >/dev/null 2>&1; then
        step "Installing scx_loader..."
        if apt_has scx_loader; then
            apt_install scx_loader || record_fail "apt: scx_loader"
        else
            record_manual "scx_loader not in APT; add to download-locks.sha256 or install manually"
        fi
    fi
    if command -v scx_loader >/dev/null 2>&1; then
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] would load $SCX_SCHEDULER via scx_loader"
        else
            # Unload any existing sched_ext scheduler first
            sudo scx_loader unload 2>/dev/null || true
            # Load the requested scheduler
            if sudo scx_loader load "$SCX_SCHEDULER" 2>/dev/null; then
                echo "      ✅ sched_ext $SCX_SCHEDULER loaded"
                record_manual "Switch scheduler at runtime: sudo scxctl set <scx_bpfland|scx_lavd>"
            else
                log_error "Failed to load $SCX_SCHEDULER; falling back to CFS"
            fi
        fi
    fi
else
    echo "    ℹ️  sched_ext skipped (ENABLE_SCHED_EXT=false)"
fi

# --- Last-mile network / hardware config (Stage 2 of macOS × Nothing OS fusion) ---
# TCP BBR + fq (gated by ENABLE_NET_BBR)
if is_true "$ENABLE_NET_BBR"; then
    step "Deploying TCP BBR + fq network tuning (99-macubuntu-net.conf)..."
    # Probe BBR/fq kernel modules before applying sysctl (no-op if already loaded or unsupported)
    if [[ "$DRY_RUN" != "1" ]]; then
        sudo modprobe tcp_bbr 2>/dev/null || true
        sudo modprobe sch_fq 2>/dev/null || true
    fi
    copy_root_file "$REPO_DIR/system/sysctl/99-macubuntu-net.conf" /etc/sysctl.d/99-macubuntu-net.conf 644
    if [[ "$DRY_RUN" != "1" ]]; then
        run sudo sysctl --system >/dev/null 2>&1 || true
    fi
    record_manual "Verify: sysctl net.ipv4.tcp_congestion_control (should return bbr)"
else
    echo "    ℹ️  TCP BBR skipped (ENABLE_NET_BBR=false)"
fi

# Wi-Fi powersave disabled (gated by ENABLE_WIFI_NO_POWERSAVE)
if is_true "$ENABLE_WIFI_NO_POWERSAVE"; then
    step "Deploying Wi-Fi powersave disable (90-macubuntu-wifi.conf)..."
    copy_root_file "$REPO_DIR/system/NetworkManager/conf.d/90-macubuntu-wifi.conf" /etc/NetworkManager/conf.d/90-macubuntu-wifi.conf 644
    if [[ "$DRY_RUN" != "1" ]]; then
        run sudo systemctl restart NetworkManager 2>/dev/null || true
    fi
else
    echo "    ℹ️  Wi-Fi powersave disable skipped (ENABLE_WIFI_NO_POWERSAVE=false)"
fi

# Tracker-miner-fs cap (gated by ENABLE_TRACKER_CAP)
if is_true "$ENABLE_TRACKER_CAP"; then
    step "Deploying tracker-miner-fs CPU/IO cap (user systemd drop-in)..."
    run_as_user install -d -m 755 "$HOME/.config/systemd/user/tracker-miner-fs-3.service.d"
    copy_user_file "$REPO_DIR/system/systemd/user/tracker-miner-fs-3.service.d/90-macubuntu-cap.conf" \
        "$HOME/.config/systemd/user/tracker-miner-fs-3.service.d/90-macubuntu-cap.conf" 644
    run_as_user systemctl --user daemon-reload 2>/dev/null || true
else
    echo "    ℹ️  Tracker cap skipped (ENABLE_TRACKER_CAP=false)"
fi

# Deploy smooth.conf BEFORE stage_perf_fusion_wave_ab so the wave's
# MESA_SHADER_CACHE_MAX_SIZE / LIBVA_DRIVER_NAME merge preserves existing values.
ensure_dir "$HOME/.config/environment.d"
copy_user_file "$REPO_DIR/system/environment.d/90-macubuntu-smooth.conf" "$HOME/.config/environment.d/90-macubuntu-smooth.conf" 644

stage_perf_fusion_wave_ab

step "Deploying low-latency NVMe/SSD I/O scheduler rules, weekly TRIM & native Wayland app environment..."
copy_root_file "$REPO_DIR/system/udev/60-macubuntu-io-scheduler.rules" /etc/udev/rules.d/60-macubuntu-io-scheduler.rules
if [[ "$DRY_RUN" != "1" ]]; then
    run sudo udevadm control --reload-rules >/dev/null 2>&1 || true
    run sudo udevadm trigger --subsystem-match=block >/dev/null 2>&1 || true
fi
systemctl_enable fstrim.timer || true
# Only enable irqbalance on multi-NUMA hosts (avoid single-CCD laptop IRQ thrash)
if [[ -f /sys/devices/system/node/online ]]; then
    numa_nodes="$(tr -cd '0-9,' < /sys/devices/system/node/online | wc -c)"
    numa_nodes=$((numa_nodes > 0 ? numa_nodes : 1))
else
    numa_nodes=1
fi
if [[ "$numa_nodes" -gt 1 ]]; then
    apt_install irqbalance || true
    systemctl_enable irqbalance || true
else
    echo "    ℹ️  irqbalance skipped (single NUMA node detected)"
fi
step "Deploying Jetsam-style PSI memory protection (systemd-oomd drop-in)..."
apt_install systemd-oomd || true
copy_root_file "$REPO_DIR/system/systemd/oomd-macubuntu.conf" /etc/systemd/oomd.conf.d/99-macubuntu.conf
systemctl_enable systemd-oomd || true

if grep -qE '^[^#].*\sswap\s' /etc/fstab 2>/dev/null; then
    if is_true "$REPLACE_DISK_SWAP"; then
        step "Backing up /etc/fstab and retiring disk swap in favor of ZRAM (--replace-swap)..."
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] backup /etc/fstab -> /etc/fstab.macubuntu.bak.$$, comment disk swap & swapoff"
        else
            fstab_bak="/etc/fstab.macubuntu.bak.$$(date +%s)"
            snapshot_file /etc/fstab
            # P5 Atomic: transform /etc/fstab via temp file + findmnt verify + write_root_file
            local fstab_tmp; fstab_tmp="$(mktemp "$WORKDIR/fstab.XXXXXX")"
            trap 'rm -f "$fstab_tmp" 2>/dev/null' RETURN
            # Comment out all swap lines (retire disk swap in favor of ZRAM)
            awk '$1 !~ /^#/ && $2 ~ /swap/ && $3 ~ /swap/ {print "# " $0; next} { print }' /etc/fstab > "$fstab_tmp"
            if ! findmnt --verify --tab-file "$fstab_tmp" >/dev/null 2>&1; then
                log_error "Generated fstab failed findmnt --verify; aborting write."
                rm -f "$fstab_tmp"
                trap - RETURN
                record_fail "fstab edit invalid — use --rollback to restore"
            else
                write_root_file /etc/fstab 644 < "$fstab_tmp"
                rm -f "$fstab_tmp"
                trap - RETURN
                record_manual "Reboot to apply noatime on / (rollback via ./install.sh --rollback, exit 3)"
            fi
            run sudo swapoff -a 2>/dev/null || record_fail "swapoff -a failed"
            run sudo systemctl restart systemd-zram-setup@zram0.service 2>/dev/null \
                || run sudo systemctl restart zramswap 2>/dev/null || record_fail "zram restart failed"
            # Verify disk swap is gone
            if grep -qE '^[^#].*\sswap\s' /etc/fstab 2>/dev/null; then
                record_fail "Disk swap still present in /etc/fstab after edit"
            fi
        fi
    else
        echo "    ℹ️  Existing disk swap detected in /etc/fstab — preserved safely (keeps Safe Sleep hibernate"
        echo "       viable while ZRAM takes priority via Priority=100). Pass --replace-swap to retire disk swap."
    fi
fi


# --- Patch 6: True Zero-RAM Standby via systemd-socket-proxyd (8gb tier) ---
# llama.cpp closed native LISTEN_FDS socket activation, so an always-running
# llama-server holds baseline RAM when idle. On 8gb machines, front it with
# systemd-socket-proxyd so idle standby drops to ~112 KB and llama-server
# spawns only when a request actually misses Gear 0/0.5.
if [[ "$HW_RAM_TIER" == "8gb" ]] && command -v apt_install >/dev/null 2>&1; then
    step "Deploying systemd-socket-proxyd zero-RAM standby for llama-server (8gb)..."
    apt_install systemd-socket-proxyd || record_fail "apt: systemd-socket-proxyd"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would create celina-llama-proxy.socket + proxy service"
        echo "      [dry-run] would write /etc/systemd/system/celina-llama-proxy.socket"
    else
        run sudo install -d -m 755 /etc/systemd/system
        # Socket unit: holds /run/user/$UID/celina/llama.sock, proxies to llama-server on demand
        cat <<'SOCKEOF' | write_root_file /etc/systemd/system/celina-llama-proxy.socket 644
[Unit]
Description=Celina LLM Zero-RAM Standby Proxy Socket (8gb)
Documentation=man:systemd-socket-proxyd(8)
After=network-online.target
Wants=network-online.target

[Socket]
ListenStream=/run/user/%i/celina/llama.sock
SocketMode=0600
SocketUser=%i
SocketGroup=render

[Install]
WantedBy=sockets.target
SOCKEOF
        # Proxy@.service: one socket-proxyd instance per accepted connection
        cat <<'SVC EOF' | write_root_file /etc/systemd/system/celina-llama-proxy@.service 644
[Unit]
Description=Celina LLM Proxy for %i (systemd-socket-proxyd)
After=network-online.target

[Service]
ExecStart=/usr/bin/systemd-socket-proxyd /run/user/%i/celina/llama.sock http://127.0.0.1:9931
User=%i
Group=render
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
SVC EOF
        run sudo systemctl daemon-reload 2>/dev/null || true
        echo "      ✅ systemd-socket-proxyd zero-RAM standby configured (8gb)"
        record_manual "Enable proxy: sudo systemctl enable --now celina-llama-proxy.socket"
    fi
else
    echo "    ℹ️  systemd-socket-proxyd skipped (not 8gb tier or apt_install unavailable)"
fi

# --- Patch 8: systemd-oomd Swap-Kill Protection Drop-Ins ---
# Prevent systemd-oomd from killing GNOME Shell, PipeWire, WirePlumber, or
# Celina Core during ZRAM/swap pressure spikes (>90%). celina-ai.slice remains
# eligible for governor-led POST /models/unload first, oomd fallback second.
step "Deploying systemd-oomd user drop-ins (ManagedOOMPreference=omit)..."
for user_unit in org.gnome.Shell@wayland.service pipewire.service wireplumber.service celina-core.service; do
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] would write /etc/systemd/user/${user_unit}.d/99-oomd-protect.conf"
    else
        run_as_user install -d -m 755 "$HOME/.config/systemd/user/${user_unit}.d"
        cat <<'OOOMDEOF' | write_user_file "$HOME/.config/systemd/user/${user_unit}.d/99-oomd-protect.conf" 644
[Service]
ManagedOOMPreference=omit
OOOMDEOF
    fi
done
if [[ "$DRY_RUN" != "1" ]]; then
    run_as_user systemctl --user daemon-reload 2>/dev/null || true
    echo "      ✅ oomd protect drop-ins installed (GNOME Shell, PipeWire, WirePlumber, Celina Core)"
fi

if [[ "$DRY_RUN" != "1" ]] && ! grep -q '^/dev/zram' /proc/swaps 2>/dev/null; then
    record_manual "ZRAM activates cleanly after reboot (verify with: zramctl || swapon --show)"
fi
