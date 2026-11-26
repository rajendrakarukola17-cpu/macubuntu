#!/usr/bin/env bash
# ============================================================================
# setup-ab-install.sh — Create A/B install directory structure for rollback
#
# Produces:
#   ~/.local/share/celina/install-a/  (active)
#   ~/.local/share/celina/install-b/  (standby)
#   ~/.local/share/celina/current -> install-a  (active symlink)
#
# Exit gate: `celina --version` works through the symlink, and switching
# the symlink to install-b rolls back cleanly.
# ============================================================================
set -Eeuo pipefail

BASE_DIR="${HOME}/.local/share/celina"
mkdir -p "$BASE_DIR"

for side in a b; do
    DIR="$BASE_DIR/install-$side"
    mkdir -p "$DIR/bin" "$DIR/lib" "$DIR/share"
    # Version marker
    echo "install-$side" > "$DIR/.install_side"
    echo "4.4.0" > "$DIR/.version"
done

# Active symlink -> install-a
if [[ ! -L "$BASE_DIR/current" ]]; then
    ln -s install-a "$BASE_DIR/current"
    echo "✅ current -> install-a (active)"
else
    echo "ℹ️  current already points to $(readlink "$BASE_DIR/current")"
fi

echo "✅ A/B install structure ready at $BASE_DIR"
echo "   To rollback: ln -snf install-b $BASE_DIR/current"

exit 0
