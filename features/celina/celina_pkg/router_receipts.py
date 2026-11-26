"""Celina AI - Router Module: Glass-Box Receipts & Classification.

Contains:
- TurnTrace dataclass (glass-box explainability receipt)
- RouteDecision / OfflineFallback dataclasses
- Cognitive gear classification (classify_cognitive_gear_*)
- Swarm role selection (select_swarm_role)
- Gear settings (_gear_settings)
- Action gate (apply_action_gate, is_informational_only_prompt)
- Tool error detection & self-correction hints
- Implicit correction detection
- Stream preview formatting
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from typing import Any

from . import config, privacy, provider_router

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IMPLICIT_CORRECTION_PATTERNS = [
    re.compile(
        r"^\s*(?:no[,.]?\s+(?:i\s+meant|don'?t|do\s+not|never|always|use|instead)|actually[,.]?\s+(?:always|never|prefer|use|don'?t))",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:always\s+use|never\s+use|stop\s+using|don'?t\s+use\s+\w+\s+(?:use|instead)|from\s+now\s+on|prefer\s+\w+\s+over\s+\w+)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:అలా\s+కాదు|ఎప్పుడూ\s+వాడు|नहीं\s+मेरा\s+मतलब|हमेशा\s+इस्तेमाल)",
        re.IGNORECASE,
    ),
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

_ANALYSIS_BLOCK_RE = re.compile(
    r"<(?:analysis|think)>.*?</(?:analysis|think)>\s*", re.DOTALL | re.IGNORECASE,
)
_OPEN_ANALYSIS_RE = re.compile(r"<(?:analysis|think)>[\s\S]*$", re.IGNORECASE)
_PARTIAL_REDACT_RE = re.compile(
    r"\[(?:EMAIL|PHONE|PAN|AADHAAR|CARD|USER_HOME|REDACTED)[A-Z0-9_]*$",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

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
    capability_rejections: list[str] = dataclasses.field(default_factory=list)
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
    vision_backend: str = ""
    vision_latency_ms: int = 0
    vision_escalated: bool = False
    compression_backend: str = ""
    compression_tier: str = ""
    compression_saved_pct: float = 0.0
    compression_ccr_ids: tuple[str, ...] = ()
    quiet_brain_hit: bool = False
    second_thought_escalated: bool = False
    second_thought_confidence: float = 1.0
    autotune_ctx: int = 0
    autotune_keep: int = 0
    latency_ms: int = 0
    source: str = ""
    cloud_provider: str = ""
    cloud_model: str = ""
    static_prefix_hash: str = ""
    prompt_hash: str = ""
    completion_verified: bool = False
    history_compacted: bool = False

    def format_receipt(self) -> str:
        lines = ["🔍 Decision Receipt (Glass-Box Trace)"]
        if self.dry_run:
            lines.append("• Mode: 🧪 DRY-RUN SIMULATION (0 bytes modified on disk)")
        if self.prompt_preview:
            lines.append(f'• Prompt: "{self.prompt_preview}"')
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
            lines.append(
                f"• SecondThought Gate: escalated to thinking "
                f"(mean_logprob={self.second_thought_confidence:.2f} < 0.82)",
            )
        if self.autotune_ctx:
            lines.append(
                f"• Autotune KV: num_ctx={self.autotune_ctx}, num_keep={self.autotune_keep}",
            )
        lang_tag = f"{self.detected_lang} ({self.lang_style})"
        if self.active_window:
            lines.append(f"• Context: lang={lang_tag} · window={self.active_window}")
        else:
            lines.append(f"• Context: lang={lang_tag}")

        if self.tools_total > 0:
            saved_pct = int(
                round((1.0 - (self.tools_attached / max(1, self.tools_total))) * 100),
            )
            doms = ", ".join(self.tool_domains) if self.tool_domains else "core"
            lines.append(
                f"• Tools Pruned: {self.tools_attached}/{self.tools_total} attached "
                f"(~{max(0, saved_pct)}% schema tokens saved · domains: {doms})",
            )
        if self.tools_called:
            lines.append(f"• Tools Executed: {' -> '.join(self.tools_called)}")
        if self.mcp_server or self.mcp_tool:
            lines.append(
                f"• MCP Dispatch: server={self.mcp_server or 'n/a'} · "
                f"tool={self.mcp_tool or 'n/a'} · "
                f"transport={self.mcp_transport or 'n/a'} · "
                f"arguments_hash={self.mcp_arguments_hash or 'n/a'}",
            )
        if self.circuit_breaker_events:
            lines.append(
                f"• Circuit Breaker: ⚡ {'; '.join(self.circuit_breaker_events[:4])}",
            )
        if self.action_gate_suppressed > 0:
            lines.append(
                f"• ActionGate: 🛡️ suppressed {self.action_gate_suppressed} mutating tool(s) "
                "(informational/how-to question)",
            )
        if self.simulated_actions:
            lines.append(
                f"• Simulated (Not Executed): {'; '.join(self.simulated_actions[:6])}",
            )
        if self.recalled_facts or self.recalled_episodes:
            f_str = ", ".join(self.recalled_facts[:5]) if self.recalled_facts else "none"
            lines.append(
                f"• Memory Recalled (BM25): facts=[{f_str}], episodes={self.recalled_episodes}",
            )
        if self.active_skill_md:
            lines.append(f"• Active Playbook: {self.active_skill_md}")
        lines.append(f"• Privacy: {self.privacy_summary} · Cache: {self.cache_status}")
        if self.vision_backend:
            lines.append(
                f"• Vision: {self.vision_backend} ({self.vision_latency_ms} ms"
                + (f", escalated={self.vision_escalated}" if self.vision_escalated else "")
                + ")",
            )
        if self.compression_backend:
            lines.append(
                f"• Compression: {self.compression_backend} ({self.compression_tier}"
                 f" | tokens {self.compression_saved_pct:.1f}% saved"
                + (f" | ccr={','.join(self.compression_ccr_ids)}" if self.compression_ccr_ids else "")
                + ")",
            )
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


# ---------------------------------------------------------------------------
# Base helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Cognitive gear classification
# ---------------------------------------------------------------------------


def classify_cognitive_gear_with_reason(prompt: str) -> tuple[str, str, str]:
    """<1 ms System-1 / System-2 classifier driven by config/neural-schema.json.
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
    """Zero-RAM Role-Swapping Multi-Agent Swarm (<1 ms):
    Dynamically selects Architect / Engineer / Researcher / Executive Orchestrator
    within the single active LLM using word-boundary trigger scoring.
    """
    if gear == "fast":
        return "Fast Instinct", ""
    low = (prompt or "").lower()
    roles = config.SWARM_ROLES or {}
    best_role: tuple[str, str] | None = None
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
            best_role = (
                str(rcfg.get("name", role_key.title())),
                str(rcfg.get("directive", "")),
            )
    if best_role is not None:
        return best_role
    if gear == "deep" and "architect" in roles:
        rcfg = roles["architect"]
        return str(rcfg.get("name", "The Architect")), str(rcfg.get("directive", ""))
    return "Executive Orchestrator", ""


def _gear_settings(gear: str) -> dict[str, Any]:
    gears = config.COGNITION_CFG.get("gears", {})
    defaults = {
        "gear0_5": {
            "reasoning_effort": "none",
            "max_tokens": 128,
            "temperature": 0.3,
            "max_steps": 1,
            "instruction": "",
        },
        "fast": {
            "reasoning_effort": "none",
            "max_tokens": 220,
            "temperature": 0.35,
            "max_steps": 2,
            "instruction": "",
        },
        "tactical": {
            "reasoning_effort": "low",
            "max_tokens": 550,
            "temperature": 0.45,
            "max_steps": 4,
            "instruction": "",
        },
        "deep": {
            "reasoning_effort": "high",
            "max_tokens": 1500,
            "temperature": 0.3,
            "max_steps": 5,
            "instruction": "",
        },
    }
    return gears.get(gear) or defaults.get(gear, defaults["tactical"])


# ---------------------------------------------------------------------------
# Tool error detection & self-correction
# ---------------------------------------------------------------------------


def _is_tool_error(output: str) -> bool:
    low = (output or "").strip().lower()
    if not low:
        return True
    if low.startswith("cancelled by user"):
        return False
    return any(
        low.startswith(m) or f"\n{m}" in low for m in _TOOL_ERROR_MARKERS
    ) or ("not found" in low[:120] and len(low) < 240)


def _build_self_correction_hint(fn_name: str, output: str, dup_failures: int) -> str:
    max_dups = int(config.REFLECTION_POLICY.get("max_identical_tool_failures", 2))
    low = (output or "").lower()
    if dup_failures >= max_dups:
        return (
            f"\n[CRITIC VERIFIER — SELF-CORRECTION REQUIRED]: '{fn_name}' failed {dup_failures} times "
            "with identical arguments. Do NOT retry the same call. Switch to a different tool/strategy "
            "or concisely explain the blocker to the user."
        )
    if fn_name in {
        "read_file", "edit_file", "trash_file", "edit_document", "read_document",
    } or "no such file" in low:
        return (
            "\n[CRITIC VERIFIER — SELF-CORRECTION REQUIRED]: That path failed. "
            "Call search_files first to locate the exact file path before retrying."
        )
    if fn_name in {"click_on_text", "click_position", "drag_and_drop"}:
        return (
            "\n[CRITIC VERIFIER — SELF-CORRECTION REQUIRED]: That UI target was not found. "
            "Call inspect_ui_elements or read_screen to read exact live coordinates/labels "
            "before clicking again."
        )
    if fn_name == "run_python_sandbox" or "traceback" in low:
        return (
            "\n[CRITIC VERIFIER — ENGINEER CODE SELF-CORRECTION]: Your Python snippet raised "
            "a runtime error/traceback. Read the stderr carefully, fix the bug in your code, "
            "and re-run run_python_sandbox to verify exit=0 before giving your final answer."
        )
    return (
        f"\n[CRITIC VERIFIER — SELF-CORRECTION REQUIRED]: Tool '{fn_name}' returned an error "
        "(sensory-motor mismatch). Analyze the error, adjust arguments or try an alternate tool once."
    )


# ---------------------------------------------------------------------------
# Implicit correction detection
# ---------------------------------------------------------------------------


def _detect_implicit_correction(prompt: str) -> tuple[str, str] | None:
    """Detects explicit user correction/preference statements so Celina remembers them permanently."""
    clean = prompt.strip()
    if len(clean) < 12 or len(clean) > 220:
        return None
    if any(p.search(clean) for p in _IMPLICIT_CORRECTION_PATTERNS):
        digest = hashlib.sha1(clean.lower().encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
        return f"preference_{digest}", clean
    return None


# ---------------------------------------------------------------------------
# Action gate
# ---------------------------------------------------------------------------

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
    """Deterministic ActionGate: Returns True when the prompt is clearly an educational /
    conceptual / how-to question with no imperative request to mutate the user's machine.
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
    """Suppresses mutating/side-effecting tools on clearly informational questions so small
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


# ---------------------------------------------------------------------------
# Stream preview formatting
# ---------------------------------------------------------------------------


def _strip_analysis_tags(text: str) -> str:
    cleaned = _ANALYSIS_BLOCK_RE.sub("", text or "").strip()
    return cleaned or (text or "").strip()


def _format_stream_preview(raw_text: str, vault: privacy.RedactionVault | None = None) -> str:
    """Strips complete or in-progress <analysis> blocks and partial redaction tokens during streaming."""
    cleaned = _ANALYSIS_BLOCK_RE.sub("", raw_text or "")
    cleaned = _OPEN_ANALYSIS_RE.sub("", cleaned)
    if vault is not None:
        cleaned = vault.restore(cleaned)
    cleaned = _PARTIAL_REDACT_RE.sub("", cleaned)
    return cleaned.strip()
