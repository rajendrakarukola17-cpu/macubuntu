"""
Celina AI (Celly) - v4.4 Self-Improving, Glass-Box Cognitive Brain + Strict Privacy Firewall
- Gear 0 (Reflex):     Instant local shortcuts (<20ms, 0 tokens, 0 MB)
- Gear 1 (Fast):       No deep thinking (reasoning_effort='none', crisp 1-2 sentence answers)
- Gear 2 (Tactical):   Pre-flight micro-plan + dynamic domain-pruned tools + error self-correction
- Gear 3 (Deep):       Full System 2 reasoning (or 3-bullet <analysis> CoT on local 3B)
- Glass-Box Receipts:  TurnTrace records gear trigger, pruned tools, BM25 recalled facts/episodes,
                       privacy summary, self-corrections & latency (/why or ⓘ Why pill)
- Continual Learning:  Implicit correction detector, auto-healing failed skills, habit pattern mining
- Multilingual:        English, Telugu (native + Tenglish), Hindi (native + Hinglish)
"""

import argparse
import ast
import ctypes
import dataclasses
import datetime
import gc
import hashlib
import json
import math
import operator as _op
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

_GEAR0_MATH_BINOPS = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
}
_GEAR0_MATH_UNARYOPS = {ast.UAdd: _op.pos, ast.USub: _op.neg}
_GEAR0_MATH_FUNCS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
}
_GEAR0_MATH_CONSTS = {"pi": math.pi, "e": math.e}
_GEAR0_MATH_PREFIX_RE = re.compile(
    r"^(?:(?:what(?:'s|\s+is)|calculate|compute|eval(?:uate)?|solve)\s+)+",
    re.IGNORECASE,
)


def _safe_math_eval(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _safe_math_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _GEAR0_MATH_BINOPS:
        left = _safe_math_eval(node.left)
        right = _safe_math_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponent too large")
        return _GEAR0_MATH_BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _GEAR0_MATH_UNARYOPS:
        return _GEAR0_MATH_UNARYOPS[type(node.op)](_safe_math_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        fn = _GEAR0_MATH_FUNCS.get(node.func.id)
        if fn is not None and 1 <= len(node.args) <= 2:
            return fn(*(_safe_math_eval(a) for a in node.args))
    if isinstance(node, ast.Name) and node.id in _GEAR0_MATH_CONSTS:
        return _GEAR0_MATH_CONSTS[node.id]
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def try_gear0_math(query: str) -> Optional[str]:
    """
    Eval-free AST arithmetic evaluator for Gear 0 (Reflex, 0 LLM tokens).
    Evaluates expressions like `sqrt(144)`, `what is 12 * 12?`, `2^8`, `sin(0)`.
    Returns None for non-math or bare single-number inputs.
    """
    raw = (query or "").strip()
    if not raw or len(raw) > 120:
        return None
    stripped = _GEAR0_MATH_PREFIX_RE.sub("", raw).strip().rstrip("=?").strip()
    if not stripped:
        return None
    expr = stripped.replace("^", "**")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    if isinstance(tree.body, ast.Constant):
        return None
    try:
        result = _safe_math_eval(tree)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(result, float):
        if math.isnan(result) or math.isinf(result):
            return None
        if result.is_integer():
            return str(int(result))
        return f"{result:.10g}"
    return str(result)

import requests
from requests.adapters import HTTPAdapter

from . import (
    automation,
    cache,
    config,
    everyday_agents,
    everyday_trainer,
    gear0_calendar,
    gear0_contacts,
    gear0_context,
    gear0_dual,
    gear0_graph,
    gear0_hardware,
    gear0_intent,
    gear0_iot,
    gear0_audit,
    gear0_brain,
    gear0_clipboard,
    gear0_focus,
    gear0_ledger,
    gear0_lists,
    gear0_macro,
    gear0_media,
    gear0_memory,
    gear0_ml_nlu,
    gear0_network,
    gear0_routines,
    gear0_5_micro,
    gear0_slots,
    gear0_status,
    gear0_voice,
    gear0_storage,
    gear0_utils,
    guardrails,
    inference_adaptive,
    libreoffice_uno,
    mcp_client,
    memory,
    net_policy,
    privacy,
    provider_router,
    skills,
    telemetry,
    tools,
    turbo_mode,
    voice,
)
from .gear0_storage import initialize_storage
from .plugins import sniffer_plugin, habit_engine, data_oracle, os_shim, self_heal
# Initialize storage backend state at import time (P0-03)
initialize_storage()

from concurrent.futures import ThreadPoolExecutor, as_completed

_LIBREOFFICE_HINTS = (
    "libreoffice", "writer", "calc", "impress", ".odt", ".ods", ".odp", ".docx", ".xlsx", "spreadsheet", "document"
)


@dataclasses.dataclass(frozen=True)
class OfflineFallback:
    """Returned by the router when a web_comms request cannot run offline or while disabled."""
    tool: str = "respond_offline"
    message: str = (
        "Offline fallback: live web_comms tools require internet access and opt-in web_comms.enabled=true. "
        "Local offline tools and local LLM remain available."
    )
    domain: str = "web_comms"


@dataclasses.dataclass(frozen=True)
class RouteDecision:
    tool_name: str
    domain: str
    tier: int = 1
    arguments: dict[str, Any] = dataclasses.field(default_factory=dict)
    arguments_hash: str = ""
    why_receipt_hash: str = ""
    gear: str = "tactical"
    preferred_backend: str = ""


def apply_offline_and_uno_policy(
    decision: RouteDecision,
    *,
    goal: str = "",
    online_probe: Optional[Callable[[], bool]] = None,
    uno_probe: Optional[Callable[[], bool]] = None,
) -> RouteDecision | OfflineFallback:
    """
    Offline-aware & UNO-preferring router policy:
      1. If decision.domain == 'web_comms' and (offline or web_comms disabled), return OfflineFallback.
      2. For files_office document edits (or GUI edits targeting LibreOffice), prefer UNO (`libreoffice_edit`)
         over fragile `wayland_gui` GUI automation when UNO is available.
    """
    probe_online = online_probe if online_probe is not None else net_policy.is_online
    if decision.domain == "web_comms":
        if not config.is_web_comms_enabled():
            return OfflineFallback(
                tool="respond_offline",
                message=(
                    "Offline fallback: web_comms is disabled by default (web_comms.enabled=false). "
                    "Enable opt-in web_comms to fetch live data."
                ),
            )
        if not probe_online():
            return OfflineFallback(
                tool="respond_offline",
                message=(
                    "Offline fallback: no internet connectivity detected for web_comms. "
                    "Answering with local offline knowledge/tools instead."
                ),
            )

    goal_low = (goal or "").lower()
    is_office_edit = (
        decision.domain == "files_office"
        and (
            decision.tool_name in {"libreoffice_edit", "edit_document", "autonomous_gui_task", "click_on_text", "type_text"}
            or any(h in goal_low for h in _LIBREOFFICE_HINTS)
        )
    ) or (
        decision.domain == "wayland_gui"
        and any(h in goal_low for h in _LIBREOFFICE_HINTS)
    )
    if is_office_edit:
        probe_uno = uno_probe if uno_probe is not None else libreoffice_uno.is_uno_available
        if probe_uno():
            return dataclasses.replace(
                decision,
                tool_name="libreoffice_edit",
                domain="files_office",
                tier=2,
                preferred_backend="uno",
            )
        return dataclasses.replace(decision, preferred_backend="wayland_gui")

    return decision

StatusCallback = Callable[[str], None]
ApprovalCallback = Callable[[str, str], bool]
PrivacyConsentCallback = privacy.PrivacyConsentCallback
TaskGlowCallback = Callable[[bool], None]
StreamCallback = Callable[[str], None]

_IMPLICIT_CORRECTION_PATTERNS = [
    re.compile(r"^\s*(?:no[,.]?\s+(?:i\s+meant|don'?t|do\s+not|never|always|use|instead)|actually[,.]?\s+(?:always|never|prefer|use|don'?t))", re.I),
    re.compile(r"\b(?:always\s+use|never\s+use|stop\s+using|don'?t\s+use\s+\w+\s+(?:use|instead)|from\s+now\s+on|prefer\s+\w+\s+over\s+\w+)\b", re.I),
    re.compile(r"(?:అలా\s+కాదు|ఎప్పుడూ\s+వాడు|नहीं\s+मेरा\s+मतलब|हमेशा\s+इस्तेमाल)", re.I),
]

_TOOL_ERROR_MARKERS = (
    "error:",
    "failed",
    "not found",
    "does not exist",
    "no such file",
    "could not find",
    "unable to",
    "denied:",
    "timed out",
    "invalid ",
    "unknown tool",
    "traceback (most recent call last):",
)

_ANALYSIS_BLOCK_RE = re.compile(r"<(?:analysis|think)>.*?</(?:analysis|think)>\s*", re.DOTALL | re.IGNORECASE)
_OPEN_ANALYSIS_RE = re.compile(r"<(?:analysis|think)>[\s\S]*$", re.IGNORECASE)
_PARTIAL_REDACT_RE = re.compile(r"\[(?:EMAIL|PHONE|PAN|AADHAAR|CARD|USER_HOME|REDACTED)[A-Z0-9_]*$")


@dataclasses.dataclass
class TurnTrace:
    """Glass-Box Explainability receipt captured on every Celina turn (/why)."""

    prompt_preview: str = ""
    gear: str = "fast"
    gear_reason: str = ""
    swarm_role: str = "Executive Orchestrator"
    detected_lang: str = "en"
    lang_style: str = "native"
    active_window: str = ""
    tool_domains: list[str] = dataclasses.field(default_factory=list)
    tools_attached: int = 0
    tools_total: int = 0
    tools_called: list[str] = dataclasses.field(default_factory=list)
    circuit_breaker_events: list[str] = dataclasses.field(default_factory=list)
    action_gate_suppressed: int = 0
    recalled_facts: list[str] = dataclasses.field(default_factory=list)
    recalled_episodes: int = 0
    active_skill_md: str = ""
    privacy_summary: str = "clean"
    cache_status: str = "miss"
    self_corrections: int = 0
    cot_used: bool = False
    dry_run: bool = False
    simulated_actions: list[str] = dataclasses.field(default_factory=list)
    learned_notes: list[str] = dataclasses.field(default_factory=list)
    compute_backend: str = ""
    mcp_server: str = ""
    mcp_tool: str = ""
    mcp_arguments_hash: str = ""
    mcp_transport: str = ""
    memweave_strategy: str = ""
    quiet_brain_hit: bool = False
    second_thought_escalated: bool = False
    second_thought_confidence: float = 1.0
    autotune_ctx: int = 0
    autotune_keep: int = 0
    latency_ms: int = 0
    source: str = ""
    cloud_provider: str = ""
    cloud_model: str = ""

    def format_receipt(self) -> str:
        lines = ["🔍 Decision Receipt (Glass-Box Trace)"]
        if self.dry_run:
            lines.append("• Mode: 🧪 DRY-RUN SIMULATION (0 bytes modified on disk)")
        if self.prompt_preview:
            lines.append(f"• Prompt: \"{self.prompt_preview}\"")
        lines.append(f"• Gear: {self.gear.upper()} — {self.gear_reason or 'default policy'}")
        if self.swarm_role:
            lines.append(f"• Swarm Role: {self.swarm_role}")
        lines.append(f"• Source: {self.source or 'n/a'} ({self.latency_ms} ms)")
        if self.compute_backend:
            lines.append(f"• Compute: {self.compute_backend}")
        if self.memweave_strategy:
            lines.append(f"• Memory Router (MemWeave): strategy={self.memweave_strategy}")
        if self.quiet_brain_hit:
            lines.append("• Quiet Brain BM25: served compiled verified answer (0 big-model tokens)")
        if self.second_thought_escalated:
            lines.append(f"• SecondThought Gate: escalated to thinking (mean_logprob={self.second_thought_confidence:.2f} < 0.82)")
        if self.autotune_ctx:
            lines.append(f"• Autotune KV: num_ctx={self.autotune_ctx}, num_keep={self.autotune_keep}")
        lang_tag = f"{self.detected_lang} ({self.lang_style})"
        if self.active_window:
            lines.append(f"• Context: lang={lang_tag} · window={self.active_window}")
        else:
            lines.append(f"• Context: lang={lang_tag}")

        if self.tools_total > 0:
            saved_pct = int(round((1.0 - (self.tools_attached / max(1, self.tools_total))) * 100))
            doms = ", ".join(self.tool_domains) if self.tool_domains else "core"
            lines.append(
                f"• Tools Pruned: {self.tools_attached}/{self.tools_total} attached "
                f"(~{max(0, saved_pct)}% schema tokens saved · domains: {doms})"
            )
        if self.tools_called:
            lines.append(f"• Tools Executed: {' -> '.join(self.tools_called)}")
        if self.mcp_server or self.mcp_tool:
            lines.append(
                f"• MCP Dispatch: server={self.mcp_server or 'n/a'} · tool={self.mcp_tool or 'n/a'} "
                f"· transport={self.mcp_transport or 'n/a'} · arguments_hash={self.mcp_arguments_hash or 'n/a'}"
            )
        if self.circuit_breaker_events:
            lines.append(f"• Circuit Breaker: ⚡ {'; '.join(self.circuit_breaker_events[:4])}")
        if self.action_gate_suppressed > 0:
            lines.append(
                f"• ActionGate: 🛡️ suppressed {self.action_gate_suppressed} mutating tool(s) "
                "(informational/how-to question)"
            )
        if self.simulated_actions:
            lines.append(f"• Simulated (Not Executed): {'; '.join(self.simulated_actions[:6])}")
        if self.recalled_facts or self.recalled_episodes:
            f_str = ", ".join(self.recalled_facts[:5]) if self.recalled_facts else "none"
            lines.append(
                f"• Memory Recalled (BM25): facts=[{f_str}], episodes={self.recalled_episodes}"
            )
        if self.active_skill_md:
            lines.append(f"• Active Playbook: {self.active_skill_md}")
        lines.append(f"• Privacy: {self.privacy_summary} · Cache: {self.cache_status}")
        if self.self_corrections > 0 or self.cot_used:
            extras = []
            if self.self_corrections > 0:
                extras.append(f"self-corrections={self.self_corrections}")
            if self.cot_used:
                extras.append("local-3B <analysis> CoT")
            lines.append(f"• Reflection: {', '.join(extras)}")
        if self.learned_notes:
            lines.append(f"• Continual Learning: {'; '.join(self.learned_notes)}")
        return "\n".join(lines)


def _get_base_system_prompt() -> str:
    return config.format_persona_instructions()


def _has_cloud_key() -> bool:
    return any(row["configured"] for row in provider_router.status())


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def classify_cognitive_gear_with_reason(prompt: str) -> tuple[str, str, str]:
    """
    <1 ms System-1 / System-2 classifier driven by config/neural-schema.json.
    Returns (gear_name, cleaned_prompt, explanation_reason).
    """
    cog = config.COGNITION_CFG
    if not cog.get("adaptive_thinking", True):
        return "tactical", prompt, "adaptive thinking disabled in schema"

    text = prompt.strip()
    low = text.lower()

    # 1. Explicit user prefix overrides ('quick: ...' or 'deep: ...')
    for pref in cog.get("fast_overrides", ["quick:", "fast:", "brief:"]):
        if low.startswith(pref):
            return "fast", text[len(pref) :].strip() or text, f"explicit fast override '{pref}'"
    for pref in cog.get("deep_overrides", ["deep:", "think deeply"]):
        if low.startswith(pref):
            return "deep", text[len(pref) :].strip() or text, f"explicit deep override '{pref}'"
        if pref in low and not pref.endswith(":"):
            return "deep", text, f"explicit deep override '{pref}'"

    # 2. High-stakes / complex triggers -> Deep Strategist (Gear 3)
    matched_deep = [kw for kw in cog.get("deep_triggers", []) if kw in low]
    has_code_or_trace = ("```" in text) or ("traceback" in low) or (text.count("\n") >= 4)
    word_count = len(text.split())
    if matched_deep:
        return "deep", text, f"matched deep trigger '{matched_deep[0]}'"
    if has_code_or_trace:
        return "deep", text, "multi-line code or traceback detected"
    if word_count >= 45:
        return "deep", text, f"long complex query ({word_count} words)"

    # 3. Actionable / advisory / OS-tool triggers -> Tactical Advisor (Gear 2)
    matched_tact = [kw for kw in cog.get("tactical_triggers", []) if kw in low]
    if matched_tact:
        return "tactical", text, f"matched tactical trigger '{matched_tact[0]}'"
    if word_count >= 16:
        return "tactical", text, f"medium prompt ({word_count} words)"

    # 4. Short, simple query -> Fast Instinct (Gear 1, deep thinking OFF!)
    return "fast", text, f"short direct prompt ({word_count} words)"


def classify_cognitive_gear(prompt: str) -> tuple[str, str]:
    gear, clean, _ = classify_cognitive_gear_with_reason(prompt)
    return gear, clean


def select_swarm_role(prompt: str, gear: str) -> tuple[str, str]:
    """
    Zero-RAM Role-Swapping Multi-Agent Swarm (<1 ms):
    Dynamically selects Architect / Engineer / Researcher / Executive Orchestrator
    within the single active LLM using word-boundary trigger scoring.
    """
    if gear == "fast":
        return "Fast Instinct", ""
    low = (prompt or "").lower()
    roles = config.SWARM_ROLES or {}
    best_role: Optional[tuple[str, str]] = None
    best_score = 0
    for role_key in ("engineer", "architect", "researcher"):
        rcfg = roles.get(role_key) or {}
        score = sum(
            1
            for trig in rcfg.get("triggers", [])
            if re.search(rf"\b{re.escape(str(trig).lower())}\b", low)
        )
        if score > best_score:
            best_score = score
            best_role = (str(rcfg.get("name", role_key.title())), str(rcfg.get("directive", "")))
    if best_role is not None:
        return best_role
    if gear == "deep" and "architect" in roles:
        rcfg = roles["architect"]
        return str(rcfg.get("name", "The Architect")), str(rcfg.get("directive", ""))
    return "Executive Orchestrator", ""


def _gear_settings(gear: str) -> dict[str, Any]:
    gears = config.COGNITION_CFG.get("gears", {})
    defaults = {
        "fast": {"reasoning_effort": "none", "max_tokens": 220, "temperature": 0.35, "max_steps": 2, "instruction": ""},
        "tactical": {"reasoning_effort": "low", "max_tokens": 550, "temperature": 0.45, "max_steps": 4, "instruction": ""},
        "deep": {"reasoning_effort": "high", "max_tokens": 1500, "temperature": 0.3, "max_steps": 5, "instruction": ""},
    }
    return gears.get(gear) or defaults.get(gear, defaults["tactical"])


def _is_tool_error(output: str) -> bool:
    low = (output or "").strip().lower()
    if not low:
        return True
    if low.startswith("cancelled by user"):
        return False
    return any(low.startswith(m) or f"\n{m}" in low for m in _TOOL_ERROR_MARKERS) or (
        "not found" in low[:120] and len(low) < 240
    )


def _build_self_correction_hint(fn_name: str, output: str, dup_failures: int) -> str:
    max_dups = int(config.REFLECTION_POLICY.get("max_identical_tool_failures", 2))
    low = (output or "").lower()
    if dup_failures >= max_dups:
        return (
            f"\n[CRITIC VERIFIER — SELF-CORRECTION REQUIRED]: '{fn_name}' failed {dup_failures} times with identical arguments. "
            "Do NOT retry the same call. Switch to a different tool/strategy or concisely explain the blocker to the user."
        )
    if fn_name in {"read_file", "edit_file", "trash_file", "edit_document", "read_document"} or "no such file" in low:
        return (
            "\n[CRITIC VERIFIER — SELF-CORRECTION REQUIRED]: That path failed. Call search_files first to locate the exact file path before retrying."
        )
    if fn_name in {"click_on_text", "click_position", "drag_and_drop"}:
        return (
            "\n[CRITIC VERIFIER — SELF-CORRECTION REQUIRED]: That UI target was not found. Call inspect_ui_elements or read_screen to read exact live coordinates/labels before clicking again."
        )
    if fn_name == "run_python_sandbox" or "traceback" in low:
        return (
            "\n[CRITIC VERIFIER — ENGINEER CODE SELF-CORRECTION]: Your Python snippet raised a runtime error/traceback. "
            "Read the stderr carefully, fix the bug in your code, and re-run run_python_sandbox to verify exit=0 before giving your final answer."
        )
    return (
        f"\n[CRITIC VERIFIER — SELF-CORRECTION REQUIRED]: Tool '{fn_name}' returned an error (sensory-motor mismatch). Analyze the error, adjust arguments or try an alternate tool once."
    )


def _detect_implicit_correction(prompt: str) -> Optional[tuple[str, str]]:
    """Detects explicit user correction/preference statements so Celina remembers them permanently."""
    clean = prompt.strip()
    if len(clean) < 12 or len(clean) > 220:
        return None
    if any(p.search(clean) for p in _IMPLICIT_CORRECTION_PATTERNS):
        digest = hashlib.sha1(clean.lower().encode("utf-8")).hexdigest()[:6]
        return f"preference_{digest}", clean
    return None


_SIDE_EFFECT_TIER1_TOOLS: frozenset[str] = frozenset({
    "set_volume", "set_brightness", "control_media", "control_connectivity",
    "open_application", "toggle_app_nap", "open_document", "set_clipboard",
    "set_timer", "remember_fact", "link_knowledge", "manage_goal",
    "save_workspace_snapshot", "set_assistant_identity", "set_focus_mode",
    "route_app_audio", "learn_skill", "save_last_actions_as_skill",
    "update_skill", "forget_skill", "start_recording", "stop_recording",
    "reject_quarantined_skill", "clear_cache",
})

_INFO_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"how\s+(?:do\s+(?:i|you|we)|does|to|can\s+(?:i|you|we)|is|are|would)\b|"
    r"what\s+(?:is|are|does|do|was|were|would|happens\s+if)\b|"
    r"why\s+(?:does|do|is|are|did|would|should)\b|"
    r"explain(?:\s+how|\s+what|\s+why|\s+the)?\b|"
    r"tell\s+me\s+(?:about|how|what|why)\b|"
    r"difference\s+between\b|"
    r"compare\b|"
    r"can\s+you\s+explain\b|"
    r"define\b|"
    r"what's\s+the\s+(?:difference|meaning|purpose)\b"
    r")",
    re.IGNORECASE,
)
_EXPLICIT_IMPERATIVE_RE = re.compile(
    r"\b(?:"
    r"please\s+(?:set|open|close|delete|trash|move|edit|write|create|run|click|type|enable|disable|turn|start|stop|launch|route|schedule|mirror)|"
    r"for\s+me\b|right\s+now\b|my\s+(?:file|folder|calendar|meeting|volume|brightness|wifi|bluetooth|screen|focus|audio|clipboard|contacts|bookmarks|laptop|pc|system)"
    r")\b",
    re.IGNORECASE,
)


def is_informational_only_prompt(prompt: str) -> bool:
    """
    Deterministic ActionGate (inspired by lumina-agent & BFCL restraint optimization):
    Returns True when the prompt is clearly an educational / conceptual / how-to question
    (e.g. 'how do you delete a file in linux?', 'what does recursive deletion do?', 'explain focus mode')
    with no imperative request to mutate the user's machine.
    """
    clean = (prompt or "").strip()
    if not clean or len(clean) < 8:
        return False
    if not _INFO_QUESTION_PREFIX_RE.search(clean):
        return False
    if _EXPLICIT_IMPERATIVE_RE.search(clean):
        return False
    return True


def apply_action_gate(
    prompt: str,
    active_tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """
    Suppresses mutating/side-effecting tools on clearly informational questions so small
    local models (1.7B–4B) never over-eagerly fire actions during 'how do I...' Q&A.
    Returns (filtered_tools, suppressed_count).
    """
    if not config.ACTION_GATE_ENABLED or not active_tools:
        return active_tools, 0
    if not is_informational_only_prompt(prompt):
        return active_tools, 0
    blocked = config.TIER2_APPROVAL_TOOLS | _SIDE_EFFECT_TIER1_TOOLS
    kept = [t for t in active_tools if t["function"]["name"] not in blocked]
    suppressed = len(active_tools) - len(kept)
    return kept, suppressed


def _strip_analysis_tags(text: str) -> str:
    cleaned = _ANALYSIS_BLOCK_RE.sub("", text or "").strip()
    return cleaned if cleaned else (text or "").strip()


def _format_stream_preview(raw_text: str, vault: Optional[privacy.RedactionVault] = None) -> str:
    """Strips complete or in-progress <analysis> blocks and partial redaction tokens during streaming."""
    cleaned = _ANALYSIS_BLOCK_RE.sub("", raw_text or "")
    cleaned = _OPEN_ANALYSIS_RE.sub("", cleaned)
    if vault is not None:
        cleaned = vault.restore(cleaned)
    cleaned = _PARTIAL_REDACT_RE.sub("", cleaned)
    return cleaned.strip()


def _build_pooled_session() -> requests.Session:
    """Creates a persistent HTTP/1.1 keep-alive Session to eliminate repeat TCP/TLS handshakes."""
    sess = requests.Session()
    adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


def _recommended_ollama_threads(cpu_count: Optional[int] = None) -> int:
    """Computes physical-core-friendly inference thread count to avoid SMT/hyperthread contention."""
    if config.OLLAMA_NUM_THREADS > 0:
        return max(1, int(config.OLLAMA_NUM_THREADS))
    logical = cpu_count if cpu_count is not None else (os.cpu_count() or 4)
    if logical <= 4:
        return max(1, logical)
    return max(4, min(8, logical // 2))


def _ollama_ctx_for_gear(
    gear: str,
    base_ctx: Optional[int] = None,
    total_ram_gb: Optional[int] = None,
) -> int:
    """
    Adaptive context window to prevent KV-cache RAM spikes & disk thrashing:
    - On <= 10GB RAM laptops: 1536 (fast) / 2048 (tactical/deep) saves ~500-900MB RAM.
    - On 12GB+ systems: 2048 (fast) / full OLLAMA_NUM_CTX (tactical/deep).
    """
    ctx = int(base_ctx if base_ctx is not None else config.OLLAMA_NUM_CTX)
    ram = int(total_ram_gb if total_ram_gb is not None else config.detect_system_ram_gb())
    if ram <= 10:
        cap = 1536 if gear == "fast" else 2048
        return max(1024, min(ctx, cap))
    if gear == "fast":
        return max(1024, min(ctx, 2048))
    return max(1024, ctx)


def _effective_ollama_keep_alive(available_ram_mb: Optional[int] = None) -> Any:
    """
    Dynamically tightens Ollama keep_alive under real RAM pressure (or Zero-Lag Turbo/Lite Mode)
    so models never hold 2-4GB hostage while browsing/working and never trigger ZRAM swap churn:
      - CELINA_LITE_MODE=1       -> 0 (evict immediately once reply completes!)
      - MemAvailable < 1200 MiB  -> 0 (evict immediately once reply completes!)
      - MemAvailable < 2200 MiB  -> "15s" (brief follow-up window, then free RAM)
      - MemAvailable >= 2200 MiB -> config.OLLAMA_KEEP_ALIVE (default "45s")
    """
    if config.is_lite_mode():
        return 0
    avail = int(available_ram_mb if available_ram_mb is not None else config.detect_available_ram_mb())
    if avail < 1200:
        return 0
    if avail < 2200:
        return "15s"
    return config.OLLAMA_KEEP_ALIVE


class CelinaRouter:
    def __init__(self, db: Optional[memory.MemoryDB] = None, response_cache: Optional[cache.SmartCache] = None):
        self.db = db if db is not None else memory.get_db()
        self._memory: memory.CelinaMemory = self.db.get_hybrid_memory()
        self.cache = response_cache if response_cache is not None else cache.get_cache()
        self.last_trace: Optional[TurnTrace] = None
        self._http: requests.Session = _build_pooled_session()
        self._last_warm_ts: float = 0.0
        self._compute_why: Optional[str] = None
        self._session_tier: Optional[int] = None
        self._supervisor: Optional[Any] = None
        skills.seed_builtin_skills(self.db)

    def get_supervisor(self) -> Any:
        """Lazily initializes Phase-1 AsyncSupervisor (0 MB RAM until `/bg` or `/jobs` is used)."""
        if self._supervisor is None:
            from . import evidence
            from .autonomy.supervisor import AsyncSupervisor

            self._supervisor = AsyncSupervisor(
                router=self,
                ledger=evidence.EvidenceLedger(),
            )
        return self._supervisor

    # ------------------------------------------------------------------
    # MemWeave 3-Way Memory Router (keyword / vector / graph / hybrid)
    # ------------------------------------------------------------------
    @staticmethod
    def _is_factual(query: str) -> bool:
        q = (query or "").strip().lower()
        return bool(
            re.search(r'["\'][^"\']+["\']|\b(?:exact|port|path|command|version|date|email|filename|config\s+key|what\s+is\s+my)\b', q)
            or re.search(r"[/\\][\w.-]+\.[a-z0-9]{1,5}\b", q)
        )

    @staticmethod
    def _is_associative(query: str) -> bool:
        q = (query or "").strip().lower()
        return bool(
            re.search(r"\b(?:similar\s+to|like|reminds?\s+me|something\s+about|roughly|fuzzy|conceptually|vibe|related\s+idea)\b", q)
        )

    @staticmethod
    def _is_relational(query: str) -> bool:
        q = (query or "").strip().lower()
        return bool(
            re.search(
                r"\b(?:why\s+did|after\s+that|before\s+that|weeks?\s+after|days?\s+after|connected\s+to|linked\s+to|depends?\s+on|led\s+to|caused\s+by|who\s+worked\s+with|prior\s+attempts?|failed\s+with)\b",
                q,
            )
        )

    def _retrieval_strategy(self, query: str) -> str:
        """MemWeave 3-way dispatch: selects keyword (FTS5), vector, graph, or RRF hybrid."""
        hits: list[str] = []
        if self._is_factual(query):
            hits.append("keyword")
        if self._is_associative(query):
            hits.append("vector")
        if self._is_relational(query):
            hits.append("graph")
        if len(hits) == 1:
            return hits[0]
        return "hybrid"

    def retrieve_context(
        self,
        query: str,
        *,
        k: int = 8,
        trace: Optional[TurnTrace] = None,
    ) -> list[dict[str, Any]]:
        """Single unified hybrid retrieval entrypoint fusing vector + FTS5 + graph via RRF."""
        strategy = self._retrieval_strategy(query)
        if trace is not None:
            trace.memweave_strategy = strategy
        return self._memory.recall(query, k=k, strategy=strategy)

    # ------------------------------------------------------------------
    # "The Brain That Goes Quiet" — BM25 Compiled Knowledge Router
    # ------------------------------------------------------------------
    def route_with_quiet_brain(
        self,
        query: str,
        *,
        confidence_threshold: float = 0.82,
        trace: Optional[TurnTrace] = None,
    ) -> dict[str, Any]:
        """
        Queries offline-compiled verified answers via SQLite FTS5 BM25 first.
        Only escalates to the runtime model when confidence < `confidence_threshold`.
        """
        res = self._memory.route_bm25_compiled(query, confidence_threshold=confidence_threshold)
        if res.get("hit") and trace is not None:
            trace.quiet_brain_hit = True
            trace.source = f"quiet_brain_bm25 ({res.get('source_model', 'compiled')})"
        return res

    # ------------------------------------------------------------------
    # SecondThought Confidence-Gated Reasoning (mean_logprob < 0.82)
    # ------------------------------------------------------------------
    @staticmethod
    def extract_mean_logprob(response_payload: dict[str, Any]) -> float:
        """
        Extracts normalized confidence in [0.0, 1.0] from an Ollama/OpenAI
        response's token `logprobs` (exp(mean_logprob)), or falls back to
        explicit `mean_logprob` / `confidence` fields when provided.
        """
        if "mean_logprob" in response_payload and response_payload["mean_logprob"] is not None:
            val = float(response_payload["mean_logprob"])
            return math.exp(val) if val <= 0.0 else max(0.0, min(1.0, val))
        lps = response_payload.get("logprobs")
        if isinstance(lps, list) and lps:
            vals = []
            for item in lps:
                if isinstance(item, (int, float)):
                    vals.append(float(item))
                elif isinstance(item, dict) and "logprob" in item:
                    vals.append(float(item["logprob"]))
            if vals:
                avg_lp = sum(vals) / len(vals)
                return max(0.0, min(1.0, math.exp(avg_lp) if avg_lp <= 0.0 else avg_lp))
        return 1.0

    def call_with_second_thought(
        self,
        goal: str,
        *,
        no_think_fn: Callable[[str], dict[str, Any]],
        think_fn: Callable[[str], dict[str, Any]],
        threshold: float = 0.82,
        trace: Optional[TurnTrace] = None,
    ) -> dict[str, Any]:
        """
        SecondThought confidence-gated reasoning (~50% fewer output tokens while
        preventing 'thinking talks the model into wrong tool calls'):
          1. Run fast no-think tool call first.
          2. Read calibrated mean token probability (`mean_logprob`).
          3. Re-run with thinking ONLY when confidence < `threshold` (default 0.82).
        """
        fast_res = dict(no_think_fn(goal) or {})
        conf = self.extract_mean_logprob(fast_res)
        fast_res["mean_logprob"] = round(conf, 4)
        fast_res["used_thinking"] = False
        if trace is not None:
            trace.second_thought_confidence = round(conf, 4)

        if conf < float(threshold):
            deep_res = dict(think_fn(goal) or {})
            deep_res["mean_logprob"] = round(conf, 4)
            deep_res["used_thinking"] = True
            deep_res["escalated_from_fast"] = True
            if trace is not None:
                trace.second_thought_escalated = True
            return deep_res
        return fast_res

    def _get_compute_why(self) -> str:
        if self._compute_why is None:
            try:
                from celina_hardware.detect_gpu import select_compute_backend

                self._compute_why = select_compute_backend().to_why_line()
            except Exception:
                self._compute_why = "compute_backend=cpu (default)"
        return self._compute_why

    def warm_local_model_async(
        self,
        *,
        total_ram_gb: Optional[int] = None,
        available_ram_mb: Optional[int] = None,
    ) -> bool:
        """
        Non-blocking Ollama weight pre-warm ONLY when plenty of free RAM exists.
        Strictly disabled on <= 10GB RAM laptops or when MemAvailable < 3500 MiB
        so opening Dynamic Island NEVER causes unnecessary disk reads or swap spikes!
        """
        ram_gb = int(total_ram_gb if total_ram_gb is not None else config.detect_system_ram_gb())
        avail_mb = int(available_ram_mb if available_ram_mb is not None else config.detect_available_ram_mb())
        if ram_gb <= 10 or avail_mb < 3500:
            return False
        now = time.monotonic()
        if now - self._last_warm_ts < 90.0:
            return False
        self._last_warm_ts = now

        def _ping() -> None:
            try:
                self._http.post(
                    f"{config.OLLAMA_API_BASE}/api/generate",
                    json={"model": config.LOCAL_MODEL, "keep_alive": _effective_ollama_keep_alive()},
                    timeout=(1.5, 4.0),
                )
            except Exception:
                pass

        threading.Thread(target=_ping, daemon=True).start()
        return True

    def get_last_receipt(self) -> str:
        if self.last_trace is None:
            return f"No turn trace recorded yet. Ask {config.get_assistant_name()} a question first, then run /why!"
        return self.last_trace.format_receipt()

    @telemetry.agent_span("router", self_attr="tracer", governance_tier="routing")
    def route_for_autonomy(self, goal: str, context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """
        Autonomy-aware routing entrypoint returning the structured decision dict
        consumed by Celina v5 `AutonomyEngine`.
        """
        import hashlib
        from . import approval

        ctx = context or {}
        clean_goal = (goal or "").strip()

        # Gear 0 — deterministic AST math (0 LLM tokens, eval-free)
        math_ans = try_gear0_math(clean_goal)
        if math_ans is not None:
            args = {"text": math_ans}
            return {
                "goal": clean_goal,
                "gear": "reflex",
                "gear_reason": "Gear 0 deterministic AST math (0 tokens)",
                "tool_name": "respond_directly",
                "arguments": args,
                "arguments_hash": approval.compute_arguments_hash("respond_directly", args),
                "tier": 1,
                "domain": "os_hardware",
                "why_receipt_hash": hashlib.sha256(f"gear0_math:{clean_goal}={math_ans}".encode("utf-8")).hexdigest(),
            }

        active_win = str(ctx.get("active_window") or "")
        gear, clean_goal, gear_reason = classify_cognitive_gear_with_reason(clean_goal)
        selected_tools, domains = tools.get_contextual_tool_schemas(
            clean_goal, gear=gear, active_window=active_win, is_recording=False
        )

        # Pick highest-ranked matching tool, honoring retry suggested_fallback & avoiding known failures
        chosen_tool = "get_system_stats"
        known_tools = config.TIER1_AUTO_TOOLS | config.TIER2_APPROVAL_TOOLS
        failed_tools = {str(x).strip() for x in (ctx.get("known_failed_attempts") or []) if str(x).strip()}
        suggested_fb = str(ctx.get("suggested_fallback") or "").strip()
        if not suggested_fb:
            last_err = str(ctx.get("last_error") or "")
            m_fb = re.search(r"suggested_fallback=([A-Za-z0-9_]+)", last_err)
            if m_fb:
                suggested_fb = m_fb.group(1)

        if suggested_fb and suggested_fb in known_tools and suggested_fb not in failed_tools:
            chosen_tool = suggested_fb
        elif selected_tools:
            # Prefer explicit tool name mention in goal if present (and not already failed)
            goal_low = clean_goal.lower()
            candidates = [t["function"]["name"] for t in selected_tools]
            fresh_candidates = [c for c in candidates if c not in failed_tools] or candidates
            explicit = [c for c in fresh_candidates if c in goal_low]
            chosen_tool = explicit[0] if explicit else fresh_candidates[0]

        tier = 2 if chosen_tool in config.TIER2_APPROVAL_TOOLS else 1
        domain = domains[0] if domains else "os_hardware"
        # Detect explicit web_comms or files_office domain from chosen tool when multiple domains matched
        for dname, dcfg in (config.TOOL_DOMAINS or {}).items():
            if chosen_tool in (dcfg.get("tools") or ()):
                domain = dname
                break
        arguments = dict(ctx.get("arguments") or {})

        raw_decision = RouteDecision(
            tool_name=chosen_tool,
            domain=domain,
            tier=tier,
            arguments=arguments,
            gear=gear,
        )
        resolved = apply_offline_and_uno_policy(
            raw_decision,
            goal=clean_goal,
            online_probe=ctx.get("online_probe"),
            uno_probe=ctx.get("uno_probe"),
        )
        if isinstance(resolved, OfflineFallback):
            trace = TurnTrace(
                prompt_preview=clean_goal[:80],
                gear=gear,
                gear_reason=gear_reason,
                active_window=active_win,
                tool_domains=list(domains),
                tools_attached=len(selected_tools),
                tools_called=[resolved.tool],
                compute_backend=self._get_compute_why(),
                source="offline_fallback",
            )
            self.last_trace = trace
            why_hash = hashlib.sha256(trace.format_receipt().encode("utf-8")).hexdigest()
            return {
                "tool_name": resolved.tool,
                "arguments": {"message": resolved.message},
                "arguments_hash": approval.compute_arguments_hash(resolved.tool, {"message": resolved.message}),
                "tier": 1,
                "domain": resolved.domain,
                "why_receipt_hash": why_hash,
                "gear": gear,
                "offline_fallback": True,
                "message": resolved.message,
                "preferred_backend": "offline",
            }

        chosen_tool = resolved.tool_name
        domain = resolved.domain
        tier = resolved.tier
        args_hash = approval.compute_arguments_hash(chosen_tool, arguments)

        trace = TurnTrace(
            prompt_preview=clean_goal[:80],
            gear=gear,
            gear_reason=gear_reason,
            active_window=active_win,
            tool_domains=list(domains),
            tools_attached=len(selected_tools),
            tools_called=[chosen_tool],
            compute_backend=self._get_compute_why(),
            source="autonomy_router",
        )
        self.last_trace = trace
        why_hash = hashlib.sha256(trace.format_receipt().encode("utf-8")).hexdigest()

        return {
            "tool_name": chosen_tool,
            "arguments": arguments,
            "arguments_hash": args_hash,
            "tier": tier,
            "domain": domain,
            "why_receipt_hash": why_hash,
            "gear": gear,
            "preferred_backend": resolved.preferred_backend,
        }

    def _build_messages(
        self,
        user_prompt: str,
        gear: str,
        language: str = "auto",
        for_cloud: bool = False,
        active_window: str = "",
        det_lang: str = "en",
        lang_style: str = "native",
        trace: Optional[TurnTrace] = None,
    ) -> list[dict[str, Any]]:
        now_str = datetime.datetime.now().strftime("%A, %Y-%m-%d %H:%M")
        gcfg = _gear_settings(gear)
        sys_parts = [
            _get_base_system_prompt(),
            f"Current local date & time: {now_str}.",
        ]
        if active_window:
            sys_parts.append(f"Active desktop window: {active_window}")
        if gcfg.get("instruction"):
            sys_parts.append(str(gcfg["instruction"]))

        # Zero-RAM Multi-Agent Swarm Role + System 2 Tree-of-Thought + Critic Gate
        role_name, role_directive = select_swarm_role(user_prompt, gear)
        if trace is not None:
            trace.swarm_role = role_name
        if role_directive:
            sys_parts.append(role_directive)

        if gear == "deep" and config.COGNITION_CFG.get("tree_of_thought_on_deep", True):
            sys_parts.append(
                "Tree-of-Thought Deliberation: Before committing to a high-impact action, mentally compare "
                "2 candidate approaches, weigh failure modes and reversibility, and pick the safest path."
            )

        if gear in {"tactical", "deep"}:
            if config.COGNITION_CFG.get("critic_audit_on_tier2", True):
                critic_dir = str((config.SWARM_ROLES.get("critic") or {}).get("directive", "")).strip()
                if critic_dir:
                    sys_parts.append(critic_dir)
            if config.REFLECTION_POLICY.get("enable_preflight_micro_plan", True):
                sys_parts.append(
                    "Execution discipline: Before invoking tools, silently plan the minimal 2-4 step sequence. "
                    "Verify tool outputs before declaring success; if a tool fails, reflect and correct your arguments once."
                )
            clar = str(config.CONFIDENCE_POLICY.get("clarification_instruction", "")).strip()
            if clar:
                sys_parts.append(clar)

        # Pillar 7A: Native + Romanized Tenglish / Hinglish Language Awareness (strict English lock on English turns!)
        lang_code = (language or "auto").lower()
        if lang_code != "auto" and lang_code in config.LANGUAGES:
            lmeta = config.LANGUAGES[lang_code]
            sys_parts.append(
                f"Reply strictly in {lmeta.get('name', lang_code)} "
                f"({lmeta.get('native_name', lang_code)}) using its native script."
            )
        elif lang_style == "romanized" and det_lang in {"te", "hi"}:
            l_label = "Romanized Telugu (Tenglish)" if det_lang == "te" else "Romanized Hindi (Hinglish)"
            sys_parts.append(
                f"User wrote in {l_label}. Understand naturally and reply in clear, natural "
                f"{'Telugu or Tenglish' if det_lang == 'te' else 'Hindi or Hinglish'} matching their tone."
            )
        elif det_lang in {"te", "hi"} and det_lang in config.LANGUAGES:
            lmeta = config.LANGUAGES[det_lang]
            sys_parts.append(
                f"LANGUAGE RULE: The user's latest message is in {lmeta.get('name', det_lang)}. "
                f"Reply naturally in {lmeta.get('name', det_lang)}."
            )
        else:
            sys_parts.append(
                "LANGUAGE RULE: The user's latest message is in English. "
                "Reply ONLY in English — never switch to Telugu, Hindi, or any other language."
            )

        # Pillar 3 + v4.1: SQLite FTS5 BM25 Top-K Memory + Knowledge Graph + Active Goals + Sensory Warnings
        mem_block, recalled_keys, recalled_ep_cnt = self.db.format_relevant_memory_for_prompt(
            user_prompt,
            max_facts=5 if gear != "fast" else 3,
            max_episodes=2 if gear != "fast" else 0,
            cloud_only=for_cloud,
            active_window=active_window,
        )
        if mem_block:
            sys_parts.append(mem_block)
        if trace is not None:
            trace.recalled_facts = recalled_keys
            trace.recalled_episodes = recalled_ep_cnt

        learned = self.db.format_skills_for_prompt()
        if learned:
            sys_parts.append(learned)

        # Dynamically inject ONLY the single matching TRUSTED SKILL.md playbook,
        # explicitly labeled UNTRUSTED REFERENCE CONTENT (cannot override system policy!)
        matched_md = skills.match_markdown_skill(user_prompt)
        if matched_md:
            sys_parts.append(skills.format_skill_as_untrusted_reference(matched_md))
            if trace is not None:
                trace.active_skill_md = str(matched_md["name"])

        if skills.recorder.is_recording:
            sys_parts.append(
                f"ACTIVE LEARNING MODE: Recording steps for skill '{skills.recorder.recording_name}'. "
                "Execute each action the user requests cleanly; when they say stop recording, call stop_recording."
            )

        messages: list[dict[str, Any]] = [{"role": "system", "content": "\n\n".join(sys_parts)}]
        hist_limit = 4 if gear == "fast" else config.MEMORY_HISTORY_LIMIT
        messages.extend(self.db.get_recent_messages(limit=hist_limit, cloud_only=for_cloud))
        messages.append({"role": "user", "content": user_prompt})
        return messages

    @staticmethod
    def _sanitize_messages_for_cloud(
        messages: list[dict[str, Any]], vault: privacy.RedactionVault
    ) -> list[dict[str, Any]]:
        """Redacts secrets & PII across system prompt, injected facts, and conversation history."""
        sanitized: list[dict[str, Any]] = []
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                redacted_content, _ = vault.redact(content)
                sanitized.append({**m, "content": redacted_content})
            else:
                sanitized.append(m)
        return sanitized

    def _post_with_retry(
        self,
        url: str,
        payload: dict[str, Any],
        timeout: int,
        headers: Optional[dict[str, str]] = None,
        retries: int = 3,
        stream: bool = False,
    ) -> requests.Response:
        """Executes pooled HTTP/1.1 keep-alive POST with backoff retries on transient 5xx/network blips."""
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                resp = self._http.post(url, headers=headers, json=payload, timeout=timeout, stream=stream)
                if resp.status_code in {429, 502, 503, 504} and attempt < retries - 1:
                    resp.close()
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return resp
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                if attempt == retries - 1:
                    raise
                time.sleep(1.5 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def _call_provider_chat(
        self,
        provider: Any,
        api_key: str,
        model: str,
        prompt: str,
        timeout: int = 12,
    ) -> dict[str, Any]:
        """Call a single provider's chat/completions endpoint and return a result dict."""
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if provider.name == "openrouter":
            headers.update({
                "HTTP-Referer": "https://celina-local.ubuntu",
                "X-Title": f"{config.get_assistant_name()} Desktop Assistant",
            })
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": prompt}],
            "model": model,
            "max_tokens": 550,
            "temperature": 0.45,
            "stream": False,
        }
        try:
            resp = self._post_with_retry(
                provider.endpoint, payload, timeout=timeout, headers=headers, retries=1,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                provider_router.mark_failure(provider.name, f"HTTP {resp.status_code}")
                resp.close()
                return {"provider": provider.name, "model": model, "response": "", "error": f"HTTP {resp.status_code}"}
            data = resp.json()
            provider_router.mark_success(provider.name)
            text = ""
            choices = data.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                text = msg.get("content", "") or ""
            resp.close()
            return {"provider": provider.name, "model": model, "response": text, "error": None}
        except Exception as exc:
            provider_router.mark_failure(provider.name, type(exc).__name__)
            return {"provider": provider.name, "model": model, "response": "", "error": type(exc).__name__}

    def _run_ensemble(
        self,
        messages: list[dict[str, Any]],
        gear: str,
        vault: Optional[privacy.RedactionVault] = None,
    ) -> dict[str, Any]:
        """
        Run task decomposition + parallel multi-provider ensemble.

        Splits the user task into subtasks, sends each to its best provider
        in parallel, merges results into one coherent answer.
        """
        # Extract user prompt
        prompt = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content", "")
                prompt = content if isinstance(content, str) else str(content)
                break

        # Get subtask -> provider -> model assignments
        candidates = provider_router.ensemble_candidates(prompt, gear)
        if not candidates:
            return {
                "choices": [{"message": {"role": "assistant", "content": ""}}],
                "_celina_provider": "ensemble",
                "_celina_model": "none",
            }

        # Run subtasks in parallel using a thread pool
        results: list[dict[str, Any]] = []
        timeout_cap = 20 if gear == "deep" else 12
        request_timeout = max(3, min(int(config.REQUEST_TIMEOUT), timeout_cap))

        with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
            future_to_candidate = {}
            for provider, api_key, model, subtask in candidates:
                future = executor.submit(
                    self._call_provider_chat, provider, api_key, model, subtask, request_timeout
                )
                future_to_candidate[future] = (provider.name, subtask)

            for future in as_completed(future_to_candidate):
                provider_name, subtask = future_to_candidate[future]
                try:
                    result = future.result()
                    result["subtask"] = subtask
                    results.append(result)
                except Exception as exc:
                    results.append({
                        "provider": provider_name,
                        "model": "",
                        "subtask": subtask,
                        "response": "",
                        "error": type(exc).__name__,
                    })

        # Merge results into one coherent answer
        merged = provider_router.merge_ensemble_results(results)

        return {
            "choices": [{"message": {"role": "assistant", "content": merged}}],
            "_celina_provider": "ensemble",
            "_celina_model": "multi",
        }

    def _execute_gear05_intent(
        self,
        micro: Any,
        query: str,
        dry_run: bool = False,
    ) -> Optional[str]:
        """
        Execute a Gear 0.5 micro-intent result deterministically through Gear 0 handlers.

        The micro-LLM only classifies intent + slots; Gear 0 executes the actual OS action.
        This guarantees safety — the LLM never touches system APIs directly.
        """
        intent = micro.intent
        slots = micro.slots or {}

        # Build a synthetic query that matches the Gear 0 handler's regex patterns
        synthetic = ""

        if intent == "timer":
            amount = slots.get("amount", 0)
            unit = slots.get("unit", "minutes")
            synthetic = f"set a timer for {amount} {unit}"
        elif intent == "remind_in":
            amount = slots.get("amount", 0)
            unit = slots.get("unit", "minutes")
            msg = slots.get("message", "Reminder")
            synthetic = f"remind me in {amount} {unit} to {msg}"
        elif intent == "remind_at":
            msg = slots.get("message", "Reminder")
            time_str = slots.get("time", "")
            synthetic = f"remind me to {msg} at {time_str}"
        elif intent == "theme":
            mode = slots.get("mode", "dark")
            synthetic = f"switch to {mode} mode"
        elif intent == "dnd":
            enabled = slots.get("enabled", True)
            synthetic = f"turn {'on' if enabled else 'off'} dnd"
        elif intent == "night_light":
            enabled = slots.get("enabled", True)
            synthetic = f"turn {'on' if enabled else 'off'} night light"
        elif intent == "volume":
            if "level" in slots:
                synthetic = f"set volume to {slots['level']}%"
            elif "direction" in slots:
                synthetic = f"volume {slots['direction']}"
        elif intent == "brightness":
            level = slots.get("level", 50)
            synthetic = f"set brightness to {level}%"
        elif intent == "open_app":
            app = slots.get("app", "")
            synthetic = f"open {app}"
        elif intent == "call":
            alias = slots.get("alias", "")
            synthetic = f"call {alias}"
        elif intent == "text":
            alias = slots.get("alias", "")
            msg = slots.get("message", "")
            synthetic = f"text {alias} saying {msg}"
        elif intent == "open_my":
            alias = slots.get("alias", "")
            synthetic = f"open my {alias}"
        elif intent == "save":
            alias = slots.get("alias", "")
            value = slots.get("value", "")
            kind = slots.get("kind", "contact")
            synthetic = f"save {alias} as {value} {kind}"
        elif intent in ("turn_on", "turn_off"):
            device = slots.get("device", "")
            action = "on" if intent == "turn_on" else "off"
            synthetic = f"turn {action} {device}"
        elif intent == "set_thermostat":
            temp = slots.get("temp", "72")
            synthetic = f"set thermostat to {temp}"
        elif intent == "mute_when_busy":
            enabled = slots.get("enabled", True)
            synthetic = f"mute notifications when im in a meeting{' off' if not enabled else ''}"
        elif intent == "remind_when_home":
            msg = slots.get("message", "")
            synthetic = f"remind me to {msg} when I get home"

        if not synthetic:
            return None

        # Route through Gear 0 modules deterministically
        handlers = [
            (gear0_intent.gear0_intent_classify, gear0_intent.gear0_intent_execute),
            (gear0_graph.gear0_graph_classify, gear0_graph.gear0_graph_execute),
            (gear0_iot.gear0_iot_classify, gear0_iot.gear0_iot_execute),
            (gear0_context.gear0_context_classify, gear0_context.gear0_context_execute),
        ]

        for classify_fn, execute_fn in handlers:
            handler = classify_fn(synthetic)
            if handler:
                result = execute_fn(handler, synthetic, dry_run=dry_run)
                if result is not None:
                    return result

        return None

    def _post_openrouter(
        self,
        messages: list[dict[str, Any]],
        gear: str,
        use_tools: bool = True,
        active_tools: Optional[list[dict[str, Any]]] = None,
        stream_cb: Optional[StreamCallback] = None,
        vault: Optional[privacy.RedactionVault] = None,
    ) -> dict[str, Any]:
        """Call a task-ranked provider with bounded, rate-limit-aware failover.

        The legacy method name is retained for compatibility with callers/tests;
        requests use the configured provider registry. No credential is logged.
        """
        # --- Automatic ensemble routing (Update 18) ---
        # Celina analyses the task and decides: parallel multi-provider ensemble
        # vs single-provider failover. No env var or user config needed — just
        # add API keys and Celina handles model discovery + routing automatically.
        want_stream = stream_cb is not None
        if not use_tools and not want_stream:
            prompt = ""
            for message in reversed(messages):
                if message.get("role") == "user":
                    content = message.get("content", "")
                    prompt = content if isinstance(content, str) else str(content)
                    break
            if prompt and provider_router.should_use_ensemble(prompt):
                return self._run_ensemble(messages, gear, vault=vault)

        gcfg = _gear_settings(gear)
        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": int(gcfg.get("max_tokens", 550)),
            "temperature": float(gcfg.get("temperature", 0.45)),
        }
        effort = str(gcfg.get("reasoning_effort", "none"))
        if effort in {"none", "low", "medium", "high"}:
            payload["reasoning"] = {"effort": effort}
        if want_stream:
            payload["stream"] = True
        tool_list = active_tools if active_tools is not None else tools.get_enabled_tool_schemas()
        if use_tools and tool_list:
            payload["tools"] = tool_list
            payload["tool_choice"] = "auto"

        prompt = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content", "")
                prompt = content if isinstance(content, str) else str(content)
                break
        candidates = provider_router.candidates(prompt, gear)
        if not candidates:
            raise RuntimeError("No cloud provider API key configured. Set one or more provider keys in ~/.ai-env.")

        # Bound worst-case delay: at most three providers per model/tool step, no
        # stacked retries or sleep on 429. Deep tasks receive a larger per-call cap.
        timeout_cap = 20 if gear == "deep" else 12
        request_timeout = max(3, min(int(config.REQUEST_TIMEOUT), timeout_cap))
        errors: list[str] = []
        for provider, api_key in candidates[:3]:
            attempt_payload = dict(payload)
            attempt_payload["model"] = provider_router.model_for(provider)
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            if provider.name == "openrouter":
                headers.update({
                    "HTTP-Referer": "https://celina-local.ubuntu",
                    "X-Title": f"{config.get_assistant_name()} Desktop Assistant",
                })
            url = provider.endpoint
            resp = None
            try:
                # One network attempt per provider prevents retry multiplication;
                # transient errors move immediately to the next configured service.
                resp = self._post_with_retry(
                    url, attempt_payload, timeout=request_timeout, headers=headers,
                    retries=1, stream=want_stream,
                )
                if resp.status_code in {400, 404, 422}:
                    # Some compatible endpoints reject optional reasoning metadata
                    # or tool schemas. Retry once after progressively removing them.
                    resp.close()
                    attempt_payload.pop("reasoning", None)
                    resp = self._post_with_retry(
                        url, attempt_payload, timeout=request_timeout, headers=headers,
                        retries=1, stream=want_stream,
                    )
                    if use_tools and resp.status_code in {400, 404, 422}:
                        resp.close()
                        attempt_payload.pop("tools", None)
                        attempt_payload.pop("tool_choice", None)
                        resp = self._post_with_retry(
                            url, attempt_payload, timeout=request_timeout, headers=headers,
                            retries=1, stream=want_stream,
                        )
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after = 0.0
                    try:
                        retry_after = float(resp.headers.get("Retry-After", "0"))
                    except (TypeError, ValueError):
                        pass
                    reason = f"HTTP {resp.status_code}"
                    provider_router.mark_failure(provider.name, reason, retry_after)
                    errors.append(f"{provider.name}: {reason}")
                    resp.close()
                    continue
                resp.raise_for_status()
                if not want_stream:
                    data = resp.json()
                    provider_router.mark_success(provider.name)
                    data["_celina_provider"] = provider.name
                    data["_celina_model"] = attempt_payload["model"]
                    return data

                acc_text: list[str] = []
                tc_by_idx: dict[int, dict[str, Any]] = {}
                with resp:
                    for raw_line in resp.iter_lines(decode_unicode=True):
                        if not raw_line:
                            continue
                        line = raw_line.strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            evt = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        choices = evt.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            acc_text.append(str(piece))
                            if stream_cb is not None:
                                cur = _format_stream_preview("".join(acc_text), vault=vault)
                                if cur:
                                    stream_cb(cur)
                        for tc_delta in delta.get("tool_calls") or []:
                            idx = int(tc_delta.get("index", 0))
                            entry = tc_by_idx.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            if tc_delta.get("id"):
                                entry["id"] = str(tc_delta["id"])
                            fn_delta = tc_delta.get("function") or {}
                            if fn_delta.get("name"):
                                entry["function"]["name"] += str(fn_delta["name"])
                            if fn_delta.get("arguments"):
                                entry["function"]["arguments"] += str(fn_delta["arguments"])
                provider_router.mark_success(provider.name)
                msg: dict[str, Any] = {"role": "assistant", "content": "".join(acc_text)}
                if tc_by_idx:
                    msg["tool_calls"] = [tc_by_idx[i] for i in sorted(tc_by_idx)]
                return {"choices": [{"message": msg}], "_celina_provider": provider.name,
                        "_celina_model": attempt_payload["model"]}
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                provider_router.mark_failure(provider.name, type(exc).__name__)
                errors.append(f"{provider.name}: {type(exc).__name__}")
                if resp is not None:
                    resp.close()
                continue
            except requests.exceptions.HTTPError as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                # Credential/model/schema errors are not retried against this service;
                # move to another configured provider without exposing response body.
                if resp is not None and (resp.status_code == 429 or resp.status_code >= 500):
                    provider_router.mark_failure(provider.name, f"HTTP {resp.status_code}")
                    errors.append(f"{provider.name}: HTTP {resp.status_code}")
                    resp.close()
                    continue
                if resp is not None:
                    resp.close()
                errors.append(f"{provider.name}: HTTP {status_code or 'error'}")
                continue
            except (ValueError, json.JSONDecodeError) as exc:
                if resp is not None:
                    resp.close()
                provider_router.mark_failure(provider.name, "invalid response")
                errors.append(f"{provider.name}: invalid response")
                continue
            except requests.exceptions.RequestException as exc:
                if resp is not None:
                    resp.close()
                provider_router.mark_failure(provider.name, type(exc).__name__)
                errors.append(f"{provider.name}: {type(exc).__name__}")
                continue
        raise RuntimeError("All configured cloud providers failed: " + "; ".join(errors))

    @staticmethod
    def _simulate_tier2_tool(fn_name: str, fn_args: dict[str, Any]) -> tuple[str, str]:
        """Simulates a Tier-2 action cleanly with 0 bytes modified on disk and 0 GUI/shell side-effects."""
        compact_args = ", ".join(f"{k}={repr(v)[:70]}" for k, v in fn_args.items())
        short_desc = f"{fn_name}({compact_args})"
        out = (
            f"[DRY-RUN SIMULATION] Planned Tier-2 action NOT executed: {short_desc}. "
            "0 bytes modified on disk, no shell/GUI/external side-effects performed."
        )
        return out, short_desc

    def _run_cloud_agent(
        self,
        messages: list[dict[str, Any]],
        gear: str,
        vault: privacy.RedactionVault,
        active_tools: Optional[list[dict[str, Any]]] = None,
        trace: Optional[TurnTrace] = None,
        status_cb: Optional[StatusCallback] = None,
        approval_cb: Optional[ApprovalCallback] = None,
        consent_cb: Optional[PrivacyConsentCallback] = None,
        task_glow_cb: Optional[TaskGlowCallback] = None,
        stream_cb: Optional[StreamCallback] = None,
        dry_run: bool = False,
    ) -> tuple[Optional[str], Optional[str], bool]:
        """
        Returns (reply, error, switched_to_local).
        Includes automatic error self-correction reflexion and privacy gating.
        """
        if not _has_cloud_key():
            return None, "No cloud provider API key configured.", False

        gcfg = _gear_settings(gear)
        max_steps = int(gcfg.get("max_steps", config.MAX_AGENT_STEPS))
        convo = list(messages)
        last_tool_out = ""
        failed_sigs: dict[str, int] = {}
        enable_reflect = bool(config.REFLECTION_POLICY.get("enable_error_self_correction", True))

        try:
            for step in range(max_steps):
                allow_tools = step < max_steps - 1
                data = self._post_openrouter(
                    convo,
                    gear=gear,
                    use_tools=allow_tools,
                    active_tools=active_tools,
                    stream_cb=stream_cb,
                    vault=vault,
                )
                if trace is not None:
                    trace.cloud_provider = str(data.get("_celina_provider", ""))
                    trace.cloud_model = str(data.get("_celina_model", ""))
                choices = data.get("choices") or []
                if not choices:
                    return None, "Empty choices returned by OpenRouter.", False
                msg = choices[0].get("message") or {}
                tool_calls = msg.get("tool_calls") or []

                if not tool_calls:
                    reply = (msg.get("content") or "").strip()
                    reply = vault.restore(reply)
                    final_txt = reply or last_tool_out
                    if not final_txt:
                        return None, "Cloud model returned an empty response.", False
                    return final_txt, None, False

                convo.append(msg)
                for tc in tool_calls:
                    fn_info = tc.get("function") or {}
                    fn_name = str(fn_info.get("name", ""))
                    fn_args = _parse_tool_args(fn_info.get("arguments"))
                    restored_args = {
                        k: (vault.restore(v) if isinstance(v, str) else v)
                        for k, v in fn_args.items()
                    }
                    if dry_run and fn_name in config.TIER2_APPROVAL_TOOLS:
                        out, sim_desc = self._simulate_tier2_tool(fn_name, restored_args)
                        if trace is not None:
                            trace.tools_called.append(f"[sim]{fn_name}")
                            trace.simulated_actions.append(sim_desc)
                        if status_cb:
                            status_cb(f"Simulating {fn_name} (dry-run)...")
                    else:
                        if trace is not None:
                            trace.tools_called.append(fn_name)
                        if status_cb:
                            status_cb(f"Running {fn_name}...")
                        if task_glow_cb:
                            task_glow_cb(True)
                        if trace is not None:
                            from . import evidence as _ev

                            _ev.set_current_why_context(
                                f"gear={trace.gear} role={trace.swarm_role} prompt={trace.prompt_preview}"
                            )
                        try:
                            out = tools.execute_tool(
                                fn_name,
                                restored_args,
                                approval_cb=approval_cb,
                                status_cb=status_cb,
                                consent_cb=consent_cb,
                                task_glow_cb=task_glow_cb,
                            )
                        finally:
                            if task_glow_cb:
                                task_glow_cb(False)
                    last_tool_out = out
                    if trace is not None and "Circuit breaker open" in out:
                        trace.circuit_breaker_events.append(f"{fn_name} (cooldown)")

                    # Sensory-Motor Learning: record (active_app, tool_name, outcome)
                    is_err = _is_tool_error(out)
                    ctx_app = (trace.active_window if trace is not None else "") or "desktop"
                    self.db.record_sensory_outcome(
                        context_app=ctx_app,
                        tool_name=fn_name,
                        succeeded=not is_err,
                        hint="" if not is_err else out[:160],
                    )

                    # Privacy Gate: inspect local tool output before sending back to cloud!
                    is_local_sensitive = fn_name in config.LOCAL_SENSITIVE_TOOLS
                    decision, cloud_out = privacy.evaluate_cloud_outbound(
                        out,
                        source_label=f"tool:{fn_name}",
                        vault=vault,
                        has_local_data=is_local_sensitive,
                        consent_cb=consent_cb,
                    )
                    if decision == "local":
                        hand_off = f"[Private local output from {fn_name}]:\n{out}"
                        return None, hand_off, True

                    # Pillar 5B + Critic Verifier: Automatic Error Self-Correction Reflexion
                    if enable_reflect and is_err:
                        sig = f"{fn_name}:{json.dumps(restored_args, sort_keys=True, default=str)}"
                        failed_sigs[sig] = failed_sigs.get(sig, 0) + 1
                        cloud_out += _build_self_correction_hint(fn_name, out, failed_sigs[sig])
                        if trace is not None:
                            trace.self_corrections += 1

                    convo.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", f"call_{step}"),
                            "content": cloud_out,
                        }
                    )
                if status_cb:
                    status_cb(f"Thinking ({gear})...")

            return vault.restore(last_tool_out) or "(Agent reached step limit)", None, False
        except (requests.RequestException, RuntimeError, ValueError, KeyError, TypeError) as exc:
            return None, f"Cloud AI error: {exc}", False

    def _read_psi(self, psi_path: Path = Path("/proc/pressure/memory")) -> float:
        """Reads kernel memory PSI `full avg10` from `/proc/pressure/memory` (fail-soft 0.0)."""
        from . import os_hardening

        psi = os_hardening.read_memory_psi(psi_path=psi_path)
        return float(psi.full_avg10) if psi.available else 0.0

    def _apply_psi_kv_trim(
        self,
        ollama_opts: dict[str, Any],
        *,
        psi_path: Path = Path("/proc/pressure/memory"),
        threshold: float = 10.0,
    ) -> dict[str, Any]:
        """kv-psi: trims `num_ctx` by 25% when `/proc/pressure/memory` full avg10 exceeds threshold."""
        psi_full = self._read_psi(psi_path=psi_path)
        if psi_full > threshold and "num_ctx" in ollama_opts:
            cur = int(ollama_opts["num_ctx"])
            trimmed = max(512, int(cur * 0.75))
            if trimmed < cur:
                import logging

                logging.getLogger("celina.router").warning(
                    "PSI full avg10=%.1f — trimming KV cache num_ctx %d -> %d",
                    psi_full,
                    cur,
                    trimmed,
                )
                ollama_opts = dict(ollama_opts)
                ollama_opts["num_ctx"] = trimmed
        return ollama_opts

    def _post_ollama(
        self,
        messages: list[dict[str, Any]],
        gear: str,
        use_tools: bool = True,
        active_tools: Optional[list[dict[str, Any]]] = None,
        stream_cb: Optional[StreamCallback] = None,
        trace: Optional[TurnTrace] = None,
    ) -> dict[str, Any]:
        gcfg = _gear_settings(gear)
        # Stream live across ALL gears (fast, tactical, deep); _format_stream_preview strips <analysis>
        want_stream = stream_cb is not None

        send_msgs = list(messages)
        # Pillar 7B: Lightweight 3-bullet <analysis> CoT for small 3B local models on Gear 3
        if (
            gear == "deep"
            and config.REFLECTION_POLICY.get("enable_local_3b_cot", True)
            and send_msgs
            and send_msgs[0].get("role") == "system"
        ):
            cot_guide = (
                "\n\n[Deep Reasoning Scaffold]: First write a concise 3-bullet <analysis> block: "
                "1) Known facts, 2) Missing info or tool needed, 3) Best decision. "
                "Close </analysis>, then give your clean, decisive final answer outside the tags."
            )
            if "[Deep Reasoning Scaffold]" not in str(send_msgs[0].get("content", "")):
                send_msgs[0] = {**send_msgs[0], "content": str(send_msgs[0]["content"]) + cot_guide}
                if trace is not None:
                    trace.cot_used = True

        prefix_text = str(send_msgs[0].get("content") or "") if send_msgs else ""
        adaptive = inference_adaptive.build_adaptive_inference_profile(
            prompt_prefix=prefix_text,
            base_num_ctx=_ollama_ctx_for_gear(gear),
            base_num_predict=int(gcfg.get("max_tokens", 550)),
        )
        autotuned = inference_adaptive.autotune_ollama_request(
            send_msgs,
            max_output_tokens=int(gcfg.get("max_tokens", 550)),
            baseline_ctx=adaptive.num_ctx,
            pin_in_ram=False,
        )
        ollama_opts = adaptive.to_ollama_options()
        # Apply autotune exact num_ctx shrink on fast/tactical turns (deep preserves full gear ctx ceiling)
        if gear != "deep":
            ollama_opts["num_ctx"] = min(int(ollama_opts["num_ctx"]), autotuned.num_ctx)
        ollama_opts["num_keep"] = autotuned.num_keep
        ollama_opts = self._apply_psi_kv_trim(ollama_opts)
        if trace is not None:
            trace.autotune_ctx = int(ollama_opts["num_ctx"])
            trace.autotune_keep = int(ollama_opts["num_keep"])
        ollama_opts["temperature"] = float(gcfg.get("temperature", 0.45))
        ollama_opts["num_thread"] = _recommended_ollama_threads()

        # Phase 0: Never burn CPU/timeout on hidden Qwen3 thinking tokens during tool routing or fast/standard turns!
        want_think = bool(config.LOCAL_THINK_ENABLED and gear == "deep" and not use_tools)
        local_timeout = max(int(config.LOCAL_TIMEOUT), 300 if gear == "deep" else int(config.LOCAL_TIMEOUT))

        payload: dict[str, Any] = {
            "model": config.LOCAL_MODEL,
            "messages": send_msgs,
            "stream": want_stream,
            "think": want_think,
            "keep_alive": _effective_ollama_keep_alive(),
            "options": ollama_opts,
        }
        tool_list = active_tools if active_tools is not None else tools.get_enabled_tool_schemas()
        if use_tools and tool_list:
            payload["tools"] = tool_list

        url = f"{config.OLLAMA_API_BASE}/api/chat"
        resp = self._post_with_retry(url, payload, timeout=local_timeout, stream=want_stream)
        # Strip 'think' FIRST (non-reasoning local models like qwen2.5:3b reject 'think' but support 'tools'!)
        if resp.status_code == 400 and "think" in payload:
            resp.close()
            payload.pop("think", None)
            resp = self._post_with_retry(url, payload, timeout=local_timeout, stream=want_stream)
        if resp.status_code == 400 and use_tools and "tools" in payload:
            resp.close()
            payload.pop("tools", None)
            resp = self._post_with_retry(url, payload, timeout=local_timeout, stream=want_stream)
        resp.raise_for_status()

        if not want_stream:
            return resp.json()

        acc_text: list[str] = []
        collected_tcs: list[dict[str, Any]] = []
        with resp:
            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                try:
                    evt = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                msg = evt.get("message") or {}
                piece = msg.get("content")
                if piece:
                    acc_text.append(str(piece))
                    if stream_cb is not None:
                        cur = _format_stream_preview("".join(acc_text))
                        if cur:
                            stream_cb(cur)
                if msg.get("tool_calls"):
                    collected_tcs.extend(msg["tool_calls"])
                if evt.get("done"):
                    break
        out_msg: dict[str, Any] = {"role": "assistant", "content": "".join(acc_text)}
        if collected_tcs:
            out_msg["tool_calls"] = collected_tcs
        return {"message": out_msg}

    def _run_local_agent(
        self,
        messages: list[dict[str, Any]],
        gear: str,
        active_tools: Optional[list[dict[str, Any]]] = None,
        trace: Optional[TurnTrace] = None,
        status_cb: Optional[StatusCallback] = None,
        approval_cb: Optional[ApprovalCallback] = None,
        consent_cb: Optional[PrivacyConsentCallback] = None,
        task_glow_cb: Optional[TaskGlowCallback] = None,
        stream_cb: Optional[StreamCallback] = None,
        dry_run: bool = False,
    ) -> tuple[Optional[str], Optional[str]]:
        gcfg = _gear_settings(gear)
        base_max_steps = int(gcfg.get("max_steps", config.MAX_AGENT_STEPS))
        forge = guardrails.ForgeGuardrailStack(max_steps=base_max_steps)
        budget = forge.compute_hardware_budget()
        max_steps = budget.max_steps
        convo = list(messages)
        last_tool_out = ""
        failed_sigs: dict[str, int] = {}
        enable_reflect = bool(config.REFLECTION_POLICY.get("enable_error_self_correction", True))

        try:
            for step in range(max_steps):
                convo, _compacted = forge.compact_messages(convo, budget=budget)
                allow_tools = step < max_steps - 1
                data = self._post_ollama(
                    convo,
                    gear=gear,
                    use_tools=allow_tools,
                    active_tools=active_tools,
                    stream_cb=stream_cb,
                    trace=trace,
                )
                msg = data.get("message") or {}
                tool_calls = msg.get("tool_calls") or []

                if not tool_calls:
                    raw_reply = (msg.get("content") or "").strip()
                    reply = _strip_analysis_tags(raw_reply)
                    return (reply or last_tool_out or "(No response)"), None

                convo.append(msg)
                for tc in tool_calls:
                    fn_info = tc.get("function") or {}
                    fn_name = str(fn_info.get("name", ""))
                    fn_args = _parse_tool_args(fn_info.get("arguments"))
                    step_ok, step_nudge = forge.enforce_step(step, fn_name, fn_args, budget=budget)
                    if not step_ok:
                        convo.append({"role": "tool", "content": step_nudge})
                        continue
                    if dry_run and fn_name in config.TIER2_APPROVAL_TOOLS:
                        out, sim_desc = self._simulate_tier2_tool(fn_name, fn_args)
                        if trace is not None:
                            trace.tools_called.append(f"[sim]{fn_name}")
                            trace.simulated_actions.append(sim_desc)
                        if status_cb:
                            status_cb(f"Simulating {fn_name} (dry-run)...")
                    else:
                        if trace is not None:
                            trace.tools_called.append(fn_name)
                        if status_cb:
                            status_cb(f"Running {fn_name} (local)...")
                        if task_glow_cb:
                            task_glow_cb(True)
                        if trace is not None:
                            from . import evidence as _ev

                            _ev.set_current_why_context(
                                f"gear={trace.gear} role={trace.swarm_role} prompt={trace.prompt_preview}"
                            )
                        try:
                            out = tools.execute_tool(
                                fn_name,
                                fn_args,
                                approval_cb=approval_cb,
                                status_cb=status_cb,
                                consent_cb=consent_cb,
                                task_glow_cb=task_glow_cb,
                            )
                        finally:
                            if task_glow_cb:
                                task_glow_cb(False)
                    last_tool_out = out
                    if trace is not None and "Circuit breaker open" in out:
                        trace.circuit_breaker_events.append(f"{fn_name} (cooldown)")

                    # Sensory-Motor Learning: record (active_app, tool_name, outcome)
                    is_err = _is_tool_error(out)
                    ctx_app = (trace.active_window if trace is not None else "") or "desktop"
                    self.db.record_sensory_outcome(
                        context_app=ctx_app,
                        tool_name=fn_name,
                        succeeded=not is_err,
                        hint="" if not is_err else out[:160],
                    )

                    tool_msg_content = out
                    if enable_reflect and is_err:
                        sig = f"{fn_name}:{json.dumps(fn_args, sort_keys=True, default=str)}"
                        failed_sigs[sig] = failed_sigs.get(sig, 0) + 1
                        tool_msg_content += _build_self_correction_hint(fn_name, out, failed_sigs[sig])
                        if trace is not None:
                            trace.self_corrections += 1

                    convo.append({"role": "tool", "content": tool_msg_content})
                if status_cb:
                    status_cb(f"Thinking ({gear} · local)...")

            return last_tool_out or "(Agent reached step limit)", None
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return None, f"Local Ollama error: {exc}"

    def ask(
        self,
        text: str,
        mode: str = "auto",
        language: Optional[str] = None,
        status_cb: Optional[StatusCallback] = None,
        approval_cb: Optional[ApprovalCallback] = None,
        consent_cb: Optional[PrivacyConsentCallback] = None,
        task_glow_cb: Optional[TaskGlowCallback] = None,
        stream_cb: Optional[StreamCallback] = None,
        dry_run: bool = False,
        tier: Optional[int] = None,
    ) -> tuple[str, str]:
        t0 = time.monotonic()
        clean = text.strip()
        if not clean:
            return "Please enter a prompt.", "system"

        # Phase 2: Explicit Local-First Compute Split (0=Reflex Only, 1=Local Fast, 2=Local Heavy, 3=Opt-In Cloud)
        effective_tier = tier if tier is not None else self._session_tier
        if effective_tier in {1, 2}:
            mode = "local"
        elif effective_tier == 3:
            mode = "cloud"

        # Movie Feature D: Dry-Run Simulation Mode ("simulate: ...", "dry run: ...", or --dry-run)
        effective_dry_run = bool(dry_run)
        dry_m = re.match(r"^(?:simulate|dry[\s-]?run)\s*[:\-]\s*(.+)$", clean, re.I)
        if dry_m:
            effective_dry_run = True
            clean = dry_m.group(1).strip()
            if not clean:
                return "Please provide an instruction to simulate.", "system"

        low = clean.lower()
        if low in {"/help", "/commands"}:
            return (
                f"{config.get_assistant_display_label()} Instant Commands (0ms local reflexes):\n"
                "• /model [tag]          — list or switch Dense / Sparse MoE Ollama model\n"
                "• /providers            — show configured cloud providers and local Ollama route\n"
                "• /ram                  — inspect live RAM Guard, context cap & keep-alive\n"
                "• /offload              — immediately eject Ollama/Whisper/caches from RAM\n"
                "• /turbo [on|off|status]— Zero-Lag 8 GB mode (eject RAM + disable heavy blur + stop background AT-SPI2)\n"
                "• /tier [0|1|2|3|auto]  — show or lock compute tier (0=0-LLM reflex, 1=local fast, 2=local heavy, 3=opt-in cloud)\n"
                "• /bg <goal> · /jobs    — dispatch non-blocking background Swarm worker & inspect job results\n"
                "• /struggle             — plain-English explanation of why the last browser/GUI/AI step struggled\n"
                "• /why                  — Glass-Box receipt for the last decision\n"
                "• /name <New> [Nick]    — rename assistant & nickname\n"
                "• /facts · /goals · /kg — view saved memory, goals & knowledge graph\n"
                "• /skills · /mcp · /cache · /clear\n"
                "• Prefixes: 'quick: ...', 'deep: ...', 'simulate: ...', 'remember key = val'"
            ), "system"

        if low == "/name" or low.startswith("/name "):
            arg = clean[5:].strip()
            if not arg:
                cur_n = config.get_assistant_name()
                cur_nick = config.get_assistant_nickname() or "(none)"
                return (
                    f"Current identity: {cur_n} · Nickname: {cur_nick}\n"
                    "Update anytime with '/name <NewName> [Nickname]' or '/name reset'."
                ), "system"
            if arg.lower() == "reset":
                n, nick = config.set_assistant_identity("Celina", "Celly")
                return f"Identity reset to default: {n} (nickname: {nick}).", "system"
            parts = [p.strip() for p in re.split(r"[\s,]+", arg, maxsplit=1) if p.strip()]
            new_name = parts[0]
            new_nick = parts[1] if len(parts) > 1 else None
            msg = tools.set_assistant_identity(new_name, new_nick)
            return msg, "system"

        if low in {"/providers", "providers", "provider status"}:
            rows = provider_router.status()
            lines = ["Cloud providers (credentials never displayed):"]
            for row in rows:
                state = "configured" if row["configured"] else "not configured"
                if row["cooldown_seconds"]:
                    state += f", cooldown {row['cooldown_seconds']}s"
                lines.append(f"• {row['name']}: {state} · {row['model']}")
            lines.append(f"• Local Ollama: {config.LOCAL_MODEL} · {config.OLLAMA_API_BASE}")
            lines.append("Set provider API keys in ~/.ai-env, then restart Celina to load them.")
            return "\n".join(lines), "system"

        if low == "/model" or low.startswith("/model "):
            arg = clean[6:].strip()
            if not arg:
                return config.format_model_catalog(), "system"
            try:
                new_tag, ram_warn = config.set_local_model(arg)
            except (ValueError, config.PersistenceError, OSError) as exc:
                return f"Could not switch local model: {exc}", "error"
            msg = (
                f"Switched active local Ollama model to '{new_tag}'. "
                f"If not downloaded yet, run: ollama pull {new_tag}"
            )
            if ram_warn:
                msg += f"\n⚠️  {ram_warn}"
            return msg, "system"

        if low in {"/ram", "/mem", "memory status"}:
            tot = config.detect_system_ram_gb()
            avail = config.detect_available_ram_mb()
            ka = _effective_ollama_keep_alive(avail)
            ctx_fast = _ollama_ctx_for_gear("fast", total_ram_gb=tot)
            ctx_deep = _ollama_ctx_for_gear("deep", total_ram_gb=tot)
            ka_label = "0s (instant unload)" if ka == 0 else str(ka)
            return (
                f"RAM Guard: ~{avail} MiB available / ~{tot} GiB total\n"
                f"Active local model: {config.LOCAL_MODEL} | Context cap: {ctx_fast}/{ctx_deep} | Keep-alive: {ka_label}\n"
                f"Hybrid Embeddings: engine={config.EMBED_ENGINE}, model={config.EMBED_MODEL} ({config.EMBED_DIM}d float16, keep_alive={config.EMBED_KEEP_ALIVE})\n"
                "Tips: Run '/offload' to immediately free Ollama+Embed+Whisper RAM, '/model qwen3:1.7b' or "
                "'/model granite3.1-moe:1b' (only 400M active) for ultra-low RAM, or set OPENROUTER_API_KEY "
                "so non-private chat uses 0 MB local RAM."
            ), "system"

        if low in {"/why", "why?", "explain last decision", "why did you do that"}:
            return self.get_last_receipt(), "explain"
        if low in {"/clear", "clear memory"}:
            self.db.clear_history()
            return "Short-term conversation history cleared.", "memory"
        if low == "/facts":
            summary = self.db.format_facts_for_prompt()
            return summary if summary else "No long-term facts saved yet.", "memory"
        if low in {"/goals", "list goals", "my goals"}:
            goals = self.db.list_goals(status="all", limit=15, cloud_only=False)
            if not goals:
                return "No persistent goals saved yet.", "memory"
            lines = [
                f"- [{g['status'].upper()}] {g['goal_id']}: {g['title']}"
                + (f" (due {g['deadline']})" if g["deadline"] else "")
                + (f" -> {', '.join(g['sub_tasks'])}" if g["sub_tasks"] else "")
                for g in goals
            ]
            return "Persistent Goals:\n" + "\n".join(lines), "memory"
        if low in {"/kg", "/graph", "knowledge graph"}:
            triples = self.db.list_knowledge_triples(limit=25, cloud_only=False)
            return (
                "Semantic Knowledge Graph:\n" + "\n".join(f"- {t}" for t in triples)
                if triples
                else "No Knowledge Graph links saved yet."
            ), "memory"
        if low in {"/skills", "list skills", "my skills"}:
            return skills.list_skills(db=self.db), "skills"
        if low in {"/train", "train celina", "run everyday trainer"}:
            rep = everyday_trainer.run_multi_agent_training(db=self.db, save_report=not effective_dry_run)
            return everyday_trainer.format_training_report(rep), "trainer"
        if low == "/cache":
            return self.cache.format_stats(), "cache"
        if low in {"/cache clear", "clear cache"}:
            self.cache.clear()
            return "Smart cache cleared and compacted.", "cache"
        if low == "/mcp":
            return mcp_client.get_mcp().list_servers(), "mcp"
        if low in {"/mcp enable-local", "/mcp enable"}:
            return everyday_agents.enable_local_zero_login_mcp(dry_run=effective_dry_run), "mcp"
        if low == "/tier" or low.startswith("/tier "):
            ram_gb = config.detect_system_ram_gb()
            t_arg = low[5:].strip()
            heavy_tag = "qwen3.5:9b" if ram_gb >= 16 else "qwen2.5:3b / qwen3:4b"
            if t_arg in {"auto", "reset", "off"}:
                self._session_tier = None
                return "Compute Tier reset to automatic local-first routing.", "system"
            if t_arg in {"0", "1", "2", "3"}:
                self._session_tier = int(t_arg)
                descs = {
                    "0": "Tier 0 (Reflex Only): 0 LLM tokens, 0 MB AI RAM — instant OS/media/file commands.",
                    "1": f"Tier 1 (Local Fast): 100% offline local Ollama ({config.LOCAL_MODEL}, think=False).",
                    "2": (
                        f"Tier 2 (Local Heavy): 100% offline local Ollama ({heavy_tag}). "
                        + ("⚠️ On 8 GB RAM this uses ~2.5-4 GB RAM — run '/offload' afterward." if ram_gb <= 8 else "")
                    ),
                    "3": "Tier 3 (Opt-In Cloud): OpenRouter cloud (still guarded by Outbound Privacy Gate).",
                }
                return (
                    f"Compute Tier {t_arg} active for this session (reset anytime with `/tier auto`, or pass `--tier {t_arg}`):\n• {descs[t_arg]}"
                ), "system"
            cur_t = f"Tier {self._session_tier}" if self._session_tier is not None else "Auto"
            return (
                f"Celina Local-First Compute Split (active: {cur_t} · detected RAM: ~{ram_gb} GB):\n"
                f"  • Tier 0 (--tier 0): Gear 0/0.5 reflexes & Everyday Agents — 0 LLM tokens, 0 MB RAM\n"
                f"  • Tier 1 (--tier 1): Local Fast Ollama (`{config.LOCAL_MODEL}`, think=False) — 100% offline\n"
                f"  • Tier 2 (--tier 2): Local Heavy Ollama (`{heavy_tag}`) — warns on 8 GB RAM\n"
                f"  • Tier 3 (--tier 3): Explicit Opt-In Cloud (OpenRouter, privacy-gated)"
            ), "system"
        if low in {"/jobs", "/swarm"}:
            return self.get_supervisor().format_jobs(), "swarm"
        if low.startswith("/bg ") or low.startswith("/swarm "):
            goal_txt = clean.split(None, 1)[1].strip() if len(clean.split(None, 1)) > 1 else ""
            if not goal_txt:
                return "Usage: /bg <goal> (e.g., `/bg research Ubuntu 26.04 Wayland tweaks`)", "swarm"
            sup = self.get_supervisor()
            kind = sup.classify_goal(goal_txt)
            if effective_dry_run:
                return f"🧪 Dry-Run [Swarm]: Would dispatch background `{kind}` worker for '{goal_txt[:80]}'.", "dry-run · swarm"
            job = sup.dispatch_background(goal_txt)
            return (
                f"🚀 Dispatched background `{job.worker_kind}` worker (`{job.job_id}`) for: {goal_txt[:80]}\n"
                "Check status & result anytime with `/jobs`."
            ), "swarm"
        if low in {"/turbo", "/turbo on", "/lite", "/lite on", "/lag-fix"}:
            return turbo_mode.enable_turbo_mode(dry_run=effective_dry_run), "system"
        if low in {"/turbo off", "/lite off"}:
            return turbo_mode.disable_turbo_mode(dry_run=effective_dry_run), "system"
        if low in {"/turbo status", "/lite status"}:
            return turbo_mode.get_turbo_status(), "system"
        if low in {"/struggle", "why did you fail", "why are you struggling"}:
            diag = automation.get_last_perception_diagnostic()
            last_src = self.last_trace.source if self.last_trace else "none"
            if diag:
                return f"Honest diagnostic ({last_src}): {diag}", "explain"
            return (
                f"Last turn ran via '{last_src}' with no browser/AT-SPI2 struggle recorded. "
                "Run '/why' for the full routing receipt or '/turbo status' to check RAM/ZRAM lag."
            ), "explain"
        if low == "/offload":
            c_res = self.cache.offload_and_prune(force_vacuum=True)
            ram_dropped = self.cache.offload_ram_if_idle(force=True)
            m_stopped = mcp_client.get_mcp().offload_idle_servers(force=True)
            w_stopped = voice.offload_whisper_if_idle(force=True)
            embed_offloaded = False
            try:
                from . import embeddings

                embed_offloaded = embeddings.get_engine().offload()
            except Exception:
                pass
            ollama_ejected = False
            try:
                r = requests.post(
                    f"{config.OLLAMA_API_BASE}/api/generate",
                    json={"model": config.LOCAL_MODEL, "keep_alive": 0},
                    timeout=2,
                )
                ollama_ejected = r.status_code < 400
            except Exception:
                pass
            gc.collect()
            try:
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:
                pass
            return (
                f"Offloaded cleanly: {ram_dropped + c_res['ram_cleared']} RAM cache items cleared, "
                f"{c_res['expired_deleted']} expired disk entries purged, {m_stopped} MCP server(s) stopped"
                f"{', Whisper STT offloaded' if w_stopped else ''}"
                f"{', Embedding engine offloaded' if embed_offloaded else ''}"
                f"{', Ollama LLM ejected from RAM' if ollama_ejected else ''}, OS heap trimmed."
            ), "system"
        if low in {"stop", "stop automation", "abort"}:
            automation.request_stop()
            return "⏹ Automation emergency stop triggered.", "system"
        if low in {"stop recording", "stop learning"} and skills.recorder.is_recording:
            return skills.recorder.stop_and_save(db=self.db), "skills"

        # Pillar 6A shortcut: 1-phrase skill healing ("heal skill <name>")
        heal_m = re.match(r"^(?:heal|fix|repair)\s+skill\s+(.+)$", clean, re.I)
        if heal_m:
            return skills.heal_skill_from_last_trace(heal_m.group(1).strip(), db=self.db), "skills"

        # Shortcut: "save last actions as <name>"
        save_m = re.match(r"^save\s+last\s+actions?\s+as\s+(.+)$", clean, re.I)
        if save_m:
            return skills.save_last_actions_as_skill(save_m.group(1).strip()), "skills"

        if low.startswith("remember ") and "=" in clean[9:]:
            body = clean[9:].strip()
            k, v = body.split("=", 1)
            if effective_dry_run:
                return f"🧪 Dry-Run [Memory]: Would save {k.strip()} = {v.strip()} to long-term memory.", "dry-run · memory"
            self.db.set_fact(k, v)
            return f"Saved to long-term memory: {k.strip()} = {v.strip()}", "memory"

        # Gear 0-math: Deterministic AST arithmetic (0 LLM tokens, eval-free, read-only even in dry-run)
        math_reply = try_gear0_math(clean)
        if math_reply is not None:
            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="reflex",
                gear_reason="Gear 0 deterministic AST math (0 tokens)",
                source="gear0_math",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            try:
                receipt_txt = self.last_trace.format_receipt()
                telemetry.get_tracer().store.save_why_receipt(
                    trace_id=telemetry.new_trace_id(),
                    prompt_preview=clean[:80],
                    gear="reflex",
                    source="gear0_math",
                    receipt_text=receipt_txt,
                    receipt_hash=hashlib.sha256(receipt_txt.encode("utf-8")).hexdigest(),
                )
            except Exception:
                pass
            return math_reply, "gear0_math"

        # Gear 0a: Instant zero-latency reflex (EN / TE / HI) + approval-gated curated Flathub install
        install_reply = tools.handle_fast_install_intent(
            clean,
            approval_cb=approval_cb,
            status_cb=status_cb,
            dry_run=effective_dry_run,
        )
        if install_reply:
            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="reflex",
                gear_reason="Gear 0 approval-gated curated Flathub app install",
                tools_called=["run_shell_command"] if not effective_dry_run else ["[sim]run_shell_command"],
                dry_run=effective_dry_run,
                source="dry-run · install" if effective_dry_run else "install",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return install_reply, ("dry-run · install" if effective_dry_run else "install")

        # Gear 0a-1: 6 Specialized Everyday Desktop Agents (Media, Web, Files/Notes, System, Routines, GUI, Learning)
        ev_match = everyday_agents.dispatch_everyday_task(
            clean,
            approval_cb=approval_cb,
            status_cb=status_cb,
            dry_run=effective_dry_run,
            db=self.db,
        )
        if ev_match is not None:
            ev_agent, ev_reply = ev_match
            if not effective_dry_run:
                self.db.add_message("user", clean)
                self.db.add_message("assistant", ev_reply)
            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="reflex",
                gear_reason=f"Everyday {ev_agent} direct execution (0 LLM tokens)",
                swarm_role=ev_agent,
                dry_run=effective_dry_run,
                source=f"dry-run · {ev_agent}" if effective_dry_run else f"everyday · {ev_agent}",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return ev_reply, (f"dry-run · {ev_agent}" if effective_dry_run else f"everyday · {ev_agent}")

        if not effective_dry_run:
            fast_reply = tools.handle_fast_intent(clean, approval_cb=approval_cb)
            if fast_reply:
                self.db.add_message("user", clean)
                self.db.add_message("assistant", fast_reply)
                if tools._FRUSTRATION_RE.search(clean):
                    self.db.add_knowledge_triple("celina", "learned_correction", clean[:120])
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason="Gear 0 instant local intent match (0 tokens)",
                    source="instant",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return fast_reply, "instant"

            # Gear 0a-2 (Capability #2): Intent Fast-Bypass Classifier (greetings & status lookups -> 0 LLM tokens)
            bypass = inference_adaptive.classify_intent_fast_bypass(clean)
            if bypass.matched:
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="fast",
                    gear_reason=f"Intent fast-bypass ({bypass.archetype}, ~{bypass.saved_tokens_est} tokens saved)",
                    source="fast_bypass",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                try:
                    receipt_txt = self.last_trace.format_receipt()
                    telemetry.get_tracer().store.save_why_receipt(
                        trace_id=telemetry.new_trace_id(),
                        prompt_preview=clean[:80],
                        gear="fast",
                        source="fast_bypass",
                        receipt_text=receipt_txt,
                        receipt_hash=hashlib.sha256(receipt_txt.encode("utf-8")).hexdigest(),
                    )
                except Exception:
                    pass
                return bypass.reply, "fast_bypass"

        # Gear 0b: Direct Learned Skill Trigger Match (0 LLM tokens, 1-click workflow approval!)
        matched_skill = self.db.match_skill_by_trigger(clean)
        if matched_skill is not None:
            if effective_dry_run:
                plan_lines = [f"🧪 Dry-Run Simulation for Skill '{matched_skill['name']}' (0 actions executed):"]
                sim_list: list[str] = []
                for idx, st in enumerate(matched_skill.get("steps", []), 1):
                    tn = str(st.get("tool", ""))
                    ta = st.get("args") or {}
                    tier_tag = "Tier-2 [SIMULATED]" if tn in config.TIER2_APPROVAL_TOOLS else "Tier-1 [WOULD RUN]"
                    plan_lines.append(f"  {idx}. {tier_tag} {tn}({json.dumps(ta, ensure_ascii=False)})")
                    sim_list.append(f"{tn}({ta})")
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"dry-run preview for skill '{matched_skill['name']}'",
                    tools_called=[f"[sim]skill:{matched_skill['name']}"],
                    dry_run=True,
                    simulated_actions=sim_list,
                    source=f"dry-run · skill ({matched_skill['name']})",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return "\n".join(plan_lines), f"dry-run · skill ({matched_skill['name']})"

            if task_glow_cb:
                task_glow_cb(True)
            try:
                res = skills.run_skill(
                    name=matched_skill["name"],
                    executor=lambda tn, ta, acb: tools.execute_tool(
                        tn, ta, approval_cb=acb, status_cb=status_cb, consent_cb=consent_cb, task_glow_cb=task_glow_cb
                    ),
                    approval_cb=approval_cb,
                    status_cb=status_cb,
                    db=self.db,
                )
            finally:
                if task_glow_cb:
                    task_glow_cb(False)
            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="reflex",
                gear_reason=f"direct learned skill trigger '{matched_skill['name']}'",
                tools_called=[f"skill:{matched_skill['name']}"],
                source=f"skill ({matched_skill['name']})",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return res, f"skill ({matched_skill['name']})"

        # ------------------------------------------------------------------
        # Gear 0c / 0-Dev / 0-Privacy / 0-Hardware — Zero-token reflexes
        # Time math, unit conversion, hashing, UUID, JSON, text transforms,
        # env queries, PII redaction, API key blocking.
        # ------------------------------------------------------------------
        gear0_handler = gear0_utils.gear0_classify(clean)
        if gear0_handler:
            gear0_result = gear0_utils.gear0_execute(gear0_handler, clean)
            if gear0_result is not None:
                # Privacy bodyguard: redact PII from any Gear 0 output that
                # might contain user data before returning it.
                safe_result = gear0_utils.gear0_redact_pii(gear0_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-{gear0_handler.split('_')[0]} ({gear0_handler})",
                    source=f"gear0_{gear0_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_{gear0_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Intent: Deterministic Slot Filling (Siri-style)
        # Timer, reminder, dark mode, DND, night light, volume, brightness, open app
        # ------------------------------------------------------------------
        intent_handler = gear0_intent.gear0_intent_classify(clean)
        if intent_handler:
            intent_result = gear0_intent.gear0_intent_execute(intent_handler, clean, dry_run=effective_dry_run)
            if intent_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(intent_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Intent ({intent_handler})",
                    source=f"gear0_intent_{intent_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_intent_{intent_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Graph: Local Personal Knowledge Base (SQLite)
        # Call Mom, text alias, open my project, save contact
        # ------------------------------------------------------------------
        graph_handler = gear0_graph.gear0_graph_classify(clean)
        if graph_handler:
            graph_result = gear0_graph.gear0_graph_execute(graph_handler, clean, dry_run=effective_dry_run)
            if graph_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(graph_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Graph ({graph_handler})",
                    source=f"gear0_graph_{graph_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_graph_{graph_handler}"

        # ------------------------------------------------------------------
        # Gear 0-IoT: Direct Local Smart Home Control (HTTP/MQTT)
        # Turn on lights, set thermostat
        # ------------------------------------------------------------------
        iot_handler = gear0_iot.gear0_iot_classify(clean)
        if iot_handler:
            iot_result = gear0_iot.gear0_iot_execute(iot_handler, clean, dry_run=effective_dry_run)
            if iot_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(iot_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-IoT ({iot_handler})",
                    source=f"gear0_iot_{iot_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_iot_{iot_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Context: State & Geofence Triggers
        # Mute when in meeting, remind when home
        # ------------------------------------------------------------------
        context_handler = gear0_context.gear0_context_classify(clean)
        if context_handler:
            context_result = gear0_context.gear0_context_execute(context_handler, clean, dry_run=effective_dry_run)
            if context_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(context_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Context ({context_handler})",
                    source=f"gear0_context_{context_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_context_{context_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Audit: Proactive Local Status Checks
        # Backup status, updates, storage, battery, memory, CPU
        # ------------------------------------------------------------------
        audit_handler = gear0_audit.gear0_audit_classify(clean)
        if audit_handler:
            audit_result = gear0_audit.gear0_audit_execute(audit_handler, clean)
            if audit_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(audit_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Audit ({audit_handler})",
                    source=f"gear0_audit_{audit_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_audit_{audit_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Media: Local Curation & Playback
        # Play music, open project, show downloads
        # ------------------------------------------------------------------
        media_handler = gear0_media.gear0_media_classify(clean)
        if media_handler:
            media_result = gear0_media.gear0_media_execute(media_handler, clean, dry_run=effective_dry_run)
            if media_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(media_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Media ({media_handler})",
                    source=f"gear0_media_{media_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_media_{media_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Focus: Atomic Deep Work State Changes
        # Start focus mode, end focus mode
        # ------------------------------------------------------------------
        focus_handler = gear0_focus.gear0_focus_classify(clean)
        if focus_handler:
            focus_result = gear0_focus.gear0_focus_execute(focus_handler, clean, dry_run=effective_dry_run)
            if focus_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(focus_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Focus ({focus_handler})",
                    source=f"gear0_focus_{focus_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_focus_{focus_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Clipboard: Local History & Snippet Recall
        # What did I copy last, clear clipboard history
        # ------------------------------------------------------------------
        clipboard_handler = gear0_clipboard.gear0_clipboard_classify(clean)
        if clipboard_handler:
            clipboard_result = gear0_clipboard.gear0_clipboard_execute(clipboard_handler, clean, dry_run=effective_dry_run)
            if clipboard_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(clipboard_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Clipboard ({clipboard_handler})",
                    source=f"gear0_clipboard_{clipboard_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_clipboard_{clipboard_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Calendar: Local calendar events (.ics, calcurse, khal)
        # ------------------------------------------------------------------
        calendar_handler = gear0_calendar.gear0_calendar_classify(clean)
        if calendar_handler:
            calendar_result = gear0_calendar.gear0_calendar_execute(calendar_handler, clean, dry_run=effective_dry_run)
            if calendar_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(calendar_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Calendar ({calendar_handler})",
                    source=f"gear0_calendar_{calendar_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_calendar_{calendar_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Network: Local internet/DNS checks
        # ------------------------------------------------------------------
        network_handler = gear0_network.gear0_network_classify(clean)
        if network_handler:
            network_result = gear0_network.gear0_network_execute(network_handler, clean, dry_run=effective_dry_run)
            if network_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(network_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Network ({network_handler})",
                    source=f"gear0_network_{network_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_network_{network_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Hardware: Local hardware state (VPN, CPU temp, mic, battery, IP)
        # ------------------------------------------------------------------
        hardware_handler = gear0_hardware.gear0_hardware_classify(clean)
        if hardware_handler:
            hardware_result = gear0_hardware.gear0_hardware_execute(hardware_handler, clean, dry_run=effective_dry_run)
            if hardware_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(hardware_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Hardware ({hardware_handler})",
                    source=f"gear0_hardware_{hardware_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_hardware_{hardware_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Ledger: Local expenses & habits
        # ------------------------------------------------------------------
        ledger_handler = gear0_ledger.gear0_ledger_classify(clean)
        if ledger_handler:
            ledger_result = gear0_ledger.gear0_ledger_execute(ledger_handler, clean, dry_run=effective_dry_run)
            if ledger_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(ledger_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Ledger ({ledger_handler})",
                    source=f"gear0_ledger_{ledger_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_ledger_{ledger_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Macro: Local text expansion
        # ------------------------------------------------------------------
        macro_handler = gear0_macro.gear0_macro_classify(clean)
        if macro_handler:
            macro_result = gear0_macro.gear0_macro_execute(macro_handler, clean, dry_run=effective_dry_run)
            if macro_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(macro_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Macro ({macro_handler})",
                    source=f"gear0_macro_{macro_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_macro_{macro_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Status: System status & audit tail
        # ------------------------------------------------------------------
        status_handler = gear0_status.gear0_status_classify(clean)
        if status_handler:
            status_result = gear0_status.gear0_status_execute(status_handler, clean, dry_run=effective_dry_run)
            if status_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(status_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Status ({status_handler})",
                    source=f"gear0_status_{status_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_status_{status_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Dual: Backend selection (Edge0 / Ollama)
        # ------------------------------------------------------------------
        dual_handler = gear0_dual.gear0_dual_classify(clean)
        if dual_handler:
            dual_result = gear0_dual.gear0_dual_execute(dual_handler, clean, dry_run=effective_dry_run)
            if dual_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(dual_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Dual ({dual_handler})",
                    source=f"gear0_dual_{dual_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_dual_{dual_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Contacts: Siri-style contact lookup & communication
        # ------------------------------------------------------------------
        contacts_handler = gear0_contacts.gear0_contacts_classify(clean)
        if contacts_handler:
            contacts_result = gear0_contacts.gear0_contacts_execute(contacts_handler, clean, dry_run=effective_dry_run)
            if contacts_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(contacts_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Contacts ({contacts_handler})",
                    source=f"gear0_contacts_{contacts_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_contacts_{contacts_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Lists: Alexa-style shopping/task lists
        # ------------------------------------------------------------------
        lists_handler = gear0_lists.gear0_lists_classify(clean)
        if lists_handler:
            lists_result = gear0_lists.gear0_lists_execute(lists_handler, clean, dry_run=effective_dry_run)
            if lists_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(lists_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Lists ({lists_handler})",
                    source=f"gear0_lists_{lists_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_lists_{lists_handler}"

        # ------------------------------------------------------------------
        # Gear 0-Routines: Multi-step local routines
        # ------------------------------------------------------------------
        routines_handler = gear0_routines.gear0_routines_classify(clean)
        if routines_handler:
            routines_result = gear0_routines.gear0_routines_execute(routines_handler, clean, dry_run=effective_dry_run)
            if routines_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(routines_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0-Routines ({routines_handler})",
                    source=f"gear0_routines_{routines_handler}",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, f"gear0_routines_{routines_handler}"

        # ------------------------------------------------------------------
        # Gear 0 Plugin #1: Active Context Sniffer
        # ------------------------------------------------------------------
        sniffer_handler = sniffer_plugin.gear0_sniffer_classify(clean)
        if sniffer_handler:
            sniffer_result = sniffer_plugin.gear0_sniffer_execute(sniffer_handler, clean, dry_run=effective_dry_run)
            if sniffer_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(sniffer_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0 Plugin #1: Context Sniffer ({sniffer_handler})",
                    source="plugin1_sniffer",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, "plugin1_sniffer"

        # ------------------------------------------------------------------
        # Gear 0 Plugin #2: Local Habit & Frequency Engine
        # ------------------------------------------------------------------
        habit_handler = habit_engine.gear0_habit_classify(clean)
        if habit_handler:
            habit_result = habit_engine.gear0_habit_execute(habit_handler, clean, dry_run=effective_dry_run)
            if habit_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(habit_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0 Plugin #2: Habit Engine ({habit_handler})",
                    source="plugin2_habit",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, "plugin2_habit"

        # ------------------------------------------------------------------
                # ------------------------------------------------------------------
        # Gear 0 Plugin #3: Local Data Oracle
        # ------------------------------------------------------------------
        data_handler = data_oracle.gear0_data_oracle_classify(clean)
        if data_handler:
            data_result = data_oracle.gear0_data_oracle_execute(data_handler, clean, dry_run=effective_dry_run)
            if data_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(data_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0 Plugin #3: Data Oracle ({data_handler})",
                    source="plugin3_data_oracle",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, "plugin3_data_oracle"

        # ------------------------------------------------------------------
        # Gear 0 Plugin #4: Universal OS Shim
        # ------------------------------------------------------------------
        shim_handler = os_shim.gear0_os_classify(clean)
        if shim_handler:
            shim_result = os_shim.gear0_os_execute(shim_handler, clean, dry_run=effective_dry_run)
            if shim_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(str(shim_result))
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0 Plugin #4: OS Shim ({shim_handler})",
                    source="plugin4_os_shim",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, "plugin4_os_shim"

        # ------------------------------------------------------------------
        # Gear 0 Plugin #5: Self-Healing Fallback Chain
        # ------------------------------------------------------------------
        heal_handler = self_heal.gear0_heal_classify(clean)
        if heal_handler:
            heal_result = self_heal.gear0_heal_execute(heal_handler, clean, dry_run=effective_dry_run)
            if heal_result is not None:
                safe_result = gear0_utils.gear0_redact_pii(heal_result)
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Gear 0 Plugin #5: Self-Healing ({heal_handler})",
                    source="plugin5_self_heal",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return safe_result, "plugin5_self_heal"


def route_query_with_meta(
    text: str,
    mode: str = "auto",
    language: Optional[str] = None,
    status_cb: Optional[StatusCallback] = None,
    approval_cb: Optional[ApprovalCallback] = None,
    consent_cb: Optional[PrivacyConsentCallback] = None,
) -> tuple[str, str]:
    return CelinaRouter().ask(
        text,
        mode=mode,
        language=language,
        status_cb=status_cb,
        approval_cb=approval_cb,
        consent_cb=consent_cb,
    )


def process_query(text: str, mode: str = "auto", language: Optional[str] = None) -> str:
    reply, _ = route_query_with_meta(text, mode=mode, language=language)
    return reply


def _has_gui_display() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def _terminal_approval(title: str, detail: str) -> bool:
    """Interactive TTY approval with native GNOME zenity popup fallback when invoked headless (e.g. Rofi/hotkey)."""
    if not sys.stdin.isatty():
        if _has_gui_display() and shutil.which("zenity"):
            try:
                res = subprocess.run(
                    [
                        "zenity", "--question",
                        "--title", f"{config.get_assistant_name()} Approval — {title}"[:120],
                        "--text", detail[:800],
                        "--ok-label", "Approve",
                        "--cancel-label", "Deny",
                        "--timeout", "30",
                    ],
                    capture_output=True,
                    timeout=35,
                )
                return res.returncode == 0
            except Exception:
                return False
        return False
    print(f"\n⚠️  {title}\n{detail}")
    try:
        return input("Approve? [y/N]: ").strip().lower() in {"y", "yes"}
    except (KeyboardInterrupt, EOFError):
        return False


def _terminal_privacy_consent(summary: str, preview: str) -> str:
    """Interactive TTY privacy consent with GNOME zenity fallback when invoked headless (fail-safe: 'local')."""
    if not sys.stdin.isatty():
        if _has_gui_display() and shutil.which("zenity"):
            try:
                res = subprocess.run(
                    [
                        "zenity", "--list", "--radiolist",
                        "--title", f"{config.get_assistant_name()} Privacy Gate — {summary}"[:120],
                        "--text", f"Local/private data detected. Choose how to proceed:\n\n{preview[:450]}",
                        "--column", "", "--column", "Action", "--column", "Description",
                        "TRUE", "local", "Keep strictly local on-device Ollama (Safest)",
                        "FALSE", "redact", "Auto-redact PII & send to cloud",
                        "FALSE", "allow", "Allow sending unredacted once",
                        "--hide-column", "2",
                        "--print-column", "2",
                        "--timeout", "30",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=35,
                )
                choice = (res.stdout or "").strip().lower()
                if res.returncode == 0 and choice in {"local", "redact", "allow"}:
                    return choice
            except Exception:
                return "local"
        return "local"
    print(f"\n🛡️  PRIVACY GATE — {summary}\nPreview (redacted):\n{preview}")
    try:
        choice = input("Action — [r]edact & send / [a]llow once / [L]ocal only: ").strip().lower()
        if choice in {"r", "redact"}:
            return "redact"
        if choice in {"a", "allow"}:
            return "allow"
        return "local"
    except (KeyboardInterrupt, EOFError):
        return "local"


def main() -> None:
    # Fast-path operational subcommands: celina undo | migrate | export | import | checkpoints | evidence | capabilities | autonomy | config
    if len(sys.argv) >= 2 and sys.argv[1] in {
        "undo", "migrate", "export", "import", "checkpoints", "evidence", "capabilities", "autonomy", "config"
    }:
        from . import state_ops

        raise SystemExit(state_ops.run_cli(sys.argv[1:]))

    asst_label = config.get_assistant_display_label()
    parser = argparse.ArgumentParser(description=f"{asst_label} AI CLI v4.4 (EN / TE / HI)")
    parser.add_argument("prompt", nargs="*", help=f"One-shot prompt to ask {config.get_assistant_name()}")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--auto", action="store_true", help="Cloud + privacy firewall + offline local fallback")
    mode_group.add_argument("--local", action="store_true", help="Force 100% offline local Ollama")
    mode_group.add_argument("--cloud", action="store_true", help="Force OpenRouter cloud (still privacy-gated)")
    parser.add_argument(
        "--lang",
        choices=["auto", "en", "te", "hi"],
        default=config.DEFAULT_LANGUAGE if config.DEFAULT_LANGUAGE in {"auto", "en", "te", "hi"} else "auto",
    )
    parser.add_argument("--voice", action="store_true", help="Siri-style voice input")
    parser.add_argument("--no-speak", action="store_true", help="Disable spoken TTS output")
    parser.add_argument("--dry-run", action="store_true", help="Simulate Tier-2 actions without modifying disk/GUI")
    parser.add_argument(
        "--tier",
        type=int,
        choices=[0, 1, 2, 3],
        default=None,
        help="Compute tier: 0=0-LLM reflex only, 1=local fast (think=False), 2=local heavy, 3=opt-in cloud",
    )
    args = parser.parse_args()

    mode = "local" if args.local else ("cloud" if args.cloud else "auto")
    tier_flag: Optional[int] = args.tier
    if tier_flag in {1, 2} and mode == "auto":
        mode = "local"
    elif tier_flag == 3 and mode == "auto":
        mode = "cloud"
    lang = args.lang
    speak_flag = False if args.no_speak else config.SPEAK_ENABLED
    dry_run_flag = bool(args.dry_run)
    cr = CelinaRouter()

    if args.voice:
        transcript, err = voice.listen_and_transcribe(
            lang_hint=lang, on_status=lambda s: print(f"[{s}]", flush=True)
        )
        if err or not transcript:
            print(f"[voice] {err or 'No speech detected.'}")
            return
        print(f"You (voice): {transcript}")
        reply, src = cr.ask(
            transcript,
            mode=mode,
            language=lang,
            approval_cb=_terminal_approval,
            consent_cb=_terminal_privacy_consent,
            dry_run=dry_run_flag,
        )
        print(f"[{src}] {reply}")
        if speak_flag and src not in {"error", "privacy"}:
            voice.speak_text(reply, enabled=True, lang=lang)
        return

    if args.prompt:
        query = " ".join(args.prompt)
        reply, src = cr.ask(
            query,
            mode=mode,
            language=lang,
            approval_cb=_terminal_approval,
            consent_cb=_terminal_privacy_consent,
            dry_run=dry_run_flag,
            tier=tier_flag,
        )
        print(f"[{src}] {reply}")
        if speak_flag and src not in {"error", "privacy"}:
            voice.speak_text(reply, enabled=True, lang=lang)
        return

    print(
        f"{config.get_assistant_display_label()} AI v4.4 [mode={mode}, lang={lang}, dry_run={'ON' if dry_run_flag else 'OFF'}, "
        f"privacy={'STRICT' if config.STRICT_PRIVACY else 'OFF'}, cache={'ON' if config.CACHE_ENABLED else 'OFF'}] "
        "(commands: 'exit', '/help', '/tier [0-3|auto]', '/turbo', '/bg <goal>', '/jobs', '/train', '/model [tag]', '/ram', '/why', '/struggle', '/offload', '/lang auto|en|te|hi')"
    )
    while True:
        try:
            user_input = input("\nYou: ").strip()
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                break
            if user_input.lower().startswith("/lang "):
                new_lang = user_input.split(None, 1)[1].strip().lower()
                if new_lang in {"auto", "en", "te", "hi"}:
                    lang = new_lang
                    print(f"Language set to: {lang}")
                else:
                    print("Supported languages: auto, en, te, hi")
                continue
            reply, src = cr.ask(
                user_input,
                mode=mode,
                language=lang,
                status_cb=lambda s: print(f"  ... {s}", flush=True),
                approval_cb=_terminal_approval,
                consent_cb=_terminal_privacy_consent,
                dry_run=dry_run_flag,
            )
            print(f"\n{config.get_assistant_name()} [{src}]: {reply}")
            if speak_flag and src not in {"error", "privacy"}:
                voice.speak_text(reply, enabled=True, lang=lang)
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break


if __name__ == "__main__":
    main()
