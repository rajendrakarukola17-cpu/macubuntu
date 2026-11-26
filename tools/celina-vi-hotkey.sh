#!/usr/bin/env bash
# ============================================================================
# celina-vi-hotkey — Standalone Visual Intelligence Hotkey Registrar
#
# Registers the <Super><Shift>v Visual Intelligence keybinding in GNOME
# without clobbering existing user custom keybindings. DRY_RUN aware.
#
# Usage: ./tools/celina-vi-hotkey.sh [--dry-run]
# ============================================================================
set -euo pipefail

DRY_RUN="${DRY_RUN:-0}"
if [[ "${1:-}" == "--dry-run" || "${1:-}" == "1" ]]; then
    DRY_RUN=1
fi

KEY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/celina-vi/"
SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
ITEM_SCHEMA="${SCHEMA}.custom-keybinding:${KEY_PATH}"
CMD="$HOME/.local/bin/celina-visual-intelligence"
BINDING="<Super><Shift>v"

log() { echo "  $*"; }
dryrun() { [[ "$DRY_RUN" == "1" ]] && echo "  [dry-run] $*" || true; }

if ! command -v gsettings >/dev/null 2>&1; then
    echo "⚠️  gsettings not available — skipping keybinding registration"
    exit 0
fi

if ! gsettings list-schemas 2>/dev/null | grep -q "^${SCHEMA}$"; then
    echo "⚠️  GNOME settings schema '$SCHEMA' not installed — skipping keybinding registration"
    exit 0
fi

# --- Safely append KEY_PATH to GNOME's GVariant custom-keybindings array ---
CURRENT="$(gsettings get "$SCHEMA" custom-keybindings 2>/dev/null || echo "@as []")"
UPDATED="$(python3 - "$CURRENT" "$KEY_PATH" <<'PY'
import ast, sys
raw, target = sys.argv[1].strip(), sys.argv[2]
items = [] if raw in ("@as []", "[]", "") else list(ast.literal_eval(raw))
if target not in items:
    items.append(target)
print(str(items))
PY
)"

if [[ "$DRY_RUN" == "1" ]]; then
    log "[dry-run] Would set custom-keybindings to: $UPDATED"
    log "[dry-run] Would set name='Celina Visual Intelligence'"
    log "[dry-run] Would set command=$CMD"
    log "[dry-run] Would set binding=$BINDING"
    log "[dry-run] Visual Intelligence hotkey registration preview complete (no changes made)"
    exit 0
fi

gsettings set "$SCHEMA" custom-keybindings "$UPDATED" 2>/dev/null || \
    echo "⚠️  Failed to set custom-keybindings list (may need manual dconf setup)"
gsettings set "$ITEM_SCHEMA" name "Celina Visual Intelligence" 2>/dev/null || true
gsettings set "$ITEM_SCHEMA" command "$CMD" 2>/dev/null || true
gsettings set "$ITEM_SCHEMA" binding "$BINDING" 2>/dev/null || true

echo "Bound <Super><Shift>v -> $CMD"
