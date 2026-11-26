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
umask 022

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
        *)           echo "❌ Unknown option: $1" >&2; usage; exit 2 ;;
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

echo "=================================================================="
echo "  ⚡ macubuntu CO-RE eBPF Sensor Readiness"
echo "=================================================================="
echo "  Source:          $SRC_C"
echo "  Kernel BTF:      $([[ $HAS_BTF -eq 1 ]] && echo "AVAILABLE ($BTF_PATH)" || echo 'MISSING')"
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

# --- P0: Validate CPU architecture explicitly; fail cleanly on unsupported ---
ARCH_MACRO=""
case "$(uname -m)" in
    x86_64|amd64)   ARCH_MACRO="__TARGET_ARCH_x86" ;;
    aarch64|arm64)  ARCH_MACRO="__TARGET_ARCH_arm64" ;;
    *)              echo "❌ Unsupported architecture: $(uname -m). Only x86_64 and aarch64/arm64 are supported." >&2; exit 1 ;;
esac

EXTRA_FLAGS=()
if [[ "$WITH_LSM" -eq 1 ]]; then
    if [[ "$HAS_BPF_LSM" -ne 1 ]]; then
        echo "  ⚠️  Note: kernel /sys/kernel/security/lsm does not include 'bpf' yet (boot with lsm=...,bpf to activate LSM)."
    fi
    EXTRA_FLAGS+=("-DENABLE_CELINA_BPF_LSM")
fi

# Multiarch include path if present
MULTIARCH_INCLUDE="/usr/include/$(uname -m)-linux-gnu"
if [[ -d "$MULTIARCH_INCLUDE" ]]; then
    EXTRA_FLAGS+=("-I$MULTIARCH_INCLUDE")
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

# --- P0: Verify all .bpf.c source files exist before compiling ---
BPF_SOURCES=(
    "$SRC_C"
    "$REPO_DIR/system/ebpf/sensors/exec.bpf.c"
    "$REPO_DIR/system/ebpf/sensors/net.bpf.c"
)
if [[ "$WITH_LSM" -eq 1 ]]; then
    BPF_SOURCES+=("$REPO_DIR/system/ebpf/enforce/focus.bpf.c")
fi
for src in "${BPF_SOURCES[@]}"; do
    if [[ ! -f "$src" ]]; then
        echo "❌ Missing eBPF source file: $src" >&2
        exit 1
    fi
done

# Verify <bpf/bpf_helpers.h> (libbpf-dev) exists
if [[ ! -f "/usr/include/bpf/bpf_helpers.h" ]]; then
    echo "❌ Missing <bpf/bpf_helpers.h>. Install libbpf-dev: sudo apt-get install -y libbpf-dev" >&2
    exit 1
fi

# --- P0: Atomic generation with cleanup trap ---
_cleanup_ebpf() {
    rm -f "$OUT_DIR/.vmlinux.h.tmp.*" "$OUT_DIR"/*.bpf.o.tmp.* 2>/dev/null || true
}
trap _cleanup_ebpf EXIT

mkdir -p "$OUT_DIR"

# Generate vmlinux.h atomically
VMXLINUX_TMP="$(mktemp "$OUT_DIR/.vmlinux.h.XXXXXX")"
bpftool btf dump file "$BTF_PATH" format c > "$VMXLINUX_TMP"
mv -f "$VMXLINUX_TMP" "$OUT_DIR/vmlinux.h"
chmod 0644 "$OUT_DIR/vmlinux.h"

# Compile .bpf.o objects atomically
_compile_bpf() {
    local src="$1" out="$2" flags="$3"
    local tmp_out
    tmp_out="$(mktemp "$OUT_DIR/.${out##*/}.tmp.XXXXXX")"
    clang -O2 -g -target bpf -D"$ARCH_MACRO" ${EXTRA_FLAGS[@]+"${EXTRA_FLAGS[@]}"} -I"$OUT_DIR" -c "$src" -o "$tmp_out"
    mv -f "$tmp_out" "$out"
    chmod 0644 "$out"
}

_compile_bpf "$SRC_C" "$OUT_DIR/celina_sensor.bpf.o" ""
_compile_bpf "$REPO_DIR/system/ebpf/sensors/exec.bpf.c" "$OUT_DIR/exec.bpf.o" ""
_compile_bpf "$REPO_DIR/system/ebpf/sensors/net.bpf.c" "$OUT_DIR/net.bpf.o" ""
if [[ "$WITH_LSM" -eq 1 ]]; then
    _compile_bpf "$REPO_DIR/system/ebpf/enforce/focus.bpf.c" "$OUT_DIR/focus.bpf.o" ""
fi

echo "✅ Compiled CO-RE eBPF objects under: $OUT_DIR"