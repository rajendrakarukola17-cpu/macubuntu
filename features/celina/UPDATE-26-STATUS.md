# Celina Update 26 — Gear 0.5: smolLM Quantized — COMPLETE

## Status: Implementation complete, all verification passes

## What's New: Gear 0.5 Tier

A new compute tier **Gear 0.5** positioned between Gear 0 (0 tokens, 0 MB AI RAM) and Gear 1 (Ollama qwen3:1.7b). Uses smolLM quantized GGUF via llama.cpp — local, offline, sub-100ms, ~100-200MB RAM.

| Tier | Model | Tokens | AI RAM | Latency | Use Case |
|------|-------|--------|--------|---------|----------|
| Gear 0 | None (deterministic) | 0 | 0 MB | <20ms | OS/media/file/reflex commands |
| **Gear 0.5** | **smolLM 4-bit GGUF** | **~50-128** | **~100-200MB** | **<100ms** | **Brief Q&A, simple reasoning** |
| Gear 1 | Ollama qwen3:1.7b | ~200-2000 | ~1.8GB | 200ms-2s | General local chat |
| Gear 2 | Ollama heavy | ~500-4000 | ~2.5-4GB | 2s-10s | Complex reasoning |
| Tier 3 | Cloud (OpenRouter) | Unlimited | 0 MB local | 5s-30s | Opt-in cloud |

## Files Created

- `celina_pkg/gear0_5_smolLM.py` — smolLM interface via llama.cpp CLI subprocess
  - `SmolLMRunner` class — probes for binary + GGUF model, generates text
  - `gear0_5_smolLM_available()` — check availability
  - `gear0_5_smolLM_generate()` — generate text (returns reply, error)
  - `gear0_5_smolLM_status()` — status info for `/tier` and diagnostics
  - Graceful fallback: if llama.cpp binary or GGUF model missing, reports unavailable
  - Configurable model (smollm-117m default, also 360m, 1.7b), threads, ctx, max_tokens, temperature

## Files Modified

- `celina_pkg/config.py` — Added SMOLLM_MODEL, SMOLLM_BIN, SMOLLM_MODEL_PATH, SMOLLM_NUM_THREADS, SMOLLM_NUM_CTX, SMOLLM_MAX_TOKENS, SMOLLM_TEMPERATURE config vars; added `gear0_5` to COGNITION_CFG gears; added `get_smolLM_status()`
- `celina_pkg/router.py` — Added `gear0_5_smolLM` import; added `_tier0_5_local_smolLM()` method; inserted tier 0.5 into `ask()` dispatch chain between Gear 0 and Gear 1; updated `_gear_settings()` defaults; updated `/tier` command to accept `0.5`; updated `/help` text

## Dispatch Chain Order

```
Tier 0:  Gear 0 reflex (0 tokens, 0 MB)          → _tier0_dispatch()
Tier 0.5: Gear 0.5 smolLM quantized (~100-200MB) → _tier0_5_local_smolLM()  [NEW]
Tier 1:  Gear 1 Ollama fast (qwen3:1.7b)         → _tier1_local_fast()
Tier 2:  Gear 2 Ollama heavy                      → _tier2_local_heavy()
Tier 3:  Gear 3 Cloud (OpenRouter)                → _tier3_cloud()
```

When `tier=None` (auto), the chain tries 0 → 0.5 → 1 → 2 → 3 in order. When `tier=0.5`, only smolLM is attempted (falls back to error message if unavailable).

## Usage

```bash
# Set tier to 0.5 for this session
/tier 0.5

# Or pass on command line
celina --tier 0.5 "What is the capital of France?"

# Check smolLM status
# (smolLM availability shown in /tier output)

# Configure smolLM (env vars)
export CELINA_SMOLLM_MODEL=smollm-360m
export CELINA_SMOLLM_BIN=/path/to/llama.cpp/llama-cli
export CELINA_SMOLLM_MODEL_PATH=/path/to/smolLM-360M.Q4_K_M.gguf
export CELINA_SMOLLM_NUM_THREADS=4
export CELINA_SMOLLM_NUM_CTX=512
export CELINA_SMOLLM_MAX_TOKENS=128
export CELINA_SMOLLM_TEMPERATURE=0.3
```

## Hard Refusals Honored

- ✅ Not in Gear 0 dispatch chain (separate tier 0.5)
- ✅ No cloud connector
- ✅ No webhook server
- ✅ No exponential backoff/jitter
- ✅ No silent error swallowing (failures reported explicitly)
- ✅ 102-tool contract preserved (zero changes)
- ✅ Local-only, offline, zero external API calls

## Verification — All Green

| Check | Result |
|-------|--------|
| Groups A+B (`test_critical_a_b.py`) | 18/18 PASS |
| P0 regression tests | 17/17 PASS, exit 0 |
| Lazy import lint | 32/32 PASS, 0 violations (gear0_5_smolLM added) |
| CI gates (9/9) | ALL PASSED |
| All modules compile | router.py, config.py, gear0_5_smolLM.py compile clean |
| Functional test (smolLM available) | tier=0.5 → tier0_5_smolLM ✅ |
| Functional test (smolLM unavailable) | Graceful error message ✅ |
| Functional test (tier=0) | Reflex-only message ✅ |
| Functional test (tier=None auto) | Falls through correctly ✅ |

## Linear Tracking

- Update 26: Gear 0.5 smolLM quantized (Backlog)

## All Updates Summary

| Update | Status | Summary |
|--------|--------|---------|
| Update 18 | CLOSED | Task-aware routing, 12 cloud providers |
| Update 19 | CLOSED | Gear 0 modules (26) + 5 plugins |
| Update 20 | CLOSED | P0 safety fixes (B1–B9 evidence complete) |
| Update 21 | CLOSED | Latency truth — all targets met |
| Update 22 | CLOSED | Structure/routing — tier-based dispatch |
| Update 23 | CLOSED | Connectors — 5 app connectors |
| Update 24 | CLOSED | Rust FFI — optional, working |
| Update 25 | CLOSED | Dispatch chain optimization — 12.9ms → 0.11ms |
| Update 26 | CLOSED | Gear 0.5 smolLM quantized — between Gear 0 and Gear 1 |
