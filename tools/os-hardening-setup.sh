#!/usr/bin/env bash
# ==============================================================================
# macubuntu OS-Level Kernel & Systems Hardening Setup (7-Layer Rollout)
# Integration order:
#   1. sched_ext + scx_lavd/scx_rusty (/etc/scx_loader/config.toml + polkit rule)
#   2. cgroup v2 slice (local-inference.slice) + Ollama MemoryHigh/MemoryMax + hardening
#   3. systemd-oomd + PSI kv-psi dynamic KV-cache trimming
#   4. Landlock LSM / SHarness policy export (celina-files-office.policy.json)
#   5. Opt-in PREEMPT_RT (--enable-preempt-rt: sysctl 99-rt.conf, voice FIFO-90, C-state cap)
#   6. Opt-in TPM 2.0 (--seal-tpm2-tierb: PCR-signed policy + systemd-creds encrypt)
#   7. io_uring verification (/sys/kernel/io_uring_disabled == 0)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
# shellcheck source=../core/detect.sh
source "$REPO_DIR/core/detect.sh"

# --- Parse CLI flags BEFORE any init so --dry-run creates ZERO side effects ---
DRY_RUN="${DRY_RUN:-0}"
ENABLE_PREEMPT_RT=0
ENABLE_OLLAMA_HARDENING=1   # Secure by default (T10); opt-out via --no-ollama-hardening
SEAL_TPM2=0
TPM2_BLOCK_DEV=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) export DRY_RUN="1"; shift ;;
        --enable-ollama-hardening) ENABLE_OLLAMA_HARDENING=1; shift ;;
        --no-ollama-hardening) ENABLE_OLLAMA_HARDENING=0; shift ;;
        --enable-preempt-rt) ENABLE_PREEMPT_RT=1; shift ;;
        --seal-tpm2-tierb) SEAL_TPM2=1; shift ;;
        --tpm2-device)
            [[ $# -ge 2 && -n "${2:-}" ]] || { echo "--tpm2-device requires a path" >&2; exit 2; }
            TPM2_BLOCK_DEV="$2"; shift 2 ;;
        -h|--help)
            cat <<'EOF'
Usage: os-hardening-setup.sh [options]
  --dry-run                   Print planned OS-level changes without writing
  --enable-ollama-hardening   Install /etc/systemd/system/ollama.service.d/hardening.conf (default: enabled)
  --no-ollama-hardening       Disable Ollama hardening (NOT recommended — T10)
  --enable-preempt-rt         Install opt-in PREEMPT_RT voice FIFO-90 + sysctl + GRUB C-state drop-ins
  --seal-tpm2-tierb           Print/run TPM 2.0 PCR-signed sealing commands for Tier B secrets
  --tpm2-device /dev/...      Optional LUKS block device for systemd-cryptenroll --tpm2-public-key
EOF
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

# Now safe to init — DRY_RUN is already set from CLI or env
init_defaults
if [[ "$DRY_RUN" -ne 1 ]]; then
    init_runtime_state
fi

# run_cmd wrapper around P5 run() for dry-run-safe execution
run_cmd() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '[dry-run] %s\n' "$*"
    else
        run "$@"
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) export DRY_RUN="1"; shift ;;
        --enable-ollama-hardening) ENABLE_OLLAMA_HARDENING=1; shift ;;
        --enable-preempt-rt) ENABLE_PREEMPT_RT=1; shift ;;
        --seal-tpm2-tierb) SEAL_TPM2=1; shift ;;
        --tpm2-device) TPM2_BLOCK_DEV="${2:-}"; shift 2 ;;
        -h|--help)
            cat <<'EOF'
Usage: os-hardening-setup.sh [options]
  --dry-run                   Print planned OS-level changes without writing
  --enable-ollama-hardening   Install /etc/systemd/system/ollama.service.d/hardening.conf (1.4 OK)
  --enable-preempt-rt         Install opt-in PREEMPT_RT voice FIFO-90 + sysctl + GRUB C-state drop-ins
  --seal-tpm2-tierb           Print/run TPM 2.0 PCR-signed sealing commands for Tier B secrets
  --tpm2-device /dev/...      Optional LUKS block device for systemd-cryptenroll --tpm2-public-key
EOF
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

echo "=== macubuntu 7-Layer OS-Level Kernel Hardening ==="

# 1. sched_ext + scx_bpfland (touch-boost for UI/input/audio)
echo "[1/7] sched_ext (scx_bpfland touch-boost)"
if detect_sched_ext_supported; then
    echo "      kernel sched_ext supported — activating scx_bpfland touch-boost layer"
else
    echo "      kernel sched_ext not exposed (requires Linux 6.12+ with sched_ext); scx config staged for future upgrade"
    record_manual "sched_ext not supported on this kernel; scx_bpfland config staged at /etc/scx_loader/config.toml"
fi
# Commit scx config via P5 atomic write_root_file (single call, deduplicated)
cat "$REPO_DIR/system/scx/config.toml" | write_root_file /etc/scx_loader/config.toml 644
run_cmd sudo install -d -m 0755 /etc/polkit-1/rules.d
run_cmd sudo install -m 0644 "$REPO_DIR/system/polkit/99-scx-loader.rules" /etc/polkit-1/rules.d/99-scx-loader.rules
if command -v scx_loader >/dev/null 2>&1; then
    echo "      scx_loader binary present — scx_bpfland will load on next sched_ext activation"
else
    echo "      scx_loader binary not found — config staged, install scx_loader package to activate"
fi

# 2. cgroup v2 slice for Ollama (RAM-tier aware — 8 GB guard)
echo "[2/7] cgroup v2 hard memory ceiling (local-inference.slice)"
RAM_GB="$(detect_ram_gb)"
if (( RAM_GB <= 8 )); then
    SLICE_HIGH="3G"; SLICE_MAX="4G"
    echo "      8 GB class detected — MemoryHigh=3G, MemoryMax=4G (8 GB RAM Guard)"
elif (( RAM_GB <= 22 )); then
    SLICE_HIGH="5G"; SLICE_MAX="6G"
    echo "      16 GB class detected — MemoryHigh=5G, MemoryMax=6G"
else
    SLICE_HIGH="8G"; SLICE_MAX="12G"
    echo "      32 GB+ class detected — MemoryHigh=8G, MemoryMax=12G"
fi
SLICE_TMPL="$REPO_DIR/system/systemd/local-inference.slice"
SLICE_TMP="$(mktemp)"
sed -e "s/MemoryHigh=.*/MemoryHigh=${SLICE_HIGH}/" \
    -e "s/MemoryMax=.*/MemoryMax=${SLICE_MAX}/" \
    "$SLICE_TMPL" > "$SLICE_TMP"
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
run_cmd install -d -m 0755 "$USER_SYSTEMD_DIR"
run_cmd install -m 0644 "$SLICE_TMP" "$USER_SYSTEMD_DIR/local-inference.slice"
run_cmd sudo install -m 0644 "$SLICE_TMP" /etc/systemd/system/local-inference.slice
rm -f "$SLICE_TMP"
run_cmd sudo install -d -m 0755 /etc/systemd/system/ollama.service.d
# NOTE: ollama-8gb-ram.conf removed — Stage 35 writes the single tier-adapted
# drop-in ollama.service.d/90-macubuntu-qos.conf (tier-based MemoryHigh/Max/IOWeight).
# No conflicting 8GB-hardcoded drop-in is installed.
if [[ "$ENABLE_OLLAMA_HARDENING" -eq 1 ]]; then
    run_cmd sudo install -m 0644 "$REPO_DIR/system/systemd/ollama-hardening.conf" /etc/systemd/system/ollama.service.d/hardening.conf
fi
run_cmd sudo systemctl daemon-reload

# 3. systemd-oomd + PSI
echo "[3/7] systemd-oomd + /proc/pressure/memory (kv-psi)"
run_cmd sudo install -d -m 0755 /etc/systemd/oomd.conf.d
run_cmd sudo install -m 0644 "$REPO_DIR/system/systemd/oomd-macubuntu.conf" /etc/systemd/oomd.conf.d/99-macubuntu.conf
run_cmd sudo systemctl enable --now systemd-oomd

# 4. Landlock / SHarness policy
echo "[4/7] Landlock LSM / SHarness files_office policy"
POLICY_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/celina/policies"
run_cmd install -d -m 0700 "$POLICY_DIR"
run_cmd install -m 0600 "$REPO_DIR/features/celina/config/policies/celina-files-office.policy.json" \
    "$POLICY_DIR/celina-files-office.policy.json"

# 5. Opt-in PREEMPT_RT
if [[ "$ENABLE_PREEMPT_RT" -eq 1 ]]; then
    echo "[5/7] PREEMPT_RT opt-in (FIFO-90 voice drop-in + 99-rt.conf + C-state cap)"
    run_cmd sudo install -m 0644 "$REPO_DIR/system/sysctl/99-celina-rt.conf" /etc/sysctl.d/99-rt.conf
    run_cmd sudo sysctl --system
    run_cmd sudo install -d -m 0755 /etc/default/grub.d
    run_cmd sudo install -m 0644 "$REPO_DIR/system/grub/99-celina-rt.cfg" /etc/default/grub.d/99-celina-rt.cfg
    run_cmd sudo update-grub
    run_cmd install -d -m 0755 "$USER_SYSTEMD_DIR/celina-voice.service.d"
    run_cmd install -m 0644 "$REPO_DIR/system/systemd/celina-voice-rt.conf" \
        "$USER_SYSTEMD_DIR/celina-voice.service.d/rt-fifo90.conf"
    run_cmd systemctl --user daemon-reload
else
    echo "[5/7] PREEMPT_RT skipped (optional; re-run with --enable-preempt-rt if cyclictest shows jitter)"
fi

# 6. Opt-in TPM 2.0 measured boot sealing (PCR-signed policy — kernel updates don't break)
if [[ "$SEAL_TPM2" -eq 1 ]]; then
    echo "[6/7] TPM 2.0 PCR-signed sealing for Tier B secrets"
    KREL="$(uname -r)"
    run_cmd sudo install -d -m 0700 /etc/celina
    # Generate PCR key pair (required by systemd-measure sign --private-key=)
    if [[ "$DRY_RUN" -ne 1 ]]; then
        if [[ ! -f /etc/celina/tierb-pcr.key ]]; then
            run_cmd sudo openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
                -out /etc/celina/tierb-pcr.key 2>/dev/null || true
            run_cmd sudo chmod 600 /etc/celina/tierb-pcr.key
        fi
        if [[ ! -f /etc/celina/tierb-pcr.pub ]]; then
            run_cmd sudo openssl rsa -pubout -in /etc/celina/tierb-pcr.key \
                -out /etc/celina/tierb-pcr.pub 2>/dev/null || true
            run_cmd sudo chmod 644 /etc/celina/tierb-pcr.pub
        fi
    fi
    # Validate TPM2 block device when sealing is requested
    if [[ -n "$TPM2_BLOCK_DEV" ]]; then
        if [[ "$DRY_RUN" -eq 1 ]]; then
            echo "      [dry-run] would validate $TPM2_BLOCK_DEV is a block device"
        elif [[ ! -b "$TPM2_BLOCK_DEV" ]]; then
            echo "❌ $TPM2_BLOCK_DEV is not a block device" >&2
            exit 2
        fi
    fi
    run_cmd sudo systemd-measure sign \
        --linux="/boot/vmlinuz-${KREL}" \
        --initrd="/boot/initrd.img-${KREL}" \
        --phase=enter-initrd \
        --private-key=/etc/celina/tierb-pcr.key \
        --public-key=/etc/celina/tierb-pcr.pub
    if [[ -n "$TPM2_BLOCK_DEV" ]]; then
        run_cmd sudo systemd-cryptenroll --tpm2-device=auto \
            --tpm2-public-key=/etc/celina/tierb-pcr.pub "$TPM2_BLOCK_DEV"
    fi
    ID_CRED="${XDG_DATA_HOME:-$HOME/.local/share}/celina/apple-research/identity.cred"
    if [[ -f "$ID_CRED" ]] || [[ "$DRY_RUN" -eq 1 ]]; then
        run_cmd sudo systemd-creds encrypt --with-key=tpm2 \
            --tpm2-public-key=/etc/celina/tierb-pcr.pub \
            --tpm2-signature=/etc/celina/tierb-pcr.pub.sig \
            "$ID_CRED" /etc/celina/tierb-secret.cred
    fi
else
    echo "[6/7] TPM 2.0 Tier B sealing skipped (optional; pass --seal-tpm2-tierb before enabling Tier B)"
fi

# 7. io_uring status
echo "[7/7] io_uring kernel check"
if [[ -r /sys/kernel/io_uring_disabled ]]; then
    echo "      /sys/kernel/io_uring_disabled = $(cat /sys/kernel/io_uring_disabled) (0 = enabled)"
else
    echo "      /sys/kernel/io_uring_disabled not present on this kernel"
fi
echo "Done."
