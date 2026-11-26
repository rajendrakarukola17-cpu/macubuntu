#!/usr/bin/env bash
# ============================================================================
# features/celina/install.sh — Hardened Built-In Celina AI Subsystem Installer
# Security Invariants:
#   - umask 077; private config/data/skills/logs enforced at 0700 / 0600.
#   - NEVER adds user to broad 'input' group or opens /dev/uinput by default
#     (GNOME RemoteDesktop Portal + AT-SPI2 + scoped broker preferred).
#   - Every remote download (Ollama installer, Piper voices) is SHA-256 pinned
#     via system/download-locks.sha256 and verified before execution/install.
#   - DRY_RUN=1 performs ZERO disk, network, or service mutations.
# ============================================================================
set -Eeuo pipefail
umask 077

FEATURE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(cd "$FEATURE_DIR/../.." && pwd)}"
# shellcheck source=../../core/common.sh
source "$REPO_DIR/core/common.sh"
init_defaults
init_runtime_state

CELINA_APP_DIR="$HOME/.local/share/celina/app"
CELINA_VENV="$HOME/.local/share/celina/venv"
VOICES_DIR="$HOME/.local/share/celina/voices"
SKILLS_TRUSTED="$HOME/.local/share/celina/skills/trusted"
SKILLS_QUARANTINE="$HOME/.local/share/celina/skills/quarantine"
LOGS_DIR="$HOME/.local/share/celina/logs"
BIN_DIR="$HOME/.local/bin"
SCHEMA_DEST="$HOME/.config/celina/neural-schema.json"
ENV_DEST="$HOME/.config/celina/env"
MCP_DEST="$HOME/.config/celina/mcp-servers.json"
IOT_DEST="$HOME/.config/celina/iot-allowlist.json"

# 1. Wayland Input Isolation (Portal + AT-SPI2 First; NO broad 'input' group by default!)
step "[Celina 1/6] Configuring safe Wayland & GPU render access (Portal + AT-SPI2; no broad input group)..."
if getent group video >/dev/null 2>&1; then
    run sudo usermod -aG video "$USER" || true
fi
if getent group render >/dev/null 2>&1; then
    run sudo usermod -aG render "$USER" || true
fi

if is_true "$ENABLE_EXPERIMENTAL_UINPUT"; then
    echo "      ⚠️  WARNING: Experimental /dev/uinput fallback explicitly enabled (--experimental-uinput)."
    echo "      Consider disabling it once GNOME RemoteDesktop Portal covers all needed actions."
    ensure_dir "$HOME/.config/systemd/user"
    copy_user_file "$REPO_DIR/system/systemd/ydotoold.service" "$HOME/.config/systemd/user/ydotoold.service" 644
    systemctl_user_enable ydotoold.service || true
else
    echo "      Safe input mode active: user NOT added to 'input' group; /dev/uinput not exposed."
    echo "      Input hierarchy: 1) GNOME Portal -> 2) App APIs -> 3) AT-SPI2 -> 4) Scoped Broker."
fi

# 2. Private Configs (0700/0600), Neural Schema, MCP Registry, IoT Allowlist & Trusted Skills
step "[Celina 2/6] Initializing private ~/.config/celina (0700/0600) & trusted skills..."
ensure_private_dir \
    "$HOME/.config/celina" \
    "$HOME/.local/share/celina" \
    "$CELINA_APP_DIR" \
    "$VOICES_DIR" \
    "$HOME/.local/share/celina/wakeword" \
    "$HOME/.local/share/celina/backups" \
    "$HOME/.local/share/celina/skills" \
    "$SKILLS_TRUSTED" \
    "$SKILLS_QUARANTINE" \
    "$LOGS_DIR"
ensure_dir "$BIN_DIR" "$HOME/.config/autostart"

# Resolve local Ollama model (honors --model / OLLAMA_MODEL, or auto-selects Dense/MoE by detected RAM)
RESOLVED_MODEL="$(resolve_ollama_model "${OLLAMA_MODEL:-auto}")"

if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] deploy private env (0600, name=$CELINA_NAME, nick=$CELINA_NICKNAME, model=$RESOLVED_MODEL), neural-schema.json (0600), mcp-servers.json (0600), iot-allowlist.json (0600), skills/trusted/*.md (0600)"
else
    if [[ ! -f "$ENV_DEST" ]]; then
        DYN_APP_VAL="0"
        is_true "${ENABLE_DYNAMIC_APPEARANCE:-false}" && DYN_APP_VAL="1"
        sed -e "s/^CELINA_NAME=.*/CELINA_NAME=\"${CELINA_NAME}\"/" \
            -e "s/^CELINA_NICKNAME=.*/CELINA_NICKNAME=\"${CELINA_NICKNAME}\"/" \
            -e "s|^CELINA_LOCAL_MODEL=.*|CELINA_LOCAL_MODEL=\"${RESOLVED_MODEL}\"|" \
            -e "s/^CELINA_DYNAMIC_APPEARANCE=.*/CELINA_DYNAMIC_APPEARANCE=\"${DYN_APP_VAL}\"/" \
            "$FEATURE_DIR/config/env.template" \
            | write_user_file "$ENV_DEST" 600
    else
        chmod_safe 600 "$ENV_DEST"
        # If the user explicitly passed --model <non-auto>, update CELINA_LOCAL_MODEL cleanly
        if [[ -n "${OLLAMA_MODEL:-}" && "${OLLAMA_MODEL}" != "auto" ]]; then
            python3 - "$ENV_DEST" "$RESOLVED_MODEL" <<'PY'
import os, sys
from pathlib import Path
p, model = Path(sys.argv[1]), sys.argv[2]
lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
out, found = [], False
for ln in lines:
    if ln.strip().startswith("CELINA_LOCAL_MODEL="):
        out.append(f'CELINA_LOCAL_MODEL="{model}"')
        found = True
    else:
        out.append(ln)
if not found:
    out.append(f'CELINA_LOCAL_MODEL="{model}"')
p.write_text("\n".join(out) + "\n", encoding="utf-8")
os.chmod(p, 0o600)
PY
        fi
    fi
    if [[ -f "$FEATURE_DIR/config/celina-capabilities.json" ]]; then
        copy_user_file "$FEATURE_DIR/config/celina-capabilities.json" "$HOME/.config/celina/celina-capabilities.json" 600
    fi
    if [[ ! -f "$SCHEMA_DEST" ]]; then
        copy_user_file "$FEATURE_DIR/config/neural-schema.json" "$SCHEMA_DEST" 600
    else
        chmod_safe 600 "$SCHEMA_DEST"
        # Seamlessly migrate pre-4.3 on-disk neural-schema.json while preserving user persona/routing edits
        python3 - "$SCHEMA_DEST" "$FEATURE_DIR/config/neural-schema.json" <<'PY' || true
import json, os, re, shutil, sys
from pathlib import Path
dst, src = Path(sys.argv[1]), Path(sys.argv[2])
try:
    old = json.loads(dst.read_text(encoding="utf-8"))
    new = json.loads(src.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)
def _ver(v):
    m = re.match(r"^\s*(\d+)(?:\.(\d+))?", str(v or "0.0"))
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
if _ver(old.get("schema_version")) < _ver(new.get("schema_version")):
    bak = dst.with_suffix(".json.bak")
    shutil.copy2(dst, bak)
    os.chmod(bak, 0o600)
    def _union(a, b):
        out, seen = [], set()
        for lst in (a or [], b or []):
            for x in lst:
                if x not in seen:
                    seen.add(x); out.append(x)
        return out
    merged = dict(new)
    for k in ("persona", "routing", "languages", "stt", "edge_lighting", "cache", "automation", "embeddings"):
        if isinstance(old.get(k), dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **old[k]}
    merged_t2 = _union(new["security_policy"].get("tier2_approval_tools"), (old.get("security_policy") or {}).get("tier2_approval_tools"))
    merged_t2_set = set(merged_t2)
    merged_t1 = [
        t for t in _union(new["security_policy"].get("tier1_auto_tools"), (old.get("security_policy") or {}).get("tier1_auto_tools"))
        if t not in merged_t2_set
    ]
    merged["security_policy"] = {
        **new["security_policy"],
        "tier1_auto_tools": merged_t1,
        "tier2_approval_tools": merged_t2,
        "protected_paths": _union(new["security_policy"].get("protected_paths"), (old.get("security_policy") or {}).get("protected_paths")),
    }
    merged["privacy"] = {
        **new["privacy"],
        **{k: v for k, v in (old.get("privacy") or {}).items() if k not in ("local_sensitive_tools", "hard_blocked_paths")},
        "local_sensitive_tools": _union(new["privacy"].get("local_sensitive_tools"), (old.get("privacy") or {}).get("local_sensitive_tools")),
        "hard_blocked_paths": _union(new["privacy"].get("hard_blocked_paths"), (old.get("privacy") or {}).get("hard_blocked_paths")),
    }
    dst.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(dst, 0o600)
PY
    fi
    if [[ ! -f "$MCP_DEST" ]]; then
        copy_user_file "$FEATURE_DIR/config/mcp-servers.json" "$MCP_DEST" 600
    else
        chmod_safe 600 "$MCP_DEST"
    fi
    if [[ ! -f "$IOT_DEST" ]]; then
        copy_user_file "$FEATURE_DIR/config/iot-allowlist.json" "$IOT_DEST" 600
    else
        chmod_safe 600 "$IOT_DEST"
    fi
    for sf in "$FEATURE_DIR/skills"/*.md; do
        [[ -f "$sf" ]] || continue
        base="$(basename "$sf")"
        [[ -f "$SKILLS_TRUSTED/$base" ]] || copy_user_file "$sf" "$SKILLS_TRUSTED/$base" 600
    done
fi

# Deploy macOS Spotlight theme for Rofi
if [[ -f "$REPO_DIR/system/gnome/rofi-spotlight.rasi" ]]; then
    copy_user_file "$REPO_DIR/system/gnome/rofi-spotlight.rasi" "$HOME/.config/rofi-spotlight.rasi" 644
fi

# 3. Local Offline Brain — Pinned, Checksum-Verified Ollama Installer + RAM-Aware Dense/MoE Tuning
step "[Celina 3/6] Installing & tuning pinned local Ollama (SHA-256 verified, q8_0 KV-cache, model=$RESOLVED_MODEL)..."
LOCAL_MODEL="$RESOLVED_MODEL"
if [[ -f "$ENV_DEST" ]]; then
    LOCAL_MODEL="$(awk -F= '/^CELINA_LOCAL_MODEL=/{gsub(/"/,"",$2); print $2}' "$ENV_DEST" 2>/dev/null || echo "$RESOLVED_MODEL")"
    LOCAL_MODEL="${LOCAL_MODEL:-$RESOLVED_MODEL}"
fi

if ! command -v ollama >/dev/null 2>&1; then
    OLLAMA_ARCHIVE="$WORKDIR/ollama-linux-amd64.tar.zst"
    if download_verified "ollama-linux-amd64" "$OLLAMA_ARCHIVE" 600; then
        if [[ "$DRY_RUN" == "1" ]]; then
            echo "      [dry-run] sudo tar --zstd -C /usr/local -xf $OLLAMA_ARCHIVE"
            echo "      [dry-run] create ollama service user & /etc/systemd/system/ollama.service (127.0.0.1:11434)"
        else
            run sudo tar --zstd -C /usr/local -xf "$OLLAMA_ARCHIVE" \
                || record_fail "Ollama verified archive extraction"
            run sudo useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama 2>/dev/null || true
            sudo tee /etc/systemd/system/ollama.service >/dev/null <<'EOF'
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="OLLAMA_HOST=127.0.0.1:11434"

[Install]
WantedBy=default.target
EOF
            sudo chmod 644 /etc/systemd/system/ollama.service
            sudo systemctl daemon-reload || true
        fi
    else
        record_fail "Ollama verified binary archive download"
    fi
else
    echo "      Ollama is already installed."
fi

copy_root_file "$REPO_DIR/system/systemd/ollama-8gb-ram.conf" /etc/systemd/system/ollama.service.d/8gb-ram.conf
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] if AMD Radeon GPU detected, deploy /etc/systemd/system/ollama.service.d/amd-vulkan.conf (OLLAMA_VULKAN=1, OLLAMA_IGPU_ENABLE=1) & grant ollama render/video access"
elif detect_has_amd_gpu; then
    step "AMD Radeon GPU detected — enabling Mesa RADV Vulkan acceleration for Ollama (no CUDA/ROCm crashes)..."
    copy_root_file "$REPO_DIR/system/systemd/ollama-amd-vulkan.conf" /etc/systemd/system/ollama.service.d/amd-vulkan.conf
    if id ollama >/dev/null 2>&1; then
        getent group render >/dev/null 2>&1 && run sudo usermod -aG render ollama || true
        getent group video >/dev/null 2>&1 && run sudo usermod -aG video ollama || true
    fi
fi
systemctl_enable ollama
run sudo systemctl daemon-reload 2>/dev/null || true
run sudo systemctl restart ollama 2>/dev/null || true

EMBED_MODEL="embeddinggemma"
if [[ -f "$ENV_DEST" ]]; then
    em="$(awk -F= '/^CELINA_EMBED_MODEL=/{gsub(/"/,"",$2); print $2}' "$ENV_DEST" 2>/dev/null || true)"
    [[ -n "$em" ]] && EMBED_MODEL="$em"
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] ollama pull $LOCAL_MODEL"
    echo "      [dry-run] ollama pull $EMBED_MODEL (200 MB multilingual Hybrid Semantic Engine)"
    echo "      [dry-run] record Ollama model SHA-256 digests -> $HOME/.config/celina/model-digests.sha256 (0600)"
else
    OLLAMA_READY=0
    for _ in {1..30}; do
        if curl -fs --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
            OLLAMA_READY=1
            break
        fi
        sleep 1
    done
    if [[ "$OLLAMA_READY" -eq 1 ]]; then
        run ollama pull "$LOCAL_MODEL" || record_manual "Pull local model manually: ollama pull $LOCAL_MODEL"
        run ollama pull "$EMBED_MODEL" || record_manual "Pull local embedding model for Hybrid Semantic Search: ollama pull $EMBED_MODEL"
        MODEL_LOCK="$HOME/.config/celina/model-digests.sha256"
        {
            echo "# macubuntu — Recorded Ollama Model Digests (verify anytime: ./install.sh --verify-models)"
            for mtag in "$LOCAL_MODEL" "$EMBED_MODEL"; do
                mdig="$(ollama show "$mtag" --modelfile 2>/dev/null | grep -oE 'sha256-[0-9a-f]{64}' | head -1 || true)"
                if [[ -z "$mdig" ]]; then
                    mdig="$(ollama list 2>/dev/null | awk -v m="$mtag" '$1==m || $1==m":latest" {print $2; exit}' || true)"
                fi
                [[ -n "$mdig" ]] && echo "${mtag}|${mdig}"
            done
        } > "$MODEL_LOCK"
        chmod 600 "$MODEL_LOCK"
    else
        record_manual "Ollama API did not respond in time; run 'ollama pull $LOCAL_MODEL && ollama pull $EMBED_MODEL' after reboot"
    fi
fi

# 4. Isolated Python Venv (--system-site-packages keeps GTK3 & AT-SPI2 gi accessible)
step "[Celina 4/6] Setting up Python venv & pinned Document/Voice dependencies..."
if [[ ! -d "$CELINA_VENV" ]]; then
    run python3 -m venv --system-site-packages "$CELINA_VENV"
fi
REQ_LOCK="$FEATURE_DIR/requirements.lock"
REQ_PIN="$FEATURE_DIR/requirements.txt"
REQ_OPT="$FEATURE_DIR/requirements-optional.txt"
if [[ -f "$REQ_LOCK" ]]; then
    pip_install "$CELINA_VENV/bin/pip" --require-hashes -r "$REQ_LOCK" -q \
        || record_manual "Hash-verified pip install from requirements.lock failed; verify Python ABI match"
elif [[ -f "$REQ_PIN" ]]; then
    pip_install "$CELINA_VENV/bin/pip" -r "$REQ_PIN" -q \
        || record_manual "Some pinned Office/Voice Python packages failed; plain text & espeak-ng fallback still work"
fi
if [[ -f "$REQ_OPT" ]]; then
    pip_install "$CELINA_VENV/bin/pip" -r "$REQ_OPT" -q >/dev/null 2>&1 || true
fi

# 5. Pinned, Checksum-Verified Multilingual Feminine Piper Neural Voices (EN / TE / HI)
download_piper_voice_pinned() {
    local model="$1"
    [[ -z "$model" ]] && return 0
    local onnx_path="$VOICES_DIR/${model}.onnx"
    local json_path="$VOICES_DIR/${model}.onnx.json"
    local lock_onnx="piper-${model}-onnx"
    local lock_json="piper-${model}-json"

    if [[ "$DRY_RUN" != "1" && -f "$onnx_path" && -f "$json_path" ]]; then
        echo "      Piper voice '$model' already present."
        return 0
    fi
    if download_verified "$lock_onnx" "$onnx_path" 600 \
        && download_verified "$lock_json" "$json_path" 600; then
        return 0
    else
        record_manual "Piper voice '$model' not pinned or checksum failed (espeak-ng fallback active)"
    fi
}

step "[Celina 5/6] Downloading SHA-256 verified feminine Piper voices (EN / TE / HI)..."
VOICE_EN="en_US-amy-medium"
VOICE_TE="te_IN-maya-medium"
VOICE_HI="hi_IN-priyamvada-medium"
if [[ "$DRY_RUN" != "1" && -f "$ENV_DEST" ]]; then
    v="$(awk -F= '/^CELINA_VOICE_EN=/{gsub(/"/,"",$2); print $2}' "$ENV_DEST" 2>/dev/null || true)"; [[ -n "$v" ]] && VOICE_EN="$v"
    v="$(awk -F= '/^CELINA_VOICE_TE=/{gsub(/"/,"",$2); print $2}' "$ENV_DEST" 2>/dev/null || true)"; [[ -n "$v" ]] && VOICE_TE="$v"
    v="$(awk -F= '/^CELINA_VOICE_HI=/{gsub(/"/,"",$2); print $2}' "$ENV_DEST" 2>/dev/null || true)"; [[ -n "$v" ]] && VOICE_HI="$v"
fi
for vmodel in "$VOICE_EN" "$VOICE_TE" "$VOICE_HI"; do
    download_piper_voice_pinned "$vmodel"
done

# 6. Deploy celina_pkg + celina_hardware + Launchers (celina, celly, celina-island, celina-voice, celina-spotlight)
step "[Celina 6/6] Deploying celina_pkg, celina_hardware & Spotlight/Island launchers..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] copy $FEATURE_DIR/celina_pkg -> $CELINA_APP_DIR/celina_pkg (0700)"
    echo "      [dry-run] copy $FEATURE_DIR/celina_hardware -> $CELINA_APP_DIR/celina_hardware (0700)"
    echo "      [dry-run] write launchers: celina-cli, celina, celly, celina-dbus, celina-export, celina-import, celina-island, celina-voice, celina-spotlight"
    echo "      [dry-run] install systemd user units: celina.service, celina-eventd.service, celina-npu-inference.service"
    echo "      [dry-run] install D-Bus session activation: org.macubuntu.Celina.service"
    echo "      [dry-run] install GNOME Shell extension: celina-island@macubuntu.org + compile GSettings schema"
    echo "      [dry-run] write ~/.config/autostart/celina-island.desktop"
else
    rm -rf "$CELINA_APP_DIR/celina_pkg" "$CELINA_APP_DIR/celina_hardware"
    cp -r "$FEATURE_DIR/celina_pkg" "$CELINA_APP_DIR/celina_pkg"
    cp -r "$FEATURE_DIR/celina_hardware" "$CELINA_APP_DIR/celina_hardware"
    chmod -R go-rwx "$CELINA_APP_DIR"

    write_user_file "$BIN_DIR/celina-cli" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.router "$@"
EOF

    ln -sf "$BIN_DIR/celina-cli" "$BIN_DIR/celina"
    ln -sf "$BIN_DIR/celina-cli" "$BIN_DIR/celly"

    write_user_file "$BIN_DIR/celina-export" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.state_ops export "$@"
EOF

    write_user_file "$BIN_DIR/celina-import" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.state_ops import "$@"
EOF

    write_user_file "$BIN_DIR/celina-island" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.island_ui "$@"
EOF

    write_user_file "$BIN_DIR/celina-voice" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec "$HOME/.local/bin/celina-island" --voice
EOF

    write_user_file "$BIN_DIR/celina-train" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.everyday_trainer "$@"
EOF

    write_user_file "$BIN_DIR/celina-login-save" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.keychain_login "$@"
EOF

    write_user_file "$BIN_DIR/celina-turbo" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.turbo_mode "$@"
EOF

    write_user_file "$BIN_DIR/celina-spotlight" 700 <<'EOF'
#!/usr/bin/env bash
# Unified macOS CoreSpotlight Bridge (Super+Space):
#   - Live inline math, unit conversion, GNOME Settings & private FTS5 files (spotlight mode)
#   - Pick an app -> launches immediately with icon (drun)
#   - Type any natural-language prompt -> routes straight to built-in Celina Dynamic Island
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"

if [[ -f "$HOME/.config/celina/env" ]]; then
    CELINA_NAME="$(awk -F= '/^CELINA_NAME=/{gsub(/"/,"",$2); print $2}' "$HOME/.config/celina/env" 2>/dev/null || echo "Celina")"
    CELINA_NICKNAME="$(awk -F= '/^CELINA_NICKNAME=/{gsub(/"/,"",$2); print $2}' "$HOME/.config/celina/env" 2>/dev/null || echo "Celly")"
fi
ASSISTANT_DISPLAY="${CELINA_NAME:-Celina}"
[[ -n "${CELINA_NICKNAME:-}" && "${CELINA_NICKNAME,,}" != "${ASSISTANT_DISPLAY,,}" ]] \
    && ASSISTANT_DISPLAY="${ASSISTANT_DISPLAY} (${CELINA_NICKNAME})"

THEME_OPT=()
[[ -f "$HOME/.config/rofi-spotlight.rasi" ]] && THEME_OPT=(-theme "$HOME/.config/rofi-spotlight.rasi")

CHOICE="$(rofi -show combi -combi-modi "drun,spotlight:$VENV_PY -m celina_pkg.spotlight_index --rofi-mode" \
    -modi "combi,drun,spotlight:$VENV_PY -m celina_pkg.spotlight_index --rofi-mode" \
    -show-icons -matching fuzzy \
    -p "Spotlight / ${ASSISTANT_DISPLAY}" \
    -mesg "Apps · Math · Units · Settings · Files · Or press Enter to ask ${ASSISTANT_DISPLAY}" \
    -run-command "echo __RUN__:{cmd}" \
    "${THEME_OPT[@]}" -print-selected-entry 2>/dev/null || true)"

[[ -z "$CHOICE" ]] && exit 0
if [[ "$CHOICE" == __RUN__:* ]]; then
    CMD="${CHOICE#__RUN__:}"
    nohup bash -c "$CMD" >/dev/null 2>&1 &
    exit 0
fi

# Non-app query -> hand off directly to Celina Dynamic Island
nohup "$HOME/.local/bin/celina-island" --ask "$CHOICE" >/dev/null 2>&1 &
EOF

    write_user_file "$BIN_DIR/celina-writing-tools" 700 <<'EOF'
#!/usr/bin/env bash
# Apple Intelligence Writing Tools Hotkey (Super+Shift+W): local Ollama proofread/rewrite on Wayland selection
set -euo pipefail
umask 077
MODE="${1:-proofread}"
exec "$HOME/.local/bin/celina-island" --ask "Use writing_tool_transform with mode='${MODE}' on my selected text."
EOF

    write_user_file "$BIN_DIR/celina-live-text" 700 <<'EOF'
#!/usr/bin/env bash
# macOS Live Text Region OCR Hotkey (Super+Shift+T): area snip -> Tesseract -> Wayland clipboard
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
OUT="$("$VENV_PY" -c "from celina_pkg.writing_tools import live_text_ocr_region; print(live_text_ocr_region(True))")"
if command -v notify-send >/dev/null 2>&1; then
    notify-send -a "Celina Live Text" "Live Text OCR" "$(printf '%s' "$OUT" | head -c 220)"
fi
EOF

    write_user_file "$BIN_DIR/celina-dbus" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.dbus_service "$@"
EOF

    write_user_file "$BIN_DIR/celina-autonomy" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.autonomy.engine "$@"
EOF

    write_user_file "$BIN_DIR/celina-mcp-server" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.mcp_server "$@"
EOF

    write_user_file "$BIN_DIR/celina-memory-mcp" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.memory_mcp_server "$@"
EOF

    write_user_file "$BIN_DIR/celina-proactive" 700 <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.proactive.daemon "$@"
EOF

    write_user_file "$HOME/.config/autostart/celina-island.desktop" 644 <<EOF
[Desktop Entry]
Type=Application
Name=${CELINA_NAME} Dynamic Island
Comment=Built-in macOS-style Dynamic Island & AI Assistant (${CELINA_NAME} / ${CELINA_NICKNAME})
Exec=$HOME/.local/bin/celina-island
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

    ensure_dir "$HOME/.config/systemd/user"
    for unit_name in celina.service celina-eventd.service celina-router.service celina-voice.service celina-memory.service celina-npu-inference.service celina-autonomy.service celina-proactive.service celina-self-improve.service celina-self-improve.timer; do
        copy_user_file "$REPO_DIR/system/systemd/$unit_name" "$HOME/.config/systemd/user/$unit_name" 644
    done
    if systemctl_user_enable celina.service; then
        systemctl --user restart celina.service >/dev/null 2>&1 || true
    fi
    if systemctl_user_enable celina-eventd.service; then
        systemctl --user restart celina-eventd.service >/dev/null 2>&1 || true
    fi
    if systemctl_user_enable celina-memory.service; then
        systemctl --user restart celina-memory.service >/dev/null 2>&1 || true
    fi
    if [[ -e /dev/accel/accel0 ]]; then
        systemctl_user_enable celina-npu-inference.service || true
    fi

    # User-session D-Bus activation (works without root; system .deb also ships /etc/dbus-1/session.d policy)
    ensure_dir "$HOME/.local/share/dbus-1/services"
    copy_user_file "$REPO_DIR/system/dbus/org.macubuntu.Celina.service" \
        "$HOME/.local/share/dbus-1/services/org.macubuntu.Celina.service" 644

    # Native GNOME Shell 48/49/50 ESM Extension (`celina-island@macubuntu.org`)
    if [[ -d "$REPO_DIR/system/gnome/extensions/celina-island@macubuntu.org" ]]; then
        EXT_DEST="$HOME/.local/share/gnome-shell/extensions/celina-island@macubuntu.org"
        ensure_dir "$HOME/.local/share/gnome-shell/extensions"
        rm -rf "$EXT_DEST"
        cp -r "$REPO_DIR/system/gnome/extensions/celina-island@macubuntu.org" "$EXT_DEST"
        if command -v glib-compile-schemas >/dev/null 2>&1 && [[ -d "$EXT_DEST/schemas" ]]; then
            glib-compile-schemas --strict "$EXT_DEST/schemas" >/dev/null 2>&1 || true
        fi
        if command -v gnome-extensions >/dev/null 2>&1; then
            gnome-extensions enable celina-island@macubuntu.org >/dev/null 2>&1 || true
        fi
    fi

    # Run idempotent schema & SQLite WAL migration and report tool/tier diffs
    "$BIN_DIR/celina-cli" migrate --template "$FEATURE_DIR/config/neural-schema.json" || true
fi
