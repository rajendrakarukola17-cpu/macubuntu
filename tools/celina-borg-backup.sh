#!/usr/bin/env bash
# ==============================================================================
# macubuntu Time Machine-Style Nightly BorgBackup Automation
# Configure destination in ~/.config/celina/borg.env:
#   BORG_REPO="/mnt/backup/celina-borg"
#   BORG_PASSCOMMAND="secret-tool lookup borg celina-repo"   # recommended (Keyring)
#   # or: BORG_PASSPHRASE="..."                              # plaintext fallback
# Safely exits 0 with instructions when BORG_REPO is not configured yet.
# ==============================================================================
set -Eeuo pipefail
umask 077

DRY_RUN=0
CONF_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/celina/borg.env"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --config)
            if [[ $# -lt 2 ]]; then
                echo "❌ --config requires a path argument" >&2
                exit 2
            fi
            CONF_FILE="$2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

# --- P0: Verify CONF_FILE ownership + 0600 before sourcing --------------------
if [[ -f "$CONF_FILE" ]]; then
    conf_mode="$(stat -c '%a' "$CONF_FILE" 2>/dev/null || echo "")"
    conf_uid="$(stat -c '%u' "$CONF_FILE" 2>/dev/null || echo "")"
    cur_uid="$(id -u)"
    if [[ "$conf_uid" != "$cur_uid" || "$conf_mode" != "600" ]]; then
        echo "❌ Refusing to source $CONF_FILE (must be owned by $(id -un) with mode 0600, got uid=${conf_uid:-?} mode=${conf_mode:-?})" >&2
        exit 1
    fi
    # shellcheck disable=SC1090
    source "$CONF_FILE"
fi

# Export BORG_PASSPHRASE so borg can operate non-interactively on encrypted repos
if [[ -n "${BORG_PASSPHRASE:-}" ]]; then
    export BORG_PASSPHRASE
fi

if [[ -z "${BORG_REPO:-}" ]]; then
    echo "BorgBackup skipped: set BORG_REPO in $CONF_FILE (or use Vorta/Pika Backup GUI)."
    exit 0
fi

ARCHIVE="${BORG_REPO}::macubuntu-$(hostname)-$(date +%Y-%m-%d_%H%M%S)-$$"
TARGETS=()
for d in "$HOME/Documents" "$HOME/Desktop" "$HOME/Projects"; do
    [[ -e "$d" ]] && TARGETS+=("$d")
done

SNAP_DIR=""
cleanup() {
    [[ -n "$SNAP_DIR" ]] && rm -rf "$SNAP_DIR" 2>/dev/null || true
}
trap cleanup EXIT

# --- P0: Crash-consistent SQLite WAL snapshot of memory.db -------------------
DB_PATH="${XDG_DATA_HOME:-$HOME/.local/share}/celina/memory.db"
if [[ -f "$DB_PATH" ]]; then
    SNAP_DIR="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/celina-borg-snap.XXXXXX")"
    chmod 700 "$SNAP_DIR"
    python3 - "$DB_PATH" "$SNAP_DIR/memory.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=10.0)
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
PY
    TARGETS+=("$SNAP_DIR/memory.db")
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] borg create --compression zstd,3 '$ARCHIVE' ${TARGETS[*]:-<none>}"
    echo "[dry-run] borg prune --keep-daily=7 --keep-weekly=4 --keep-monthly=6 '$BORG_REPO'"
    echo "[dry-run] borg compact '$BORG_REPO'"
    exit 0
fi

# --- P0: Require encryption passphrase/passcommand before actual backup ------
if [[ -z "${BORG_PASSPHRASE:-}" && -z "${BORG_PASSCOMMAND:-}" ]]; then
    echo "❌ BORG_PASSPHRASE or BORG_PASSCOMMAND (recommended: secret-tool lookup) must be set for encrypted backups (T5)." >&2
    exit 1
fi

if ! command -v borg >/dev/null 2>&1; then
    echo "borg binary not installed; run: sudo apt install borgbackup" >&2
    exit 1
fi

if [[ "${#TARGETS[@]}" -eq 0 ]]; then
    echo "No backup targets found under $HOME."
    exit 0
fi

# --- P1: Accept Borg warning exit code 1 without aborting prune/compact ------
create_rc=0
borg create --stats --compression zstd,3 "$ARCHIVE" "${TARGETS[@]}" || create_rc=$?
if (( create_rc > 1 )); then
    exit "$create_rc"
fi
borg prune --keep-daily=7 --keep-weekly=4 --keep-monthly=6 "$BORG_REPO"
borg compact "$BORG_REPO"