#!/usr/bin/env bash
# ==============================================================================
# macubuntu Time Machine-Style Nightly BorgBackup Automation
# Configure destination in ~/.config/celina/borg.env:
#   BORG_REPO="/mnt/backup/celina-borg"
#   BORG_PASSPHRASE="..."
# Safely exits 0 with instructions when BORG_REPO is not configured yet.
# ==============================================================================
set -euo pipefail
umask 077

DRY_RUN=0
CONF_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/celina/borg.env"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --config) CONF_FILE="${2:-}"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ -f "$CONF_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONF_FILE"
fi

if [[ -z "${BORG_REPO:-}" ]]; then
    echo "BorgBackup skipped: set BORG_REPO in $CONF_FILE (or use Vorta/Pika Backup GUI)."
    exit 0
fi

ARCHIVE="${BORG_REPO}::macubuntu-$(hostname)-$(date +%Y-%m-%d_%H%M)"
TARGETS=()
for d in "$HOME/Documents" "$HOME/Desktop" "$HOME/Projects" "${XDG_DATA_HOME:-$HOME/.local/share}/celina/memory.db"; do
    [[ -e "$d" ]] && TARGETS+=("$d")
done

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] borg create --compression zstd,3 '$ARCHIVE' ${TARGETS[*]:-<none>}"
    echo "[dry-run] borg prune --keep-daily=7 --keep-weekly=4 --keep-monthly=6 '$BORG_REPO'"
    exit 0
fi

if ! command -v borg >/dev/null 2>&1; then
    echo "borg binary not installed; run: sudo apt install borgbackup" >&2
    exit 1
fi

if [[ "${#TARGETS[@]}" -eq 0 ]]; then
    echo "No backup targets found under $HOME."
    exit 0
fi

borg create --stats --compression zstd,3 "$ARCHIVE" "${TARGETS[@]}"
borg prune --keep-daily=7 --keep-weekly=4 --keep-monthly=6 "$BORG_REPO"
