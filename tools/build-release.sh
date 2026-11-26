#!/usr/bin/env bash
# ============================================================================
# tools/build-release.sh — Reproducible Release Archive & SHA256SUMS Packager
# Produces: <output-dir>/macubuntu-v<version>.tar.gz + <output-dir>/SHA256SUMS
# ============================================================================
set -Eeuo pipefail
umask 022

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="4.4.0"
OUT_DIR="$REPO_DIR/releases"
DRY_RUN="${DRY_RUN:-0}"

usage() {
    cat <<'EOF'
Usage: ./tools/build-release.sh [options]

Packages macubuntu into a clean, reproducible tarball + SHA256SUMS manifest
with executable permissions preserved and caches excluded.

Options:
  --version <semver>   Release version tag (default: 4.4.0)
  --output-dir <dir>   Output directory (default: ./releases)
  --dry-run            Preview package plan without writing any files
  -h, --help           Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)    VERSION="${2:?missing version}"; shift 2 ;;
        --output-dir) OUT_DIR="${2:?missing output-dir}"; shift 2 ;;
        --dry-run)    DRY_RUN="1"; shift ;;
        -h|--help)    usage; exit 0 ;;
        *)            echo "❌ Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([a-zA-Z0-9.-]*)?$ ]]; then
    echo "❌ Invalid version string: '$VERSION'" >&2
    exit 1
fi

ARCHIVE_NAME="macubuntu-v${VERSION}.tar.gz"
PREFIX="macubuntu-v${VERSION}"

if [[ "$DRY_RUN" == "1" ]]; then
    echo "[dry-run] Would package $REPO_DIR -> ${OUT_DIR}/${ARCHIVE_NAME}"
    echo "[dry-run] Would write SHA-256 manifest -> ${OUT_DIR}/SHA256SUMS"
    echo "[dry-run] Would verify archive with sha256sum -c"
    exit 0
fi

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
ARCHIVE_PATH="${OUT_DIR}/${ARCHIVE_NAME}"
SUMS_PATH="${OUT_DIR}/SHA256SUMS"

# Build deterministic, sorted tarball excluding runtime caches & previous releases
TMP_STAGE="$(mktemp -d)"
trap 'rm -rf "$TMP_STAGE"' EXIT

mkdir -p "$TMP_STAGE/$PREFIX"
tar -C "$REPO_DIR" \
    --exclude='./__pycache__' \
    --exclude='*/__pycache__' \
    --exclude='./.pytest_cache' \
    --exclude='*/.pytest_cache' \
    --exclude='./releases' \
    --exclude='./updates/*.tar.gz' \
    --exclude='./updates/SHA256SUMS' \
    --exclude='./dist' \
    --exclude='./.git' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.bak' \
    -cf - . | tar -C "$TMP_STAGE/$PREFIX" -xf -

# Guarantee executable permissions on all shell entrypoints/stages/tools inside archive
find "$TMP_STAGE/$PREFIX" -name '*.sh' -exec chmod 0755 {} +

TAR_FLAGS=(--sort=name --owner=0 --group=0 --numeric-owner --mtime='@1763251200')
if ! tar "${TAR_FLAGS[@]}" -cf /dev/null -T /dev/null 2>/dev/null; then
    TAR_FLAGS=()
fi

tar -C "$TMP_STAGE" "${TAR_FLAGS[@]}" -czf "$ARCHIVE_PATH" "$PREFIX"

(
    cd "$OUT_DIR"
    shopt -s nullglob
    ARTIFACTS=(macubuntu-v*.tar.gz macubuntu_*.deb)
    sha256sum "${ARTIFACTS[@]}" > "$SUMS_PATH"
    sha256sum -c "$SUMS_PATH"
)

SIZE_HUMAN="$(du -h "$ARCHIVE_PATH" | awk '{print $1}')"
FILE_COUNT="$(tar -tzf "$ARCHIVE_PATH" | wc -l | tr -d ' ')"
echo "✅ Release packaged & verified:"
echo "   Archive: $ARCHIVE_PATH ($SIZE_HUMAN, $FILE_COUNT entries)"
echo "   SHA-256: $(cat "$SUMS_PATH")"
