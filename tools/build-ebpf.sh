#!/usr/bin/env bash
# ============================================================================
# macubuntu — CO-RE eBPF Kernel Sensor Builder & Pre-Flight Checker
#
# Checks kernel BTF (/sys/kernel/btf/vmlinux), BPF LSM (/sys/kernel/security/lsm),
# clang, and bpftool before compiling system/ebpf/celina_sensor.bpf.c into a
# portable CO-RE ELF object (`celina_sensor.bpf.o`).
#
# Usage:
#   ./tools/build-ebpf.sh --check       # Inspect kernel eBPF/BTF/LSM support (0 writes)
#   ./tools/build-ebpf.sh --dry-run     # Print exact compile steps (0 writes)
#   ./tools/build-ebpf.sh [--with-lsm]  # Generate vmlinux.h & compile CO-RE .bpf.o
# ============================================================================
set -Eeuo pipefail
umask 077

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_C="$REPO_DIR/system/ebpf/celina_sensor.bpf.c"
OUT_DIR="${MACUBUNTU_EBPF_OUT:-$REPO_DIR/system/ebpf}"
SYS_ROOT="${MACUBUNTU_SYS_ROOT:-/sys}"

CHECK_ONLY=0
DRY_RUN=0
WITH_LSM=0

usage() {
    cat <<'EOF'
Usage: ./tools/build-ebpf.sh [options]

Options:
  --check      Audit kernel BTF, BPF LSM, clang & bpftool readiness (zero writes)
  --dry-run    Show exact CO-RE compilation commands without running them
  --with-lsm   Compile optional SEC("lsm/socket_connect") Focus Mode hook
  -h, --help   Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)     CHECK_ONLY=1; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        --with-lsm)  WITH_LSM=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *)           echo "❌ Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

BTF_PATH="$SYS_ROOT/kernel/btf/vmlinux"
LSM_PATH="$SYS_ROOT/kernel/security/lsm"
LOCKDOWN_PATH="$SYS_ROOT/kernel/security/lockdown"

HAS_BTF=0
HAS_BPF_LSM=0
[[ -r "$BTF_PATH" ]] && HAS_BTF=1
if [[ -r "$LSM_PATH" ]] && grep -qw "bpf" "$LSM_PATH" 2>/dev/null; then
    HAS_BPF_LSM=1
fi
LOCKDOWN_VAL="none"
if [[ -r "$LOCKDOWN_PATH" ]]; then
    LOCKDOWN_VAL="$(tr -d '\n' < "$LOCKDOWN_PATH" 2>/dev/null || echo unknown)"
fi

echo "================================================================"
echo "  ⚡ macubuntu CO-RE eBPF Sensor Readiness"
echo "================================================================"
echo "  Source:          $SRC_C"
echo "  Kernel BTF:      $([[ $HAS_BTF -eq 1 ]] && echo 'AVAILABLE (/sys/kernel/btf/vmlinux)' || echo 'MISSING')"
echo "  BPF LSM Hook:    $([[ $HAS_BPF_LSM -eq 1 ]] && echo 'ENABLED in kernel lsm=' || echo 'NOT ENABLED (tracepoints-only safe)')"
echo "  Kernel Lockdown: $LOCKDOWN_VAL"
echo "  Toolchain:       clang=$(command -v clang 2>/dev/null || echo missing) | bpftool=$(command -v bpftool 2>/dev/null || echo missing)"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    if [[ "$HAS_BTF" -eq 1 ]]; then
        echo "  Status: ✅ Kernel supports CO-RE eBPF tracepoint sensing."
    else
        echo "  Status: ⚠️  BTF absent; Celina automatically falls back to D-Bus/inotify os_events.py."
    fi
    exit 0
fi

ARCH_MACRO="__TARGET_ARCH_x86"
case "$(uname -m)" in
    aarch64|arm64) ARCH_MACRO="__TARGET_ARCH_arm64" ;;
esac

EXTRA_FLAGS=()
if [[ "$WITH_LSM" -eq 1 ]]; then
    if [[ "$HAS_BPF_LSM" -ne 1 ]]; then
        echo "  ⚠️  Note: kernel /sys/kernel/security/lsm does not include 'bpf' yet (boot with lsm=...,bpf to activate LSM)."
    fi
    EXTRA_FLAGS+=("-DENABLE_CELINA_BPF_LSM")
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [dry-run] bpftool btf dump file \"$BTF_PATH\" format c > \"$OUT_DIR/vmlinux.h\""
    echo "  [dry-run] clang -O2 -g -target bpf -D${ARCH_MACRO} ${EXTRA_FLAGS[*]:-} -I\"$OUT_DIR\" -c \"$SRC_C\" -o \"$OUT_DIR/celina_sensor.bpf.o\""
    echo "  [dry-run] clang -O2 -g -target bpf -D${ARCH_MACRO} -I\"$OUT_DIR\" -c \"$REPO_DIR/system/ebpf/sensors/exec.bpf.c\" -o \"$OUT_DIR/exec.bpf.o\""
    echo "  [dry-run] clang -O2 -g -target bpf -D${ARCH_MACRO} -I\"$OUT_DIR\" -c \"$REPO_DIR/system/ebpf/sensors/net.bpf.c\" -o \"$OUT_DIR/net.bpf.o\""
    if [[ "$WITH_LSM" -eq 1 ]]; then
        echo "  [dry-run] clang -O2 -g -target bpf -D${ARCH_MACRO} -I\"$OUT_DIR\" -c \"$REPO_DIR/system/ebpf/enforce/focus.bpf.c\" -o \"$OUT_DIR/focus.bpf.o\""
    fi
    exit 0
fi

if [[ "$HAS_BTF" -ne 1 ]]; then
    echo "❌ Missing kernel BTF ($BTF_PATH). Install Ubuntu generic kernel with CONFIG_DEBUG_INFO_BTF=y." >&2
    exit 1
fi
for req in clang bpftool; do
    if ! command -v "$req" >/dev/null 2>&1; then
        echo "❌ Missing '$req'. Install via: sudo apt-get install -y clang llvm libbpf-dev linux-tools-common bpftool" >&2
        exit 1
    fi
done

mkdir -p "$OUT_DIR"
bpftool btf dump file "$BTF_PATH" format c > "$OUT_DIR/vmlinux.h"
clang -O2 -g -target bpf "-D${ARCH_MACRO}" "${EXTRA_FLAGS[@]}" -I"$OUT_DIR" -c "$SRC_C" -o "$OUT_DIR/celina_sensor.bpf.o"
clang -O2 -g -target bpf "-D${ARCH_MACRO}" -I"$OUT_DIR" -c "$REPO_DIR/system/ebpf/sensors/exec.bpf.c" -o "$OUT_DIR/exec.bpf.o"
clang -O2 -g -target bpf "-D${ARCH_MACRO}" -I"$OUT_DIR" -c "$REPO_DIR/system/ebpf/sensors/net.bpf.c" -o "$OUT_DIR/net.bpf.o"
if [[ "$WITH_LSM" -eq 1 ]]; then
    clang -O2 -g -target bpf "-D${ARCH_MACRO}" -I"$OUT_DIR" -c "$REPO_DIR/system/ebpf/enforce/focus.bpf.c" -o "$OUT_DIR/focus.bpf.o"
fi
echo "✅ Compiled CO-RE eBPF objects under: $OUT_DIR"
