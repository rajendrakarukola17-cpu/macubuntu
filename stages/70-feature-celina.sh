#!/usr/bin/env bash
# ============================================================================
# Stage 70: Built-In Feature — Celina AI (Siri / Apple Intelligence Subsystem)
# ============================================================================
set -Eeuo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

section "[70] Built-In Feature: Celina AI (${CELINA_NAME} / ${CELINA_NICKNAME} | ENABLE_CELINA=$ENABLE_CELINA)"

# P0-5: ENABLE_CELINA=false must exit at TOP — zero $HOME or systemd mutations
if ! is_true "$ENABLE_CELINA"; then
    echo "    Skipping built-in Celina feature (ENABLE_CELINA=false)."
    exit 0
fi

# --- Hardware-scaled Celina slice + model selection (hardware.json driven) ---
if [[ -f /etc/macubuntu/hardware.json ]] && command -v jq >/dev/null 2>&1; then
    HW_TIER="$(jq -r '.tier // "balanced"' /etc/macubuntu/hardware.json)"
    HW_RAM_MB="$(jq -r '.ram_mb // 0' /etc/macubuntu/hardware.json)"
    HW_VRAM_MB="$(jq -r '.vram_mb // 0' /etc/macubuntu/hardware.json)"
else
    HW_TIER="balanced"
    HW_RAM_MB="$(detect_ram_total_mb 2>/dev/null || echo 8192)"
    HW_VRAM_MB=0
fi

case "$HW_TIER" in
    lite)
        CELINA_MODEL="qwen3:1.7b"
        SLICE_HIGH="3G"; SLICE_MAX="4G"
        SLICE_CPU="40"; SLICE_IO="40"
        echo "    📋 Lite hardware — Celina model: $CELINA_MODEL (1.7B), slice: MemoryHigh=3G MemoryMax=4G"
        ;;
    balanced)
        CELINA_MODEL="qwen3:4b"
        SLICE_HIGH="7G"; SLICE_MAX="9G"
        SLICE_CPU="40"; SLICE_IO="40"
        echo "    📋 Balanced hardware — Celina model: $CELINA_MODEL (4B), slice: MemoryHigh=7G MemoryMax=9G"
        ;;
    workstation)
        CELINA_MODEL="qwen3.5:9b"
        SLICE_HIGH="16G"; SLICE_MAX="22G"
        SLICE_CPU="40"; SLICE_IO="40"
        echo "    📋 Workstation hardware — Celina model: $CELINA_MODEL (9B), slice: MemoryHigh=16G MemoryMax=22G"
        ;;
    *)
        CELINA_MODEL="qwen3:4b"
        SLICE_HIGH="7G"; SLICE_MAX="9G"
        SLICE_CPU="40"; SLICE_IO="40"
        echo "    📋 Unknown hardware — Celina model: $CELINA_MODEL (4B), slice: MemoryHigh=7G MemoryMax=9G"
        ;;
esac

# Source profile-based slice ceilings if available (aligns with Stage 35 & macubuntu-reconfigure)
if [[ -f "$REPO_DIR/profiles/${HW_TIER}.env" ]]; then
    # shellcheck source=/dev/null
    source "$REPO_DIR/profiles/${HW_TIER}.env" 2>/dev/null || true
    SLICE_HIGH="${CELINA_SLICE_HIGH:-$SLICE_HIGH}"
    SLICE_MAX="${CELINA_SLICE_MAX:-$SLICE_MAX}"
fi

# Write hardware-scaled celina-ai.slice for user systemd (Celina AI runtime guard)
if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [dry-run] would write ~/.config/systemd/user/celina-ai.slice (MemoryHigh=$SLICE_HIGH MemoryMax=$SLICE_MAX)"
    echo "  [dry-run] would pull Ollama model: $CELINA_MODEL"
else
    cat <<SLICEEOF | write_user_file "${HOME}/.config/systemd/user/celina-ai.slice" 644
[Unit]
Description=Hardware-scaled Celina AI resource guard (tier=$HW_TIER)
Documentation=file:///usr/share/macubuntu/ARCHITECTURE.md

[Slice]
CPUWeight=${SLICE_CPU}
IOWeight=${SLICE_IO}
CPUQuota=200%
MemoryHigh=${SLICE_HIGH}
MemoryMax=${SLICE_MAX}
MemorySwapMax=2G
ManagedOOMMemoryPressure=kill
SLICEEOF
    run_as_user systemctl --user daemon-reload 2>/dev/null || true
    echo "      ✅ ~/.config/systemd/user/celina-ai.slice written (tier=$HW_TIER)"
fi

# --- XDG-compliant Celina directory provisioning (hardware-adaptive, 0700 private) ---
step "Provisioning XDG-compliant Celina directories (0700 private)..."
CELINA_PRIVATE_DIRS=(
    "${HOME}/.config/celina"
    "${HOME}/.local/share/celina"
    "${HOME}/.local/share/celina/skills/verified"
    "${HOME}/.local/share/celina/skills/quarantine"
    "${HOME}/.local/share/celina/vault"
    "${HOME}/.local/state/celina/audit"
    "${HOME}/.local/state/celina/checkpoints"
    "${HOME}/.local/state/celina/logs"
    "${HOME}/.cache/celina/models"
)
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] would create ${#CELINA_PRIVATE_DIRS[@]} private XDG directories (0700)"
    RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    echo "      [dry-run] would create $RUNTIME_DIR/celina (0700)"
else
    RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    for dir in "${CELINA_PRIVATE_DIRS[@]}"; do
        ensure_private_dir "$dir"
    done
    # Runtime dir under XDG_RUNTIME_DIR (skip if parent doesn't exist)
    if [[ -d "$RUNTIME_DIR" ]]; then
        ensure_private_dir "$RUNTIME_DIR/celina"
    fi
    # chattr +C (nodatacow) on model cache if on btrfs
    if [[ -d "${HOME}/.cache/celina/models" ]] && command -v chattr >/dev/null 2>&1; then
        chattr +C "${HOME}/.cache/celina/models" 2>/dev/null || true
    fi
    echo "      ✅ XDG directories provisioned (0700 private)"
fi

# --- Voice pipeline upgrade: Kokoro-82M TTS + parakeet-rs ASR + Silero VAD ---
# Full-duplex local voice with conversational turn-taking & barge-in
# (replaces batch Whisper + Piper TTS for Jarvis-like live partner feel)
if is_true "$ENABLE_VOICE_PIPELINE_UPGRADE"; then
    step "Deploying full-duplex voice pipeline (Kokoro-82M TTS + parakeet-rs ASR + Silero VAD)..."
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] pip install into Celina venv: kokoro-onnx openwakeword parakeet-rs silero-vad onnxruntime"
        echo "      [dry-run] download Kokoro-82M model (82M params, Apache 2.0, 24 kHz natural cadence)"
        echo "      [dry-run] configure streaming ASR with parakeet-rs + OpenWakeWord wake phrase"
    else
        VOICE_DIR="$HOME/.local/share/celina/voice"
        ensure_private_dir "$VOICE_DIR"
        # Install voice pipeline Python dependencies into Celina venv (never bare system pip3)
        CELINA_VENV_PIP="$HOME/.local/share/celina/venv/bin/pip"
        if [[ -x "$CELINA_VENV_PIP" ]]; then
            pip_install "$CELINA_VENV_PIP" kokoro-onnx openwakeword parakeet-rs silero-vad onnxruntime || \
                echo "      ⚠️  Some voice packages unavailable via pip — install manually"
        elif command -v pip3 >/dev/null 2>&1; then
            echo "      ⚠️  Celina venv not found — skipping system pip3 install (PEP 668 externally-managed-environment)"
        fi
        # Download Kokoro-82M model
        if [[ ! -f "$VOICE_DIR/kokoro-v0_19.onnx" ]]; then
            echo "      ℹ️  Kokoro-82M model not found — download from https://github.com/thewh1tespider/kokoro-onnx"
            echo "      ℹ️  Place kokoro-v0_19.onnx + voices/ in $VOICE_DIR/"
        else
            echo "      ✅ Kokoro-82M model already present"
        fi
        echo "      ✅ Voice pipeline configured (Kokoro-82M TTS + parakeet-rs ASR + Silero VAD + OpenWakeWord)"
    fi
else
    echo "    ℹ️  Voice pipeline upgrade skipped (ENABLE_VOICE_PIPELINE_UPGRADE=false)"
fi

# --- Ollama model pull: resolve OLLAMA_MODEL=auto to hardware-matched $CELINA_MODEL ---
if [[ -z "${OLLAMA_MODEL:-}" || "${OLLAMA_MODEL}" == "auto" ]]; then
    FINAL_OLLAMA_MODEL="$CELINA_MODEL"
else
    FINAL_OLLAMA_MODEL="$OLLAMA_MODEL"
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [dry-run] ollama pull ${FINAL_OLLAMA_MODEL} (hardware-matched: $CELINA_MODEL)"
else
    if [[ -n "$FINAL_OLLAMA_MODEL" && "$FINAL_OLLAMA_MODEL" != "auto" ]]; then
        echo "  [real] ollama pull ${FINAL_OLLAMA_MODEL}"
        ollama pull "${FINAL_OLLAMA_MODEL}" 2>/dev/null || true
        # Record pulled digest for doctor.sh verification
        DIGESTS_FILE="$HOME/.config/celina/model-digests.sha256"
        ensure_private_dir "$HOME/.config/celina"
        if command -v ollama >/dev/null 2>&1; then
            DIGEST="$(ollama show "${FINAL_OLLAMA_MODEL}" 2>/dev/null | grep -i 'digest:' | awk '{print $2}' || true)"
            if [[ -n "$DIGEST" ]]; then
                echo "${DIGEST}  ${FINAL_OLLAMA_MODEL}" | write_user_file "$DIGESTS_FILE" 600
                echo "      ✅ Recorded model digest to $DIGESTS_FILE (0600)"
            fi
        fi
    fi
fi


if [[ "${DRY_RUN:-0}" == "1" ]]; then
    if [[ -n "${OLLAMA_MODEL:-}" && "${OLLAMA_MODEL}" != "auto" ]]; then
        echo "  [dry-run] ollama pull ${OLLAMA_MODEL} (would use hardware-matched: $CELINA_MODEL)"
    fi
    PYTHONPATH="$REPO_DIR/features/celina" bash "$REPO_DIR/features/celina/install.sh" --dry-run
else
    # Use hardware-matched model unless user explicitly specified one
    FINAL_OLLAMA_MODEL="${OLLAMA_MODEL:-$CELINA_MODEL}"
    if [[ -n "$FINAL_OLLAMA_MODEL" && "$FINAL_OLLAMA_MODEL" != "auto" ]]; then
        echo "  [real] ollama pull ${FINAL_OLLAMA_MODEL}"
        ollama pull "${FINAL_OLLAMA_MODEL}" 2>/dev/null || true
    fi
    PYTHONPATH="$REPO_DIR/features/celina" bash "$REPO_DIR/features/celina/install.sh"
fi

# Wire macubuntu Center, Celina Shortcuts, and Polkit updater helper (DRY_RUN-safe)
step "Installing macubuntu Center, Celina Shortcuts, and Polkit updater helper..."
if [[ -f "$REPO_DIR/apps/macubuntu-center/main.py" ]]; then
    cat <<CENTEREOF | write_user_file "${HOME}/.local/bin/macubuntu-center" 755
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${REPO_DIR}/features/celina${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "${REPO_DIR}/apps/macubuntu-center/main.py" "\$@"
CENTEREOF
    if [[ -f "$REPO_DIR/data/org.macubuntu.Center.desktop" ]]; then
        copy_user_file "$REPO_DIR/data/org.macubuntu.Center.desktop" "${HOME}/.local/share/applications/org.macubuntu.Center.desktop" 644
    fi
fi
if [[ -f "$REPO_DIR/apps/celina-shortcuts/main.py" ]]; then
    cat <<SHORTCUTSEOF | write_user_file "${HOME}/.local/bin/celina-shortcuts" 755
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${REPO_DIR}/features/celina${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "${REPO_DIR}/apps/celina-shortcuts/main.py" "\$@"
SHORTCUTSEOF
    if [[ -f "$REPO_DIR/apps/celina-shortcuts/celina-shortcuts.desktop" ]]; then
        copy_user_file "$REPO_DIR/apps/celina-shortcuts/celina-shortcuts.desktop" "${HOME}/.local/share/applications/celina-shortcuts.desktop" 644
    fi
fi
if [[ -f "$REPO_DIR/libexec/macubuntu-updater-helper" ]]; then
    copy_root_file "$REPO_DIR/libexec/macubuntu-updater-helper" "/usr/local/libexec/macubuntu-updater-helper" 755
fi
if [[ -f "$REPO_DIR/polkit/org.macubuntu.center.policy" ]]; then
    copy_root_file "$REPO_DIR/polkit/org.macubuntu.center.policy" "/usr/share/polkit-1/actions/org.macubuntu.center.policy" 644
fi
