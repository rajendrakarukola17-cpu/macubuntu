# Celina Model Selection & Routing Architecture (v4.5, Update 27)

**Local catalog reference (not proof of artifact identity or provenance)** across Celina: Whisper STT, Ollama LLM, Edge0 GGUF, Gear 0.5 smolLM, cloud provider routing, and heavy-task decomposition.

File: `features/celina/celina_pkg/model_selector.py`

---

## 1. Overview

Every model decision is driven by detected system resources (RAM, disk, CPU cores, GPU, NPU) and can be overridden via environment variables or CLI commands. No model is downloaded automatically — the selector reports what SHOULD be installed; the installer/stages perform the actual download.

The system makes **five independent decisions**:

| Decision | Module | Factors |
|---|---|---|
| Whisper STT model | `select_whisper_model()` | RAM, language, accuracy, disk |
| Ollama LLM model | `select_ollama_model()` | RAM, GPU/NPU, vision, heavy tasks, disk |
| Edge0 GGUF model | `select_edge0_model()` | RAM, disk, edge0-infer binary |
| Gear 0.5 smolLM | inline | RAM, llama.cpp availability |
| Cloud routing | `decide_cloud_routing()` | Task complexity, domains, provider availability, RAM |

---

## 2. Whisper STT Model Selection

### Available Models

| Model | Size | RAM | Speed | Accuracy | Best For |
|---|---|---|---|---|---|
| `tiny` | ~39 MB | ~150 MB | ~10x realtime | Low | 8 GB RAM, wake-word + short commands |
| `base` | ~74 MB | ~300 MB | ~5x realtime | Medium | 8-16 GB RAM, general dictation (default) |
| `small` | ~244 MB | ~800 MB | ~2.5x realtime | High | 16 GB RAM, multilingual EN/TE/HI, meetings |
| `medium` | ~769 MB | ~2 GB | ~1.5x realtime | Very High | 24+ GB RAM, high-stakes transcription |
| `large-v3` | ~1550 MB | ~3.5 GB | ~0.7x realtime | Best | 32+ GB RAM, server-grade transcription |

### Selection Logic

1. **User override**: `CELINA_WHISPER_MODEL` env var (validated against known models)
2. **Accuracy preference**: `high`/`best` forces `small+`; default starts from `tiny`
3. **Language need**: EN/TE/HI multilingual requires at least `small` (removes `tiny`/`base`)
4. **RAM filter**: reserves ~1.5 GB for Celina core + TTS + VAD; picks first model that fits
5. **Fallback**: `tiny` if nothing else fits

### Installation

```bash
python3 -m venv ~/.cache/celina/venv
~/.cache/celina/venv/bin/pip install faster-whisper
# Model auto-downloads on first use (~39 MB to ~1.5 GB depending on selection)
```

### Example Decisions

| System | Language | Selected | Reason |
|---|---|---|---|
| 8 GB RAM, EN only | English | `base` | RAM=8 GB, 6.7 GB available → base fits |
| 8 GB RAM, EN+TE+HI | Multilingual | `small` | Multilingual removes tiny/base, small fits in 6.7 GB |
| 16 GB RAM, EN | English | `base` | Default balance; small also fits but base is faster |
| 4 GB RAM, EN | English | `tiny` | Only tiny fits after 1.5 GB reserve |

---

## 3. Ollama LLM Model Selection

### Catalog

| Tag | Arch | Total | Active | Size | Min RAM | Rec. RAM | Best For |
|---|---|---|---|---|---|---|---|
| `granite3.1-moe:1b` | Sparse MoE | 1B | 0.4B | 1.4 GB | 6 GB | 8 GB | 6-8 GB, lowest CPU/RAM |
| `qwen3:1.7b` | Dense | 1.7B | 1.7B | 1.4 GB | 6 GB | 8 GB | 8 GB, zero-lag default |
| `granite3.1-moe:3b` | Sparse MoE | 3B | 0.8B | 2.0 GB | 8 GB | 12 GB | 8-12 GB, tool-calling |
| `qwen2.5:3b` | Dense | 3B | 3B | 1.9 GB | 8 GB | 12 GB | 8-12 GB, reasoning |
| `qwen3:4b` | Dense | 4B | 4B | 2.5 GB | 11 GB | 16 GB | 12-16 GB, coding |
| `qwen3.5:4b` | Dense Vision | 4B | 4B | 3.5 GB | 8 GB | 12 GB | 8-16 GB, vision |
| `qwen3.5:9b` | Dense Vision | 9B | 9B | 6.6 GB | 14 GB | 20 GB | 16-24 GB, reasoning+vision |
| `qwen3:30b-a3b` | Sparse MoE | 30.5B | 3.3B | 19 GB | 24 GB | 32 GB | 24-32 GB, heavy reasoning |
| `gemma4:26b` | Sparse MoE Vision | 25.2B | 3.8B | 17 GB | 22 GB | 32 GB | 24-32 GB, vision+reasoning |
| `qwen3.5:35b-a3b` | Sparse MoE Vision | 34.7B | 3B | 22 GB | 28 GB | 40 GB | 32+ GB, maximum local |

### Selection Logic

1. **User override**: `CELINA_LOCAL_MODEL` env var (validated against catalog)
2. **RAM filter**: model's `min_ram_gb` must be <= detected RAM (with ~2 GB reserve for OS + Celina core)
3. **Vision preference**: if vision tasks expected, bonus to vision-capable models
4. **Heavy preference**: if heavy tasks expected, bonus to larger MoE models
5. **Sorting**: by capability (size desc) with vision/heavy bonuses
6. **Fallback**: `granite3.1-moe:1b` if nothing fits

### Hardware Notes

| Hardware | Effect |
|---|---|
| NPU (`/dev/accel/*`) | Routes to `celina-npu-inference.service:11435` (decoupled from Ollama) |
| GPU (`/dev/dri/renderD*`) | GPU-accelerated Ollama (CUDA/ROCm/Vulkan) |
| Neither | CPU-only Ollama |

### Installation

```bash
ollama pull <model-tag>
# Example: ollama pull qwen3:1.7b
```

### Example Decisions

| System | GPU | Vision | Heavy | Selected | Reason |
|---|---|---|---|---|---|
| 8 GB RAM | No | No | No | `granite3.1-moe:1b` or `qwen3:1.7b` | Fits in 6 GB available |
| 8 GB RAM | Yes | Yes | No | `qwen3.5:4b` | Vision-capable, fits in 6 GB |
| 16 GB RAM | No | No | Yes | `qwen3:4b` | Stronger reasoning, fits in 14 GB |
| 32 GB RAM | Yes | Yes | Yes | `gemma4:26b` or `qwen3:30b-a3b` | Maximum local capability |

---

## 4. Edge0 Model — Source & Installation

### What is Edge0?

Edge0 is a family of **Mixture-of-Experts (MoE)** models that run via the `edge0-infer` binary. They use **mmap streaming from SSD**, keeping active RAM very low (1.0-2.9 GB) even for 8B-35B parameter models.

### Available Models

| Model | Active RAM | Disk | Source | Best For |
|---|---|---|---|---|
| `edge0-8b` | ~1.0 GB | 4.2 GB | GitHub releases | 8 GB RAM, 24-25 tok/s |
| `edge0-35b-compressed` | ~1.8 GB | 23 GB | GitHub releases | 16 GB RAM, reasoning+coding |
| `edge0-35b-standard` | ~2.9 GB | 23 GB | GitHub releases | 24+ GB RAM, maximum local |

### Where It Comes From

Edge0 models are **NOT** Ollama models. They are downloaded from the Edge0 AI project GitHub releases as GGUF files:

```
https://github.com/edge0-ai/edge0-8b/releases
https://github.com/edge0-ai/edge0-35b/releases
```

### Installation Steps

```bash
# 1. Create model directory
mkdir -p ~/.cache/celina/models/edge0/

# 2. Download the GGUF with SHA-256 verification (example for Edge0-8B)
curl -L --fail --location -o ~/.cache/celina/models/edge0/edge0-8b.Q4_K_M.gguf \
  https://github.com/edge0-ai/edge0-8b/releases/download/v1.0/edge0-8b.Q4_K_M.gguf
echo "ed26eb39330fd296cd037b8229adccea0197b21989ec0a1ad4f4f74f5a41c7a7  ~/.cache/celina/models/edge0/edge0-8b.Q4_K_M.gguf" | sha256sum --check --strict

# 3. Install edge0-infer binary (from Edge0 project)
# Verify SHA-256 before executing; ensure edge0-infer is on PATH

# 4. Set environment variable
export CELINA_EDGE0_MODEL=edge0-8b
```

### Selection Logic

| RAM | Disk | Selected |
|---|---|---|
| <= 8 GB | >= 4.2 GB | `edge0-8b` |
| 8-16 GB | >= 23 GB | `edge0-35b-compressed` |
| 8-16 GB | < 23 GB | `edge0-8b` |
| >= 16 GB | >= 23 GB | `edge0-35b-standard` |
| >= 16 GB | < 23 GB | Step down to compressed, then 8B |

---

## 5. Gear 0.5 smolLM

### What It Is

UNVERIFIED: local model artifacts, source provenance, licenses, resource usage, and performance have not been independently validated. Catalog entries are configuration identifiers, not proof that a model exists or is installed. The default model maps to smollm-135m.Q4_K_M.gguf and digest selector CELINA_SMOLLM_135M_SHA256. A separately obtained GGUF and trusted pin are required. The protected config.py legacy path field is not the runner path unless explicitly overridden.

A **sub-200 MB intent classification layer** that sits between Gear 0 (0 tokens, 0 MB) and Gear 1 (Ollama qwen3:1.7b). Uses `llama.cpp` CLI with a quantized GGUF model.

| Model | Active RAM | Source | Purpose |
|---|---|---|---|
| `SmolLM2-135M-Instruct` | ~100 MB | `~/.celina/models/smollm-135m.Q4_K_M.gguf` | 8 GB RAM, sub-100ms intent classification |
| `smollm-360m` | ~150 MB | `~/.cache/celina/models/smolLM/smollm-360m.gguf` | 16 GB RAM, better NLU |
| `smollm-1.7b` | ~700 MB | `~/.cache/celina/models/smolLM/smollm-1.7b.gguf` | 24+ GB RAM, complex routing |

### Installation

```bash
mkdir -p ~/.cache/celina/models/smolLM/
# Obtain a compatible GGUF through an independently verified source and pin its digest
# Install llama.cpp (provides llama-cli)
export CELINA_SMOLLM_MODEL=SmolLM2-135M-Instruct
```

---

## 6. Cloud Provider Routing

### Providers

| Provider | Endpoint | Default Model | Best For |
|---|---|---|---|
| Groq | api.groq.com | llama-3.3-70b-versatile | Fast inference |
| Gemini | generativelanguage.googleapis.com | gemini-2.5-flash | Creative, multilingual, long context |
| NVIDIA | integrate.api.nvidia.com | meta/llama-3.3-70b-instruct | Math, reasoning |
| Mistral | api.mistral.ai | mistral-small-latest | Code, general |
| Cerebras | api.cerebras.ai | llama-3.3-70b | Ultra-fast inference |
| OpenRouter | openrouter.ai | openrouter/free | General, free tier |
| xAI | api.x.ai | grok-3-mini | Math, reasoning |
| GLM | open.bigmodel.cn | glm-4 | Multilingual (CN/EN) |
| DeepSeek | api.deepseek.com | deepseek-chat | Code, math |
| Together | api.together.xyz | meta-llama/Llama-3.1-8b-Instruct-Turbo | General |
| Perplexity | api.perplexity.ai | sonar-small | Search, web |

### Routing Strategies

| Strategy | When Used | Behavior |
|---|---|---|
| **Failover** | Simple/moderate tasks | Try primary → next on failure |
| **Hybrid** | Complex tasks | Primary + parallel backups, reassemble locally |
| **Parallel** | Heavy multi-domain tasks | All providers run simultaneously, best result wins |

### Task Complexity Classification

| Complexity | Word Count | Domains | Strategy |
|---|---|---|---|
| Simple | < 10 | 0-1 | Failover |
| Moderate | 10-24 | 1 | Failover |
| Complex | 25-59 | 2-3 | Hybrid |
| Heavy | >= 60 or >= 3 domains | 3+ | Parallel |

### Provider Groups by Domain

| Domain | Providers |
|---|---|
| Code | deepseek, cerebras, groq, mistral |
| Math | xai, nvidia, groq, gemini |
| Creative | gemini, groq, mistral |
| Search | perplexity, openrouter, gemini |
| Multilingual | glm, gemini, groq |
| Vision | gemini, glm |
| General | groq, cerebras, gemini, mistral, openrouter |

### Heavy Task Division

For heavy tasks (>= 3 domains or >= 60 words), work is divided among providers:

```
Task: "Write a Python script to analyze sales data, create a chart, and write a blog post about the findings"

Split:
  ├── deepseek:  → "Write the Python script" (code domain)
  ├── gemini:    → "Write the blog post" (creative domain)
  ├── perplexity:→ "Find current sales data" (search domain)
  └── Local merge: Reassemble all results into final answer
```

### Parallel vs Fallback Decision

| Condition | Decision |
|---|---|
| Simple task | Failover (single provider) |
| Complex task | Hybrid (primary + backups) |
| Heavy task | Parallel (all domain specialists) |
| RAM <= 8 GB | Downgrade Parallel → Hybrid (less context to manage) |
| User prefers parallel | Force Hybrid or Parallel |

### Cloud Escalation & DLP PII Redaction Gate (Mandatory Opt-In)

Cloud routing is **disabled by default** (`cloud_escalation=false`). No task text is ever sent
to a cloud provider unless explicitly opted in:

1. **`cloud_escalation=true`** must be set in Celina config or via `--cloud-escalation` CLI flag.
2. **DLP PII redaction** is applied automatically before any cloud call: names, emails, phones,
   addresses, SSNs, credit-card numbers, API keys, and passwords are replaced with `[REDACTED]` tokens
   using `celina_pkg.dlp.redact_pii()`.
3. **Audit log**: every cloud escalation is recorded in `~/.local/share/celina/evidence/ledger.jsonl`
   with the provider, task domain, redacted token count, and SHA-256 of the original (never the plaintext).
4. **Zero cloud by default**: All heavy tasks run locally via Ollama qwen3:4b (16 GB) or
   qwen3:1.7b (8 GB) with `serialize_heavy_models=true`.

### Installation

No local installation needed — cloud providers require only API keys.
**NEVER** store plaintext API keys in `~/.ai-env` (Threat T4: plaintext secrets on disk).

**Preferred**: Store secrets in GNOME Keyring via `libsecret`:
```bash
# Store via secret-tool (libsecret)
secret-tool store --label="Groq API Key" service=groq attribute=api_key
secret-tool store --label="Gemini API Key" service=gemini attribute=api_key

# Retrieve in code via secret-tool lookup
GROQ_API_KEY=$(secret-tool lookup service=groq attribute=api_key)
```

**Headless fallback** (no GNOME Keyring): `~/.ai-env` mode `0600` only, never committed to version control:
```bash
# ~/.ai-env  (mode 0600, owner-only readable)
GROQ_API_KEY=your_key
GEMINI_API_KEY=your_key
DEEPSEEK_API_KEY=your_key
```

---

## 7. Full Model Decision Flow

```
make_full_model_decision(goal, providers)
    │
    ├── detect system resources (RAM, disk, CPU, GPU, NPU)
    │
    ├── select_whisper_model()          → whisper decision
    ├── select_ollama_model()           → ollama decision
    ├── select_edge0_model()            → edge0 decision
    ├── gear 0.5 smolLM decision        → smolLM decision
    │
    ├── decide_cloud_routing(goal)      → cloud routing decision
    │     ├── classify_task_complexity()
    │     ├── select_providers_for_task()
    │     └── execution plan
    │
    └── return FullModelDecision
```

### CLI Usage

```bash
# See current model decisions
celina --model-decision

# See with a specific task goal
celina --model-decision --goal "write a python script to analyze data"

# Override whisper model
CELINA_WHISPER_MODEL=small celina --model-decision

# Override ollama model
CELINA_LOCAL_MODEL=qwen3:4b celina --model-decision
```

### Environment Variables

| Variable | Purpose | Example |
|---|---|---|
| `CELINA_WHISPER_MODEL` | Force whisper model | `CELINA_WHISPER_MODEL=small` |
| `CELINA_LOCAL_MODEL` | Force ollama model | `CELINA_LOCAL_MODEL=qwen3:4b` |
| `CELINA_EDGE0_MODEL` | Force edge0 model | `CELINA_EDGE0_MODEL=edge0-8b` |
| `CELINA_SMOLLM_MODEL` | Force smolLM model | `CELINA_SMOLLM_MODEL=smollm-360m` |
| `GROQ_API_KEY` | Groq cloud API key | `GROQ_API_KEY=...` |
| `GEMINI_API_KEY` | Gemini cloud API key | `GEMINI_API_KEY=...` |

---

## 8. Integration with Existing Code

### config.py Integration

The `model_selector.py` is designed to integrate with `config.py`:

- `config.WHISPER_MODEL` → can be set from `select_whisper_model()["model"]`
- `config.LOCAL_MODEL` → can be set from `select_ollama_model()["model"]`
- `config.SMOLLM_MODEL` → used by gear0_5_smolLM.py
- `config.OLLAMA_API_BASE` → used by compute.py for endpoint resolution

### router.py Integration

Cloud routing results feed into `router.py`'s `_run_cloud_agent()`:

- Primary provider → first attempt
- Parallel providers → `_run_ensemble()` for multi-provider execution
- Fallback list → `provider_router.candidates()` for failover
- Task split → domain-specific provider assignment

### compute.py Integration

- GPU/NPU detection → `detect_compute_topology()`
- Endpoint resolution → `resolve_inference_endpoint()` (NPU 11435 → Ollama 11434 fallback)
- Model selection informs which Ollama model to pull and run

---

## 9. Example: Complete 8 GB RAM System

**System**: 8 GB RAM, 4 CPU cores, no GPU, no NPU, 50 GB disk

```
Whisper:     base (~74 MB, 300 MB RAM) — general dictation
Ollama:      qwen3:1.7b (1.4 GB, 6 GB min RAM) — zero-lag default
Edge0:       edge0-8b (~1 GB active, 4.2 GB disk) — if edge0-infer available
smolLM:      SmolLM2-135M-Instruct (~100 MB) — Gear 0.5 intent classification
Cloud:       Failover strategy (simple/moderate tasks default)
             Primary: groq → Fallback: gemini, deepseek, openrouter
```

**Install commands:**
```bash
python3 -m venv ~/.cache/celina/venv
~/.cache/celina/venv/bin/pip install faster-whisper          # downloads 'base' model on first use
ollama pull qwen3:1.7b             # 1.4 GB download
# Edge0: download GGUF from GitHub, install edge0-infer
# Cloud: set API keys in ~/.ai-env
```

---

## 10. Example: Complete 32 GB RAM System

**System**: 32 GB RAM, 16 CPU cores, NVIDIA GPU, 500 GB disk

```
Whisper:     small (~244 MB, 800 MB RAM) — multilingual EN/TE/HI
Ollama:      gemma4:26b (17 GB, 22 GB min RAM) — vision + reasoning
Edge0:       edge0-35b-standard (~2.9 GB active, 23 GB disk) — maximum local
smolLM:      smollm-1.7b (~700 MB) — Gear 0.5 complex routing
Cloud:       Parallel strategy (heavy tasks)
             Primary: groq → Parallel: deepseek, gemini, nvidia → Fallback: all
```

---

**End of document** — v4.5, Update 27