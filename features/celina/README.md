# Celina AI (Celly) & macOS UI Ecosystem (v4.4 — Ebbinghaus Memory, App Intents, PipeWire Loopback STT, Deep Ubuntu 26.04 & Complete iOS Continuity)

Ultra-lightweight **Celina (nickname: Celly)** self-improving desktop OS assistant built for **Ubuntu 26.04 LTS** on **8GB RAM / Wayland (GNOME)** (~18 MB boot RAM via stub launch, 0.0% idle CPU via zero-tick deep sleep, **99 tools across 8 domains — 92 offline-capable / 7 opt-in web-gated**, customizable assistant identity, default `qwen3:1.7b` / MoE switcher).

## Dynamic Assistant Name & Nickname (Default: `Celina` / `Celly`)

By default, your assistant is named **Celina** with nickname **Celly** (CLI aliases `celina` and `celly`), paired with natural feminine Piper neural voices in English (`en_US-amy-medium`), Telugu (`te_IN-maya-medium`), and Hindi (`hi_IN-priyamvada-medium`).

You can **update her name & nickname anytime** (no reinstall required) in any of **4 ways**:
1. **Live command in Dynamic Island or CLI:** `/name Aria` · `/name Aria Ari` · `/name reset` (resets to `Celina` / `Celly`).
2. **Natural language (Tool #60 `set_assistant_identity`):** Say *"Change your name to Nova with nickname Novi"* (works in English, తెలుగు, or हिन्दी) — updates the Dynamic Island header, prompt placeholder, window titles, Rofi Spotlight prompt, notifications, and system instructions immediately.
3. **Environment file (`~/.config/celina/env`):** Edit `CELINA_NAME="Celina"` and `CELINA_NICKNAME="Celly"`.
4. **Neural Schema (`~/.config/celina/neural-schema.json`):** Edit `"persona": { "name": "Celina", "nickname": "Celly" }`.

## Core Capabilities

1. **v4.2 Production QA Hardening & 4 Movie-Grade Assistant Features (0 MB Extra Idle RAM):**
   - **Barge-In Interruption (`voice.py`, `island_ui.py`):** Pressing `Super+Alt+C` or `Esc` immediately cuts off active Piper/espeak TTS mid-sentence so you can interrupt Celina naturally and start speaking right away.
   - **0-Token Executable Workspace Snapshots (`save_workspace_snapshot` — Tool #59):** Captures all currently visible desktop applications and open document paths via AT-SPI2 + `/proc/<pid>/cmdline` into a named reusable skill (`"Save this as my Exam Prep workspace"` → `"Launch my Exam Prep workspace"` replays your desk in 0 LLM tokens).
   - **Zero-Polling Clipboard Watchdog Smart Chip (`island_ui.py`):** Runs ONLY when the Dynamic Island drawer opens (0 background timers) to detect copied Python/JS/Java/GCC tracebacks (`🔧 Debug Clipboard Traceback`) or public URLs (`📄 Summarize Copied Link`) as a 1-click contextual chip.
   - **Dry-Run Simulation Mode (`router.py`):** Prefix any command with `simulate:` / `dry run:` (or pass `--dry-run`) to let Tier-1 read tools inspect real files while intercepting every Tier-2 side-effect tool (0 bytes modified on disk, full planned actions logged in `/why`).
   - **7 Real-World Ubuntu Wayland QA Fixes:**
     1. *Dynamic Island Self-Focus / Self-Screenshot Guard:* AT-SPI2 dynamically skips Celina's own windows/PID (even after renaming) and `capture_screenshot()` auto-collapses the drawer while capturing.
     2. *HiDPI / Fractional Scaling Accuracy:* `_get_logical_screen_size()` prefers AT-SPI2 root desktop extents (immune to XWayland scaling drift).
     3. *Ubuntu 24.04/26.04 AppArmor `bwrap` Probe:* Runtime `_can_use_bwrap()` check detects unprivileged userns restrictions and degrades cleanly with explicit approval transparency (plus symlink-safe secret masking & `~/.netrc` / `~/.git-credentials` protection).
     4. *Anti-CAPTCHA Resilient `web_search`:* Firefox Ubuntu User-Agent, DuckDuckGo HTML/Lite `GET` with HTTP 202/CAPTCHA detection, and zero-key Wikipedia Opensearch + HN Algolia fallbacks.
     5. *Cross-Process SQLite Reliability:* `PRAGMA busy_timeout=5000;` across `memory.py` and `cache.py` + O(log N) KG FTS deduplication.
     6. *Headless / Non-TTY Approval & Privacy Popups:* `sys.stdin.isatty()` detection with native GNOME `zenity` dialogs (fail-safe deny/`local` when headless).
     7. *Echo-Free Wake-Word Resume:* `WakeWordListener` resumes via `voice.speak_text(..., on_done=...)` strictly AFTER TTS playback + 250ms echo decay (supports custom `.onnx` wake models in `~/.local/share/celina/wakeword/`).
2. **Cognitive Neural Schema — Zero-RAM Multi-Agent Swarm, Knowledge Graph, Goals, Bubblewrap Sandbox & World Model:**
   - **Zero-RAM Role-Swapping Swarm (`router.py`, `neural-schema.json`):** Dynamically switches personas (`The Architect`, `The Coder / Engineer`, `The Researcher`, `The Critic / Verifier`) inside a single active model in <1 ms, adds **Tree-of-Thought** deliberation on Gear 3 + Critic audits on Tier-2 actions, and logs the active Swarm Role in `/why` Decision Receipts.
   - **Semantic Knowledge Graph, Persistent Goals & Sensory-Motor Learning (`memory.py`, `tools.py`):**
     - **`kg_triples` (`link_knowledge`, `/kg`):** Stores `(Subject --[Predicate]--> Object)` triples in SQLite + FTS5 with 1-hop & 2-hop traversal and strict `cloud_safe` isolation.
     - **`goals` (`manage_goal`, `/goals`):** Persists multi-step projects, sub-tasks, execution budgets, and deadlines across reboots and auto-injects active goals into context.
     - **`sensory_schemas`:** Records `(active_app, tool_name, success/failure)` after every tool call so Celina warns herself about app-specific failure modes before repeating mistakes.
   - **Bubblewrap (`bwrap`) Zero-RAM Sandbox & IoT Webhook Bridge (`tools.py`, `stages/10-base-deps.sh`):** `run_shell_command` and `run_python_sandbox` run inside Ubuntu's unprivileged `bwrap` Linux namespace by default (`--ro-bind / /`, `--unshare-pid`, empty `--tmpfs` over `~/.config/celina`, `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.kube` at 0 MB idle RAM & 3ms startup). Includes approval-gated `trigger_iot_webhook` with explicit host/port/method allowlisting in `~/.config/celina/iot-allowlist.json` (`0600`) for Home Assistant / Node-RED / LAN smart-home automation.
   - **Proactive World Model (`island_ui.py`):** Piggybacks on the existing 10-minute maintenance wakeup (0 new timers, 0 subprocesses, <0.1 ms reading `/proc/meminfo` & `/sys/class/power_supply/BAT*`) to alert on RAM >= 88% or discharging battery <= 18%.
3. **Self-Improving, Glass-Box Architecture (0 MB Extra Idle RAM):**
   - **Hybrid 8-Domain + Lexical Tool Router, ActionGate & TOON UI Maps (`tools.py`, `router.py`, `automation.py`, `neural-schema.json` v4.4):** All **99 tools** (`92` offline-capable, `7` web-gated) partitioned across 8 focused domains (`os_hardware`, `apple_pim_writing`, `continuity_audio_privacy`, `files_office`, `web_comms`, `wayland_gui`, `skills_memory`, `mcp_system`) with EN/TE/HI word-boundary triggers + zero-dependency lexical fallback + small-model arg coercion + `ActionGate` restraint on informational questions; compact tabular **TOON** desktop perception (`# A11Y(role|label|x,y)` and `# OCR(x,y|text)`) cuts UI map tokens by **~45%**.
   - **Hybrid Semantic & Embedding Engine (`embeddings.py`, `memory.py`, `tools.py`, `cache.py`, `spotlight_index.py`):** Lazy, zero-idle-RAM multilingual embeddings (`Ollama embeddinggemma` `keep_alive="30s"` or `Model2Vec potion-multilingual-128M`, 256d Matryoshka `float16` BLOBs = 512 bytes/item, optional `sqlite-vec`) fused with SQLite FTS5 BM25 via **Reciprocal Rank Fusion (RRF k=60)** across cross-lingual EN/TE/HI Memory, the 99-tool Semantic Router, Paraphrase Response Cache (`sim >= 0.92`), and CoreSpotlight document chunks — with instant 0 ms fallback to BM25 when offline/unpulled.
   - **v4.4 Cognitive, Ubuntu 26.04 & iOS Continuity Suite:** Ebbinghaus temporal memory decay & KG functional-predicate contradiction superseding (`memory.py`), 0-token compound `prepare_meeting_context` brief (`app_intents.py`), 100% offline PipeWire `@DEFAULT_AUDIO_SINK@.monitor` meeting transcriber (`transcribe_system_audio`), self-verifying Bubblewrap Python sandbox (`run_python_sandbox` + Coder/Engineer Critic loop), zero-polling Systemd Crash Doctor + UDisks2 USB Guard + Freedesktop Secret Vault status + MIME default handler switcher + OS parity health telemetry (`os_events.py`), plus USB iPhone mount (`ifuse`), Continuity Camera (`UxPlay` → `v4l2loopback`), iPad Sidecar + Apple Pencil (`Weylus`), and BLE ANCS call accept/dismiss (`continuity.py`).
   - **3-Tier Memory with SQLite FTS5 BM25 & Episodic Compaction (`memory.py`):** Built-in SQLite FTS5 (`porter unicode61` for EN/TE/HI) retrieves only top-K relevant facts, KG links & episodes at **0 MB extra RAM** while strictly isolating private `cloud_safe=0` turns. Before pruning chats past 200 rows, the oldest 40 turns auto-compact into 3-bullet `episodes`.
   - **Glass-Box Explainability Receipts (`router.py`, `island_ui.py`):** Every turn records a `TurnTrace` (gear & trigger reason, active Swarm Role, pruned tool count & domains, BM25 recalled facts/episodes, active `SKILL.md`, itemized PII redaction summary, cache status, dry-run simulated actions, self-corrections, latency). Inspect anytime via **`/why`** or the clickable **`ⓘ Why`** pill button in Dynamic Island.
   - **Autonomous Plan → Execute → Reflect & Astra Goal Verification (`router.py`, `computer_use.py`):** Pre-flight micro-planner + uncertainty policy on Gear 2/3, automatic `[CRITIC VERIFIER — SELF-CORRECTION REQUIRED]` reflexion + duplicate-failure guards on tool errors in both cloud & local loops, and live desktop goal-state verification before Project Astra accepts `action == "done"`.
   - **Continual Learning (`skills.py`, `memory.py`, `router.py`):** Automatically persists permanent preference rules from implicit user corrections (`"no, I meant..."`, `"always use..."`, `"అలా కాదు"`, `"नहीं मेरा मतलब"`), offers 1-phrase **Auto-Healing** (`heal skill <name>`) when a manual sequence fixes a previously failed skill, and mines recurring 2–5 tool workflows into 1-click skill suggestions after 3 runs.
   - **Romanized Tenglish/Hinglish & RAM-Tiered Local Reasoning (`voice.py`, `router.py`):** Native + Romanized Telugu (`Tenglish`) and Hindi (`Hinglish`) particle detection (`detect_language_detail`) plus a lightweight 3-bullet `<analysis>` reasoning scaffold on Gear 3 (`deep`) across RAM-matched Dense & Sparse MoE Ollama models (`qwen3:1.7b` / `qwen3:4b` / `qwen3.5:9b` / `gemma4:26b` / `qwen3.5:35b-a3b`) with clean streaming tag stripping.
4. **Apple-Style 8GB RAM OS Engineering (`island_ui.py`, `router.py`, `tools.py`, `voice.py`, `features/celina/install.sh`, `system/sysctl/99-macubuntu-vm.conf`):**
   - **Stub Launch & Lazy Loading (~18 MB boot RAM):** At login, `celina-island` loads ONLY the compact GTK pill and `config`. `CelinaRouter`, SQLite DBs, 99 tool schemas, and the fullscreen `EdgeGlowOverlay` initialize on first interaction.
   - **Pro-Grade Voice Pipeline (`voice.py`):** PipeWire automatic background music ducking (`PipeWireAudioDucker`), zero-PyTorch **Silero VAD v5 ONNX** (with automatic RMS fallback), sentence-streamed TTS (`SentenceStreamSpeaker`) with barge-in, native trilingual Piper (EN/TE/HI), and opt-in **Kokoro-82M ONNX** for English/Hindi.
   - **Zero-Tick Deep Sleep (0.0% idle CPU):** Reminder checks stop completely when 0 timers exist; media titles use in-process D-Bus MPRIS (`NameOwnerChanged`, 0 `playerctl` forks) and sleep at 0 ticks whenever no player runs.
   - **Heap Compaction & Instant Offload:** Periodic maintenance + `/offload` run `gc.collect()` + `libc.malloc_trim(0)`, destroy hidden fullscreen Cairo surfaces, unload idle MCP/Whisper workers (180s), and immediately eject Ollama (`keep_alive: 0`, auto-sleep `45s`).
   - **Ollama `q8_0` KV-Cache + ZRAM Tuning + macOS App Nap (`toggle_app_nap`):** Flash Attention + 8-bit KV cache + `num_ctx=4096`, `vm.swappiness=150` pure-ZRAM kernel tuning (`system/sysctl/99-macubuntu-vm.conf`), and lossless cgroup v2 / `SIGSTOP`/`SIGCONT` App Nap to freeze unfocused Firefox/Chrome/VS Code tabs to 0% CPU.
5. **Unified Rofi Spotlight → Celina Bridge, Highlighted Text, Edge Lighting & Project Astra:**
   - **`Super+Space` (`celina-spotlight`):** Launches matching `.desktop`/Flatpak apps with WhiteSur icons, or sends any typed question/command straight into Dynamic Island via 4096-byte Unix socket IPC (`--ask`), dynamically reading your active assistant name.
   - **Apple Intelligence Edge Lighting (`edge_glow.py` + `island_ui.py`):** Flowing iridescent pill halo during voice/thinking/streaming plus click-through fullscreen monitor perimeter aurora while Celina executes desktop tasks.
   - **Project Astra Computer-Use (`computer_use.py` + `automation.py` + `portal_input.py`):** Observe → Think → Act → Verify loop combining GNOME AT-SPI2, offline multilingual Tesseract OCR, optional privacy-gated Cloud Vision, Tier-1 GNOME `org.freedesktop.portal.RemoteDesktop` D-Bus input + `InputBroker` (zero `/dev/uinput` or broad `input` group required; optional opt-in `--experimental-uinput` `ydotool` fallback).
   - **Smart Cache, Ephemeral Open-Source MCP Connectors, Safe LibreOffice/ODT Editing & Zero-Leak Privacy Firewall.**

## Repository Structure (Inside `macubuntu/features/celina/`)

```text
macubuntu/features/celina/
├── install.sh                  # Self-contained Celina feature installer (called by stages/70-feature-celina.sh)
├── README.md
├── config/
│   ├── env.template
│   ├── neural-schema.json
│   ├── mcp-servers.json
│   └── iot-allowlist.json
├── skills/
│   ├── code-debugger.md
│   ├── document-polisher.md
│   ├── web-research-brief.md
│   ├── ubuntu-sysadmin.md
│   └── daily-planner.md
├── celina_hardware/                   # 7 hardware, compute offload, audio routing, privacy dot & biometric modules
│   ├── __init__.py
│   ├── models.py
│   ├── adapters.py
│   ├── biometrics.py
│   ├── backends.py
│   ├── compute.py
│   └── detect_gpu.py
└── celina_pkg/                        # 47+ hardened Python modules (+ autonomy/, planner/, proactive/, learning/)
    ├── __init__.py
    ├── config.py
    ├── memory.py
    ├── embeddings.py
    ├── cache.py
    ├── privacy.py
    ├── net_policy.py
    ├── approval.py
    ├── command_runner.py
    ├── documents.py
    ├── portal_input.py
    ├── automation.py
    ├── computer_use.py
    ├── mcp_client.py
    ├── skills.py
    ├── os_events.py
    ├── spotlight_index.py
    ├── continuity.py
    ├── ios_ancs.py
    ├── calendar_bridge.py
    ├── app_intents.py
    ├── writing_tools.py
    ├── biometric_anim.py
    ├── edge_glow.py
    ├── voice.py
    ├── tools.py
    ├── router.py
    └── island_ui.py
```

## Installation & Shortcuts

```bash
# Installed automatically as part of the unified macubuntu OS setup:
cd macubuntu
./install.sh --profile default-8gb

# Or install/upgrade ONLY the Celina feature:
./install.sh --only 70
# (or standalone: bash features/celina/install.sh)
```

Reboot once after install, then use:
- **`Super + Shift + C`** — Toggle Celina Dynamic Island
- **`Super + Alt + C`** — Instant voice capture / Barge-In (`celina-voice`)
- **`Super + Space`** — macOS-style Rofi Spotlight Search & Celina Bridge
- **`Super + Shift + W`** — Apple Intelligence Writing Tools (`celina-writing-tools`)
- **`Super + Shift + T`** — macOS Live Text Region OCR (`celina-live-text`)
- **`Esc`** — Instant Barge-In silence (during speech) or Emergency Stop (during GUI/Astra tasks)
- **CLI Commands:** `celina` or `celly` (e.g. `celly "check RAM"`, `celina --dry-run "..."`, `celly --voice`)
- **Built-in Commands:** `/model`, `/ram`, `/offload`, `/name <NewName> [Nickname]`, `/name reset`, `/why`, `/goals`, `/kg`, `/skills`, `/cache`, `/cache clear`, `/mcp`, `/facts`, `/clear`, `simulate: ...`, `stop`
