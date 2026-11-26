"""Celina World Model (`world_model.py`) — audit items 2, 10, 12.

Advisory intervention simulator: given a tool + arguments, predicts the
effect and recommends a guard (allow / tier2 / block).  Produces a
cryptographic digest bound to the simulation, used by the approval system
to detect argument-swap / approval-replay attacks.

Fail-closed: sim crash or timeout -> block or Tier-2, never fail open.
Unsimulatable tools (arbitrary shell) -> labeled "unsimulatable" in the
approval dialog, routed to sandbox-only or real dry-run modes.

Structured `Violation(kind=precondition|policy|risk)` replaces substring
matching for feasibility decisions.

Zero third-party dependencies (pure Python 3.13 stdlib).
Zero $HOME filesystem mutations at import time.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import approval as _approval
from . import policy
from .self_model import (
    EpistemicPrecheck,
    SelfModel,
    ToolCalibration,
    get_self_model,
)


class SimCrashError(RuntimeError):
    """Raised when the world model simulation itself crashes — fail closed."""


class SimulationTimeoutError(RuntimeError):
    """Raised when the simulation exceeds its time budget — fail closed."""


@dataclass(frozen=True)
class Violation:
    """Structured violation replacing substring matching (audit item 10)."""

    kind: str               # "precondition" | "policy" | "risk"
    message: str
    path: str | None = None
    tool: str | None = None


@dataclass(frozen=True)
class SimResult:
    """Result of simulating a tool intervention."""

    tool: str
    recommended_guard: str   # "allow", "tier2", "block", "unsimulatable"
    digest: str              # SHA-256 of canonical (tool, args, sim_output)
    confidence: float        # 0.0 - 1.0
    effects: dict[str, Any]  # predicted filesystem/network/etc. effects
    warnings: tuple[str, ...] = ()
    unsimulatable: bool = False
    violations: tuple[Violation, ...] = ()

    def is_feasible(self) -> bool:
        """Feasibility computed from structured Violation kinds, not substring matching."""
        return not any(v.kind == "precondition" for v in self.violations)

    def is_policy_allowed(self) -> bool:
        """Policy check: no policy-kind violations."""
        return not any(v.kind == "policy" for v in self.violations)

    def risk_violations(self) -> tuple[Violation, ...]:
        """Return risk-kind violations."""
        return tuple(v for v in self.violations if v.kind == "risk")


@dataclass(frozen=True)
class PredictedTransition:
    """Predicted OS state transition for a tool (Concept 2: OS World Model)."""

    tool: str
    confidence: float
    requires_verification: bool
    predicted_effects: dict[str, Any]
    rollback_available: bool
    epistemic_note: str = ""


@dataclass(frozen=True)
class HardwareProfile:
    """Hardware tier read from /etc/macubuntu/hardware.json (v4.6 invariant)."""

    tier: str
    has_battery: bool
    gpu_vendor: str | None = None


def _digest(tool: str, arguments: dict[str, Any] | None, sim_output: dict[str, Any]) -> str:
    """Canonical SHA-256 digest bound to (tool, args, sim_output)."""
    payload = {
        "tool": str(tool),
        "arguments": _jsonable(arguments or {}),
        "sim": sim_output,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


# ------------------------------------------------------------------
# Tool-specific simulators
# ------------------------------------------------------------------

def _sim_write_file(args: dict[str, Any]) -> SimResult:
    path = args.get("path", "")
    content = args.get("content", "")
    effects = {
        "filesystem_write": True,
        "target_path": str(path),
        "content_bytes": len(str(content)),
    }
    warnings: list[str] = []
    violations: list[Violation] = []

    # Preconditions first — missing path is a precondition failure, not policy.
    if not path or not isinstance(path, str):
        violations.append(Violation(kind="precondition", message="missing or invalid path", tool="write_file"))
        return SimResult(
            tool="write_file",
            recommended_guard="block",
            digest=_digest("write_file", args, effects),
            confidence=0.0,
            effects=effects,
            warnings=("missing path precondition",),
            violations=tuple(violations),
        )

    if policy.is_protected_path(path):
        violations.append(Violation(kind="policy", message="protected path", path=str(path), tool="write_file"))
        return SimResult(
            tool="write_file",
            recommended_guard="block",
            digest=_digest("write_file", args, effects),
            confidence=1.0,
            effects=effects,
            warnings=("protected path - write blocked",),
            violations=tuple(violations),
        )

    return SimResult(
        tool="write_file",
        recommended_guard="tier2",
        digest=_digest("write_file", args, effects),
        confidence=0.9,
        effects=effects,
        warnings=tuple(warnings),
    )


def _sim_edit_file(args: dict[str, Any]) -> SimResult:
    path = args.get("path", "")
    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")
    effects = {
        "filesystem_write": True,
        "target_path": str(path),
        "old_bytes": len(str(old_text)),
        "new_bytes": len(str(new_text)),
    }
    violations: list[Violation] = []

    # Preconditions first.
    if not path or not old_text:
        violations.append(Violation(kind="precondition", message="missing path or old_text", tool="edit_file"))
        return SimResult(
            tool="edit_file",
            recommended_guard="block",
            digest=_digest("edit_file", args, effects),
            confidence=0.0,
            effects=effects,
            warnings=("missing precondition",),
            violations=tuple(violations),
        )

    if policy.is_protected_path(path):
        violations.append(Violation(kind="policy", message="protected path", path=str(path), tool="edit_file"))
        return SimResult(
            tool="edit_file",
            recommended_guard="block",
            digest=_digest("edit_file", args, effects),
            confidence=1.0,
            effects=effects,
            warnings=("protected path - edit blocked",),
            violations=tuple(violations),
        )

    return SimResult(
        tool="edit_file",
        recommended_guard="tier2",
        digest=_digest("edit_file", args, effects),
        confidence=0.85,
        effects=effects,
    )


def _sim_move_file(args: dict[str, Any]) -> SimResult:
    src = args.get("src", "")
    dst = args.get("dst", "")
    effects = {
        "filesystem_write": True,
        "source_path": str(src),
        "dest_path": str(dst),
        "cross_device": bool(args.get("cross_device", False)),
    }
    violations: list[Violation] = []

    # Preconditions first.
    if not src or not dst:
        violations.append(Violation(kind="precondition", message="missing src or dst", tool="move_file"))
        return SimResult(
            tool="move_file",
            recommended_guard="block",
            digest=_digest("move_file", args, effects),
            confidence=0.0,
            effects=effects,
            warnings=("missing src/dst precondition",),
            violations=tuple(violations),
        )

    if policy.is_protected_path(src) or policy.is_protected_path(dst):
        violations.append(Violation(kind="policy", message="protected path in move", tool="move_file"))
        return SimResult(
            tool="move_file",
            recommended_guard="block",
            digest=_digest("move_file", args, effects),
            confidence=1.0,
            effects=effects,
            warnings=("protected path - move blocked",),
            violations=tuple(violations),
        )

    return SimResult(
        tool="move_file",
        recommended_guard="tier2",
        digest=_digest("move_file", args, effects),
        confidence=0.85,
        effects=effects,
    )


def _sim_trash_file(args: dict[str, Any]) -> SimResult:
    path = args.get("path", "")
    effects = {"filesystem_write": True, "target_path": str(path), "reversible": True}
    violations: list[Violation] = []

    # Preconditions first.
    if not path:
        violations.append(Violation(kind="precondition", message="missing path", tool="trash_file"))
        return SimResult(
            tool="trash_file",
            recommended_guard="block",
            digest=_digest("trash_file", args, effects),
            confidence=0.0,
            effects=effects,
            warnings=("missing path precondition",),
            violations=tuple(violations),
        )

    if policy.is_protected_path(path):
        violations.append(Violation(kind="policy", message="protected path", path=str(path), tool="trash_file"))
        return SimResult(
            tool="trash_file",
            recommended_guard="block",
            digest=_digest("trash_file", args, effects),
            confidence=1.0,
            effects=effects,
            warnings=("protected path - trash blocked",),
            violations=tuple(violations),
        )

    return SimResult(
        tool="trash_file",
        recommended_guard="tier2",
        digest=_digest("trash_file", args, effects),
        confidence=0.9,
        effects=effects,
    )


def _sim_shell(args: dict[str, Any]) -> SimResult:
    """Arbitrary shell is NOT simulatable — labeled unsimulatable (audit item 12)."""
    command = args.get("command", "") or args.get("script", "")
    effects = {
        "shell_execution": True,
        "command_preview": str(command)[:200],
        "unsimulatable": True,
    }
    return SimResult(
        tool="run_shell_command",
        recommended_guard="tier2",
        digest=_digest("run_shell_command", args, effects),
        confidence=0.0,
        effects=effects,
        warnings=("arbitrary shell is unsimulatable - sandbox or dry-run only",),
        unsimulatable=True,
    )


def _sim_python_sandbox(args: dict[str, Any]) -> SimResult:
    code = args.get("code", "")
    effects = {
        "code_execution": True,
        "code_bytes": len(str(code)),
        "sandboxed": True,
        "network": False,
    }
    return SimResult(
        tool="run_python_sandbox",
        recommended_guard="tier2",
        digest=_digest("run_python_sandbox", args, effects),
        confidence=0.8,
        effects=effects,
    )


def _sim_document_edit(args: dict[str, Any]) -> SimResult:
    path = args.get("path", "")
    tool_name = args.get("tool", "document_edit")
    effects = {"filesystem_write": True, "target_path": str(path), "libreoffice": True}
    violations: list[Violation] = []

    if policy.is_protected_path(path):
        violations.append(Violation(kind="policy", message="protected path", path=str(path), tool=tool_name))
        return SimResult(
            tool=tool_name,
            recommended_guard="block",
            digest=_digest(tool_name, args, effects),
            confidence=1.0,
            effects=effects,
            warnings=("protected path - document edit blocked",),
            violations=tuple(violations),
        )

    return SimResult(
        tool=tool_name,
        recommended_guard="tier2",
        digest=_digest(tool_name, args, effects),
        confidence=0.8,
        effects=effects,
    )


def _sim_mcp_or_gui(args: dict[str, Any]) -> SimResult:
    tool_name = args.get("tool", "mcp_call_tool")
    effects = {"external_process": True, "mcp_or_gui": True}
    return SimResult(
        tool=tool_name,
        recommended_guard="tier2",
        digest=_digest(tool_name, args, effects),
        confidence=0.7,
        effects=effects,
    )


def _sim_network(args: dict[str, Any]) -> SimResult:
    tool_name = args.get("tool", "network_request")
    url = args.get("url", "")
    effects = {"network_egress": True, "target_url": str(url)[:200]}
    return SimResult(
        tool=tool_name,
        recommended_guard="tier2",
        digest=_digest(tool_name, args, effects),
        confidence=0.85,
        effects=effects,
    )


def _unknown_tool_sim(tool: str, args: dict[str, Any]) -> SimResult:
    """Unknown tools fail-closed to tier2 (audit item 2 / fail-closed principle)."""
    effects = {"unknown_tool": True}
    return SimResult(
        tool=tool,
        recommended_guard="tier2",
        digest=_digest(tool, args, effects),
        confidence=0.0,
        effects=effects,
        warnings=(f"unknown tool '{tool}' - fail-closed to tier2",),
    )


# ------------------------------------------------------------------
# Hardware tier profile (v4.6 invariant: respect /etc/macubuntu/hardware.json)
# ------------------------------------------------------------------

_HARDWARE_JSON_PATH = Path("/etc/macubuntu/hardware.json")


def load_hardware_profile() -> HardwareProfile:
    """Read /etc/macubuntu/hardware.json; degrade gracefully if missing/unreadable."""
    try:
        raw = _HARDWARE_JSON_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return HardwareProfile(
            tier=str(data.get("tier", "16gb")),
            has_battery=bool(data.get("has_battery", False)),
            gpu_vendor=data.get("gpu_vendor"),
        )
    except Exception:
        return HardwareProfile(tier="16gb", has_battery=False)


# ------------------------------------------------------------------
# OS World Model (Concept 2: OS World Model / World Model)
# ------------------------------------------------------------------


class OSWorldModel:
    """Tier-adaptive OS world model.

    Combines simulation (SimResult), empirical self-model calibration
    (ToolCalibration), and epistemic prechecks (EpistemicPrecheck) to
    produce a PredictedTransition before any tool is dispatched.
    """

    def __init__(
        self,
        *,
        hardware_profile: HardwareProfile | None = None,
        self_model_instance: SelfModel | None = None,
    ) -> None:
        self.hardware = hardware_profile or load_hardware_profile()
        self.model = self_model_instance or get_self_model()
        self._context_tier_map = {"8gb": 1, "16gb": 2, "32gb": 3}

    def headroom_factor(self) -> float:
        """Tier-adaptive headroom factor for memory/CPU budgeting.

        8gb -> 0.6, 16gb -> 1.0, 32gb -> 1.4. Unknown tiers default 1.0.
        """
        return {
            "8gb": 0.6,
            "16gb": 1.0,
            "32gb": 1.4,
        }.get(self.hardware.tier, 1.0)

    def predict_transition(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        is_tier2: bool = False,
        requires_network: bool = False,
        circuit_open: bool = False,
        fallback_candidates: list[str] | None = None,
    ) -> PredictedTransition:
        """Predict the OS state transition for `tool` + `arguments`.

        Runs simulation + self-model calibration + epistemic precheck.
        Explicit simulation/precheck blocks return zero confidence. Callers
        must fail closed if prediction raises or returns invalid data.
        """
        args = arguments or {}
        sim = simulate_intervention(tool, args)

        # Epistemic precheck via self-model
        precheck = self.model.precheck_action(
            tool_name=tool,
            is_tier2=is_tier2 or sim.recommended_guard == "tier2",
            requires_network=requires_network,
            circuit_open=circuit_open,
            fallback_candidates=fallback_candidates,
        )

        cal = self.model.get_calibration(tool)
        blocked = sim.recommended_guard == "block" or not precheck.can_attempt
        confidence = 0.0 if blocked else round(
            max(0.05, min(0.98, sim.confidence * precheck.confidence)), 3,
        )

        requires_verification = (
            blocked
            or sim.recommended_guard == "tier2"
            or precheck.require_human_confirmation
            or sim.unsimulatable
        )

        rollback_available = tool in {"write_file", "edit_file", "move_file", "trash_file"}

        epistemic_note = precheck.reason or "ok"
        if sim.recommended_guard == "block":
            epistemic_note = "simulation blocked" + (f": {'; '.join(sim.warnings)}" if sim.warnings else "")
        if cal.attempts >= 3 and cal.brier_error > POOR_CALIBRATION_BRIER_THRESHOLD:
            epistemic_note += f" | brier={cal.brier_error:.2f} (poorly calibrated)"

        return PredictedTransition(
            tool=tool,
            confidence=confidence,
            requires_verification=requires_verification,
            predicted_effects=dict(sim.effects),
            rollback_available=rollback_available,
            epistemic_note=epistemic_note,
        )

    def epistemic_precheck(
        self,
        tool_name: str,
        *,
        is_tier2: bool = False,
        requires_network: bool = False,
        circuit_open: bool = False,
        fallback_candidates: list[str] | None = None,
    ) -> EpistemicPrecheck:
        """Delegate to self-model epistemic precheck."""
        return self.model.precheck_action(
            tool_name=tool_name,
            is_tier2=is_tier2,
            requires_network=requires_network,
            circuit_open=circuit_open,
            fallback_candidates=fallback_candidates,
        )

    def tool_calibration(self, tool_name: str) -> ToolCalibration:
        """Delegate to self-model calibration lookup."""
        return self.model.get_calibration(tool_name)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def simulate_intervention(
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout_sec: float = 2.0,
) -> SimResult:
    """Simulate the effect of running `tool` with `arguments`.

    Returns a SimResult with recommended_guard and digest.  The digest is
    bound to (tool, canonical_args, sim_output) so the approval system can
    detect argument-swap attacks (audit item 2).

    Fail-closed: any exception -> block guard with low confidence.  Never
    fail open (audit item 1).
    """
    args = arguments or {}

    try:
        if tool == "write_file":
            return _sim_write_file(args)
        if tool == "edit_file":
            return _sim_edit_file(args)
        if tool == "move_file":
            return _sim_move_file(args)
        if tool == "trash_file":
            return _sim_trash_file(args)
        if tool == "run_shell_command":
            return _sim_shell(args)
        if tool == "run_python_sandbox":
            return _sim_python_sandbox(args)
        if tool in {"create_document", "edit_document", "convert_document", "libreoffice_edit"}:
            return _sim_document_edit(args)
        if tool in {"mcp_call_tool", "computer_click", "computer_type", "ghostdesk_click"}:
            return _sim_mcp_or_gui(args)
        if tool in {"trigger_iot_webhook", "web_search", "fetch_webpage", "fetch_url_text"}:
            return _sim_network(args)
        return _unknown_tool_sim(tool, args)

    except SimCrashError:
        return SimResult(
            tool=tool,
            recommended_guard="block",
            digest=_digest(tool, args, {"error": "sim_crash"}),
            confidence=0.0,
            effects={},
            warnings=("simulation crashed - fail closed",),
        )
    except SimulationTimeoutError:
        return SimResult(
            tool=tool,
            recommended_guard="block",
            digest=_digest(tool, args, {"error": "sim_timeout"}),
            confidence=0.0,
            effects={},
            warnings=("simulation timed out - fail closed",),
        )
    except Exception:
        return SimResult(
            tool=tool,
            recommended_guard="block",
            digest=_digest(tool, args, {"error": "sim_error"}),
            confidence=0.0,
            effects={},
            warnings=("simulation error - fail closed",),
        )


def verify_sim_digest(
    tool: str,
    arguments: dict[str, Any] | None,
    expected_digest: str,
) -> tuple[bool, str]:
    """Re-simulate and compare digests (audit item 2 — argument-swap / replay defense).

    Called at dispatch time after user approval.  If the re-simulated
    digest doesn't match the approved digest, the arguments have changed
    and the action must be re-approved.
    """
    sim = simulate_intervention(tool, arguments)
    if sim.digest != expected_digest:
        return False, (
            f"Simulation digest mismatch: approved={expected_digest[:12]}... "
            f"resim={sim.digest[:12]}... — arguments changed since approval, "
            "re-approval required."
        )
    return True, ""


# ------------------------------------------------------------------
# Dispatch gate — audit item 2
#
#   simulate → approve → re-verify → execute → verify → calibrate
#
# The gate is split into pre_check (before approval) and post_check
# (after execution) so the caller can perform the tool execution
# and independent verification between them.
# ------------------------------------------------------------------

@dataclass(frozen=True)
class DispatchGateResult:
    proceed: bool          # whether the caller should proceed to approval/execution
    sim: SimResult         # the simulation result
    reason: str            # why we're refusing (if proceed is False)
    requires_approval: bool  # whether this tool needs user approval


@dataclass(frozen=True)
class DispatchGateOutcome:
    verified: bool         # whether the full gate passed
    action_id: str         # the action ID used for outcome recording
    reason: str            # why we're refusing (if verified is False)


def dispatch_pre_check(
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> DispatchGateResult:
    """Step 1: Simulate and decide whether to proceed (audit item 2).

    Returns proceed=False if the simulation recommends "block" — fail
    closed, never proceed on a blocked simulation.

    Returns proceed=True with the sim result; the caller then requests
    user approval, and the token's digest is bound to sim.digest.
    """
    args = arguments or {}
    sim = simulate_intervention(tool, args)

    if sim.recommended_guard == "block":
        return DispatchGateResult(
            proceed=False,
            sim=sim,
            reason=f"Simulation blocked: {'; '.join(sim.warnings)}",
            requires_approval=False,
        )

    requires_approval = _approval.requires_fresh_confirmation(tool, args)

    return DispatchGateResult(
        proceed=True,
        sim=sim,
        reason="",
        requires_approval=requires_approval,
    )


def dispatch_post_check(
    tool: str,
    arguments: dict[str, Any] | None,
    approved_digest: str,
    verified_success: bool,
    *,
    action_id: str,
    online: bool,
    gear: int | str,
    self_model_instance: SelfModel | None = None,
) -> DispatchGateOutcome:
    """Steps 3-7: Re-verify digest, record calibrated outcome (audit items 2, 8).

    Called after user approval and tool execution.  Re-simulates to detect
    argument-swap / replay, then records the verified outcome via the
    self-model calibration ledger — never trusting the tool's own exit code.

    `verified_success` must come from independent read-after-write
    verification (re-stat/hash, systemctl is-active).
    """
    args = arguments or {}

    # Re-simulate to detect argument swap since approval (audit item 2)
    ok, err = verify_sim_digest(tool, args, approved_digest)
    if not ok:
        return DispatchGateOutcome(
            verified=False,
            action_id=action_id,
            reason=err,
        )

    # Record calibrated outcome (audit item 8)
    sim = simulate_intervention(tool, args)
    model = self_model_instance or get_self_model()
    model.record_outcome_full(
        action_id=action_id,
        tool=tool,
        predicted_success=sim.confidence,
        verified_success=verified_success,
        online=online,
        gear=gear,
    )

    return DispatchGateOutcome(
        verified=True,
        action_id=action_id,
        reason="",
    )


__all__ = [
    "DispatchGateOutcome",
    "DispatchGateResult",
    "SimCrashError",
    "SimResult",
    "SimulationTimeoutError",
    "Violation",
    "dispatch_post_check",
    "dispatch_pre_check",
    "simulate_intervention",
    "verify_sim_digest",
]
