#!/usr/bin/env bash
# ============================================================================
# scripts/pack-update.sh — Signed .mubundle packager
#
# Usage: ./scripts/pack-update.sh <version> <stages_csv> <payload_dir> <privkey.pem> <out.mubundle>
#
# Builds a signed, zstd-compressed tarball containing:
#   payload/                (files to rsync into REPO_ROOT)
#   update_manifest.json    (version, target_stages, changelog)
#   SHA256SUMS              (sha256 of payload + manifest)
#   SHA256SUMS.sig          (Ed25519/RSA signature of SHA256SUMS)
# ============================================================================
set -euo pipefail

VER="$1"
STAGES_CSV="$2"
PAYLOAD_DIR="$3"
PRIVKEY="$4"
OUT="$5"

[[ -n "$VER" && -n "$STAGES_CSV" && -n "$PAYLOAD_DIR" && -n "$PRIVKEY" && -n "$OUT" ]] || {
    echo "Usage: $0 <version> <stages_csv> <payload_dir> <privkey.pem> <out.mubundle>" >&2
    exit 2
}
[[ -f "$PRIVKEY" ]] || { echo "Private key not found: $PRIVKEY" >&2; exit 2; }
[[ -d "$PAYLOAD_DIR" ]] || { echo "Payload dir not found: $PAYLOAD_DIR" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp -a "$PAYLOAD_DIR" "$TMP/payload"
STAGES_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1].split(",")))' "$STAGES_CSV")"

cat <<EOF > "$TMP/update_manifest.json"
{"version": "$VER", "target_stages": $STAGES_JSON, "changelog": "macubuntu $VER update"}
EOF

(cd "$TMP" && find payload update_manifest.json -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
openssl pkeyutl -sign -inkey "$PRIVKEY" -rawin -in "$TMP/SHA256SUMS" -out "$TMP/SHA256SUMS.sig"

tar --zstd -cf "$OUT" -C "$TMP" .
echo "Created signed bundle: $OUT"
