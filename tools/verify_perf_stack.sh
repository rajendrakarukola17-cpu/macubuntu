#!/usr/bin/env bash
set -euo pipefail

PASS=0; WARN=0; FAIL=0; INFO=0; SKIP=0
ok()   { echo "  [PASS] $*"; PASS=$((PASS+1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN+1)); }
fail() { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
info() { echo "  [INFO] $*"; INFO=$((INFO+1)); }
skip() { echo "  [SKIP] $*"; SKIP=$((SKIP+1)); }

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

echo "=== macubuntu Performance & Celina Exemption Verification ==="

# ---------------------------------------------------------------------------
# 1. Conflict checks (must NEVER coexist)
# ---------------------------------------------------------------------------
systemctl is-active --quiet earlyoom 2>/dev/null && fail "earlyoom active (conflicts with systemd-oomd)" || ok "No earlyoom conflict"
systemctl is-active --quiet tuned 2>/dev/null && systemctl is-active --quiet power-profiles-daemon 2>/dev/null \
    && fail "tuned + ppd both active" || ok "Single power daemon active"
systemctl is-active --quiet preload 2>/dev/null && warn "preload active (recommend removing in favor of PSI vmtouch)" || ok "No blind preload daemon"

# ---------------------------------------------------------------------------
# 2. Zero-trust & Security Invariants
# ---------------------------------------------------------------------------
grep -qw "mitigations=off" /proc/cmdline && warn "CPU mitigations OFF (weakens bwrap/Landlock)" || ok "CPU mitigations intact"
grep -q "libmimalloc" /etc/ld.so.preload 2>/dev/null && fail "Global mimalloc LD_PRELOAD detected!" || ok "No global mimalloc preload"
# T4: ptrace_scope must be >= 1 (restricted) to prevent ptrace-based local escalation
ptrace_scope="$(sysctl -n kernel.yama.ptrace_scope 2>/dev/null || echo 0)"
[[ "$ptrace_scope" -ge 1 ]] && ok "kernel.yama.ptrace_scope=$ptrace_scope (ptrace restricted)" || warn "kernel.yama.ptrace_scope=$ptrace_scope (T4: set >= 1, e.g. sysctl -w kernel.yama.ptrace_scope=1)"

# ---------------------------------------------------------------------------
# 3. Kernel VM + ZRAM + MGLRU
# ---------------------------------------------------------------------------
[[ "$(sysctl -n vm.page-cluster 2>/dev/null)" == "0" ]] && ok "vm.page-cluster=0 (zram optimal)" || warn "vm.page-cluster != 0"
[[ "$(sysctl -n vm.swappiness 2>/dev/null)" == "150" ]] && ok "vm.swappiness=150" || warn "vm.swappiness != 150"
if [[ -r /sys/kernel/mm/lru_gen/enabled ]]; then
    val="$(cat /sys/kernel/mm/lru_gen/enabled)"
    [[ "$val" == "0x0007" ]] && ok "MGLRU active (0x0007, min_ttl_ms=$(cat /sys/kernel/mm/lru_gen/min_ttl_ms))" || warn "MGLRU=${val}"
fi
grep -q '\[madvise\]' /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null && ok "THP=madvise" || warn "THP not set to madvise"

# ---------------------------------------------------------------------------
# 4. SCX sched_ext touch-boost layer
# ---------------------------------------------------------------------------
echo "--- sched_ext / scx_bpfland ---"
if [[ -d /sys/kernel/sched_ext ]]; then
    ops="$(cat /sys/kernel/sched_ext/root/ops 2>/dev/null || echo unknown)"
    case "$ops" in
        *bpfland*|*lavd*) ok "sched_ext active with $ops" ;;
        *) warn "sched_ext active but ops=$ops (expected bpfland/lavd)" ;;
    esac
else
    info "sched_ext not exposed by kernel (requires Linux 6.12+ sched_ext); scx config staged at /etc/scx_loader/config.toml"
fi
if command -v scx_loader >/dev/null 2>&1; then
    ok "scx_loader binary present"
else
    warn "scx_loader binary not found — install scx-loader package to activate scx_bpfland"
fi
if [[ -f /etc/scx_loader/config.toml ]]; then
    if grep -q 'default_sched = "scx_bpfland"' /etc/scx_loader/config.toml 2>/dev/null; then
        ok "/etc/scx_loader/config.toml defaults to scx_bpfland"
    else
        warn "/etc/scx_loader/config.toml does not default to scx_bpfland"
    fi
else
    warn "/etc/scx_loader/config.toml not yet committed"
fi

# ---------------------------------------------------------------------------
# 5. Celina Exemptions & QoS Protection
# ---------------------------------------------------------------------------
[[ -f /etc/ananicy.d/99-celina-macubuntu.rules ]] && ok "Celina ananicy exemption rules present" || warn "Missing 99-celina-macubuntu.rules"
[[ -f /etc/systemd/system/ollama.service.d/90-macubuntu-qos.conf ]] && ok "Ollama MemoryHigh/QoS cap present" || warn "Ollama uncapped"
# T10: Ollama hardening drop-in must be present and listening only on loopback
if [[ -f /etc/systemd/system/ollama.service.d/hardening.conf ]]; then
    ok "Ollama hardening drop-in present (T10)"
else
    warn "Ollama hardening drop-in missing (T10: re-run os-hardening-setup.sh --enable-ollama-hardening)"
fi
if command -v ollama >/dev/null 2>&1 && pgrep -x ollama >/dev/null 2>&1; then
    ollama_listen="$(ss -tlnp 2>/dev/null | grep ollama | awk '{print $4}' || true)"
    if echo "$ollama_listen" | grep -qE '^(127\.0\.0\.1|::1|\[::1\])'; then
        ok "Ollama listening on loopback only"
    else
        warn "Ollama listening on non-loopback: $ollama_listen (T10: bind to 127.0.0.1/::1 only)"
    fi
else
    info "Ollama not running — skipping loopback bind check"
fi
[[ -f "$HOME/.config/systemd/user/celina-sandbox.slice" ]] && ok "Celina bwrap sandbox slice present" || warn "Sandbox slice missing"

# ---------------------------------------------------------------------------
# 6. vmtouch safety (ensure NO mlock)
# ---------------------------------------------------------------------------
# Inspect /proc/<pid>/cmdline (NUL-delimited) over pgrep -x vmtouch so that
# filenames containing "-l" never trigger false failures.
vmtouch_mlock=0
while IFS= read -r pid; do
    [[ -z "$pid" || ! -r "/proc/$pid/cmdline" ]] && continue
    while IFS= read -r -d '' arg; do
        if [[ "$arg" =~ ^-[a-zA-Z]*l ]]; then
            vmtouch_mlock=1
            break 2
        fi
    done < "/proc/$pid/cmdline"
done < <(pgrep -x vmtouch 2>/dev/null || true)
(( vmtouch_mlock == 1 )) && fail "vmtouch running with -l (mlock)! Remove -l immediately on 8 GB!" || ok "vmtouch mlock guard clean"

# ---------------------------------------------------------------------------
# 7. Network / Hardware / Tracker / Extras (last-mile hardware config)
# ---------------------------------------------------------------------------
echo "--- Network / Hardware / Tracker ---"

# TCP BBR + fq (from system/sysctl/99-macubuntu-net.conf)
if [[ -f /etc/sysctl.d/99-macubuntu-net.conf ]] && grep -q 'net.core.default_qdisc=fq' /etc/sysctl.d/99-macubuntu-net.conf 2>/dev/null; then
    ok "99-macubuntu-net.conf present with fq default qdisc"
else
    warn "99-macubuntu-net.conf missing or not setting fq default qdisc"
fi
if sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null | grep -q 'bbr'; then
    ok "TCP BBR active: $(sysctl -n net.ipv4.tcp_congestion_control)"
else
    warn "TCP BBR not active (net.ipv4.tcp_congestion_control != bbr)"
fi
# Built-in OR modular TCP BBR check — lsmod misses built-in (=y) drivers
if grep -qw 'bbr' /proc/sys/net/ipv4/tcp_available_congestion_control 2>/dev/null; then
    ok "tcp_bbr available in kernel (built-in or module)"
else
    warn "tcp_bbr not available (modprobe tcp_bbr)"
fi

# Wi-Fi powersave disabled (from system/NetworkManager/conf.d/90-macubuntu-wifi.conf)
# NetworkManager: wifi.powersave=0 (default), 1 (ignore), 2 (disable), 3 (enable)
# We want to DISABLE powersave → wifi.powersave=2
if [[ -f /etc/NetworkManager/conf.d/90-macubuntu-wifi.conf ]] && grep -Eq '^\s*wifi\.powersave\s*=\s*2\b' /etc/NetworkManager/conf.d/90-macubuntu-wifi.conf 2>/dev/null; then
    ok "90-macubuntu-wifi.conf present with wifi.powersave=2 (powersave disabled)"
else
    warn "90-macubuntu-wifi.conf missing or wifi.powersave != 2 (note: 1=ignore, 2=disable, 3=enable)"
fi
if nmcli 2>/dev/null | grep -q 'Wireless\|wifi'; then
    wifi_state="$(nmcli -t -f STATE radio wifi 2>/dev/null | head -1 || true)"
    info "Wi-Fi radio state: ${wifi_state:-nmcli not parseable}"
else
    skip "nmcli not available — skipping Wi-Fi runtime state check"
fi

# Tracker-miner-fs cap (user unit — correct path is /etc/systemd/user/ or ~/.config/systemd/user/)
if [[ -f /etc/systemd/user/tracker-miner-fs-3.service.d/90-macubuntu-cap.conf ]] || [[ -f "$HOME/.config/systemd/user/tracker-miner-fs-3.service.d/90-macubuntu-cap.conf" ]]; then
    ok "tracker-miner-fs-3 cap drop-in present"
else
    warn "tracker-miner-fs-3 cap drop-in missing (tracker may consume CPU/IO on idle)"
fi
if systemctl --user is-active --quiet tracker-miner-fs-3 2>/dev/null; then
    info "tracker-miner-fs-3 user service: active (capped by drop-in if present)"
else
    info "tracker-miner-fs-3 user service: not active (or not running under systemd --user)"
fi

# gnome-sushi (Quick Look equivalent — stage 60)
if command -v sushi >/dev/null 2>&1 || dpkg -s gnome-sushi >/dev/null 2>&1; then
    ok "gnome-sushi installed (spacebar Quick Look)"
else
    warn "gnome-sushi not installed — install via stage 45-apps-backups-search.sh"
fi

# ---------------------------------------------------------------------------
# 8. GPU / Shader Cache / PSI Telemetry
# ---------------------------------------------------------------------------
[[ -f /etc/environment.d/90-macubuntu-smooth.conf ]] && ok "Shader cache / VA-API env configured" || warn "90-macubuntu-smooth.conf missing"
[[ -r /proc/pressure/memory ]] && ok "PSI telemetry active: $(head -n1 /proc/pressure/memory)" || warn "PSI unavailable"

# ---------------------------------------------------------------------------
# 9. GPU acceleration & renderer check (graceful when no display)
# ---------------------------------------------------------------------------
echo "--- GPU acceleration ---"
if command -v glxinfo >/dev/null 2>&1; then
    if glxinfo -B 2>/dev/null | grep -qiE 'direct rendering: Yes'; then
        renderer="$(glxinfo -B 2>/dev/null | grep -iE 'OpenGL renderer|OpenGL core profile' | head -1)"
        ok "GPU direct rendering active: ${renderer:-detected}"
    else
        warn "glxinfo reports no direct rendering"
    fi
else
    skip "glxinfo not installed — skipping GPU renderer check"
fi

# ---------------------------------------------------------------------------
# 10. Celina Island choreography.js static guardrails
# ---------------------------------------------------------------------------
echo "--- Celina Island choreography.js ---"
CHOREOGRAPHY_JS="$REPO_DIR/system/gnome/extensions/celina-island@macubuntu.org/choreography.js"
EXTENSION_JS="$REPO_DIR/system/gnome/extensions/celina-island@macubuntu.org/extension.js"

if [[ -f "$CHOREOGRAPHY_JS" ]]; then
    ok "choreography.js present"

    # Guard: no legacy imports.gi.*
    if grep -q 'imports\.gi\.' "$CHOREOGRAPHY_JS"; then
        fail "choreography.js uses legacy imports.gi.* (must use gi:// ESM imports)"
    else
        ok "choreography.js uses ESM gi:// imports only"
    fi

    # Guard: no Shell.GLSLEffect
    if grep -q 'Shell\.GLSLEffect' "$CHOREOGRAPHY_JS"; then
        fail "choreography.js uses deprecated Shell.GLSLEffect"
    else
        ok "choreography.js avoids Shell.GLSLEffect"
    fi

    # Guard: disable() restores slow_down_factor
    if grep -qE 'slow_down_factor|slow-down-factor' "$CHOREOGRAPHY_JS" && grep -q 'disable' "$CHOREOGRAPHY_JS"; then
        ok "choreography.js disable() references slow_down_factor restore"
    else
        fail "choreography.js disable() must restore slow_down_factor"
    fi

    # Guard: has PerceptualChoreographer class
    if grep -q 'export class PerceptualChoreographer' "$CHOREOGRAPHY_JS"; then
        ok "choreography.js exports PerceptualChoreographer class"
    else
        fail "choreography.js missing PerceptualChoreographer export"
    fi
else
    fail "choreography.js missing"
fi

# Guard: extension.js wires choreographer
if [[ -f "$EXTENSION_JS" ]]; then
    if grep -q 'PerceptualChoreographer' "$EXTENSION_JS"; then
        ok "extension.js imports PerceptualChoreographer"
    else
        fail "extension.js does not import PerceptualChoreographer"
    fi
    if grep -q 'showLaunchFeedback' "$EXTENSION_JS" && grep -q 'clearLaunchFeedback' "$EXTENSION_JS"; then
        ok "extension.js implements showLaunchFeedback / clearLaunchFeedback hooks"
    else
        warn "extension.js missing showLaunchFeedback / clearLaunchFeedback pill hooks"
    fi
else
    fail "extension.js missing"
fi

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
echo ""
echo "=== Result: PASS=${PASS} WARN=${WARN} FAIL=${FAIL} INFO=${INFO} SKIP=${SKIP} ==="
(( FAIL == 0 ))
