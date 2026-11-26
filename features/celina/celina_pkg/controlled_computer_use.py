"""macubuntu celina_pkg.controlled_computer_use — Controlled 7-Step Computer-Use
Harness (Astra-style, interface-drift safe).

Replaces blind click loops with a disciplined, human-in-the-loop execution
pattern mirroring OpenAI's Astra controlled computer-use:

  1. Define Scope      — allowed app/domain, permitted action types
  2. Validate Inputs   — check inputs match expected patterns
  3. Observe           — AT-SPI2 UI tree + ScreenSpot-style VLM localization
  4. Stage Action      — prepare action WITHOUT submitting (fill form, no Submit click)
  5. Preflight Verify  — check totals, recipients, dates match expectations
  6. Approve (island)  — explicit celina-island approval card before any mutation
  7. Act & Log         — execute via Wayland portal/ydotool; log to evidence.py

Interface-drift halt: if a dialog, logged-in account, or layout diverges from
the expected scope at any step, the harness pauses immediately instead of
guessing. Requires explicit re-approval to continue after a drift halt.

Integrations (existing Celina subsystems, no TOOL_SCHEMAS changes):
  - automation.perceive_desktop() / get_atspi_elements()   — Observe
  - automation.executor / InputBroker                       — Act
  - approval.requires_fresh_confirmation() / celina-island  — Approve (step 6)
  - evidence.EvidenceLedger                                 — Log (step 7)
  - net_policy.validate_public_url()                        — scope SSRF guard
  - mcp_guard Dual-Context Barrier                          — reader→planner split

stdlib-only: json, re, hashlib, time, pathlib, dataclasses, typing.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import automation, evidence
from .security import mcp_guard

# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


@dataclass
class ControlledScope:
    """Step 1: Defined scope — what the harness is allowed to touch."""

    allowed_apps: list[str] = field(default_factory=list)   # e.g. ["Firefox", "Thunderbird"]
    allowed_domains: list[str] = field(default_factory=list)  # e.g. ["example.com", "bank.example.com"]
    allowed_actions: list[str] = field(default_factory=lambda: ["click", "type", "select"])
    blocked_actions: list[str] = field(default_factory=lambda: ["submit", "drag", "hotkey"])
    max_steps: int = 7
    require_submit_approval: bool = True   # never auto-submit forms


@dataclass
class ControlledObservation:
    """Step 3: Observed desktop state."""

    active_app: str = ""
    active_title: str = ""
    atspi_elements: list[dict[str, Any]] = field(default_factory=list)
    ocr_boxes: list[dict[str, Any]] = field(default_factory=list)
    screen_fingerprint: str = ""
    timestamp_utc: str = ""
    layout_hash: str = ""          # for interface-drift detection
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ControlledAction:
    """Step 4: Staged (not yet executed) action."""

    action: str = ""               # click | type | select | navigate | wait | done
    target: str = ""               # element text / role / coordinates
    args: dict[str, Any] = field(default_factory=dict)
    stage_only: bool = True        # True = prepare only, do not execute
    expected_result: str = ""      # what should change after acting


@dataclass
class ControlledPlan:
    """Full 7-step plan result."""

    step: int = 0                  # which step we're on (1..7)
    scope: ControlledScope | None = None
    observation: ControlledObservation | None = None
    staged_action: ControlledAction | None = None
    preflight_ok: bool = False
    drift_detected: bool = False
    drift_reason: str = ""
    approved: bool = False
    executed: bool = False
    result: str = ""
    evidence_id: str = ""
    halt: bool = False             # True = interface drift → pause immediately


# --------------------------------------------------------------------------- #
# Interface-drift detection
# --------------------------------------------------------------------------- #


def _compute_layout_hash(elements: list[dict[str, Any]]) -> str:
    """Compact hash of the AT-SPI element layout for drift detection."""
    sig = json.dumps(
        [(e.get("role", ""), e.get("name", "")) for e in elements[:60]],
        sort_keys=True,
    )
    return hashlib.sha256(sig.encode("utf-8")).hexdigest()[:16]


def detect_interface_drift(
    *,
    current: ControlledObservation,
    expected_scope: ControlledScope,
    previous_layout_hash: str | None = None,
) -> tuple[bool, str]:
    """Detect interface drift: dialog, logged-in account change, or layout divergence.

    Returns (drifted, reason). If drifted, the harness must halt at step 3/4.
    """
    # 1. Active app must be within scope
    if expected_scope.allowed_apps and current.active_app not in expected_scope.allowed_apps:
        return True, f"active app '{current.active_app}' not in allowed scope {expected_scope.allowed_apps}"

    # 2. Active window title domain check (if web)
    if expected_scope.allowed_domains and current.active_title:
        title_low = current.active_title.lower()
        if not any(d.lower() in title_low for d in expected_scope.allowed_domains):
            return True, f"window title '{current.active_title}' outside allowed domains {expected_scope.allowed_domains}"

    # 3. Layout hash drift vs previous observation
    if previous_layout_hash and current.layout_hash and current.layout_hash != previous_layout_hash:
        return True, f"layout changed (hash {previous_layout_hash} -> {current.layout_hash}) — possible dialog/account/layout shift"

    return False, ""


# --------------------------------------------------------------------------- #
# Step 1: Define Scope
# --------------------------------------------------------------------------- #


def define_scope(
    *,
    allowed_apps: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    allowed_actions: list[str] | None = None,
    blocked_actions: list[str] | None = None,
    max_steps: int = 7,
    require_submit_approval: bool = True,
) -> ControlledScope:
    """Step 1 — Define Scope.

    Declares what the harness may touch. Fail-closed: empty scope = nothing
    allowed. All parameters optional with safe defaults.
    """
    return ControlledScope(
        allowed_apps=list(allowed_apps or []),
        allowed_domains=list(allowed_domains or []),
        allowed_actions=list(allowed_actions or ["click", "type", "select"]),
        blocked_actions=list(blocked_actions or ["submit", "drag", "hotkey"]),
        max_steps=max(1, min(20, int(max_steps))),
        require_submit_approval=bool(require_submit_approval),
    )


# --------------------------------------------------------------------------- #
# Step 2: Validate Inputs
# --------------------------------------------------------------------------- #


def validate_inputs(
    *,
    goal: str,
    inputs: dict[str, Any],
    expected_fields: list[str] | None = None,
    forbidden_patterns: list[str] | None = None,
) -> tuple[bool, str]:
    """Step 2 — Validate Inputs.

    Checks that user-provided inputs match expected patterns before any
    observation or action. Fail-closed: missing required fields or forbidden
    patterns block execution.
    """
    low_goal = (goal or "").strip()
    if not low_goal:
        return False, "empty goal"

    # Required fields present
    for fld in (expected_fields or []):
        if fld not in inputs or not str(inputs[fld]).strip():
            return False, f"missing required input field: {fld}"

    # Forbidden pattern check (e.g. no raw shell metacharacters in form fields)
    for pat in (forbidden_patterns or [r";\s*rm\b", r"\$\(", r"```", r"sudo\b"]):
        compiled = re.compile(pat, re.IGNORECASE)
        for key, val in inputs.items():
            if compiled.search(str(val)):
                return False, f"forbidden pattern '{pat}' in input field '{key}'"

    return True, "inputs valid"


# --------------------------------------------------------------------------- #
# Step 3: Observe (AT-SPI2 + ScreenSpot-style localization)
# --------------------------------------------------------------------------- #


def observe_desktop(
    *,
    previous_layout_hash: str | None = None,
    max_elements: int = 80,
) -> ControlledObservation:
    """Step 3 — Observe.

    Captures the current desktop state via AT-SPI2 UI tree + OCR, computes a
    layout hash for interface-drift detection. ScreenSpot-style VLM
    localization is invoked when a VLM backend is configured (offline/local
    preferred; cloud gated by privacy consent).
    """
    ui_map, fingerprint = automation.perceive_desktop(max_chars=4000)
    elements = automation.get_atspi_elements(max_elements=max_elements)
    win = automation.get_active_window_info()

    obs = ControlledObservation(
        active_app=str(win.get("app", "")),
        active_title=str(win.get("title", "")),
        atspi_elements=elements,
        screen_fingerprint=fingerprint,
        layout_hash=_compute_layout_hash(elements),
        timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    obs.meta = {"ui_map": ui_map}
    return obs


# --------------------------------------------------------------------------- #
# Step 4: Stage Action (prepare, do not execute)
# --------------------------------------------------------------------------- #


def stage_action(
    *,
    action: str,
    target: str,
    args: dict[str, Any] | None = None,
    expected_result: str = "",
    scope: ControlledScope | None = None,
) -> ControlledAction:
    """Step 4 — Stage Action.

    Prepares an action WITHOUT executing it. "Fill form without clicking
    Submit." The staged action is returned for preflight verification before
    any mutation occurs.

    Fail-closed: actions in scope.blocked_actions are rejected.
    """
    if scope and action in scope.blocked_actions:
        return ControlledAction(
            action="blocked",
            target=target,
            args=args or {},
            stage_only=True,
            expected_result=f"action '{action}' is blocked by scope",
        )

    return ControlledAction(
        action=action,
        target=target,
        args=args or {},
        stage_only=True,
        expected_result=expected_result,
    )


# --------------------------------------------------------------------------- #
# Step 5: Preflight Verify
# --------------------------------------------------------------------------- #


def preflight_verify(
    *,
    staged: ControlledAction,
    observation: ControlledObservation,
    expected_totals: list[str] | None = None,
    expected_recipients: list[str] | None = None,
    expected_dates: list[str] | None = None,
) -> tuple[bool, str]:
    """Step 5 — Preflight Verify.

    Checks that the expected outcomes (totals, recipients, dates) are present
    in the observed desktop state before approving execution. Returns
    (ok, reason). Fail-closed: any expected value not found blocks approval.
    """
    ui_map = observation.meta.get("ui_map", "") if observation.meta else ""
    combined = f"{ui_map} {observation.active_title} {observation.active_app}".lower()

    for label, expected_list in (
        ("totals", expected_totals or []),
        ("recipients", expected_recipients or []),
        ("dates", expected_dates or []),
    ):
        for exp in expected_list:
            exp_low = str(exp).strip().lower()
            if exp_low and exp_low not in combined:
                return False, f"preflight: expected {label} '{exp}' not found on screen"

    return True, "preflight verified"


# --------------------------------------------------------------------------- #
# Step 6: Approve in celina-island (Dual-Context Barrier)
# --------------------------------------------------------------------------- #


def approve_in_island(
    *,
    staged: ControlledAction,
    observation: ControlledObservation,
    scope: ControlledScope | None = None,
    require_approval: bool = True,
) -> tuple[bool, str]:
    """Step 6 — Approve in celina-island.

    Uses the mcp_guard Dual-Context Barrier:
      - Quarantined Reader: converts the staged action + observation into a
        typed QuarantinedIntent (never exposes raw screen text to the planner).
      - Privileged Planner: decides whether to approve, based only on the
        typed intent. Untrusted sources require explicit approval.

    Returns (approved, reason). If require_approval is False, auto-approves
    (used only for trusted local automation).
    """
    guard = mcp_guard.McpGuard()

    # Quarantined Reader: build intent from staged action (no raw text leak)
    intent = guard.reader_to_intent(
        raw_text=(
            f"{staged.action} {staged.target} "
            f"{json.dumps(staged.args, sort_keys=True)} "
            f"{observation.active_app} {observation.active_title}"
        ),
        source_trust=mcp_guard.DEFAULT_SOURCE_TRUST,
        allowed_actions=list(scope.allowed_actions if scope else ["click", "type", "select"]),
    )

    # Scope gate: action must be in allowed_actions
    allowed = list(scope.allowed_actions if scope else ["click", "type", "select"])
    if intent.action and intent.action not in allowed and intent.action:
        return False, f"action '{intent.action}' not in allowed scope {allowed}"

    plan = guard.planner_plan(
        intent=intent,
        require_approval=require_approval,
        allowlist=allowed,
    )

    return bool(plan.get("approved", False)), str(plan.get("reason", ""))


# --------------------------------------------------------------------------- #
# Step 7: Act & Log to evidence.py
# --------------------------------------------------------------------------- #


def act_and_log(
    *,
    staged: ControlledAction,
    observation: ControlledObservation,
    executor: Callable[..., Any] | None = None,
    scope: ControlledScope | None = None,
) -> tuple[bool, str, str]:
    """Step 7 — Act & Log.

    Executes the staged action via the Wayland executor (portal/ydotool),
    then logs the result to evidence.py's EvidenceLedger with a content hash.

    Returns (executed, result, evidence_id).
    """
    executed = False
    result = ""
    evidence_id = ""

    try:
        ledger = evidence.get_evidence_ledger()
    except Exception:
        ledger = None

    # Build the execution record BEFORE acting (for rollback/audit)
    exec_record: dict[str, Any] = {
        "action": staged.action,
        "target": staged.target,
        "args": staged.args,
        "app": observation.active_app,
        "title": observation.active_title,
        "layout_hash": observation.layout_hash,
        "timestamp_utc": observation.timestamp_utc,
    }

    if executor is not None:
        try:
            res = executor(staged.action, staged.args)
            executed = True
            result = str(res)
        except TypeError:
            # Executor may have a different signature; try action-only
            try:
                res = executor(staged.action)
                executed = True
                result = str(res)
            except Exception as exc:
                result = f"execution error: {exc}"
        except Exception as exc:
            result = f"execution error: {exc}"
    else:
        result = "no executor provided — action staged but not executed"

    exec_record["result"] = result
    exec_record["executed"] = executed

    # Log to evidence ledger (content-hashed, append-only)
    if ledger is not None:
        try:
            _rec = ledger.record_security_event(
                event_type="controlled_computer_use",
                tool_name=str(staged.action),
                detail=json.dumps(exec_record, sort_keys=True),
                summary=str(staged.target or ""),
                policy_decision=("allow" if executed else "deny"),
            )
            evidence_id = str(_rec.get("entry_hash", ""))
        except Exception:
            evidence_id = ""

    return executed, result, evidence_id


# --------------------------------------------------------------------------- #
# Orchestrator: run all 7 steps with interface-drift halt
# --------------------------------------------------------------------------- #


def controlled_execute(
    *,
    goal: str,
    scope: ControlledScope,
    inputs: dict[str, Any],
    staged_action: ControlledAction,
    executor: Callable[..., Any] | None = None,
    require_approval: bool = True,
    previous_layout_hash: str | None = None,
    expected_totals: list[str] | None = None,
    expected_recipients: list[str] | None = None,
    expected_dates: list[str] | None = None,
    approval_cb: Callable[[str, str], bool] | None = None,
) -> ControlledPlan:
    """Orchestrates the full 7-step controlled computer-use harness.

    Steps:
      1. Define Scope      (validated scope parameter)
      2. Validate Inputs   (goal + form inputs)
      3. Observe           (AT-SPI2 + layout hash)
      4. Stage Action      (prepare, no execution)
      5. Preflight Verify  (totals/recipients/dates on screen)
      6. Approve (island)  (Dual-Context Barrier + explicit approval)
      7. Act & Log         (execute + evidence ledger)

    Interface-drift halt: if step 3 detects drift (wrong app/domain/layout),
    the plan halts with halt=True and requires explicit re-approval.

    Returns a ControlledPlan with the outcome of each step.
    """
    plan = ControlledPlan(step=0, scope=scope)

    # --- Step 1: Define Scope (already provided) ---------------------------
    plan.step = 1
    if not scope.allowed_apps and not scope.allowed_domains:
        plan.halt = True
        plan.drift_reason = "empty scope — nothing allowed (fail-closed)"
        return plan

    # --- Step 2: Validate Inputs ------------------------------------------
    plan.step = 2
    inputs_ok, inputs_reason = validate_inputs(
        goal=goal,
        inputs=inputs,
    )
    if not inputs_ok:
        plan.halt = True
        plan.drift_reason = f"inputs invalid: {inputs_reason}"
        return plan

    # --- Step 3: Observe --------------------------------------------------
    plan.step = 3
    observation = observe_desktop(
        previous_layout_hash=previous_layout_hash,
    )
    plan.observation = observation

    # Interface-drift check
    drifted, drift_reason = detect_interface_drift(
        current=observation,
        expected_scope=scope,
        previous_layout_hash=previous_layout_hash,
    )
    if drifted:
        plan.halt = True
        plan.drift_detected = True
        plan.drift_reason = drift_reason
        return plan

    # --- Step 4: Stage Action --------------------------------------------
    plan.step = 4
    plan.staged_action = staged_action

    # --- Step 5: Preflight Verify ----------------------------------------
    plan.step = 5
    preflight_ok, preflight_reason = preflight_verify(
        staged=staged_action,
        observation=observation,
        expected_totals=expected_totals,
        expected_recipients=expected_recipients,
        expected_dates=expected_dates,
    )
    plan.preflight_ok = preflight_ok
    if not preflight_ok:
        plan.halt = True
        plan.drift_reason = f"preflight failed: {preflight_reason}"
        return plan

    # --- Step 6: Approve in celina-island ---------------------------------
    plan.step = 6
    # If an approval_cb is provided, use it for the final gate (island card)
    island_approved, island_reason = approve_in_island(
        staged=staged_action,
        observation=observation,
        scope=scope,
        require_approval=require_approval,
    )

    # Additional explicit approval gate (celina-island card)
    if island_approved and approval_cb is not None:
        preview = (
            f"Controlled Computer-Use (Astra) — Approve Action?\n"
            f"Action: {staged_action.action}\n"
            f"Target: {staged_action.target}\n"
            f"App: {observation.active_app} | {observation.active_title}\n"
            f"Expected: {staged_action.expected_result}\n"
            f"Press Esc to abort."
        )
        island_approved = bool(approval_cb("Approve controlled computer-use action?", preview))
        island_reason = "approved via celina-island" if island_approved else "rejected via celina-island"

    plan.approved = island_approved
    if not island_approved:
        plan.halt = True
        plan.drift_reason = f"not approved: {island_reason}"
        return plan

    # --- Step 7: Act & Log ------------------------------------------------
    plan.step = 7
    executed, result, evidence_id = act_and_log(
        staged=staged_action,
        observation=observation,
        executor=executor,
        scope=scope,
    )
    plan.executed = executed
    plan.result = result
    plan.evidence_id = evidence_id
    return plan


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover

    scope = define_scope(
        allowed_apps=["Firefox"],
        allowed_domains=["example.com"],
        allowed_actions=["click", "type"],
        blocked_actions=["submit", "drag"],
    )
    print("Scope:", scope)

    ok, reason = validate_inputs(
        goal="fill contact form on example.com",
        inputs={"name": "Ada Lovelace", "email": "ada@example.com"},
        expected_fields=["name", "email"],
    )
    print("Validate inputs:", ok, reason)

    plan = controlled_execute(
        goal="fill contact form",
        scope=scope,
        inputs={"name": "Ada", "email": "ada@example.com"},
        staged_action=stage_action(
            action="type",
            target="name field",
            args={"text": "Ada Lovelace"},
            expected_result="name field populated",
        ),
        require_approval=False,
    )
    print("Plan step:", plan.step, "halt:", plan.halt, "reason:", plan.drift_reason)
    print("Approved:", plan.approved, "Executed:", plan.executed, "Evidence:", plan.evidence_id)
