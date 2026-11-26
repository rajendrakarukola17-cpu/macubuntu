#!/usr/bin/env bash
# ==============================================================================
# macubuntu Auto Dark Mode (Diff-Gated Non-Thrashing Sunset-to-Sunrise Switcher)
# - Runs every 10 minutes via celina-auto-theme.timer
# - Reads optional offline ~/.config/auto-theme-switcher/config.json (no network required)
# - Diff-gates gsettings: ONLY calls `gsettings set` when the current color-scheme
#   differs from the target, preventing GTK/icon theme engine thrashing on every tick!
# ==============================================================================
set -Eeuo pipefail

DRY_RUN=0
CONFIG_JSON="${XDG_CONFIG_HOME:-$HOME/.config}/auto-theme-switcher/config.json"
DAY_START_HOUR="${CELINA_DAY_START_HOUR:-7}"
NIGHT_START_HOUR="${CELINA_NIGHT_START_HOUR:-19}"
DAY_SCHEME="prefer-light"
NIGHT_SCHEME="prefer-dark"
OVERRIDE_HOUR=""
OVERRIDE_CURRENT=""

usage() {
    cat <<'EOF'
Usage: celina-auto-theme.sh [options]

Options:
  --dry-run      Preview the theme switch without calling gsettings
  --hour <N>     Override current hour (integer 0..23) for testing
  --current <scheme>  Override detected current color-scheme (prefer-light|prefer-dark)
  --config <path>   Path to auto-theme-switcher config.json
  -h, --help      Show this help

Exit codes: 0 no-change | 1 error | 2 misuse
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --hour)
            if [[ $# -lt 2 ]]; then echo "❌ --hour requires an integer argument" >&2; usage; exit 2; fi
            OVERRIDE_HOUR="$2"; shift 2 ;;
        --current)
            if [[ $# -lt 2 ]]; then echo "❌ --current requires a scheme argument" >&2; usage; exit 2; fi
            OVERRIDE_CURRENT="$2"; shift 2 ;;
        --config)
            if [[ $# -lt 2 ]]; then echo "❌ --config requires a path argument" >&2; usage; exit 2; fi
            CONFIG_JSON="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
    esac
done

# --- P0: Strict hour validation (rejects arithmetic injection, fixes 08/09 octal) ---
validate_hour() {
    local label="$1" raw="$2"
    if [[ ! "$raw" =~ ^[0-9]{1,2}$ ]]; then
        echo "❌ Invalid $label: '$raw' (expected integer 0..23)" >&2
        exit 2
    fi
    local val
    val=$((10#$raw))  # force base-10 (prevents 08/09 octal parse crash)
    if (( val < 0 || val > 23 )); then
        echo "❌ Out-of-range $label: $val (expected 0..23)" >&2
        exit 2
    fi
    echo "$val"
}

# Validate override hour if provided
if [[ -n "$OVERRIDE_HOUR" ]]; then
    OVERRIDE_HOUR="$(validate_hour "--hour" "$OVERRIDE_HOUR")"
fi

# Validate env-provided hours
DAY_START_HOUR="$(validate_hour "CELINA_DAY_START_HOUR" "$DAY_START_HOUR")"
NIGHT_START_HOUR="$(validate_hour "CELINA_NIGHT_START_HOUR" "$NIGHT_START_HOUR")"

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
    [[ -n "${cfg_day:-}" ]] && DAY_START_HOUR="$(validate_hour "config.day_start_hour" "$cfg_day")"
    [[ -n "${cfg_night:-}" ]] && NIGHT_START_HOUR="$(validate_hour "config.night_start_hour" "$cfg_night")"
    [[ -n "${cfg_day_scheme:-}" ]] && DAY_SCHEME="$cfg_day_scheme"
    [[ -n "${cfg_night_scheme:-}" ]] && NIGHT_SCHEME="$cfg_night_scheme"
fi

CUR_HOUR="${OVERRIDE_HOUR:-$(date +%H | sed 's/^0//')}"
[[ -n "$CUR_HOUR" ]] || CUR_HOUR=0

# --- P0: Handle wrap-around schedules (DAY_START_HOUR >= NIGHT_START_HOUR) ---
if (( DAY_START_HOUR < NIGHT_START_HOUR )); then
    # Normal schedule: day from DAY_START to NIGHT_START
    if (( CUR_HOUR >= DAY_START_HOUR && CUR_HOUR < NIGHT_START_HOUR )); then
        TARGET="$DAY_SCHEME"
    else
        TARGET="$NIGHT_SCHEME"
    fi
else
    # Wrap-around schedule (e.g., day=22, night=6): night from NIGHT_START to DAY_START
    if (( CUR_HOUR >= NIGHT_START_HOUR && CUR_HOUR < DAY_START_HOUR )); then
        TARGET="$NIGHT_SCHEME"
    else
        TARGET="$DAY_SCHEME"
    fi
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
else
    echo "⚠️  gsettings not found; cannot set color-scheme to '$TARGET'" >&2
    exit 1
fi