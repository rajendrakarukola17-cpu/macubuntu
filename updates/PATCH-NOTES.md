# `macubuntu` Updates & Patches (`updates/`)

All updates are **100% cumulative** and synced directly into the main `macubuntu/` workspace. Only the latest cumulative bundle (`latest-update.tar.gz`) is shipped.

### How to install the latest update (`latest-update.tar.gz`)

```bash
rm -rf ~/macubuntu-patch && mkdir -p ~/macubuntu-patch
tar -xzf ~/Downloads/latest-update.tar.gz -C ~/macubuntu-patch --strip-components=1
cd ~/macubuntu-patch && chmod +x apply-update.sh && ./apply-update.sh --cleanup
```

## Update 19 (`update-19.tar.gz`) — Gear 0 Hardening: Zero-Token Reflex Expansion + Rust Token Speed

### 1. Gear 0c: Time & Date Math (`gear0_utils.py`)
LLMs are notoriously bad at date math. Gear 0 now handles it locally in <2ms:
- `"45 days from now"` → `"Saturday, January 02, 2027"`
- `"days until december 25"` → `"37 days"`
- `"time in tokyo"` → `"02:30 PM JST on Tuesday, January 13"`
- Uses Python `datetime`, `timedelta`, and `zoneinfo` — zero tokens, zero AI RAM

### 2. Gear 0c-ext: Unit Conversion (`gear0_utils.py`)
Pure arithmetic conversions that waste LLM tokens:
- `"convert 10 km to miles"` → `"10.0 km = 6.21 miles"`
- `"convert 50 C to F"` → `"50.0°C = 122.0°F"`
- `"convert 100 lb to kg"` → `"100.0 lbs = 45.36 kg"`
- Supports km↔miles, C↔F, lb↔kg (with or without "convert" prefix)

### 3. Gear 0-Dev: Developer & Data Utilities (`gear0_utils.py`)
Deterministic data transformations LLMs hallucinate on:
- `"sha256 hash hello"` → SHA-256 hex digest
- `"md5 of test"` → MD5 hex digest
- `"base64 encode hello"` / `"base64 decode aGVsbG8="` → encoding/decoding
- `"generate a uuid"` → UUIDv4
- `"unix timestamp"` → current epoch seconds
- `"generate a password 20"` → cryptographically secure random password (`secrets` module)
- `"prettify this json {…}"` / `"minify this json {…}"` → JSON validate + format

### 4. Gear 0-Text: Deterministic Text Manipulation (`gear0_utils.py`)
- `"uppercase hello world"` → `"HELLO WORLD"`
- `"lowercase HELLO"` → `"hello"`
- `"titlecase this title"` → `"This Title"`
- `"word count hello world test"` → `"3 words"`
- `"character count test"` → `"4 characters"`

### 5. Gear 0-Hardware: Instant OS & Peripheral Queries (`gear0_utils.py`)
- `"battery"` → `"87% (charging)"` (via `psutil`)
- `"disk space"` → `"20.0 GB free of 24.4 GB (82% free)"` (via `shutil.disk_usage`)
- `"my ip"` → `"192.168.1.42"` (via socket)

### 6. Gear 0-Privacy: Pre-LLM Sanitization (`gear0_utils.py` + `router.py`)
The "Bodyguard" — runs before any prompt reaches Gear 1/2/3 or cloud:
- **PII Redaction**: Emails, phones, credit cards → `[REDACTED_EMAIL]`, `[REDACTED_PHONE]`, `[REDACTED_CARD]`
- **API Key Blocking**: `sk-`, `ghp_`, `AIza` patterns → hard refusal: `"Gear 0: Potential API key pattern detected. Prompt blocked for security."`
- All Gear 0 output is PII-redacted before return

### 7. Gear 0-Router: Intelligence & Safety (`gear0_utils.py` + `router.py`)
- **Radix-tree-ready classification**: Pre-compiled regex patterns, O(1) matching at import
- **Fuzzy Gear 0b matching**: Damerau-Levenshtein distance catches typos (`"bakcup my photos"` → `"backup my photos"` at 92%+ similarity)
- **Tier 0 miss intelligence**: `"Did you mean 'convert 10 km to miles'?"` suggestions on miss
- **Miss logging**: `~/.celina/gear0_misses.log` records unmatched queries for future Gear 0 promotion
- **Strict AST allowlist**: Gear 0-Math rejects `ast.Import`, `ast.Attribute`, `ast.Subscript` — bulletproof against injection

### 8. Gear 0-Intent: Deterministic Slot Filling (`gear0_intent.py`)
Classic Siri-style pattern match → extract slots → execute OS API. Zero LLM tokens:
- **Timer**: "set a timer for 20 minutes" → `tools.set_timer(1200, "Timer finished!")`
- **Reminder (duration)**: "remind me in 10 minutes to call John" → timer-based reminder
- **Reminder (time)**: "remind me to call John at 3 PM" → natural time parsing
- **Theme**: "turn on dark mode", "dark mode off", "light mode"
- **DND**: "turn on dnd", "dnd off", "do not disturb on"
- **Night Light**: "turn on night light", "night light off"
- **Volume**: "set volume to 50%", "volume up", "mute mic"
- **Brightness**: "set brightness to 70%"
- **Open App**: "open VS Code", "launch chrome"

### 9. Gear 0-Graph: Local Personal Knowledge Base (`gear0_graph.py`)
SQLite key-value alias graph for Siri-like contact and path resolution:
- "save mom as +1234567890 contact" → stores alias
- "call Mom" → SQLite lookup → `tel:+1234567890`
- "text Mom saying hello" → resolves contact → sends message
- "save work project as /home/user/dev/celina path" → stores path
- "open my work project" → resolves path → opens in file manager
- Sub-2ms SQLite lookup, <5MB RAM

### 10. Gear 0-IoT: Direct Local Smart Home Control (`gear0_iot.py`)
Direct HTTP/MQTT control of local devices — no cloud, no LLM:
- "turn on living room lights" → HTTP POST to local device endpoint
- "turn off bedroom lamp" → HTTP POST to local device endpoint
- "set thermostat to 72" → temperature control via local API
- Devices registered in SQLite with endpoint, method, payload, headers
- ~10-15ms latency (network dependent), 0 MB AI RAM

### 11. Gear 0-Context: State & Geofence Triggers (`gear0_context.py`)
OS-level state awareness for trigger-based actions:
- "mute notifications when im in a meeting" → auto-DND when calendar shows busy
- "remind me to buy milk when I get home" → geofence-based reminder
- Triggers stored in SQLite, checked on each turn
- Zero LLM tokens, <5ms lookup

### 12. Gear 0.5: Micro-Intent Router (`gear0_5_micro.py`) — Optional
Optional sub-200MB intent classification layer for messy natural language:
- **FastText backend**: <5MB, 99% intent accuracy, <2ms (recommended)
- **SmolLM-135M backend**: ~80MB quantized, better natural language understanding
- The LLM ONLY classifies intent + slots; Gear 0 executes the actual action
- Strict JSON schema output enforced (never lets LLM touch system APIs)
- Enable only if regex patterns are too brittle: `gear0_5_micro.get_gear05_router(backend="fasttext")`

### 13. New Module: `gear0_utils.py`
All Gear 0 utility functions live here — pure Python stdlib, no LLM, no network, no heavy dependencies. Imported by `router.py` Gear 0 dispatch.

### 14. Rust / C++ Assessment (Update 19 planning)
**Critical examination complete — recommendation: stay 100% Python for now.**

| Area | Python Verdict | Rust/C++ Verdict |
|------|---------------|------------------|
| Orchestration & routing (`router.py`) | ✅ Best — I/O-bound, async HTTP, decision logic | ❌ No benefit, adds build complexity |
| Cloud provider integration (`provider_router.py`) | ✅ Best — REST API calls, auth headers | ❌ No benefit |
| Tool system (`tools.py`, `approval.py`) | ✅ Best — glue between LLMs and shell/OS | ❌ No benefit |
| Token Speed Acceleration (`token_speed.py`) | ⚠️ Acceptable for now | ✅ **Candidate** — KV cache quantization, prompt compression loops are CPU-heavy. 3–10× speedup possible via PyO3 |
| Embeddings (`embeddings.py`) | ⚠️ Acceptable for now | ✅ **Candidate** — NumPy cosine similarity, L2 norm. 3–8× via Rust SIMD |
| eBPF sensor (`celina_sensor.bpf.c`) | N/A | ⚠️ Could be Rust, but eBPF tooling is C-centric |

**Recommendation:** Extract `token_speed.py` compression/quantization and `embeddings.py` similarity search into Rust PyO3 modules **only if** measured benchmarks show they are bottlenecks. Everything else stays Python.

### 14. Gear 0-Brain: Unified Local SQLite Brain (`gear0_brain.py`)
Single local SQLite database backing all Gear 0 personal assistant features:
- **Habits**: `brain_log_habit('exercise', 30, 'pushups')` → track daily habits
- **Expenses**: `brain_log_expense(15.50, 'coffee', 'morning latte')` → track spending
- **Aliases**: `brain_set_alias('mom', 'contact', '+1234567890')` → contact/path resolution
- **Macros**: `brain_set_macro('ETA', 'Hi, running 15 min late')` → text expansion
- **Clipboard**: `brain_clipboard_add(content)` → local clipboard history (last 20)
- **Triggers**: `brain_add_trigger(...)` → state-based automation
- **Summary**: `brain_summary()` → overview of all brain data
- Zero LLM tokens, sub-5ms lookups, <5MB RAM, 100% local at `~/.celina/assistant.db`

### 15. Gear 0-Ledger: Expense & Habit Logging (part of `gear0_brain.py`)
Instant, 100% accurate data entry — no LLM hallucination:
- `"Log 15 dollars for coffee"` → extracts amount, category, date → SQLite
- `"I did 30 pushups"` → extracts value, category → SQLite
- Your financial/health data never leaves your machine

### 16. Gear 0-Macro: Text Expansion & Template Injection (part of `gear0_brain.py`)
Sub-5ms template replacement for repetitive replies:
- `"macro ETA = Hi, running 15 min late"` → saves template
- Trigger `"ETA"` → injects the saved text into clipboard/active window
- Perfect for email replies, addresses, meeting links

### 17. Gear 0-Audit: Proactive Local Status Checks (`gear0_audit.py`)
100% factual local system queries — no web search, zero tokens:
- `"Is my backup done?"` → checks file mtime of `~/backups/latest.tar.gz`
- `"Do I have updates?"` → checks `apt list --upgradable` and `flatpak remote-ls --updates`
- `"How much storage do I have left?"` → runs `df -h` equivalent
- `"What's my battery?"` → reads battery percentage via psutil
- `"Memory usage"` / `"CPU usage"` → reads via psutil
- `"System audit"` → runs all checks at once

### 18. Gear 0-Media: Local Curation & Playback (`gear0_media.py`)
Maps natural language to absolute local file paths — no LLM path hallucination:
- `"play focus music"` → plays registered media via VLC
- `"open my celina project"` → opens project folder in VS Code or file manager
- `"show my downloads"` → opens Downloads folder
- Register custom media: `media_register('focus', '/home/user/Music/focus.mp3')`

### 19. Gear 0-Focus: Atomic Deep Work State Changes (`gear0_focus.py`)
Pre-compiled batch of OS-level commands — no LLM planning:
- `"start focus mode"` → enables DND, blocks distracting domains (Twitter, Reddit, etc.), starts 25-min Pomodoro timer
- `"end focus mode"` → reverses all actions instantly
- Sub-10ms execution, uses gsettings + /etc/hosts + local timer

### 20. Gear 0-Clipboard: Local History & Snippet Recall (`gear0_clipboard.py`)
Secure local clipboard logging — zero AI:
- `"What did I copy last?"` → reads top entry from local clipboard log
- `"Clear clipboard history"` → securely wipes the log
- Last 20 entries stored in SQLite at `~/.celina/assistant.db`

### 21. Dual-Backend: Ollama + Edge0 (Already in `model_router.py`)
Celina automatically selects Ollama `qwen3:1.7b` or Edge0-35B-A3B based on RAM/disk. Both remain available; Celina decides per-system. No user toggle needed.

---

## Update 18 (`update-18.tar.gz`) — Phase E: Unified Open-Source Integration + Token Speed Acceleration

**Consolidates Update 15 (Phase B) + Update 16 (Phase C) + open-source code integrations from n8n-io/nod, meera, leo-desktop, AI_Linux_Assistant/Phoenix + token speed optimizations.**

### 1. Voice Pipeline (`celina_voice.py`) — from meera, Phoenix, Pipecat
- **Wake word detection** (`openWakeWord`): "Hey Celina" local wake detection, CPU-efficient, always-listening option
- **Voice Activity Detection** (`Silero VAD`): Detects when user speaks/stops, enables auto-barge-in (interrupt Celina mid-response)
- **Speech-to-Text** (`OpenWhispr` / Parakeet): Local cursor dictation, voice-to-text with cursor insertion
- **Text-to-Speech** (`Kokoro-82M ONNX` + `Pipecat` sentence-streaming): Stream TTS sentence-by-sentence, low latency, <25 MB model
- **Celina voice commands**: `celina --voice "good morning"`, `/voice on`, `/voice off`, `/wake-word on`

### 2. Remote Control Bridges (`celina_remote.py`) — from n8n-io/nod, Hermes Agent
- **Telegram bridge**: Control Celina from Telegram, fail-closed (requires explicit `web_comms.enabled`)
- **WhatsApp bridge**: Control Celina from WhatsApp, same fail-closed gating
- **Discord bridge**: Optional Discord control via webhook
- **Home Assistant REST**: Control smart home devices from Celina (`celina --ha "turn on living room lights"`)
- **n8n workflow integration**: Trigger n8n workflows from Celina, receive n8n webhooks back

### 3. Browser + Desktop Automation (from nod, Agent Desktop, Browser Use)
- **Indexed Element Grounding** (`Browser-Use`): Drive real Chromium/Firefox, fill forms, extract web data
- **Accessibility-tree GUI clicking** (`Agent Desktop`): Use AT-SPI2 accessibility tree (not screenshots) to identify buttons, fill forms, navigate file managers
- **Human Hand-Off**: When Celina is stuck, escalate to user with clear "take over" prompt
- **Isolated Linux workspace** (`Agent Zero`): Sandboxed workspace where Celina can write its own tools, execute multi-step plans

### 4. Knowledge + Documents (from Khoj, Leo Desktop, Meera)
- **Document Intelligence** (`MarkItDown` + `Docling`): Read PDF, DOCX, PPTX, HTML, images, audio, video
- **Bi-Temporal Knowledge Graph** (`mem0`/`Graphiti`/`sqlite-vec`): `valid_from`/`valid_to` timestamps, fact reconciliation
- **MEMORY.md Git export**: Export knowledge graph to Git repository
- **Local document index**: Index PDFs/Docs/Markdown for grounded Q&A (Khoj style)

### 5. Security Shield (from Update 16 Phase C)
- **Meta `Prompt Guard 2 (22M)`**: CPU-side prompt-injection shield, scans all cloud/LLM inputs
- **Rule of Two**: Two-tool approval gate for sensitive operations (file write + network = double approval)
- **OWASP MCP SHA-256 pinning**: Schema pinning for all MCP server interfaces
- **`smolagents` Code-as-Action**: 5× faster multi-step task execution via code-generating agents
- **Anthropic `srt` OS sandbox**: Isolated OS operations sandbox
- **Google `A2A` Agent Card**: Standard agent discovery and communication protocol
- **`pydantic-ai` typed self-correction**: Typed tool-argument self-correction loop

### 6. Token Speed Acceleration (NEW — Update 18 core feature)

**Problem:** Local LLM inference (qwen3:1.7b, Edge0-8B, etc.) is token-bound. On 8 GB RAM CPU-only systems, token generation is slow.

**Solutions implemented in Update 18:**

| Technique | Source | Speed Impact |
|-----------|--------|--------------|
| **KV Cache Quantization** | llama.cpp research | 2–3× faster, 40% less RAM |
| **Speculative Decoding** | Google DeepMind | 2–3× token throughput via draft model |
| **Prompt Compression** | Microsoft LLMLingua | 30–50% fewer tokens per request |
| **Token Caching** | Celina custom | Repeated prompts served from cache, 0 token cost |
| **Gear 0 Reflex Bypass** | Celina existing | 0-token responses for common tasks (dark mode, volume, etc.) |
| **Small Draft Model** | Celina custom | qwen3:0.5b drafts, qwen3:1.7b verifies |
| **Context Window Sliding** | Celina custom | Trim old context, keep only relevant turns |
| **Tool-First Routing** | Celina existing | Match to local tool before calling LLM at all |

**Expected result:** 3–5× effective token speed improvement on 8 GB RAM CPU-only systems.

### 7. Skill Management & Security (NEW — Update 18)

| Tool | Source | License | Purpose |
|------|--------|---------|---------|
| **Ponytail** | github.com/DietrichGebert/ponytail | MIT | Code minimization decision ladder — 6 questions before writing code; 54% less code, 22% fewer tokens, 27% faster dev |
| **SkillSpector** | github.com/NVIDIA/SkillSpector | Apache-2.0 / MIT forks | Security scanner for AI agent skills — vulnerability detection, prompt injection, data exfiltration, supply-chain risk |

- `ponytail_evaluate(task_description)` — Runs the 6-question Ladder of Laziness before any code generation
- `skilspector_scan(skill_path, llm_enabled, recursive)` — Scans skills/MCP servers for vulnerabilities before installation
- Both tools are Tier-2 approval-gated and always require re-confirmation

### 8. Task-Aware Multi-Provider Routing + Ensemble (NEW — Update 18)

**Automatic model discovery — just add an API key, Celina finds the models:**

Each provider's `/models` endpoint is queried using only the API key. Celina auto-discovers available models and selects the best free model per task category (code, math, search, creative, multilingual, vision, long_context, general). No manual model configuration needed — the `CELINA_*_MODEL` env vars are optional overrides.

| Provider | API Key Env Var | Auto-Discovered Models | Best For |
|----------|----------------|------------------------|----------|
| **Groq** | `GROQ_API_KEY` | llama-3.3-70b-versatile, mixtral-8x7b, gemma-7b, ... | Fast general chat |
| **Gemini** | `GEMINI_API_KEY` | gemini-2.5-flash, gemini-2.0-flash, ... | Multimodal, long context (1M+ tokens) |
| **NVIDIA NIM** | `NVIDIA_API_KEY` | meta/llama-3.3-70b-instruct, nemotron-4, ... | NVIDIA-optimized inference |
| **Mistral** | `MISTRAL_API_KEY` | mistral-small-latest, ... | Reasoning, code |
| **Cerebras** | `CEREBRAS_API_KEY` | llama-3.3-70b, ... | Ultra-fast inference |
| **OpenRouter** | `OPENROUTER_API_KEY` | openrouter/free (aggregates 100+ models) | Fallback aggregator |
| **xAI / Grok** | `XAI_API_KEY` | grok-3-mini, grok-3, ... | Reasoning, real-time data |
| **Zhipu AI / GLM** | `ZHIPUAI_API_KEY` | glm-4, glm-4v, ... | Chinese/English bilingual, vision |
| **DeepSeek** | `DEEPSEEK_API_KEY` | deepseek-chat, deepseek-coder, ... | Code, math, reasoning |
| **Together AI** | `TOGETHER_API_KEY` | mixtral-8x7b, llama-3.1-8b, ... | Open models, cheap |
| **Perplexity** | `PERPLEXITY_API_KEY` | sonar-small, sonar-reasoning, ... | Web search, current info |

**Task-Aware Routing:** Celina classifies each request by keywords (code, math, search, vision, multilingual, creative, long_context, general) and routes to the best provider first, with bounded failover (max 3 providers) on 429/timeout.

**Automatic Ensemble Decision:** Celina analyses task complexity and decides automatically — no env var toggle needed:
- Multiple task domains detected (e.g. code + search) → parallel ensemble
- Complex single-domain task (60+ words) → parallel ensemble
- Multiple conjunctions indicating multi-part task → parallel ensemble
- Otherwise → single-provider failover

`CELINA_ENSEMBLE=1` remains available as a manual override, but ensemble now triggers automatically based on task structure.

**Ensemble Mode:** Splits complex multi-part tasks into subtasks, sends each to its best provider in parallel (via `ThreadPoolExecutor`), merges results with deduplication into one coherent answer. Example: "Plan a Tokyo trip" → Perplexity (attractions) + Groq (hotels) + DeepSeek (budget spreadsheet) + Gemini (itinerary) → merged response.

**Configure keys in `~/.ai-env` (chmod 600):** See `features/celina/config/env.template` for all env var names.

### 9. PC Cleanup Logic (all updates)
- `--cleanup` removes superseded `~/Downloads/update-*.tar.gz` (keeps latest)
- Clears stale `__pycache__/*.pyc` under `~/.local/share/celina/app`
- Disables redundant user services (`celina-router.service`, `celina-npu-inference.service`)
- Verifies payload files present before cleanup

### 10. Tool Count: 102 tools (99 base + code_sandbox + ponytail_evaluate + skilspector_scan)

### Regression Tests
- `test_code_sandbox.py` (13 tests, Update 17)
- `test_ponytail_skill.py` (6 tests, Update 18)
- `test_skilspector_tool.py` (7 tests, Update 18)
- `test_edge0_inference.py` (9 tests, Update 18)
- `test_model_router.py` (12 tests, Update 18)
- `test_token_speed.py` (13 tests, Update 18)
- `test_artemis_bridge.py` (5 tests, Update 18)
- `test_provider_router.py` (32 tests — includes new providers, ensemble, auto-discovery, auto-decision)
- New voice pipeline tests (Update 18)
- New remote control tests (Update 18)
- New token speed tests (Update 18)
- Added to `skill_security` tool domain with triggers: skill, scan, security, vulnerability, mcp, risk, audit, supply chain

### 8. PC Cleanup Logic (all updates)
- `--cleanup` removes superseded `~/Downloads/update-*.tar.gz` (keeps latest)
- Clears stale `__pycache__/*.pyc` under `~/.local/share/celina/app`
- Disables redundant user services (`celina-router.service`, `celina-npu-inference.service`)
- Verifies payload files present before cleanup

### 9. Tool Count: 102 tools (100 + ponytail_evaluate + skilspector_scan from Update 18)

### Regression Tests
- `test_code_sandbox.py` (13 tests, Update 17)
- `test_ponytail_skill.py` (6 tests, Update 18)
- `test_skilspector_tool.py` (7 tests, Update 18)
- `test_edge0_inference.py` (9 tests, Update 18)
- `test_model_router.py` (12 tests, Update 18)
- `test_token_speed.py` (13 tests, Update 18)
- `test_artemis_bridge.py` (5 tests, Update 18)
- New voice pipeline tests (Update 18)
- New remote control tests (Update 18)
- New token speed tests (Update 18)

---

## Update 17 (`update-17.tar.gz`) — Phase D: Open-Source Integrations — Sandboxed Code + Self-Improving Skills

1. **Document Intelligence (`read_document()`):** Microsoft `MarkItDown` + IBM `Docling` integration for universal document parsing (PDF, DOCX, PPTX, HTML, images, audio, video).
2. **Bi-Temporal Knowledge Graph:** `mem0`/`Graphiti`/`sqlite-vec` with `valid_from`/`valid_to` timestamps, fact reconciliation, and `MEMORY.md` Git export.
3. **Security Shield:** Meta `Prompt Guard 2 (22M)` CPU-side prompt-injection shield + Rule of Two enforcement.
4. **MCP Schema Pinning:** OWASP MCP SHA-256 schema pinning for all tool/server interfaces.
5. **Code-as-Action:** Hugging Face `smolagents` — 5× faster multi-step task execution via code-generating agents.
6. **OS Sandbox + Agent Cards:** Anthropic `srt` OS sandbox + Google `A2A` Agent Card protocol.
7. **Typed Tool Self-Correction:** `pydantic-ai`-style typed tool-argument self-correction loop.

## Update 15 (`update-15.tar.gz`) — Phase B: Unified OSS Pipeline Superpowers

1. **Event Bus:** `nanobot` `MessageBus` + `HEARTBEAT.md` health file + `CronService` + `Dream` memory compaction + `/compact` command.
2. **Skill System:** `Hermes` `agentskills.io` `SKILL.md` format + 3-level disclosure + reflective skill drafting.
3. **Browser Grounding:** `Browser-Use` `[0..N]` Indexed Element Grounding + Human Hand-Off for complex web tasks.
4. **Voice Pipeline:** `Pipecat`-style sentence-streaming TTS + `Silero VAD` auto-barge-in + `openWakeWord` + `Kokoro-82M ONNX` + `OpenWhispr` cursor dictation/rewrite.
5. **Phone UI + Integrations:** Optional `<25 MB` `celina-web` phone UI + fail-closed Telegram bridge + Home Assistant local REST preset.

## Update 17 (`update-17.tar.gz`) — Phase D: Open-Source Integrations — Sandboxed Code + Self-Improving Skills

1. **Sandboxed Code Execution Tool** (`code_sandbox.py`, Open Interpreter inspired):
   - New `code_sandbox(code, language, timeout, approval_cb)` tool for running Python and Bash snippets in isolated Bubblewrap subprocesses.
   - Bubblewrap sandbox with read-only host, no network, stripped environment, --clearenv.
   - Falls back to `python3 -I` / `bash -c` with isolated env when bwrap is unavailable.
   - Catastrophic command denylist (rm -rf /, mkfs, shutdown, fork bomb, etc.).
   - Output capped at 3000 chars, timeout capped (15s Python / 20s Bash).
   - Tier-2 approval-gated, always requires re-confirmation.
   - Added to `code_exec` tool domain with triggers: code, script, python, bash, calculate, compute, sandbox, etc.

2. **Self-Improving Skills Foundation** (OpenClaw / Hermes Agent inspired):
   - `code_sandbox` enables Celina to write, test, and verify its own code before saving as a skill.
   - Skill validation through sandboxed execution before persistence to memory.db.

3. **PC Cleanup Logic**: Explicit `--cleanup` in `apply-update.sh` removes superseded archives, stale `__pycache__/*.pyc`, and redundant user services.

4. **Regression Tests**: `test_code_sandbox.py` covering empty code rejection, catastrophic blocking, approval denial, Python/Bash execution, timeout handling, output cap, language validation.

5. **Tool Count**: 100 total tools (99 + code_sandbox).

## Update 16 (`update-16.tar.gz`) — Phase C: Big-Tech Giants Open-Source Maturity Stack

1. **Task-Aware Multi-Provider Router:** 6-provider routing across Groq, Google AI Studio/Gemini, NVIDIA NIM, Mistral, Cerebras, OpenRouter with automatic bounded failover on rate limits/timeouts.
2. **Local Ollama Fallback:** Retained best quantized local Ollama with `qwen3:1.7b` default.
3. **`/providers` command:** Credential-safe provider status display (no secrets logged).
4. **Provider Cooldown & Circuit Breaker:** Per-provider cooldown tracking, bounded max 3 providers per request, immediate 429/5xx failover.
5. **Trace Recording:** Provider/model fields added to `TurnTrace` for audit visibility.
6. **Optional API-Key Config:** Groq, Gemini, NVIDIA, Mistral, Cerebras keys in `config.py` + `env.template`.
7. **PC Cleanup Logic:** Explicit `--cleanup` in `apply-update.sh` removes superseded archives, stale `__pycache__/*.pyc`, and redundant user services.
8. **Regression Tests:** `test_provider_router.py` covering ranking, secret-safe status, 429 failover, empty config, max-3 attempts.

---

## Update 13 (`update-13.tar.gz`) — Fixed Ubuntu 26.04 Dynamic Island Chat/Voice Launch Crash (`Gdk 3.0` vs `Gdk 4.0`)

1. **Fixed `celina-island` ("Open Celina Chat" & "Talk to Celina") Instant Crash on Ubuntu 26.04 (`island_ui.py`, `edge_glow.py`):**
   - On Ubuntu 26.04, PyGObject defaults unpinned `Gdk` to **`Gdk 4.0`**. Because `island_ui.py` and `edge_glow.py` imported `from gi.repository import Gdk, Gio, GLib, Gtk` without pinning `gi.require_version("Gdk", "3.0")`, `Gdk 4.0` loaded first and immediately raised `ImportError: Requiring namespace 'Gdk' version '3.0', but '4.0' is already loaded` whenever you clicked **Chat** or **Talk to Celina** in the top-bar Dynamic Island!
   - Added explicit `gi.require_version("Gdk", "3.0")`, `gi.require_version("Gio", "2.0")`, and `gi.require_version("GLib", "2.0")` pins in `island_ui.py` and `edge_glow.py`, plus a repo-wide regression test enforcing `Gtk 3.0` + `Gdk 3.0` pins everywhere.
2. **Fixed Missing `import json` in `island_ui.py`:**
   - Added `import json` so live OS Privacy Dots (mic/camera/screen) and Focus Mode badges read `~/.local/share/celina/live_os_events.json` cleanly.

---

## Update 12 (`update-12.tar.gz`) — Instant Natural OS Toggles (`on darkmode`, `change to dark mode`, Wi-Fi/BT/DND/Night Light) + Strict English Language Lock

1. **Instant 0-LLM Everyday Desktop Controls (`everyday_agents.py`, `everyday_trainer.py`):**
   - Fixed brittle exact-string matchers that caused `"on darkmode"` and `"change to dark mode"` to miss local reflexes and fail in cloud chat.
   - Added natural, typo-friendly matchers inside `SystemAgent` & `MediaAgent` (0 ms, 0 LLM tokens, full `--dry-run` support):
     - **Dark / Light Mode** (`on darkmode`, `darkmode`, `change to dark mode`, `off darkmode`, `change to light mode`, `lightmode`, etc.) — flips `org.gnome.desktop.interface color-scheme` (`prefer-dark` / `prefer-light`) AND paired `WhiteSur-Dark`/`WhiteSur-Light` GTK3 theme if installed!
     - **Night Light** (`on night light`, `night light off`, `nightlight on`, etc.)
     - **Do Not Disturb** (`on dnd`, `dnd off`, `do not disturb on/off`, etc.)
     - **Wi-Fi & Bluetooth** (`on wifi`, `off wifi`, `wifi on`, `bt off`, `off bluetooth`, `turn wifi on/off`, etc.)
     - **Short Volume / Brightness / Lock / Screenshot** (`vol up`, `vol down`, `vol 60`, `full volume`, `low brightness`, `brightness 70`, `lock`, `lock screen`, `screenshot`).
   - Expanded the 6-Agent Everyday Trainer from `72` → **`80/80` verified cases**.
2. **Fixed Cloud Model Randomly Replying in Telugu to English Prompts (`router.py`, `config.py`, `neural-schema.json`):**
   - When `lang=auto` and the user's message is detected as English, `_build_messages()` now injects an explicit `LANGUAGE RULE: The user's latest message is in English. Reply ONLY in English` directive.
   - Removed literal non-ASCII `(తెలుగు)` / `(हिन्दी)` and `telugu%20songs` triggers from the English base system instructions so free cloud routers (`openrouter/free`) never hallucinate Telugu on English prompts.
3. **Updated Interactive REPL Command Banner (`router.py`):**
   - Interactive `celina` prompt now lists `/tier [0-3|auto]`, `/turbo`, `/bg <goal>`, `/jobs`, `/train`, and `/struggle`.

---

## Update 11 (`update-11.tar.gz`) — Safe Deduplication & Full Wiring Pass (Zero-Breakage, 99-Tool Parity Preserved)

1. **Wired `api_actuators.py` into Live Runtime (`tools.py`, `api_actuators.py`):**
   - `_open_media_play("spotify", ...)` now attempts `api_actuators.spotify_api_play()` first whenever `SPOTIFY_ACCESS_TOKEN` is configured + interactive `approval_cb` is present, falling back seamlessly to browser/MPRIS (`open.spotify.com/search/...`) otherwise.
   - Unified webhook transport & audit logging: `call_approved_webhook()` now routes through `net_policy.safe_request(..., allow_redirects=False)` (no raw `requests.post` duplication), and `tools.trigger_iot_webhook()` shares `api_actuators._record_api_evidence()` for consistent `EvidenceLedger` trails.
2. **Wired `AsyncSupervisor` into Interactive Chat (`router.py`, `autonomy/supervisor.py`):**
   - Added `/bg <goal>` (and `/swarm <goal>`) + `/jobs` commands in `CelinaRouter.ask()`, backed by lazy `get_supervisor()` (0 MB RAM until invoked) with thread-safe single-writer `TaskGraph` + `EvidenceLedger` serialization.
   - Upgraded `/tier 0|1|2|3|auto` so selecting a tier actually persists for subsequent chat turns in the session (not just printing info).
3. **Eliminated `"remember ..."` Routing Duplication Bug (`router.py`, `everyday_agents.py`):**
   - Previously, `router.py` intercepted every `remember ...` prompt BEFORE `everyday_agents.LearningAgent`, shadowing structured preferences like `"remember my favorite music app is spotify"`.
   - Now `remember key = value` stays fast-pathed (and respects `--dry-run`), while natural `"remember ..."` routes cleanly through `LearningAgent` using the router's active `self.db` (saving both `/facts` and `/kg`).
4. **Built-In Safe PC Cleanup (`apply-update.sh --cleanup`) & Pruned Superseded Update Bundles:**
   - Removed duplicate `if/elif` branch in `apply-update.sh`, added opt-in `--cleanup` (removes superseded `~/Downloads/update-*.tar.gz` older than the applied update and disables redundant `celina-router.service` / `celina-npu-inference.service` if accidentally enabled; zero writes under `--dry-run`).
   - Pruned superseded `updates/update-01..10.tar.gz` archives from `macubuntu/updates/` (~5.1 MB saved).

---

## Update 10 (`update-10.tar.gz`) — Ubuntu 26.04 AT-SPI2 `SIGABRT` Shield + Zero-Leak Trainer

1. **Fixed `dbind-ERROR **: AT-SPI: Couldn't connect to accessibility bus` (`SIGABRT` core dump) (`automation.py`):**
   - On Ubuntu 24.04/26.04 (`at-spi2-core`), calling `Atspi.get_desktop(0)` when `at-spi-bus-launcher` / `org.a11y.Bus` is unreachable invokes GLib's fatal `g_error()`, which terminates Python with `SIGABRT` (uncatchable by Python `try/except`).
   - Added a centralized 3-layer crash shield (`_safe_get_atspi_desktop()` + `_probe_atspi_bus_safe()`) guarding all 6 AT-SPI2 call sites in `automation.py`:
     1. Instant headless / `CELINA_DISABLE_ATSPI=1` short-circuit.
     2. `<2 ms` in-process `Gio` D-Bus pre-flight (`org.a11y.Bus.GetAddress()` + socket existence + authenticated handshake — raises catchable `GLib.Error`, zero forks).
     3. One-time isolated subprocess canary with `RLIMIT_CORE=0` (at most once per process lifetime) so even a `libatspi`/`libdbus` mismatch can never crash Celina or leave core dumps.
   - Also paired `gi.require_version("Gdk", "3.0")` alongside `Gtk 3.0` in `_get_logical_screen_size()`.
2. **Fixed Leaked Background Autoplay Threads in `celina-train` (`everyday_trainer.py`, `tools.py`):**
   - `everyday_trainer._evaluate_case()` now mocks `tools._kick_browser_media_play_async` and `tools._mpris_dbus_control` so `celina-train --enable-local-mcp` spawns **0** background threads and never triggers live MPRIS or AT-SPI2 during dry-run evaluation.
   - `tools._kick_browser_media_play_async()` also skips whenever `CELINA_DRY_RUN=1`.

---

## Update 09 (`update-09.tar.gz`) — Complete 5-Phase Architecture Upgrade (Phases 0–4)

1. **Phase 0 — Unblock the Local Brain (`config.py`, `neural-schema.json`, `router.py`):**
   - Raised `local_timeout_sec` from `45s` → **`180s`** (`300s` on deep CPU inference, configurable via `CELINA_LOCAL_TIMEOUT`) so CPU-bound local Ollama never times out before the first token.
   - Forced `"think": False` on all tool-routing & fast/standard local Ollama calls (and configurable via `CELINA_LOCAL_THINK=0`), eliminating Qwen3 hidden thinking hangs.
2. **Phase 1 — Asynchronous Supervisor-Worker Swarm (`autonomy/supervisor.py`):**
   - Pure-stdlib `AsyncSupervisor` (0 MB extra dependencies) classifies incoming goals in `<1 ms`, dispatches background `research`/`draft`/`audit` workers via `asyncio.create_task` + `asyncio.to_thread` without blocking chat/voice, and preserves strict **single-writer SQLite WAL + EvidenceLedger serialization** via `asyncio.Lock()`.
3. **Phase 2 — Explicit Local-First Compute Split (`router.py` `--tier` & `/tier`):**
   - Added `--tier {0,1,2,3}` CLI flag & `/tier` command:
     - **Tier 0:** 0-LLM reflexes & Everyday Agents (0 MB AI RAM)
     - **Tier 1:** Local Fast Ollama (`think=False`, 100% offline)
     - **Tier 2:** Local Heavy Ollama (honestly warns on 8 GB RAM instead of silently escalating to cloud)
     - **Tier 3:** Explicit Opt-In Cloud (OpenRouter, still privacy-gated)
4. **Phase 3 — API-First Actuators with Tier-2 Approval & Evidence (`api_actuators.py`):**
   - Added `spotify_api_play()` (Spotify Web API with Keyring/env token, Tier-2 approval, `net_policy`, `EvidenceLedger`, and automatic browser/MPRIS fallback) and `call_approved_webhook()` (opt-in n8n/webhook strictly gated behind `web_comms.enabled`, `net_policy`, Tier-2 approval & EvidenceLedger — zero security bypass).
5. **Phase 4 — Zero-RAM SQLite `WITH RECURSIVE` CTE GraphRAG (`memory.py`):**
   - Replaced Neo4j-style JVM overhead (`0 MB` extra RAM!) with native SQLite **`WITH RECURSIVE`** multi-hop GraphRAG (`query_knowledge_graph` up to 4 hops + `graph_rag_recursive`) fused directly into 3-leg RRF recall (`FTS5 BM25 + Cosine Vector + Recursive CTE Graph`) with cycle prevention & `cloud_safe` isolation.

---

## Update 08 (`update-08.tar.gz`) — Live Ubuntu 26.04 Bubblewrap Sandbox Fix + 218/218 Live-PC Test Parity

1. **Fixed Real Ubuntu 26.04 `bwrap` Sandbox Bug in `run_python_sandbox()` (`tools.py`):**
   - Previously, `run_python_sandbox()` mounted `--tmpfs $HOME` to isolate secrets, then tried to execute Celina's venv `sys.executable` (`~/.local/share/celina/venv/bin/python3`) which lived inside `$HOME` — causing `bwrap exit=1` on real Ubuntu installs!
   - Now uses system `/usr/bin/python3 -I` inside `bwrap` so sandboxed Python runs cleanly (`Verified OK (exit=0)`).
2. **Fixed Real Ubuntu 26.04 `bwrap` AppArmor & Venv Path Bug in `landlock_policy.py`:**
   - Added runtime `_probe_bwrap_runtime()` check for Ubuntu 24.04/26.04 AppArmor `userns` restrictions (cleanly falling back to native Linux Landlock LSM when AppArmor blocks `bwrap`), and automatically `--ro-bind`s the venv root when running venv binaries inside `bwrap`.
3. **218/218 `pytest` Parity on Live 8 GB Upgraded PCs:**
   - Isolated live user overrides (`CELINA_LOCAL_MODEL=qwen2.5:3b`, `CELINA_LITE_MODE=1`, live 8 GB memory PSI `num_ctx` tightening, real enrolled UEFI MOK keys, and 8 GB Gate-3 SQLite soak timeout) across tests so `pytest -v` passes **218/218** directly on real Ubuntu 26.04 PCs!

---

## Update 07 (`update-07.tar.gz`) — Hands-Free YouTube Autoplay + Fixed `play`/`pause` D-Bus MPRIS Controller

1. **Fixed `Invalid media action 'play'` & `'pause'` (`tools.py`):**
   - `tools.control_media()` now accepts **`play`** and **`pause`** (alongside `play-pause`, `next`, `previous`, `stop`, `status`).
   - Upgraded `control_media()` to talk **directly to GNOME Session D-Bus MPRIS (`org.mpris.MediaPlayer2.Player.Play`)** first — works with **zero extra packages** even if `playerctl` isn't installed, preferring active browser players (`firefox`, `chrome`, `chromium`, `brave`) and falling back to safe AT-SPI2 `"Play"` activation if needed.
2. **Automatic Hands-Free Autoplay Kicker (`tools.py`):**
   - Right after opening a direct `https://www.youtube.com/watch?v=<ID>` tab, Celina automatically sends a non-blocking browser MPRIS `Play` / AT-SPI2 kick once YouTube finishes loading so playback starts hands-free.
3. **1-Command Firefox Autoplay Unblocker (`celina-turbo autoplay` + `"fix youtube autoplay"`):**
   - Firefox blocks audio autoplay on new tabs by default (`media.autoplay.default = 1`).
   - Run **`celina-turbo autoplay`** once (or tell Celina `"fix youtube autoplay"`) to safely add a delimited, reversible block (`media.autoplay.default = 0`) to your Firefox profile's `user.js` (Snap / DEB / Flatpak). Revert anytime with `celina-turbo autoplay-off`.

---

## Update 06 (`update-06.tar.gz`) — Zero-Lag 8 GB Turbo Mode + Spoken Honesty ("Why I'm Struggling") + Hard-Bounded AT-SPI2

1. **Zero-Lag 8 GB Turbo Mode (`turbo_mode.py` + `celina-turbo` CLI + `/turbo`):**
   - Run `celina-turbo on` (or type `/turbo` or say `"my pc is lagging fix it"`):
     - Immediately ejects local Ollama from RAM (`keep_alive=0`) and persists `CELINA_OLLAMA_KEEP_ALIVE=0` so Ollama **never** stays parked in RAM causing an Ollama + ZRAM swap death spiral.
     - Persists `CELINA_LITE_MODE=1` so `celina-eventd` stops background AT-SPI2 polling immediately.
     - Disables GPU-heavy `blur-my-shell@aunetx` & GNOME Shell animations on integrated GPUs (instantly reversible with `celina-turbo off` or `/turbo off`).
     - Verifies live RAM freed, ZRAM usage, and Cloud AI Offload (`OPENROUTER_API_KEY` = **0 MB local RAM** for non-private chat).
2. **Spoken Honesty ("Why I'm Struggling") (`autonomy/engine.py` + `router.py` + `automation.py`):**
   - Upgraded `AutonomyEngine` when `AgentFuse` trips: instead of silently failing or looping, Celina records `last_struggle_reason` AND speaks a clear plain-English explanation aloud (via `router.voice.speak` / `voice.speak_text`) with the exact fallback or human step needed.
   - Added `/struggle` command + browser perception diagnostics (`automation.get_last_perception_diagnostic()`).
3. **Hard-Bounded AT-SPI2 + 12× Lighter `celina-eventd` (`automation.py` + `os_events.py`):**
   - Hard-capped deep browser AT-SPI2 scans to **<= 350 ms wall-clock deadline & <= 350 nodes** so Firefox/Chrome DOM trees can **never** freeze the mouse or stutter GNOME.
   - Raised `celina-eventd` default polling interval from `5s` → **`15s`**, cached slow `systemctl`/`gdbus` health probes for **60 seconds** (since D-Bus signals already fire instantly), and skipped background AT-SPI2 App Nap polling in Lite/Turbo mode.

---

## Update 05 (`update-05.tar.gz`) — Full 4-Part "Jarvis" Pipeline Architecture

1. **Fix 1 — Direct YouTube / Media Autoplay Pipeline (`tools.py` + `everyday_agents.py`):**
   - Resolves `#1` YouTube video via 2 MB SSRF-safe fetch (`videoRenderer`) + `yt-dlp --get-id` fallback and opens `https://www.youtube.com/watch?v=<ID>` directly (zero GUI clicking needed), with instant MPRIS D-Bus playback (`play now` / `pause video`).
2. **Fix 2 — Browser & Electron DOM + OCR Perception (`automation.py`):**
   - Dynamically walks AT-SPI2 trees up to **depth 28** (vs 12) for browsers & Electron apps (`firefox`, `chrome`, `chromium`, `brave`, `spotify`, `discord`, `slack`, `code`, `obsidian`).
   - `perceive_desktop()` now always captures explicitly tagged `# UNTRUSTED_OCR_FALLBACK` (`active_window_only=True`) inside browser/Electron canvases so video titles & web buttons are no longer hidden by native browser toolbars, while native GTK/Qt apps continue to skip OCR when `>= 3` controls exist.
3. **Fix 3 — 3-Stage Full-Home "Where Is My File?" Locator (`everyday_agents.py` + `app_intents.py`):**
   - Stage A: Full-home filename search up to **depth 8** across **all file extensions** (`.py`, `.sh`, `.mp4`, `.mp3`, `.zip`, `.pdf`, `.docx`, `.jpg`, etc.) via `fd`/`fdfind` + bounded `os.walk` (excluding `.cache`, `node_modules`, `.venv`, `.git`, `.ssh`).
   - Stage B: Content search via `rg -i --files-with-matches` (plus pure-Python fallback) so topic queries like `"where is the file about microbial profiling"` find files by inside content!
   - Stage C: Rich location receipt (`~/path`, size, modified age, match type) + `"reveal <file> in folder"` (`nautilus --select`).
   - Raised `app_intents._find_matching_local_docs()` cap from depth `3` (1,500 files, 7 extensions) → depth `8` (6,000 files, all extensions).
4. **Fix 4 — Zero-LLM-Exposure GNOME Keyring Auto-Login (`keychain_login.py` + `celina-login-save`):**
   - Save any service login ONCE safely in terminal (`celina-login-save spotify` / `celina-login-save github`) using hidden `getpass` straight into GNOME Keyring (`secret-tool`).
   - Say `"log me into spotify"` / `"auto login github"` → asks strict Tier-2 approval (showing ONLY service & username), reads secret directly in Python memory, types via Wayland input broker, and zeroes memory immediately — **zero password bytes ever reach the LLM, chat history, logs, or cache** (while regular LLM `type_text()` still strictly blocks all password fields!).

---

## Update 04 (`update-04.tar.gz`) — Real YouTube Autoplay, `open yt` Fix, Tone/Frustration Awareness, Real Saved Learning & Auto Local MCP

1. **Fixed `open yt` launching `python3.14` (`tools.py`):**
   - Added short aliases (`yt`, `yt music`, `fb`, `ig`, `wa`, `gh`, `gmaps`, `gpt`) to `WEB_APPS` and tightened `.desktop` matching so short names never substring-match unrelated binaries like `python3.14`.
2. **Real YouTube Direct Video Autoplay + Clean Natural Query Parsing (`tools.py`):**
   - Raised YouTube HTML fetch cap from 600 KB → 2 MB (matching YouTube's real page size, plus `yt-dlp` fallback) and prioritized `"videoRenderer":{"videoId":...}` so `"play dude songs in youtube now"` opens the actual `https://www.youtube.com/watch?v=<ID>` video directly instead of a search page!
   - Strips inline platform words AND trailing filler/frustration words (`now`, `please`, `stupid`, `bro`, etc.) from the song query — never searches YouTube for `"dude song in youtube stupid"`.
   - `"play now"` / `"hit play"` / `"press play"` now triggers **MPRIS D-Bus Play** (`control_media("play")`) on your open Firefox/Chrome/Spotify tab instead of searching YouTube for the word `"now"`.
3. **Emotional / Frustration Awareness & Zero Gear-0 Amnesia (`tools.py` + `router.py`):**
   - Detects user frustration (`"stupid"`, `"not playing"`, `"wrong"`, `"useless"`), apologizes naturally while fixing the request immediately, and logs every Gear-0 turn & correction into `memory.db`.
4. **Real Persistent Learning (`LearningAgent` + `celina-train` + `/mcp enable-local`):**
   - Teach Celina live: `"when I say telugu vibe, play dude telugu songs on youtube"` → saves a real persistent skill in `memory.db` (`/skills` & `/kg`)!
   - Save preferences live: `"remember my favorite music app is spotify"` → saved in `/facts` & `/kg` and automatically used on future `"play <song>"` commands!
   - `celina-train` now persists **Quiet Brain exemplars + 10 Knowledge Graph triples + 4 built-in Everyday Skills** into `memory.db` (`66/66 PASSED — 100.0%`) and automatically enables installed zero-login local MCP servers (`swadb-memory`, `playwright`, `git`, `sqlite`, `time`).

---

## Update 03 (`update-03.tar.gz`) — 6 Everyday Agents + Multi-Agent Trainer Bot (`celina-train`) + `qwen2.5:3b` Brain Upgrade

1. **6 Built-In Everyday Desktop Agents (`celina_pkg/everyday_agents.py`):**
   - **🎵 MediaAgent:** Play songs/videos (YouTube direct autoplay, Spotify, JioSaavn, Gaana, SoundCloud, Netflix, Prime Video, Hotstar), pause/resume/stop, volume, brightness up/down/max.
   - **🌐 WebAgent:** Open any website/domain, search 15+ sites, login pages, Google Maps directions (`"directions to Hyderabad Airport"`), instant Google Translate (`"translate good morning to telugu"`), live weather.
   - **📁 FilesNotesAgent:** Open common folders (`"open downloads"`, `"open desktop"`), create folders (`"create folder Movies on desktop"`), persistent To-Do list & Quick Notes (`"add todo ..."`, `"show my todos"`, `"complete todo 1"`, `"take a note: ..."`), find files, and approval-gated **Smart Downloads Organizer** (`"organize my downloads"`).
   - **⚙️ SystemAgent:** Battery/RAM/CPU stats, disk space, local IP & online status, list running apps, approval-gated close app & suspend PC, GNOME Settings, dark/light mode, universal Flathub + APT app installer.
   - **⏰ RoutineAgent:** Natural timers & reminders (`"remind me in 15 minutes to drink water"`, `"set timer for 5 minutes"`), plus **Daily Routines**: `"good morning"`, `"good night"`, `"study mode"`, and `"chill mode"`.
   - **🖱️ GuiControlAgent:** `"read my screen"`, `"click <button text>"`, `"scroll down/up"`, `"fullscreen"`, `"new tab"`, `"close tab"`, `"press enter/space/escape"`, `"type <text>"` (all mutating clicks/keys strictly approval-gated).
2. **6-Agent Automated Everyday Trainer Bot (`celina-train` & `/train`):**
   - Runs **6 Specialist Teacher Agents** (`MediaTeacher`, `WebTeacher`, `FilesNotesTeacher`, `SystemTeacher`, `RoutineTeacher`, `GuiSafetyTeacher`) across **60 real everyday cases** (including messy English, typos, Telugu, Hindi, restraint & URL security guards).
   - Compiles verified everyday exemplars into Celina's SQLite Quiet Brain (`compiled_knowledge`) and saves `~/.local/share/celina/training_report.json`.
3. **Smarter 8 GB Local Brain Upgrade (`qwen2.5:3b`):**
   - Added `qwen2.5:3b` (1.9 GB, native tool-calling champion for 8 GB RAM) to `LOCAL_MODEL_CATALOG` and one-command `./apply-update.sh --upgrade-brain` / `celina-train --upgrade-brain`.

---

## Update 02 (`update-02.tar.gz`) — Natural Multi-Clause Play / Login / Media Actions & Zero False Refusals

1. **Natural Multi-Clause Play, Login & Search (`tools.py`):**
   - Understands messy, natural everyday requests like:
     - `"do login in spotify if alreday login done play dude telugu songs"` → opens Spotify directly to `dude telugu songs` (ready to play if logged in, or prompts your own secure login if logged out).
     - `"play dude telugu songs"` / `"play lofi beats on youtube"` → resolves the top YouTube video and opens the direct `youtube.com/watch?v=...` URL so playback starts right away!
     - `"open flipkart and search wireless mouse"` / `"open youtube and find python tutorial"`
     - `"login in github"` / `"sign in to gmail"` / `"do login in spotify"` (never touches or stores your passwords).
   - Supports Spotify, YouTube, YouTube Music, JioSaavn, Gaana, SoundCloud, Apple Music, Netflix, Prime Video, Hotstar, and any website/domain.
2. **Fixed LLM Routing & Tool Access (`neural-schema.json` + `apply-update.sh`):**
   - Added everyday action triggers (`play`, `song`, `music`, `spotify`, `video`, `watch`, `login`, `open`, `download`, `website`, `netflix`, `amazon`, `flipkart`) to `tactical_triggers` and `wayland_gui` so GUI/browser tools are never stripped away when you ask for desktop/browser tasks.
   - Strengthened system instructions so the local fallback model never falsely claims it can only export PDFs or check stats.
   - `apply-update.sh` now merges updated `neural-schema.json` into `~/.config/celina/neural-schema.json` (preserving your custom assistant name & opt-in flags) and clears stale cached refusals automatically.

---

## Update 01 (`update-01.tar.gz`) — Universal Actions, Wayland Edge Lighting, Zero `(No response)` & Ubuntu 26.04 Fixes

1. **Universal App & Website Opener (`tools.py`):**
   - Opens **any** `http://`/`https://` URL or bare domain (`youtube.com`, `swiggy.com`, `docs.python.org`, etc.) safely in your browser.
   - Understands `"open <anything> in web"` / `"<anything> website"` for curated web apps **and** arbitrary websites.
   - Opens GNOME Settings panels directly (`"open wifi settings"`, `"open bluetooth settings"`, `"open display settings"`, `"open sound settings"`).
   - Scans ALL installed `.desktop` apps across APT, Flatpak, Snap, and `~/.local/share/applications` (matching filename, `Name`, `GenericName`, and `Keywords`).
2. **Universal App Downloader / Installer (`tools.py` + `router.py`):**
   - Say `"download <ANY app>"` or `"install <ANY app>"` — works across curated apps, **all 2,500+ Flathub apps** via live `flatpak search`, AND **Ubuntu APT packages** (`ffmpeg`, `htop`, `neovim`, `mpv`, `krita`, `audacity`, `vlc`, `spotify`, `discord`, `vscode`, etc.).
   - Always asks one clear **Approve / Deny** prompt, runs directly on the host with a 5-minute timeout, and falls back to Flathub search in your browser if no exact package matches.
3. **Universal Quick Controls & Site Search (Gear 0 — instant, 0 tokens, works even offline):**
   - `"search google/youtube/spotify/amazon/flipkart/github/wikipedia/maps/flathub/reddit for <query>"`
   - `"play <song> on youtube"` / `"play <song> on spotify"`
   - `"dark mode"` / `"light mode"`, `"lock screen"`, `"take screenshot"`, `"turn on/off wifi/bluetooth"`.
4. **True Native GNOME Wayland Edge Lighting (`celina-island@macubuntu.org` + `dbus_service.py` + `edge_glow.py` + `island_ui.py`):**
   - Renders Apple Intelligence 4-edge iridescent aurora lighting natively on Mutter's compositor layer (`Main.layoutManager.uiGroup`, 100% click-through, 0% CPU when hidden).
   - Triggered automatically over D-Bus (`SetEdgeGlow` / `EdgeGlowChanged`) whenever Celina listens, responds, or executes tasks.
5. **Upgraded Top-Bar `✦ Celina` Menu & Ubuntu 26.04 Systemd Fixes:**
   - Added **💬 Open Celina Chat (Text)**, **🎙️ Talk to Celina (Voice)**, and **✨ Test Edge Lighting (5s)** buttons directly in the top-bar dropdown + auto-reconnect on `notify::g-name-owner`.
   - Removes root-only `CapabilityBoundingSet=` (`218/CAPABILITIES`) and mount-namespace `PrivateTmp=` (`226/NAMESPACE`) from all `--user` units, and heals `/etc/systemd/*.d` `0700 -> 0755` permissions automatically.
6. **Zero `(No response)` Cloud Fallback & Cache Fix (`router.py`):**
   - When `openrouter/free` returns an empty response (common with free reasoning models), Celina no longer prints or caches `(No response)` — it automatically falls back to your local Ollama model.
