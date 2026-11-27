#!/usr/bin/env bash
# ============================================================================
# macubuntu — Unified Ubuntu 26.04 -> macOS OS Project (with Built-In Celina)
# Security-Hardened Orchestrator (Run as normal user, NEVER root).
#
# Exit Code Contract (P5-D4):
#   0  Success — --help, --list, dry-run clean, or full pipeline passed
#   1  General error — root invocation, missing paths, pipeline stage failures
#   2  Misuse — invalid arguments / unknown options
#   3  Rollback completed — --rollback finished (reboot to restore)
#   N  Stage exit code propagated — Stage 00 hard-abort or --fail-fast abort
#
# Examples:
#   ./install.sh --dry-run                       # 100% side-effect-free preview
#   ./install.sh --profile default-8gb           # Full macOS + built-in Celina (Celly)
#   ./install.sh --profile macos-only            # Pure macOS OS without Celina feature
#   ./install.sh --only 20,25                    # Re-apply appearance + dock/extensions only
#   ./install.sh --only 70                       # Install/upgrade built-in Celina feature only
#   ./install.sh --from 35 --skip 60             # Resume from ZRAM, skipping extras
#   ./install.sh --verify                        # Post-install strict health & LUKS audit
# ============================================================================
set -Eeuo pipefail
umask 077
trap 'echo "🚨 Critical error at line $LINENO (exit $?)" >&2' ERR

if [[ $EUID -eq 0 ]]; then
    echo "❌ Do NOT run as root. Execute as your normal desktop user." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export REPO_DIR

# --- Single version source (P1) ---
# Read canonical versions from version.toml. All version fields across the
# repository (manifest celina_version/manifest_version, neural-schema
# schema_version, ARCHITECTURE.md doc version) must agree with this file.
# Single-pass version.toml loader — passes file path via sys.argv, no shell interpolation
load_toml_versions() {
    local toml_file="${REPO_DIR}/version.toml"
    [[ -f "$toml_file" ]] || return 0
    if command -v python3 >/dev/null 2>&1; then
        local out
        out="$(python3 - "$toml_file" <<'PY' 2>/dev/null || true
import sys
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        sys.exit(0)
with open(sys.argv[1], "rb") as fh:
    data = tomllib.load(fh)
def get_v(sec, key="version"):
    val = data.get(sec, {}).get(key, "")
    return str(val) if isinstance(val, (str, int, float)) else ""
print("	".join([
    get_v("celina"),
    get_v("manifest"),
    get_v("schema"),
    get_v("doc"),
    get_v("release", "semver"),
]))
PY
)"
        IFS=$'	' read -r CELINA_VERSION MANIFEST_VERSION SCHEMA_VERSION DOC_VERSION RELEASE_SEMVER <<< "$out"
        export CELINA_VERSION MANIFEST_VERSION SCHEMA_VERSION DOC_VERSION RELEASE_SEMVER
    fi
}

load_toml_versions

ONLY_LIST=""
SKIP_LIST=""
FROM_STAGE=""
LIST_ONLY=0
VERIFY_ONLY=0
DOCTOR_ONLY=0
LOCALE_ONLY=0
UPGRADE_MODE=0

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Options:
  --dry-run                Preview all actions with zero filesystem, network, or service mutations
  --profile <name>         Load allowlisted preset from profiles/ (default-8gb | macos-only | power-user)
  --no-celina              Disable built-in Celina feature (ENABLE_CELINA=false)
  --name <Name>            Set initial assistant display name (default: Celina)
  --nickname <Nick>        Set initial assistant nickname (default: Celly)
  --only <s1,s2>           Run only stages matching prefix/name (e.g. 20,25 or 70-feature-celina)
  --skip <s1,s2>           Skip stages matching prefix/name (e.g. 60)
  --from <num>             Start execution from numbered stage >= <num> (e.g. 35)
  --system-only            Run only system-scoped stages (00,10,35,50,55,99) — useful on multi-user hosts
  --user-only              Run only user-scoped desktop/app/Celina stages (00,20,25,30,40,45,60,70,99)
  --upgrade                Idempotent in-place upgrade from v4.3 -> v4.4 (preserves user DBs & configs)
  --replace-swap           Opt into retiring existing /etc/fstab disk swap in favor of pure ZRAM
  --accessibility          Opt into Orca screen reader, KMagnifier & AT-SPI2 accessibility tools
  --thirdparty             Opt into pinned community third-party extras in Stage 60
  --rust-tools             Opt into pinned Rust cargo builds (Lan Mouse, Clipp) in Stage 60
  --top-panel <style>      Ubuntu top bar style: transparent (default, zero black line) | hidden (removed completely) | glass
  --animation-speed <spd>  GNOME animation cadence: fast (default 0.6x snappy) | normal (1.0x) | instant (disabled)
  --model <tag|auto>       Local Ollama model (default: auto by RAM; e.g. qwen3:4b, granite3.1-moe:3b, qwen3.5:9b, gemma4:26b, qwen3:30b-a3b)
  --continue-on-error      Keep running remaining stages if one stage fails, then print summary (default: ON)
  --fail-fast              Abort immediately on the first failed stage (strict CI/debug mode)
  --macos-cmd-keys         Opt into Wayland-native terminal-aware macOS Cmd+C/V/Q remapping (via keyd)
  --silent-boot            Opt into flicker-free GRUB boot drop-in + active AMD/Intel CPU P-States
  --rollback               Restore pre-change snapshots of /etc/fstab, GRUB, PAM (emergency rollback)
  --experimental-uinput    Opt into legacy /dev/uinput ydotoold fallback (NOT recommended; Portal/AT-SPI preferred)
  --doctor                 Run read-only fast-path health check with actionable fixes (zero sudo/writes)
  --locale                 Run read-only Stage 00 locale & AT-SPI2 .mo localization audit (zero sudo/writes)
  --verify                 Run only Stage 99 strict verification audit (returns nonzero on required failure)
  --verify-models          Run Stage 99 + verify pulled Ollama model SHA-256 digests against model-digests.sha256
  --list                   Show all pipeline stages & features and exit
  -h, --help               Show this help

Environment overrides (take precedence over profiles):
  DRY_RUN=0|1  PROFILE=default-8gb|macos-only|power-user  ENABLE_CELINA=true|false
  DOCK_CHOICE=ubuntu-dock|dash-to-dock|simple-taskbar|none
  LAUNCHER_CHOICE=celina-spotlight|ulauncher|gnome
  SEARCH_BACKEND=lightweight|gnome-indexer  POWER_BACKEND=ppd|tlp
  ENABLE_DESKTOP_ICONS=true|false  POINTER_ACCEL=flat|adaptive  FONT_AA=rgba|grayscale
  ENABLE_EXPERIMENTAL_UINPUT=false|true  ENABLE_BIOMETRICS=false|true
  REPLACE_DISK_SWAP=false|true  ENABLE_ACCESSIBILITY=false|true
  CELINA_NAME="Celina"  CELINA_NICKNAME="Celly"
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)              export DRY_RUN="1"; shift ;;
        --profile)              if [[ $# -lt 2 ]]; then echo "❌ Missing profile name" >&2; exit 2; fi; export PROFILE="$2"; shift 2 ;;
        --no-celina)            export ENABLE_CELINA="false"; shift ;;
        --name)                 if [[ $# -lt 2 ]]; then echo "❌ Missing name" >&2; exit 2; fi; export CELINA_NAME="$2"; shift 2 ;;
        --nickname)             if [[ $# -lt 2 ]]; then echo "❌ Missing nickname" >&2; exit 2; fi; export CELINA_NICKNAME="$2"; shift 2 ;;
        --only)                 if [[ $# -lt 2 ]]; then echo "❌ Missing stage list" >&2; exit 2; fi; ONLY_LIST="$2"; shift 2 ;;
        --skip)                 if [[ $# -lt 2 ]]; then echo "❌ Missing stage list" >&2; exit 2; fi; SKIP_LIST="$2"; shift 2 ;;
        --from)                 if [[ $# -lt 2 ]]; then echo "❌ Missing stage number" >&2; exit 2; fi; FROM_STAGE="$2"; shift 2 ;;
        --system-only)          ONLY_LIST="00,10,32,33,34,35,49,50,55,61,99"; shift ;;
        --user-only)            ONLY_LIST="00,20,25,30,45,46,60,70,99"; shift ;;
        --upgrade)              export UPGRADE_MODE=1; shift ;;
        --replace-swap)         export REPLACE_DISK_SWAP="true"; shift ;;
        --accessibility)        export ENABLE_ACCESSIBILITY="true"; shift ;;
        --thirdparty)           export INSTALL_THIRDPARTY="true"; shift ;;
        --rust-tools)           export INSTALL_RUST_TOOLS="true"; shift ;;
        --top-panel)            if [[ $# -lt 2 ]]; then echo "❌ Missing top panel style" >&2; exit 2; fi; export TOP_PANEL_STYLE="$2"; shift 2 ;;
        --animation-speed)      if [[ $# -lt 2 ]]; then echo "❌ Missing animation speed" >&2; exit 2; fi; export ANIMATION_SPEED="$2"; shift 2 ;;
        --model)                if [[ $# -lt 2 ]]; then echo "❌ Missing Ollama model tag" >&2; exit 2; fi; export OLLAMA_MODEL="$2"; shift 2 ;;
        --continue-on-error)    export CONTINUE_ON_ERROR="true"; shift ;;
        --fail-fast)            export CONTINUE_ON_ERROR="false"; shift ;;
        --macos-cmd-keys)       export ENABLE_MACOS_CMD_KEYS="true"; shift ;;
        --silent-boot)          export ENABLE_SILENT_BOOT="true"; shift ;;
        --rollback)             ROLLBACK_ONLY=1; shift ;;
        --experimental-uinput)  export ENABLE_EXPERIMENTAL_UINPUT="true"; shift ;;
        --doctor)               DOCTOR_ONLY=1; shift ;;
        --locale)               LOCALE_ONLY=1; shift ;;
        --list)                 LIST_ONLY=1; shift ;;
        --verify)               VERIFY_ONLY=1; shift ;;
        --verify-models)        VERIFY_ONLY=1; export VERIFY_MODELS="1"; shift ;;
        -h|--help)              usage; exit 0 ;;
        *)                      echo "❌ Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

# Validate --from strictly as digits (prevents arithmetic injection & crash on non-numeric input)
if [[ -n "$FROM_STAGE" ]]; then
    if [[ ! "$FROM_STAGE" =~ ^[0-9]+$ ]]; then
        echo "❌ Invalid --from value '$FROM_STAGE' (expected numeric stage prefix, e.g. 35)" >&2
        exit 2
    fi
fi

# Validate --top-panel value
if [[ -n "${TOP_PANEL_STYLE:-}" ]]; then
    case "$TOP_PANEL_STYLE" in
        transparent|hidden|glass) ;;
        *) echo "❌ Invalid --top-panel '$TOP_PANEL_STYLE' (expected: transparent|hidden|glass)" >&2; exit 2 ;;
    esac
fi

# Validate --animation-speed value
if [[ -n "${ANIMATION_SPEED:-}" ]]; then
    case "$ANIMATION_SPEED" in
        fast|normal|instant) ;;
        *) echo "❌ Invalid --animation-speed '$ANIMATION_SPEED' (expected: fast|normal|instant)" >&2; exit 2 ;;
    esac
fi

# Validate --profile value
if [[ -n "${PROFILE:-}" ]]; then
    case "$PROFILE" in
        default-8gb|macos-only|power-user) ;;
        *) echo "❌ Invalid --profile '$PROFILE' (expected: default-8gb|macos-only|power-user)" >&2; exit 2 ;;
    esac
fi

# shellcheck source=core/common.sh
source "$REPO_DIR/core/common.sh"

# Execute rollback if requested (P0-2: init_defaults & init_runtime_state first)
if [[ "${ROLLBACK_ONLY:-0}" == "1" ]]; then
    init_defaults
    init_runtime_state
    echo "================================================================"
    echo "  🔄 macubuntu Emergency Rollback"
    echo "================================================================"
    rc=0
    rollback_all || rc=$?
    if [[ "$rc" -ne 0 ]]; then
        echo "❌ Rollback encountered errors (exit ${rc})." >&2
        exit "$rc"
    fi
    echo "================================================================"
    echo "  Rollback complete. Reboot to restore system state."
    echo "================================================================"
    exit 3
fi

if [[ "$DOCTOR_ONLY" -eq 1 ]]; then
    exec bash "$REPO_DIR/tools/doctor.sh"
fi

if [[ "$LOCALE_ONLY" -eq 1 ]]; then
    LOCALE_AUDIT_ONLY=1 DRY_RUN=1 exec bash "$REPO_DIR/stages/00-preflight.sh"
fi

init_defaults

shopt -s nullglob
ALL_STAGES=("$REPO_DIR"/stages/*.sh)
shopt -u nullglob

if [[ ${#ALL_STAGES[@]} -eq 0 ]]; then
    echo "❌ No stages found in $REPO_DIR/stages" >&2
    exit 1
fi

if [[ "$LIST_ONLY" -eq 1 ]]; then
    echo "macubuntu OS Pipeline Stages ($REPO_DIR/stages):"
    for st in "${ALL_STAGES[@]}"; do
        base="$(basename "$st")"
        desc="$(sed -n 's/^# Stage //p' "$st" | head -n1)"
        printf "  %-30s %s\n" "$base" "$desc"
    done
    echo ""
    echo "Built-In Features ($REPO_DIR/features):"
    for fdir in "$REPO_DIR"/features/*/; do
        [[ -d "$fdir" ]] && printf "  %-30s %s\n" "$(basename "$fdir")" "Self-contained feature subsystem"
    done
    exit 0
fi

# Only chmod +x when REPO_DIR is writable by current user (never fail on /usr/share/macubuntu .deb install!)
if [[ "${DRY_RUN:-0}" != "1" && -w "$REPO_DIR/stages" ]]; then
    chmod +x "$REPO_DIR"/stages/*.sh "$REPO_DIR"/core/*.sh "$REPO_DIR"/features/celina/install.sh
fi

if [[ "$VERIFY_ONLY" -eq 1 ]]; then
    exec bash "$REPO_DIR/stages/99-verify.sh"
fi

if [[ "${DRY_RUN:-0}" != "1" ]]; then
    # Concurrency guard (#5): prevent two simultaneous install.sh runs from racing on APT/gsettings
    mkdir -p -m 700 "$HOME/.local/state/macubuntu"
    INSTALL_LOCK_PATH="${XDG_RUNTIME_DIR:-$HOME/.local/state/macubuntu}/macubuntu-install-${UID}.lock"
    if command -v flock >/dev/null 2>&1; then
        exec 9>"$INSTALL_LOCK_PATH"
        if ! flock -n 9; then
            echo "❌ Another macubuntu install.sh instance is already running (lock: $INSTALL_LOCK_PATH)." >&2
            echo "   Wait for it to finish before starting another run." >&2
            exit 1
        fi
    fi

    mkdir -p -m 700 "$HOME/.local/state/macubuntu"
    LOG_FILE="$HOME/.local/state/macubuntu/install.log"
    touch "$LOG_FILE"
    chmod 600 "$LOG_FILE"
    # Prompt for sudo BEFORE redirecting stdout to tee (so password prompt works cleanly)
    sudo -v

    # Keep sudo ticket alive (short sleep loop, killed in cleanup)
    (
        while true; do
            sudo -n true
            sleep 2
            kill -0 "$$" 2>/dev/null || exit
        done
    ) 2>/dev/null &
    SUDO_KEEPALIVE=$!

    exec > >(tee -a "$LOG_FILE") 2>&1
else
    LOG_FILE="(disabled in --dry-run; zero disk writes)"
fi

init_runtime_state
cleanup() {
    if [[ -n "${SUDO_KEEPALIVE:-}" ]]; then
        kill "$SUDO_KEEPALIVE" 2>/dev/null || true
        pkill -P "$SUDO_KEEPALIVE" 2>/dev/null || true
    fi
    if [[ "${DRY_RUN:-0}" != "1" && -n "${WORKDIR:-}" && -d "${WORKDIR:-}" ]]; then
        rm -rf "$WORKDIR" || true
    fi
    if [[ -n "${DIFF_TMP_HOME:-}" && -d "$DIFF_TMP_HOME" ]]; then
        rm -rf "$DIFF_TMP_HOME" || true
    fi
    return 0
}
trap cleanup EXIT

echo "================================================================"
echo "  🍎 macubuntu Unified OS Setup — $(date)"
if [[ "$UPGRADE_MODE" -eq 1 ]]; then
    echo "  Mode:     IDEMPOTENT UPGRADE (--upgrade: preserves SQLite DBs, skills & secrets)"
fi
echo "  Log:      $LOG_FILE"
echo "  Lockfile: $LOCKFILE"
echo "  Profile:  $PROFILE | DRY_RUN=$DRY_RUN | CONTINUE_ON_ERROR=$CONTINUE_ON_ERROR | Built-In Celina=$ENABLE_CELINA (${CELINA_NAME}/${CELINA_NICKNAME})"
echo "  Model:    OLLAMA_MODEL=$OLLAMA_MODEL (resolved: $(resolve_ollama_model "$OLLAMA_MODEL" 2>/dev/null || echo "$OLLAMA_MODEL"))"
echo "================================================================"

if [[ "$UPGRADE_MODE" -eq 1 && "${DRY_RUN:-0}" == "1" ]]; then
    echo ""
    echo "================================================================"
    echo "  📦 Upgrade Simulation Diff (--dry-run --upgrade | apt --simulate mode)"
    echo "================================================================"
    if command -v python3 >/dev/null 2>&1; then
        DIFF_TMP_HOME="$(mktemp -d)"
        trap 'rm -rf "$DIFF_TMP_HOME"' RETURN
        REAL_HOME_VAL="$HOME" \
        HOME="$DIFF_TMP_HOME" TARGET_HOME="$DIFF_TMP_HOME" XDG_CONFIG_HOME="$DIFF_TMP_HOME/.config" XDG_DATA_HOME="$DIFF_TMP_HOME/.local/share" \
        PYTHONDONTWRITEBYTECODE=1 REPO_DIR="$REPO_DIR" python3 - <<'PY' || true
import hashlib
import json
import os
import sys
from pathlib import Path

repo = Path(os.environ["REPO_DIR"])
real_home = Path(os.environ["REAL_HOME_VAL"])
sys.path.insert(0, str(repo / "features" / "celina"))

from celina_pkg import state_ops  # noqa: E402

rep = state_ops.migrate_state(
    dry_run=True,
    schema_path=real_home / ".config" / "celina" / "neural-schema.json",
    template_schema_path=repo / "features" / "celina" / "config" / "neural-schema.json",
    base_dir=real_home / ".local" / "share" / "celina",
)
print(state_ops.format_migrate_report(rep))

# GNOME extensions lockfile diff (read-only)
ext_lock = repo / "system" / "gnome" / "extensions.lock"
ext_dir = real_home / ".local" / "share" / "gnome-shell" / "extensions"
if ext_lock.is_file():
    pins = {}
    for ln in ext_lock.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split("|")
        # Detect host GNOME Shell major version instead of hardcoding 50
        import subprocess
        try:
            gnome_ver = subprocess.check_output(["gnome-shell", "--version"], text=True).strip()
            gnome_major = "".join(ch for ch in gnome_ver if ch.isdigit()).split()[0] if gnome_ver else "50"
        except Exception:
            gnome_major = "50"
        if len(parts) >= 6 and parts[1] == gnome_major:
            pins[parts[0]] = parts[2]
    print("\n🧩 GNOME Extension Pin Diff (GNOME 50 lockfile):")
    for uuid, target_ver in sorted(pins.items()):
        meta = ext_dir / uuid / "metadata.json"
        if meta.is_file():
            try:
                cur_ver = str(json.loads(meta.read_text(encoding="utf-8")).get("version", "?"))
            except Exception:
                cur_ver = "?"
            status = "up-to-date" if cur_ver == target_ver else f"upgrade v{cur_ver} -> v{target_ver}"
        else:
            status = f"would install pinned v{target_ver}"
        print(f"    • {uuid}: {status}")

# System drop-in diff (read-only SHA-256 comparison)
print("\n🛠️  OS Config Drop-In Diff:")
dropins = [
    (repo / "system/sysctl/99-macubuntu-vm.conf", Path("/etc/sysctl.d/99-macubuntu-vm.conf")),
    (repo / "system/environment.d/90-macubuntu-smooth.conf", real_home / ".config/environment.d/90-macubuntu-smooth.conf"),
    (repo / "system/systemd/resolved-mdns.conf", Path("/etc/systemd/resolved.conf.d/99-macubuntu-mdns.conf")),
    (repo / "system/udev/60-macubuntu-io-scheduler.rules", Path("/etc/udev/rules.d/60-macubuntu-io-scheduler.rules")),
]
for src, dst in dropins:
    if not src.is_file():
        continue
    if not dst.is_file():
        state = "NEW (would install)"
    else:
        try:
            s_sha = hashlib.sha256(src.read_bytes()).hexdigest()
            d_sha = hashlib.sha256(dst.read_bytes()).hexdigest()
            state = "unchanged" if s_sha == d_sha else "MODIFIED (would update)"
        except OSError:
            state = "unreadable (would inspect with sudo)"
    print(f"    • {dst}: {state}")
PY
        rm -rf "$DIFF_TMP_HOME"
    fi
    echo "================================================================"
    echo ""
fi

for req_path in core system system/download-locks.sha256 profiles stages features/celina/celina_pkg features/celina/celina_hardware features/celina/config features/celina/skills; do
    if [[ ! -e "$REPO_DIR/$req_path" ]]; then
        echo "❌ Missing required path: $REPO_DIR/$req_path" >&2
        exit 1
    fi
done

if [[ "${DRY_RUN:-0}" != "1" ]]; then
    sudo -v
    (
        while true; do
            sudo -n true
            sleep 50
            kill -0 "$$" 2>/dev/null || exit
        done
    ) 2>/dev/null &
    SUDO_PID=$!
fi

matches_csv() {
    local stage_base="$1" csv="$2"
    local stem="${stage_base%.sh}"
    local num="${stage_base%%-*}"
    local item
    IFS=',' read -r -a items <<< "$csv"
    for item in "${items[@]}"; do
        item="${item%.sh}"
        if [[ "$item" == "$stage_base" || "$item" == "$stem" || "$item" == "$num" ]]; then
            return 0
        fi
    done
    return 1
}

SELECTED=()
for st in "${ALL_STAGES[@]}"; do
    base="$(basename "$st")"
    num="${base%%-*}"
    if [[ -n "$FROM_STAGE" ]] && [[ "$num" =~ ^[0-9]+$ ]] && (( 10#$num < 10#$FROM_STAGE )); then
        continue
    fi
    if [[ -n "$ONLY_LIST" ]] && ! matches_csv "$base" "$ONLY_LIST"; then
        continue
    fi
    if [[ -n "$SKIP_LIST" ]] && matches_csv "$base" "$SKIP_LIST"; then
        echo "⏭️  Skipping $base (--skip)"
        continue
    fi
    SELECTED+=("$st")
done

if [[ ${#SELECTED[@]} -eq 0 ]]; then
    echo "⚠️  No stages matched your filter (--only/--skip/--from)."
    exit 0
fi

PASSED_STAGES=()
FAILED_STAGES=()
FAILED_STAGE_NUMS=()

for st in "${SELECTED[@]}"; do
    base="$(basename "$st")"
    num="${base%%-*}"
    emit_stage_event "$base" "start" 0 ""
    rc=0
    bash "$st" || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        PASSED_STAGES+=("$base")
        emit_stage_event "$base" "passed" 0 ""
    else
        FAILED_STAGES+=("${base} (exit ${rc})")
        FAILED_STAGE_NUMS+=("$num")
        emit_stage_event "$base" "failed" "$rc" "stage exited nonzero"
        if [[ "$num" == "00" ]]; then
            echo "❌ Pre-flight security gate (${base}) failed (exit ${rc}) — aborting install." >&2
            exit "$rc"
        fi
        if ! is_true "$CONTINUE_ON_ERROR"; then
            echo "❌ Stage ${base} failed (exit ${rc}) — aborting immediately (--fail-fast)." >&2
            exit "$rc"
        fi
        echo "" >&2
        echo "⚠️  Stage ${base} exited with code ${rc} — safeguarding & continuing to next stage..." >&2
        echo "" >&2
    fi
done

if [[ ${#FAILED_STAGES[@]} -gt 0 ]]; then
    RETRY_CSV="$(IFS=,; echo "${FAILED_STAGE_NUMS[*]}")"
    echo ""
    echo "================================================================"
    echo "  ⚠️  macubuntu PIPELINE FINISHED WITH STAGE FAILURES"
    echo "  Passed stages (${#PASSED_STAGES[@]}): ${PASSED_STAGES[*]:-(none)}"
    echo "  Failed stages (${#FAILED_STAGES[@]}): ${FAILED_STAGES[*]}"
    echo "  Retry failed stages only: ./install.sh --only ${RETRY_CSV}"
    echo "================================================================"
    exit 1
fi

# Success banner when all selected stages pass
echo ""
echo "================================================================"
echo "  ✅ macubuntu PIPELINE COMPLETE — all ${#PASSED_STAGES[@]} stage(s) passed"
echo "  Passed stages: ${PASSED_STAGES[*]}"
echo "================================================================"
exit 0
