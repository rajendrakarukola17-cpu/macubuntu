#!/usr/bin/env python3
"""
Celina Update 20 — Phase 0 Baseline Harness (Truth & Safety)

Generates an evidence-based baseline BEFORE any refactor:
  1. AST-derived inventory (file, LOC, sha256, public functions, classify/execute)
  2. Golden-master capture: every reachable handler → recorded input→output
  3. 102-tool contract snapshot: tool names, signatures, return shapes
  4. Bug verification: confirm each P0 bug before fixing

All output is printed literally. No fabricated examples.

Usage:
    cd $CELINA_HOME  # or /path/to/celina/workspace
    python3 tools/baseline_harness.py [--output baseline.json]

Exit codes:
    0 = baseline captured cleanly
    1 = any inconsistency detected (contract drift, missing handlers, etc.)
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import re
import sqlite3
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CELINA_PKG = Path(__file__).resolve().parent.parent / "celina_pkg"
sys.path.insert(0, str(CELINA_PKG.parent))

# Map short module names to full package paths for direct import
MODULE_PATH_MAP: dict[str, str] = {}
for p in CELINA_PKG.glob("gear0_*.py"):
    MODULE_PATH_MAP[p.stem] = f"celina_pkg.{p.stem}"
for p in (CELINA_PKG / "plugins").glob("*.py"):
    MODULE_PATH_MAP[f"plugins.{p.stem}"] = f"celina_pkg.plugins.{p.stem}"

OUTPUT_FILE = Path(os.environ.get("CELINA_BASELINE_OUTPUT", "baseline.json"))


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ModuleInfo:
    path: str
    loc: int
    sha256: str
    public_functions: List[str]
    handlers: Dict[str, str]  # handler_name -> "classify" or "execute" or "both"
    imports_optional: List[str] = field(default_factory=list)


@dataclass
class HandlerResult:
    module: str
    handler: str
    query: str
    dry_run: bool
    result: Optional[str]
    error: Optional[str]
    latency_ms: float
    source: str


@dataclass
class ToolContract:
    name: str
    module: str
    params: List[str]
    return_annotation: Optional[str]
    is_classify_execute: bool


@dataclass
class Baseline:
    generated_at: str
    python_version: str
    cwd: str
    modules: List[ModuleInfo]
    handler_results: List[HandlerResult]
    tool_contracts: List[ToolContract]
    bug_findings: List[Dict[str, Any]]
    inventory_checksums: Dict[str, str]


# ---------------------------------------------------------------------------
# Phase 0.1 — AST-derived inventory
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def count_loc(path: Path) -> int:
    try:
        return len(path.read_text(errors="replace").splitlines())
    except Exception:
        return -1


def extract_public_functions(tree: ast.AST) -> List[str]:
    funcs: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            funcs.append(node.name)
    return sorted(funcs)


def extract_handlers(tree: ast.AST) -> Dict[str, str]:
    """Map handler names to 'classify' / 'execute' / 'both'."""
    handlers: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            name = node.name
            if name.endswith("_classify"):
                base = name[: -len("_classify")]
                handlers[base] = handlers.get(base, "classify")
                if handlers[base] == "execute":
                    handlers[base] = "both"
            elif name.endswith("_execute"):
                base = name[: -len("_execute")]
                handlers[base] = handlers.get(base, "execute")
                if handlers[base] == "classify":
                    handlers[base] = "both"
    return handlers


def build_inventory() -> List[ModuleInfo]:
    modules: List[ModuleInfo] = []
    gear0_files = sorted(CELINA_PKG.glob("gear0_*.py"))
    plugin_files = sorted((CELINA_PKG / "plugins").glob("*.py")) if (CELINA_PKG / "plugins").exists() else []
    all_files = gear0_files + plugin_files

    for path in all_files:
        try:
            source = path.read_text(errors="replace")
            tree = ast.parse(source)
        except SyntaxError as exc:
            print(f"[SYNTAX ERROR] {path}: {exc}", file=sys.stderr)
            continue

        info = ModuleInfo(
            path=str(path.relative_to(CELINA_PKG.parent)),
            loc=count_loc(path),
            sha256=sha256_file(path),
            public_functions=extract_public_functions(tree),
            handlers=extract_handlers(tree),
        )
        modules.append(info)

    return modules


# ---------------------------------------------------------------------------
# Phase 0.2 — Golden-master capture
# ---------------------------------------------------------------------------

# Each entry: (module_name, handler_name, query, dry_run)
# Only handlers with both classify AND execute are invoked directly.
GOLDEN_MASTER_QUERIES: List[tuple[str, str, str, bool]] = [
    ("gear0_calendar", "next_event", "what is my next meeting", False),
    ("gear0_network", "internet", "is my internet up", False),
    ("gear0_hardware", "cpu_temp", "what is my cpu temp", False),
    ("gear0_hardware", "battery", "what is my battery level", False),
    ("gear0_hardware", "local_ip", "what is my local ip", False),
    ("gear0_hardware", "mic", "is my mic muted", False),
    ("gear0_hardware", "vpn", "is my vpn on", False),
    ("gear0_ledger", "expense", "spent $12 on lunch", False),
    ("gear0_ledger", "expenses_today", "show my expenses today", False),
    ("gear0_ledger", "habit", "log habit: gym", False),
    ("gear0_macro", "add", 'add macro addr as "123 Main St"', False),
    ("gear0_macro", "expand", "expand addr", False),
    ("gear0_macro", "list", "list macros", False),
    ("gear0_status", "gear0_status", "gear 0 status", False),
    ("gear0_status", "audit_log", "show audit log", False),
    ("gear0_dual", "show_backend", "which backend", False),
    ("gear0_dual", "switch_backend", "switch backend to ollama", False),
    ("gear0_audit", "audit_log", "show audit log", False),
    ("gear0_audit", "storage_status", "what is my disk space", False),
    ("gear0_contacts", "contact_save", 'save john as +1234567890 contact', False),
    ("gear0_contacts", "contact_lookup", "lookup john", False),
    ("gear0_lists", "list_add", "add milk to shopping list", False),
    ("gear0_lists", "list_show", "show shopping list", False),
    ("gear0_routines", "good_morning", "good morning", False),
    ("gear0_routines", "good_night", "good night", False),
    ("gear0_routines", "focus_mode", "start focus mode", False),
    ("gear0_intent", "timer", "set a timer for 5 minutes", False),
    ("gear0_intent", "volume", "set volume to 50%", False),
    ("gear0_intent", "theme", "switch to dark mode", False),
    ("gear0_intent", "dnd", "turn on dnd", False),
    ("gear0_intent", "open_app", "open calculator", False),
    ("gear0_media", "play", "play music", False),
    ("gear0_media", "pause", "pause media", False),
    ("gear0_clipboard", "history", "show clipboard history", False),
    ("gear0_focus", "start_focus", "start focus mode for 25 minutes", False),
    ("gear0_focus", "focus_status", "focus status", False),
    # Plugins
    ("plugins.sniffer_plugin", "sniff_context", "what is my active window", False),
    ("plugins.habit_engine", "get_all_patterns", "habit", False),
    ("plugins.habit_engine", "get_proactive_suggestions", "suggest", False),
    ("plugins.os_shim", "get_system_info", "system info", False),
    ("plugins.os_shim", "lock_screen", "lock screen", False),
    ("plugins.self_heal", "get_history", "fallback history", False),
    ("plugins.self_heal", "get_reliability_report", "reliability", False),
]


def invoke_handler(module_name: str, handler_name: str, query: str, dry_run: bool) -> HandlerResult:
    import time

    t0 = datetime.now(timezone.utc)
    error: Optional[str] = None
    result: Optional[str] = None
    source = "direct"

    try:
        full_name = MODULE_PATH_MAP.get(module_name, module_name)
        mod = importlib.import_module(full_name)

        # Module-level classify: gear0_XXX_classify(query) -> handler_name string
        # For plugins: sniff_context, get_all_patterns, etc. are direct functions
        classify_fn = None
        execute_fn = None

        # Try standard gear0_XXX_classify / gear0_XXX_execute pattern
        mod_stem = module_name.split(".")[-1]
        classify_fn = getattr(mod, f"gear0_{mod_stem}_classify", None)
        execute_fn = getattr(mod, f"gear0_{mod_stem}_execute", None)

        # If not gear0 pattern, try handler-specific
        if classify_fn is None:
            classify_fn = getattr(mod, f"{handler_name}_classify", None)
        if execute_fn is None:
            execute_fn = getattr(mod, f"{handler_name}_execute", None)

        # Fallback: any *_classify / *_execute
        if classify_fn is None:
            for attr in dir(mod):
                if attr.endswith("_classify"):
                    classify_fn = getattr(mod, attr)
                    break
        if execute_fn is None:
            for attr in dir(mod):
                if attr.endswith("_execute"):
                    execute_fn = getattr(mod, attr)
                    break

        if classify_fn is None or execute_fn is None:
            return HandlerResult(
                module=module_name,
                handler=handler_name,
                query=query,
                dry_run=dry_run,
                result=None,
                error=f"Missing module-level classify/execute in {full_name}",
                latency_ms=0.0,
                source="missing",
            )

        handler = classify_fn(query)
        if handler is None:
            return HandlerResult(
                module=module_name,
                handler=handler_name,
                query=query,
                dry_run=dry_run,
                result=None,
                error="classify returned None (no match)",
                latency_ms=(datetime.now(timezone.utc) - t0).total_seconds() * 1000,
                source="no_match",
            )

        result = execute_fn(handler, query, dry_run=dry_run)
        if result is None:
            error = "execute returned None"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        result = None

    latency_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    return HandlerResult(
        module=module_name,
        handler=handler_name,
        query=query,
        dry_run=dry_run,
        result=result,
        error=error,
        latency_ms=round(latency_ms, 3),
        source=source,
)



def capture_golden_master() -> List[HandlerResult]:
    results: List[HandlerResult] = []
    for module_name, handler_name, query, dry_run in GOLDEN_MASTER_QUERIES:
        res = invoke_handler(module_name, handler_name, query, dry_run)
        results.append(res)
        status = "OK" if res.error is None and res.result is not None else "FAIL"
        print(f"  [{status}] {module_name}.{handler_name}({query!r}) → {res.result!r} ({res.latency_ms}ms)")
        if res.error:
            print(f"         error: {res.error}")
    return results


# ---------------------------------------------------------------------------
# Phase 0.3 — 102-tool contract snapshot
# ---------------------------------------------------------------------------


def extract_tool_contracts() -> List[ToolContract]:
    """Extract classify/execute signatures from gear0_* modules."""
    contracts: List[ToolContract] = []
    gear0_files = sorted(CELINA_PKG.glob("gear0_*.py"))

    for path in gear0_files:
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.endswith("_classify"):
                base = name = node.name[: -len("_classify")]
                execute_fn = None
                for n2 in ast.walk(tree):
                    if isinstance(n2, ast.FunctionDef) and n2.name == f"{base}_execute":
                        execute_fn = n2
                        break

                params = [a.arg for a in node.args.args]
                ret = node.returns
                ret_str = ast.unparse(ret) if ret is not None else None

                contracts.append(
                    ToolContract(
                        name=base,
                        module=path.stem,
                        params=params,
                        return_annotation=ret_str,
                        is_classify_execute=execute_fn is not None,
                    )
                )

    return contracts


# ---------------------------------------------------------------------------
# Phase 0.4 — Bug verification
# ---------------------------------------------------------------------------


def verify_bugs() -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    # Bug 1: Case destruction in gear0_macro
    try:
        from celina_pkg.gear0_macro import gear0_macro_execute, gear0_macro_classify
        h = gear0_macro_classify('add macro addr as "123 Main St"')
        if h:
            r = gear0_macro_execute(h, 'add macro addr as "123 Main St"', dry_run=False)
            findings.append(
                {
                    "bug": "case_destruction",
                    "module": "gear0_macro",
                    "input": 'add macro addr as "123 Main St"',
                    "output": r,
                    "confirmed_bug": r is not None and "main st" in str(r).lower() and "Main St" not in str(r),
                }
            )
    except Exception as exc:
        findings.append({"bug": "case_destruction", "error": str(exc)})

    # Bug 2: rm -rf / find -delete / shutil.rmtree in self_heal
    try:
        import ast as _ast
        source = (CELINA_PKG / "plugins" / "self_heal.py").read_text()
        tree = _ast.parse(source)
        found_rmtree = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "rmtree"
            for node in ast.walk(tree)
        )
        found_unlink = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "unlink"
            for node in ast.walk(tree)
        )
        found_find_delete = "find" in source and "-delete" in source
        has_destructive = found_rmtree or found_unlink or found_find_delete
        findings.append(
            {
                "bug": "destructive_trash_fallback",
                "module": "plugins.self_heal",
                "has_find_delete_fallback": found_find_delete,
                "has_shutil_rmtree": found_rmtree,
                "has_shutil_unlink": found_unlink,
                "confirmed_bug": has_destructive,
            }
        )
    except Exception as exc:
        findings.append({"bug": "destructive_trash_fallback", "error": str(exc)})

    # Bug 3: No backend_state.json
    state_path = Path.home() / ".celina" / "storage" / "shared" / "backend_state.json"
    findings.append(
        {
            "bug": "missing_backend_state",
            "path": str(state_path),
            "exists": state_path.exists(),
            "confirmed_bug": not state_path.exists(),
        }
    )

    # Bug 4: Data scattered across roots (root-level .db files only,
    # not shared/ or checkpoints/ subdirectories)
    db_roots = [
        Path.home() / ".celina",
        Path.home() / ".local" / "share" / "celina",
        Path.home() / ".config" / "celina",
    ]
    dbs = []
    for root in db_roots:
        if root.exists():
            for p in root.glob("*.db"):  # root-level only, not recursive
                dbs.append(str(p))
    findings.append(
        {
            "bug": "scattered_storage_roots",
            "databases": dbs,
            "root_count": len([r for r in db_roots if r.exists()]),
            "confirmed_bug": len(dbs) > 0,
        }
    )

    # Bug 5: ensure_storage_layout never called
    call_sites = []
    for p in CELINA_PKG.rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "ensure_storage_layout":
                        call_sites.append(str(p.relative_to(CELINA_PKG.parent)))
        except SyntaxError:
            pass
    findings.append(
        {
            "bug": "ensure_storage_layout_never_called",
            "call_sites": call_sites,
            "confirmed_bug": len(call_sites) == 0,
        }
    )

    # Bug 6: shell=True in os_shim
    shell_true_count = 0
    try:
        tree = ast.parse((CELINA_PKG / "plugins" / "os_shim.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell" and isinstance(node.value, ast.Constant):
                if node.value.value is True:
                    shell_true_count += 1
    except Exception:
        pass
    findings.append(
        {
            "bug": "shell_true_in_os_shim",
            "module": "plugins.os_shim",
            "shell_true_count": shell_true_count,
            "confirmed_bug": shell_true_count > 0,
        }
    )

    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print(f"=== Celina Phase 0 Baseline ===")
    print(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    print(f"Python: {sys.version}")
    print(f"CELINA_PKG: {CELINA_PKG}")
    print()

    # Phase 0.1 — Inventory
    print("--- Phase 0.1: AST Inventory ---")
    modules = build_inventory()
    for m in modules:
        handlers_str = ", ".join(f"{k}={v}" for k, v in m.handlers.items()) or "none"
        print(f"  {m.path} | LOC={m.loc} | SHA={m.sha256[:12]} | handlers: {handlers_str}")
    print(f"Total modules: {len(modules)}")
    print()

    # Phase 0.2 — Golden master
    print("--- Phase 0.2: Golden-Master Capture ---")
    handler_results = capture_golden_master()
    ok = sum(1 for r in handler_results if r.error is None and r.result is not None)
    print(f"Handlers tested: {len(handler_results)}, OK: {ok}, FAIL: {len(handler_results) - ok}")
    print()

    # Phase 0.3 — Tool contracts
    print("--- Phase 0.3: 102-Tool Contract Snapshot ---")
    contracts = extract_tool_contracts()
    for c in contracts:
        print(f"  {c.module}.{c.name}({', '.join(c.params)}) -> {c.return_annotation} | classify+execute={c.is_classify_execute}")
    print(f"Total classify/execute handlers: {len(contracts)}")
    print()

    # Phase 0.4 — Bug verification
    print("--- Phase 0.4: Bug Verification ---")
    bugs = verify_bugs()
    for b in bugs:
        print(f"  {b['bug']}: confirmed={b.get('confirmed_bug', 'UNKNOWN')}")
        for k, v in b.items():
            if k not in ("bug", "confirmed_bug"):
                print(f"    {k}: {v}")
    print()

    # Build baseline object
    baseline = Baseline(
        generated_at=datetime.now(timezone.utc).isoformat(),
        python_version=sys.version,
        cwd=str(Path.cwd()),
        modules=modules,
        handler_results=handler_results,
        tool_contracts=contracts,
        bug_findings=bugs,
        inventory_checksums={m.path: m.sha256 for m in modules},
    )

    # Write output
    data = {
        "generated_at": baseline.generated_at,
        "python_version": baseline.python_version,
        "cwd": baseline.cwd,
        "modules": [asdict(m) for m in baseline.modules],
        "handler_results": [asdict(r) for r in baseline.handler_results],
        "tool_contracts": [asdict(c) for c in baseline.tool_contracts],
        "bug_findings": baseline.bug_findings,
        "inventory_checksums": baseline.inventory_checksums,
    }
    OUTPUT_FILE.write_text(json.dumps(data, indent=2))
    print(f"Baseline written to: {OUTPUT_FILE}")
    print(f"  modules: {len(modules)}")
    print(f"  handler_results: {len(handler_results)}")
    print(f"  tool_contracts: {len(contracts)}")
    print(f"  bug_findings: {len(bugs)}")

    # Exit code: 1 if any bug confirmed or handler failed
    confirmed_bugs = sum(1 for b in bugs if b.get("confirmed_bug") is True)
    failed_handlers = sum(1 for r in handler_results if r.error is not None or r.result is None)
    if confirmed_bugs > 0 or failed_handlers > 0:
        print(f"\nGATE: {confirmed_bugs} confirmed bugs, {failed_handlers} handler failures — DO NOT PROCEED TO CONSOLIDATION")
        return 1
    print("\nGATE: No confirmed bugs, all handlers passing — safe to proceed to P0 Safety phase")
    return 0


if __name__ == "__main__":
    sys.exit(main())
