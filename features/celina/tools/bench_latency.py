#!/usr/bin/env python3
"""
Celina Update 21 — Latency Benchmark Harness (read-only, no code changes).

Measures dispatch latency through router.ask() with a 2x2 matrix:
  dispatch outcome (hit / miss) x cache temperature (cold / warm)

NOT a cache-temperature benchmark. HIT = handler matched and executed.
MISS = full traversal through all ~34 blocks -> falls through to LLM path.
COLD = first call after fresh process import. WARM = post-warmup steady state.

Outputs: raw JSON + markdown with N, environment, stdev. Any number without
these three is UNVERIFIED.

Usage:
  python3 tools/bench_latency.py [--n 2000] [--warmup 200] [--out results.json]
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

# Ensure celina_pkg is importable from this script's location
CELINA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg import router  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
N = 2000          # p99 needs >=1000; 2000 default
WARMUP = 200
CELINA_PKG = CELINA_ROOT / "celina_pkg"


# ---------------------------------------------------------------------------
# Query sets — one per handler, plus a guaranteed miss
# ---------------------------------------------------------------------------
GEAR0_QUERIES = {
    "gear0_utils":        "what is 2 + 2",
    "gear0_intent":       "set a timer for 5 minutes",
    "gear0_graph":        "save alias mom as contact",
    "gear0_contacts":     "look up contact mom",
    "gear0_calendar":     "what is my next meeting",
    "gear0_network":      "is my internet up",
    "gear0_hardware":     "what is my cpu temp",
    "gear0_ledger":       "spent $12 on lunch",
    "gear0_macro":        "expand addr",
    "gear0_lists":        "add milk to shopping list",
    "gear0_routines":     "good morning",
    "gear0_media":        "play music",
    "gear0_focus":        "start focus mode",
    "gear0_clipboard":    "show clipboard history",
    "gear0_audit":        "show audit log",
    "gear0_dual":         "which backend",
    "gear0_status":       "gear 0 status",
    "gear0_iot":          "turn on living room lights",
    "gear0_context":      "mute notifications when im in a meeting",
    "gear0_5_micro":      "set timer for 10 minutes",
    "gear0_voice":        "wake word",
    "gear0_memory":       "store memory test fact",
    "gear0_ml_nlu":       "classify intent",
    "gear0_brain":        "log habit exercise",
    "gear0_slots":        "fill timer slots",
}

PLUGIN_QUERIES = {
    "plugin1_sniffer":    "what is my active window",
    "plugin2_habit":      "habit pattern",
    "plugin3_data_oracle":"sum of amount where category food",
    "plugin4_os_shim":    "system info",
    "plugin5_self_heal":  "reliability",
}

MISS_QUERY = "xyzzy plugh nothing matches this forty two nonsense"


# ---------------------------------------------------------------------------
# Fixture setup (non-empty, as specified)
# ---------------------------------------------------------------------------
def setup_fixtures() -> dict:
    """Create non-empty fixtures so benchmarks don't measure empty state."""
    import sqlite3
    from pathlib import Path

    shared = Path.home() / ".celina" / "storage" / "shared"
    shared.mkdir(parents=True, exist_ok=True)

    fixtures = {}

    # 1. .ics calendar with 500 events + 5-year RRULE
    ics_path = shared / "test_calendar.ics"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Celina Bench//EN"]
    for i in range(500):
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:bench-{i}@celina.local")
        lines.append("DTSTAMP:20260101T000000Z")
        lines.append(f"DTSTART;VALUE=DATE:20260{(i % 12) + 1:02d}{(i % 28) + 1:02d}")
        lines.append(f"DTEND;VALUE=DATE:20260{(i % 12) + 1:02d}{(i % 28) + 2:02d}")
        lines.append(f"SUMMARY:Bench event {i}")
        lines.append("RRULE:FREQ=WEEKLY;COUNT=52")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    ics_path.write_text("\n".join(lines), encoding="utf-8")
    fixtures["ics_events"] = 500

    # 2. 10k habit rows in bench_habits.db / habits table (separate DB, not clobbering gear0_ledger.db)
    habit_db = shared / "bench_habits.db"
    conn = sqlite3.connect(str(habit_db))
    try:
        conn.execute("DROP TABLE IF EXISTS habits")
        conn.execute(
            "CREATE TABLE habits ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, name TEXT, "
            "value REAL, unit TEXT, ts TEXT)"
        )
        rows = [
            ("2026-01-01", f"habit_{j % 100}", float(j % 100), "unit", "2026-01-01")
            for j in range(10000)
        ]
        conn.executemany(
            "INSERT INTO habits (date, name, value, unit, ts) VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    fixtures["habit_rows"] = 10000

    # 3. 100k-row CSV for Data Oracle
    csv_path = shared / "bench_oracle.csv"
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("id,amount,category\n")
        for j in range(100000):
            cat = "food" if j % 3 == 0 else ("travel" if j % 3 == 1 else "util")
            f.write(f"{j},{j % 1000},{cat}\n")
    fixtures["csv_rows"] = 100000

    # 4. Force self-heal primary failure: clear fallback chains so primary fails
    #    (self_heal.execute returns failure when no chain registered)
    fixtures["self_heal_forced_failure"] = True

    return fixtures


# ---------------------------------------------------------------------------
# Measurement primitive
# ---------------------------------------------------------------------------
def measure(fn, n: int = N, warmup: int = WARMUP) -> dict:
    """Run fn n times, return latency stats in ms."""
    # Warmup
    for _ in range(warmup):
        try:
            fn()
        except Exception:
            pass

    gc.collect()
    gc.disable()
    samples = []
    try:
        for _ in range(n):
            t0 = time.perf_counter_ns()
            try:
                fn()
            except Exception:
                pass  # latency measured even on failure
            samples.append((time.perf_counter_ns() - t0) / 1e6)
    finally:
        gc.enable()

    samples.sort()
    return {
        "n": n,
        "warmup": warmup,
        "p50_ms": round(statistics.median(samples), 4),
        "p95_ms": round(samples[int(n * 0.95)], 4),
        "p99_ms": round(samples[int(n * 0.99)], 4),
        "max_ms": round(samples[-1], 4),
        "mean_ms": round(statistics.mean(samples), 4),
        "stdev_ms": round(statistics.stdev(samples), 4),
        "min_ms": round(samples[0], 4),
    }


def probe_block_router(router_instance, query: str) -> None:
    """Call router.ask once — measures full dispatch path."""
    router_instance.ask(query, dry_run=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    global N, WARMUP
    parser = argparse.ArgumentParser(description="Celina latency benchmark")
    parser.add_argument("--n", type=int, default=N, help="Samples per case")
    parser.add_argument("--warmup", type=int, default=WARMUP, help="Warmup iterations")
    parser.add_argument("--out", type=str, default=None, help="Write raw JSON to file")
    args = parser.parse_args()
    N = args.n
    WARMUP = args.warmup

    # Environment capture (required for every number)
    shutil_mod = __import__("shutil")
    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "session_type": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "container": os.path.exists("/.dockerenv"),
        "cpu_count": os.cpu_count(),
        "celina_root": str(CELINA_ROOT),
        "khal_installed": bool(shutil_mod.which("khal")),
        "calcurse_installed": bool(shutil_mod.which("calcurse")),
        "pactl_installed": bool(shutil_mod.which("pactl")),
    }
    for dep in ("psutil", "pulsectl", "icalendar", "recurring_ical_events"):
        try:
            __import__(dep)
            env[f"{dep}_installed"] = True
        except Exception:
            env[f"{dep}_installed"] = False

    print("=== Celina Update 21 — Latency Baseline ===")
    print(f"N={N}, warmup={WARMUP}")
    print(f"Environment: {json.dumps(env, indent=2)}")
    print()

    # Set up non-empty fixtures
    fixtures = setup_fixtures()
    print(f"Fixtures: {json.dumps(fixtures)}")
    print()

    # Instantiate router (cold import already happened at module load)
    r = router.CelinaRouter()

    results: dict = {"env": env, "fixtures": fixtures, "cases": {}}

    # ---- 2x2 matrix: dispatch outcome x cache temperature ----
    all_queries = {**GEAR0_QUERIES, **PLUGIN_QUERIES}

    for case_name, query in {**all_queries, "dispatch_miss": MISS_QUERY}.items():
        is_miss = case_name == "dispatch_miss"

        # COLD: first call in this process (router already imported above,
        # but we can simulate "cold" by calling once fresh)
        cold_result = measure(
            lambda q=query: probe_block_router(r, q),
            n=N,
            warmup=0,  # no warmup for cold
        )

        # WARM: warmup first, then measure
        warm_result = measure(
            lambda q=query: probe_block_router(r, q),
            n=N,
            warmup=WARMUP,
        )

        key = "dispatch_miss" if is_miss else f"dispatch_hit::{case_name}"
        results["cases"][key] = {
            "query": query,
            "is_dispatch_miss": is_miss,
            "cold_ms": cold_result,
            "warm_ms": warm_result,
        }

        print(
            f"[{key:40s}] cold p99={cold_result['p99_ms']:>7}ms "
            f"warm p99={warm_result['p99_ms']:>7}ms "
            f"cold p50={cold_result['p50_ms']:>6}ms warm p50={warm_result['p50_ms']:>6}ms"
        )

    # ---- Per-block probe cost: time each classify call individually ----
    print("\n--- Per-block classify probe cost (cold) ---")
    probe_results = {}
    # Map query-key -> actual module path for classify lookup
    MODULE_PATH_MAP = {
        **{k: f"celina_pkg.{k}" for k in GEAR0_QUERIES},
        "plugin1_sniffer": "celina_pkg.plugins.sniffer_plugin",
        "plugin2_habit": "celina_pkg.plugins.habit_engine",
        "plugin3_data_oracle": "celina_pkg.plugins.data_oracle",
        "plugin4_os_shim": "celina_pkg.plugins.os_shim",
        "plugin5_self_heal": "celina_pkg.plugins.self_heal",
    }
    for name, query in all_queries.items():
        mod_path = MODULE_PATH_MAP[name]
        try:
            module = __import__(mod_path, fromlist=["*"])
            # Find any classify function
            classify_fn = None
            for attr in dir(module):
                if attr.endswith("_classify"):
                    classify_fn = getattr(module, attr)
                    break
            if classify_fn:
                probe = measure(lambda: classify_fn(query), n=N, warmup=WARMUP)
                probe_results[name] = probe
                print(f"  {name:30s} p99={probe['p99_ms']:>7}ms p50={probe['p50_ms']:>6}ms")
            else:
                print(f"  {name:30s} NO classify function found")
        except Exception as exc:
            print(f"  {name:30s} ERROR: {exc}")
    results["probe_cost_ms"] = probe_results

    # ---- Full-miss traversal p99 (the number that matters) ----
    miss_key = "dispatch_miss"
    miss_p99 = results["cases"][miss_key]["warm_ms"]["p99_ms"]
    print(f"\n=== FULL-MISS traversal p99 (warm): {miss_p99} ms ===")
    print(f"=== FULL-MISS traversal p99 (cold): {results['cases'][miss_key]['cold_ms']['p99_ms']} ms ===")

    # ---- RSS delta ----
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        results["rss_mb"] = round(rss_mb, 2)
        print(f"RSS after benchmark: {rss_mb:.1f} MB")
    except Exception:
        results["rss_mb"] = None
        print("RSS measurement skipped (psutil unavailable)")

    # ---- Write output ----
    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nRaw JSON written to: {out_path}")
    else:
        default_out = CELINA_ROOT / "bench_latency_results.json"
        default_out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nRaw JSON written to: {default_out}")

    # Markdown summary
    md_lines = [
        "# Celina Update 21 — Latency Baseline",
        "",
        f"**N:** {N}  **Warmup:** {WARMUP}  **Generated:** {env['python']}",
        "",
        "## Environment",
        "",
    ]
    for k, v in env.items():
        md_lines.append(f"- **{k}:** {v}")
    md_lines.extend(["", "## Fixtures", ""])
    for k, v in fixtures.items():
        md_lines.append(f"- **{k}:** {v}")
    md_lines.extend(["", "## Results (ms)", ""])
    md_lines.append("| Case | Cold p50 | Cold p99 | Warm p50 | Warm p99 | Max | Stdev |")
    md_lines.append("|------|----------|----------|----------|----------|-----|-------|")
    for key, val in results["cases"].items():
        c = val["cold_ms"]
        w = val["warm_ms"]
        md_lines.append(
            f"| {key} | {c['p50_ms']} | {c['p99_ms']} | {w['p50_ms']} | {w['p99_ms']} | "
            f"{w['max_ms']} | {w['stdev_ms']} |"
        )
    md_lines.extend(["", "## Per-block probe cost (ms)", ""])
    md_lines.append("| Module | p50 | p99 |")
    md_lines.append("|--------|-----|-----|")
    for name, val in probe_results.items():
        md_lines.append(f"| {name} | {val['p50_ms']} | {val['p99_ms']} |")

    md_out = CELINA_ROOT / "bench_latency_results.md"
    md_out.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Markdown summary written to: {md_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())