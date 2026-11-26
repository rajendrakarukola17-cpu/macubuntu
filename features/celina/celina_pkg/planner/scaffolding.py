"""Celina v5 — Capability #5: Pre-Execution Logical Scaffolding (FORST)

Builds a deterministic, structured logical scaffold BEFORE leaf execution or
multi-step tree expansion to prevent initial logical drift, and injects canonical
KV-cache anchor prefixes so local 1.7B–4B models start with warm LCP KV slots.

stdlib-only (sqlite3, datetime, re, json).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from typing import Any


@dataclasses.dataclass(frozen=True)
class LogicalScaffold:
    goal: str
    objective_type: str
    preconditions: tuple[str, ...]
    invariants: tuple[str, ...]
    milestones: tuple[str, ...]
    verification_criteria: tuple[str, ...]
    kv_anchor_id: str
    anchor_prefix: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "objective_type": self.objective_type,
            "preconditions": list(self.preconditions),
            "invariants": list(self.invariants),
            "milestones": list(self.milestones),
            "verification_criteria": list(self.verification_criteria),
            "kv_anchor_id": self.kv_anchor_id,
            "anchor_prefix": self.anchor_prefix,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


# Canonical KV-cache anchor templates reused verbatim across planning sessions
_ANCHOR_TEMPLATES: dict[str, str] = {
    "file_mutation": (
        "[FORST_ANCHOR:FILE_MUTATION]\n"
        "INVARIANT_1: Never touch ~/.ssh, ~/.gnupg, ~/.aws, ~/.kube, or credential stores.\n"
        "INVARIANT_2: Inspect target path before mutating and capture SHA-256 pre-execution checkpoint.\n"
        "INVARIANT_3: Verify resulting state after edit; roll back on verification failure.\n"
    ),
    "office_document": (
        "[FORST_ANCHOR:OFFICE_DOCUMENT]\n"
        "INVARIANT_1: Prefer structured LibreOffice UNO pipe (localhost:2002) over GUI clicks.\n"
        "INVARIANT_2: Capture SHA-256 UndoJournal snapshot prior to mutation.\n"
        "INVARIANT_3: Confirm document stored cleanly before marking done.\n"
    ),
    "system_diagnostic": (
        "[FORST_ANCHOR:SYSTEM_DIAGNOSTIC]\n"
        "INVARIANT_1: Prefer read-only Tier-1 introspection tools first.\n"
        "INVARIANT_2: Redact any credentials or secrets in logs and evidence receipts.\n"
        "INVARIANT_3: Report exact metrics and root cause concisely.\n"
    ),
    "gui_automation": (
        "[FORST_ANCHOR:GUI_AUTOMATION]\n"
        "INVARIANT_1: Capture AT-SPI2 semantic blueprint before clicking or typing.\n"
        "INVARIANT_2: Re-confirm before interacting with sensitive windows (terminal, keyring, bank).\n"
        "INVARIANT_3: Verify visible post-condition after each actuation.\n"
    ),
    "general_autonomy": (
        "[FORST_ANCHOR:GENERAL_AUTONOMY]\n"
        "INVARIANT_1: Respect Tier-1 auto vs. Tier-2 approval boundaries and observe-only mode.\n"
        "INVARIANT_2: Decompose goal into minimal reversible milestones.\n"
        "INVARIANT_3: Emit tamper-evident evidence with root-goal intent linkage.\n"
    ),
}

_OBJECTIVE_KEYWORDS: dict[str, list[str]] = {
    "office_document": [
        r"\.odt\b", r"\.docx\b", r"\.ods\b", r"\.xlsx\b",
        r"\blibreoffice\b", r"\bspreadsheet\b", r"\bwriter\b", r"\bcalc\b",
    ],
    "file_mutation": [
        r"\brefactor\b", r"\bpatch\b", r"\bmove\b", r"\brename\b",
        r"\bdelete\b", r"\btrash\b", r"\bwrite\b", r"\bedit\b",
    ],
    "gui_automation": [
        r"\bclick\b", r"\bscreen\b", r"\bwindow\b", r"\bgui\b",
        r"\bform\b", r"\bbutton\b",
    ],
    "system_diagnostic": [
        r"\bstatus\b", r"\bcheck\b", r"\bdiagnose\b", r"\bram\b",
        r"\bcpu\b", r"\baudit\b", r"\binspect\b", r"\bstats\b",
    ],
}


def _classify_objective(goal: str) -> str:
    """Classify objective type using word-boundary regexes (P1 fix).

    Avoids substring false positives: \"undelete\" no longer matches \"delete\",
    \"create\" no longer triggers file_mutation via \"delete\", etc.
    """
    low = (goal or "").lower()
    scores: dict[str, int] = dict.fromkeys(_OBJECTIVE_KEYWORDS, 0)
    for obj_type, patterns in _OBJECTIVE_KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, low):
                scores[obj_type] += 1
    # Deterministic tie-break: office_document > file_mutation > gui > diagnostic > general
    tiebreak = ["office_document", "file_mutation", "gui_automation", "system_diagnostic"]
    best = max(tiebreak, key=lambda t: scores[t])
    if scores[best] > 0:
        return best
    return "general_autonomy"


class LogicalScaffolder:
    """Builds FORST pre-execution logical scaffolds with deterministic KV-cache anchors."""

    def build_scaffold(
        self,
        goal: str,
        *,
        available_tools: list[str] | None = None,
        world_state: dict[str, Any] | None = None,
    ) -> LogicalScaffold:
        clean_goal = " ".join((goal or "").strip().split())
        obj_type = _classify_objective(clean_goal)
        anchor_prefix = _ANCHOR_TEMPLATES[obj_type]
        kv_anchor_id = hashlib.sha256(anchor_prefix.encode("utf-8")).hexdigest()[:16]

        ws = world_state or {}

        clauses = [c.strip() for c in re.split(r"\b(?:and then|then|after that|;)\b", clean_goal, flags=re.IGNORECASE) if c.strip()]
        if not clauses:
            clauses = [clean_goal or "Complete objective"]

        tool_count = len(available_tools or [])
        preconditions = (
            f"Objective parsed ({obj_type}) with {tool_count} declared tools",
            "Active security policy & capability boundaries verified",
        )
        if ws:
            preconditions += (
                f"World state snapshot captured ({len(ws)} keys): " + ", ".join(sorted(ws)[:5]),
            )

        invariants = tuple(
            ln.strip() for ln in anchor_prefix.splitlines() if ln.strip().startswith("INVARIANT_")
        )

        milestones = tuple(f"M{idx}: {clause}" for idx, clause in enumerate(clauses[:5], 1))

        verification = (
            "All leaf tasks completed or safely observed without policy violation",
            "Hash-chained evidence verified from root goal to leaf execution",
        )
        return LogicalScaffold(
            goal=clean_goal,
            objective_type=obj_type,
            preconditions=preconditions,
            invariants=invariants,
            milestones=milestones,
            verification_criteria=verification,
            kv_anchor_id=kv_anchor_id,
            anchor_prefix=anchor_prefix,
        )

    def render_prompt_prefix(self, scaffold: LogicalScaffold) -> str:
        """Produces a canonical KV-cache-friendly prefix for planner/worker inference."""
        lines = [
            scaffold.anchor_prefix.rstrip(),
            f"GOAL: {scaffold.goal}",
            "MILESTONES:",
            *(f"  - {m}" for m in scaffold.milestones),
            "VERIFICATION:",
            *(f"  - {v}" for v in scaffold.verification_criteria),
        ]
        return "\n".join(lines)
