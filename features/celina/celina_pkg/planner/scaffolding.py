"""
Celina v5 — Capability #5: Pre-Execution Logical Scaffolding (FORST)

Builds a deterministic, structured logical scaffold BEFORE leaf execution or
multi-step tree expansion to prevent initial logical drift, and injects canonical
KV-cache anchor prefixes so local 1.7B–4B models start with warm LCP KV slots.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from typing import Any, Optional


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
        "INVARIANT_1: Prefer structured LibreOffice UNO bridge (localhost:2002) over GUI clicks.\n"
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


def _classify_objective(goal: str) -> str:
    low = (goal or "").lower()
    if any(k in low for k in (".odt", ".docx", ".ods", ".xlsx", "libreoffice", "spreadsheet", "writer", "calc")):
        return "office_document"
    if any(k in low for k in ("edit ", "write ", "move ", "rename ", "delete ", "trash ", "refactor ", "patch ")):
        return "file_mutation"
    if any(k in low for k in ("click", "screen", "window", "gui", "form", "button")):
        return "gui_automation"
    if any(k in low for k in ("status", "check ", "diagnose", "ram", "cpu", "audit", "inspect", "stats")):
        return "system_diagnostic"
    return "general_autonomy"


class LogicalScaffolder:
    """Builds FORST pre-execution logical scaffolds with deterministic KV-cache anchors."""

    def build_scaffold(
        self,
        goal: str,
        *,
        available_tools: Optional[list[str]] = None,
        world_state: Optional[dict[str, Any]] = None,
    ) -> LogicalScaffold:
        clean_goal = " ".join((goal or "").strip().split())
        obj_type = _classify_objective(clean_goal)
        anchor_prefix = _ANCHOR_TEMPLATES[obj_type]
        kv_anchor_id = hashlib.sha256(anchor_prefix.encode("utf-8")).hexdigest()[:16]

        clauses = [c.strip() for c in re.split(r"\b(?:and then|then|after that|;)\b", clean_goal, flags=re.I) if c.strip()]
        if not clauses:
            clauses = [clean_goal or "Complete objective"]

        preconditions = (
            f"Objective parsed ({obj_type}) with {len(available_tools or [])} declared tools",
            "Active security policy & capability boundaries verified",
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
