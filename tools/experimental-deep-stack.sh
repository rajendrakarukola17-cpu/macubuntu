#!/usr/bin/env bash
# ============================================================================
# tools/experimental-deep-stack.sh — Opt-In Tier B Apple Protocol Research Sandbox
# Target: Ubuntu 26.04 LTS | Python 3.12+ | Strict 0700/0600 Isolation
#
# Architectural Firewall:
#   - NEVER called by install.sh or any stage in stages/.
#   - Uses supply-chain-pinned commits from system/download-locks.sha256 only.
#   - Supports true zero-side-effect --dry-run.
#   - Account-Safety: NEVER use your primary Apple ID with experimental GSA/FindMy
#     clients; always bind any local Anisette server strictly to 127.0.0.1!
# ============================================================================
set -Eeuo pipefail
umask 077

if [[ $EUID -eq 0 ]]; then
    echo "❌ Do NOT run as root. Execute as your normal desktop user." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_DIR

ASSUME_YES=0
GENERATE_TEST_KEYS=0
ALLOW_EXISTING_APPLE_SESSION=0

usage() {
    cat <<'EOF'
Usage: ./tools/experimental-deep-stack.sh [options]

Isolated opt-in Tier B sandbox (~/.local/share/celina/apple-research, mode 0700)
for studying open-source AWDL (owl), AirDrop (opendrop), and Find My (FindMy.py).

Options:
  --dry-run                       Preview every step with ZERO filesystem, APT, pip, or git mutations
  --yes, -y                       Skip interactive risk confirmation prompt
  --generate-test-keys            Also generate a local 0600 RSA-2048 SHA-256 test keypair & CSR
  --allow-existing-apple-session  Override pre-flight abort if Apple/iCloud keyring tokens are detected
  -h, --help                      Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)                      export DRY_RUN="1"; shift ;;
        --yes|-y)                       ASSUME_YES=1; shift ;;
        --generate-test-keys)           GENERATE_TEST_KEYS=1; shift ;;
        --allow-existing-apple-session) ALLOW_EXISTING_APPLE_SESSION=1; shift ;;
        -h|--help)                      usage; exit 0 ;;
        *)                              echo "❌ Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

check_no_primary_apple_id_tokens() {
    # Enforced account-safety guardrail: refuse to run Tier B if the current desktop
    # keyring or user profile already contains Apple ID / iCloud / GSA session artifacts.
    # Never prints secret values — checks presence only.
    local found_hits=()
    if command -v secret-tool >/dev/null 2>&1; then
        local out=""
        for kw in apple icloud grandslam anisette; do
            out="$(timeout 3 secret-tool search --all service "$kw" 2>/dev/null || true)"
            if [[ -n "$out" ]]; then
                found_hits+=("gnome-keyring(service=$kw)")
            fi
        done
        if [[ -z "${found_hits[*]:-}" ]]; then
            out="$(timeout 3 secret-tool search --all domain "apple.com" 2>/dev/null || true)"
            [[ -n "$out" ]] && found_hits+=("gnome-keyring(domain=apple.com)")
            out="$(timeout 3 secret-tool search --all domain "icloud.com" 2>/dev/null || true)"
            [[ -n "$out" ]] && found_hits+=("gnome-keyring(domain=icloud.com)")
        fi
    fi

    local p
    for p in "$HOME/.config/FindMy" "$HOME/.config/FindMy.py" "$HOME/.local/share/anisette" "$HOME/.config/apple-id"; do
        if [[ -e "$p" ]]; then
            found_hits+=("$p")
        fi
    done

    if [[ "${#found_hits[@]}" -gt 0 ]]; then
        echo "❌ ABORTED — Tier B Account-Safety Guardrail triggered!" >&2
        echo "   Detected existing Apple/iCloud session indicator(s): ${found_hits[*]}" >&2
        echo "   Running experimental GSA/Anisette/FindMy tools under a desktop profile with" >&2
        echo "   a primary Apple ID risks account challenges or lockouts." >&2
        echo "   Use a clean dedicated secondary Linux user / burner Apple ID, or pass" >&2
        echo "   --allow-existing-apple-session ONLY if you have verified this is a burner account." >&2
        return 1
    fi
    return 0
}

STACK_DIR="$HOME/.local/share/celina/apple-research"
VENV_DIR="$STACK_DIR/venv"
CRYPTO_DIR="$STACK_DIR/crypto"

section "TIER B: EXPERIMENTAL APPLE PROTOCOL RESEARCH SANDBOX (DRY_RUN=${DRY_RUN:-0})"
echo "   Target workspace: $STACK_DIR (mode 0700, strictly isolated from Tier A)"
echo "   WARNING: Never use your primary Apple ID with experimental GSA/FindMy clients."
echo "            Always use a dedicated secondary test Apple ID and localhost-only Anisette."

if [[ "$ALLOW_EXISTING_APPLE_SESSION" -eq 0 ]]; then
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "   [dry-run] Pre-flight guardrail: checking GNOME keyring & profile for primary Apple ID tokens..."
    fi
    check_no_primary_apple_id_tokens || exit 1
    echo "   ✅ Account-safety guardrail passed (no existing Apple/iCloud keyring tokens detected)."
else
    echo "   ⚠️  --allow-existing-apple-session override active; ensure only a secondary burner Apple ID is used."
fi

if [[ "${DRY_RUN:-0}" != "1" && "$ASSUME_YES" -eq 0 && -t 0 ]]; then
    read -r -p "Press [Enter] to continue into the isolated Tier B sandbox or [Ctrl+C] to abort..."
fi

# 1. Create private 0700 workspace directories
ensure_private_dir "$STACK_DIR" "$CRYPTO_DIR"

# 2. Install Ubuntu 26.04 C/PCAP/OpenSSL/Python build dependencies
step "[1/5] Ensuring Ubuntu 26.04 cryptographic, PCAP & Python research dependencies..."
apt_install_each \
    build-essential cmake pkg-config git curl \
    libssl-dev libpcap-dev libnl-3-dev libnl-genl-3-dev libev-dev \
    python3 python3-venv python3-pip python3-dev protobuf-compiler

# 3. Create isolated Python virtual environment (never touches ~/.local/share/celina/venv)
step "[2/5] Setting up isolated research Python venv ($VENV_DIR)..."
REQ_LOCK="$REPO_DIR/tools/requirements-tier-b.lock"
REQ_PIN="$REPO_DIR/tools/requirements-tier-b.txt"
if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "      [dry-run] python3 -m venv $VENV_DIR"
    if [[ -f "$REQ_LOCK" ]]; then
        echo "      [dry-run] $VENV_DIR/bin/pip install --require-hashes -r $REQ_LOCK"
    else
        echo "      [dry-run] $VENV_DIR/bin/pip install -r $REQ_PIN"
    fi
else
    if [[ ! -d "$VENV_DIR" ]]; then
        run python3 -m venv "$VENV_DIR"
    fi
    if [[ -f "$REQ_LOCK" ]]; then
        pip_install "$VENV_DIR/bin/pip" --require-hashes -r "$REQ_LOCK" -q
    else
        pip_install "$VENV_DIR/bin/pip" -r "$REQ_PIN" -q
    fi
fi

# 4. Optional local RSA-2048 SHA-256 test keypair & CSR (strictly opt-in, never impersonates Apple)
step "[3/5] Local protocol test keypair (opt-in via --generate-test-keys)..."
if [[ "$GENERATE_TEST_KEYS" -eq 1 ]]; then
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
        echo "      [dry-run] openssl genrsa -out $CRYPTO_DIR/research_local.key 2048 (chmod 600)"
        echo "      [dry-run] openssl req -new -sha256 -> $CRYPTO_DIR/research_local.csr (chmod 600)"
    else
        if [[ ! -f "$CRYPTO_DIR/research_local.key" ]]; then
            openssl genrsa -out "$CRYPTO_DIR/research_local.key" 2048 >/dev/null 2>&1
            chmod 600 "$CRYPTO_DIR/research_local.key"
        fi
        openssl req -new -key "$CRYPTO_DIR/research_local.key" -sha256 \
            -out "$CRYPTO_DIR/research_local.csr" \
            -subj "/CN=macubuntu-local-research-lab"
        chmod 600 "$CRYPTO_DIR/research_local.csr"
    fi
else
    echo "      Skipped (pass --generate-test-keys if you need a local RSA-2048 SHA-256 test keypair)."
fi

# 5. Clone real, cryptographically pinned research repositories from system/download-locks.sha256
step "[4/5] Fetching pinned open-source research repos (owl, opendrop, findmy-py)..."
clone_pinned_repo "owl" "$STACK_DIR/owl" || record_manual "Clone owl manually if offline"
clone_pinned_repo "opendrop" "$STACK_DIR/opendrop" || record_manual "Clone opendrop manually if offline"
clone_pinned_repo "findmy-py" "$STACK_DIR/FindMy.py" || record_manual "Clone FindMy.py manually if offline"

step "[5/5] Writing localhost-hardened quick reference ($STACK_DIR/README-USAGE.txt)..."
write_user_file "$STACK_DIR/README-USAGE.txt" 600 <<'EOF'
====================================================================
MACUBUNTU TIER B — APPLE PROTOCOL RESEARCH SANDBOX (LOCALHOST ONLY)
====================================================================
1. Activate isolated research Python venv:
   source ~/.local/share/celina/apple-research/venv/bin/activate

2. Optional local Anisette v3 container — STRICTLY localhost-bound (127.0.0.1!) & digest-pinned:
   docker run -d --restart unless-stopped --name anisette-v3 \
       -p 127.0.0.1:6969:6969 \
       dadoum/anisette-v3-server@sha256:d551c75454bcbee68dc262d4247d5f87fa309dfc8f2125644d333c648596e3c7
   (Digest recorded in system/download-locks.sha256 as 'anisette-v3-server'; NEVER publish on 0.0.0.0!)

3. Inspect BLE Find My research library (use a secondary test Apple ID ONLY):
   cd ~/.local/share/celina/apple-research/FindMy.py

4. Inspect AWDL (owl, archived research repo) & AirDrop (opendrop):
   Requires a dedicated monitor-mode + frame-injection Wi-Fi adapter.
   cd ~/.local/share/celina/apple-research/owl
   cd ~/.local/share/celina/apple-research/opendrop

5. Clean removal anytime:
   ./uninstall.sh --purge-data   (or: rm -rf ~/.local/share/celina/apple-research)
====================================================================
EOF

echo "✅ Tier B research sandbox ready at $STACK_DIR (DRY_RUN=${DRY_RUN:-0})."
