# Celina AI OS (`macubuntu` v4.4.0 · Update 13) — Detailed Architecture Manual

> **Verified Live Codebase Metrics:** **79 Python modules (`44,682 LOC`)** · **99 Tools** (`60 Auto` / `39 Confirm-Gated`) · **17 SQLite Tables** · **6 Everyday Gear-0 Agents (`80/80` Trainer)** · **27 Slash Commands** · **29 Systemd Units/Configs** · **GNOME Shell Extension + GTK3 Dynamic Island** · **223/223 Pytest Verified**

---

## 1. High-Level System Architecture (End-to-End Flow)

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                     LAYER 1 — USER INTERFACES & SENSORS                      │
│  • GNOME Top-Bar Extension (celina-island@macubuntu.org — ESM JS + D-Bus)    │
│  • Floating Dynamic Island Overlay (island_ui.py — GTK3/Gdk3 + Unix IPC)     │
│  • Terminal Interactive REPL (celina CLI — 27 instant slash commands)        │
│  • Voice Stack (voice.py — Wake Word, Whisper STT, Piper TTS, Esc Barge-In)  │
│  • Desktop Hotkeys (Super+Shift+W Writing Tools · Super+Shift+T Live Text)   │
│  • Event Sensors (os_events.py PipeWire Privacy Dots · ios_ancs.py BLE ·     │
│                   biometric_anim.py fprintd · ebpf_sensor.py)                │
└───────────────────────────────────────┬──────────────────────────────────────┘
                                        │ Unix Socket / D-Bus / Direct Call
                                        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    LAYER 2 — COGNITIVE ROUTER (router.py)                    │
│  Step 1: Slash Commands (0 ms)  -> /help /ram /why /facts /kg /bg /swarm     │
│  Step 2: Privacy & DLP Shield   -> privacy.py (Secret strip / PII redact)    │
│  Step 3: Gear-0 Fast-Path       -> everyday_agents.py (6 agents) + skills.py │
│  Step 4: Smart Cache Check      -> cache.py (zero-cost repeated Q&A hits)    │
│  Step 5: Context & Memory Load  -> MemWeave 3-way recall (memory.py)         │
│  Step 6: Tiered LLM Execution   -> Fast / Tactical / Deep / Local Ollama /   │
│                                    Cloud API + Reflection + AgentFuse        │
│  Step 7: Glass-Box Trace        -> TurnTrace (/why explainability receipt)   │
└──────────────┬────────────────────────────────────────┬──────────────────────┘
               │                                        │
               ▼                                        ▼
┌──────────────────────────────────────┐ ┌─────────────────────────────────────┐
│   LAYER 3 — MEMORY & KNOWLEDGE CORE  │ │  LAYER 4 — AUTONOMY & PLANNING      │
│  • memory.py + memory_tiers.py       │ │  • planner/ (TaskGraph, TreePlanner,│
│  • 17 SQLite Tables (WAL mode)       │ │    ControlFlow, Scaffolding)        │
│  • FTS5 + Dense Vectors (embeddings) │ │  • autonomy/ (Engine, AsyncSuper-   │
│  • Knowledge Graph (kg_triples +     │ │    visor /bg, Checkpoint, Saga      │
│    kg_promoter.py)                   │ │    Rollback, SelfHealing,           │
│  • skills.py (Record/Learn/Quarantine│ │    Reliability, MCP Loader)         │
│  • learning/self_improve.py          │ │  • proactive/daemon.py (idle checks)│
│  • everyday_trainer.py (80 cases)    │ │  • evidence.py (audit ledger)       │
└──────────────┬───────────────────────┘ └──────────────────┬──────────────────┘
               └────────────────────┬───────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│            LAYER 5 — ZERO-TRUST GUARDRAILS & EXECUTION GATEWAY               │
│  • approval.py (60 Auto-Run vs 39 Confirm-Gated tools)                       │
│  • guardrails.py + governance.py (AgentFuse circuit breaker, risk policy)    │
│  • net_policy.py (SSRF blocker: loopback/RFC1918/link-local + scheme guard)  │
│  • landlock_policy.py + os_hardening.py (Bubblewrap + Landlock LSM + rlimits)│
│  • keychain_login.py (OS Keyring / Secret Service vault)                     │
└───────────────────────────────────────┬──────────────────────────────────────┘
                                        ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│           LAYER 6 — 99 OS TOOLS, ACTUATORS & HARDWARE ADAPTERS               │
│  • tools.py (5,220 LOC) + api_actuators.py + command_runner.py + state_ops.py│
│  • GUI & Vision: automation.py (AT-SPI2 safe-probe + OCR), computer_use.py,  │
│                  portal_input.py (Wayland RemoteDesktop), semantic_blueprint │
│  • Office & Docs: documents.py + libreoffice_uno.py + writing_tools.py       │
│  • Search & Apps: spotlight_index.py + app_intents.py + calendar_bridge.py   │
│  • Ecosystem: continuity.py (iOS/iPad/Android/AirPlay) + macos_glue.py       │
│  • MCP Bridge: mcp_client.py + mcp_server.py + memory_mcp_server.py          │
│  • Hardware Layer: celina_hardware/ (GPU/NPU/Vulkan/ROCm/OpenVINO/Biometrics)│
│  • Performance: turbo_mode.py + inference_adaptive.py (8 GB RAM Guard)       │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detailed Layer-by-Layer Breakdown

### Layer 1 — User Interfaces, Desktop Integration & Event Sensors

| Component | File(s) | LOC | Exact Design & Mechanism |
|---|---|---:|---|
| **GNOME Top-Bar Extension** | `system/gnome/extensions/celina-island@macubuntu.org/{extension,dbus,prefs}.js` | ESM JS | Native GNOME Shell top-bar pill (`✦ Celina`). Subscribes to `org.macubuntu.Celina` over Session D-Bus and spawns `~/.local/bin/celina-island --toggle` / `--voice` via `Gio.Subprocess`. |
| **Dynamic Island Floating UI** | `celina_pkg/island_ui.py` | 1,880 | Single-instance **GTK3 (`Gtk 3.0` + `Gdk 3.0` pinned)** floating top-center pill (`310×42` collapsed → `540×350` expanded). Uses **Unix Domain Socket IPC** (`/run/user/$UID/celina_island_$UID.sock`) for instant re-triggering. **Zero-Tick Deep Sleep**: zero idle polling timers — wakes ONLY on D-Bus MPRIS media signals, `Gio.FileMonitor` changes on `live_os_events.json`, or scheduled SQLite reminders. Includes Mode switcher (`AUTO/CLOUD/LOCAL`), Language badge, TTS mute, Clipboard Watchdog (traceback/URL detection on expand only), Approval & Privacy consent cards, Control Center hardware bar, and `ⓘ Why` receipt viewer. |
| **Edge Glow Overlay** | `celina_pkg/edge_glow.py` | 377 | Non-intrusive GTK3/Cairo translucent screen-edge aura showing live status: Voice Listening, Thinking/Automation Active, or Skill Recording. |
| **Biometric Animation** | `celina_pkg/biometric_anim.py` | 516 | Observes real Linux **`fprintd`** & **GNOME ScreenSaver** D-Bus signals (zero access to raw biometric templates) to animate Face ID / Touch ID feedback inside Dynamic Island. |
| **OS Event Daemon** | `celina_pkg/os_events.py` + `celina-eventd.service` | 1,002 | Event-driven daemon monitoring PipeWire mic/camera/screen-share streams, Focus Mode, and Secret Vault lock status, writing `~/.local/share/celina/live_os_events.json` for zero-CPU UI Privacy Dots (`🟠 Mic` · `🟢 Cam` · `🟣 Screen`). |
| **D-Bus IPC Daemon** | `celina_pkg/dbus_service.py` + `org.macubuntu.Celina.*` | 502 | Exposes `org.macubuntu.Celina` on Session D-Bus for native desktop clients, GNOME extension status updates, and identity sync. |
| **Voice Stack** | `celina_pkg/voice.py` + `celina-voice.service` | 1,308 | Handles wake-word listening (`WakeWordListener`, auto-paused during TTS to eliminate speaker-to-mic loops), VAD speech capture, Whisper STT, Piper/espeak TTS, and instant `Esc` **Barge-In** interruption. |
| **iOS BLE ANCS Bridge** | `celina_pkg/ios_ancs.py` | 909 | Connects over Linux BlueZ GATT to Apple Notification Center Service (ANCS) on a paired iPhone to mirror notifications into Dynamic Island and send positive/negative actions. |

---

### Layer 2 — The Cognitive Router (`router.py`, `2,579 LOC`)

Every user message passes through a strict **7-stage decision pipeline**:

1. **Instant Slash Commands (`0 ms`, zero LLM):**
   - **27 commands:** `/help`, `/commands`, `/model`, `/tier`, `/ram`, `/proc`, `/offload`, `/turbo`, `/lite`, `/lag-fix`, `/lang`, `/name`, `/facts`, `/goals`, `/kg`, `/graph`, `/mem`, `/skills`, `/train`, `/mcp`, `/cache`, `/clear`, `/why`, `/struggle`, `/bg`, `/jobs`, `/swarm`.
   - Also supports prompt prefixes: `quick: ...`, `deep: ...`, `simulate: ...` / `dry-run: ...`, and `when I say <trigger>, <action>`.
2. **Privacy & Secret Shield (`privacy.py`, `206 LOC`):**
   - **Hard-Stripped Secrets:** Private keys (`BEGIN PRIVATE KEY`), API keys (`sk-*`, `ghp_*`, `AIza*`, `AKIA*`), and `password=...` tokens are **NEVER** sent to external cloud APIs.
   - **Reversible PII Redaction:** Emails, PAN, Aadhaar, Indian phone numbers, and card numbers are redacted locally before cloud requests (or prompt the user to force **Local Ollama Only** via `consent_cb`).
3. **Gear-0 Deterministic Fast-Path (`everyday_agents.py`, `1,222 LOC` + `skills.py`):**
   - **6 Everyday Agents** execute common OS intents deterministically in **`0–170 ms`** with **0 LLM tokens**:
     - `MediaAgent` (YouTube/Spotify search, play/pause/next/prev/status)
     - `WebAgent` (open safe URLs, Google/Wikipedia lookups)
     - `FilesNotesAgent` (quick local file/folder/note operations)
     - `SystemAgent` (dark/light mode, night light, DND, volume, brightness, Wi-Fi/BT, lock, screenshot, stats)
     - `RoutineAgent` (timers, reminders, `remember that ...`, `when I say ...` skills)
     - `GuiSafetyAgent` (safe window/UI inspection & stop controls)
   - Verified by `everyday_trainer.py` (**80/80 test curriculum in ~12–18 ms**).
4. **Smart Response Cache (`cache.py`, `492 LOC`):**
   - Two-tier RAM + disk cache for static deterministic Q&A; automatically bypassed for live OS/time/weather/mutating queries.
5. **MemWeave 3-Way Context Assembly:**
   - Dynamically classifies incoming queries into **Factual** (keyword/FTS5), **Associative** (vector similarity), or **Relational** (Knowledge Graph traversal) and injects only the minimal high-signal context into the prompt.
   - Enforces **Strict English Language Lock** (`LANGUAGE RULE` in `_build_messages()`) so English prompts ALWAYS receive English answers (explicit Telugu/Hindi prompts still receive Telugu/Hindi).
6. **4-Tier Cognitive Compute Split (`inference_adaptive.py`, `601 LOC`):**
   - **Tier 0 (Reflex Only):** Gear-0 + slash commands only (`0 MB` model RAM).
   - **Tier 1 (Local Fast — Ollama `qwen3:1.7b`):** 100% offline/private, RAM-guarded for 7.6 GiB systems (`ollama-8gb-ram.conf` + dynamic context cap & `keep_alive` offload).
   - **Tier 2 (Cloud Fast/Tactical — currently `openrouter/free`; upgrading to 6-Provider Smart Router in Update 14):** Multi-step tool execution (`max_steps`: Fast=2, Tactical=4, Deep=5) with tool-domain filtering (`tool_domains` attaches only relevant tools per turn instead of dumping all 99 schemas!).
   - **Tier 3 (Deep / Swarm / Async `/bg`):** Multi-role Planner → Researcher → Coder → Critic verification + non-blocking `AsyncSupervisor`.
   - **Self-Correction & AgentFuse:** Automatically catches tool errors (`reflection_policy`) and trips `agentfuse_tripped:<rung>` circuit breaker if a tool loop repeats without progress.
7. **Glass-Box Explainability (`TurnTrace` / `/why`):**
   - Records exact `gear`, `gear_reason`, `swarm_role`, `detected_lang`, `tool_domains`, `tools_attached/total`, `tools_called`, `recalled_facts`, `privacy_summary`, `cache_status`, `self_corrections`, `compute_backend`, and latency for every single turn.

---

### Layer 3 — Unified Hybrid Memory, Knowledge Graph & Skills (`memory.py`, `2,274 LOC`)

Single local-first **SQLite WAL database** (`~/.local/share/celina/memory.db`, concurrency-tested across CLI + Dynamic Island + daemons with `busy_timeout`) containing **17 tables**:

| Table Group | Tables | Purpose |
|---|---|---|
| **Short-Term & Episodic** | `conversations`, `episodes` | Sliding chat history (`/clear` clears `conversations` only) + summarized multi-turn task episodes. |
| **Semantic & Hybrid Vector/FTS** | `facts`, `memory_fts`, `memory_vectors`, `hybrid_memories`, `hybrid_memories_fts` | Persistent user facts (`/facts`), FTS5 BM25 full-text indexes, local dense embeddings (`embeddings.py`), and unified hybrid retrieval. |
| **Knowledge Graph** | `kg_triples`, `hybrid_graph_edges`, `compiled_knowledge`, `compiled_knowledge_fts` | Structured `(subject, predicate, object)` graph (`/kg`, `/graph`) promoted automatically by `kg_promoter.py`. |
| **Procedural & Behavioral** | `skills`, `skill_runs`, `habit_patterns`, `sensory_schemas` | Learned & recorded skills (`skills.py`), execution counters, learned habits, and UI layout blueprints (`semantic_blueprint.py`). |
| **Goals & Proactive Timers** | `goals`, `reminders` | Long-running tracked goals (`/goals`) and persistent SQLite background reminders. |

**Skills & Quarantine Lifecycle (`skills.py`, `874 LOC`):**
- Create skills 3 ways: natural `"when I say <X>, do <Y>"`, live `start_recording`/`stop_recording` (shows red `🔴 REC` in Dynamic Island), or `save_last_actions_as_skill`.
- Third-party community skills (`install_community_skill`) **always land in Quarantine first** — require explicit inspection & `approve_quarantined_skill` / `reject_quarantined_skill` before execution.

---

### Layer 4 — Autonomy, Planning & Proactive Subsystems

| Module | LOC | Responsibility |
|---|---:|---|
| `planner/{task_graph,tree_planner,control_flow,scaffolding}.py` | 735 | Decomposes multi-step goals into dependency DAGs, conditional branches, and structured sub-tasks. |
| `autonomy/engine.py` + `checkpoint.py` + `saga.py` | 979 | Durable multi-step goal runner with JSON checkpoints and compensating **Saga rollback** if a step fails mid-workflow. |
| `autonomy/supervisor.py` | 360 | Lazy-loaded **`AsyncSupervisor`** (`0 MB` RAM until invoked via `/bg <goal>` or `/jobs`) executing long research/coding jobs off-thread with evidence logging. |
| `autonomy/reliability.py` + `self_healing.py` | 1,017 | Retry budgets, degraded-mode fallbacks, and automatic recovery probes. |
| `autonomy/mcp_loader.py` | 1,004 | Discovers, validates, and lazily connects external Model Context Protocol servers. |
| `proactive/daemon.py` + `learning/self_improve.py` | 547 | Background idle checks (`celina-proactive.service`) & scheduled self-evaluation (`celina-self-improve.timer`). |
| `evidence.py` + `telemetry.py` | 938 | Append-only action evidence ledger & performance/struggle diagnostics (`/struggle`). |

---

### Layer 5 — Zero-Trust Security, Sandboxing & Guardrails

| Defense Layer | Module(s) | Exact Protection Guarantee |
|---|---|---|
| **Tool Risk Classification** | `approval.py`, `governance.py` | **60 Auto-Approved** read-only/low-risk tools run immediately; **39 Mutating/System** tools (`run_shell_command`, `write_file`, `trash_file`, `compose_message`, GUI clicks, etc.) enforce explicit user approval (CLI prompt or Dynamic Island Approve/Deny card). |
| **Network & SSRF Shield** | `net_policy.py` (`650 LOC`) | Blocks loopback (`127.0.0.0/8`, `::1`), RFC1918 private LAN (`10/8`, `172.16/12`, `192.168/16`), and cloud metadata (`169.254.169.254`); `open_url_safe()` strictly rejects non-`http(s)` schemes (`file://`, `javascript:`, `smb://`) before URL normalization. Outbound webhooks/comms require opt-in `web_comms` allowlist (`config.is_web_comms_enabled()`). |
| **Code Sandbox Isolation** | `landlock_policy.py`, `os_hardening.py` | `run_python_sandbox` isolates untrusted Python with **Bubblewrap (`bwrap`)** (RO root, isolated `/work` tmpdir, disabled network) with automatic **Linux Landlock LSM + `rlimits` fallback** for Ubuntu 26.04 AppArmor `userns` restrictions. |
| **Desktop & Password Safety** | `automation.py`, `guardrails.py` | `is_focused_password_field()` strictly refuses reading or typing inside password boxes. Destructive file ops use recoverable freedesktop **Trash** (`trash_file`) instead of `rm -rf`. `NoNewPrivileges=true` enforced in systemd units (`celina.service`, `celina-eventd.service`). |
| **AT-SPI2 Crash Isolation** | `automation.py` | `_safe_get_atspi_desktop()` probes AT-SPI2 inside a throwaway subprocess with `RLIMIT_CORE=0` first so C-level accessibility `SIGABRT` bugs on Ubuntu 26.04 can never crash Celina. |
| **Secret Storage** | `keychain_login.py` | Integrates with OS Keyring (`org.freedesktop.secrets`) & locked vault monitoring (`get_secret_vault_status`). |

---

### Layer 6 — The 99 OS Tools, Actuators & Hardware Stack

Every tool maintains **exact 7-way parity** across schemas, dispatch tables, risk policies, auto-maps, domains, docs, and tests (`test_audit_fixes_and_parity.py`):

| Domain | Key Modules | Representative Tools (99 Total) |
|---|---|---|
| **1. System, Power & Audio** | `tools.py`, `turbo_mode.py`, `state_ops.py` | `get_system_stats`, `get_hardware_capabilities`, `set_volume`, `set_brightness`, `control_connectivity`, `set_focus_mode`, `toggle_app_nap`, `route_app_audio`, `manage_default_apps`, `diagnose_systemd_service` |
| **2. Apps, Media & Identity** | `tools.py`, `app_intents.py`, `voice.py` | `open_application`, `close_application`, `list_running_apps`, `control_media`, `transcribe_system_audio`, `set_assistant_identity` |
| **3. Web, Live Info & Browser** | `tools.py`, `net_policy.py` | `web_search`, `fetch_webpage`, `fetch_url_text`, `get_weather`, `get_news_headlines`, `get_current_time_zone`, `search_browser_bookmarks` |
| **4. Files, Spotlight & Writing** | `tools.py`, `spotlight_index.py`, `writing_tools.py` | `search_files`, `search_in_files`, `read_file`, `write_file`, `edit_file`, `move_file`, `trash_file`, `spotlight_query`, `get_clipboard`, `set_clipboard`, `get_highlighted_text`, `writing_tool_transform` |
| **5. Documents & LibreOffice** | `documents.py`, `libreoffice_uno.py` | `open_document`, `read_document`, `create_document`, `edit_document`, `convert_document`, `libreoffice_edit` |
| **6. Memory, Calendar & Comms** | `memory.py`, `calendar_bridge.py` | `remember_fact`, `search_memory`, `link_knowledge`, `manage_goal`, `set_timer`, `get_upcoming_events`, `create_calendar_event`, `search_contacts`, `prepare_meeting_context`, `compose_message` |
| **7. Skills & Snapshots** | `skills.py`, `tools.py` | `learn_skill`, `save_last_actions_as_skill`, `start_recording`, `stop_recording`, `list_skills`, `update_skill`, `forget_skill`, `run_skill`, `save_workspace_snapshot`, `install_community_skill`, `list/approve/reject_quarantined_skill` |
| **8. Vision, OCR & GUI** | `automation.py`, `computer_use.py`, `portal_input.py`, `api_actuators.py` | `read_screen`, `find_text_on_screen`, `click_on_text`, `click_position`, `type_text`, `press_hotkey`, `inspect_ui_elements`, `scroll_screen`, `drag_and_drop`, `autonomous_gui_task`, `computer_click`, `computer_type`, `vision_ocr`, `vision_analyze`, `ghostdesk_click`, `live_text_ocr`, `search_active_app_menus` |
| **9. Code, Shell, MCP & Goals** | `tools.py`, `mcp_client.py`, `autonomy/` | `run_shell_command`, `run_python_sandbox`, `mcp_list_servers`, `mcp_list_tools`, `mcp_call_tool`, `submit_goal`, `cache_status`, `clear_cache`, `trigger_iot_webhook` |
| **10. Continuity & Security** | `continuity.py`, `ios_ancs.py`, `macos_glue.py` | `get_continuity_status`, `mount_ios_device`, `start_ios_continuity_camera`, `start_ipad_sidecar_weylus`, `respond_ios_notification`, `launch_android_mirror`, `control_airplay_mirror`, `audit_app_permissions`, `get_secret_vault_status` |

**Hardware Abstraction (`celina_hardware/`, `1,943 LOC`):**
- `detect_gpu.py` + `compute.py` + `adapters.py`: Auto-detects Intel/AMD/NVIDIA GPU, NPU, RAM ceiling, and selects the right inference backend (`backends/vulkan.py`, `rocm.py`, `openvino_npu.py`, or CPU-optimized Ollama).
- `biometrics.py`: Hardware-backed `fprintd` / PAM sensor capability detection.

---

## 3. On-Disk Runtime Layout (Installed Ubuntu 26.04 PC)

```text
~/.local/
├── bin/
│   ├── celina                  # Main interactive CLI REPL launcher
│   ├── celina-island           # Floating GTK3 Dynamic Island launcher (IPC client/server)
│   ├── celina-train            # 6-Agent 80-case Everyday Trainer CLI
│   ├── celina-turbo            # Zero-Lag 8 GB Turbo Mode toggle
│   └── celina-login-save       # Secure session/login helper
├── share/
│   ├── celina/
│   │   ├── app/                # Installed celina_pkg/ + celina_hardware/ + config/
│   │   ├── venv/               # Isolated Python 3.14 venv (--system-site-packages for PyGObject)
│   │   ├── memory.db           # Unified 17-table SQLite WAL Quiet Brain + KG + Skills + Reminders
│   │   └── live_os_events.json # Event-driven Privacy Dots + Focus Mode + Vault state
│   └── gnome-shell/extensions/
│       └── celina-island@macubuntu.org/   # Native GNOME top-bar extension
└── config/
    ├── celina/                 # User overrides, mcp-servers.json, iot-allowlist.json
    └── systemd/user/           # celina.service, celina-eventd.service, timers, slices
~/.ai-env                       # Mode 0600 API keys & provider env settings
```

---

## 4. Planned Evolution Roadmap (Queued After Your 1,000-Item Report)

| Release | Architectural Upgrade (Zero Duplication + Auto Old-Pattern Cleanup via `apply-update.sh --cleanup`) |
|---|---|
| **Update 14** *(Next)* | Fix all findings from `smoke-30-report.md` + `full-1000-report.md` (`F1`–`F9`: `read_screen` Wayland timeout, `get_clipboard`, `get_weather` read-only access) · **Smart Task-Aware 6-Provider Cloud Router** (`Groq`, `Google AI Studio/Gemini`, `NVIDIA NIM`, `Mistral`, `Cerebras`, `OpenRouter` with `<1s` failover) + **Best Quantized Local Ollama presets** · **Expanded `<50 ms` Gear-0 reflexes** · **NanoClaw Credential-Blind Sandbox** · **`Thuki` `screen:` instant prefix**. |
| **Update 15** | **`nanobot` `MessageBus` + `HEARTBEAT.md` + `CronService` + `Dream` compaction + `/compact`** · **`Hermes` `agentskills.io` `SKILL.md` + 3-level disclosure + reflective skill drafting** · **`Browser-Use` `[0..N]` Indexed Element Grounding + Human Hand-Off** · **`Pipecat`-style sentence-streaming TTS + `Silero VAD` auto-barge-in + `openWakeWord` + `Kokoro-82M ONNX` + `OpenWhispr` cursor dictation/rewrite** · **Optional `<25 MB` `celina-web` phone UI + fail-closed Telegram bridge + Home Assistant local REST preset**. |
| **Update 16** | **Microsoft `MarkItDown` + IBM `Docling`** inside `read_document()` · **`mem0`/`Graphiti`/`sqlite-vec`** bi-temporal KG (`valid_from`/`valid_to`) + fact reconciliation + `MEMORY.md` Git export · **Meta `Prompt Guard 2 (22M)` CPU shield + Rule of Two** · **OWASP MCP SHA-256 schema pinning** · **Hugging Face `smolagents` Code-as-Action** (`5×` faster multi-step tasks) · **Anthropic `srt` OS sandbox** + **Google `A2A` Agent Card** · **`pydantic-ai`-style typed tool-arg self-correction**. |
