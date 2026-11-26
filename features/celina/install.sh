#!/usr/bin/env bash
#
# Celina v4.4 — Installer
# Installs Celina into a Python venv, creates systemd services and helper scripts.
# Usage: bash install.sh [--fail-fast] [--dry-run]
#
set -uo pipefail

CELINA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${CELINA_DIR}/.."
CELINA_VENV="${HOME}/.local/share/celina/venv"
CELINA_BIN="${HOME}/.local/bin"
CELINA_PKG="${CELINA_DIR}/celina_pkg"
CELINA_APP="${HOME}/.local/share/celina/app"

FAIL_FAST=false
DRY_RUN="${DRY_RUN:-false}"
FAILURES=0

for arg in "$@"; do
    case "$arg" in
        --fail-fast) FAIL_FAST=true ;;
        --dry-run) DRY_RUN=true ;;
        --help|-h)
            echo "Usage: $0 [--fail-fast] [--dry-run]"
            echo "  --fail-fast   Stop on first error (default: continue on error)"
            echo "  --dry-run     Preview changes without applying"
            exit 0
            ;;
    esac
done

log()  { echo "[celina-install] $*"; }
fail() { echo "[celina-install] ERROR: $*" >&2; FAILURES=$((FAILURES + 1)); if [ "$FAIL_FAST" = true ]; then exit 1; fi; }

# is_true() fallback for standalone mode (when not sourced from core/common.sh)
if ! declare -f is_true >/dev/null 2>&1; then
    is_true() {
        case "$1" in
            true|True|TRUE|1|yes|Yes|YES) return 0 ;;
            *) return 1 ;;
        esac
    }
fi

run() {
    if [ "$DRY_RUN" = true ]; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

# ------------------------------------------------------------------
# 0. Validate environment
# ------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not found"
    exit 1
fi

# ------------------------------------------------------------------
# 1. Create Python venv
# ------------------------------------------------------------------
log "Creating Python venv at ${CELINA_VENV}..."
run python3 -m venv --system-site-packages "${CELINA_VENV}" || fail "Failed to create venv"

# ------------------------------------------------------------------
# 2. Install core dependencies (with hash pinning when lockfile present)
# ------------------------------------------------------------------
log "Installing core dependencies..."

PIP="${CELINA_VENV}/bin/pip"
PIP_INSTALL_ARGS=()

if [[ -f "${CELINA_DIR}/requirements.lock" ]]; then
    log "Hash-pinned install detected: enforcing --require-hashes from requirements.lock"
    PIP_INSTALL_ARGS+=(--require-hashes)
    run "${PIP}" install --upgrade pip "${PIP_INSTALL_ARGS[@]}" || fail "pip upgrade failed"
    run "${PIP}" install -r "${CELINA_DIR}/requirements.lock" || fail "Core requirements (locked) install failed"
else
    log "No requirements.lock — installing from requirements.txt (unpinned)"
    run "${PIP}" install --upgrade pip || fail "pip upgrade failed"
    run "${PIP}" install -r "${CELINA_DIR}/requirements.txt" || fail "Core requirements install failed"
fi

# ------------------------------------------------------------------
# 3. Install optional dependencies (only when feature-enabled)
# ------------------------------------------------------------------
# Optional deps are intentionally NOT installed by default. They are pulled
# in by Stage 70 when the corresponding ENABLE_* flag is true. Installing
# them unconditionally would pull PyTorch (sentence-transformers), GPIO libs
# (gpiozero), and compile-from-source crates (webrtcvad, pyaudio, dbus-python,
# llama-cpp-python) that contradict the 8 GB design.
if is_true "${ENABLE_OPTIONAL_DEPS:-false}"; then
    log "Installing optional dependencies from requirements-optional.txt (ENABLE_OPTIONAL_DEPS=true)..."
    if [[ -f "${CELINA_DIR}/requirements-optional.lock" ]]; then
        PIP_INSTALL_ARGS=(--require-hashes)
        run "${PIP}" install -r "${CELINA_DIR}/requirements-optional.lock" "${PIP_INSTALL_ARGS[@]}" || fail "Optional requirements (locked) install failed"
    else
        run "${PIP}" install -r "${CELINA_DIR}/requirements-optional.txt" || fail "Optional requirements install failed"
    fi
else
    log "Skipping optional dependencies (set ENABLE_OPTIONAL_DEPS=true to install)"
fi

# ------------------------------------------------------------------
# 4. Install Celina package into app dir (not editable — repo-relative paths break on move)
# ------------------------------------------------------------------
log "Installing Celina package into ${CELINA_APP}..."
run mkdir -p "${CELINA_APP}"
# Copy source tree (not symlink — editable installs break when the repo moves)
run cp -r "${CELINA_DIR}/celina_pkg" "${CELINA_APP}/"
run cp -r "${CELINA_DIR}/celina_hardware" "${CELINA_APP}/"
run cp -r "${CELINA_DIR}/config" "${CELINA_APP}/"

# Verify the copy worked
run "${CELINA_VENV}/bin/python" -P -c "import sys; sys.path.insert(0, '${CELINA_APP}'); import celina_pkg.router; print('celina_pkg.router: OK')" || fail "Import verification failed"

# ------------------------------------------------------------------
# 5. Create helper scripts (all use python -P to prevent cwd import hijacking)
# ------------------------------------------------------------------
run mkdir -p "${CELINA_BIN}"

WRAPPER_PYTHON="${CELINA_VENV}/bin/python -P"

make_wrapper() {
    local name="$1" module="$2"
    log "Creating helper script: ${name}"
    run tee "${CELINA_BIN}/${name}" >/dev/null <<'EOF'
#!/usr/bin/env bash
exec "{{VENV_PYTHON}}" -m "{{MODULE}}" "$@"
EOF
    # Replace placeholders with actual values (safe: no $@ expansion in heredoc)
    run sed -i "s|{{VENV_PYTHON}}|${WRAPPER_PYTHON}|g; s|{{MODULE}}|${module}|g" "${CELINA_BIN}/${name}"
    run chmod +x "${CELINA_BIN}/${name}"
}

make_wrapper celina-island celina_pkg.island_ui
make_wrapper celina-dbus celina_pkg.dbus_service
make_wrapper celina-npu-runner celina_pkg.neural.npu_worker
make_wrapper celina-memory celina_pkg.memory
make_wrapper celina celina_pkg.router
make_wrapper celina-autonomy celina_pkg.router   # autonomy subcommand handled by router
make_wrapper celina-writing-tools celina_pkg.router
make_wrapper celina-live-text celina_pkg.router

# Symlink celly → celina
run ln -sf celina "${CELINA_BIN}/celly" 2>/dev/null || run cp "${CELINA_BIN}/celina" "${CELINA_BIN}/celly"

# Missing wrappers (docs reference them)
make_wrapper celina-voice celina_pkg.voice
make_wrapper celina-spotlight celina_pkg.spotlight_index
make_wrapper celina-train celina_pkg.everyday_trainer
make_wrapper celina-turbo celina_pkg.turbo_mode
make_wrapper celina-login-save celina_pkg.keychain_login

# celina-ocr: no dedicated module found; create a thin wrapper to router
make_wrapper celina-ocr celina_pkg.router

# ------------------------------------------------------------------
# 6. Create systemd user services
# ------------------------------------------------------------------
SYSTEMD_DIR="${HOME}/.config/systemd/user"
run mkdir -p "${SYSTEMD_DIR}"

log "Installing systemd user units (hardened: NoNewPrivileges, UMask=0077, seccomp)..."
for unit in celina celina-eventd celina-daemon; do
    src="${CELINA_DIR}/system/systemd/${unit}.service"
    if [[ -f "$src" ]]; then
        sed -e "s|%h|${HOME}|g" \
            -e "s|%h/.local/share/celina/venv/bin/python3|${CELINA_VENV}/bin/python|g" \
            "$src" > "${SYSTEMD_DIR}/${unit}.service"
        run chmod 644 "${SYSTEMD_DIR}/${unit}.service"
    else
        if [ "$DRY_RUN" = true ]; then
            log "[dry-run] would install systemd unit: ${unit}.service (source missing)"
        else
            fail "Missing systemd unit: ${unit}.service"
        fi
    fi
done

# System-level services (e.g. ollama.service) installed under /etc must be 0644,
# not 0600, even when the installer runs with umask 077.
run chmod 644 /etc/systemd/system/ollama.service 2>/dev/null || true

# Reload user systemd to pick up new units (non-fatal if no user bus yet)
if [ "$DRY_RUN" != true ]; then
    systemctl --user daemon-reload 2>/dev/null || log "Note: user systemd daemon-reload skipped (no user bus)"
fi

# ------------------------------------------------------------------
# 6b. Remaining CLI wrappers are created above in make_wrapper loop
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# 6c. Provision ~/.config/celina/ (0700) and private config files (0600)
#     Without clobbering existing user files on upgrade.
# ------------------------------------------------------------------
CELINA_CONFIG_DIR="${HOME}/.config/celina"
run mkdir -p -m 700 "${CELINA_CONFIG_DIR}"

# Copy env.template -> env ONLY if absent (never clobber user secrets)
if [[ ! -f "${CELINA_CONFIG_DIR}/env" ]]; then
    if [[ -f "${CELINA_DIR}/config/env.template" ]]; then
        run install -m 600 "${CELINA_DIR}/config/env.template" "${CELINA_CONFIG_DIR}/env"
    fi
fi

# Deploy JSON configs (preserving user overrides on upgrade)
for cfg in neural-schema.json mcp-servers.json iot-allowlist.json; do
    src="${CELINA_DIR}/config/${cfg}"
    dst="${CELINA_CONFIG_DIR}/${cfg}"
    if [[ -f "$src" ]]; then
        if [[ ! -f "$dst" ]]; then
            run install -m 600 "$src" "$dst"
        fi
    fi
done

# Ensure data dir exists (0700)
run mkdir -p -m 700 "${HOME}/.local/share/celina"

# ------------------------------------------------------------------
# 7. Verify installation
# ------------------------------------------------------------------
log "Verifying installation..."
run "${CELINA_VENV}/bin/python" -P -c "import sys; sys.path.insert(0, '${CELINA_APP}'); import celina_pkg.router; print('celina_pkg.router: OK')" || fail "Import verification failed"
run "${CELINA_VENV}/bin/python" -P -c "import sys; sys.path.insert(0, '${CELINA_APP}'); from celina_pkg.gear0_5_smolLM import gear0_5_smolLM_available; print('gear0_5_smolLM: OK')" || fail "smolLM import verification failed"

# ------------------------------------------------------------------
# 8. Exit with proper code
# ------------------------------------------------------------------
if [ "${FAILURES}" -gt 0 ]; then
    log "Celina installation completed with ${FAILURES} error(s)."
    exit 1
fi

log "Celina installation complete."
log "Ven: ${CELINA_VENV}"
log "App: ${CELINA_APP}"
log "Services: ${SYSTEMD_DIR}/celina.service, ${SYSTEMD_DIR}/celina-eventd.service, ${SYSTEMD_DIR}/celina-daemon.service"
log "Helper scripts: ${CELINA_BIN}/celina-{island,dbus,npu-runner,memory,autonomy,writing-tools,live-text,voice,spotlight,train,turbo,login-save,ocr}, ${CELINA_BIN}/celly"
log "Config: ${CELINA_CONFIG_DIR}/ (0700), configs 0600"
log ""
log "To start: systemctl --user start celina.service celina-eventd.service"
log "On upgrade: systemctl --user restart celina-eventd.service"
