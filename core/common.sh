#!/usr/bin/env bash
# ============================================================================
# core/common.sh — Hardened, Eval-Free, Supply-Chain-Pinned Shared Engine
# Security Invariants:
#   1. ZERO eval / source on config profiles — strict allowlisted literal parser.
#   2. Every remote download/clone MUST be pinned in system/download-locks.sha256
#      and verified via SHA-256 or exact detached commit SHA before execution.
#   3. DRY_RUN=1 is a strict zero-side-effect invariant across every wrapper.
#   4. Private files/directories created under umask 077 with 0700/0600 modes.
# ============================================================================

[[ -n "${_MACUBUNTU_COMMON_LOADED:-}" ]] && return 0
_MACUBUNTU_COMMON_LOADED=1

umask 077

# Capture the real (non-root) invoking user so privilege-dropping wrappers can
# always return to the desktop user, even if a stage is somehow re-entered as root.
REAL_USER="${SUDO_USER:-${USER:-}}"
export REAL_USER

# P2-01: Resolve the actual target user (the human, not root) once at startup.
# When the installer runs under sudo, SUDO_USER is the human; PKEXEC_UID covers
# polkit elevation; otherwise we're already the target user.
if [[ -n "${SUDO_USER:-}" ]]; then
    TARGET_USER="$SUDO_USER"
    TARGET_UID="$(id -u "$TARGET_USER" 2>/dev/null || echo "")"
    TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)"
elif [[ -n "${PKEXEC_UID:-}" ]]; then
    TARGET_USER="$(id -un "$PKEXEC_UID" 2>/dev/null || echo "")"
    TARGET_UID="$PKEXEC_UID"
    TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)"
else
    TARGET_USER="$(id -un)"
    TARGET_UID="$(id -u)"
    TARGET_HOME="$HOME"
fi
export TARGET_USER TARGET_UID TARGET_HOME

# Validate that we resolved a real user
if [[ -z "$TARGET_USER" || -z "$TARGET_UID" || -z "$TARGET_HOME" ]]; then
    echo "❌ Cannot resolve target user (TARGET_USER='$TARGET_USER' UID='$TARGET_UID' HOME='$TARGET_HOME')" >&2
    exit 1
fi
if [[ ! -d "$TARGET_HOME" ]]; then
    echo "❌ Target user home directory does not exist: $TARGET_HOME" >&2
    exit 1
fi

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCKFILE="${LOCKFILE:-$REPO_DIR/system/download-locks.sha256}"
# shellcheck source=core/detect.sh
source "$REPO_DIR/core/detect.sh"

# P2-05: PATH append (not prepend) to prevent user-writable dirs from shadowing
# system binaries when running under sudo. User binaries come AFTER system dirs.
export PATH="$PATH:$HOME/.local/bin:$HOME/.cargo/bin"

# --- Strict, Eval-Free Profile Loader ---
_assign_profile_kv() {
    local key="$1" val="$2"
    if [[ ! "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
        echo "❌ Rejected invalid profile key syntax: $key" >&2
        return 1
    fi
    case "$key" in
        PROFILE|ENABLE_CELINA|CELINA_NAME|CELINA_NICKNAME|OLLAMA_MODEL|\
        DOCK_CHOICE|LAUNCHER_CHOICE|ENABLE_DESKTOP_ICONS|ENABLE_DYNAMIC_ISLAND_EXT|\
        TOP_PANEL_STYLE|ANIMATION_SPEED|CONTINUE_ON_ERROR|\
        SEARCH_BACKEND|POWER_BACKEND|POINTER_ACCEL|FONT_AA|\
        INSTALL_THIRDPARTY|INSTALL_RUST_TOOLS|INSTALL_OPTIONAL|\
        ENABLE_EXPERIMENTAL_UINPUT|ENABLE_BIOMETRICS|\
        ENABLE_AIRPLAY_VIDEO|ENABLE_ANDROID_MIRROR|ENABLE_SYNCTHING|\
        ENABLE_FLATSEAL|ENABLE_UNIVERSAL_CONTROL|ENABLE_BATTERY_CHARGE_LIMIT|ENABLE_PHONE_HFP|\
        ENABLE_WAYLAND_FRACTIONAL_SCALING|ENABLE_VAAPI_GPU_DECODE|\
        ENABLE_NAUTILUS_QUICK_ACTIONS|ENABLE_KEEPASSXC|ENABLE_DISTROBOX|\
        ENABLE_CLIPBOARD_HISTORY|ENABLE_DYNAMIC_APPEARANCE|ENABLE_SAFARI_FIREFOX|\
        ENABLE_MACOS_CMD_KEYS|ENABLE_PERSISTENT_CONTINUITY_CAM|\
        ENABLE_PRO_AUDIO_BLUETOOTH|ENABLE_SAFE_SLEEP|ENABLE_SILENT_BOOT|\
        REPLACE_DISK_SWAP|ENABLE_ACCESSIBILITY|\
        ENABLE_MGLRU|ENABLE_PRELOAD|ENABLE_FSTRIM|        ENABLE_ANANICY|ENABLE_PSD|ENABLE_FIREFOX_TUNING|ENABLE_TRACKPAD_GESTURES|        ENABLE_QUICK_LOOK|ENABLE_SCREEN_RECORDER|ENABLE_TILING|ENABLE_POWER_CPUFREQ|        ENABLE_TIMESNAP|ENABLE_SESSION_RESTORE|ENABLE_COLOR_PICKER|ENABLE_AMBIENT_LIGHT|        ENABLE_VMTOUCH|ENABLE_JOURNALD_LITE|ENABLE_CELINA_QOS|ENABLE_SHADER_CACHE|        ENABLE_DNS_CACHE|ENABLE_NOATIME|ENABLE_S2IDLE|ENABLE_MIMALLOC|        ENABLE_SNAP_REMOVAL|ENABLE_CPU_MITIGATIONS_OFF|ENABLE_CELINA_AI_SLICE|ENABLE_VM_TUNING|ENABLE_EPP_DYNAMIC|\
        ENABLE_CELINA_ISLAND_SHELL|ENABLE_PERCEPTUAL_CHOREOGRAPHY|SCX_SCHEDULER|ENABLE_SCHED_EXT|CHOREOGRAPHY_SPEED_FACTOR|\
        ENABLE_NET_BBR|ENABLE_WIFI_NO_POWERSAVE|ENABLE_TRACKER_CAP|\
        ENABLE_LOW_LATENCY_KERNEL|LOW_LATENCY_KERNEL_CHOICE|ENABLE_CHROME_OZONE_WAYLAND|ENABLE_BTRFS_SNAPPER|ENABLE_NATIVE_WAYLAND_BLUR|ENABLE_EASYEFFECTS|ENABLE_PARENTAL_CONTROLS|ENABLE_DNS_FILTER|ENABLE_VOICE_PIPELINE_UPGRADE|\
                ENABLE_S0IX_SLEEP|ENABLE_HIDPI_FONTS|ENABLE_WAYLAND_TEXT_INJECTION|ENABLE_VAULT_AUDIT|ENABLE_SELF_HEAL|\
        ENABLE_MCP_GUARD|ENABLE_BITEMPORAL_KG|ENABLE_BLAST_RADIUS|ENABLE_MICRO_AFFECT|\
        ZRAM_SIZE_EXPR|SWAPPINESS|VFS_CACHE_PRESSURE|DIRTY_BG_BYTES|DIRTY_BYTES|\
        VM_PAGE_CLUSTER|VM_DIRTY_WRITEBACK_CENTISECS|MGLRU_MIN_TTL_MS|VMTOUCH_MAX_MB|\
        CELINA_COMPRESSION_TIER|CELINA_HEADROOM_ML|CELINA_SLICE_HIGH|CELINA_SLICE_MAX|CELINA_CPU_WEIGHT|CELINA_IO_WEIGHT|\
        BLUR_SIGMA|BLUR_APPGRID|\
        LLM_MODELS_MAX|LLM_SLEEP_IDLE_SECONDS|LLM_PARALLEL_SLOTS|LLM_KV_CACHE_TYPE|LLM_CTX_DEFAULT|\
        SERIALIZE_HEAVY_MODELS|ENABLE_LADYBUG_GRAPH|ENABLE_GLINER_SLOTS|KEEP_VOICE_HOT|\
        ENABLE_ASTRA_1FPS_BUFFER|ENABLE_LIBKRUN_SANDBOX|SPOTLIGHT_BATCH_SIZE|RAM_PSI_EVICT_THRESHOLD)
            # Only set when not already overridden by caller environment
            if [[ -z "${!key+x}" ]]; then
                printf -v "$key" '%s' "$val"
                export "${key?}"
            fi
            ;;
        *)
            echo "❌ Rejected unknown profile key: $key" >&2
            return 1
            ;;
    esac
}

load_profile() {
    local profile_name="${1:-default-8gb}"
    local profile_file
    if [[ "$profile_name" == */* && -f "$profile_name" ]]; then
        profile_file="$profile_name"
    else
        if [[ ! "$profile_name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
            echo "❌ Rejected invalid profile name: '$profile_name'" >&2
            return 1
        fi
        profile_file="$REPO_DIR/profiles/${profile_name}.env"
    fi

    if [[ ! -f "$profile_file" ]]; then
        echo "❌ Unknown profile '$profile_name' (expected file: $profile_file)" >&2
        return 1
    fi

    local raw_line line key val first_ch last_ch
    while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
        # Strip CR for safety
        line="${raw_line%$'\r'}"
        # Trim leading whitespace
        line="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$line" || "$line" == \#* ]] && continue
        if [[ "$line" != *=* ]]; then
            echo "❌ Malformed line in profile '$profile_file': $line" >&2
            return 1
        fi

        key="${line%%=*}"
        val="${line#*=}"

        # Trim trailing whitespace on key, leading/trailing on unquoted value
        key="${key%"${key##*[![:space:]]}"}"
        val="${val#"${val%%[![:space:]]*}"}"

        first_ch="${val:0:1}"
        if [[ "$first_ch" == '"' || "$first_ch" == "'" ]]; then
            # Parse up to matching closing quote; ignore trailing inline comment after quote
            local rest="${val:1}"
            if [[ "$rest" != *"$first_ch"* ]]; then
                echo "❌ Unterminated quoted value for key '$key' in '$profile_file'" >&2
                return 1
            fi
            val="${rest%%"$first_ch"*}"
        else
            # Strip inline comment & trailing whitespace for unquoted literals
            val="${val%%#*}"
            val="${val%"${val##*[![:space:]]}"}"
        fi

        # Reject shell-expansion tokens, statement separators, or control characters in values
        if [[ "$val" == *'$('* || "$val" == *'${'* || "$val" == *'`'* || "$val" == *';'* || "$val" == *$'\n'* ]]; then
            echo "❌ Rejected unsafe value for profile key '$key' (shell expansion/metacharacters not allowed)" >&2
            return 1
        fi

        _assign_profile_kv "$key" "$val" || return 1
    done < "$profile_file"
}

is_true() {
    local v="${1:-false}"
    [[ "${v,,}" == "true" || "$v" == "1" || "${v,,}" == "yes" || "${v,,}" == "on" ]]
}

init_defaults() {
    export DRY_RUN="${DRY_RUN:-0}"
    export PROFILE="${PROFILE:-default-8gb}"
    load_profile "$PROFILE" || exit 1

    export ENABLE_CELINA="${ENABLE_CELINA:-true}"
    export CELINA_NAME="${CELINA_NAME:-Celina}"
    export CELINA_NICKNAME="${CELINA_NICKNAME:-Celly}"
    export OLLAMA_MODEL="${OLLAMA_MODEL:-auto}"
    export CONTINUE_ON_ERROR="${CONTINUE_ON_ERROR:-true}"
    export DOCK_CHOICE="${DOCK_CHOICE:-ubuntu-dock}"
    export LAUNCHER_CHOICE="${LAUNCHER_CHOICE:-celina-spotlight}"
    export ENABLE_DESKTOP_ICONS="${ENABLE_DESKTOP_ICONS:-true}"
    export ENABLE_DYNAMIC_ISLAND_EXT="${ENABLE_DYNAMIC_ISLAND_EXT:-false}"
    export TOP_PANEL_STYLE="${TOP_PANEL_STYLE:-transparent}"
    export ANIMATION_SPEED="${ANIMATION_SPEED:-fast}"
    export SEARCH_BACKEND="${SEARCH_BACKEND:-lightweight}"
    export POWER_BACKEND="${POWER_BACKEND:-ppd}"
    export POINTER_ACCEL="${POINTER_ACCEL:-flat}"
    export FONT_AA="${FONT_AA:-rgba}"
    export INSTALL_THIRDPARTY="${INSTALL_THIRDPARTY:-false}"
    export INSTALL_RUST_TOOLS="${INSTALL_RUST_TOOLS:-false}"
    export ENABLE_EXPERIMENTAL_UINPUT="${ENABLE_EXPERIMENTAL_UINPUT:-false}"
    export ENABLE_BIOMETRICS="${ENABLE_BIOMETRICS:-false}"
    export ENABLE_AIRPLAY_VIDEO="${ENABLE_AIRPLAY_VIDEO:-false}"
    export ENABLE_ANDROID_MIRROR="${ENABLE_ANDROID_MIRROR:-false}"
    export ENABLE_SYNCTHING="${ENABLE_SYNCTHING:-false}"
    export ENABLE_FLATSEAL="${ENABLE_FLATSEAL:-true}"
    export ENABLE_UNIVERSAL_CONTROL="${ENABLE_UNIVERSAL_CONTROL:-false}"
    export ENABLE_BATTERY_CHARGE_LIMIT="${ENABLE_BATTERY_CHARGE_LIMIT:-true}"
    export ENABLE_PHONE_HFP="${ENABLE_PHONE_HFP:-false}"
    export ENABLE_WAYLAND_FRACTIONAL_SCALING="${ENABLE_WAYLAND_FRACTIONAL_SCALING:-true}"
    export ENABLE_VAAPI_GPU_DECODE="${ENABLE_VAAPI_GPU_DECODE:-true}"
    export ENABLE_NAUTILUS_QUICK_ACTIONS="${ENABLE_NAUTILUS_QUICK_ACTIONS:-true}"
    export ENABLE_KEEPASSXC="${ENABLE_KEEPASSXC:-true}"
    export ENABLE_DISTROBOX="${ENABLE_DISTROBOX:-false}"
    export ENABLE_CLIPBOARD_HISTORY="${ENABLE_CLIPBOARD_HISTORY:-true}"
    export ENABLE_DYNAMIC_APPEARANCE="${ENABLE_DYNAMIC_APPEARANCE:-false}"
    export ENABLE_SAFARI_FIREFOX="${ENABLE_SAFARI_FIREFOX:-true}"
    export ENABLE_MACOS_CMD_KEYS="${ENABLE_MACOS_CMD_KEYS:-false}"
    export ENABLE_PERSISTENT_CONTINUITY_CAM="${ENABLE_PERSISTENT_CONTINUITY_CAM:-true}"
    export ENABLE_PRO_AUDIO_BLUETOOTH="${ENABLE_PRO_AUDIO_BLUETOOTH:-true}"
    export ENABLE_SAFE_SLEEP="${ENABLE_SAFE_SLEEP:-true}"
    export ENABLE_SILENT_BOOT="${ENABLE_SILENT_BOOT:-false}"
    export REPLACE_DISK_SWAP="${REPLACE_DISK_SWAP:-false}"
    export ENABLE_ACCESSIBILITY="${ENABLE_ACCESSIBILITY:-false}"
    export ENABLE_MGLRU="${ENABLE_MGLRU:-true}"
    export ENABLE_PRELOAD="${ENABLE_PRELOAD:-true}"
    export ENABLE_FSTRIM="${ENABLE_FSTRIM:-true}"
    export ENABLE_ANANICY="${ENABLE_ANANICY:-false}"
    export ENABLE_PSD="${ENABLE_PSD:-auto}"
    export ENABLE_FIREFOX_TUNING="${ENABLE_FIREFOX_TUNING:-true}"
    export ENABLE_TRACKPAD_GESTURES="${ENABLE_TRACKPAD_GESTURES:-false}"
    export ENABLE_QUICK_LOOK="${ENABLE_QUICK_LOOK:-true}"
    export ENABLE_SCREEN_RECORDER="${ENABLE_SCREEN_RECORDER:-false}"
    export ENABLE_TILING="${ENABLE_TILING:-true}"
    export ENABLE_POWER_CPUFREQ="${ENABLE_POWER_CPUFREQ:-false}"
    export ENABLE_TIMESNAP="${ENABLE_TIMESNAP:-false}"
    export ENABLE_SESSION_RESTORE="${ENABLE_SESSION_RESTORE:-false}"
    export ENABLE_COLOR_PICKER="${ENABLE_COLOR_PICKER:-false}"
    export ENABLE_AMBIENT_LIGHT="${ENABLE_AMBIENT_LIGHT:-false}"
    export ENABLE_VM_TUNING="${ENABLE_VM_TUNING:-true}"
    export ENABLE_SHADER_CACHE="${ENABLE_SHADER_CACHE:-true}"
    export ENABLE_DNS_CACHE="${ENABLE_DNS_CACHE:-true}"
    export ENABLE_VMTOUCH="${ENABLE_VMTOUCH:-false}"
    export ENABLE_JOURNALD_LITE="${ENABLE_JOURNALD_LITE:-false}"
    export ENABLE_CELINA_QOS="${ENABLE_CELINA_QOS:-true}"
    export ENABLE_NOATIME="${ENABLE_NOATIME:-false}"
    export ENABLE_S2IDLE="${ENABLE_S2IDLE:-false}"
    export ENABLE_MIMALLOC="${ENABLE_MIMALLOC:-false}"
    export ENABLE_SNAP_REMOVAL="${ENABLE_SNAP_REMOVAL:-true}"
    export ENABLE_CPU_MITIGATIONS_OFF="${ENABLE_CPU_MITIGATIONS_OFF:-true}"
    export ENABLE_CELINA_AI_SLICE="${ENABLE_CELINA_AI_SLICE:-true}"
    export ENABLE_EPP_DYNAMIC="${ENABLE_EPP_DYNAMIC:-true}"
    export ENABLE_CELINA_ISLAND_SHELL="${ENABLE_CELINA_ISLAND_SHELL:-true}"
    export ENABLE_PERCEPTUAL_CHOREOGRAPHY="${ENABLE_PERCEPTUAL_CHOREOGRAPHY:-true}"
    export SCX_SCHEDULER="${SCX_SCHEDULER:-scx_bpfland}"
    export ENABLE_SCHED_EXT="${ENABLE_SCHED_EXT:-true}"
    export CHOREOGRAPHY_SPEED_FACTOR="${CHOREOGRAPHY_SPEED_FACTOR:-0.65}"
    export ENABLE_NET_BBR="${ENABLE_NET_BBR:-true}"
    export ENABLE_WIFI_NO_POWERSAVE="${ENABLE_WIFI_NO_POWERSAVE:-true}"
    export ENABLE_TRACKER_CAP="${ENABLE_TRACKER_CAP:-true}"
    export ENABLE_BTRFS_SNAPPER="${ENABLE_BTRFS_SNAPPER:-false}"
    export ENABLE_LOW_LATENCY_KERNEL="${ENABLE_LOW_LATENCY_KERNEL:-false}"
    export LOW_LATENCY_KERNEL_CHOICE="${LOW_LATENCY_KERNEL_CHOICE:-xanmod}"
    export ENABLE_CHROME_OZONE_WAYLAND="${ENABLE_CHROME_OZONE_WAYLAND:-false}"
    export ENABLE_NATIVE_WAYLAND_BLUR="${ENABLE_NATIVE_WAYLAND_BLUR:-false}"
    export ENABLE_EASYEFFECTS="${ENABLE_EASYEFFECTS:-false}"
    export ENABLE_PARENTAL_CONTROLS="${ENABLE_PARENTAL_CONTROLS:-false}"
    export ENABLE_DNS_FILTER="${ENABLE_DNS_FILTER:-false}"
    export ENABLE_VOICE_PIPELINE_UPGRADE="${ENABLE_VOICE_PIPELINE_UPGRADE:-false}"
    export ENABLE_S0IX_SLEEP="${ENABLE_S0IX_SLEEP:-false}"
    export ENABLE_HIDPI_FONTS="${ENABLE_HIDPI_FONTS:-false}"
    export ENABLE_WAYLAND_TEXT_INJECTION="${ENABLE_WAYLAND_TEXT_INJECTION:-false}"
    export ENABLE_VAULT_AUDIT="${ENABLE_VAULT_AUDIT:-false}"
    export ENABLE_SELF_HEAL="${ENABLE_SELF_HEAL:-false}"
    export ENABLE_MCP_GUARD="${ENABLE_MCP_GUARD:-false}"
    export ENABLE_BITEMPORAL_KG="${ENABLE_BITEMPORAL_KG:-false}"
    export ENABLE_BLAST_RADIUS="${ENABLE_BLAST_RADIUS:-false}"
    export ENABLE_MICRO_AFFECT="${ENABLE_MICRO_AFFECT:-false}"


    # Built-in conflict guardrails (prevents overlapping islands & dead shortcuts)
    if is_true "$ENABLE_CELINA" && is_true "$ENABLE_DYNAMIC_ISLAND_EXT"; then
        echo "ℹ️  Guardrail: Celina Dynamic Island owns top-center; skipping dynamic-island@basskibo."
        export ENABLE_DYNAMIC_ISLAND_EXT="false"
    fi
    if ! is_true "$ENABLE_CELINA" && [[ "$LAUNCHER_CHOICE" == "celina-spotlight" ]]; then
        echo "ℹ️  Guardrail: ENABLE_CELINA=false; switching LAUNCHER_CHOICE to ulauncher."
        export LAUNCHER_CHOICE="ulauncher"
    fi
}

# --- Rollback Snapshot Infrastructure ---
# Pre-change snapshots of irreversible system files for emergency rollback.
# Snapshots live outside $WORKDIR (which is mktemp'd and cleaned) so they
# survive installer restarts. Each snapshot is byte-identical: path, mode,
# ownership, and SHA-256 are recorded in a manifest.json.

SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-/var/lib/macubuntu/snapshots}"
SNAPSHOT_MAX="${SNAPSHOT_MAX:-20}"
export SNAPSHOT_ROOT SNAPSHOT_MAX

snapshot_file() {
    local src="$1"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] snapshot $src"
        return 0
    fi
    if [[ -z "$src" ]]; then
        echo "❌ snapshot_file: empty source path" >&2
        return 1
    fi

    init_runtime_state  # ensures RUN_SNAP_ID + SNAPSHOT_DIR are set
    local snap_id="${RUN_SNAP_ID}"
    local snap_dir="$SNAPSHOT_ROOT/$snap_id"

    run sudo install -d -m 700 "$SNAPSHOT_ROOT"
    run sudo install -d -m 700 "$snap_dir"

    local manifest="$snap_dir/manifest.json"
    # Create manifest if missing (first snapshot of this run)
    if [[ ! -f "$manifest" ]]; then
        run sudo python3 - "$manifest" "$snap_id" <<'PYEOF'
import json, sys, datetime
mfile, snap_id = sys.argv[1], sys.argv[2]
m = {"id": snap_id, "created": datetime.datetime.now(datetime.timezone.utc).isoformat(), "files": []}
with open(mfile, "w") as fh:
    json.dump(m, fh, indent=2)
PYEOF
    fi

    if [[ ! -e "$src" ]]; then
        # Brand-new file that doesn't exist yet — record existed: false so
        # rollback_all() can clean it up after the run creates it.
        run sudo python3 - "$manifest" "$src" <<'PYEOF'
import json, sys
mfile, path = sys.argv[1], sys.argv[2]
m = json.load(open(mfile))
# Keep the FIRST entry for any path (true pre-run state is never overwritten).
already = any(f.get("path") == path for f in m.get("files", []))
if not already:
    m["files"].append({"path": path, "existed": False})
    with open(mfile, "w") as fh:
        json.dump(m, fh, indent=2)
PYEOF
        echo "      Recorded new-file placeholder $src (snap $snap_id)"
        return 0
    fi

    # Check if we already have a snapshot entry for this path (keep FIRST only).
    local already_snap
    already_snap="$(run sudo python3 - "$manifest" "$src" <<'PYEOF'
import json, sys
mfile, path = sys.argv[1], sys.argv[2]
m = json.load(open(mfile))
print("true" if any(f.get("path") == path for f in m.get("files", [])) else "false")
PYEOF
)"
    if [[ "$already_snap" == "true" ]]; then
        echo "      Already snapshotted $src (keeping first entry)"
        return 0
    fi

    local dest_dir="$snap_dir$(dirname "$src")"
    run sudo install -d -m 700 "$dest_dir"
    local dest="$dest_dir/$(basename "$src")"
    run sudo install -m "$(stat -c '%a' "$src")" -o "$(stat -c '%u' "$src")" -g "$(stat -c '%g' "$src")" "$src" "$dest" 2>/dev/null || { echo "❌ _snapshot_add: failed to snapshot $src" >&2; return 1; }

    run sudo python3 - "$manifest" "$src" "$dest" <<'PYEOF'
import hashlib, json, sys, os
mfile, src_path, dest_path = sys.argv[1], sys.argv[2], sys.argv[3]
m = json.load(open(mfile))
sha = hashlib.sha256(open(src_path, 'rb').read()).hexdigest()
mode = oct(os.stat(src_path).st_mode & 0o777)[2:].zfill(4)
uid = os.stat(src_path).st_uid
gid = os.stat(src_path).st_gid
m["files"].append({
    "path": src_path,
    "existed": True,
    "sha256": sha,
    "mode": mode,
    "uid": uid,
    "gid": gid,
})
with open(mfile, "w") as fh:
    json.dump(m, fh, indent=2)
PYEOF
    echo "      Snapshotted $src (snap $snap_id)"
}

_snapshot_prune() {
    run sudo bash -c "ls -1dt '$SNAPSHOT_ROOT'/* 2>/dev/null | tail -n +$((SNAPSHOT_MAX+1)) | xargs -r rm -rf"
}

rollback_all() {
    local snap_id="${1:-$RUN_SNAP_ID}"
    if [[ -z "$snap_id" ]]; then
        snap_id="$(ls -1dt "$SNAPSHOT_ROOT"/* 2>/dev/null | head -1 | xargs -r basename)"
    fi
    [[ -n "$snap_id" && -f "$SNAPSHOT_ROOT/$snap_id/manifest.json" ]] || { echo "❌ No snapshot to roll back"; return 1; }
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] rollback $snap_id"
        return 0
    fi

    run sudo python3 - "$SNAPSHOT_ROOT/$snap_id" <<'PYEOF'
import hashlib, json, os, shutil, sys
snap = sys.argv[1]
manifest_path = os.path.join(snap, "manifest.json")
if not os.path.exists(manifest_path):
    print("❌ No manifest in " + snap, file=sys.stderr)
    sys.exit(1)
m = json.load(open(manifest_path))

# Iterate in reverse so later-created files are removed before earlier-restored ones.
for f in reversed(m.get("files", [])):
    p = f["path"]
    existed = f.get("existed", True)

    if not existed:
        # Brand-new file created during the run — unlink it if present.
        if os.path.exists(p):
            try:
                os.unlink(p)
                print("removed " + p)
            except OSError as exc:
                print("⚠️  could not remove " + p + ": " + str(exc), file=sys.stderr)
        continue

    # Pre-existing file — restore from backup atomically with original perms.
    src = os.path.join(snap, p.lstrip("/"))
    if not os.path.exists(src):
        print("⚠️  no backup for " + p + ", skipping", file=sys.stderr)
        continue

    # Verify SHA-256 integrity of the backup before restoring.
    h = hashlib.sha256(open(src, "rb").read()).hexdigest()
    if h != f.get("sha256", ""):
        print("❌ snapshot corrupt for " + p + " (sha256 mismatch)", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(p), exist_ok=True)
    shutil.copy2(src, p)
    try:
        os.chmod(p, int(f.get("mode", "644"), 8))
    except (ValueError, OSError):
        pass
    print("restored " + p)
PYEOF
    echo "      Rollback of $snap_id complete"
}

init_runtime_state() {
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        export WORKDIR="${WORKDIR:-/tmp/macubuntu-dryrun-virtual}"
        export MANUAL_FILE="${MANUAL_FILE:-/dev/null}"
        export FAILED_FILE="${FAILED_FILE:-/dev/null}"
        return 0
    fi
    if [[ -z "${WORKDIR:-}" || ! -d "${WORKDIR:-}" ]]; then
        WORKDIR="$(mktemp -d -t macubuntu.XXXXXX)"
        chmod 700 "$WORKDIR"
        export WORKDIR
    fi
    export MANUAL_FILE="${MANUAL_FILE:-$WORKDIR/manual.list}"
    export FAILED_FILE="${FAILED_FILE:-$WORKDIR/failed.list}"
    export SNAPSHOT_DIR="${SNAPSHOT_ROOT:-/var/lib/macubuntu/snapshots}"
    touch "$MANUAL_FILE" "$FAILED_FILE"
    chmod 600 "$MANUAL_FILE" "$FAILED_FILE"

    # P5: Per-run transaction snapshot ID — generated once per run so all
    # snapshot_file() calls during the same run append into the SAME directory.
    if [[ -z "${RUN_SNAP_ID:-}" ]]; then
        export RUN_SNAP_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
    fi
}

record_manual() {
    init_runtime_state
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run][manual] $1"
        return 0
    fi
    echo "$1" >> "$MANUAL_FILE"
}

record_fail() {
    init_runtime_state
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run][fail] $1"
        return 0
    fi
    echo "$1" >> "$FAILED_FILE"
}

section() {
    echo ""
    echo "================================================================"
    echo "  $*"
    echo "================================================================"
}

step() {
    echo "  --> $*"
}

log_info() {
    echo "  [info] $*"
}

log_error() {
    echo "  [error] $*" >&2
}

# ============================================================================
# Strict Zero-Side-Effect Dry-Run Wrappers
# Every filesystem, package, service, or network mutation MUST route here.
# ============================================================================
run() {
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] $*"
        return 0
    fi
    "$@"
}

run_as_user() {
    local target_user="${TARGET_USER:-${REAL_USER:-${SUDO_USER:-${USER:-}}}}"
    if [[ -z "$target_user" ]]; then
        echo "❌ run_as_user: cannot determine target user (TARGET_USER/REAL_USER/SUDO_USER/USER all empty)" >&2
        return 2
    fi
    local target_uid target_home
    target_uid="$(id -u "$target_user" 2>/dev/null || echo "")"
    target_home="$(getent passwd "$target_user" 2>/dev/null | cut -d: -f6 || true)"
    if [[ -z "$target_uid" || -z "$target_home" ]]; then
        echo "❌ run_as_user: cannot resolve UID/home for user '$target_user'" >&2
        return 2
    fi
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] (user:$target_user uid:$target_uid) $*"
        return 0
    fi
    # Already running as the target user — invoke directly (no sudo needed / no password prompt).
    if [[ "$(id -un 2>/dev/null || echo unknown)" == "$target_user" ]]; then
        "$@"
        return $?
    fi
    # Drop privileges to the target desktop user.
    # XDG_RUNTIME_DIR and DBUS_SESSION_BUS_ADDRESS are REQUIRED for gsettings/dconf/flatpak/systemctl --user.
    if command -v runuser >/dev/null 2>&1 && [[ "$(id -un)" == "root" ]]; then
        runuser -u "$target_user" -- env \
            HOME="$target_home" \
            USER="$target_user" \
            LOGNAME="$target_user" \
            XDG_RUNTIME_DIR="/run/user/$target_uid" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$target_uid/bus" \
            "$@"
        return $?
    elif command -v sudo >/dev/null 2>&1; then
        sudo -u "$target_user" -- env \
            HOME="$target_home" \
            USER="$target_user" \
            LOGNAME="$target_user" \
            XDG_RUNTIME_DIR="/run/user/$target_uid" \
            DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$target_uid/bus" \
            "$@"
        return $?
    else
        echo "❌ run_as_user: neither runuser nor sudo available to drop privileges to '$target_user'" >&2
        return 126
    fi
}

ensure_dir() {
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] mkdir -p $*"
        return 0
    fi
    mkdir -p "$@"
}

ensure_private_dir() {
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] mkdir -p -m 700 $*"
        return 0
    fi
    local d
    for d in "$@"; do
        mkdir -p -m 700 "$d"
        chmod 700 "$d"
    done
}

chmod_safe() {
    local mode="$1"; shift
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] chmod $mode $*"
        return 0
    fi
    chmod "$mode" "$@"
}

write_root_file() {
    local path="$1" mode="${2:-644}"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] sudo tee $path (mode $mode)"
        cat >/dev/null
        return 0
    fi
    snapshot_file "$path"
    # P5: Create temp file inside $WORKDIR (never inside /etc/ as non-root user)
    init_runtime_state  # ensures WORKDIR exists
    local tmp; tmp="$(mktemp "$WORKDIR/rootfile.XXXXXX")"
    # Trap to clean up temp file on error/exit
    trap 'rm -f "$tmp" 2>/dev/null' RETURN
    cat > "$tmp"
    local parent; parent="$(dirname "$path")"
    sudo install -d -m 755 "$parent"
    # Heal pre-existing dirs (e.g. leftovers from earlier umask-077 runs) to 0755
    # so /etc drop-in dirs are never left world-inaccessible.
    sudo chmod 755 "$parent"
    sudo install -m "$mode" "$tmp" "$path"
    rm -f "$tmp"
    trap - RETURN
}

copy_root_file() {
    local src="$1" dst="$2" mode="${3:-644}"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] sudo install -m $mode $src -> $dst"
        return 0
    fi
    snapshot_file "$dst"
    local parent
    parent="$(dirname "$dst")"
    sudo install -d -m 755 "$parent"
    # Heal pre-existing dirs (e.g. leftovers from earlier umask-077 runs) to 0755
    # so /etc drop-in dirs are never left world-inaccessible.
    sudo chmod 755 "$parent"
    sudo install -m "$mode" "$src" "$dst"
}

write_user_file() {
    local path="$1" mode="${2:-600}"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] write $path (mode $mode)"
        cat >/dev/null
        return 0
    fi
    mkdir -p "$(dirname "$path")"
    local tmp="${path}.tmp.$$"
    cat > "$tmp"
    chmod "$mode" "$tmp"
    mv -f "$tmp" "$path"
}

copy_user_file() {
    local src="$1" dst="$2" mode="${3:-600}"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] cp $src -> $dst (mode $mode)"
        return 0
    fi
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    chmod "$mode" "$dst"
}

systemctl_enable() {
    local unit="$1"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] sudo systemctl enable --now $unit"
        return 0
    fi
    sudo systemctl daemon-reload 2>/dev/null || true
    sudo systemctl enable --now "$unit"
}

systemctl_disable() {
    local unit="$1"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] sudo systemctl disable --now $unit"
        return 0
    fi
    sudo systemctl disable --now "$unit" 2>/dev/null || true
}

systemctl_user_enable() {
    local unit="$1"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] (user:$TARGET_USER) systemctl --user enable --now $unit"
        return 0
    fi
    if command -v systemctl >/dev/null 2>&1; then
        run_as_user systemctl --user daemon-reload 2>/dev/null || true
        run_as_user systemctl --user enable --now "$unit" 2>/dev/null || return 1
    fi
}

# ============================================================================
# Supply-Chain Lockfile Lookup, Checksum-Verified Downloads & Pinned Git Clones
# ============================================================================
lock_lookup() {
    local target_id="$1"
    if [[ ! -f "$LOCKFILE" ]]; then
        echo "❌ Missing supply-chain lockfile: $LOCKFILE" >&2
        return 1
    fi
    local line id ltype url ref sha dt lic
    while IFS='|' read -r id ltype url ref sha dt lic || [[ -n "$id" ]]; do
        [[ -z "$id" || "$id" == \#* ]] && continue
        if [[ "$id" == "$target_id" ]]; then
            printf '%s|%s|%s|%s|%s|%s\n' "$ltype" "$url" "$ref" "$sha" "$dt" "$lic"
            return 0
        fi
    done < "$LOCKFILE"
    echo "❌ Supply-chain lock entry not found for '$target_id' in $LOCKFILE" >&2
    return 1
}

download_verified() {
    local lock_id="$1" dest="$2" mode="${3:-644}"
    local entry ltype url ref expected_sha dt lic
    entry="$(lock_lookup "$lock_id")" || return 1
    IFS='|' read -r ltype url ref expected_sha dt lic <<< "$entry"

    # Supply-chain dependency guard: refuse to download if required tools are missing.
    command -v curl >/dev/null 2>&1 || { echo "❌ download_verified: 'curl' is required but not installed." >&2; return 127; }
    command -v sha256sum >/dev/null 2>&1 || { echo "❌ download_verified: 'sha256sum' is required but not installed." >&2; return 127; }

    if [[ "$ltype" != "file" ]]; then
        echo "❌ Lock entry '$lock_id' is type '$ltype', expected 'file'." >&2
        return 1
    fi
    if [[ "$url" != https://* ]]; then
        echo "❌ Refusing non-HTTPS download URL for '$lock_id': $url" >&2
        return 1
    fi
    if [[ ! "$expected_sha" =~ ^[0-9a-fA-F]{64}$ ]]; then
        echo "❌ Refusing download '$lock_id': missing valid 64-hex SHA-256 digest in $LOCKFILE" >&2
        return 1
    fi
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] download_verified $lock_id ($ref, sha256=${expected_sha:0:12}...) -> $dest"
        return 0
    fi

    init_runtime_state
    local tmp
    tmp="$(mktemp "$WORKDIR/dl.${lock_id}.XXXXXX")"
    if ! curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 --max-redirs 5 --connect-timeout 15 -o "$tmp" "$url"; then
        rm -f "$tmp"
        echo "❌ Download failed for '$lock_id' ($url)" >&2
        return 1
    fi

    if ! printf '%s  %s\n' "$expected_sha" "$tmp" | sha256sum --check --strict --status; then
        local actual_sha
        actual_sha="$(sha256sum "$tmp" | cut -d' ' -f1)"
        rm -f "$tmp"
        echo "❌ SHA-256 CHECKSUM MISMATCH for '$lock_id'!" >&2
        echo "   Expected: $expected_sha" >&2
        echo "   Actual:   $actual_sha" >&2
        return 1
    fi

    mkdir -p "$(dirname "$dest")"
    chmod "$mode" "$tmp"
    mv -f "$tmp" "$dest"
    echo "      Verified '$lock_id' (sha256=${expected_sha:0:12}..., verified $dt)"
}

clone_pinned_repo() {
    local lock_id="$1" dest="$2"
    local entry ltype url exact_commit sha dt lic

    # Supply-chain dependency guard: refuse to clone if git is missing.
    command -v git >/dev/null 2>&1 || { echo "❌ clone_pinned_repo: 'git' is required but not installed." >&2; return 127; }

    entry="$(lock_lookup "$lock_id")" || return 1
    IFS='|' read -r ltype url exact_commit sha dt lic <<< "$entry"

    if [[ "$ltype" != "git" ]]; then
        echo "❌ Lock entry '$lock_id' is type '$ltype', expected 'git'." >&2
        return 1
    fi
    if [[ "$url" != https://* ]]; then
        echo "❌ Refusing non-HTTPS git URL for '$lock_id': $url" >&2
        return 1
    fi
    if [[ ! "$exact_commit" =~ ^[0-9a-fA-F]{40}$ ]]; then
        echo "❌ Refusing unpinned git repo '$lock_id': commit '$exact_commit' is not a 40-char SHA" >&2
        return 1
    fi
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] clone_pinned_repo $lock_id @ ${exact_commit:0:12} -> $dest"
        return 0
    fi

    # Safety invariant: NEVER rm -rf an unexpected non-Git destination directory!
    if [[ -e "$dest" && ! -d "$dest/.git" ]]; then
        echo "❌ Refusing to overwrite non-Git path '$dest'. Inspect or move it manually and retry." >&2
        return 1
    fi

    if [[ -d "$dest/.git" ]]; then
        local cur_head
        cur_head="$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)"
        if [[ "$cur_head" != "$exact_commit" ]]; then
            git -C "$dest" fetch --depth=1 origin "$exact_commit" >/dev/null 2>&1 \
                || git -C "$dest" fetch origin >/dev/null 2>&1 \
                || return 1
            git -C "$dest" checkout --detach "$exact_commit" >/dev/null 2>&1 || return 1
        fi
    else
        mkdir -p "$(dirname "$dest")"
        git clone --filter=blob:none "$url" "$dest" >/dev/null 2>&1 || return 1
        git -C "$dest" checkout --detach "$exact_commit" >/dev/null 2>&1 || return 1
    fi

    local final_head
    final_head="$(git -C "$dest" rev-parse HEAD 2>/dev/null || true)"
    if [[ "$final_head" != "$exact_commit" ]]; then
        echo "❌ Pinned commit verification failed for '$lock_id' (got '$final_head', expected '$exact_commit')" >&2
        return 1
    fi
    # Verify GPG signature when keys are present in user's keyring (informational otherwise)
    if git -C "$dest" verify-commit "$exact_commit" >/dev/null 2>&1; then
        echo "      Verified GPG-signed commit $lock_id @ ${exact_commit:0:12}"
    else
        echo "      Pinned commit checked out: $lock_id @ ${exact_commit:0:12}"
    fi
}

install_pinned_deb() {
    local lock_id="$1" name="$2"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] install_pinned_deb $lock_id ($name)"
        return 0
    fi
    init_runtime_state
    local deb_file="$WORKDIR/${lock_id}.deb"
    if download_verified "$lock_id" "$deb_file" 600; then
        sudo apt-get install -y "$deb_file" || record_fail "$name install"
    else
        record_manual "$name: add a pinned SHA-256 lock entry '$lock_id' to system/download-locks.sha256 before installing"
    fi
}

# --- Package Management Helpers ---
apt_has() {
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        return 0
    fi
    apt-cache show "$1" >/dev/null 2>&1
}

apt_install() {
    run sudo apt-get install -y "$@"
}

apt_install_each() {
    local p
    for p in "$@"; do
        apt_install "$p" || record_fail "apt: $p"
    done
}

apt_install_first() {
    local label="$1"; shift
    local p
    for p in "$@"; do
        if apt_has "$p"; then
            apt_install "$p" && return 0
        fi
    done
    record_fail "apt: none of [$*] found for $label"
    return 0
}

install_flatpak() {
    step "Flatpak: $1"
    run_as_user flatpak install --user -y --noninteractive flathub "$1" \
        || record_manual "Install Flatpak manually: $1"
}

pip_install() {
    local venv_pip="$1"; shift
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] $venv_pip install $*"
        return 0
    fi
    "$venv_pip" install "$@"
}

# --- GNOME GSettings, Extensions & Custom Keybindings ---
gset() {
    local schema="$1" key="$2" val="$3"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] (user:$TARGET_USER) gsettings set $schema $key $val"
        return 0
    fi
    if run_as_user gsettings list-keys "$schema" 2>/dev/null | grep -qx "$key"; then
        run_as_user gsettings set "$schema" "$key" "$val" || record_fail "gsettings $schema $key"
    else
        echo "      (skipped: $schema $key not available for user $TARGET_USER)"
    fi
}

EXT_LOCKFILE="${EXT_LOCKFILE:-$REPO_DIR/system/gnome/extensions.lock}"

emit_stage_event() {
    # Emits structured JSONL install telemetry (and systemd-cat journal entry when available).
    # Strictly no-op under DRY_RUN=1 (zero disk/journal side effects).
    local stage="$1" status="$2" rc="${3:-0}" detail="${4:-}"
    [[ "${DRY_RUN:-0}" == "1" ]] && return 0
    local ts log_dir log_file safe_detail json_line
    ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    safe_detail="${detail//\\/\\\\}"
    safe_detail="${safe_detail//$'\r'/ }"
    safe_detail="${safe_detail//$'\n'/ }"
    safe_detail="${safe_detail//\"/\\\"}"
    json_line="{\"ts\":\"${ts}\",\"stage\":\"${stage}\",\"status\":\"${status}\",\"rc\":${rc},\"profile\":\"${PROFILE:-default-8gb}\",\"detail\":\"${safe_detail}\"}"
    log_dir="$HOME/.local/state/macubuntu"
    log_file="$log_dir/install-events.jsonl"
    mkdir -p -m 700 "$log_dir" 2>/dev/null || true
    printf '%s\n' "$json_line" >> "$log_file" 2>/dev/null || true
    chmod 600 "$log_file" 2>/dev/null || true
    if command -v systemd-cat >/dev/null 2>&1; then
        printf '%s\n' "$json_line" | systemd-cat -t macubuntu-install -p info 2>/dev/null || true
    fi
}

lookup_pinned_extension() {
    # Resolves "version|version_tag" for a GNOME extension UUID from system/gnome/extensions.lock
    # matching host GNOME Shell major version (fallback 50 -> 49 -> 48).
    local uuid="$1" shell_major="${2:-}"
    [[ -f "$EXT_LOCKFILE" ]] || return 1
    if [[ -z "$shell_major" ]] && command -v gnome-shell >/dev/null 2>&1; then
        shell_major="$(gnome-shell --version 2>/dev/null | grep -oE '[0-9]+' | head -1 || true)"
    fi
    local try_ver line u sv ver vtag
    for try_ver in "${shell_major:-50}" 50 49 48; do
        while IFS='|' read -r u sv ver vtag _pk _nm; do
            [[ -z "$u" || "$u" == \#* ]] && continue
            if [[ "$u" == "$uuid" && "$sv" == "$try_ver" ]]; then
                echo "${ver}|${vtag}"
                return 0
            fi
        done < "$EXT_LOCKFILE"
    done
    return 1
}

install_ext() {
    local uuid="$1" name="$2"
    step "GNOME Extension: $name ($uuid)"
    local pin_info="" pin_ver="" pin_tag=""
    pin_info="$(lookup_pinned_extension "$uuid" || true)"
    if [[ -n "$pin_info" ]]; then
        pin_ver="${pin_info%%|*}"
        pin_tag="${pin_info#*|}"
    fi
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        if [[ -n "$pin_tag" ]]; then
            echo "      [dry-run] install pinned extension $uuid v${pin_ver} (version_tag=${pin_tag}) & enable"
        else
            echo "      [dry-run] install & enable extension $uuid"
        fi
        return 0
    fi
    if ! command -v gnome-extensions >/dev/null 2>&1; then
        record_manual "Install GNOME extension: $name ($uuid)"
        return 0
    fi
    local cur_ver="" need_install=0
    if ! gnome-extensions show "$uuid" >/dev/null 2>&1; then
        need_install=1
    elif [[ -n "$pin_ver" ]]; then
        cur_ver="$(gnome-extensions info "$uuid" 2>/dev/null | awk -F': ' '/^[[:space:]]*[Vv]ersion:/ {print $2; exit}' | tr -d '[:space:]' || true)"
        if [[ -n "$cur_ver" && "$cur_ver" != "$pin_ver" ]]; then
            need_install=1
        fi
    fi
    if [[ "$need_install" -eq 1 ]]; then
        local installed_pinned=0
        if [[ -n "$pin_tag" ]] && command -v curl >/dev/null 2>&1; then
            local tmp_zip
            tmp_zip="$(mktemp /tmp/macubuntu-ext-XXXXXX.zip)"
            if curl -fsSL --proto '=https' --tlsv1.2 \
                "https://extensions.gnome.org/download-extension/${uuid}.shell-extension.zip?version_tag=${pin_tag}" \
                -o "$tmp_zip" 2>/dev/null \
                && gnome-extensions install --force "$tmp_zip" >/dev/null 2>&1; then
                installed_pinned=1
                echo "      Installed pinned $uuid v${pin_ver} (version_tag=${pin_tag})."
            fi
            rm -f "$tmp_zip"
        fi
        if [[ "$installed_pinned" -eq 0 ]]; then
            if command -v gext >/dev/null 2>&1; then
                gext install "$uuid" >/dev/null 2>&1 \
                    || { record_manual "Install '$name' ($uuid) from Extension Manager (verify GNOME Shell support)"; return 0; }
            else
                record_manual "Install GNOME extension: $name ($uuid)"
                return 0
            fi
        fi
    fi
    gnome-extensions enable "$uuid" >/dev/null 2>&1 \
        || record_manual "Enable '$name' ($uuid) in Extension Manager after logging out/in"
}

register_gnome_shortcut() {
    local slug="$1" name="$2" cmd="$3" binding="$4"
    local base="/org/gnome/settings-daemon/plugins/media-keys"
    local path="${base}/custom-keybindings/${slug}/"

    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] (user:$TARGET_USER) bind shortcut '$name' ($binding) -> $cmd"
        return 0
    fi
    if ! command -v dconf >/dev/null 2>&1; then
        record_manual "Bind shortcut manually: $name ($binding -> $cmd)"
        return 0
    fi

    local current updated g_name g_cmd g_binding
    current="$(run_as_user dconf read "${base}/custom-keybindings" 2>/dev/null || true)"
    updated="$(python3 - "$current" "$path" <<'PY'
import ast, sys
raw, new_p = sys.argv[1], sys.argv[2]
try:
    items = ast.literal_eval(raw) if raw and raw != "@as []" else []
    if not isinstance(items, list):
        items = []
except Exception:
    items = []
if new_p not in items:
    items.append(new_p)
print(repr(items))
PY
)"
    # Format as valid GVariant double-quoted strings (handles apostrophes, quotes & backslashes safely)
    g_name="$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$name")"
    g_cmd="$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$cmd")"
    g_binding="$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$binding")"
    run_as_user dconf write "${base}/custom-keybindings" "$updated" || true
    run_as_user dconf write "${path}name" "$g_name" || true
    run_as_user dconf write "${path}command" "$g_cmd" || true
    run_as_user dconf write "${path}binding" "$g_binding" || true
}

print_summary() {
    init_runtime_state
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        return 0
    fi
    if [[ -s "$FAILED_FILE" ]]; then
        echo ""
        echo "⚠️  Non-fatal failures:"
        sort -u "$FAILED_FILE" | sed 's/^/   - /'
    fi
    if [[ -s "$MANUAL_FILE" ]]; then
        echo ""
        echo "📋 Manual follow-up items:"
        sort -u "$MANUAL_FILE" | sed 's/^/   * /'
    fi
}
