"""macubuntu celina_pkg.shortcuts.crystallizer — LLM-to-Shortcut habit mining.

Scans evidence.py turn receipts during overnight sleep consolidation, compiles
repeated (>=3-run) multi-step LLM tool chains into parameterized .cshortcut.yaml
files in shortcuts/quarantine/, validates with skilspector_scan, and stages them
for one-click promotion in celina-island.

stdlib + PyYAML. Zero generative LLM, no network, no PyTorch.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from celina_pkg.evidence import EvidenceLedger
from celina_pkg.shortcuts.engine import ShortcutInput, ShortcutSpec, ShortcutStep

MIN_REPEATS = 3
QUARANTINE_SUBDIR = "shortcuts/quarantine"


def signature_of_trace(tool_names: list[str]) -> str:
    seq = "->".join(tool_names)
    return hashlib.sha256(seq.encode()).hexdigest()[:12]


def find_crystallizable_habits(
    turn_traces: list[dict[str, Any]],
    min_repeats: int = MIN_REPEATS,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in turn_traces:
        calls = trace.get("tool_calls", [])
        if trace.get("status") == "ok" and len(calls) >= 2:
            tool_names = [c.get("action", "") for c in calls]
            buckets[signature_of_trace(tool_names)].append(trace)

    candidates: list[dict[str, Any]] = []
    for sig, traces in buckets.items():
        if len(traces) < min_repeats:
            continue
        sample_calls = traces[-1]["tool_calls"]
        phrases = sorted({str(t.get("user_query", "")).strip() for t in traces if t.get("user_query")})
        steps = [
            {
                "id": "step_" + str(i + 1),
                "action": tc.get("action", ""),
                "args": tc.get("args", {}),
                "tier": tc.get("tier", 1),
            }
            for i, tc in enumerate(sample_calls)
        ]
        candidates.append(
            {
                "id": "auto_" + sig,
                "name": "Auto Shortcut (" + sample_calls[0].get("action", "unknown") + "…)",
                "description": "Crystallized from " + str(len(traces)) + " repeated LLM runs.",
                "phrases": phrases[:6],
                "triggers": [],
                "inputs": [],
                "steps": steps,
                "allow_without_llm": True,
                "preauthorized_tier2": False,
            },
        )
    return candidates


def traces_from_ledger(
    ledger: EvidenceLedger,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    entries = ledger.list_entries(limit=limit)
    turns: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        if e.get("outcome") != "ok":
            continue
        goal = e.get("root_goal_id") or e.get("task_id") or "unknown"
        turns[goal].append(e)

    traces: list[dict[str, Any]] = []
    for goal, calls in turns.items():
        calls.sort(key=lambda c: c.get("seq", 0))
        tool_calls = [
            {
                "action": c.get("tool", ""),
                "args": {},
                "tier": c.get("tier", 1),
            }
            for c in calls
        ]
        if len(tool_calls) >= 2:
            traces.append(
                {
                    "tool_calls": tool_calls,
                    "user_query": goal,
                    "status": "ok",
                },
            )
    return traces


def stage_to_quarantine(
    spec_dict: dict[str, Any],
    quarantine_dir: Path,
) -> Path:
    quarantine_dir = Path(quarantine_dir)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    out = quarantine_dir / (spec_dict["id"] + ".cshortcut.yaml")
    out.write_text(yaml.safe_dump(spec_dict, sort_keys=False), encoding="utf-8")
    return out


def crystallize(
    ledger: EvidenceLedger,
    quarantine_dir: Path,
    *,
    min_repeats: int = MIN_REPEATS,
    limit: int = 1000,
    skilspector_scan: Any = None,
) -> dict[str, Any]:
    traces = traces_from_ledger(ledger, limit=limit)
    candidates = find_crystallizable_habits(traces, min_repeats=min_repeats)

    staged: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for cand in candidates:
        spec = ShortcutSpec(
            id=cand["id"],
            name=cand["name"],
            description=cand["description"],
            phrases=list(cand.get("phrases", [])),
            triggers=list(cand.get("triggers", [])),
            inputs=[
                ShortcutInput(
                    name=i.get("name", ""),
                    kind=str(i.get("kind", "text")),
                    required=bool(i.get("required", True)),
                    default=i.get("default"),
                )
                for i in cand.get("inputs", [])
            ],
            steps=[
                ShortcutStep(
                    id=s.get("id", ""),
                    action=str(s.get("action", "")),
                    args=dict(s.get("args") or {}),
                    tier=int(s.get("tier", 1)),
                )
                for s in cand.get("steps", [])
            ],
            allow_without_llm=bool(cand.get("allow_without_llm", True)),
            preauthorized_tier2=bool(cand.get("preauthorized_tier2", False)),
        )

        sandbox_ok = True
        if skilspector_scan is not None:
            try:
                code = "# Auto-generated Shortcut: " + spec.name + "\n"
                for s in spec.steps:
                    code += "# step " + s.id + ": " + s.action + "\n"
                sandbox_ok = bool(skilspector_scan(code, timeout=30))
            except Exception:
                sandbox_ok = False

        if sandbox_ok:
            path = stage_to_quarantine(
                {
                    "id": spec.id,
                    "name": spec.name,
                    "description": spec.description,
                    "phrases": spec.phrases,
                    "triggers": spec.triggers,
                    "inputs": [
                        {"name": i.name, "kind": i.kind, "required": i.required, "default": i.default}
                        for i in spec.inputs
                    ],
                    "steps": [
                        {"id": s.id, "action": s.action, "args": s.args, "tier": s.tier}
                        for s in spec.steps
                    ],
                    "allow_without_llm": spec.allow_without_llm,
                    "preauthorized_tier2": spec.preauthorized_tier2,
                },
                quarantine_dir,
            )
            staged.append({"id": spec.id, "path": str(path), "repeats": min_repeats})
        else:
            skipped.append({"id": spec.id, "reason": "sandbox_validation_failed"})

    return {
        "candidates_found": len(candidates),
        "staged": staged,
        "skipped": skipped,
        "quarantine_dir": str(Path(quarantine_dir)),
    }
