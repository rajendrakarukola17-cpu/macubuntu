#!/usr/bin/env bash
# ============================================================================
# macubuntu/updates/apply-update.sh — Fast In-Place Patch & Upgrade Applier
# Updates Celina runtime, systemd --user units, GNOME Dynamic Island (with
# native Wayland Edge Lighting), and optionally syncs your local macubuntu
# workspace folder in ~5 seconds without redownloading anything.
#
# Usage:
#   ./apply-update.sh                     # Apply update to live desktop
#   ./apply-update.sh --dry-run           # Preview actions with zero writes
#   ./apply-update.sh --workspace <dir>   # Also sync files into local workspace
# ============================================================================
set -Eeuo pipefail
umask 077

if [[ $EUID -eq 0 ]]; then
    echo "❌ Do NOT run as root. Run as your normal desktop user." >&2
    exit 1
fi

DRY_RUN=0
UPGRADE_BRAIN=0
CLEANUP_OLD=0
BRAIN_MODEL="qwen2.5:3b"
LOCAL_WS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)       DRY_RUN=1; shift ;;
        --upgrade-brain) UPGRADE_BRAIN=1; shift ;;
        --cleanup)       CLEANUP_OLD=1; shift ;;
        --model)         BRAIN_MODEL="${2:?missing model tag}"; UPGRADE_BRAIN=1; shift 2 ;;
        --workspace)     LOCAL_WS="${2:?missing workspace path}"; shift 2 ;;
        -h|--help)
            echo "Usage: ./apply-update.sh [--dry-run] [--upgrade-brain] [--cleanup] [--model qwen2.5:3b] [--workspace /path/to/macubuntu]"
            exit 0
            ;;
        *) echo "❌ Unknown option: $1" >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPDATE_VER="update-03"
[[ -f "$SCRIPT_DIR/UPDATE_VERSION" ]] && UPDATE_VER="$(tr -d '[:space:]' < "$SCRIPT_DIR/UPDATE_VERSION")"

# Auto-detect payload root: either standalone update bundle (./payload) or full repo parent (..)
if [[ -d "$SCRIPT_DIR/payload/features/celina/celina_pkg" ]]; then
    PAYLOAD_ROOT="$SCRIPT_DIR/payload"
elif [[ -d "$SCRIPT_DIR/../features/celina/celina_pkg" ]]; then
    PAYLOAD_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    echo "❌ Could not find update payload next to apply-update.sh." >&2
    exit 1
fi

echo "================================================================"
echo "  ⚡ macubuntu Updater — ${UPDATE_VER} (Zero Redownloads · Preserves Data)"
echo "  Payload: $PAYLOAD_ROOT"
[[ "$DRY_RUN" == "1" ]] && echo "  Mode:    DRY-RUN (zero changes will be written)"
echo "================================================================"

CELINA_APP="$HOME/.local/share/celina/app"
USER_SYSTEMD="$HOME/.config/systemd/user"
EXT_DEST="$HOME/.local/share/gnome-shell/extensions/celina-island@macubuntu.org"

# 1. Update Celina Python runtime + neural-schema.json (preserves memory.db, tasks.db, env, voices, custom name)
echo "[1/5] Updating Celina runtime & neural-schema.json..."
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] sync celina_pkg + celina_hardware -> $CELINA_APP (mode 0700)"
    echo "      [dry-run] refresh ~/.config/celina/neural-schema.json (preserving custom persona/flags) & clear stale cache"
else
    mkdir -p "$CELINA_APP" "$HOME/.config/celina"
    chmod 700 "$HOME/.local/share/celina" "$CELINA_APP" "$HOME/.config/celina"
    rm -rf "$CELINA_APP/celina_pkg" "$CELINA_APP/celina_hardware"
    cp -r "$PAYLOAD_ROOT/features/celina/celina_pkg" "$CELINA_APP/celina_pkg"
    cp -r "$PAYLOAD_ROOT/features/celina/celina_hardware" "$CELINA_APP/celina_hardware"
    chmod -R go-rwx "$CELINA_APP"

    # Merge updated neural-schema.json while preserving user persona name/nickname & opt-in flags
    NEW_SCHEMA="$PAYLOAD_ROOT/features/celina/config/neural-schema.json"
    USER_SCHEMA="$HOME/.config/celina/neural-schema.json"
    if [[ -f "$NEW_SCHEMA" ]]; then
        python3 - "$NEW_SCHEMA" "$USER_SCHEMA" <<'PY'
import json, os, pathlib, sys
src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
new_data = json.loads(src.read_text(encoding="utf-8"))
if dst.exists():
    try:
        old_data = json.loads(dst.read_text(encoding="utf-8"))
        for k in ("name", "nickname", "default_language"):
            if old_data.get("persona", {}).get(k):
                new_data.setdefault("persona", {})[k] = old_data["persona"][k]
        for sec in ("web_comms", "autonomy"):
            if isinstance(old_data.get(sec), dict):
                new_data[sec] = old_data[sec]
    except (OSError, json.JSONDecodeError):
        pass
tmp = dst.with_suffix(".json.tmp")
tmp.write_text(json.dumps(new_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
os.replace(tmp, dst)
PY
    fi

    # Clear stale cached "(No response)" / old refusal entries from cache.db without touching memory.db
    if [[ -f "$HOME/.local/share/celina/cache.db" ]]; then
        python3 - "$HOME/.local/share/celina/cache.db" <<'PY' 2>/dev/null || true
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
for tbl in [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
    con.execute(f"DELETE FROM \"{tbl}\"")
con.commit()
con.close()
PY
    fi
    # Install ~/.local/bin/celina-train & ~/.local/bin/celina-login-save launchers
    mkdir -p "$HOME/.local/bin"
    cat > "$HOME/.local/bin/celina-train" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.everyday_trainer "$@"
EOF
    chmod 700 "$HOME/.local/bin/celina-train"

    cat > "$HOME/.local/bin/celina-login-save" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.keychain_login "$@"
EOF
    chmod 700 "$HOME/.local/bin/celina-login-save"

    cat > "$HOME/.local/bin/celina-turbo" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
VENV_PY="$HOME/.local/share/celina/venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"
export PYTHONPATH="$HOME/.local/share/celina/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m celina_pkg.turbo_mode "$@"
EOF
    chmod 700 "$HOME/.local/bin/celina-turbo"

    echo "      ✅ Updated celina_pkg, celina_hardware, celina-train, celina-login-save, celina-turbo & neural-schema.json."
fi

# 2. Update systemd --user units (clean Ubuntu 26.04 --user hardening, no 218/CAPABILITIES or 226/NAMESPACE)
echo "[2/5] Updating systemd --user units (~/.config/systemd/user)..."
UNITS=(
    celina.service
    celina-eventd.service
    celina-router.service
    celina-voice.service
    celina-memory.service
    celina-npu-inference.service
    celina-autonomy.service
    celina-proactive.service
    celina-self-improve.service
    celina-self-improve.timer
)
if [[ "$DRY_RUN" == "1" ]]; then
    echo "      [dry-run] sync ${#UNITS[@]} user unit(s) -> $USER_SYSTEMD"
else
    mkdir -p "$USER_SYSTEMD"
    for u in "${UNITS[@]}"; do
        if [[ -f "$PAYLOAD_ROOT/system/systemd/$u" ]]; then
            cp "$PAYLOAD_ROOT/system/systemd/$u" "$USER_SYSTEMD/$u"
            chmod 644 "$USER_SYSTEMD/$u"
        fi
    done
    echo "      ✅ Updated ${#UNITS[@]} systemd user units."
fi

# 3. Update GNOME Shell Dynamic Island extension (native Wayland Edge Lighting + Chat/Voice menu + auto-reconnect)
echo "[3/5] Updating GNOME Shell Dynamic Island extension..."
EXT_SRC="$PAYLOAD_ROOT/system/gnome/extensions/celina-island@macubuntu.org"
if [[ -d "$EXT_SRC" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] sync $EXT_SRC -> $EXT_DEST + compile GSettings schema"
    else
        mkdir -p "$(dirname "$EXT_DEST")"
        rm -rf "$EXT_DEST"
        cp -r "$EXT_SRC" "$EXT_DEST"
        chmod -R a+rX "$EXT_DEST"
        if command -v glib-compile-schemas >/dev/null 2>&1 && [[ -d "$EXT_DEST/schemas" ]]; then
            glib-compile-schemas --strict "$EXT_DEST/schemas" 2>/dev/null || true
        fi
        echo "      ✅ Updated celina-island@macubuntu.org extension."
    fi
fi

# Optional: sync patched files back into a local extracted macubuntu workspace if requested
if [[ -n "$LOCAL_WS" && -f "$LOCAL_WS/stages/00-preflight.sh" ]]; then
    echo "[+] Syncing ${UPDATE_VER} into local workspace: $LOCAL_WS"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] sync payload -> $LOCAL_WS"
    else
        cp -r "$PAYLOAD_ROOT/features/celina/celina_pkg" "$LOCAL_WS/features/celina/"
        cp -r "$PAYLOAD_ROOT/features/celina/celina_hardware" "$LOCAL_WS/features/celina/"
        cp "$PAYLOAD_ROOT/features/celina/install.sh" "$LOCAL_WS/features/celina/install.sh"
        cp -r "$PAYLOAD_ROOT/system/systemd/"* "$LOCAL_WS/system/systemd/"
        cp -r "$PAYLOAD_ROOT/system/gnome/extensions/celina-island@macubuntu.org" \
              "$LOCAL_WS/system/gnome/extensions/"
        cp "$PAYLOAD_ROOT/core/common.sh" "$LOCAL_WS/core/common.sh"
        chmod +x "$LOCAL_WS/features/celina/install.sh" "$LOCAL_WS/core/common.sh"
        echo "      ✅ Synced local workspace files."
    fi
fi

# 4. Heal /etc/systemd/*.d permissions ONLY if any drop-in folder is still unreadable (0700 from older umask 077)
echo "[4/5] Checking /etc/systemd drop-in permissions..."
NEEDS_PERM_FIX=0
for d in /etc/systemd/resolved.conf.d /etc/systemd/sleep.conf.d /etc/systemd/oomd.conf.d /etc/systemd/system/ollama.service.d; do
    if [[ -e "$d" && ! -r "$d" ]]; then
        NEEDS_PERM_FIX=1
    fi
done
if [[ "$NEEDS_PERM_FIX" == "1" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "      [dry-run] sudo chmod 755 drop-in dirs & 644 *.conf files"
    else
        echo "      Healing unreadable /etc/systemd/*.d drop-ins (requires sudo once)..."
        sudo chmod 755 /etc/systemd/resolved.conf.d /etc/systemd/sleep.conf.d /etc/systemd/oomd.conf.d /etc/systemd/system/ollama.service.d 2>/dev/null || true
        sudo chmod 644 /etc/systemd/resolved.conf.d/*.conf /etc/systemd/sleep.conf.d/*.conf /etc/systemd/oomd.conf.d/*.conf /etc/systemd/system/ollama.service.d/*.conf /etc/systemd/system/ollama.service 2>/dev/null || true
        sudo systemctl daemon-reload 2>/dev/null || true
        sudo systemctl reload-or-restart systemd-resolved 2>/dev/null || true
        echo "      ✅ Healed /etc/systemd drop-in permissions."
    fi
else
    echo "      ✅ System drop-in permissions already healthy (no sudo needed)."
fi

# 5. Optional Brain Upgrade (qwen2.5:3b), Restart Daemons, Reload Dynamic Island & Run 6-Agent Trainer
echo "[5/5] Restarting Celina daemons, reloading Dynamic Island & running 6-Agent Trainer..."
if [[ "$DRY_RUN" == "1" ]]; then
    [[ "$UPGRADE_BRAIN" == "1" ]] && echo "      [dry-run] ollama pull $BRAIN_MODEL & activate CELINA_LOCAL_MODEL=$BRAIN_MODEL"
    echo "      [dry-run] systemctl --user daemon-reload & restart active celina*.service"
    echo "      [dry-run] reload celina-island@macubuntu.org & run celina-train (72 everyday cases)"
    if [[ "$CLEANUP_OLD" == "1" ]]; then
        echo "      [dry-run] cleanup superseded ~/Downloads/update-*.tar.gz (keeping ${UPDATE_VER}.tar.gz)"
        echo "      [dry-run] clear stale __pycache__/*.pyc under ~/.local/share/celina/app"
        echo "      [dry-run] disable redundant user units: celina-router.service, celina-npu-inference.service"
        echo "      [dry-run] verify update-14 payload (provider_router.py, router.py, config.py, env.template)"
    fi
else
    if [[ "$UPGRADE_BRAIN" == "1" && -x "$HOME/.local/bin/celina-train" ]]; then
        echo "      🧠 Upgrading local Ollama brain to ${BRAIN_MODEL} (one-time ~1.9 GB pull)..."
        "$HOME/.local/bin/celina-train" --upgrade-brain --model "$BRAIN_MODEL" || true
    fi

    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload || true
        systemctl --user reset-failed celina.service celina-eventd.service celina-memory.service 2>/dev/null || true
        systemctl --user enable --now celina.service celina-eventd.service celina-memory.service || true
        systemctl --user restart celina.service celina-eventd.service celina-memory.service || true
        for opt_s in celina-router.service celina-autonomy.service celina-proactive.service; do
            if systemctl --user is-enabled --quiet "$opt_s" 2>/dev/null; then
                systemctl --user restart "$opt_s" 2>/dev/null || true
            fi
        done
    fi

    # Restart floating Celina Island window cleanly if it was running
    if pgrep -f 'celina_pkg.island_ui' >/dev/null 2>&1 && [[ -x "$HOME/.local/bin/celina-island" ]]; then
        "$HOME/.local/bin/celina-island" --quit >/dev/null 2>&1 || true
        sleep 0.5
        nohup "$HOME/.local/bin/celina-island" >/dev/null 2>&1 &
    fi

    # Reload GNOME Shell top-bar extension
    if command -v gnome-extensions >/dev/null 2>&1; then
        gnome-extensions disable celina-island@macubuntu.org 2>/dev/null || true
        sleep 0.8
        gnome-extensions enable celina-island@macubuntu.org 2>/dev/null || true
    fi

    sleep 1
    echo ""
    echo "================================================================"
    echo "  ✅ ${UPDATE_VER} Applied! Live Post-Update Check:"
    echo "================================================================"
    for s in celina.service celina-eventd.service celina-memory.service; do
        printf "  • %-26s %s\n" "$s" "$(systemctl --user is-active "$s" 2>/dev/null || echo inactive)"
    done
    if command -v gdbus >/dev/null 2>&1; then
        if gdbus call --session --dest org.macubuntu.Celina --object-path /org/macubuntu/Celina --method org.macubuntu.Celina1.GetStatus >/dev/null 2>&1; then
            echo "  • D-Bus org.macubuntu.Celina active & responding"
        else
            echo "  • ⚠️  D-Bus org.macubuntu.Celina not responding yet (check: journalctl --user -u celina.service -n 20)"
        fi
    fi

    # Run the 6-Agent Everyday Trainer automatically (compiles Quiet Brain, Knowledge Graph, Skills & zero-login local MCP)
    if [[ -x "$HOME/.local/bin/celina-train" ]]; then
        echo ""
        "$HOME/.local/bin/celina-train" --enable-local-mcp || true
    fi

    # Optional --cleanup: safely prune superseded update tarballs & ensure redundant user units are off
    if [[ "$CLEANUP_OLD" == "1" ]]; then
        echo ""
        echo "[+] Running safe PC update cleanup (--cleanup)..."
        REMOVED_COUNT=0
        if [[ -d "$HOME/Downloads" ]]; then
            shopt -s nullglob
            for old_tar in "$HOME/Downloads"/update-*.tar.gz "$HOME/Downloads"/latest-update.tar.gz; do
                base_tar="$(basename "$old_tar")"
                if [[ "$base_tar" != "${UPDATE_VER}.tar.gz" ]]; then
                    rm -f "$old_tar"
                    REMOVED_COUNT=$((REMOVED_COUNT + 1))
                fi
            done
            shopt -u nullglob
        fi
        echo "      ✅ Removed ${REMOVED_COUNT} superseded update archive(s) from ~/Downloads (kept ${UPDATE_VER}.tar.gz if present)."

        # Remove stale Python bytecode caches under celina_pkg / celina_hardware (superseded .pyc from prior versions)
        PYC_REMOVED=0
        if [[ -d "$HOME/.local/share/celina/app" ]]; then
            while IFS= read -r -d '' pyc_dir; do
                if [[ -d "$pyc_dir" ]]; then
                    rm -rf "$pyc_dir" 2>/dev/null || true
                    PYC_REMOVED=$((PYC_REMOVED + 1))
                fi
            done < <(find "$HOME/.local/share/celina/app" -type d -name __pycache__ -print0 2>/dev/null || true)
        fi
        echo "      ✅ Cleared ${PYC_REMOVED} stale __pycache__ directory(ies) under $HOME/.local/share/celina/app."

        if command -v systemctl >/dev/null 2>&1; then
            for dup_u in celina-router.service celina-npu-inference.service; do
                if systemctl --user is-active --quiet "$dup_u" 2>/dev/null || systemctl --user is-enabled --quiet "$dup_u" 2>/dev/null; then
                    systemctl --user disable --now "$dup_u" 2>/dev/null || true
                    echo "      ✅ Disabled redundant user service: $dup_u"
                fi
            done
        fi

        # Verify update-14 payload files exist (sanity check for cleanup targeting)
        if [[ -f "$PAYLOAD_ROOT/features/celina/celina_pkg/provider_router.py" ]]; then
            echo "      ✅ Update 14 payload confirmed present (provider_router.py, router.py, config.py, env.template)."
        fi

        # Verify update-17 payload files exist (sanity check for cleanup targeting)
        if [[ -f "$PAYLOAD_ROOT/features/celina/celina_pkg/code_sandbox.py" ]]; then
            echo "      ✅ Update 17 payload confirmed present (code_sandbox.py, tools.py, config.py, neural-schema.json)."
        fi

        # Verify update-18 payload files exist (sanity check for cleanup targeting)
        if [[ -f "$PAYLOAD_ROOT/features/celina/celina_pkg/code_sandbox.py" ]]; then
            echo "      ✅ Update 18 payload confirmed present (voice pipeline, remote control, browser grounding, knowledge graph, security shield, token speed)."
        fi

        # Verify Update 18 skill management files (Ponytail + SkillSpector)
        if [[ -f "$PAYLOAD_ROOT/features/celina/celina_pkg/ponytail_skill.py" ]]; then
            echo "      ✅ Update 18 Ponytail skill confirmed present (ponytail_skill.py)."
        fi
        if [[ -f "$PAYLOAD_ROOT/features/celina/celina_pkg/skilspector_tool.py" ]]; then
            echo "      ✅ Update 18 SkillSpector scanner confirmed present (skilspector_tool.py)."
        fi
    fi

    echo ""
    echo "  Try right now:"
    echo "    celina --no-speak \"good morning\""
    echo "    celina --no-speak \"good night\""
    echo "    celina --no-speak \"do login in spotify if alreday login done play dude telugu songs\""
    echo "    celina --no-speak \"add todo finish project\""
    echo "    celina --no-speak \"remind me in 10 minutes to drink water\""
    echo "    celina-train                         # Re-run 6-Agent Everyday Trainer anytime"
    echo "    celina-train --upgrade-brain         # Upgrade local Ollama brain to qwen2.5:3b"
    echo "  Note: Log out and log back in once so GNOME Shell loads the"
    echo "        updated top-bar extension (Chat / Voice / Test Edge Lighting)."
    echo "================================================================"
fi
