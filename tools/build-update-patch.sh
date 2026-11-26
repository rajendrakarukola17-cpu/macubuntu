#!/usr/bin/env bash
# ============================================================================
# tools/build-update-patch.sh — Standalone Numbered Update Builder
# Builds a self-contained update archive into macubuntu/updates/ as:
#   updates/update-01.tar.gz  (+ updates/latest-update.tar.gz + SHA256SUMS)
# ============================================================================
set -Eeuo pipefail
umask 022

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_NUM="01"
OUT_DIR="$REPO_DIR/updates"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --num|--tag) UPDATE_NUM="${2:?missing update number}"; shift 2 ;;
        --out-dir)   OUT_DIR="${2:?missing out dir}"; shift 2 ;;
        -h|--help)
            echo "Usage: ./tools/build-update-patch.sh [--num 01] [--out-dir ./updates]"
            exit 0
            ;;
        *) echo "❌ Unknown option: $1" >&2; exit 1 ;;
    esac
done

# Normalize bare digits like "1" -> "01"
if [[ "$UPDATE_NUM" =~ ^[0-9]$ ]]; then
    UPDATE_NUM="0${UPDATE_NUM}"
fi
PREFIX="update-${UPDATE_NUM}"

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

VERSIONED_ARCHIVE="${OUT_DIR}/${PREFIX}.tar.gz"
LATEST_ARCHIVE="${OUT_DIR}/latest-update.tar.gz"
SUMS_PATH="${OUT_DIR}/SHA256SUMS"

TMP_STAGE="$(mktemp -d)"
trap 'rm -rf "$TMP_STAGE"' EXIT

PKG_ROOT="$TMP_STAGE/$PREFIX"
mkdir -p "$PKG_ROOT/payload/features/celina" "$PKG_ROOT/payload/system/gnome/extensions" "$PKG_ROOT/payload/core"

cp "$REPO_DIR/updates/apply-update.sh" "$PKG_ROOT/apply-update.sh"
chmod 0755 "$PKG_ROOT/apply-update.sh"
if [[ -f "$REPO_DIR/updates/PATCH-NOTES.md" ]]; then
    cp "$REPO_DIR/updates/PATCH-NOTES.md" "$PKG_ROOT/PATCH-NOTES.md"
fi
printf '%s\n' "$PREFIX" > "$PKG_ROOT/UPDATE_VERSION"

# Copy updated runtime & workspace payload (no caches, no releases, no tests)
cp -r "$REPO_DIR/features/celina/celina_pkg" "$PKG_ROOT/payload/features/celina/celina_pkg"
cp -r "$REPO_DIR/features/celina/celina_hardware" "$PKG_ROOT/payload/features/celina/celina_hardware"
cp -r "$REPO_DIR/features/celina/config" "$PKG_ROOT/payload/features/celina/config"
cp "$REPO_DIR/features/celina/install.sh" "$PKG_ROOT/payload/features/celina/install.sh"
cp -r "$REPO_DIR/system/systemd" "$PKG_ROOT/payload/system/systemd"
cp -r "$REPO_DIR/system/gnome/extensions/celina-island@macubuntu.org" \
      "$PKG_ROOT/payload/system/gnome/extensions/celina-island@macubuntu.org"
cp "$REPO_DIR/core/common.sh" "$PKG_ROOT/payload/core/common.sh"

find "$PKG_ROOT" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$PKG_ROOT" -name '*.pyc' -delete 2>/dev/null || true
find "$PKG_ROOT" -name '*.sh' -exec chmod 0755 {} +

TAR_FLAGS=(--sort=name --owner=0 --group=0 --numeric-owner --mtime='@1763251200')
if ! tar "${TAR_FLAGS[@]}" -cf /dev/null -T /dev/null 2>/dev/null; then
    TAR_FLAGS=()
fi

tar -C "$TMP_STAGE" "${TAR_FLAGS[@]}" -czf "$VERSIONED_ARCHIVE" "$PREFIX"
cp -f "$VERSIONED_ARCHIVE" "$LATEST_ARCHIVE"

(
    cd "$OUT_DIR"
    shopt -s nullglob
    FILES=(update-*.tar.gz latest-update.tar.gz)
    sha256sum "${FILES[@]}" > "$SUMS_PATH"
    sha256sum -c "$SUMS_PATH"
)

SIZE="$(du -h "$VERSIONED_ARCHIVE" | awk '{print $1}')"
echo "✅ Built standalone update bundle: $VERSIONED_ARCHIVE ($SIZE)"
echo "✅ Updated latest pointer:         $LATEST_ARCHIVE ($SIZE)"
