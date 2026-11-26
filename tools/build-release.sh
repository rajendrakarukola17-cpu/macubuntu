#!/usr/bin/env bash
# ============================================================================
# tools/build-release.sh — Reproducible Release Archive & SHA256SUMS Packager
# Produces: <output-dir>/macubuntu-v<version>.tar.gz + <output-dir>/SHA256SUMS
# ============================================================================
set -Eeuo pipefail
umask 022

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- Default version from version.toml (single source of truth) ---------------
RELEASE_VERSION_DEFAULT="4.4.0"
if [[ -f "$REPO_DIR/version.toml" ]]; then
    toml_ver="$(grep -E '^version\s*=' "$REPO_DIR/version.toml" | head -n1 | sed -E 's/^version\s*=\s*"([^"]+)".*/\1/' || true)"
    [[ -n "$toml_ver" ]] && RELEASE_VERSION_DEFAULT="$toml_ver"
elif [[ -f "$REPO_DIR/VERSION" ]]; then
    RELEASE_VERSION_DEFAULT="$(cat "$REPO_DIR/VERSION" | tr -d '[:space:]' || echo "$RELEASE_VERSION_DEFAULT")"
fi
VERSION="${RELEASE_VERSION:-$RELEASE_VERSION_DEFAULT}"
OUT_DIR="$REPO_DIR/releases"
DRY_RUN="${DRY_RUN:-0}"

usage() {
    cat <<'EOF'
Usage: ./tools/build-release.sh [options]

Packages macubuntu into a clean, reproducible tarball + SHA256SUMS manifest
with executable permissions preserved and caches excluded.

Options:
  --version <semver>   Release version tag (default: from version.toml)
  --output-dir <dir>   Output directory (default: ./releases)
  --dry-run            Preview package plan without writing any files
  -h, --help           Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            if [[ $# -lt 2 ]]; then echo "❌ --version requires a version argument" >&2; usage; exit 2; fi
            VERSION="${2:?missing version}"; shift 2 ;;
        --output-dir)
            if [[ $# -lt 2 ]]; then echo "❌ --output-dir requires a directory argument" >&2; usage; exit 2; fi
            OUT_DIR="${2:?missing output-dir}"; shift 2 ;;
        --dry-run)    DRY_RUN="1"; shift ;;
        -h|--help)    usage; exit 0 ;;
        *)            echo "❌ Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([a-zA-Z0-9.-]*)?$ ]]; then
    echo "❌ Invalid version string: '$VERSION'" >&2
    exit 2
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

# Build deterministic, sorted tarball excluding runtime caches, secrets, previous releases
TMP_STAGE="$(mktemp -d)"
trap 'rm -rf "$TMP_STAGE"' EXIT

mkdir -p "$TMP_STAGE/$PREFIX"
tar -C "$REPO_DIR" \
    --exclude='./__pycache__' \
    --exclude='*/__pycache__' \
    --exclude='./.pytest_cache' \
    --exclude='*/.pytest_cache' \
    --exclude='./.ruff_cache' \
    --exclude='*/.ruff_cache' \
    --exclude='./.mypy_cache' \
    --exclude='*/.mypy_cache' \
    --exclude='./releases' \
    --exclude='./updates/*.tar.gz' \
    --exclude='./updates/SHA256SUMS' \
    --exclude='./dist' \
    --exclude='./.git' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.bak' \
    --exclude='._*' \
    --exclude='.DS_Store' \
    --exclude='__MACOSX' \
    --exclude='.env' \
    --exclude='*.env' \
    --exclude='.ai-env' \
    --exclude='*.key' \
    --exclude='*.pem' \
    --exclude='*.db' \
    --exclude='*.db-wal' \
    --exclude='*.db-shm' \
    --exclude='*venv*' \
    --exclude='.venv' \
    --exclude='node_modules' \
    --exclude='*.egg-info' \
    --exclude='*.mubundle' \
    -cf - . | tar -C "$TMP_STAGE/$PREFIX" -xf -

# Preserve 0755 on extensionless tools and *.py as well as *.sh
find "$TMP_STAGE/$PREFIX" -name '*.sh' -exec chmod 0755 {} +
find "$TMP_STAGE/$PREFIX" -name '*.py' -exec chmod 0755 {} +
for ext_bin in macubuntu-reconfigure macubuntu-ram-change-detector celina-vmtouch-ctl celina-visual-intelligence; do
    [[ -f "$TMP_STAGE/$PREFIX/tools/$ext_bin" ]] && chmod 0755 "$TMP_STAGE/$PREFIX/tools/$ext_bin"
done

# Reproducible tar + gzip -n (strip wall-clock from gzip header)
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1763251200}"
TAR_FLAGS=(--sort=name --owner=0 --group=0 --numeric-owner --mtime="@${SOURCE_DATE_EPOCH}")
if ! tar "${TAR_FLAGS[@]}" -cf /dev/null -T /dev/null 2>/dev/null; then
    TAR_FLAGS=()
fi

COPYFILE_DISABLE=1 tar -C "$TMP_STAGE" "${TAR_FLAGS[@]}" -cf - "$PREFIX" | gzip -n -9 > "$ARCHIVE_PATH"

# Atomic SHA256SUMS via mktemp + mv
TMP_SUMS="$(mktemp "$OUT_DIR/SHA256SUMS.XXXXXX")"
(
    cd "$OUT_DIR"
    shopt -s nullglob
    ARTIFACTS=(macubuntu-v*.tar.gz macubuntu_*.deb)
    if [[ ${#ARTIFACTS[@]} -gt 0 ]]; then
        sha256sum "${ARTIFACTS[@]}" > "$TMP_SUMS"
        sha256sum -c "$TMP_SUMS"
    fi
)
mv -f "$TMP_SUMS" "$SUMS_PATH"

SIZE_HUMAN="$(du -h "$ARCHIVE_PATH" | awk '{print $1}')"
FILE_COUNT="$(tar -tzf "$ARCHIVE_PATH" | wc -l | tr -d ' ')"
echo "✅ Release packaged & verified:"
echo "   Archive: $ARCHIVE_PATH ($SIZE_HUMAN, $FILE_COUNT entries)"
echo "   SHA-256: $(cat "$SUMS_PATH")"