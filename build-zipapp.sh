#!/usr/bin/env bash
# ============================================================================
# build-zipapp.sh — Build a standalone Celina .pyz executable (zipapp)
#
# Produces: dist/celina-<semver>.pyz
# A self-contained, single-file executable bundling Celina + its Python deps.
# Zero install required — just `chmod +x` and run.
#
# Exit gate: .pyz must start and respond to --help offline.
# ============================================================================
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# Canonical release semver from version.toml (single source of truth)
if command -v python3 >/dev/null 2>&1; then
    RELEASE_SEMVER="$(python3 -c "
import tomllib
with open('version.toml','rb') as fh:
    print(tomllib.load(fh)['release']['semver'])
")"
else
    echo "❌ python3 required for zipapp build" >&2
    exit 1
fi

DIST_DIR="$REPO_DIR/dist"
mkdir -p "$DIST_DIR"

# Source tree to bundle
SRC_DIR="$REPO_DIR/features/celina"

# Main entry point
MAIN_MODULE="celina_pkg.__main__:main"

# Build the zipapp
python3 -m zipapp \
    --compress \
    --python="/usr/bin/env python3" \
    --main="$MAIN_MODULE" \
    --output="$DIST_DIR/celina-$RELEASE_SEMVER.pyz" \
    "$SRC_DIR"

echo "✅ Built $DIST_DIR/celina-$RELEASE_SEMVER.pyz"

# Verify it starts offline
if "$DIST_DIR/celina-$RELEASE_SEMVER.pyz" --help >/tmp/pyz_help.out 2>&1; then
    echo "✅ .pyz starts and responds to --help (offline gate passed)"
else
    echo "❌ .pyz failed to start — see /tmp/pyz_help.out"
    cat /tmp/pyz_help.out >&2
    exit 1
fi

# SHA-256 for release manifest
sha256sum "$DIST_DIR/celina-$RELEASE_SEMVER.pyz" > "$DIST_DIR/celina-$RELEASE_SEMVER.pyz.sha256"
echo "✅ SHA-256 written to $DIST_DIR/celina-$RELEASE_SEMVER.pyz.sha256"

exit 0
