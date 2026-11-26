#!/usr/bin/env bash
# ==============================================================================
# macubuntu Auto Dark Mode (Diff-Gated Non-Thrashing Sunset-to-Sunrise Switcher)
# - Runs every 10 minutes via celina-auto-theme.timer
# - Reads optional offline ~/.config/auto-theme-switcher/config.json (no network required)
# - Diff-gates gsettings: ONLY calls `gsettings set` when the current color-scheme
#   differs from the target, preventing GTK/icon theme engine thrashing on every tick!
# ==============================================================================
set -euo pipefail

DRY_RUN=0
CONFIG_JSON="${XDG_CONFIG_HOME:-$HOME/.config}/auto-theme-switcher/config.json"
DAY_START_HOUR="${CELINA_DAY_START_HOUR:-7}"
NIGHT_START_HOUR="${CELINA_NIGHT_START_HOUR:-19}"
DAY_SCHEME="prefer-light"
NIGHT_SCHEME="prefer-dark"
OVERRIDE_HOUR=""
OVERRIDE_CURRENT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --hour) OVERRIDE_HOUR="${2:-}"; shift 2 ;;
        --current) OVERRIDE_CURRENT="${2:-}"; shift 2 ;;
        --config) CONFIG_JSON="${2:-}"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Optional offline lat/lon or explicit hour config (~/.config/auto-theme-switcher/config.json)
# Parsed safely via tab-delimited read (never `eval`).
if [[ -f "$CONFIG_JSON" ]] && command -v python3 >/dev/null 2>&1; then
    IFS=$'\t' read -r cfg_day cfg_night cfg_day_scheme cfg_night_scheme < <(
        python3 - "$CONFIG_JSON" "$DAY_START_HOUR" "$NIGHT_START_HOUR" "$DAY_SCHEME" "$NIGHT_SCHEME" <<'PY'
import json, sys
p, d, n, ds, ns = sys.argv[1:6]
try:
    cfg = json.loads(open(p, encoding="utf-8").read())
    if "day_start_hour" in cfg:
        d = str(int(cfg["day_start_hour"]))
    if "night_start_hour" in cfg:
        n = str(int(cfg["night_start_hour"]))
    if cfg.get("day_scheme") in ("default", "prefer-light"):
        ds = str(cfg["day_scheme"])
    if cfg.get("night_scheme") in ("prefer-dark",):
        ns = str(cfg["night_scheme"])
except Exception:
    pass
print(f"{d}\t{n}\t{ds}\t{ns}")
PY
    ) || true
    [[ -n "${cfg_day:-}" ]] && DAY_START_HOUR="$cfg_day"
    [[ -n "${cfg_night:-}" ]] && NIGHT_START_HOUR="$cfg_night"
    [[ -n "${cfg_day_scheme:-}" ]] && DAY_SCHEME="$cfg_day_scheme"
    [[ -n "${cfg_night_scheme:-}" ]] && NIGHT_SCHEME="$cfg_night_scheme"
fi

CUR_HOUR="${OVERRIDE_HOUR:-$(date +%H | sed 's/^0//')}"
[[ -n "$CUR_HOUR" ]] || CUR_HOUR=0

if (( CUR_HOUR >= DAY_START_HOUR && CUR_HOUR < NIGHT_START_HOUR )); then
    TARGET="$DAY_SCHEME"
else
    TARGET="$NIGHT_SCHEME"
fi

CURRENT="$OVERRIDE_CURRENT"
if [[ -z "$CURRENT" ]] && command -v gsettings >/dev/null 2>&1; then
    CURRENT="$(gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null | tr -d "'" || true)"
fi

# Diff-gate: do NOT thrash GTK/icon theme engine when already matching target!
if [[ "$CURRENT" == "$TARGET" ]]; then
    echo "unchanged: color-scheme already '$TARGET' (hour=${CUR_HOUR})"
    exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] gsettings set org.gnome.desktop.interface color-scheme '$TARGET' (prev='${CURRENT:-unknown}', hour=${CUR_HOUR})"
    exit 0
fi

if command -v gsettings >/dev/null 2>&1; then
    gsettings set org.gnome.desktop.interface color-scheme "$TARGET"
fi
