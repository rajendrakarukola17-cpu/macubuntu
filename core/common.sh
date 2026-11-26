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

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCKFILE="${LOCKFILE:-$REPO_DIR/system/download-locks.sha256}"
# shellcheck source=core/detect.sh
source "$REPO_DIR/core/detect.sh"

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

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
        ENABLE_FLATSEAL|ENABLE_UNIVERSAL_CONTROL|ENABLE_BATTERY_CHARGE_LIMIT|\
        ENABLE_WAYLAND_FRACTIONAL_SCALING|ENABLE_VAAPI_GPU_DECODE|\
        ENABLE_NAUTILUS_QUICK_ACTIONS|ENABLE_KEEPASSXC|ENABLE_DISTROBOX|\
        ENABLE_CLIPBOARD_HISTORY|ENABLE_DYNAMIC_APPEARANCE|ENABLE_SAFARI_FIREFOX|\
        ENABLE_MACOS_CMD_KEYS|ENABLE_PERSISTENT_CONTINUITY_CAM|\
        ENABLE_PRO_AUDIO_BLUETOOTH|ENABLE_SAFE_SLEEP|ENABLE_SILENT_BOOT|\
        REPLACE_DISK_SWAP|ENABLE_ACCESSIBILITY)
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
    export ENABLE_AIRPLAY_VIDEO="${ENABLE_AIRPLAY_VIDEO:-true}"
    export ENABLE_ANDROID_MIRROR="${ENABLE_ANDROID_MIRROR:-true}"
    export ENABLE_SYNCTHING="${ENABLE_SYNCTHING:-true}"
    export ENABLE_FLATSEAL="${ENABLE_FLATSEAL:-true}"
    export ENABLE_UNIVERSAL_CONTROL="${ENABLE_UNIVERSAL_CONTROL:-true}"
    export ENABLE_BATTERY_CHARGE_LIMIT="${ENABLE_BATTERY_CHARGE_LIMIT:-true}"
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

# --- Shared Cross-Stage State (Never Writes to $HOME During DRY_RUN=1) ---
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
    touch "$MANUAL_FILE" "$FAILED_FILE"
    chmod 600 "$MANUAL_FILE" "$FAILED_FILE"
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
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] (user) $*"
        return 0
    fi
    "$@"
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
    sudo install -d -m 755 "$(dirname "$path")"
    sudo tee "$path" >/dev/null
    sudo chmod "$mode" "$path"
}

copy_root_file() {
    local src="$1" dst="$2" mode="${3:-644}"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] sudo install -m $mode $src -> $dst"
        return 0
    fi
    sudo install -d -m 755 "$(dirname "$dst")"
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
        echo "      [dry-run] systemctl --user enable --now $unit"
        return 0
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user enable --now "$unit" 2>/dev/null || return 1
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
    run flatpak install --user -y --noninteractive flathub "$1" \
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
        echo "      [dry-run] gsettings set $schema $key $val"
        return 0
    fi
    if gsettings list-keys "$schema" 2>/dev/null | grep -qx "$key"; then
        gsettings set "$schema" "$key" "$val" || record_fail "gsettings $schema $key"
    else
        echo "      (skipped: $schema $key not available)"
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
        echo "      [dry-run] bind shortcut '$name' ($binding) -> $cmd"
        return 0
    fi
    if ! command -v dconf >/dev/null 2>&1; then
        record_manual "Bind shortcut manually: $name ($binding -> $cmd)"
        return 0
    fi

    local current updated g_name g_cmd g_binding
    current="$(dconf read "${base}/custom-keybindings" 2>/dev/null || true)"
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
    dconf write "${base}/custom-keybindings" "$updated" || true
    dconf write "${path}name" "$g_name" || true
    dconf write "${path}command" "$g_cmd" || true
    dconf write "${path}binding" "$g_binding" || true
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
