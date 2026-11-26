#!/usr/bin/env bash
# ============================================================================
# macubuntu — Native Debian (.deb) Package Builder (v4.4.0)
#
# Assembles a standards-compliant FHS Debian package (`macubuntu_<ver>_all.deb`)
# shipping:
#   - /usr/lib/systemd/user/{celina,celina-eventd,celina-npu-inference}.service
#   - /usr/lib/systemd/system/celina-ebpf-sensor.service (privilege-separated)
#   - /etc/dbus-1/session.d/org.macubuntu.Celina.conf + /usr/share/dbus-1/services/
#   - /usr/share/gnome-shell/extensions/celina-island@macubuntu.org/ (with compiled GSettings)
#   - /usr/share/macubuntu/ (canonical payload + CO-RE eBPF source + capability manifest)
#   - /usr/bin/{macubuntu-install,macubuntu-doctor,macubuntu-compute,celina-dbus}
#   - DEBIAN/{control,conffiles,postinst,prerm,postrm}
#
# Usage:
#   ./tools/build-deb.sh [--out <dir>] [--version <ver>] [--dry-run]
# ============================================================================
set -Eeuo pipefail
umask 022

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_DIR/releases"
DRY_RUN=0

# --- Default version from version.toml (single source of truth) ---------------
# [celina].version takes priority; falls back to VERSION file; then hardcoded.
DEB_VERSION_DEFAULT="4.4.0"
if [[ -f "$REPO_DIR/version.toml" ]]; then
    toml_ver="$(grep -E '^version\s*=' "$REPO_DIR/version.toml" | head -n1 | sed -E 's/^version\s*=\s*"([^"]+)".*/\1/' || true)"
    [[ -n "$toml_ver" ]] && DEB_VERSION_DEFAULT="$toml_ver"
elif [[ -f "$REPO_DIR/VERSION" ]]; then
    DEB_VERSION_DEFAULT="$(cat "$REPO_DIR/VERSION" | tr -d '[:space:]' || echo "$DEB_VERSION_DEFAULT")"
fi
VERSION="$DEB_VERSION_DEFAULT"

usage() {
    cat <<'EOF'
Usage: ./tools/build-deb.sh [options]

Options:
  --out <dir>       Output directory for macubuntu_<ver>_all.deb (default: releases/)
  --version <ver>   Override package version (default: contents of version.toml)
  --dry-run         Validate inputs and print planned package layout without building
  -h, --help        Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)
            if [[ $# -lt 2 ]]; then echo "❌ --out requires a directory argument" >&2; usage; exit 2; fi
            OUT_DIR="$2"; shift 2 ;;
        --version)
            if [[ $# -lt 2 ]]; then echo "❌ --version requires a version argument" >&2; usage; exit 2; fi
            VERSION="$2"; shift 2 ;;
        --dry-run)  DRY_RUN=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          echo "❌ Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

# Validate Debian version syntax
if [[ ! "$VERSION" =~ ^[0-9]+(:[0-9]+)?(\.[0-9]+)*([a-zA-Z0-9.+~.-]*)?$ ]]; then
    echo "❌ Invalid Debian version string: '$VERSION'" >&2
    exit 2
fi

PKG_NAME="macubuntu"
ARCH="all"
DEB_FILE="${PKG_NAME}_${VERSION}_${ARCH}.deb"

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] Would build ${OUT_DIR}/${DEB_FILE}"
    echo "[dry-run] Units:  /usr/lib/systemd/user/{celina,celina-eventd,celina-npu-inference}.service"
    echo "[dry-run] eBPF:   /usr/lib/systemd/system/celina-ebpf-sensor.service"
    echo "[dry-run] D-Bus:  /etc/dbus-1/session.d/org.macubuntu.Celina.conf + /usr/share/dbus-1/services/org.macubuntu.Celina.service"
    echo "[dry-run] GNOME:  /usr/share/gnome-shell/extensions/celina-island@macubuntu.org (compiled GSettings)"
    echo "[dry-run] CLIs:   /usr/bin/{macubuntu-install,macubuntu-doctor,macubuntu-compute,celina-dbus}"
    exit 0
fi

for req in dpkg-deb glib-compile-schemas sha256sum; do
    if ! command -v "$req" >/dev/null 2>&1; then
        echo "❌ Missing required packaging tool: $req" >&2
        exit 1
    fi
done

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
DEB_PATH="$OUT_DIR/$DEB_FILE"

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/macubuntu-deb.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT
STAGE="$TMP_ROOT/${PKG_NAME}_${VERSION}_${ARCH}"

mkdir -p \
    "$STAGE/DEBIAN" \
    "$STAGE/etc/dbus-1/session.d" \
    "$STAGE/usr/bin" \
    "$STAGE/usr/lib/systemd/user/celina.service.d" \
    "$STAGE/usr/lib/systemd/system" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/dbus-1/services" \
    "$STAGE/usr/share/gnome-shell/extensions" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" \
    "$STAGE/usr/share/macubuntu" \
    "$STAGE/usr/share/doc/macubuntu"

# 1. Systemd user & system units (split micro-daemons + v5 autonomy/proactive/nightly
#    + decoupled NPU + isolated eBPF + local-inference.slice + auto-theme/borg timers)
USER_UNITS=(
    celina.service celina-eventd.service celina-router.service celina-voice.service celina-memory.service
    celina-npu-inference.service celina-autonomy.service celina-proactive.service celina-self-improve.service
    celina-self-improve.timer celina-mcp-n8n.service celina-auto-theme.service celina-auto-theme.timer
    celina-borg-backup.service celina-borg-backup.timer celina-daemon.service
)
for unit in "${USER_UNITS[@]}"; do
    if [[ -f "$REPO_DIR/system/systemd/$unit" ]]; then
        install -m 0644 "$REPO_DIR/system/systemd/$unit" \
            "$STAGE/usr/lib/systemd/user/$unit"
    fi
done
# celina-sandbox.slice (system slice)
if [[ -f "$REPO_DIR/system/systemd/celina-sandbox.slice" ]]; then
    install -m 0644 "$REPO_DIR/system/systemd/celina-sandbox.slice" \
        "$STAGE/usr/lib/systemd/system/celina-sandbox.slice"
fi
install -m 0644 "$REPO_DIR/system/systemd/local-inference.slice" \
    "$STAGE/usr/lib/systemd/system/local-inference.slice"
install -m 0644 "$REPO_DIR/system/systemd/celina-strict-fs.conf" \
    "$STAGE/usr/lib/systemd/user/celina.service.d/celina-strict-fs.conf.example"
install -m 0644 "$REPO_DIR/system/ebpf/celina-ebpf-sensor.service" \
    "$STAGE/usr/lib/systemd/system/celina-ebpf-sensor.service"
install -d -m 0755 "$STAGE/usr/share/polkit-1/rules.d"
install -m 0644 "$REPO_DIR/system/polkit/99-scx-loader.rules" \
    "$STAGE/usr/share/polkit-1/rules.d/99-scx-loader.rules"

# 2. D-Bus session policy (Tier A + isolated Tier B) + activation
install -m 0644 "$REPO_DIR/system/dbus/org.macubuntu.Celina.conf" \
    "$STAGE/etc/dbus-1/session.d/org.macubuntu.Celina.conf"
install -m 0644 "$REPO_DIR/system/dbus/org.macubuntu.Celina.TierB.conf" \
    "$STAGE/etc/dbus-1/session.d/org.macubuntu.Celina.TierB.conf"
install -m 0644 "$REPO_DIR/system/dbus/org.macubuntu.Celina.service" \
    "$STAGE/usr/share/dbus-1/services/org.macubuntu.Celina.service"

# 2b. Desktop launcher & scalable SVG icon
install -m 0644 "$REPO_DIR/system/icons/celina.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/celina.svg"
cat > "$STAGE/usr/share/applications/celina-island.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Celina Dynamic Island
Comment=AI-Native macOS-style Dynamic Island & OS Control Plane
Exec=/usr/bin/celina-dbus --toggle
Icon=celina
Terminal=false
Categories=Utility;System;
StartupNotify=false
EOF
chmod 0644 "$STAGE/usr/share/applications/celina-island.desktop"

# 3. Native GNOME Shell 48/49/50 ESM extension + compiled GSettings schema
EXT_SRC="$REPO_DIR/system/gnome/extensions/celina-island@macubuntu.org"
EXT_DST="$STAGE/usr/share/gnome-shell/extensions/celina-island@macubuntu.org"
cp -a "$EXT_SRC" "$EXT_DST"
glib-compile-schemas --strict "$EXT_DST/schemas"
find "$EXT_DST" -type d -exec chmod 0755 {} +
find "$EXT_DST" -type f -exec chmod 0644 {} +

# 4. Canonical repository payload under /usr/share/macubuntu
for item in VERSION version.toml install.sh uninstall.sh core stages system tools features docs profiles apps README.md ARCHITECTURE.md COMPATIBILITY.md LIMITATIONS.md CHANGELOG.md; do
    if [[ -e "$REPO_DIR/$item" ]]; then
        cp -a "$REPO_DIR/$item" "$STAGE/usr/share/macubuntu/$item"
    fi
done

# --- P0: Explicitly exclude Tier B, fault injectors, secrets, venv, AppleDouble ---
find "$STAGE/usr/share/macubuntu/tools" -name 'experimental-deep-stack.sh' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu/tools" -name 'inject_parity_break.py' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu/tools" -name 'requirements-tier-b.*' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '._*' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '.DS_Store' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '__MACOSX' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '.env*' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '*.key' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '*.pem' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '*.db*' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '*venv*' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '.venv' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '.pytest_cache' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -name '.ruff_cache' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -type f -name '*.pyc' -delete 2>/dev/null || true
find "$STAGE/usr/share/macubuntu" -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.ruff_cache' \) -prune -exec rm -rf {} + 2>/dev/null || true

chmod 0755 \
    "$STAGE/usr/share/macubuntu/install.sh" \
    "$STAGE/usr/share/macubuntu/uninstall.sh" \
    "$STAGE/usr/share/macubuntu/features/celina/install.sh" \
    "$STAGE/usr/share/macubuntu/stages/"*.sh \
    "$STAGE/usr/share/macubuntu/tools/"*.sh \
    "$STAGE/usr/share/macubuntu/core/"*.sh

# --- P0/P1: Extensionless executables and *.py get 0755 -----------------------
for ext_bin in celina-vmtouch-ctl macubuntu-reconfigure macubuntu-ram-change-detector celina-visual-intelligence; do
    [[ -f "$STAGE/usr/share/macubuntu/tools/$ext_bin" ]] && chmod 0755 "$STAGE/usr/share/macubuntu/tools/$ext_bin"
done
find "$STAGE/usr/share/macubuntu/tools" -name '*.py' -exec chmod 0755 {} + 2>/dev/null || true

# 5. System CLI wrappers under /usr/bin
cat > "$STAGE/usr/bin/macubuntu-install" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/share/macubuntu/install.sh "$@"
EOF

cat > "$STAGE/usr/bin/macubuntu-doctor" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/share/macubuntu/tools/doctor.sh "$@"
EOF

cat > "$STAGE/usr/bin/macubuntu-compute" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/share/macubuntu/tools/detect-compute.sh "$@"
EOF

cat > "$STAGE/usr/bin/macubuntu-os-hardening" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/share/macubuntu/tools/os-hardening-setup.sh "$@"
EOF

# --- P0: Never fall back $HOME to /tmp in wrappers; use system paths -----------
cat > "$STAGE/usr/bin/celina-dbus" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ -z "${HOME:-}" || "${HOME:-}" == "/tmp" ]]; then
    echo "❌ celina-dbus: HOME is unset or /tmp; refusing to run (P0-3)" >&2
    exit 2
fi
VENV_PY="${HOME}/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
USER_APP="${HOME}/.local/share/celina/app"
if [[ -d "$USER_APP/celina_pkg" ]]; then
    export PYTHONPATH="$USER_APP${PYTHONPATH:+:$PYTHONPATH}"
else
    export PYTHONPATH="/usr/share/macubuntu/features/celina${PYTHONPATH:+:$PYTHONPATH}"
fi
exec "$VENV_PY" -m celina_pkg.dbus_service "$@"
EOF

cat > "$STAGE/usr/bin/celina-autonomy" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ -z "${HOME:-}" || "${HOME:-}" == "/tmp" ]]; then
    echo "❌ celina-autonomy: HOME is unset or /tmp; refusing to run (P0-3)" >&2
    exit 2
fi
VENV_PY="${HOME}/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
USER_APP="${HOME}/.local/share/celina/app"
if [[ -d "$USER_APP/celina_pkg" ]]; then
    export PYTHONPATH="$USER_APP${PYTHONPATH:+:$PYTHONPATH}"
else
    export PYTHONPATH="/usr/share/macubuntu/features/celina${PYTHONPATH:+:$PYTHONPATH}"
fi
exec "$VENV_PY" -m celina_pkg.autonomy.engine "$@"
EOF

cat > "$STAGE/usr/bin/celina-proactive" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ -z "${HOME:-}" || "${HOME:-}" == "/tmp" ]]; then
    echo "❌ celina-proactive: HOME is unset or /tmp; refusing to run (P0-3)" >&2
    exit 2
fi
VENV_PY="${HOME}/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
USER_APP="${HOME}/.local/share/celina/app"
if [[ -d "$USER_APP/celina_pkg" ]]; then
    export PYTHONPATH="$USER_APP${PYTHONPATH:+:$PYTHONPATH}"
else
    export PYTHONPATH="/usr/share/macubuntu/features/celina${PYTHONPATH:+:$PYTHONPATH}"
fi
exec "$VENV_PY" -m celina_pkg.proactive.daemon "$@"
EOF

cat > "$STAGE/usr/bin/celina-mcp-server" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ -z "${HOME:-}" || "${HOME:-}" == "/tmp" ]]; then
    echo "❌ celina-mcp-server: HOME is unset or /tmp; refusing to run (P0-3)" >&2
    exit 2
fi
VENV_PY="${HOME}/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
USER_APP="${HOME}/.local/share/celina/app"
if [[ -d "$USER_APP/celina_pkg" ]]; then
    export PYTHONPATH="$USER_APP${PYTHONPATH:+:$PYTHONPATH}"
else
    export PYTHONPATH="/usr/share/macubuntu/features/celina${PYTHONPATH:+:$PYTHONPATH}"
fi
exec "$VENV_PY" -m celina_pkg.mcp_server "$@"
EOF

cat > "$STAGE/usr/bin/celina-memory-mcp" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ -z "${HOME:-}" || "${HOME:-}" == "/tmp" ]]; then
    echo "❌ celina-memory-mcp: HOME is unset or /tmp; refusing to run (P0-3)" >&2
    exit 2
fi
VENV_PY="${HOME}/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
USER_APP="${HOME}/.local/share/celina/app"
if [[ -d "$USER_APP/celina_pkg" ]]; then
    export PYTHONPATH="$USER_APP${PYTHONPATH:+:$PYTHONPATH}"
else
    export PYTHONPATH="/usr/share/macubuntu/features/celina${PYTHONPATH:+:$PYTHONPATH}"
fi
exec "$VENV_PY" -m celina_pkg.memory_mcp_server "$@"
EOF

chmod 0755 \
    "$STAGE/usr/bin/macubuntu-install" \
    "$STAGE/usr/bin/macubuntu-doctor" \
    "$STAGE/usr/bin/macubuntu-compute" \
    "$STAGE/usr/bin/macubuntu-os-hardening" \
    "$STAGE/usr/bin/celina-dbus" \
    "$STAGE/usr/bin/celina-autonomy" \
    "$STAGE/usr/bin/celina-proactive" \
    "$STAGE/usr/bin/celina-mcp-server" \
    "$STAGE/usr/bin/celina-memory-mcp"

# 6. Documentation & Debian copyright
for doc in README.md ARCHITECTURE.md COMPATIBILITY.md LIMITATIONS.md CHANGELOG.md; do
    [[ -f "$REPO_DIR/$doc" ]] && install -m 0644 "$REPO_DIR/$doc" "$STAGE/usr/share/doc/macubuntu/$doc"
done
cat > "$STAGE/usr/share/doc/macubuntu/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: macubuntu
Source: https://github.com/macubuntu/macubuntu

Files: *
Copyright: 2024-2026 macubuntu Contributors
License: MIT

Files: system/ebpf/celina_sensor.bpf.c
Copyright: 2024-2026 macubuntu Contributors
License: GPL-2.0 or MIT
EOF
chmod 0644 "$STAGE/usr/share/doc/macubuntu/copyright"

# 7. DEBIAN metadata & maintainer scripts
INSTALLED_KB="$(du -sk "$STAGE/usr" "$STAGE/etc" | awk '{sum += $1} END {print sum}')"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Maintainer: macubuntu Maintainers <maintainers@macubuntu.org>
Installed-Size: ${INSTALLED_KB}
Depends: bash (>= 5.0), python3 (>= 3.10), python3-defusedxml, systemd, dbus, libglib2.0-bin, bubblewrap, jq, curl
Recommends: gnome-shell (>= 48), python3-gi, gir1.2-gtk-3.0, gir1.2-gtk-4.0, gir1.2-adw-1, python3-yaml, python3-jsonschema, python3-httpx, tesseract-ocr, wl-clipboard, pipewire
Suggests: clang, bpftool, libbpf-dev, v4l2loopback-dkms
Conflicts: macubuntu-old
Replaces: macubuntu-old
Homepage: https://github.com/macubuntu/macubuntu
Description: AI-Native macOS-style Ubuntu Desktop & Celina OS Control Plane
 macubuntu transforms Ubuntu 24.04–26.04 (GNOME 48/49/50/51, Wayland) into a
 hardened AI-native desktop integrating the Celina assistant directly with
 Linux OS primitives:
  * Split systemd user micro-daemons (celina, celina-eventd, celina-router,
    celina-voice, celina-memory, decoupled celina-npu-inference)
  * Session D-Bus API (org.macubuntu.Celina + isolated Tier B bus policy)
  * Native GNOME Shell 48–51 ESM Dynamic Island extension + Adw preferences
  * Modular CO-RE eBPF sensors & opt-in signed BPF LSM Focus Mode enforcer
  * NPU (/dev/accel), Strix Halo ROCm (gfx1151) & Vulkan/CUDA compute offload
EOF
chmod 0644 "$STAGE/DEBIAN/control"

cat > "$STAGE/DEBIAN/conffiles" <<'EOF'
/etc/dbus-1/session.d/org.macubuntu.Celina.conf
/etc/dbus-1/session.d/org.macubuntu.Celina.TierB.conf
EOF
chmod 0644 "$STAGE/DEBIAN/conffiles"

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

case "$1" in
    configure)
        if command -v glib-compile-schemas >/dev/null 2>&1 && \
           [ -d /usr/share/gnome-shell/extensions/celina-island@macubuntu.org/schemas ]; then
            glib-compile-schemas --strict /usr/share/gnome-shell/extensions/celina-island@macubuntu.org/schemas || true
        fi

        if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -d /usr/share/icons/hicolor ]; then
            gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
        fi

        if command -v update-desktop-database >/dev/null 2>&1 && [ -d /usr/share/applications ]; then
            update-desktop-database -q /usr/share/applications || true
        fi

        if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
            systemctl daemon-reload >/dev/null 2>&1 || true
            systemctl --global enable celina.service celina-eventd.service celina-memory.service >/dev/null 2>&1 || true
        fi

        echo "macubuntu installed cleanly."
        echo "  Next steps (as your desktop user):"
        echo "    1. macubuntu-install --user-only   # apply desktop theme, shortcuts & Celina venv"
        echo "    2. systemctl --user enable --now celina.service celina-eventd.service celina-memory.service"
        echo "    3. gnome-extensions enable celina-island@macubuntu.org"
        ;;
esac

exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst"

# --- P0/P1: prerm disables ALL packaged user units/timers ----------------------
cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e

case "$1" in
    remove|deconfigure)
        if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
            systemctl --global disable \
                celina.service celina-eventd.service celina-router.service \
                celina-voice.service celina-memory.service celina-npu-inference.service \
                celina-autonomy.service celina-proactive.service celina-self-improve.timer \
                celina-auto-theme.timer celina-borg-backup.timer celina-daemon.service \
                celina-mcp-n8n.service celina-self-improve.service >/dev/null 2>&1 || true
            systemctl --global stop \
                celina.service celina-eventd.service celina-router.service \
                celina-voice.service celina-memory.service celina-npu-inference.service \
                celina-autonomy.service celina-proactive.service celina-self-improve.timer \
                celina-auto-theme.timer celina-borg-backup.timer celina-daemon.service \
                celina-mcp-n8n.service celina-self-improve.service >/dev/null 2>&1 || true
            systemctl stop celina-ebpf-sensor.service >/dev/null 2>&1 || true
            systemctl disable celina-ebpf-sensor.service >/dev/null 2>&1 || true
        fi
        ;;
esac

exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/prerm"

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e

case "$1" in
    remove|purge)
        if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -d /usr/share/icons/hicolor ]; then
            gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
        fi
        if command -v update-desktop-database >/dev/null 2>&1 && [ -d /usr/share/applications ]; then
            update-desktop-database -q /usr/share/applications || true
        fi
        if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
            systemctl daemon-reload >/dev/null 2>&1 || true
        fi
        ;;
esac

exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postrm"

# 8. Build .deb reproducibly without requiring root/fakeroot
dpkg-deb --root-owner-group --build "$STAGE" "$DEB_PATH" >/dev/null

# 9. Verify package structure & atomically refresh SHA256SUMS
dpkg-deb --info "$DEB_PATH" >/dev/null
dpkg-deb --contents "$DEB_PATH" >/dev/null

SUMS_PATH="$OUT_DIR/SHA256SUMS"
TMP_SUMS="$(mktemp "$OUT_DIR/SHA256SUMS.XXXXXX")"
(
    cd "$OUT_DIR"
    shopt -s nullglob
    ARTIFACTS=(macubuntu-v*.tar.gz macubuntu_*.deb)
    if [[ ${#ARTIFACTS[@]} -gt 0 ]]; then
        sha256sum "${ARTIFACTS[@]}" > "$TMP_SUMS"
    fi
)
mv -f "$TMP_SUMS" "$SUMS_PATH"

echo "✅ Built verified Debian package: $DEB_PATH ($(du -h "$DEB_PATH" | cut -f1))"