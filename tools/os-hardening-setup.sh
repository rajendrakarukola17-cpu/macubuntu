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

DRY_RUN=0
ENABLE_PREEMPT_RT=0
ENABLE_OLLAMA_HARDENING=0
SEAL_TPM2=0
TPM2_BLOCK_DEV=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
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

run_cmd() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        printf '[dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

echo "=== macubuntu 7-Layer OS-Level Kernel Hardening ==="

# 1. sched_ext + scx_lavd / scx_rusty
echo "[1/7] sched_ext (scx_lavd <-> scx_rusty)"
if [[ -f /sys/kernel/sched_ext/state ]]; then
    echo "      kernel sched_ext state: $(cat /sys/kernel/sched_ext/state 2>/dev/null || echo unknown)"
else
    echo "      kernel sched_ext not exposed (requires Linux 6.12+); configs staged for upgrade"
fi
run_cmd sudo install -d -m 0755 /etc/scx_loader /etc/polkit-1/rules.d
run_cmd sudo install -m 0644 "$REPO_DIR/system/scx/config.toml" /etc/scx_loader/config.toml
run_cmd sudo install -m 0644 "$REPO_DIR/system/polkit/99-scx-loader.rules" /etc/polkit-1/rules.d/99-scx-loader.rules

# 2. cgroup v2 slice for Ollama
echo "[2/7] cgroup v2 hard memory ceiling (local-inference.slice: High=5G, Max=6G)"
USER_SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
run_cmd install -d -m 0755 "$USER_SYSTEMD_DIR"
run_cmd install -m 0644 "$REPO_DIR/system/systemd/local-inference.slice" "$USER_SYSTEMD_DIR/local-inference.slice"
run_cmd sudo install -m 0644 "$REPO_DIR/system/systemd/local-inference.slice" /etc/systemd/system/local-inference.slice
run_cmd sudo install -d -m 0755 /etc/systemd/system/ollama.service.d
run_cmd sudo install -m 0644 "$REPO_DIR/system/systemd/ollama-8gb-ram.conf" /etc/systemd/system/ollama.service.d/8gb-ram.conf
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
    run_cmd install -d -m 0755 "$USER_SYSTEMD_DIR/celina-voice.service.d"
    run_cmd install -m 0644 "$REPO_DIR/system/systemd/celina-voice-rt.conf" \
        "$USER_SYSTEMD_DIR/celina-voice.service.d/rt-fifo90.conf"
else
    echo "[5/7] PREEMPT_RT skipped (optional; re-run with --enable-preempt-rt if cyclictest shows jitter)"
fi

# 6. Opt-in TPM 2.0 measured boot sealing (using --tpm2-public-key so kernel updates don't break)
if [[ "$SEAL_TPM2" -eq 1 ]]; then
    echo "[6/7] TPM 2.0 PCR-signed sealing for Tier B secrets"
    KREL="$(uname -r)"
    run_cmd sudo install -d -m 0700 /etc/celina
    run_cmd sudo systemd-measure sign \
        --linux="/boot/vmlinuz-${KREL}" \
        --initrd="/boot/initrd.img-${KREL}" \
        --phase=enter-initrd \
        --public-key=/etc/celina/tierb-pcr.pub
    if [[ -n "$TPM2_BLOCK_DEV" ]]; then
        run_cmd sudo systemd-cryptenroll --tpm2-device=auto \
            --tpm2-public-key=/etc/celina/tierb-pcr.pub "$TPM2_BLOCK_DEV"
    fi
    ID_CRED="${XDG_DATA_HOME:-$HOME/.local/share}/celina/apple-research/identity.cred"
    if [[ -f "$ID_CRED" ]] || [[ "$DRY_RUN" -eq 1 ]]; then
        run_cmd sudo systemd-creds encrypt --with-key=tpm2 "$ID_CRED" /etc/celina/tierb-secret.cred
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
