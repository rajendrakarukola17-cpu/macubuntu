# Unified Open-Source Integration & Zero-Duplication Pipeline Blueprint for Celina

> **Status:** Complete Research & Architecture Plan (covering **ALL 29 open-source projects**).
> **Execution Rule:** Production code remains untouched while your **1,000-item capability test** runs. Once `full-1000-report.md` is uploaded, we will ship **Update 14** (all live fixes + 6-Provider Smart Router + Gear-0 speedups) followed by **Update 15** (this unified OSS pipeline integration — **zero duplication, zero heavy Electron/Docker bloat, engineered for 7.6 GiB RAM CPU-only Ubuntu 26.04**).

---

## 1. Unified 5-Stage Pipeline Architecture (How Everything Connects Without Duplication)

```text
STAGE 1 — UNIFIED INPUT SURFACES (nanobot MessageBus + OVOS + OpenWhispr + MambaBot)
  • Dynamic Island GTK3  • Terminal CLI  • Hold-to-Talk Voice Dictation & In-Place Rewrite
  • openWakeWord ("Hey Celina") + Silero VAD v6 (<1ms)
  • Optional celina-web Phone UI (<25 MB RAM)  • Optional Fail-Closed Telegram Channel
                                     │
                                     ▼
STAGE 2 — CONTEXT & PERCEPTION (Thuki + Hermes agentskills.io + AYO + nanobot Dream)
  • `screen:` On-Demand Screen Context (AT-SPI2 native tree first -> fast OCR -> VLM)
  • 3-Level Progressive `SKILL.md` Loader (~3k token compact index; full body on match)
  • Hybrid SQLite Memory (FTS5 + Vector + KG) + `/compact` + Idle `Dream` Consolidation
                                     │
                                     ▼
STAGE 3 — SMART ROUTING BRAIN (Gear-0 + Gloamy/FutureOS 6-Provider + Quantized Ollama)
  • Gear-0 Deterministic Reflexes (<50 ms, 0 API): OS toggles, CPU/RAM, apps, clipboard, skills
  • Task-Aware 6-Provider Cloud Failover (Groq · Cerebras · Gemini · NVIDIA NIM · Mistral · OpenRouter)
  • Gear-1 Private/Offline: Best Quantized Ollama (qwen3:1.7b / 4b Q4_K_M, granite3.1-moe:1b)
                                     │
                                     ▼
STAGE 4 — ZERO-TRUST EXECUTION & ACTUATION (NanoClaw + Browser-Use + MambaBot + HA + nanobot)
  • Credential-Blind Execution: Raw API keys stripped from child shell/sandbox/skill env
  • Indexed Element Grounding [0], [1], [2]... for both AT-SPI2 Desktop & Browser DOM
  • Human Hand-Off pause on Login/Captcha + Password-Field Guard + Dynamic Island Approval Box
  • `CronService` + `HEARTBEAT.md` Proactive Tasks + Home Assistant Local REST Preset
                                     │
                                     ▼
STAGE 5 — STREAMING OUTPUT & SELF-IMPROVEMENT (Pipecat + Kokoro-82M + Edge + Hermes)
  • Sentence-Chunked Streaming TTS (Piper / optional Kokoro-82M ONNX on CPU) + Auto-Barge-In
  • Live Step Progress Rail (`[1/4 ✅] [2/4 ⏳]`) in CLI & Dynamic Island
  • Reflective Learning Loop: Drafts verified `SKILL.md` after successful multi-step runs
```

---

## 2. Complete Project-by-Project Breakdown (Zero-Duplication Audit)

### Wave A — Agent Core, Skills, Multi-Provider, Voice & Browser

| Project | Core Mechanism | Already in Celina? | Decision & Target Module |
|---|---|---|---|
| **Hermes Agent & `agentskills.io`** | Portable `SKILL.md` (YAML frontmatter + *When to Use / Procedure / Pitfalls / Verification*), 3-level progressive loading, reflective auto-skill drafting with `user-locked`. | Partial (`skills.py`) | **ADOPT into `skills.py`** — add `SKILL.md` import/export, 3-level lazy loading, `Verification` section, reflective draft generator. |
| **Gloamy & FutureOS** | Trait-based multi-provider router, fail-closed security, scheduled task run ledger, forkable/compactable sessions. | Partial (`router.py`, `supervisor.py`) | **ADOPT into `router.py` & `supervisor.py`** — powers 6-Provider Smart Router (`F10`), `/compact`, and recurring task ledger. |
| **Browser-Use** | Injects `buildDomTree.js` to detect clickable elements (incl. same-origin iframes & DOM0 `onclick`), tags them `[0..N]`, clicks by integer index. | Partial (`automation.py`) | **ADOPT into `automation.py`** — add Indexed Element Grounding `[0..N]` for both AT-SPI2 desktop widgets & Playwright/CDP browser DOM. |
| **Pipecat** | Streaming Frame pipeline (`Mic -> Silero VAD v6 -> End-of-Turn -> STT -> Streaming LLM -> Streaming TTS`) with automatic interruption. | Partial (`voice.py`) | **ADOPT pattern into `voice.py`** — sentence-chunked streaming TTS + Silero VAD auto-barge-in (zero WebRTC server bloat). |
| **Fish Speech / CosyVoice** | Expressive neural TTS + cloning (**requires 6–12 GB+ NVIDIA VRAM**). | Piper TTS (`voice.py`) | **ADAPT FOR CPU PC**: Skip GPU-only Fish Speech/CosyVoice; support **`Kokoro-82M ONNX`** (Apache-2.0, <1 GB, faster-than-realtime on CPU, 54 voices) alongside Piper! |
| **MambaBot OSS** | Zero-dependency phone web UI with inline approval cards, `/compact`, and **Human Hand-Off** on captchas/logins. | Partial (CLI + Island) | **ADOPT cleanly** — optional on-demand `celina-web` (<25 MB RAM, localhost + pairing token) + Human Hand-Off pause in `automation.py`. |
| **Home Assistant (Assist)** | Local REST `/api/services/...` + Wyoming offline smart-home control. | Partial (`trigger_iot_webhook`) | **ADOPT into `trigger_iot_webhook`** — optional `HOME_ASSISTANT_URL` + `HOME_ASSISTANT_TOKEN` local preset (0 new tools). |
| **Edge (Todoforai)** | Live To-Do / Step Plan rail driving browser, shell, files, `DOCX`/`XLSX`. | Mostly covered | **ADOPT UX**: Live Step Progress Rail (`[1/4 ✅] [2/4 ⏳]`) in CLI & Dynamic Island. |
| **Open WebUI / LobeHub / Open Interpreter / AutoGPT / PersonalJarvis / Leon AI / Quasnooze** | Standalone web apps, code runners, MCP, memory, wake word. | **Already covered in Celina** | **DO NOT DUPLICATE** (would consume 500 MB–1.5 GB RAM on 7.6 GiB PC). |

### Wave B — OpenClaw Lightweight Family & Floating Voice/Screen Assistants

| Project | Core Mechanism | Already in Celina? | Decision & Target Module |
|---|---|---|---|
| **HKUDS/nanobot** | Clean ~4k-LOC Python `MessageBus` (`InboundMessage`/`OutboundMessage`), **`HEARTBEAT.md`** proactive checklist, **`CronService`** (`jobs.json`), nightly **`Dream`** memory compaction. | Partial (`router.py`, `memory.py`) | **ADOPT directly** — lightweight Python `MessageBus` uniting CLI/Island/Voice/Web/Telegram + `HEARTBEAT.md` + `CronService` + idle `Dream` memory compaction. |
| **NanoClaw** | **Credential Isolation Proxy** (raw API keys never exposed to untrusted tools/sandboxes) + per-session container isolation. | Partial (`bwrap`/Landlock) | **ADOPT security pattern** — strip all `*_API_KEY` env vars from child shell/sandbox/skill subprocesses (`credential-blind execution`), zero heavy Docker overhead! |
| **ZeroClaw & NullClaw** | Single-binary Rust minimal RAM footprint & strict Landlock/seccomp hardening. | Partial (`landlock_policy.py`) | **ADOPT lean principle** — keep zero-tick idle (<0.1% CPU), lazy imports, strict Landlock/rlimits without heavy Node/Electron/Docker services. |
| **CoPaw & MicroClaw** | Modular multi-channel gateway & compact Telegram-first personal automation. | None (local desktop only) | **ADOPT thin channel plugin** — single optional fail-closed Telegram bot plugin wired directly onto Celina's `MessageBus` (allowlisted to owner's chat ID only). |
| **TinyClaw & IronClaw** | Specialized agent team & personal CRM/contact workflows. | **Already covered** (`/swarm`, `search_contacts`, KG) | **NO DUPLICATION** — enrich existing KG/contacts tags cleanly without adding new engines. |
| **OpenWhispr** | System-wide Linux Wayland hold-to-talk **voice dictation into ANY app** (`whisper.cpp`/`sherpa-onnx` + filler-word cleanup) + **in-place voice rewrite of highlighted text**. | Partial (`writing_tool_transform`, `voice.py`) | **ADOPT into `voice.py` + `writing_tools.py`** — add global Dictate-at-Cursor & Voice-Rewrite-Selection pipeline (zero Electron bloat!). |
| **Thuki & AYO** | Instant **`screen:` prefix** for offline/on-demand screen awareness (code/errors/docs) + ambient desktop companion. | Partial (`read_screen`, `island_ui.py`) | **ADOPT into `router.py` + `island_ui.py`** — add instant `screen:` prefix capturing active window context cleanly (AT-SPI2 -> fast OCR -> VLM). |
| **Cursor Voice** | Voice orb clicking UI elements by name, typing, scrolling, shell via voice intent. | Partial (`click_on_text`, `island_ui.py`) | **PIPELINE together** — connect Voice -> Indexed Element Grounding `[0..N]` so speaking *"click Submit"* hits exact named element reliably. |
| **OpenVoiceOS (OVOS) & `openWakeWord`** | Linux-first pluggable audio bus + Apache-2.0 CPU-friendly **`openWakeWord`** + offline deterministic intent matching. | Partial (`voice.py`, `everyday_agents.py`) | **ADOPT `openWakeWord` (Apache-2.0)** + pluggable Wake/VAD/STT/Intent/TTS stages inside `voice.py`. |

---

## 3. Code & Open-Source Library Reuse Strategy (Clean Licensing, Zero Bloat)

Instead of copy-pasting incompatible Rust/Electron/Node.js repos that would conflict or eat RAM, we reuse **real permissive Python/ONNX open-source packages (MIT / Apache-2.0 / BSD-2)** + clean native Python implementations inside Celina's existing modules:

1. **Direct Permissive OSS Libraries (optional pip installs inside `~/.local/share/celina/venv`):**
   - `openwakeword` (Apache-2.0) — true offline CPU wake word (*OVOS / Home Assistant standard*)
   - `silero-vad` / ONNX (MIT, ~2.2 MB) — `<1 ms` speech detection & auto-barge-in (*Pipecat standard*)
   - `kokoro-onnx` (Apache-2.0, optional) — CPU faster-than-realtime studio TTS (*beats GPU-only Fish Speech on 8 GB CPU PCs*)
   - `playwright` (Apache-2.0, optional) — indexed `[0..N]` DOM grounding (*Browser-Use pattern*)
   - `croniter` (MIT) — persistent cron expression parsing (*nanobot `CronService` pattern*)
2. **Clean Native Python Integrations (preserving our exact 99-tool contract & 223+ green tests):**
   - `celina_pkg/bus.py` — Unified `MessageBus` (*nanobot*)
   - `celina_pkg/providers.py` — 6-Provider Smart Router (`Groq`, `Gemini`, `NVIDIA NIM`, `Mistral`, `Cerebras`, `OpenRouter`) + Quantized Ollama presets (*Gloamy / FutureOS*)
   - `celina_pkg/skills.py` — `agentskills.io` `SKILL.md` + 3-level disclosure + reflective drafting (*Hermes Agent*)
   - `celina_pkg/automation.py` — Indexed `[0..N]` element grounding + Human Hand-Off + `screen:` fast context (*Browser-Use / Thuki / MambaBot / Cursor Voice*)
   - `celina_pkg/voice.py` — Streaming sentence TTS + Silero VAD auto-barge-in + OpenWhispr hold-to-talk dictation & voice-rewrite (*Pipecat / OpenWhispr / OVOS*)
   - `celina_pkg/autonomy/supervisor.py` — `CronService` + `HEARTBEAT.md` + nightly `Dream` memory compaction (*nanobot / FutureOS*)
   - `celina_pkg/tools.py` — Credential-blind child env stripping (*NanoClaw*) + Home Assistant local REST preset (*HA Assist*) + Gear-0 speedups (`F1`–`F9`)
   - `celina_pkg/web_companion.py` — Optional on-demand `<25 MB` phone Web UI + optional fail-closed Telegram bridge (*MambaBot / MicroClaw / CoPaw*)

---

## 4. Phased Delivery Plan

- **Update 14 (Immediately after `full-1000-report.md`):**
  - Fix every bug/timeout from `smoke-30-report.md` + `full-1000-report.md` (`F1`–`F9`)
  - Ship **6-Provider Smart Router** (`Groq`, `Gemini`, `NVIDIA NIM`, `Mistral`, `Cerebras`, `OpenRouter`) + **Best Quantized Local Ollama** (`F10`)
  - Ship **Gear-0 Instant Local Reflexes** (`<50 ms`) + **NanoClaw Credential-Blind Sandbox** + **`screen:` Context Prefix** (`Thuki`)
- **Update 15 (Unified OSS Pipeline Superpowers):**
  - `nanobot` `MessageBus` + `HEARTBEAT.md` + `CronService` + `Dream` memory compaction + `/compact`
  - `Hermes Agent` / `agentskills.io` `SKILL.md` + 3-level progressive disclosure + reflective skill drafting
  - `Browser-Use` `[0..N]` Indexed Element Grounding + `MambaBot` Human Hand-Off + `Edge` Step Progress Rail
  - `Pipecat` streaming sentence TTS + `Silero VAD` auto-barge-in + `openWakeWord` + `Kokoro-82M ONNX` + `OpenWhispr` system-wide voice dictation/rewrite
  - Optional `<25 MB` `celina-web` phone companion + fail-closed Telegram bridge + Home Assistant local REST preset

---

## 5. Big-Tech Giants Open-Source Maturity Stack (Phase C — Making Celina Enterprise-Grade)

*All verified permissive open-source projects from major tech companies (Microsoft, Google, Anthropic, Meta, OpenAI, Hugging Face, ByteDance, Mozilla) filtered strictly for zero duplication and 7.6 GiB RAM Ubuntu 26.04 compatibility:*

| Tech Giant | Open-Source Project & License | Core Superpower | Exact Zero-Duplication Integration in Celina |
|---|---|---|---|
| **Anthropic** | **`sandbox-runtime` (`srt`, Apache-2.0)** + **`MCP`** | OS-level Linux/macOS filesystem + network sandboxing for arbitrary commands & MCP servers **without Docker/VMs** (all child processes inherit the sandbox). | Wrap `run_shell_command`, `run_python_sandbox`, and local MCP servers with OS-level sandbox rules (`srt` / Bubblewrap / Landlock fallback). |
| **Microsoft** | **`MarkItDown` (MIT)** | Lightweight Python converter turning **PDF, DOCX, XLSX, PPTX, HTML, audio, images** into crisp, table-preserving Markdown for LLMs. | Drop-in upgrade inside `read_document()` (`tools.py`) — replaces fragile raw PDF text extraction cleanly. |
| **Microsoft** | **`BitNet b1.58 2B4T` (MIT)** | Native 1.58-bit LLM running in **~0.4 GB RAM** at ~29 ms/token on pure CPU! | Optional ultra-light CPU offline tier alongside `qwen3:1.7b` for 8 GB laptops. |
| **Microsoft** | **`OmniParser V2` + `Magentic-UI` (MIT)** | Structured screen icon/element grounding + human-centered co-browsing hand-off. | Combine with `Browser-Use` `[0..N]` element indexing + Human Hand-Off pause in `automation.py`. |
| **Meta** | **`Llama Prompt Guard 2 (22M/86M)`** + **LlamaFirewall "Rule of Two"** | **22M-param (~45 MB, ~10 ms on 1 CPU core!)** prompt-injection & jailbreak classifier + hard architectural **Rule of Two** (never combine *untrusted input* + *private secrets* + *state/network mutation* without human approval). | Local CPU screening layer on fetched webpages, PDFs, clipboard, and MCP outputs + enforce Rule-of-Two policy directly in `security.py` / `router.py`. |
| **Google** | **`A2A` (Agent2Agent, Apache-2.0 / Linux Foundation)** + **`ADK` (Apache-2.0)** | Open inter-agent protocol (`/.well-known/agent.json` Agent Cards + JSON-RPC tasks) + deterministic workflow nodes mixed with dynamic LLM agents. | Expose Celina as an optional localhost A2A agent card & allow `/swarm` to call external A2A coding/research agents alongside `MCP`. |
| **Hugging Face** | **`smolagents` Code-as-Action Pattern (Apache-2.0)** | LLM emits ONE short sandboxed Python snippet calling tools as functions instead of 5 sequential JSON tool round-trips (**5× faster, ~70% fewer tokens!**). | Add optional `code-act` execution mode inside `router.py` + `run_python_sandbox` for multi-file/data analysis tasks (`Q191`, `T247`, etc.). |
| **OpenAI** | **`Agents SDK` Patterns (Apache-2.0)** + **`Whisper` (MIT)** | Typed agent handoffs, parallel input/output guardrails, structured trace spans. | Upgrade `/why` `TurnTrace` into structured trace spans + parallel safety guardrail checks. |
| **OWASP / Invariant** | **`MCP-scan` & Tool-Descriptor Hash Pinning** | Detects MCP "rug-pull" attacks where a remote/third-party MCP server silently changes a tool description after user approval. | Pin approved MCP tool schema SHA-256 hashes inside `mcp_client.py` (re-prompt user if hash changes!). |
| **ByteDance / Simular** | **`UI-TARS` / `Agent S` Grounding Split (Apache-2.0)** | Decouples high-level **Planner LLM** from specialized **GUI Grounder** (never loads heavy 7B VLM locally on 8 GB RAM). | Use AT-SPI2 / `[0..N]` DOM locally first, escalating to cloud VLM/UI-TARS endpoint ONLY when visual grounding is truly needed. |

### Standing PC Hygiene Rule (Applies to Updates 14, 15, & 16)
Every update that adds, replaces, or removes a module/pattern MUST include explicit cleanup rules in `updates/apply-update.sh --cleanup` to delete obsolete files, stale `__pycache__/*.pyc`, superseded archives, and deprecated config entries on the live Ubuntu PC (zero disk/RAM bloat, zero duplicates).

---

## 6. Top GitHub Ecosystem Audit (2026 High-Star Repos Mapped Directly to Celina)

*Verified GitHub projects (2026 active releases) ranked by real engineering impact, license safety, and fit on a 7.6 GiB RAM / 5400 RPM HDD Ubuntu 26.04 PC:*

| GitHub Repo | Stars / License | Why It Leads on GitHub | Keep Ours vs. Adopt Into Celina |
|---|---|---|---|
| **`mem0ai/mem0`** + **`getzep/graphiti`** + **`vectorize-io/hindsight`** | ~67k★ / ~32k★ / ~5.7k★ · Apache-2.0 / MIT | Best-in-class agent memory: **add/update/delete/noop fact reconciliation** (Mem0), **bi-temporal KG validity windows (`valid_from`/`valid_to`)** (Graphiti), and **Ebbinghaus importance decay + 4-way RRF** (Hindsight). | **REFINE OUR `memory.py`** (no heavy Neo4j server!): Add `valid_from`/`valid_to` columns to our SQLite KG, Mem0-style fact reconciliation on `remember_fact`, Ebbinghaus `last_accessed` decay, + optional `OKF`/`MEMORY.md` human-readable Git export! |
| **`asg017/sqlite-vec`** | MIT / Apache-2.0 | Single tiny SQLite loadable vector extension — fast hybrid vector + FTS5 search in **one `.db` file with 0 background RAM**. | **ADOPT into `memory.py`** — upgrades our SQLite `hybrid_memories` table natively with zero Qdrant/Chroma server overhead. |
| **`unclecode/crawl4ai`** | ~87k★ · Apache-2.0 · Python | #1 GitHub web crawler for AI agents; turns JS-heavy web pages into clean LLM-ready Markdown. | **ADOPT (optional) inside `fetch_webpage` / `fetch_url_text`** — keeps our strict SSRF `net_policy` while massively improving web extraction quality. |
| **`docling-project/docling`** *(IBM / LF AI)* | MIT · Python | Layout-aware PDF/DOCX/PPTX/XLSX parsing & clean table extraction (complements Microsoft `MarkItDown`). | **ADOPT alongside `MarkItDown`** inside `read_document()` for crisp PDF/Office extraction. |
| **`pydantic/pydantic-ai`** | MIT · Python (v2.68+) | Strict typed tool-argument validation with automatic LLM self-correction retries when a small local/free model sends malformed JSON args. | **ADOPT pattern inside `router.py`** — eliminates mangled tool-call arguments from `qwen3:1.7b` and free cloud models! |
| **`vicinaehq/vicinae`** | Native C++/Qt Linux | Native ultra-fast Linux **Raycast / Spotlight** command palette (clipboard history, snippets, app/file/window search, Raycast extensions). | **INSPIRE + OPTIONAL COMPANION**: Keep our MacUbuntu Spotlight + Dynamic Island lean; borrow snippet expansion & clipboard history UX (or offer optional Vicinae integration for power users). |
| **`block/goose`** | ~55k★ · Apache-2.0 · Rust | Top local-first developer agent with clean YAML **Recipes** & modular extension lifecycle. | **KEEP OUR PYTHON CORE**; borrow Goose's clean YAML Recipe validation for our `skills.py` / `agentskills.io` workflows. |
| **`screenpipe/screenpipe`** & **`Mirix-AI/MIRIX`** | ~22k★ (Source-Avail Commercial) / ~3.5k★ | 24/7 continuous screen + audio memory + per-pipe deterministic YAML data permissions. | **SKIP 24/7 continuous recording** (thrashes 5400 RPM HDD + uses Commercial License); **ADOPT** deterministic YAML per-pipe data permission rules + on-demand `screen:` snapshots! |
| **Massive Frameworks (`LangChain`, `AutoGen`, `CrewAI`, `LlamaIndex`, `OpenClaw`)** | 50k–385k★ | Heavy orchestration wrappers with large dependency trees & extra memory overhead. | **KEEP OUR CELINA CORE** — our zero-bloat `CelinaRouter` + 6 Everyday Agents + 99 tools is already faster (`0–32 ms` Gear-0!), leaner on 7.6 GiB RAM, and far easier to maintain! |


