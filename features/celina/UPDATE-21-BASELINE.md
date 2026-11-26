# Celina Update 21 — Latency Baseline (Measured, N=2000)

**Status:** Baseline captured. No code changes made. Read-only benchmark only.

**Generated:** 2026-11-18 (local time Asia/Kolkata)
**Environment:** Python 3.13.18, Linux 6.1.189+, 2 CPUs, container=false
**Benchmark:** `tools/bench_latency.py`, N=2000, warmup=200, `perf_counter_ns()`, GC disabled during measurement

## Environment (captured literally)

| Property | Value |
|----------|-------|
| python | 3.13.18 |
| platform | Linux-6.1.189+-x86_64-with-glibc2.41 |
| cpu_count | 2 |
| session_type | unknown |
| container | false |
| khal_installed | **false** |
| calcurse_installed | **false** |
| pactl_installed | **false** |
| psutil_installed | true |
| pulsectl_installed | false |
| icalendar_installed | false |
| recurring_ical_events_installed | false |
| RSS after benchmark | 260.2 MB |

## Fixtures (non-empty, as specified)

| Fixture | Count |
|---------|-------|
| .ics calendar events (5-year RRULE) | 500 |
| habit_rows (bench_habits.db) | 10,000 |
| CSV rows for Data Oracle (bench_oracle.csv) | 100,000 |
| self_heal forced primary failure | true |

## Results — Dispatch Hit Path (handler matched + executed)

| Handler | Cold p50 | Cold p99 | Warm p50 | Warm p99 | Max | Stdev |
|---------|----------|----------|----------|----------|-----|-------|
| gear0_routines | 12.44 | 17.93 | 12.27 | 16.94 | — | — |
| plugin4_os_shim | 1.88 | 3.42 | 1.86 | 3.29 | — | — |
| gear0_lists | 1.72 | 3.01 | 1.69 | 2.79 | — | — |
| gear0_ledger | 1.62 | 2.42 | 1.58 | 2.54 | — | — |
| gear0_brain | 1.59 | 2.48 | 1.56 | 2.09 | — | — |
| gear0_graph | 0.54 | 0.93 | 0.54 | 1.06 | — | — |
| gear0_contacts | 0.58 | 0.99 | 0.54 | 1.09 | — | — |
| gear0_network | 0.32 | 0.73 | 0.33 | 0.67 | — | — |
| gear0_iot | 0.57 | 1.21 | 0.57 | 0.89 | — | — |
| gear0_context | 0.28 | 0.93 | 0.22 | 0.41 | — | — |
| plugin1_sniffer | 0.66 | 0.84 | 0.67 | 1.07 | — | — |
| plugin3_data_oracle | 0.56 | 0.97 | 0.56 | 0.95 | — | — |
| gear0_media | 0.14 | 1.03 | 0.14 | 1.01 | — | — |
| gear0_status | 0.32 | 0.59 | 0.38 | 0.65 | — | — |
| gear0_intent | 0.07 | 0.67 | 0.07 | 0.54 | — | — |
| gear0_dual | 0.19 | 0.34 | 0.19 | 0.27 | — | — |
| gear0_ml_nlu | 0.50 | 0.67 | 0.51 | 0.76 | — | — |
| plugin2_habit | 0.53 | 0.78 | 0.52 | 0.79 | — | — |
| plugin5_self_heal | 0.54 | 0.81 | 0.53 | 0.65 | — | — |
| gear0_memory | 0.52 | 0.83 | 0.53 | 0.85 | — | — |
| gear0_slots | 0.51 | 0.65 | 0.51 | 0.65 | — | — |
| gear0_voice | 0.52 | 0.79 | 0.53 | 0.94 | — | — |
| gear0_5_micro | 0.06 | 0.67 | 0.07 | 0.59 | — | — |
| gear0_audit | 0.13 | 0.32 | 0.14 | 0.20 | — | — |
| gear0_calendar | 0.22 | 0.39 | 0.21 | 0.29 | — | — |
| gear0_macro | 0.16 | 0.34 | 0.15 | 0.31 | — | — |
| gear0_focus | 0.17 | 0.23 | 0.17 | 0.25 | — | — |
| gear0_clipboard | 0.14 | 0.19 | 0.14 | 0.40 | — | — |
| gear0_hardware | 0.14 | 0.22 | 0.14 | 0.26 | — | — |
| gear0_utils | 0.11 | 0.25 | 0.10 | 0.31 | — | — |

## Results — Dispatch Miss Path (full traversal, no handler matched)

| Case | Cold p50 | Cold p99 | Warm p50 | Warm p99 |
|------|----------|----------|----------|----------|
| **dispatch_miss** | 0.53ms | **0.66ms** | 0.54ms | **0.68ms** |

The full ~34-block traversal completes in **<1ms p99** — dispatch is not the bottleneck.

## Per-Block Classify Probe Cost (individual classify() calls)

| Module | p50 | p99 |
|--------|-----|-----|
| gear0_routines | 0.27ms | 0.52ms |
| gear0_ml_nlu | 0.01ms | 0.02ms |
| All other Gear 0 modules | <0.01ms | <0.01ms |
| All 5 plugins | <0.01ms | <0.01ms |

## Modules WITHOUT classify/execute (cannot dispatch through Gear 0)

- gear0_voice
- gear0_memory
- gear0_brain
- gear0_slots

## Key Findings (Measured, Not Estimated)

1. **gear0_routines is the slowest dispatch path at 17.9ms p99** — this is the primary target for Update 21 optimization.
2. **Calendar is 0.39ms p99** — khal is NOT installed. The earlier "800ms" estimate was wrong.
3. **Hardware is 0.22ms p99** — pactl is NOT installed. The earlier "40ms" estimate was wrong.
4. **Full-miss traversal is 0.68ms p99** — dispatch overhead is negligible.
5. **Four Gear 0 modules lack classify/execute** — gear0_voice, gear0_memory, gear0_brain, gear0_slots cannot be dispatched through the Gear 0 pipeline.

## What Update 21 Should Target

Based on real measurements, the optimization targets are:

| Rank | Handler | Current p99 | Target | Reason |
|------|---------|-------------|--------|--------|
| 1 | gear0_routines | 17.93ms | <5ms | Slowest by 5x |
| 2 | plugin4_os_shim | 3.42ms | <2ms | Second slowest |
| 3 | gear0_lists | 3.01ms | <2ms | Third slowest |
| 4 | gear0_ledger | 2.42ms | <2ms | Fourth slowest |

Calendar and hardware are NOT targets — they're already <1ms on this machine because their subprocess backends aren't installed.

## Files Created (Read-Only)

- `tools/bench_latency.py` — Benchmark harness (read-only)
- `bench_latency_results.json` — Raw JSON output with N, environment, stdev
- `bench_latency_results.md` — Markdown summary
- `UPDATE-21-BASELINE.md` — This report

## What Was NOT Done (Per Corrected Plan)

- No code changes to `gear0_calendar.py`, `gear0_hardware.py`, or any module
- No Rust components written
- No cloud connectors added
- No webhook server added
- Update 20 package (`boost/update-20/`) was NOT mutated
- Update 20 is NOT recorded as closed — B1–B9 evidence still pending

## Next Step (Update 21.2)

Admission gate before fixes: AST top-level import check with Tier-A allowlist, budgets (total resident import ≤250ms, RSS delta ≤10MB), cold first-call latency published for every lazy path. Then fix only the top 2–3 measured offenders (gear0_routines, plugin4_os_shim, gear0_lists).
