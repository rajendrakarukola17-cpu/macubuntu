# macubuntu + Celina AI OS — System Architecture (v4.5, Update 27)

**Project:** macubuntu — Apple Protocol-Free macOS Parity on Ubuntu 26.04 Wayland  
**Assistant:** Celina AI OS — Executive OS Orchestrator (codename "Celly")  
**Document version:** v4.5 (target release)  
**Deployed config version:** 4.4 (single source: `version.toml`; see `celina_version` / `schema_version` / `manifest_version`)  
**Runtime:** Python 3.13.18 + Ollama + llama.cpp + Bubblewrap + Landlock LSM  
**Security status:** P0/P1 controls specified and code-verified; see §Security Status below  
**Known limitations & honest disclosures:** see `LIMITATIONS.md` for threat model gaps, operational boundaries, and P0/P1/P2 control exceptions  
**Version source:** `version.toml` is the single source of truth for all version fields (celina, manifest, schema, doc)  

---

## 1. Executive Summary

`macubuntu` is a complete, Apple-ID-free macOS parity stack for Ubuntu 26.04 Wayland that runs entirely on open-source components. At its core is **Celina**, a local-first AI OS assistant with **106 tools** across 11 domains, a 5-tier cognitive compute split, zero-trust-inspired sandboxing, and a 17-table hybrid memory system.

The architecture is divided into two strictly isolated tiers:

| Tier | Purpose | Invoked by install.sh? | Apple ID Required? |
|------|---------|----------------------|-------------------|
| **Tier A** | Production daily-driver OS + Celina AI assistant | Yes | No |
| **Tier B** | Opt-in experimental Apple protocol research sandbox | **No** (manual only) | No (secondary test ID recommended) |

Tier B is **never** invoked by `install.sh` or any file in `stages/`. It installs strictly into `~/.local/share/celina/apple-research/` (`chmod 700`) with its own Python venv — it never touches Tier A files, system services, or the installation pipeline.

### Tool count reconciliation

Three sources enumerate tools differently:

| Source | Count | Explanation |
|--------|-------|-------------|
| `celina-capabilities.json` manifest | **106** | All declared capability entries (source of truth for enforcement) |
| `neural-schema.json` `tier1_auto_tools` | **64** | Auto-approved tier-1 tools |
| `neural-schema.json` `tier2_approval_tools` | **42** | Approval-gated tier-2 tools |
| 64 + 42 | **106** | Schema tier split matches manifest total |

The legacy "99 tools" figure appears in older documentation and test names; the current authoritative count is **106**.

---

## 2. High-Level System Architecture (End-to-End Flow)

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                    LAYER 1 — USER INTERFACES & SENSORS                              │
│  • GNOME Top-Bar Extension (celina-island@macubuntu.org — ESM JS + D-Bus)          │
│  • Floating Dynamic Island Overlay (island_ui.py — GTK3 + Unix IPC)               │
│  • Terminal Interactive REPL (celina CLI — 27 instant slash commands)             │
│  • Voice Stack (voice.py — Wake Word, Whisper STT, Piper TTS, Esc Barge-In)       │
│  • Desktop Hotkeys (Super+Shift+W Writing Tools · Super+Shift+T Live Text)        │
│  • Event Sensors (os_events.py · ios_ancs.py · biometric_anim.py · ebpf_sensor.py)│
└──────────────────────────────────┬───────────────────────────────────────────────────┘
                                   │ Unix Socket / D-Bus / Direct Python Call
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                    LAYER 2 — COGNITIVE ROUTER (router.py)                           │
│  Step 1: Slash Commands (0 ms) → /help /ram /why /facts /kg /bg /swarm /tier ...   │
│  Step 2: Privacy & DLP Shield  → privacy.py (secret strip / PII redact / consent)  │
│  Step 3: Gear-0 Fast-Path      → everyday_agents.py (6 agents) + gear0_* modules   │
│  Step 4: Smart Cache Check     → cache.py (zero-cost repeated Q&A hits)            │
│  Step 5: Context & Memory Load → MemWeave 3-way recall (memory.py + memory_tiers)  │
│  Step 6: Tiered LLM Execution  → Fast / Tactical / Deep / Local Ollama / Cloud API │
│                     + Reflection + AgentFuse circuit breaker + TurnTrace receipt   │
└──────────────┬──────────────────────────────────────────────────────────┬───────────┘
               │                                                          │
               ▼                                                          ▼
┌──────────────────────────────────────┐  ┌─────────────────────────────────────────┐
│   LAYER 3 — MEMORY & KNOWLEDGE CORE  │  │  LAYER 4 — AUTONOMY & PLANNING           │
│  • memory.py + memory_tiers.py       │  │  • planner/ (TaskGraph, TreePlanner,     │
│  • 17 SQLite Tables (WAL mode)       │  │    ControlFlow, Scaffolding)            │
│  • FTS5 + Dense Vectors (embeddings) │  │  • autonomy/ (Engine, AsyncSupervisor,  │
│  • Knowledge Graph (kg_triples)      │  │    Saga Rollback, SelfHealing, MCP Loader)│
│  • skills.py (Record/Learn/Quarantine│  │  • proactive/daemon.py (idle checks)    │
│  • learning/self_improve.py          │  │  • evidence.py (append-only audit ledger)│
└──────────────┬───────────────────────┘  └──────────────────┬────────────────────────┘
               └────────────────────┬───────────────────────┘
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│            LAYER 5 — ZERO-TRUST GUARDRAILS & EXECUTION GATEWAY                     │
│  • capabilities.py — OS capability manifest enforcement (SHA-256 integrity, fail-closed)│
│  • approval.py — 64 Auto-Run vs 42 Confirm-Gated tools                             │
│  • guardrails.py + governance.py — AgentFuse circuit breaker, risk policy          │
│  • net_policy.py — SSRF blocker (loopback/RFC1918/link-local + scheme guard)       │
│  • landlock_policy.py + os_hardening.py — Bubblewrap + Landlock LSM + rlimits      │
│  • keychain_login.py — OS Keyring / Secret Service vault integration               │
│  • ananicy-cpp — process comm-name QoS (celina-daemon, celina-dbus, celina-router, │
│                    celina-spotlight, celina-ocr, gnome-shell, pipewire, wireplumber)│
└──────────────────────────────────┬───────────────────────────────────────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│           LAYER 6 — 106 OS TOOLS, ACTUATORS & HARDWARE ADAPTERS                    │
│  • tools.py + api_actuators.py + command_runner.py + state_ops.py                  │
│  • GUI & Vision: automation.py, computer_use.py, portal_input.py, writing_tools.py │
│  • Office & Docs: documents.py + libreoffice_uno.py                                │
│  • Search & Apps: spotlight_index.py + app_intents.py + calendar_bridge.py          │
│  • Ecosystem: continuity.py (iOS/iPad/Android/AirPlay) + macos_glue.py             │
│  • MCP Bridge: mcp_client.py + mcp_server.py + memory_mcp_server.py                │
│  • Hardware Layer: celina_hardware/ (GPU/NPU/Vulkan/ROCm/OpenVINO/Biometrics)      │
│  • Performance: turbo_mode.py + inference_adaptive.py (8 GB RAM Guard)             │
│  • Code Sandbox: code_sandbox.py (bwrap under celina-sandbox.slice via systemd-run)│
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Layer-by-Layer Breakdown

### Layer 1 — User Interfaces, Desktop Integration & Event Sensors

| Component | File(s) | Purpose |
|---|---|---|
| **GNOME Top-Bar Extension** | `system/gnome/extensions/celina-island@macubuntu.org/` | Native GNOME Shell top-bar pill (`✦ Celina`). Subscribes to `org.macubuntu.Celina` over Session D-Bus via `CelinaDBusClient` (single consolidated subscription path in `dbus.js`). Files: `extension.js`, `dbus.js`, `prefs.js`, `metadata.json`, `stylesheet.css`, GSettings schema XML. Consumes `StatusChanged` (not `StateChanged`) for Dynamic Island glow/state machine. Handles `MCPActionStarted` via `_applyMCPAction(server, tool)`. |
| **Floating Dynamic Island UI** | `celina_pkg/island_ui.py` | Single-instance GTK3 (`Gtk 3.0` + `Gdk 3.0`) floating top-center pill (`310×42` collapsed → `540×350` expanded). Unix Domain Socket IPC at `/run/user/$UID/celina_island_$UID.sock`. Zero-Tick Deep Sleep — wakes only on D-Bus MPRIS signals, `Gio.FileMonitor` changes, or SQLite reminders. Includes Mode switcher, Language badge, TTS mute, Clipboard Watchdog, Approval & Privacy consent cards, Control Center hardware bar, `ⓘ Why` receipt viewer. **NOT autostarted alongside celina-island@macubuntu.org on GNOME** (guardrail). |
| **Edge Glow Overlay** | `celina_pkg/edge_glow.py` | GTK3/Cairo translucent screen-edge aura showing live status: Voice Listening, Thinking/Automation Active, Skill Recording. |
| **Biometric Animation** | `celina_pkg/biometric_anim.py` | Observes real Linux `fprintd` & GNOME ScreenSaver D-Bus signals (zero access to raw biometric templates) to animate Face ID / Touch ID feedback inside Dynamic Island. |
| **OS Event Daemon** | `celina_pkg/os_events.py` + `celina-eventd.service` | Event-driven daemon monitoring PipeWire mic/camera/screen-share streams, Focus Mode, Secret Vault lock status. Writes `~/.local/share/celina/live_os_events.json` for zero-CPU UI Privacy Dots (`🟠 Mic` · `🟢 Cam` · `🟣 Screen`). |
| **D-Bus IPC Daemon** | `celina_pkg/dbus_service.py` + `system/systemd/celina-daemon.service` | Exposes `org.macubuntu.Celina` on Session D-Bus for native desktop clients, GNOME extension status updates, identity sync. Verifies `GetConnectionUnixUser` peer UID against `os.getuid()`, enforces fail-closed Tier-2 approval on `AskPrompt`. `run_dbus_daemon(role)` accepts `"control"` (celina-dbus) or `"router"` (celina-router) and sets process comm via `set_process_name("celina-dbus"\|"celina-router")` for ananicy matching. Wires `backend._mcp_action_cb` to a D-Bus signal emitter closure so `engine._emit_mcp_action()` → `backend.emit_mcp_action_started()` → `conn.emit_signal(MCPActionStarted)`. |
| **Voice Stack** | `celina_pkg/voice.py` + `celina-voice.service` | Wake-word listening (auto-paused during TTS to eliminate speaker-to-mic loops), VAD speech capture, Whisper STT, Piper/espeak TTS, instant `Esc` **Barge-In** interruption. |
| **iOS BLE ANCS Bridge** | `celina_pkg/ios_ancs.py` | Connects over Linux BlueZ GATT to Apple Notification Center Service (ANCS) on a paired iPhone to mirror notifications into Dynamic Island and send positive/negative actions. |

### D-Bus Signal Consolidation

The extension previously subscribed to both `StatusChanged` and `StateChanged` signals. `dbus_service.py` only emits `StatusChanged` — `StateChanged` was orphaned/duplicated. The fix:

- `extension.js` `_connectDBusAsync()` now passes `onStatus` (handling `StatusChanged`) instead of `onState` (handling `StateChanged`). The status JSON is parsed for `focus_mode` to set the island state.
- `dbus.js` `CelinaDBusClient` consolidates all signal subscriptions: `StatusChanged`, `StateChanged` (retained for backward compatibility), `ActivityChanged`, `PrivacyDotsChanged`, `EdgeGlowChanged`, `LiteModeChanged`, `TaskStateChanged`, `MCPActionStarted`. `extension.js` uses only `CelinaDBusClient` — no raw `Gio.DBusProxy` signal duplication.
- `MCPActionStarted` is emitted over D-Bus by `dbus_service.run_dbus_daemon()` via the `_mcp_action_dbus_emit` closure installed as `backend._mcp_action_cb`.

### Layer 2 — The Cognitive Router (`router.py`, ~2,579 LOC)

Every user message passes through a strict **7-stage decision pipeline**:

| Stage | Component | Behavior |
|---|---|---|
| **1. Slash Commands** | `router.py` `_tier0_dispatch` | 27 commands (`/help`, `/ram`, `/why`, `/facts`, `/kg`, `/bg`, `/swarm`, `/tier`, `/model`, `/turbo`, `/lang`, `/name`, `/skills`, `/train`, `/mcp`, `/cache`, `/clear`, `/struggle`, `/offload`, etc.) execute in **0 ms** with zero LLM tokens. Prompt prefixes: `quick:`, `deep:`, `simulate:`, `dry-run:`, `when I say <trigger>, <action>`. |
| **2. Privacy & DLP Shield** | `celina_pkg/privacy.py` | Hard-strips private keys, API keys (`sk-*`, `ghp_*`, `AKIA*`), `password=...` tokens — **never** sent to cloud. Reversible PII redaction (emails, PAN, Aadhaar, phone, card numbers) before cloud requests; user can force **Local Ollama Only** via consent callback. |
| **3. Gear-0 Deterministic Fast-Path** | `celina_pkg/everyday_agents.py` + `gear0_*` modules | **6 Everyday Agents** execute common OS intents deterministically in **0–170 ms** with **0 LLM tokens**: MediaAgent (YouTube/Spotify), WebAgent (safe URLs), FilesNotesAgent, SystemAgent (theme/DND/volume/brightness), RoutineAgent (timers/reminders), GuiSafetyAgent. Verified by `everyday_trainer.py` (80/80 curriculum). |
| **4. Smart Response Cache** | `celina_pkg/cache.py` | Two-tier RAM + disk cache for static Q&A; bypassed for live OS/time/weather/mutating queries. |
| **5. MemWeave 3-Way Context** | `celina_pkg/memory.py` + `memory_tiers.py` | Classifies queries into **Factual** (FTS5 keyword), **Associative** (vector similarity), or **Relational** (KG traversal). Injects minimal high-signal context. **Language rule:** English prompts receive English answers; Telugu/Hindi prompts receive Telugu/Hindi. The `auto_match_user_language` schema flag enables per-turn language detection — it does not override the rule that the reply language matches the user's input language. |
| **6. Tiered LLM Execution** | `celina_pkg/router.py` + `inference_adaptive.py` | 5-Tier Cognitive Compute Split (see §4). Self-correction (`reflection_policy`) and AgentFuse circuit breaker (`agentfuse_tripped:<rung>`) prevent tool loops. |
| **7. Glass-Box Explainability** | `router.py` `TurnTrace` | Records `gear`, `gear_reason`, `swarm_role`, `detected_lang`, `tool_domains`, `tools_attached/total`, `tools_called`, `recalled_facts`, `privacy_summary`, `cache_status`, `self_corrections`, `compute_backend`, latency. Accessible via `/why` command. |

### Layer 3 — Memory & Knowledge Core

Single local-first **SQLite WAL database** (`~/.local/share/celina/memory.db`) with **17 tables**:

| Table Group | Tables | Purpose |
|---|---|---|
| **Short-Term & Episodic** | `conversations`, `episodes` | Sliding chat history (`/clear` clears `conversations` only) + summarized multi-turn task episodes. |
| **Semantic & Hybrid Vector/FTS** | `facts`, `memory_fts`, `memory_vectors`, `hybrid_memories`, `hybrid_memories_fts` | Persistent user facts (`/facts`), FTS5 BM25 indexes, local dense embeddings (`embeddings.py`), unified hybrid retrieval. |
| **Knowledge Graph** | `kg_triples`, `hybrid_graph_edges`, `compiled_knowledge`, `compiled_knowledge_fts` | Structured `(subject, predicate, object)` graph (`/kg`, `/graph`), promoted automatically by `kg_promoter.py`. |
| **Procedural & Behavioral** | `skills`, `skill_runs`, `habit_patterns`, `sensory_schemas` | Learned & recorded skills (`skills.py`), execution counters, learned habits, UI layout blueprints (`semantic_blueprint.py`). |
| **Goals & Proactive Timers** | `goals`, `reminders` | Long-running tracked goals (`/goals`) and persistent SQLite background reminders. |

**Skills & Quarantine Lifecycle** (`skills.py`, `874 LOC`):
- Create skills via natural `\"when I say <X>, do <Y>\"`, live `start_recording`/`stop_recording` (red `🔴 REC` in Dynamic Island), or `save_last_actions_as_skill`.
- Third-party community skills (`install_community_skill`) **always land in Quarantine first** — require explicit `approve_quarantined_skill` / `reject_quarantined_skill` before execution.

**⚠️ memory.db single point of compromise:** The 17-table SQLite file holds executable skills, facts, goals, and habits. It is read by the sqlite MCP server, written by the memory MCP server, and borg-backed up (backup location/encryption not yet specified in this document). A compromise of this file is a whole-agent takeover. Protection of `memory.db` (WAL integrity, access mediation, skill-row quarantine) is a tracked gap — see §Threat Model, item 5.

### Layer 4 — Autonomy, Planning & Proactive Subsystems

| Module | LOC | Responsibility |
|---|---:|---|
| `planner/task_graph.py` | — | Decomposes multi-step goals into dependency DAGs |
| `planner/tree_planner.py` | — | Tree-of-Thought planning for deep reasoning |
| `planner/control_flow.py` | — | Conditional branches and structured sub-tasks |
| `planner/scaffolding.py` | — | Plan scaffolding and validation |
| `autonomy/engine.py` | — | Durable multi-step goal runner with JSON checkpoints; `mcp_action_cb` wired to `dbus_service.CelinaDBusBackend.emit_mcp_action_started()` for MCPActionStarted D-Bus emission |
| `autonomy/saga.py` | — | Compensating **Saga rollback** if a step fails mid-workflow |
| `autonomy/checkpoint.py` | — | Checkpoint persistence and recovery |
| `autonomy/supervisor.py` | 360 | Lazy-loaded **AsyncSupervisor** (0 MB RAM until invoked via `/bg <goal>` or `/jobs`) |
| `autonomy/reliability.py` | — | Retry budgets, degraded-mode fallbacks |
| `autonomy/self_healing.py` | — | Automatic recovery probes |
| `autonomy/mcp_loader.py` | 1,004 | Discovers, validates, lazily connects external MCP servers |
| `proactive/daemon.py` | — | Background idle checks (`celina-proactive.service`) |
| `learning/self_improve.py` | — | Scheduled self-evaluation (`celina-self-improve.timer`) |
| `evidence.py` | — | Append-only action evidence ledger |
| `telemetry.py` | — | Performance/struggle diagnostics (`/struggle`) |

**Process naming for ananicy matching:** All Celina entry points call `config.set_process_name()` via `prctl(PR_SET_NAME)` (15-byte kernel `comm` limit, silent no-op on non-Linux):
- `celina-daemon` — `router.main()` and `dbus_service.run_dbus_daemon()`
- `celina-autonomy` — `engine.main()`
- `celina-dbus` — `dbus_service.run_dbus_daemon(role="control")`
- `celina-router` — `dbus_service.run_dbus_daemon(role="router")`
- `celina-spotlight` — `spotlight_index.__main__`

### Layer 5 — Zero-Trust-Inspired Security, Sandboxing & Guardrails

This layer implements the **P0/P1 Security Audit (Update 26)** controls. All controls are specified in code and verified against the deployed artifacts.

| Defense Layer | Module(s) | Protection Guarantee |
|---|---|---|
| **Capability Manifest Enforcement** | `celina_pkg/capabilities.py` | OS capability manifest (`celina-capabilities.json`) is the security policy oracle. All 106 tools have `auth_operation` bound to AuthPolicyEngine tiers. Manifest integrity verified via SHA-256 with **fail-closed** on violation. |
| **Tool Risk Classification** | `celina_pkg/approval.py` | **64 Auto-Approved** read-only/low-risk tools run immediately; **42 Mutating/System** tools enforce explicit user approval (CLI prompt or Dynamic Island Approve/Deny card). |
| **Intent Validation** | `celina_pkg/gear0_5_micro.py` | `INTENT_SCHEMA` with `additionalProperties: false`, per-intent slot sub-schemas, confidence range [0,1], jsonschema validation at import time. 19 intents mapped to declared capabilities with tier assignment. `save kind=command` rejected. |
| **Network & SSRF Shield** | `celina_pkg/net_policy.py` | Blocks loopback (`127.0.0.0/8`, `::1`), RFC1918 private LAN, cloud metadata (`169.254.169.254`). `open_url_safe()` rejects non-`http(s)` schemes before URL normalization. Outbound webhooks require opt-in `web_comms` allowlist. |
| **Code Sandbox Isolation** | `celina_pkg/code_sandbox.py` + `landlock_policy.py` + `os_hardening.py` | `run_python_sandbox` isolates untrusted Python with **Bubblewrap** (RO root, isolated `/work` tmpdir, disabled network) wrapped under `systemd-run --user --quiet --scope --slice=celina-sandbox.slice` when active user systemd bus present; falls back to bare `bwrap` in headless/CI. Catastrophic denylist checked **before** bwrap availability. Linux Landlock LSM + rlimits fallback for Ubuntu 26.04 AppArmor userns restrictions. |
| **Desktop & Password Safety** | `celina_pkg/automation.py` + `guardrails.py` | `is_focused_password_field()` refuses reading/typing inside password boxes. Destructive file ops use recoverable freedesktop **Trash**. `NoNewPrivileges=true` enforced in systemd units. |
| **AT-SPI2 Crash Isolation** | `celina_pkg/automation.py` | `_safe_get_atspi_desktop()` probes AT-SPI2 in throwaway subprocess with `RLIMIT_CORE=0` so C-level accessibility `SIGABRT` bugs cannot crash Celina. |
| **Secret Storage** | `celina_pkg/keychain_login.py` | Integrates with OS Keyring (`org.freedesktop.secrets`) & locked vault monitoring (`get_secret_vault_status`). |
| **Model Integrity** | `gear0_5_smolLM.py` + `gear0_5_micro.py` | SHA-256 hash verification for GGUF model files and FastText models via env-configured `CELINA_SMOLLM_SHA256_*` / `CELINA_FASTTEXT_SHA256` / `CELINA_SMOLLM_135M_SHA256`. |
| **Process QoS (ananicy-cpp)** | `system/ananicy.d/99-celina-macubuntu.rules` | `celina-daemon`, `celina-dbus`, `celina-router`, `celina-spotlight`, `celina-ocr` matched by exact `comm` name (set via `prctl(PR_SET_NAME)`). `gnome-shell`, `pipewire`, `wireplumber`, `keyd` also tuned. |

### Layer 6 — The 106 OS Tools, Actuators & Hardware Stack

Every tool maintains **exact parity** across schemas, dispatch tables, risk policies, auto-maps, domains, docs, and tests (`test_audit_fixes_and_parity.py`).

| Domain | Key Modules | Representative Tools |
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

**Hardware Abstraction** (`celina_hardware/`, `1,943 LOC`):
- `detect_gpu.py` + `compute.py` + `adapters.py`: Auto-detects Intel/AMD/NVIDIA GPU, NPU, RAM ceiling, selects inference backend (`backends/vulkan.py`, `rocm.py`, `openvino_npu.py`, or CPU-optimized Ollama)
- `biometrics.py`: Hardware-backed `fprintd` / PAM sensor capability detection
- `backends/openvino_npu.py`, `rocm.py`, `vulkan.py`: Compute backend implementations

---

## 4. 5-Tier Cognitive Compute Split

| Tier | Name | Model | RAM | Offline? | Use Case |
|---|---|---|---|---|---|
| **0** | Reflex Only | Gear-0 deterministic (no model) | 0 MB | Yes | Slash commands, 6 everyday agents, instant intents |
| **0.5** | smolLM Quantized | SmolLM2-135M-Instruct / smolLM-360M / smolLM-1.7B GGUF via llama.cpp (artifacts UNVERIFIED) | ~100–200 MB | Yes | Sub-100ms local intent classification (Gear 0.5 micro-router) |
| **1** | Local Fast | Ollama `qwen3:1.7b` (think=False) | ~1–2 GB | Yes | Fast tactical responses, simple tool calls |
| **2** | Local Heavy | Ollama reasoning model (`qwen2.5:3b` / `qwen3:4b` on 8 GB) | ~2.5–4 GB | Yes | Deep reasoning, multi-step planning |
| **3** | Opt-In Cloud | OpenRouter / 6-Provider Smart Router | 0 MB local | No | Complex tasks requiring cloud models (opt-in, privacy-gated) |

**RAM Guard** (`inference_adaptive.py` + `turbo_mode.py`):
- On ≤10 GB RAM: context cap 1536 (fast) / 2048 (tactical/deep)
- `Ollama keep_alive` dynamically tightened under RAM pressure (0s / 15s / 45s)
- `celina-eventd.service` runs with `OOMScoreAdjust=-300` (`MemoryMax=384M`)
- `ollama.service` runs with `OOMScoreAdjust=300` + `ManagedOOMMemoryPressure=kill`

**⚠️ Sub-100ms latency claim:** Gear 0.5 spawns `llama-cli` and loads a GGUF per call. The sub-100ms figure is a design target, not a measured production number. Concurrent model loads (ollama + whisper-base + embeddinggemma + piper + GNOME) on 8 GB RAM are not yet bounded by a concurrency test. See §Threat Model, item 8.

---

## 5. Performance Fusion — Drop-Ins & Stage Ownership

### Stage 35 — Memory, ZRAM & Performance Fusion (`stages/35-memory-zram.sh`)

Stage 35 is the **single authoritative owner** for ZRAM, VM sysctl, Celina QoS, DNS cache, journald rate-limit, ananicy-cpp, vmtouch, EPP, PSD, fstrim, and shader cache. All file writes use P5 atomic DRY_RUN-safe wrappers (`write_root_file`, `copy_root_file`, `write_user_file`, `run_as_user install -Dm644`).

**Wave A — Perf Fusion tuning:**

| Drop-in | Gate | Destination | Purpose |
|---|---|---|---|
| `90-macubuntu-qos.conf` (ollama) | `ENABLE_CELINA_QOS` | `/etc/systemd/system/ollama.service.d/` | Ollama CPU/IO/Memory QoS cap |
| `90-macubuntu-qos.conf` (celina-daemon) | `ENABLE_CELINA_QOS` | `$HOME/.config/systemd/user/celina-daemon.service.d/` | Celina daemon QoS slices + Ollama QoS cap |
| `celina-sandbox.slice` | `ENABLE_CELINA_QOS` | `$HOME/.config/systemd/user/` | User slice for code_sandbox bwrap via systemd-run |
| `90-macubuntu-dnscache.conf` | `ENABLE_DNS_CACHE` | `/etc/systemd/resolved.conf.d/` | systemd-resolved DNS cache drop-in |
| `90-macubuntu.conf` (journald) | `ENABLE_JOURNALD_LITE` | `/etc/systemd/journald.conf.d/` | systemd-journald rate-limit cap |
| `99-celina-macubuntu.rules` | `ENABLE_ANANICY` | `/etc/ananicy.d/` | Celina process comm-name QoS rules |
| `celina-vmtouch-ctl` + units | `ENABLE_VMTOUCH` | `$HOME/.local/bin/` + `$HOME/.config/systemd/user/` | PSI-aware RAM warmer (opt-in) |
| `91-macubuntu-epp.rules` | `ENABLE_EPP_DYNAMIC` | `/etc/udev/rules.d/` | Dynamic AC/Battery EPP udev rule |

**`celina-daemon.service`** (`system/systemd/celina-daemon.service`): User systemd unit so the `celina-daemon.service.d/90-macubuntu-qos.conf` `.service.d` drop-in applies to a real unit. Runs `python3 -m celina_pkg.dbus_service --daemon` with `NoNewPrivileges=true`, seccomp filter, `MemoryHigh=128M`, `MemoryMax=512M`, `CPUQuota=25%`.

### Stage 25 — Desktop Dock & Tiling Assistant (`stages/25-desktop-dock.sh`)

- **Explicitly disables** stock Ubuntu's `tiling-assistant@ubuntu.com` **before** enabling `tiling-assistant@leleat-on-github` to avoid duplicate tiling daemons fighting for window management.
- Registers GNOME shortcuts for `celina-autonomy` and `celina-spotlight`.

---

## 6. De-Confliction Summary (Part 2)

Four active overlaps were eliminated:

### 6.1 Sysctl Deduplication

Removed 4 duplicate `net.*` keys from `system/sysctl/99-macubuntu-vm.conf`:
- `net.core.default_qdisc`
- `net.ipv4.tcp_congestion_control`
- `net.ipv4.tcp_fastopen`
- `net.ipv4.tcp_slow_start_after_idle`

These are now owned **solely** by `system/sysctl/99-macubuntu-net.conf`. `99-macubuntu-vm.conf` retains only VM-specific keys (`vm.*`, `kernel.*`, `fs.*`, `net.ipv4.tcp_mtu_probing`).

### 6.2 Tiling Assistant De-confliction

`stages/25-desktop-dock.sh` runs `gnome-extensions disable tiling-assistant@ubuntu.com` **before** `install_ext "tiling-assistant@leleat-on-github"` to prevent duplicate tiling daemons.

### 6.3 Single-Owner Package Stages

| Package | Removed From | Kept In |
|---|---|---|
| `vmtouch` | `stages/10-base-deps.sh` | `stages/35-memory-zram.sh` (as `celina-vmtouch-ctl`) |
| `ananicy-cpp` | `stages/10-base-deps.sh` | `stages/35-memory-zram.sh` |
| `timeshift` | `stages/45-apps-backups-search.sh` | `stages/61-recovery-polish.sh` |
| `gnome-sushi` | `stages/10-base-deps.sh` + `stages/60-extras.sh` | `stages/45-apps-backups-search.sh` (sole owner) |

### 6.4 EPP Race Guard (`system/udev/91-macubuntu-epp.rules`)

Raw sysfs EPP writes are guarded so they run **only** when `power-profiles-daemon` is not active:

```
SUBSYSTEM=="power_supply", ATTR{type}=="Mains", ATTR{online}=="1",
  RUN+="/bin/sh -c 'systemctl is-active --quiet power-profiles-daemon || for p in ...; do echo balance_performance > "$p"; done'"
```

The `systemctl is-active --quiet power-profiles-daemon ||` prefix ensures EPP writes are skipped when ppd manages CPU frequency scaling, avoiding AC/battery race conditions.

---

## 7. Security Architecture — P0/P1 Audit Status

### Capability Manifest (`celina-capabilities.json`)

Two identical copies maintained in sync:
- `system/capabilities/celina-capabilities.json`
- `features/celina/config/celina-capabilities.json`

**Verified properties (code-checked):**

| Property | Value | Verified via |
|---|---|---|
| Total tools | 106 | `len(manifest["tools"])` |
| Tools with `auth_operation` | 106/106 | grep count |
| Tools with `fs_scope: proc_sys_ro` | 0 | grep `"fs_scope": "proc_sys_ro"` |
| Tools with structured PipeWire grants | 6 | dict-type `pipewire` entries |
| Tools with `pipewire: true/false` boolean | 96 | boolean-type `pipewire` entries |
| Tools with `credential_access: secret_material` | 9 | clipboard/screen/secrets tools |
| Tools with `credential_access: none` | 93 | remaining tools |
| `never_exposed_paths` entries | 18 | includes `~/.celina/models/` |
| Integrity block present | Yes | `integrity` key in manifest |
| `default_policy` | `"deny"` | fail-closed policy |
| Manifest copies identical | Yes | `diff` clean |

### Manifest Integrity Hash

The manifest carries a SHA-256 integrity block computed over the **canonical JSON** (manifest minus its own `integrity` block, sorted keys, compact separators). This is the hash the enforcement code recomputes and compares at load time — any mismatch causes fail-closed rejection.

**Raw file hash** (`sha256sum`):
```
906bffe61a1c88e58e5641963ad459a737ecf674d2d340b463801022aa6d6811  system/capabilities/celina-capabilities.json
906bffe61a1c88e58e5641963ad459a737ecf674d2d340b463801022aa6d6811  features/celina/config/celina-capabilities.json
```

**Canonical hash** (computed by `capabilities.py` `verify_manifest_integrity()`): differs from raw `sha256sum` because it hashes canonical JSON, not raw file bytes. Both copies produce the same canonical hash.

**Important:** The canonical hash value is not hardcoded in this document because it changes whenever the manifest is edited. It is computed at runtime by the enforcement code. Documenting a static hash in markdown creates a drift hazard — the hash in §5 would need to be updated on every manifest change.

### Enforcement Code (`capabilities.py`)

| Check | Implementation |
|---|---|
| `VALID_FS_SCOPES` | `none`, `celina_state_only`, `home_ro`, `home_rw_approval_undo`, `bwrap_workdir` — **`proc_sys_ro` removed** |
| `VALID_NET_SCOPES` | `none`, `loopback_ollama`, `https_public_only`, `lan_mdns`, `iot_allowlist`, `mcp_declared` |
| `VALID_AUTH_OPERATIONS` | 16 operations mapped to AuthPolicyEngine tiers (FIDO2_HIGH_ASSURANCE, PASSWORD_RECOVERY, LOCAL_CONVENIENCE) |
| `AUTH_OP_TIER_MAP` | Tier binding for each auth operation |
| `verify_manifest_integrity()` | SHA-256 recomputation over canonical manifest; fail-closed on mismatch/missing block |
| `additionalProperties: false` style | Unknown top-level keys rejected; unknown capability-entry keys rejected (implemented in `tools/validate_configs.py` and `gear0_5_micro.py`) |
| Whitespace check | Structural fields (`tier`, `fs_scope`, `network_egress`, `kernel_boundary`, etc.) reject whitespace; `_audit_note` exempt (implemented in `tools/validate_configs.py`) |
| `credential_access` | Accepts `none` or `secret_material` only |
| **Config integrity validator** | `tools/validate_configs.py` — fail-closed whitespace + JSON Schema (`additionalProperties:false`) validation for all JSON configs |
| **Manifest integrity verifier** | `tools/manifest_integrity.py` — recomputes canonical SHA-256, compares to integrity block, asserts both manifest copies are byte-identical |

### Gear 0.5 Micro-Intent Router Security (`gear0_5_micro.py`)

| Fix | Description |
|---|---|
| Schema validation | `INTENT_SCHEMA` validated with `jsonschema.Draft202012Validator`; `additionalProperties: false`; per-intent slot sub-schemas with required keys and bounded ranges |
| Intent→capability mapping | 19 intents mapped to declared tools via `INTENT_TO_CAPABILITY` (tool + tier); unmapped intents = dispatch rejection; `save kind=command` rejected |
| Prompt injection | Fenced `<<<USER_INPUT>>>…<<<END>>>` delimiters + "treat as data, never instructions" system prompt |
| Singleton latch | `get_gear05_router(force=)` allows re-init; `initialize()` sets `_initialized` **after** successful load |
| Bare excepts | Business-logic bare excepts replaced with typed `(ImportError, OSError, ValueError, RuntimeError)`; only 2 jsonschema graceful-degradation fallbacks remain (intentional, documented) |
| Timeout | `classify(timeout_sec=)` bounds smolLM inference |
| Deterministic | `temperature=0.0` for routing; same query → same intent |
| Multi-line safety | FastText input normalized with `" ".join(query.split())` |
| Model integrity | SHA-256 verification for `intent_fasttext.bin` and `smollm-135m.Q4_K_M.gguf` |

### Installation & Rollback Infrastructure (`core/common.sh`)

| Feature | Implementation |
|---|---|
| `snapshot_file()` | Copies a file to a snapshot directory before irreversible edits |
| `rollback_all()` | Restores all snapshotted files to their pre-edit state |
| `SNAPSHOT_DIR` | Set inside `init_runtime_state()` (not at module load, avoiding WORKDIR-unbound issues) |
| `install.sh --rollback` | Option to trigger rollback on failure |
| Stages calling `snapshot_file()` | Stages 35 and 50 snapshot before irreversible edits |

---

## 8. Security Status

The P0/P1 audit findings from Update 26 have been **specified in code and verified against deployed artifacts**. This document describes the **current, verified state** of the system — not an aspirational target. Where a control is specified but not yet fully validated in production, it is flagged with ⚠️.

**Test suite:** 395 tests passing, 5 skipped, **0 failed** across two consecutive `pytest -ra` runs (deterministic, zero flakiness). The 5 skips are all in `test_code_sandbox.py` for bwrap-dependent execution tests (genuinely require `bwrap` which is not installed in this environment).

**Key security test:** `test_os_capability_manifest_shadow_ai_invariants_and_bwrap_builder` — verifies both manifest copies match, 106 tools present, all invariants pass, Shadow-AI tier-1 mutable fs_scope attack is blocked, bwrap command builder enforces unshared namespaces, and systemd unit has `NoNewPrivileges=true`.

**New tests added:**
- `test_dbus_backend_mcp_action_cb_wiring` — verifies `CelinaDBusBackend` accepts/invokes `mcp_action_cb` and fails open on callback errors
- `test_run_dbus_daemon_wires_mcp_action_cb` — verifies `run_dbus_daemon` wires `_mcp_action_cb` to a D-Bus signal emitter for `MCPActionStarted`
- `test_build_systemd_run_bwrap_prefix_uses_celina_sandbox_slice` — verifies systemd-run prefix uses `celina-sandbox.slice`
- `test_build_systemd_run_bwrap_prefix_custom_slice` — verifies custom slice name honored
- `test_bwrap_python_cmd_prepends_systemd_prefix` — verifies systemd prefix prepended to bwrap command
- `test_bwrap_bash_cmd_prepends_systemd_prefix` — verifies systemd prefix prepended to bash bwrap command
- `test_bwrap_cmd_without_systemd_prefix` — verifies no-prefix mode starts directly with bwrap
- `test_systemd_run_available_fallback_headless` — verifies `_systemd_run_available()` returns False without `DBUS_SESSION_BUS_ADDRESS`

---

## 9. Threat Model

This section names adversaries and traces attack paths. Each item lists the relevant controls and any residual gaps.

### T1 — Indirect Prompt Injection → Persistent Memory → Tier-2 Execution

**Path:** `fetch_webpage`/`read_document`/`get_clipboard`/`live_text_ocr` (tier-1, attacker-controlled input) → `remember_fact`/`learn_skill`/`update_skill` (tier-1, persists it) → later turn executes persisted skill/fact.

**Controls:** Intent schema validation (Gear 0.5), per-intent slot sub-schemas, `save kind=command` rejection, Critic/verifier LLM (advisory only).

**Residual gap:** No taint model tracks attacker-controlled text through memory persistence into execution. The Critic is advisory, not enforceable.

### T2 — AT-SPI Self-Approval

**Path:** `automation.py` holds AT-SPI access; portal consent dialogs are ordinary windows that AT-SPI can click. An agent could approve its own escalation.

**Controls:** `is_focused_password_field()` password refusal, Approval/Deny card in Dynamic Island.

**Residual gap:** No `forbidden_ui_targets` list. No binding between the approval card the user sees and the actual tool call arguments. Approval is not per-call, argument-bound, single-use, or TTL-limited.

### T3 — Continuity Camera IR Spoofing (Self-Installed Virtual Camera)

**Path:** Stage files load `v4l2loopback`; `biometrics.py` decides `ir_camera_verified` by device-name regex. A virtual camera named "IR Camera" (installed by the same system) satisfies the face-unlock capability gate.

**Controls:** None cross-referencing these two subsystems.

**Residual gap:** The v4l2loopback module and biometric IR check were audited separately; together they form a spoofing primitive. No allowlist of physical camera device paths.

### T4 — Same-User Isolation Absent

**Path:** Router, memory, skills, and 60 tier-1 tools run in-process with full user rights. `in_process_ro` is a code-discipline label, not a kernel boundary. Any same-user process can `ptrace` Celina and read `/proc/<pid>/mem|environ` — API keys, memory contents, credentials.

**Controls:** Bubblewrap sandboxing for ~5 code-execution tools only.

**Residual gap:** No Yama `ptrace_scope` restriction. No separate UID or container for the router/memory processes. The honest claim is "capability-gated, kernel-sandboxed for code execution," not "zero-trust" across all components.

### T5 — memory.db Single Point of Compromise

**Path:** One SQLite WAL file holds executable skills, facts, goals, habits. Read by sqlite MCP server, written by memory MCP server, borg-backed up.

**Controls:** SQLite WAL mode, skill quarantine before approval.

**Residual gap:** No WAL integrity verification, no separate access mediation for `memory.db`, no encryption-at-rest specification for borg backups, no backup location documented.

### T6 — Root-Level Input Daemons

**Path:** `ydotoold.service` and `keyd` run as root-level synthetic-input services. ydotool's socket ACLs are historically world-writable → any local user injects keystrokes as root.

**Controls:** None documented.

**Residual gap:** Neither daemon appears in the security control inventory. scx_loader polkit rules also unaddressed.

### T7 — Default-On LAN Listeners vs. "Zero-Trust"

**Path:** `ENABLE_AIRPLAY_VIDEO`, `ENABLE_ANDROID_MIRROR`, `ENABLE_SYNCTHING`, `ENABLE_UNIVERSAL_CONTROL` all default **true**. shairport-sync/uxplay have CVE history.

**Controls:** Manifest has no inbound/listening dimension at all.

**Residual gap:** No manifest entry for inbound services/listeners. No network exposure audit for default-on services.

### T8 — Approval-Card Integrity

**Path:** The approval gate is a UI card rendered by Celina's own Dynamic Island. A crafted tool description can phrase a destructive action benignly; the user approves what they see, not what executes.

**Controls:** Approval/Deny card UI, `approval.py` classification.

**Residual gap:** No per-call, argument-bound, single-use, TTL-limited approval binding. No verification that the displayed card matches the executed tool call.

### T9 — DLP Is a Denylist

**Path:** `privacy.py` strips `sk-*`/`ghp_*`/`AKIA*` regexes. Misses every other secret format and everything inside file contents read by `read_file`. "Reversible PII redaction" means originals are stored somewhere.

**Controls:** Secret stripping before cloud, reversible PII redaction, Local Ollama Only consent option.

**Residual gap:** Denylist approach is inherently incomplete. Storage location and protection for unredacted originals unspecified.

### T10 — Ollama No-Auth Problem

**Path:** `ollama-hardening.conf` is listed in system config but its contents (auth binding, no-auth exposure) are not specified in this document.

**Controls:** None documented.

**Residual gap:** Ollama API bind address, auth requirement, and network exposure are unspecified.

---

## 10. Operational Gaps

| Gap | Description | Status |
|---|---|---|
| **No Tier A uninstall story** | Tier B has `uninstall.sh`; Tier A touches PAM, GRUB, keyd, ydotool, udev, ~15 systemd units — no clean removal path | NOT-IMPLEMENTED |
| **No incident behavior** | Fail-closed on manifest violation → Celina refuses to run. No user-facing explanation or recovery path documented | NOT-IMPLEMENTED |
| **No log-privacy policy** | `install-events.jsonl`, `telemetry.py` (`/struggle`), `evidence.py` record tool arguments (paths, queries, recipients). Content classes and retention unspecified | NOT-IMPLEMENTED |
| **`celina-login-save`** | Login/session helper described in one line; auth-adjacent binary needs a dedicated section | NOT-IMPLEMENTED |
| **Rollback not wired to Stage 00** | `snapshot_file()`/`rollback_all()` exist in `common.sh` but Stage 00 pre-flight does not call them before hard-abort | NOT-IMPLEMENTED |
| **Evidence ledger determinism** | Stale evidence ledger anchor/state could cause test order pollution | FIXED — conftest.py `_isolate_evidence_ledger_state` autouse fixture cleans stale anchor/ledger state outside workspace at session start/teardown |

---

## 11. Controls That Are Well-Designed (Negative Controls)

The following designs are correct and defensible:

* **Tier A/B firewall** — manual-only launch, `chmod 700`, separate venv, session-detection abort, dry-run. Correct isolation shape.
* **Known Ceilings honesty** — ECID/SEP impossibility, BlueBubbles iMessage recommendation, 8 GB OOM policy. Rare transparency in architecture docs.
* **RAM/OOM governance** — `celina-eventd.service` protected (`OOMScoreAdjust=-300`), `ollama.service` reapable (`OOMScoreAdjust=300`). Correct priority inversion.
* **Saga rollback + JSON checkpoints + retry budgets** — correct reliability pattern for autonomy.
* **TurnTrace glass-box receipts** (`/why`) — real accountability mechanism; should be extended to routing decisions and approval grants.
* **AT-SPI crash isolation** (`RLIMIT_CORE=0` probe subprocess) — right problem, right fix.
* **Password-field refusal, trash-not-delete, `NoNewPrivileges=true`, `UMask=0077`, seccomp** on `celina.service`.
* **zram/hibernate invariant** in `detect.sh` (real swap ≥ RAM + zram) — correct and unusual rigor.
* **`open_url_safe()` checks scheme before normalization** — correct ordering prevents SSRF bypass.
* **`resolved-mdns.conf` killing UDP 5353 races** — LAN surface consideration.
* **D-Bus `StatusChanged` single subscription path** via `CelinaDBusClient` — eliminates orphaned `StateChanged` and duplicated signal handlers.
* **`mcp_action_cb` wired from `AutonomyEngine` → `CelinaDBusBackend.emit_mcp_action_started()` → D-Bus `MCPActionStarted` signal** — MCP actions are observable over D-Bus.
* **`prctl(PR_SET_NAME)` process naming** for ananicy matching — `celina-daemon`, `celina-dbus`, `celina-router`, `celina-autonomy`, `celina-spotlight` all set exact `comm` names.

---

## 12. On-Disk Runtime Layout (Installed Ubuntu 26.04 PC)

```
~/.local/
├── bin/
│   ├── celina                  # Main interactive CLI REPL launcher
│   ├── celina-island           # Floating GTK3 Dynamic Island launcher (IPC client/server)
│   ├── celina-dbus             # D-Bus control plane daemon (python -m celina_pkg.dbus_service)
│   ├── celina-train            # 6-Agent 80-case Everyday Trainer CLI
│   ├── celina-turbo            # Zero-Lag 8 GB Turbo Mode toggle
│   └── celina-login-save       # Secure session/login helper
├── share/
│   ├── celina/
│   │   ├── app/                # Installed celina_pkg/ + celina_hardware/ + config/
│   │   ├── venv/               # Isolated Python 3.13 venv (--system-site-packages for PyGObject)
│   │   ├── memory.db           # Unified 17-table SQLite WAL Quiet Brain + KG + Skills + Reminders
│   │   └── live_os_events.json # Event-driven Privacy Dots + Focus Mode + Vault state
│   └── gnome-shell/extensions/
│       └── celina-island@macubuntu.org/   # Native GNOME top-bar extension
└── config/
    ├── celina/                 # User overrides, mcp-servers.json, iot-allowlist.json
    └── systemd/user/           # celina.service, celina-eventd.service, celina-daemon.service.d/, timers, slices
~/.ai-env                       # Mode 0600 API keys & provider env settings
```

### System Configuration (read-only, installed by `install.sh`)

Key security-relevant files: `celina.service` (`NoNewPrivileges=true`, `UMask=0077`, seccomp), `celina-daemon.service` (user unit with QoS drop-in support), `celina-eventd.service` (read-only D-Bus subscriber), `celina-strict-fs.conf` (opt-in strict FS), `celina-capabilities.json` (security policy oracle), `download-locks.sha256` (supply-chain pins), `ebpf/` (CORE eBPF sensor, CAP_BPF/CAP_PERFMON isolated), `pipewire/99-macubuntu-audio.conf`, `zram/`, `systemd/ollama-hardening.conf` (Ollama auth binding — contents TBD), `system/ananicy.d/99-celina-macubuntu.rules` (process comm-name QoS), `system/sysctl/99-macubuntu-vm.conf` (VM/sysctl, no `net.*` keys), `system/sysctl/99-macubuntu-net.conf` (sole owner of `net.*` keys), `system/udev/91-macubuntu-epp.rules` (ppd-guarded EPP writes).

---

## 13. Stages / Installation Pipeline

`install.sh` runs up to 19 modular stage scripts under `stages/` in numerical order. Stage 00 is a hard pre-flight gate.

| Stage | File | Purpose |
|---|---|---|
| **00** | `stages/00-preflight.sh` | Hard-abort if manifest/parity checks or Tier-2 mutation lock fails |
| **10** | `stages/10-base-deps.sh` | Base system dependencies (`vmtouch`, `ananicy-cpp`, `gnome-sushi` REMOVED — owned by stages 35 and 45) |
| **20** | `stages/20-appearance.sh` | Appearance theming |
| **25** | `stages/25-desktop-dock.sh` | Desktop dock configuration (disables `tiling-assistant@ubuntu.com` before enabling leleat fork) |
| **30** | `stages/30-input-typography.sh` | Input & typography |
| **32** | `stages/32-low-latency-kernel.sh` | Optional low-latency kernel setup |
| **33** | `stages/33-preload.sh` | Preload / warm-cache tuning |
| **34** | `stages/34-snap-removal.sh` | Optional snap removal / deb transition |
| **35** | `stages/35-memory-zram.sh` | zram compressed memory + fstab validation + Perf Fusion drop-ins (QoS, DNS cache, journald, ananicy, vmtouch, EPP) |
| **40** | `stages/40-continuity-audio.sh` | Continuity audio (AirPlay, PipeWire) |
| **45** | `stages/45-apps-backups-search.sh` | Apps, backups, search indexing (sole owner of `gnome-sushi`, `timeshift` moved to stage 61) |
| **46** | `stages/46-browser-tuning.sh` | Browser tuning & Firefox Safari chrome setup |
| **49** | `stages/49-cpu-mitigations-off.sh` | Opt-in CPU mitigation tuning |
| **50** | `stages/50-power.sh` | Power management, hibernate, LUKS |
| **55** | `stages/55-security-biometrics.sh` | Security, biometrics, `fprintd` PAM |
| **60** | `stages/60-extras.sh` | Extra utilities (`gnome-sushi` removed — owned by stage 45) |
| **61** | `stages/61-recovery-polish.sh` | Recovery & polish (sole owner of `timeshift`) |
| **70** | `stages/70-feature-celina.sh` | Celina AI OS feature installation (`PYTHONPATH` set to `features/celina` parent dir) |
| **99** | `stages/99-verify.sh` | Post-install verification (`ollama`, `bwrap`, `celina --help`, PAM state, swap presence, macubuntu Center smoke & bundle checks) |

**Core & Hardware Helpers:** `core/common.sh` (`snapshot_file()`, `rollback_all()`, `init_runtime_state()`), `core/detect.sh` (hardware detection), `lib/hw_detect.sh`, `lib/npu_detect.sh`.

**Installer features:** `--rollback` option, `--dry-run` zero-side-effect verification, `--system-only` / `--user-only` modes.

**Guardrail:** `DRY_RUN=1 ./install.sh` produces zero side effects — no files or directories (`.cache`, `.config`, `.local`) created in `$HOME`. Verified by `test_install_dry_run_produces_zero_side_effects_in_clean_home`.

---

## 14. Tier A vs Tier B — Architectural Firewall

### Tier A (Production Daily-Driver)

- Installed and verified by `./install.sh` and `./install.sh --verify`
- Zero proprietary Apple credentials, zero binary blobs, 100% open-source and supply-chain pinned
- Celina AI assistant with 106 tools, 5-tier cognitive split, zero-trust-inspired sandboxing

### Tier B (Opt-In Experimental Apple Protocol Research)

- **Never invoked by `install.sh` or any file in `stages/`**
- Must be launched manually via `tools/experimental-deep-stack.sh`
- Installs strictly into `~/.local/share/celina/apple-research/` (`chmod 700`) with own Python venv
- Never touches `~/.local/share/celina/venv`, `install.sh`, `stages/`, or system services
- 5 protocol layers: AWDL (`owl`), AirDrop (`opendrop`), Find My (`FindMy.py`), GSA/Anisette (localhost Docker), Silicon/SEP (hard boundary)
- Firewall pin: `127.0.0.1:6969:6969` — research services bind only to loopback, never external interfaces
- Account-safety guardrail: aborts if Apple/iCloud/GSA/Anisette session indicators detected unless `--allow-existing-apple-session` passed
- Zero-side-effect dry-run: `--dry-run` verifies every command without touching disk, APT, pip, or git
- Clean rollback: `./uninstall.sh` removes research venv; `--purge-data` wipes research directory

---

## 15. Supply-Chain Security

| Mechanism | Implementation |
|---|---|
| **Commit SHA pinning** | Tier B research repos (`owl`, `opendrop`, `findmy-py`) pinned to exact 40-char commit SHAs |
| **Docker digest pinning** | Anisette v3: `dadoum/anisette-v3-server@sha256:d551c75454bcbee68dc262d4247d5f87fa309dfc8f2125644d333c648596e3c7` |
| **GNOME extension pin** | `system/gnome/extensions.lock` with immutable `version_tag` |
| **Python dependency pinning** | `features/celina/requirements.txt` + `tools/requirements-tier-b.txt` with `pip install --require-hashes -r *.lock` |
| **Ubuntu APT** | Un-held (allows `unattended-upgrades` CVE patches) |
| **Flathub apps** | GPG-verified via OSTree |
| **Model hash verification** | SHA-256 for GGUF and FastText models (env-configured) |

---

## 16. Known Hardware & ECID/SEP Ceilings

- **ECID-Bound Device Quota & Secure Enclave (SEP):** No open-source Linux software can manufacture a cryptographic Apple SEP identity, bypass hardware attestation, or guarantee immunity from Apple server-side rate limits or account challenges.
- **Recommended iMessage Bridge:** Use BlueBubbles bridge (not Tier B research scripts) for daily iMessage.
- **Wayland Clipboard Persistence:** Native Wayland compositors drop clipboard selections when the owning app exits. Celina prefers `org.freedesktop.portal.Clipboard`, pairs `wl-clipboard` with GNOME's `clipboard-indicator` persistence extension.
- **8 GB OOM & ZRAM Policy:** `vm.swappiness=150` + `systemd-oomd`. `celina-eventd.service` (`OOMScoreAdjust=-300`, `MemoryMax=384M`) stays alive while `ollama.service` (`OOMScoreAdjust=300`, `ManagedOOMMemoryPressure=kill`) is reaped cleanly on model spikes.

---

## 17. Test Suite

**395 tests passing**, 5 skipped, **0 failed** across two consecutive `pytest -ra` runs (deterministic, zero flakiness).

Test files include:
- `test_native_os_integration`
- `test_hardware_biometrics`
- `test_audit_p0_p1_fixes`
- `test_audit_fixes_and_parity`
- `test_os_level_hardening`
- `test_ai_os_boundaries`
- `test_hardware_backends_p0_p1`
- `test_code_sandbox` (14 passed, 5 skipped — bwrap not installed)
- `test_mcp_perception_actuation` (includes `test_dbus_backend_mcp_action_cb_wiring`, `test_run_dbus_daemon_wires_mcp_action_cb`)
- `test_installer_dryrun_rollback` (includes `test_python_codebase_static_security_hygiene`, `test_install_dry_run_produces_zero_side_effects_in_clean_home`)

**Key security test:** `test_os_capability_manifest_shadow_ai_invariants_and_bwrap_builder` — verifies manifest parity, 106 tools, all invariants, Shadow-AI tier-1 attack blocked, bwrap namespace enforcement, `NoNewPrivileges=true` in systemd unit.

---

## 18. Verification Commands

Run these to confirm the deployed state matches this document:

```bash
# P1 Config integrity: whitespace + JSON Schema validation (fail-closed)
python tools/validate_configs.py && echo PASS

# Manifest integrity: recompute SHA-256, verify integrity block, assert copies identical
python tools/manifest_integrity.py --verify

# Both manifest copies byte-identical
diff -u system/capabilities/celina-capabilities.json features/celina/config/celina-capabilities.json && echo PASS

# Version consistency: all version fields match version.toml
python - <<'PY'
import json, tomllib
with open("version.toml","rb") as f: v = tomllib.load(f)
m = json.loads(open("system/capabilities/celina-capabilities.json").read())
assert m["celina_version"] == v["celina"]["version"]
assert m["manifest_version"] == v["manifest"]["version"]
n = json.loads(open("features/celina/config/neural-schema.json").read())
assert n["schema_version"] == v["schema"]["version"]
print("PASS: versions consistent")
PY

# Tool count and auth_operation coverage
python3 -c "import json; m=json.load(open('system/capabilities/celina-capabilities.json')); print('tools:', len(m.get('tools',{}))); print('auth_operation:', sum(1 for c in m['tools'].values() if 'auth_operation' in c))"

# proc_sys_ro must be 0 as fs_scope
grep -rn '"fs_scope": "proc_sys_ro"' system/capabilities/ features/celina/config/ | wc -l

# never_exposed_paths includes ~/.celina/models/
python3 -c "import json; m=json.load(open('system/capabilities/celina-capabilities.json')); print('never_exposed_paths:', len(m.get('never_exposed_paths',[]))); print('celina models protected:', any('celina/models' in p for p in m.get('never_exposed_paths',[])))"

# capabilities.py enforcement present
grep -c "VALID_FS_SCOPES\|AUTH_OP_TIER_MAP\|verify_manifest_integrity" features/celina/celina_pkg/capabilities.py

# common.sh rollback infrastructure
grep -n "snapshot_file\|rollback_all" core/common.sh

# Full test suite (deterministic)
python3 -m pytest tests/ -ra

# Pre-flight parity gate
bash stages/00-preflight.sh

# Dry-run install (zero side effects)
DRY_RUN=1 bash install.sh && git status --short   # must be clean

# Perf stack verification
bash tools/verify_perf_stack.sh
```

---

## 19. macubuntu Center — System Installer & Deployment GUI (`apps/macubuntu-center/`)

macubuntu Center is a **complete system installer/deployment tool**, not just a control plane. It is a GTK4/libadwaita application (`Adw.Application`) with an Apple Liquid-Glass design system, providing a full OS installation wizard, system wipe/reset, and post-install configuration.

### File Inventory

| File | Purpose |
|---|---|
| `apps/macubuntu-center/main.py` | GTK4/libadwaita app: sidebar navigation, 16 pages, X-Ray strip, metrics, decisions, drop-zone updater, Model Hub, API Keys, Access, Install wizard, Reset wipe |
| `apps/macubuntu-center/backend.py` | Telemetry, Keyring vault, model fit, `.mubundle` inspector, access policy |
| `apps/macubuntu-center/installer.py` | `InstallWizard`, `SystemWiper`, `PackageManager`, `DownloadManager` + offline mode & stage selection backend |
| `apps/macubuntu-center/style.css` | Apple Liquid-Glass GTK4 design system |
| `data/org.macubuntu.Center.desktop` | `.desktop` launcher into GNOME App Grid |

### InstallWizard Flow (7 Steps)

| Step | ID | Description |
|---|---|---|
| 1 | `download` | Download packages & stages (skipped in offline mode) |
| 2 | `verify` | Verify `.mubundle` signatures (Ed25519) |
| 3 | `install_base` | Install base system |
| 4 | `install_stages` | Run macubuntu install stages |
| 5 | `configure_hardware` | Configure hardware profile (ZRAM, App Nap, power, etc.) |
| 6 | `start_services` | Start Celina services |
| 7 | `reboot` | Restart required |

Each step row in the UI shows a status pill (`Pending`/`Running`/`Done`/`Error`) and a `Gtk.ProgressBar` with percentage text. An overall progress bar and status label track aggregate progress.

### 25 Install Wizard Features

The Install page (`_page_install()`) collects all install-time configuration through `Adw.PreferencesGroup` cards. Each card tracks state in `self._install_*` instance variables with safe defaults and exposes `_on_install_*()` handlers:

| # | Feature | Card | State Variables | Handler(s) |
|---|---|---|---|---|
| 1 | Offline install mode | Installation Source | `_install_offline_mode`, `_install_staged_bundles`, `_install_selected_stages` | `_on_install_source_toggled`, `_on_install_browse_bundle`, `_on_install_bundle_dropped`, `_refresh_bundle_list`, `_on_install_stage_toggled` |
| 3 | LUKS disk encryption | Disk Encryption | `_install_luks_enabled`, `_install_luks_passphrase` | `_on_install_luks_toggled`, `_on_install_luks_passphrase_changed`, `_on_install_save_recovery_key`, `_on_install_copy_recovery_key` |
| 4 | User account creation | Create Your Account | `_install_user_full_name`, `_install_user_account`, `_install_user_password`, `_install_user_admin`, `_install_user_auto_login`, `_install_user_require_pw_wake` | `_on_install_user_full_changed`, `_on_install_user_acct_changed`, `_on_install_user_pw_changed`, `_on_install_user_admin_toggled`, `_on_install_user_std_toggled`, `_on_install_user_auto_login_toggled`, `_on_install_user_require_pw_toggled` |
| 5 | ZRAM compressed swap | ZRAM Compressed Swap | `_install_zram_enabled`, `_install_zram_size`, `_install_zram_alg` | `_on_install_zram_toggled`, `_on_install_zram_size_changed`, `_on_install_zram_alg_changed` |
| 6 | Snapshot rollback | System Snapshots | `_install_snapshot_enabled` | `_on_install_snapshot_toggled` |
| 7 | App Nap controls | App Nap | `_install_app_nap_enabled` | `_on_install_app_nap_toggled` |
| 8 | Hardware profile presets | Hardware Profile Preset | `_install_hardware_profile` | `_on_install_profile_changed` |
| 9 | Network configuration | Network Configuration | `_install_network_dhcp`, `_install_network_static_ip`, `_install_network_gateway`, `_install_network_dns` | `_on_install_net_dhcp_toggled`, `_on_install_net_ip_changed`, `_on_install_net_gw_changed`, `_on_install_net_dns_changed` |
| 10 | Storage configuration | Storage Configuration | `_install_storage_auto_partition`, `_install_storage_btrfs` | `_on_install_stor_auto_toggled`, `_on_install_stor_btrfs_toggled` |
| 11 | Software selection | Software Selection | `_install_software` (dict: office, creative, development, gaming, multimedia) | `_on_install_software_toggled` |
| 12 | Timezone configuration | Timezone | `_install_timezone` | `_on_install_tz_changed` |
| 13 | Locale & keyboard | Locale & Keyboard | `_install_locale`, `_install_keyboard` | `_on_install_locale_changed`, `_on_install_kb_changed` |
| 14 | Accessibility options | Accessibility | `_install_a11y_screen_reader`, `_install_a11y_high_contrast`, `_install_a11y_large_text`, `_install_a11y_sticky_keys` | `_on_install_a11y_toggled` |
| 15 | Power management | Power Management | `_install_power_profile`, `_install_power_sleep` | `_on_install_power_profile_changed`, `_on_install_power_sleep_changed` |
| 16 | Security configuration | Security | `_install_security_firewall`, `_install_security_autoupdate`, `_install_security_snapd` | `_on_install_sec_firewall_toggled`, `_on_install_sec_autoupdate_toggled`, `_on_install_sec_snapd_toggled` |
| 17 | Backup configuration | Backups | `_install_backup_enabled`, `_install_backup_target` | `_on_install_bak_enabled_toggled`, `_on_install_bak_target_changed` |
| 18 | Update preferences | Update Preferences | `_install_update_channel`, `_install_update_auto` | `_on_install_upd_channel_changed`, `_on_install_upd_auto_toggled` |
| 19 | Services configuration | Services | `_install_svc_celina`, `_install_svc_npu`, `_install_svc_app_nap` | `_on_install_svc_toggled` |
| 20 | Install summary/finish page | Install Summary | `_install_finish_shown` | (read-only summary display) |
| 21 | GPG key management | GPG Keys (on Access page) | `_install_gpg_keys` (list) | `_on_gpg_import_key` |
| 22 | Model Hub integration | AI Models | `_install_models` (list) | `_on_install_model_toggled` |
| 23 | API Keys integration | API Keys (Optional) | `_install_api_keys_configured` | `_on_install_api_keys_toggled` |
| 24 | Access & Permissions | Access & Permissions | `_install_access_landlock`, `_install_access_sandbox` | `_on_install_acc_landlock_toggled`, `_on_install_acc_sandbox_toggled` |
| 25 | System Wipe integration | Selective Wipe Options / Confirm Reset | `_install_wipe_keep_home`, `_install_wipe_keep_configs`, `_install_wipe_confirm` | `_on_install_wipe_keep_home_toggled`, `_on_install_wipe_keep_configs_toggled`, `_on_install_wipe_confirm_changed` |

### Offline Mode (`.mubundle` Staging)

When Offline Install is selected, the installer:
- Shows a drag & drop drop zone (`Gtk.DropTarget`) for `.mubundle` files
- Provides a multi-file browse dialog (`Gtk.FileDialog.open_multiple`)
- Lists staged bundles with name, path, and a signed/unsigned pill
- Short-circuits `DownloadManager.run_download()` — no network download
- Filters stages by `selected_stages` (empty = all stages)

### System Wipe / Reset to Ubuntu

The Reset page (`_page_reset()`) provides:
- A warning pill about destructive action
- **What Will Be Removed** preview (packages, services, files, total count)
- **Selective Wipe Options**: Keep home directory, Keep user configs
- **Confirm Reset**: Type `WIPE` to enable the wipe button
- `SystemWiper` executes in a background thread with progress callbacks
- Reboot button shown on completion

### Backend Classes (`installer.py`)

| Class | Responsibility |
|---|---|
| `InstallWizard` | Orchestrates the 7-step install flow; accepts `target_tier`, `offline_mode`, `staged_bundles`, `selected_stages`; exposes `plan.overall_progress`, `run_next()`, `is_complete()` |
| `SystemWiper` | Executes the system wipe with progress callback; `get_wipe_preview()` returns removal summary |
| `PackageManager` | Installs/removes packages during base system install |
| `DownloadManager` | Downloads packages/stages (skipped in offline mode) |

### Polkit & `pkexec` Architecture

| Component | File | Purpose |
|---|---|---|
| Polkit action | `polkit/org.macubuntu.center.policy` | `org.macubuntu.center` action with `auth_admin` authorization |
| pkexec worker | `libexec/macubuntu-updater-helper` | Privileged updater worker (Ed25519 signature verification + Snapper snapshots + Stage 99) |
| Signed packager | `scripts/pack-update.sh` | Builds signed `.mubundle` tarballs (payload + manifest + SHA256SUMS + Ed25519 signature) |
| `.desktop` launcher | `data/org.macubuntu.Center.desktop` | GNOME App Grid entry point |

---

## 20. Stage 99 Integrity & Parity Gate Additions

`stages/99-verify.sh` includes smoke and verification coverage:

### Headless GTK Smoke Test
Verifies `apps/macubuntu-center/` source files (`main.py`, `installer.py`, `backend.py`, `style.css`) parse cleanly without requiring a live display server.

### Pack-Update `.mubundle` Signature Verification Test
End-to-end signed bundle verification using `scripts/pack-update.sh` (Ed25519 key pair + SHA256SUMS + fail-closed signature check; requires `zstd` on the target desktop).

---

## 21. Remaining 8 Unwired Screens (Master Agent Prompt)

The following 8 screens exist in `NAV_SECTIONS` and await `_page_*()` implementations as specified in `AGENT_PROMPT_REMAINING_SCREENS.md`:

| Screen ID | Title | Section | Backend Socket |
|---|---|---|---|
| `performance` | Performance & App Nap | SYSTEM | `/run/user/$UID/celina/app_nap.sock` |
| `appearance` | Appearance & Input | SYSTEM | (UI only) |
| `continuity` | Continuity & Power | SYSTEM | cgroup v2 (`/sys/fs/cgroup/`) |
| `recovery` | Snapshots & Recovery | SYSTEM | `/run/user/$UID/celina/snapshot.sock` |
| `knowledge` | Knowledge & Memory | CELINA AI & MODELS | SQLite (`$HOME/.local/share/celina/db/memory.db`) |
| `voice` | Voice & Live Playground | CELINA AI & MODELS | `/run/user/$UID/celina/voice.sock` |
| `agents` | Agents & Shortcuts | ACCESS & TRUST | `/run/user/$UID/celina/agents.sock` |
| `security` | 106 Tools & MCP03 | ACCESS & TRUST | `/run/user/$UID/celina/mcp.sock` |

---

## 22. Gate 4.7 Integration — Security, Model Selection, Self-Improve & Packaging

The unified `macubuntu` repository (`master`) integrates the full **celina-gate47** hardening and subsystem suite alongside Update 19/20:

### 22.1 Gate 4.7 Approval Security & Execution Boundary
- **Design & Scope:** `GATE47_APPROVAL_SECURITY_DESIGN.md` and `GATE47_RELEASE_SCOPE.md` define canonical UTF-8 JSON request binding, SHA-256 verification, opaque single-use broker grants, and fail-closed defaults (`gear0_5_1_mirage.py` disabled/fail-closed absent signed-manifest policy).
- **Modules (`features/celina/celina_pkg/`):** `approval_broker.py`, `approval_transport.py`, `policy.py`, `controlled_computer_use.py`, `security/`, `vault_audit.py`.
- **OS Sandboxing & Service Hardening:** `seccomp-bpf.policy`, `celina.service`, `polkit/org.macubuntu.center.policy`.

### 22.2 Model Selection & Routing (`MODEL_SELECTION_ARCHITECTURE.md`)
- **Modules (`features/celina/celina_pkg/`):** `model_selector.py` (`select_whisper_model()`, `select_ollama_model()`, `select_edge0_model()`, `decide_cloud_routing()`), `model_integrity.py`, `router_gear0.py`, `router_providers.py`, `router_receipts.py`, `frontier_escalation.py`, `llm/`.
- Resource-aware selection (RAM, disk, CPU/GPU/NPU) with explicit env overrides and zero implicit unapproved downloads.

### 22.3 Self-Improve & Protected Core Manifest
- **Protected Core Hash Lock:** `selfimprove/protected_hashes.json` tracks SHA-256 hashes of 6 critical modules (`approval.py`, `config.py`, `connectors/base.py`, `evidence.py`, `landlock_policy.py`, `net_policy.py`), alongside `selfimprove/l7_sensitive_paths.json`.
- **Self-Improve Engine:** `features/celina/celina_pkg/selfimprove/` (L2–L7 evaluators, patcher, worktree, wiring facade), `self_heal.py`, `self_model.py`, `docs/selfimprove/`, `tests/selfimprove/`.

### 22.4 Memory, Knowledge Graph, MCP & Runtime Extensions
- **Knowledge & Compression:** `bitemporal_kg.py`, `graph_store.py`, `memory_wal.py`, `context_compressor.py`, `seed_knowledge.py`, `world_model.py`, `sensory_buffer.py`, `persona.py`.
- **Runtime, Bus & MCP:** `event_bus.py`, `brain_harness.py`, `harness.py`, `cli_bridge.py`, `doctor.py`, `registry.py`, `prompt_transpiler.py`, `web_scraper.py`, `runtime/`, `neural/`, `shortcuts/`, `mcp/`, `mcp_dbus_desktop.py`, `mcp_media_ondemand.py`, `mcp_proactive_observer.py`.
- **Desktop Apps & Release Tooling:** `apps/celina-shortcuts/`, `apps/macubuntu-center/`, `celina/tools/`, `extensions/celina-island@macubuntu/`, `build-release.sh`, `build-zipapp.sh`, `setup-ab-install.sh`, `pyproject.toml`, `CODEOWNERS`, `.pre-commit-config.yaml`, `.secrets.baseline`.

### 22.5 Verification Gate Pipeline (`scripts/verify_all.sh`)
- **Gate 0:** `tools/manifest_integrity.py --verify` (canonical SHA-256 + byte-identical manifest check)
- **Gate 0b:** `tools/validate_configs.py` (JSON Schema + whitespace validation)
- **Gate 1–3 & Hygiene:** `pytest` with branch coverage floor (`tests/`, `tests/audit/`, `tests/selfimprove/`), `bandit`, `pip-audit`, `mypy`, `ruff`, `detect-secrets` (outputs to `audit-reports/`).

---

**End of Architecture Document** — v4.5 (Unified `macubuntu` + Gate 4.7)  
**Status:** Controls specified and code-verified; `celina-gate47` integrated on `master`. Threat model and operational gaps documented in §9–§10; remaining 8 UI screens pending per §21.
