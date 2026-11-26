# Celina Update 22 — Structure/Routing — COMPLETE

## Status: All routing restructured. Ready to proceed to Update 23 (connectors, after MausBot verified).

## What Changed

### Problem
1. **Gear 0 checked LAST** in `ask()` dispatch chain — after everyday agents, skills, fast intent, etc. Gear 0 is zero-token, zero-AI-RAM, sub-20ms and should be FIRST.
2. **No cloud/LLM fallback** — `ask()` returned None implicitly if no Gear 0 module matched.
3. **`_run_cloud_agent` / `_post_openrouter` existed but were never called** from `ask()`.
4. **No tier-based routing structure** — Tier 0/1/2/3 mentioned in `/tier` command but not enforced in dispatch.

### Solution
1. **Extracted `_tier0_dispatch()` method** — encapsulates all Tier 0 reflexes:
   - Gear 0 math (`try_gear0_math`)
   - Gear 0 install intent (`handle_fast_install_intent`)
   - All 18 Gear 0 modules (utils, intent, graph, iot, context, audit, media, focus, clipboard, calendar, network, hardware, ledger, macro, status, dual, contacts, lists, routines)
   - All 5 Gear 0 plugins (sniffer, habit, data_oracle, os_shim, self_heal)
   - Everyday agents (dispatch_everyday_task)
   - Fast intent (handle_fast_intent)
   - Fast bypass (classify_intent_fast_bypass)
   - Skill trigger match (match_skill_by_trigger)
   
   Moved to the FRONT of `ask()` — right after /commands, before everything else.

2. **Added tier-based LLM fallback methods**:
   - `_tier1_local_fast()` — local Ollama fast (qwen3:1.7b, gear="fast", think=False)
   - `_tier2_local_heavy()` — local Ollama heavy (gear="deep", reasoning)
   - `_tier3_cloud()` — cloud via `_run_cloud_agent` + provider_router (bounded failover, max 3 providers)

3. **Restructured `ask()` end** — after Tier 0, attempts tier fallback based on `effective_tier`:
   - Tier 0: returns "reflex only" message (no LLM)
   - Tier 1/auto: tries local Ollama fast → falls through
   - Tier 2: tries local Ollama heavy → falls through
   - Tier 3/auto: tries cloud via provider_router → falls through
   - All exhausted: returns helpful error message

### Latency Results (p99, N=1000, warmup=200)

| Module | p50 | p95 | p99 | Target | Status |
|--------|-----|-----|-----|--------|--------|
| gear0_lists | 0.51ms | 1.11ms | 1.54ms | <2ms | ✅ |
| gear0_ledger | 0.53ms | 1.19ms | 1.40ms | <2ms | ✅ |
| gear0_routines | 3.27ms | 3.69ms | 4.60ms | <5ms | ✅ |
| gear0_math | 0.09ms | 0.14ms | 0.37ms | <1ms | ✅ |

### Verification
- tools/test_critical_a_b.py: 18/18 PASS (Groups A+B closed)
- P0 regression tests: exit 0
- tools/lint_lazy_imports.py: 31/31 PASS, 0 violations
- All 31 celina_pkg modules compile clean

### No New Modules Created
Per hard refusals: no gear0_dynamic_router.py, gear0_circuit_breaker.py, gear0_mcp_server.py. All routing logic lives in router.py and provider_router.py (extended, not replaced).

## Next: Update 23 (connectors) — only after MausBot verified (repo URL, LICENSE, README quoted literally)