#!/usr/bin/env bash
# ============================================================================
# build-release.sh — Create signed release manifest (SHA-256 + optional GPG)
#
# Produces: dist/release-<semver>.manifest.json + .sha256 + optional .asc
#
# The manifest lists every distributable artifact with its SHA-256 hash,
# enabling end-to-end verification of upgrade + rollback drills (P2 gate).
#
# Exit gate: manifest is valid JSON, all listed hashes verify,
# and upgrade/rollback drill passes offline.
# ============================================================================
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

if command -v python3 >/dev/null 2>&1; then
    RELEASE_SEMVER="$(python3 -c "
import tomllib
with open('version.toml','rb') as fh:
    print(tomllib.load(fh)['release']['semver'])
")"
else
    echo "❌ python3 required" >&2
    exit 1
fi

DIST_DIR="$REPO_DIR/dist"
MANIFEST="$DIST_DIR/release-$RELEASE_SEMVER.manifest.json"

mkdir -p "$DIST_DIR"

# Collect all distributable artifacts
ARTIFACTS=(
    "celina-$RELEASE_SEMVER.pyz"
    "celina.service"
)

# Build the manifest
python3 - "$DIST_DIR" "$MANIFEST" "$RELEASE_SEMVER" << 'PYEOF'
import sys
import json
import hashlib
import os
from pathlib import Path

dist_dir = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
semver = sys.argv[3]

artifacts = {}
for name in ("celina-{}.pyz".format(semver), "celina.service"):
    p = dist_dir / name
    if p.is_file():
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        size = p.stat().st_size
        artifacts[name] = {
            "sha256": h,
            "size_bytes": size,
        }

manifest = {
    "schema": "celina.release.v1",
    "release_semver": semver,
    "created_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    "default_policy": "deny",
    "artifacts": artifacts,
}

manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(f"✅ Wrote {manifest_path} with {len(artifacts)} artifacts")
PYEOF

# Write .sha256 for the manifest itself
sha256sum "$MANIFEST" > "$MANIFEST.sha256"
echo "✅ SHA-256 written to $MANIFEST.sha256"

# Optional GPG detached signature
if command -v gpg >/dev/null 2>&1 && gpg --list-secret-keys >/dev/null 2>&1; then
    gpg --detach-sign --armor "$MANIFEST"
    echo "✅ GPG detached signature written to $MANIFEST.asc"
else
    echo "ℹ️  No GPG key found — skipping detached signature (install without --verify-sig, or import a key)"
fi

# Verify all hashes in the manifest
python3 - "$MANIFEST" << 'PYEOF'
import sys
import json
import hashlib
from pathlib import Path

manifest_path = Path(sys.argv[1])
manifest = json.loads(manifest_path.read_text())
dist_dir = manifest_path.parent

all_ok = True
for name, info in manifest["artifacts"].items():
    p = dist_dir / name
    if not p.is_file():
        print(f"❌ Missing artifact: {name}")
        all_ok = False
        continue
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if h != info["sha256"]:
        print(f"❌ Hash mismatch for {name}: expected {info['sha256'][:16]}..., got {h[:16]}...")
        all_ok = False
    else:
        print(f"✅ {name}: hash verified")

if not all_ok:
    sys.exit(1)
print("✅ All artifact hashes verified")
PYEOF

exit 0
