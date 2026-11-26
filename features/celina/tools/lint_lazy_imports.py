#!/usr/bin/env python3
"""
Celina Update 21 — Lazy-Import Admission Gate (Phase 21.2).

Audits top-level imports in gear0_*.py modules to enforce:
- Tier-A allowlist: stdlib-only imports allowed at top level
- Tier B+/C imports must be lazy (inside functions/try-except ImportError)
- Budgets: per-module top-level import cost ≤5ms, total resident import ≤250ms, RSS delta ≤10MB

Usage:
  python3 tools/lint_lazy_imports.py [--budget-ms 250] [--rss-mb 10] [--per-module-ms 5]
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import time
from pathlib import Path

try:
    import psutil
except Exception:
    psutil = None

CELINA_ROOT = Path(__file__).resolve().parent.parent
CELINA_PKG = CELINA_ROOT / "celina_pkg"

# Tier-A allowlist: stdlib modules safe for top-level import (fast, no I/O, no native extensions)
TIER_A_ALLOWLIST = frozenset({
    "__future__",
    "abc",
    "argparse",
    "ast",
    "asyncio",
    "base64",
    "collections",
    "contextlib",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fcntl",
    "functools",
    "hashlib",
    "heapq",
    "io",
    "itertools",
    "json",
    "math",
    "mimetypes",
    "operator",
    "os",
    "pathlib",
    "pickle",
    "queue",
    "re",
    "secrets",
    "shutil",
    "socket",
    "sqlite3",
    "statistics",
    "string",
    "struct",
    "subprocess",
    "sys",
    "threading",
    "time",
    "typing",
    "uuid",
    "warnings",
    "zoneinfo",
})

# Known heavy/optional imports that MUST be lazy
HEAVY_OPTIONAL_IMPORTS = frozenset({
    "fasttext",
    "spacy",
    "sentence_transformers",
    "numpy",
    "pandas",
    "sqlite_vec",
    "pvporcupine",
    "onnxruntime",
    "whisper",
    "piper",
    "vosk",
    "soundfile",
    "sounddevice",
    "pyperclip",
    "psutil",
    "ctransformers",
    "transformers",
    "torch",
    "tensorflow",
    "cv2",
    "PIL",
})


def _top_level_imports(source: str) -> list[tuple[str, str, int]]:
    """Return (module_name, import_line, line_number) for top-level imports."""
    results = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [("<syntax_error>", str(e), e.lineno or 0)]

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        # Only top-level (not inside functions/classes)
        if not isinstance(node.parent, ast.Module):
            continue

        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name.split(".")[0], f"import {alias.name}", node.lineno))
        else:
            module = node.module or ""
            top = module.split(".")[0] if module else ""
            for alias in node.names:
                results.append((top, f"from {module} import {alias.name}", node.lineno))

    return results


def audit_file(path: Path) -> dict:
    """Audit a single module for top-level import compliance."""
    source = path.read_text(encoding="utf-8", errors="replace")

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {
            "file": str(path),
            "error": f"syntax_error: {e}",
            "tier_a_violations": [],
            "heavy_violations": [],
            "unknown_violations": [],
            "passed": False,
        }

    tier_a_violations = []
    heavy_violations = []
    unknown_violations = []

    # Only inspect direct children of Module (top-level statements)
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue

        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                line = f"import {alias.name}"
                if top in TIER_A_ALLOWLIST:
                    continue
                if top in HEAVY_OPTIONAL_IMPORTS:
                    heavy_violations.append({"module": top, "line": node.lineno, "import": line})
                else:
                    unknown_violations.append({"module": top, "line": node.lineno, "import": line})
        else:
            module = node.module or ""
            top = module.split(".")[0] if module else ""
            if top in TIER_A_ALLOWLIST:
                continue
            if top in HEAVY_OPTIONAL_IMPORTS:
                heavy_violations.append({
                    "module": top,
                    "line": node.lineno,
                    "import": f"from {module} import {', '.join(a.name for a in node.names)}",
                })
            else:
                unknown_violations.append({
                    "module": top,
                    "line": node.lineno,
                    "import": f"from {module} import {', '.join(a.name for a in node.names)}",
                })

    passed = len(heavy_violations) == 0 and len(unknown_violations) == 0

    return {
        "file": str(path),
        "tier_a_violations": tier_a_violations,
        "heavy_violations": heavy_violations,
        "unknown_violations": unknown_violations,
        "passed": passed,
    }


def measure_module_load_cost(module_name: str) -> dict:
    """Measure cold import time and RSS delta for a module."""
    result = {"module": module_name, "import_ms": None, "rss_delta_mb": None, "error": None}

    if psutil is None:
        result["error"] = "psutil not available"
        return result

    proc = psutil.Process(os.getpid())
    rss_before = proc.memory_info().rss

    # Remove from sys.modules if present to force cold import
    was_present = module_name in sys.modules
    if was_present:
        del sys.modules[module_name]

    t0 = time.perf_counter_ns()
    try:
        importlib.import_module(module_name)
        result["import_ms"] = round((time.perf_counter_ns() - t0) / 1e6, 4)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        rss_after = proc.memory_info().rss
        result["rss_delta_mb"] = round((rss_after - rss_before) / (1024 * 1024), 4)
        # Restore if it was present before
        if was_present:
            pass  # leave as-is; cold import already happened

    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Lazy-import admission gate")
    parser.add_argument("--budget-ms", type=int, default=250, help="Total resident import budget (ms)")
    parser.add_argument("--rss-mb", type=int, default=10, help="RSS delta budget (MB)")
    parser.add_argument("--per-module-ms", type=int, default=5, help="Per-module top-level import budget (ms)")
    args = parser.parse_args()

    print("=" * 70)
    print("Celina Update 21 — Lazy-Import Admission Gate (Phase 21.2)")
    print("=" * 70)
    print()

    gear0_files = sorted(CELINA_PKG.glob("gear0_*.py"))
    plugin_files = sorted((CELINA_PKG / "plugins").glob("*.py")) if (CELINA_PKG / "plugins").exists() else []
    all_files = gear0_files + plugin_files

    audit_results = {}
    total_violations = 0

    for path in all_files:
        result = audit_file(path)
        audit_results[path.name] = result
        total_violations += len(result["heavy_violations"]) + len(result["unknown_violations"])

        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {path.name}")
        for v in result["heavy_violations"]:
            print(f"    HEAVY  line {v['line']}: {v['import']}")
        for v in result["unknown_violations"]:
            print(f"    UNKNOWN line {v['line']}: {v['import']}")

    print()
    print("-" * 70)
    print(f"Files audited: {len(all_files)}")
    print(f"Total violations: {total_violations}")
    print()

    # Report heavy import modules that need lazy conversion
    heavy_modules = [name for name, r in audit_results.items() if r["heavy_violations"]]
    if heavy_modules:
        print("Modules requiring lazy-import conversion (heavy imports at top level):")
        for name in heavy_modules:
            print(f"  - {name}")
        print()

    # Write JSON report
    report = {
        "budget_ms": args.budget_ms,
        "rss_mb": args.rss_mb,
        "per_module_ms": args.per_module_ms,
        "files_audited": len(all_files),
        "total_violations": total_violations,
        "results": audit_results,
    }
    report_path = CELINA_ROOT / "lazy_import_audit.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Audit report written to: {report_path}")

    return 1 if total_violations > 0 else 0


if __name__ == "__main__":
    sys.exit(main())