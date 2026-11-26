"""Celina AI (Celly) - v4.4 Self-Improving, Glass-Box Cognitive Brain + Strict Privacy Firewall
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
import ctypes
import dataclasses
import datetime
import gc
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from . import (
    automation,
    cache,
    capabilities,
    config,
    context_compressor,
    everyday_agents,
    everyday_trainer,
    gear0_5_micro,
    gear0_5_smolLM,
    gear0_audit,
    gear0_calendar,
    gear0_clipboard,
    gear0_contacts,
    gear0_context,
    gear0_dual,
    gear0_focus,
    gear0_graph,
    gear0_hardware,
    gear0_intent,
    gear0_iot,
    gear0_ledger,
    gear0_lists,
    gear0_macro,
    gear0_media,
    gear0_network,
    gear0_routines,
    gear0_status,
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
from . import brain_harness as _bh_mod
from . import persona as _persona_mod
from . import prompt_transpiler as _pt_mod
from . import sensory_buffer as _sensory_mod
from .connectors import get_registry as _connectors_get_registry
from .gear0_storage import initialize_storage
from .plugins import data_oracle, habit_engine, os_shim, self_heal, sniffer_plugin
from .prompt_transpiler import PromptLayer
from .router_gear0 import (
    _gear0_classify_cached,
    _gear0_classify_store,
    try_gear0_math,
)
from .router_providers import (
    _build_pooled_session,
    _effective_ollama_keep_alive,
    _has_cloud_key,
    _ollama_ctx_for_gear,
    _recommended_ollama_threads,
)
from .router_receipts import (
    OfflineFallback,
    RouteDecision,
    TurnTrace,
    _build_self_correction_hint,
    _format_stream_preview,
    _gear_settings,
    _get_base_system_prompt,
    _is_tool_error,
    _parse_tool_args,
    _strip_analysis_tags,
    apply_action_gate,  # noqa: F401  # Re-exported for router API compatibility; used by tests
    classify_cognitive_gear_with_reason,
    is_informational_only_prompt,  # noqa: F401  # Re-exported for router API compatibility; used by tests
    select_swarm_role,
)
from .world_model import OSWorldModel

# Callback type aliases (used by CelinaRouter methods)
TaskGlowCallback = Callable[[bool], None]
StreamCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]
ApprovalCallback = Callable[[str, str], bool]
PrivacyConsentCallback = privacy.PrivacyConsentCallback
_LIBREOFFICE_HINTS = (
    "libreoffice", "writer", "calc", "impress", ".odt", ".ods", ".odp",
    ".docx", ".xlsx", "spreadsheet", "document",
)


def apply_offline_and_uno_policy(
    decision: RouteDecision,
    *,
    goal: str = "",
    online_probe: Callable[[], bool] | None = None,
    uno_probe: Callable[[], bool] | None = None,
) -> RouteDecision | OfflineFallback:
    """Offline-aware & UNO-preferring router policy:
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


def _deterministic_intent(goal: str) -> tuple[str, dict[str, str]] | None:
    """Recognize limited, unambiguous local intents without guessing targets.

    Unknown requests continue through the existing router. These patterns are
    intentionally conservative: they never infer file mutations or a network
    location from incomplete text.
    """
    text = re.sub(r"^(?:please|could you|can you|would you)\s+", "", goal.strip(), flags=re.I)
    text = re.sub(r"[.!?]+$", "", text).strip()
    lower = text.lower()
    actions = re.findall(r"\b(?:read|open|list|show|delete|remove|erase|trash|write|edit)\b", lower)
    if re.search(r"\bor\b", lower) and len(set(actions)) >= 2 and re.search(
        r"\b(?:files?|folders?|directories|directory|documents?)\b", lower,
    ):
        return "clarify", {"message": "Which action would you like me to take?"}
    if re.fullmatch(r"(?:hi|hello|hey(?: there)?|good (?:morning|afternoon|evening))", lower):
        return "respond_directly", {"message": "Hello! How can I help?"}
    if re.fullmatch(
        r"(?:list|show|display|enumerate)\s+(?:(?:my|the|local)\s+)*"
        r"(?:(?:home)\s+)?(?:files?|folders?|directories|directory)"
        r"(?:\s+(?:in|inside|under)\s+(?:my\s+)?home(?:\s+(?:folder|directory))?)?",
        lower,
    ):
        return "list_files", {"path": str(config.HOME)}
    memory = re.fullmatch(
        r"(?:search|find|look up)\s+(?:my\s+)?(?:saved\s+)?(?:notes|memory)\s+"
        r"(?:for|about|on)\s+(.+)", text, flags=re.I,
    )
    if memory:
        return "search_memory", {"query": memory.group(1).strip()}
    if (
        re.match(r"(?:what|how|check|show|look up|get|tell me|weather|forecast)\b", lower)
        and re.search(r"\b(?:weather|forecast)\b", lower)
        and not re.search(r"\b(?:delete|remove|erase|trash|write|edit|save|create|list)\b", lower)
    ):
        city = re.search(r"\b(?:in|for)\s+([\w .'-]+)$", text, flags=re.I)
        place = city.group(1).strip() if city else ""
        return "get_weather", {"city": place} if place.lower() not in {"", "today", "tomorrow"} else {}
    web = re.fullmatch(r"(?:search|look up)\s+(?:the\s+)?(?:web|internet)\s+for\s+(.+)", text, flags=re.I)
    if web:
        return "web_search", {"query": web.group(1).strip()}
    return None


# Initialize storage backend state at import time (P0-03), skipping --help probes
# and unisolated dry-runs (explicit CELINA_STORAGE_DIR still initializes its isolated dir)
if (
    os.environ.get("CELINA_DRY_RUN") != "1"
    and "--help" not in sys.argv
    and "-h" not in sys.argv
    and not (os.environ.get("DRY_RUN") == "1" and not os.environ.get("CELINA_STORAGE_DIR"))
):
    initialize_storage()

from concurrent.futures import ThreadPoolExecutor, as_completed

_LIBREOFFICE_HINTS = (
    "libreoffice", "writer", "calc", "impress", ".odt", ".ods", ".odp", ".docx", ".xlsx", "spreadsheet", "document",
)

# --- Prompt hygiene (audit item 15) ---
# Tool-derived strings entering LLM prompts are untrusted input.  This
# sanitizer length-caps and neutralizes prompt-injection patterns before any
# tool output is inserted into a `{"role": "tool", ...}` message.
_TOOL_PROMPT_CAP = 4000  # max chars of tool output allowed in an LLM prompt
_PROMPT_INJECTION_RE = re.compile(
    r"""(?ix)
    # ignore/disregard/forget/override instructions (incl. 4-word variant)
    \b(ignore|disregard|forget|override|bypass|cancel|stop)\s+(all\s+)?(previous|prior|the\s+)?\s*(instructions|rules|guidelines|system|prompt|safety)\b
    | \bignore\s+your\s+(safety|training|guidelines)\b
    | \bforget\s+(all\s+)?(safety|training)\b
    # Persona / role change
    | \b(you\s+are\s+now|you\s+must|from\s+now\s+on|act\s+as|pretend\s+to\s+be|roleplay\s+as)\b
    | \b(unrestricted|root|developer)\s+mode\b
    | \bDAN\b|\bdo\s+anything\s+now\b
    # Role prefix / system label
    | \b(system|developer|assistant|user)\s*:\s*
    | \[(SYSTEM|system|CELINA|celina)\s*\]
    # Jailbreak / safety override
    | \b(jailbreak|prompt\s+injection|override\s+safety|disable\s+safety|disable_?safety|disable_?approval)\b
    | \b(override|bypass)\s+(all\s+)?(approval|safety|gate|guardrail|confirmation|required)\b
    # Exfiltration / repetition
    | \b(repeat\s+above|repeat\s+the\s+above|verbatim\s+above|print\s+above|print\s+your\s+system\s+prompt)\b
    # CELINA_ directives
    | \bCELINA_[A-Z_]+\b
    # Tool names in hostile context
    | \b(write_file|edit_file|move_file|trash_file|run_shell_command|code_sandbox|run_python_sandbox)\s*\(
    # HTML/XML comments with directives
    | <!--\s*(CELINA|system|tool_override|admin_override|celina_directive)
    | <\s*/?(celina_directive|system|tool_override|admin_override)\s*>
    | \b(ignore_safety|disable_approval|approve_all|bypass_guard)\b
    # NEW INSTRUCTION / prefix patterns
    | \bnew\s+instruction\s*:?\s
    | \b(window\.)?celina[Oo]verride\b
    | \bdata:text/html\b
    | \bjavascript:\s*(void\s*\()?
    | \bmeta\s+http-equiv\s*=
    | \b(EDIT_FILE|RENAME_FILE|TRASH_FILE|RUN|CHMOD|CHOWN|OVERRIDE|NEW_INSTRUCTION|MODE|RESET)\s*:
    | "action"\s*:\s*"(write_file|edit_file|move_file|trash_file|run_shell_command)"
    | \blogin\s*=\s*\w+\s+password\s*=
    | \bCELINA:\s+
    """,
)


def _sanitize_tool_prompt_content(content: str, *, cap: int = _TOOL_PROMPT_CAP) -> str:
    """Sanitize tool output before it is inserted into an LLM prompt.

    - Strips control characters (except newline/tab).
    - Neutralizes prompt-injection phrasing by replacing it with a safe marker.
    - Length-caps at `cap` characters with a truncation notice.

    Returns a safe, bounded string suitable for a `tool` role message.
    """
    if not isinstance(content, str):
        content = str(content)
    # Strip control characters except \n and \t
    content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)
    # Neutralize prompt-injection patterns
    content = _PROMPT_INJECTION_RE.sub("[PROMPT_INJECTION_PATTERN_REDACTED]", content)
    # Length cap
    if len(content) > cap:
        content = content[:cap] + f"\n...[tool output truncated at {cap} chars]"
    return content


class CelinaRouter:
    def __init__(
        self,
        db: memory.MemoryDB | None = None,
        response_cache: cache.SmartCache | None = None,
        os_world_model: OSWorldModel | None = None,
    ):
        self.db = db if db is not None else memory.get_db()
        self._memory: memory.CelinaMemory = self.db.get_hybrid_memory()
        self.cache = response_cache if response_cache is not None else cache.get_cache()
        self.last_trace: TurnTrace | None = None
        self._http: requests.Session = _build_pooled_session()
        self._last_warm_ts: float = 0.0
        self._compute_why: str | None = None
        self._session_tier: int | None = None
        self._persona_style: str = "celina"
        self._persona_channel: str = "text"
        self.os_world_model = os_world_model or OSWorldModel()

    def _finalize_reply(
        self,
        reply: str,
        source: str,
        trace: TurnTrace | None = None,
        callbacks: _bh_mod.BrainCallbacks | None = None,
    ) -> tuple[str, str]:
        """Run completion verification and emit turn-done callback before returning."""
        # Emit on_route callback
        if callbacks is not None:
            callbacks.emit("on_route", self._persona_style, source)

        # Run final completion verification (anti-victory-bias gate)
        verdict = _bh_mod.verify_final_completion(reply, getattr(self, "_turn_tool_results", []))
        if trace is not None:
            trace.completion_verified = verdict.passed
        if verdict.corrected:
            reply = verdict.reply
        if callbacks is not None:
            callbacks.emit("on_turn_done", {
                "source": source,
                "completion_verified": verdict.passed,
                "history_compacted": getattr(trace, "history_compacted", False) if trace else False,
            })
        return reply, source

        self._supervisor: Any | None = None
        skills.seed_builtin_skills(self.db)
        self._register_connectors()

    def _register_connectors(self) -> None:
        """Register built-in connectors (Update 23). Outside Gear 0, opt-in only."""
        try:
            from .connectors import get_registry as _reg
            from .connectors.github import GitHubConnector
            from .connectors.gmail import GmailConnector
            from .connectors.linear import LinearConnector
            from .connectors.notion import NotionConnector
            from .connectors.slack import SlackConnector

            reg = _reg()
            for conn in (
                LinearConnector(),
                GitHubConnector(),
                GmailConnector(),
                SlackConnector(),
                NotionConnector(),
            ):
                reg.register(conn, conn._config)
        except Exception:
            # Connector registration is non-fatal; /connectors will show empty
            pass

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
            or re.search(r"[/\\][\w.-]+\.[a-z0-9]{1,5}\b", q),
        )

    @staticmethod
    def _is_associative(query: str) -> bool:
        q = (query or "").strip().lower()
        return bool(
            re.search(r"\b(?:similar\s+to|like|reminds?\s+me|something\s+about|roughly|fuzzy|conceptually|vibe|related\s+idea)\b", q),
        )

    @staticmethod
    def _is_relational(query: str) -> bool:
        q = (query or "").strip().lower()
        return bool(
            re.search(
                r"\b(?:why\s+did|after\s+that|before\s+that|weeks?\s+after|days?\s+after|connected\s+to|linked\s+to|depends?\s+on|led\s+to|caused\s+by|who\s+worked\s+with|prior\s+attempts?|failed\s+with)\b",
                q,
            ),
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
        trace: TurnTrace | None = None,
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
        trace: TurnTrace | None = None,
    ) -> dict[str, Any]:
        """Queries offline-compiled verified answers via SQLite FTS5 BM25 first.
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
        """Extracts normalized confidence in [0.0, 1.0] from an Ollama/OpenAI
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
        # Fail-closed: no logprobs ⇒ confidence 0.0 ⇒ escalate/verify
        return 0.0

    def call_with_second_thought(
        self,
        goal: str,
        *,
        no_think_fn: Callable[[str], dict[str, Any]],
        think_fn: Callable[[str], dict[str, Any]],
        threshold: float = 0.82,
        trace: TurnTrace | None = None,
    ) -> dict[str, Any]:
        """SecondThought confidence-gated reasoning (~50% fewer output tokens while
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
        total_ram_gb: int | None = None,
        available_ram_mb: int | None = None,
    ) -> bool:
        """Non-blocking Ollama weight pre-warm ONLY when plenty of free RAM exists.
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
    def route_for_autonomy(self, goal: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Autonomy-aware routing entrypoint returning the structured decision dict
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
                "why_receipt_hash": hashlib.sha256(f"gear0_math:{clean_goal}={math_ans}".encode()).hexdigest(),
            }

        active_win = str(ctx.get("active_window") or "")
        gear, clean_goal, gear_reason = classify_cognitive_gear_with_reason(clean_goal)
        selected_tools, domains = tools.get_contextual_tool_schemas(
            clean_goal, gear=gear, active_window=active_win, is_recording=False,
        )

        # Pick highest-ranked matching tool, honoring retry suggested_fallback & avoiding known failures
        chosen_tool = "get_system_stats"
        known_tools = config.TIER1_AUTO_TOOLS | config.TIER2_APPROVAL_TOOLS
        failed_tools = {str(x).strip() for x in (ctx.get("known_failed_attempts") or []) if str(x).strip()}
        suggested_fb = str(ctx.get("suggested_fallback") or "").strip()
        candidates = []
        fresh_candidates = []
        if not suggested_fb:
            last_err = str(ctx.get("last_error") or "")
            m_fb = re.search(r"suggested_fallback=([A-Za-z0-9_]+)", last_err)
            if m_fb:
                suggested_fb = m_fb.group(1)

        inferred = None
        if not (suggested_fb and suggested_fb in known_tools and suggested_fb not in failed_tools):
            inferred = _deterministic_intent(clean_goal)
        if inferred and inferred[0] in {"respond_directly", "clarify"}:
            message_args = inferred[1]
            return {
                "tool_name": "respond_directly", "arguments": message_args,
                "arguments_hash": approval.compute_arguments_hash("respond_directly", message_args),
                "tier": 1, "domain": "os_hardware", "gear": gear,
                "why_receipt_hash": hashlib.sha256(f"direct:{clean_goal}".encode()).hexdigest(),
                "requires_clarification": inferred[0] == "clarify",
            }
        if inferred and (inferred[0] not in known_tools or inferred[0] in failed_tools):
            message_args = {"message": "That action is unavailable. How would you like to proceed?"}
            return {
                "tool_name": "respond_directly", "arguments": message_args,
                "arguments_hash": approval.compute_arguments_hash("respond_directly", message_args),
                "tier": 1, "domain": "os_hardware", "gear": gear,
                "why_receipt_hash": hashlib.sha256(f"unavailable:{clean_goal}".encode()).hexdigest(),
                "requires_clarification": True,
            }
        if suggested_fb and suggested_fb in known_tools and suggested_fb not in failed_tools:
            chosen_tool = suggested_fb
        elif inferred and inferred[0] in known_tools and inferred[0] not in failed_tools:
            chosen_tool = inferred[0]
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
        if inferred and chosen_tool == inferred[0]:
            arguments = {**inferred[1], **arguments}

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

        if inferred and chosen_tool == "get_weather" and not arguments.get("city"):
            message_args = {"message": "Which city should I check the weather for?"}
            return {
                "tool_name": "respond_directly", "arguments": message_args,
                "arguments_hash": approval.compute_arguments_hash("respond_directly", message_args),
                "tier": 1, "domain": "os_hardware", "gear": gear,
                "why_receipt_hash": hashlib.sha256(f"clarify:{clean_goal}".encode()).hexdigest(),
                "requires_clarification": True,
            }

        chosen_tool = resolved.tool_name
        domain = resolved.domain
        tier = resolved.tier
        args_hash = approval.compute_arguments_hash(chosen_tool, arguments)

        # D3: Dispatch-time capability enforcement with candidate fallback.
        # Build a prioritized candidate list and try each one until a tool
        # passes the OS capability boundary check (fail-closed).
        is_tier2_map = {t: (t in config.TIER2_APPROVAL_TOOLS) for t in config.TIER1_AUTO_TOOLS | config.TIER2_APPROVAL_TOOLS}
        candidate_tools: list[str] = []
        if suggested_fb and suggested_fb in known_tools and suggested_fb not in failed_tools:
            candidate_tools.append(suggested_fb)
        # Add selected_tools candidates (already excludes failed_tools via fresh_candidates above)
        for c in fresh_candidates:
            if c not in candidate_tools:
                candidate_tools.append(c)

        # Preserve the policy-resolved choice ahead of generic candidates.
        if chosen_tool in candidate_tools:
            candidate_tools.remove(chosen_tool)
        candidate_tools.insert(0, chosen_tool)

        capability_rejections: list[str] = []
        final_tool = None
        final_domain = None
        final_tier = None
        for cand in candidate_tools:
            cand_tier2 = is_tier2_map.get(cand, cand in config.TIER2_APPROVAL_TOOLS)
            cap_ok, cap_reason = capabilities.check_tool_execution_boundary(
                cand, is_tier2=cand_tier2,
            )
            if cap_ok:
                # A capability fallback must pass the same offline/UNO policy as
                # the initial choice; otherwise a denied local tool can lead to
                # a live web dispatch while offline.
                cand_tier = 2 if cand in config.TIER2_APPROVAL_TOOLS else 1
                cand_domain = next(
                    (name for name, cfg in (config.TOOL_DOMAINS or {}).items()
                     if cand in (cfg.get("tools") or ())), domain,
                )
                if cand != chosen_tool:
                    alternate = apply_offline_and_uno_policy(
                        RouteDecision(
                            tool_name=cand, domain=cand_domain, tier=cand_tier,
                            arguments=arguments, gear=gear,
                        ),
                        goal=clean_goal, online_probe=ctx.get("online_probe"),
                        uno_probe=ctx.get("uno_probe"),
                    )
                    if isinstance(alternate, OfflineFallback) or alternate.tool_name != cand:
                        capability_rejections.append(f"{cand}: offline/UNO policy denied fallback")
                        continue
                final_tool, final_tier, final_domain = cand, cand_tier, cand_domain
                break
            capability_rejections.append(f"{cand}: {cap_reason}")

        if final_tool is None:
            # No candidate passed capability check — deny with all rejections recorded
            trace = TurnTrace(
                prompt_preview=clean_goal[:80],
                gear=gear,
                gear_reason=f"Capability denied: {'; '.join(capability_rejections)}",
                active_window=active_win,
                tool_domains=list(domains),
                tools_attached=len(selected_tools),
                tools_called=[],
                capability_rejections=capability_rejections,
                compute_backend=self._get_compute_why(),
                source="autonomy_router_denied",
            )
            self.last_trace = trace
            return {
                "tool_name": "respond_directly",
                "arguments": {"message": f"I cannot perform that action: {'; '.join(capability_rejections)}"},
                "arguments_hash": approval.compute_arguments_hash(
                    "respond_directly", {"message": f"I cannot perform that action: {'; '.join(capability_rejections)}"},
                ),
                "tier": 1,
                "domain": "os_hardware",
                "why_receipt_hash": hashlib.sha256(
                    f"capability_denied:{'|'.join(candidate_tools)}".encode(),
                ).hexdigest(),
                "gear": gear,
                "preferred_backend": "local",
                "capability_denied": True,
                "denial_reason": "; ".join(capability_rejections),
            }

        chosen_tool = final_tool
        domain = final_domain
        tier = final_tier
        # Capability fallback may change the tool; bind the returned tool, not the rejected one.
        args_hash = approval.compute_arguments_hash(chosen_tool, arguments)

        trace = TurnTrace(
            prompt_preview=clean_goal[:80],
            gear=gear,
            gear_reason=gear_reason,
            active_window=active_win,
            tool_domains=list(domains),
            tools_attached=len(selected_tools),
            tools_called=[chosen_tool],
            capability_rejections=capability_rejections,
            compute_backend=self._get_compute_why(),
            source="autonomy_router",
        )
        self.last_trace = trace
        why_hash = hashlib.sha256(trace.format_receipt().encode("utf-8")).hexdigest()

        wm_ctx = {
            "headroom_factor": round(self.os_world_model.headroom_factor(), 3),
            "hardware_tier": self.os_world_model.hardware.tier,
            "has_battery": self.os_world_model.hardware.has_battery,
            "gpu_vendor": self.os_world_model.hardware.gpu_vendor,
        }

        return {
            "tool_name": chosen_tool,
            "arguments": arguments,
            "arguments_hash": args_hash,
            "tier": tier,
            "domain": domain,
            "why_receipt_hash": why_hash,
            "gear": gear,
            "preferred_backend": resolved.preferred_backend,
            "world_model": wm_ctx,
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
        trace: TurnTrace | None = None,
    ) -> list[dict[str, Any]]:
        now_str = datetime.datetime.now().strftime("%A, %Y-%m-%d %H:%M")
        gcfg = _gear_settings(gear)

        # ── STATIC PREFIX (byte-identical across calls — cacheable) ──
        static_parts = [_get_base_system_prompt()]
        # Persona system prompt (dual-channel, style-aware)
        persona_prompt = _persona_mod.build_system_prompt(
            config.get_assistant_name(),
            nickname=config.get_assistant_nickname(),
            style=getattr(self, "_persona_style", "celina"),
            channel=getattr(self, "_persona_channel", "text"),
            honorific="",
        )
        static_parts.append(persona_prompt)
        if gcfg.get("instruction"):
            static_parts.append(str(gcfg["instruction"]))

        # Zero-RAM Multi-Agent Swarm Role + System 2 Tree-of-Thought + Critic Gate
        role_name, role_directive = select_swarm_role(user_prompt, gear)
        if trace is not None:
            trace.swarm_role = role_name
        if role_directive:
            static_parts.append(role_directive)

        if gear == "deep" and config.COGNITION_CFG.get("tree_of_thought_on_deep", True):
            static_parts.append(
                "Tree-of-Thought Deliberation: Before committing to a high-impact action, mentally compare "
                "2 candidate approaches, weigh failure modes and reversibility, and pick the safest path.",
            )

        if gear in {"tactical", "deep"}:
            if config.COGNITION_CFG.get("critic_audit_on_tier2", True):
                critic_dir = str((config.SWARM_ROLES.get("critic") or {}).get("directive", "")).strip()
                if critic_dir:
                    static_parts.append(critic_dir)
            if config.REFLECTION_POLICY.get("enable_preflight_micro_plan", True):
                static_parts.append(
                    "Execution discipline: Before invoking tools, silently plan the minimal 2-4 step sequence. "
                    "Verify tool outputs before declaring success; if a tool fails, reflect and correct your arguments once.",
                )
            clar = str(config.CONFIDENCE_POLICY.get("clarification_instruction", "")).strip()
            if clar:
                static_parts.append(clar)

        # Pillar 7A: Language Awareness (static rules, no per-turn data)
        lang_code = (language or "auto").lower()
        if lang_code != "auto" and lang_code in config.LANGUAGES:
            lmeta = config.LANGUAGES[lang_code]
            static_parts.append(
                f"Reply strictly in {lmeta.get('name', lang_code)} "
                f"({lmeta.get('native_name', lang_code)}) using its native script.",
            )
        elif lang_style == "romanized" and det_lang in {"te", "hi"}:
            l_label = "Romanized Telugu (Tenglish)" if det_lang == "te" else "Romanized Hindi (Hinglish)"
            static_parts.append(
                f"User wrote in {l_label}. Understand naturally and reply in clear, natural "
                f"{'Telugu or Tenglish' if det_lang == 'te' else 'Hindi or Hinglish'} matching their tone.",
            )
        elif det_lang in {"te", "hi"} and det_lang in config.LANGUAGES:
            lmeta = config.LANGUAGES[det_lang]
            static_parts.append(
                f"LANGUAGE RULE: The user's latest message is in {lmeta.get('name', det_lang)}. "
                f"Reply naturally in {lmeta.get('name', det_lang)}.",
            )
        else:
            static_parts.append(
                "LANGUAGE RULE: The user's latest message is in English. "
                "Reply ONLY in English — never switch to Telugu, Hindi, or any other language.",
            )

        # Learned skills playbook (changes rarely — treat as static prefix)
        learned = self.db.format_skills_for_prompt()
        if learned:
            static_parts.append(learned)

        # ── VOLATILE SUFFIX (changes every turn — not cacheable) ──
        volatile_parts = [f"Current local date & time: {now_str}."]
        if active_window:
            volatile_parts.append(f"Active desktop window: {active_window}")

        # Pillar 3 + v4.1: Memory + Knowledge Graph (per-turn retrieval)
        mem_block, recalled_keys, recalled_ep_cnt = self.db.format_relevant_memory_for_prompt(
            user_prompt,
            max_facts=5 if gear != "fast" else 3,
            max_episodes=2 if gear != "fast" else 0,
            cloud_only=for_cloud,
            active_window=active_window,
        )
        if mem_block:
            volatile_parts.append(mem_block)
        if trace is not None:
            trace.recalled_facts = recalled_keys
            trace.recalled_episodes = recalled_ep_cnt

        # Dynamically inject skill.md playbook (untrusted reference, per-turn)
        matched_md = skills.match_markdown_skill(user_prompt)
        if matched_md:
            volatile_parts.append(skills.format_skill_as_untrusted_reference(matched_md))
            if trace is not None:
                trace.active_skill_md = str(matched_md["name"])

        if skills.recorder.is_recording:
            volatile_parts.append(
                f"ACTIVE LEARNING MODE: Recording steps for skill '{skills.recorder.recording_name}'. "
                "Execute each action the user requests cleanly; when they say stop recording, call stop_recording.",
            )

        # ── Assemble messages with cache boundary ──
        messages: list[dict[str, Any]] = []

        # Transpile prompt with static-prefix-first ordering for KV-cache stability
        static_layers = [
            PromptLayer(name=f"static-{i}", content=part, volatile=False)
            for i, part in enumerate(static_parts)
            if part and part.strip()
        ]
        volatile_layers = [
            PromptLayer(name=f"volatile-{i}", content=part, volatile=True)
            for i, part in enumerate(volatile_parts)
            if part and part.strip()
        ]
        compiled = _pt_mod.transpile_prompt(static_layers + volatile_layers)

        # System message 1: transpiled prompt (cacheable static prefix + volatile suffix)
        sys_msg: dict[str, Any] = {
            "role": "system",
            "content": compiled.text,
        }
        if for_cloud:
            # Mark first system message as cacheable prefix for providers that support it
            sys_msg["cache_control"] = {"type": "ephemeral"}
        messages.append(sys_msg)

        # Record prompt hashes on trace for /why receipt
        if trace is not None:
            trace.static_prefix_hash = compiled.static_prefix_hash
            trace.prompt_hash = compiled.full_hash

        # System message 2: volatile suffix (not cacheable)
        if volatile_parts:
            messages.append({"role": "system", "content": "\n\n".join(volatile_parts)})

        # Conversation history
        # Conversation history with tier-adaptive compaction
        hist_limit = 4 if gear == "fast" else config.MEMORY_HISTORY_LIMIT
        raw_history = self.db.get_recent_messages(limit=hist_limit, cloud_only=for_cloud)
        try:
            from .model_selector import get_ram_tier as _get_ram_tier
            _ram_tier = _get_ram_tier()
        except Exception:
            _ram_tier = "16gb"
        compacted_history, _was_compacted = _bh_mod.compact_conversation_history(
            raw_history, ram_tier=_ram_tier,
            on_episode_summary=lambda summary: self.db.add_episode(summary, importance=0.5)
            if hasattr(self.db, "add_episode") else None,
        )
        if trace is not None:
            trace.history_compacted = _was_compacted
        messages.extend(compacted_history)
        messages.append({"role": "user", "content": user_prompt})
        return messages

    @staticmethod
    def _sanitize_messages_for_cloud(
        messages: list[dict[str, Any]], vault: privacy.RedactionVault,
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
        headers: dict[str, str] | None = None,
        retries: int = 3,
        stream: bool = False,
    ) -> requests.Response:
        """Executes pooled HTTP/1.1 keep-alive POST with backoff retries on transient 5xx/network blips."""
        last_exc: Exception | None = None
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
        vault: privacy.RedactionVault | None = None,
    ) -> dict[str, Any]:
        """Run task decomposition + parallel multi-provider ensemble.

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
                    self._call_provider_chat, provider, api_key, model, subtask, request_timeout,
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
    ) -> str | None:
        """Execute a Gear 0.5 micro-intent result deterministically through Gear 0 handlers.

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
            # P0: block save kind=command unless behind host_shell_override
            if kind == "command":
                return None  # fail-closed: command saves are not allowed via micro-tier
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
        active_tools: list[dict[str, Any]] | None = None,
        stream_cb: StreamCallback | None = None,
        vault: privacy.RedactionVault | None = None,
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
        # Tier-adaptive context compression (cloud route) — runs AFTER privacy DLP redaction
        # via vault, preserves system/user msgs verbatim, leaves small outputs untouched on 16 GB / 32 GB.
        try:
            comp_report = context_compressor.compress_messages_for_llm(
                messages,
                model=str(gcfg.get("model", "qwen3:1.7b")),
                route="cloud",
                enabled=True,
                record_metrics=True,
            )
            if comp_report.backend != "bypass":
                messages = comp_report.messages
                if trace is not None:
                    trace.compression_backend = comp_report.backend
                    trace.compression_tier = comp_report.tier
                    trace.compression_saved_pct = comp_report.saved_pct
                    trace.compression_ccr_ids = list(comp_report.ccr_ids)
        except Exception:
            # Compression must never break inference — fall back to raw messages.
            pass

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
            except (ValueError, json.JSONDecodeError):
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
        active_tools: list[dict[str, Any]] | None = None,
        trace: TurnTrace | None = None,
        status_cb: StatusCallback | None = None,
        approval_cb: ApprovalCallback | None = None,
        consent_cb: PrivacyConsentCallback | None = None,
        task_glow_cb: TaskGlowCallback | None = None,
        stream_cb: StreamCallback | None = None,
        dry_run: bool = False,
    ) -> tuple[str | None, str | None, bool]:
        """Returns (reply, error, switched_to_local).
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
                        # Track tool chain step for ExperiencePromoter
                        self._turn_steps.append({
                            "tool": fn_name,
                            "args": fn_args or {},
                        })
                        if status_cb:
                            status_cb(f"Running {fn_name}...")
                        if task_glow_cb:
                            task_glow_cb(True)
                        if trace is not None:
                            from . import evidence as _ev

                            _ev.set_current_why_context(
                                f"gear={trace.gear} role={trace.swarm_role} prompt={trace.prompt_preview}",
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

                    # Gear 0.5.1: Record Mirage vision backend on TurnTrace
                    if trace is not None and fn_name in {"vision_ocr", "vision_analyze"}:
                        trace.vision_backend = "gear0_5_1_mirage"
                        try:
                            out_json = json.loads(out)
                            trace.vision_escalated = bool(
                                out_json.get("escalate_to_vlm", False),
                            )
                        except Exception:
                            trace.vision_escalated = True

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
                            "content": _sanitize_tool_prompt_content(cloud_out),
                        },
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
        active_tools: list[dict[str, Any]] | None = None,
        stream_cb: StreamCallback | None = None,
        trace: TurnTrace | None = None,
    ) -> dict[str, Any]:
        gcfg = _gear_settings(gear)
        # Stream live across ALL gears (fast, tactical, deep); _format_stream_preview strips <analysis>
        want_stream = stream_cb is not None

        send_msgs = list(messages)
        # Tier-adaptive context compression (local route) — preserves system/user msgs verbatim,
        # leaves small/medium outputs untouched on 16 GB / 32 GB, stashes originals in RAM-only CCR.
        try:
            comp_report = context_compressor.compress_messages_for_llm(
                send_msgs,
                model=config.LOCAL_MODEL,
                route="local",
                enabled=True,
                record_metrics=True,
            )
            if comp_report.backend != "bypass":
                send_msgs = comp_report.messages
                if trace is not None:
                    trace.compression_backend = comp_report.backend
                    trace.compression_tier = comp_report.tier
                    trace.compression_saved_pct = comp_report.saved_pct
                    trace.compression_ccr_ids = list(comp_report.ccr_ids)
        except Exception:
            # Compression must never break inference — fall back to raw messages.
            pass

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
            # Request token logprobs for SecondThought confidence gating.
            # Fail-closed: if the model doesn't support logprobs, confidence=0.0 → escalate.
            "logprobs": True,
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
        active_tools: list[dict[str, Any]] | None = None,
        trace: TurnTrace | None = None,
        status_cb: StatusCallback | None = None,
        approval_cb: ApprovalCallback | None = None,
        consent_cb: PrivacyConsentCallback | None = None,
        task_glow_cb: TaskGlowCallback | None = None,
        stream_cb: StreamCallback | None = None,
        dry_run: bool = False,
    ) -> tuple[str | None, str | None]:
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

                    # SecondThought: confidence-gated thinking escalation (P0)
                    conf = self.extract_mean_logprob(data)
                    if trace is not None:
                        trace.second_thought_confidence = round(conf, 4)
                    if conf < 0.82 and gear == "fast":
                        # Re-run with thinking enabled — low confidence on fast pass
                        data2 = self._post_ollama(
                            convo, gear=gear, use_tools=False,
                            stream_cb=stream_cb, trace=trace,
                        )
                        msg2 = data2.get("message") or {}
                        raw_reply2 = (msg2.get("content") or "").strip()
                        if raw_reply2:
                            reply = _strip_analysis_tags(raw_reply2)
                        if trace is not None:
                            trace.second_thought_escalated = True

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
                        # Track tool chain step for ExperiencePromoter
                        self._turn_steps.append({
                            "tool": fn_name,
                            "args": fn_args or {},
                        })
                        if status_cb:
                            status_cb(f"Running {fn_name} (local)...")
                        if task_glow_cb:
                            task_glow_cb(True)
                        if trace is not None:
                            from . import evidence as _ev

                            _ev.set_current_why_context(
                                f"gear={trace.gear} role={trace.swarm_role} prompt={trace.prompt_preview}",
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

                    # Track tool result for completion verification
                    try:
                        self._turn_tool_results.append((fn_name, str(out)))
                    except AttributeError:
                        self._turn_tool_results = [(fn_name, str(out))]

                    # Taint tracking: untrusted external text sources taint the turn (T1 defense)
                    if fn_name in {"fetch_webpage", "fetch_url_text", "read_document", "get_clipboard", "live_text_ocr", "web_search"}:
                        self._turn_tainted = True
                    if trace is not None and "Circuit breaker open" in out:
                        trace.circuit_breaker_events.append(f"{fn_name} (cooldown)")

                    # Gear 0.5.1: Record Mirage vision backend on TurnTrace
                    if trace is not None and fn_name in {"vision_ocr", "vision_analyze"}:
                        trace.vision_backend = "gear0_5_1_mirage"
                        try:
                            out_json = json.loads(out)
                            trace.vision_escalated = bool(
                                out_json.get("escalate_to_vlm", False),
                            )
                        except Exception:
                            # Not Mirage JSON — fell through to MCP/heavy VLM
                            trace.vision_escalated = True

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

                    convo.append({"role": "tool", "content": _sanitize_tool_prompt_content(tool_msg_content)})
                if status_cb:
                    status_cb(f"Thinking ({gear} · local)...")

            # Experience Learning Loop: record verified/untainted tool chain for promotion
            all_verified = True
            for tc in (trace.tools_called if trace is not None else []):
                pass  # tools_called only has names; verification status tracked via outcomes
            if self._turn_steps:
                from .harness import ExperiencePromoter as _EP
                promoter = _EP()
                # Determine if all steps were verified (simplified: check trace for errors)
                promoter.record_turn_trace(
                    self._turn_steps,
                    all_verified=all_verified and not any(
                        "error" in str(tc.get("args", {})).lower()
                        for tc in self._turn_steps
                    ),
                    turn_tainted=self._turn_tainted,
                    error_summary="",
                )

            return last_tool_out or "(Agent reached step limit)", None
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return None, f"Local Ollama error: {exc}"

    # ------------------------------------------------------------------
    # Tier 0 dispatch — zero-token, zero-AI-RAM, sub-20ms local reflexes.
    # Every handler here is deterministic, local, and offline. No LLM,
    # no external API, no semantic understanding. Returns (reply, source)
    # or (None, None) when nothing matches.
    # ------------------------------------------------------------------
    def _tier0_dispatch(
        self,
        clean: str,
        effective_dry_run: bool,
        approval_cb: ApprovalCallback | None,
        status_cb: StatusCallback | None,
        consent_cb: PrivacyConsentCallback | None,
        task_glow_cb: TaskGlowCallback | None,
    ) -> tuple[str | None, str | None]:
        t0 = time.monotonic()

        # ---- Memory v2: /context slash command (Gear 0 reflex) ----
        if clean.strip().lower() == "/context":
            try:
                from .memory import get_db

                ctx = get_db().build_tiered_context(
                    query="",
                    gear="deep",
                    ram_tier=None,
                    redact_for_cloud=True,
                )
                lines = [
                    f"[CONTEXT] tier={ctx['tier']} budget={ctx['budget_tokens']}tok "
                    f"L0={ctx['l0_count']} L1={ctx['l1_count']} L2={ctx['l2_count']}",
                    "",
                    ctx["text"] or "(no active context)",
                ]
                reply = "\n".join(lines)
            except Exception as exc:  # pragma: no cover - defensive
                reply = f"[CONTEXT] Error building tiered context: {exc}"
            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="reflex",
                gear_reason="Gear 0 /context slash command (build_tiered_context deep+redact)",
                source="dry-run · context" if effective_dry_run else "context",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return reply, "context"

        # ---- Memory v2 / v0.9.1: /seed slash command (Gear 0 reflex) ----
        seed_match = re.match(r"^/seed(?:\s+(.*))?$", clean.strip(), re.IGNORECASE)
        if seed_match:
            args_str = seed_match.group(1) or ""
            # Parse simple flags: --dir <path>, --include-ref, --dry-run
            seed_dir = None
            include_ref = False
            seed_dry_run = effective_dry_run
            tokens = args_str.split()
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                if tok == "--dir" and i + 1 < len(tokens):
                    seed_dir = Path(tokens[i + 1])
                    i += 2
                elif tok == "--include-ref":
                    include_ref = True
                    i += 1
                elif tok == "--dry-run":
                    seed_dry_run = True
                    i += 1
                else:
                    i += 1
            if seed_dir is None:
                seed_dir = config.CONFIG_DIR / "seed"
            if not seed_dry_run:
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80], gear="reflex",
                    gear_reason="Live /seed disabled until broker-owned execution",
                    source="seed", latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return "Denied: live seeding requires broker-owned execution.", "seed"
            try:
                from . import seed_knowledge

                bundle = seed_knowledge.run_seed_bundle(
                    seed_dir=seed_dir,
                    include_reference=include_ref,
                    reference_dir=(seed_dir / "reference") if include_ref else None,
                    dry_run=seed_dry_run,
                )
                lines = [
                    f"[SEED] dir={seed_dir} dry_run={seed_dry_run} include_ref={include_ref}",
                    f"  SOUL preferences: {bundle['soul'].get('preferences', 0)}",
                    f"  USER facts/triples: {bundle['user'].get('facts', 0)}/{bundle['user'].get('triples', 0)}",
                    f"  Corpus: files={bundle['corpus'].get('files_scanned', 0)} "
                    f"facts={bundle['corpus'].get('facts_seeded', 0)} "
                    f"triples={bundle['corpus'].get('triples_seeded', 0)} "
                    f"skipped_sensitive={bundle['corpus'].get('skipped_sensitive', 0)} "
                    f"errors={bundle['corpus'].get('errors', 0)}",
                    f"  Total writes: {bundle.get('total_writes', 0)}",
                ]
                if seed_dry_run:
                    lines.insert(1, "  [DRY-RUN] No knowledge database writes; local files were inspected.")
                reply = "\n".join(lines)
            except Exception as exc:  # pragma: no cover - defensive
                reply = f"[SEED] Error seeding knowledge: {exc}"
            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="reflex",
                gear_reason="Gear 0 /seed slash command (run_seed_bundle)",
                dry_run=seed_dry_run,
                source="dry-run · seed",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return reply, "dry-run · seed"

        # ---- Gear 0-math: Deterministic AST arithmetic (0 LLM tokens) ----
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

        # ---- Gear 0a: Instant zero-latency reflex + approval-gated install ----
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
                gear_reason="Gear 0 install request (live execution disabled without broker)",
                tools_called=["[sim]run_shell_command"] if effective_dry_run else [],
                dry_run=effective_dry_run,
                source="dry-run · install" if effective_dry_run else "install",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return install_reply, ("dry-run · install" if effective_dry_run else "install")

        # ---- Gear 0a-1: 6 Specialized Everyday Desktop Agents (0 LLM tokens) ----
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

        # ---- Gear 0a-2: Instant local intent match (0 tokens) ----
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

        # ---- Gear 0a-2: Intent fast-bypass classifier (greetings & status) ----
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

        # ---- Gear 0b: Direct Learned Skill Trigger Match (0 LLM tokens) ----
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
                        tn, ta, approval_cb=acb, status_cb=status_cb, consent_cb=consent_cb, task_glow_cb=task_glow_cb,
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

        # ---- Gear 0c: Zero-token reflexes (18 modules) ----
        # Modules ordered by expected hit frequency: lists, ledger, routines
        # are common desktop queries and are checked first to minimize
        # sequential classify-chain overhead (Update 25).
        gear0_modules = [
            ("gear0_lists", gear0_lists.gear0_lists_classify, gear0_lists.gear0_lists_execute),
            ("gear0_ledger", gear0_ledger.gear0_ledger_classify, gear0_ledger.gear0_ledger_execute),
            ("gear0_routines", gear0_routines.gear0_routines_classify, gear0_routines.gear0_routines_execute),
            ("gear0_utils", gear0_utils.gear0_classify, gear0_utils.gear0_execute),
            ("gear0_intent", gear0_intent.gear0_intent_classify, gear0_intent.gear0_intent_execute),
            ("gear0_graph", gear0_graph.gear0_graph_classify, gear0_graph.gear0_graph_execute),
            ("gear0_iot", gear0_iot.gear0_iot_classify, gear0_iot.gear0_iot_execute),
            ("gear0_context", gear0_context.gear0_context_classify, gear0_context.gear0_context_execute),
            ("gear0_audit", gear0_audit.gear0_audit_classify, gear0_audit.gear0_audit_execute),
            ("gear0_media", gear0_media.gear0_media_classify, gear0_media.gear0_media_execute),
            ("gear0_focus", gear0_focus.gear0_focus_classify, gear0_focus.gear0_focus_execute),
            ("gear0_clipboard", gear0_clipboard.gear0_clipboard_classify, gear0_clipboard.gear0_clipboard_execute),
            ("gear0_calendar", gear0_calendar.gear0_calendar_classify, gear0_calendar.gear0_calendar_execute),
            ("gear0_network", gear0_network.gear0_network_classify, gear0_network.gear0_network_execute),
            ("gear0_hardware", gear0_hardware.gear0_hardware_classify, gear0_hardware.gear0_hardware_execute),
            ("gear0_macro", gear0_macro.gear0_macro_classify, gear0_macro.gear0_macro_execute),
            ("gear0_status", gear0_status.gear0_status_classify, gear0_status.gear0_status_execute),
            ("gear0_dual", gear0_dual.gear0_dual_classify, gear0_dual.gear0_dual_execute),
            ("gear0_contacts", gear0_contacts.gear0_contacts_classify, gear0_contacts.gear0_contacts_execute),
        ]

        # Check classify cache first — repeated queries skip the chain entirely
        cached = _gear0_classify_cached(clean)
        if cached is not None:
            mod_name, handler = cached
            # Find the execute_fn for the cached module
            execute_fn = None
            for _m, _cf, _ef in gear0_modules:
                if _m == mod_name:
                    execute_fn = _ef
                    break
            if execute_fn is not None:
                result = execute_fn(
                    handler, clean, dry_run=effective_dry_run,
                    approval_cb=approval_cb, status_cb=status_cb, consent_cb=consent_cb,
                )
                if result is not None:
                    safe_result = gear0_utils.gear0_redact_pii(result)
                    self.last_trace = TurnTrace(
                        prompt_preview=clean[:80],
                        gear="reflex",
                        gear_reason=f"Gear 0-{mod_name} ({handler}) [cached]",
                        source=f"{mod_name}_{handler}",
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                    return safe_result, f"{mod_name}_{handler}"

        for mod_name, classify_fn, execute_fn in gear0_modules:
            handler = classify_fn(clean)
            if handler:
                # Cache the classification result for future queries
                _gear0_classify_store(clean, mod_name, handler)
                result = execute_fn(
                    handler, clean, dry_run=effective_dry_run,
                    approval_cb=approval_cb, status_cb=status_cb, consent_cb=consent_cb,
                )
                if result is not None:
                    safe_result = gear0_utils.gear0_redact_pii(result)
                    self.last_trace = TurnTrace(
                        prompt_preview=clean[:80],
                        gear="reflex",
                        gear_reason=f"Gear 0-{mod_name} ({handler})",
                        source=f"{mod_name}_{handler}",
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                    return safe_result, f"{mod_name}_{handler}"

        # ---- Gear 0 Plugins (5 plugins) ----
        gear0_plugins = [
            ("plugin1_sniffer", sniffer_plugin.gear0_sniffer_classify, sniffer_plugin.gear0_sniffer_execute),
            ("plugin2_habit", habit_engine.gear0_habit_classify, habit_engine.gear0_habit_execute),
            ("plugin3_data_oracle", data_oracle.gear0_data_oracle_classify, data_oracle.gear0_data_oracle_execute),
            ("plugin4_os_shim", os_shim.gear0_os_classify, os_shim.gear0_os_execute),
            ("plugin5_self_heal", self_heal.gear0_heal_classify, self_heal.gear0_heal_execute),
        ]

        for mod_name, classify_fn, execute_fn in gear0_plugins:
            handler = classify_fn(clean)
            if handler:
                result = execute_fn(
                    handler, clean, dry_run=effective_dry_run,
                    approval_cb=approval_cb, status_cb=status_cb, consent_cb=consent_cb,
                )
                if result is not None:
                    if mod_name == "plugin4_os_shim":
                        safe_result = gear0_utils.gear0_redact_pii(str(result))
                    else:
                        safe_result = gear0_utils.gear0_redact_pii(result)
                    self.last_trace = TurnTrace(
                        prompt_preview=clean[:80],
                        gear="reflex",
                        gear_reason=f"Gear 0 Plugin: {mod_name} ({handler})",
                        source=mod_name,
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                    return safe_result, mod_name

        return None, None

    # ------------------------------------------------------------------
    # Tier 0.5 — Local smolLM quantized (Update 26)
    # Between Gear 0 (0 tokens, 0 MB) and Gear 1 (Ollama qwen3:1.7b).
    # Uses smolLM 4-bit GGUF via llama.cpp — ~100-200MB RAM, sub-100ms.
    # ------------------------------------------------------------------
    def _tier0_5_micro_classify(
        self,
        clean: str,
        effective_dry_run: bool,
        approval_cb: ApprovalCallback | None,
        status_cb: StatusCallback | None,
        consent_cb: PrivacyConsentCallback | None,
        task_glow_cb: TaskGlowCallback | None,
    ) -> tuple[str | None, str | None, str | None]:
        """Gear 0.5 micro-tier: intent + slot classification → Gear 0 execution.

        This is the *designed* role of Tier 0.5: the micro-LLM only classifies
        intent + slots; Gear 0 handlers execute the actual OS action. Never
        answers with free text — that is Tier 1's job.

        Returns (reply, error, source) or (None, None, None) if no micro result.
        """
        t0 = time.monotonic()

        try:
            backend = config.GEAR0_5_BACKEND
            if backend not in {"fasttext", "smollm", "none", "auto"}:
                backend = "auto"

            micro_result = None
            if backend != "none":
                chosen = backend
                if chosen == "auto":
                    chosen = "fasttext"

                micro_router = gear0_5_micro.get_gear05_router(backend=chosen)
                if micro_router is not None and micro_router._initialized:
                    micro_result = micro_router.classify(
                        clean, timeout_sec=10.0,
                    )

            if micro_result is not None and micro_result.intent != "unknown":
                if micro_result.confidence >= config.GEAR0_5_MIN_CONFIDENCE:
                    executed = self._execute_gear05_intent(
                        micro_result, clean, dry_run=effective_dry_run,
                    )
                    if executed is not None:
                        self.last_trace = TurnTrace(
                            prompt_preview=clean[:80],
                            gear="reflex",
                            gear_reason=f"Tier 0.5 micro ({micro_result.intent}, conf={micro_result.confidence:.2f}, src={micro_result.confidence_source})",
                            source="tier0_5_micro",
                            latency_ms=int((time.monotonic() - t0) * 1000),
                        )
                        return executed, None, "tier0_5_micro"

        except Exception:
            # Micro-tier failure is non-fatal; fall through to caller's next tier
            pass

        return None, None, None

    def _tier0_5_smollm_generate(
        self,
        clean: str,
        effective_dry_run: bool,
    ) -> tuple[str | None, str | None, str | None]:
        """Gear 0.5 smolLM free-text generation.

        Only invoked when Tier 1 is unavailable (auto mode) or when the user
        explicitly selects Tier 0.5. Never preempts Tier 1 in auto mode.
        """
        t0 = time.monotonic()

        try:
            if not gear0_5_smolLM.gear0_5_smolLM_available():
                return None, "smolLM not available (llama.cpp binary or GGUF model missing)", None

            # Build a concise system prompt for smolLM
            gcfg = _gear_settings("gear0_5")
            system_prompt = gcfg.get("instruction", "")
            if system_prompt:
                full_prompt = f"{system_prompt}\n\nUser: {clean}\nAssistant:"
            else:
                full_prompt = f"User: {clean}\nAssistant:"

            if effective_dry_run:
                reply = f"🧪 Dry-Run [Gear 0.5 smolLM]: Would answer '{clean[:60]}' using local smolLM ({config.SMOLLM_MODEL})."
                self.last_trace = TurnTrace(
                    prompt_preview=clean[:80],
                    gear="reflex",
                    gear_reason=f"Tier 0.5 smolLM dry-run ({config.SMOLLM_MODEL})",
                    source="tier0_5_smolLM_dryrun",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )
                return reply, None, "tier0_5_smolLM_dryrun"

            reply, err = gear0_5_smolLM.gear0_5_smolLM_generate(
                full_prompt,
                max_tokens=int(gcfg.get("max_tokens", config.SMOLLM_MAX_TOKENS)),
                temperature=float(gcfg.get("temperature", config.SMOLLM_TEMPERATURE)),
                timeout_sec=10.0,
            )
            if err:
                return None, err, None

            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="reflex",
                gear_reason=f"Tier 0.5 smolLM quantized ({config.SMOLLM_MODEL})",
                source="tier0_5_smolLM",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return reply, None, "tier0_5_smolLM"
        except Exception as exc:
            return None, f"Tier 0.5 smolLM error: {exc}", None

    def _tier1_available(self) -> bool:
        """Lightweight check: is the Tier 1 Ollama endpoint reachable?"""
        try:
            import urllib.request
            url = f"{config.OLLAMA_API_BASE.rstrip('/')}/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:  # nosec B310 — Ollama localhost API, scheme fixed to http
                return resp.status == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Tier 1 — Local Ollama fast (qwen3:1.7b, think=False, sub-200ms target)
    # ------------------------------------------------------------------
    def _tier1_local_fast(
        self,
        clean: str,
        effective_dry_run: bool,
        approval_cb: ApprovalCallback | None,
        status_cb: StatusCallback | None,
        consent_cb: PrivacyConsentCallback | None,
        task_glow_cb: TaskGlowCallback | None,
        stream_cb: StreamCallback | None,
    ) -> tuple[str | None, str | None, str | None]:
        """Returns (reply, error, source) or (None, None, None) on fallback."""
        t0 = time.monotonic()
        try:
            messages = [{"role": "user", "content": clean}]
            reply, err = self._run_local_agent(
                messages=messages,
                gear="fast",
                approval_cb=approval_cb,
                status_cb=status_cb,
                consent_cb=consent_cb,
                task_glow_cb=task_glow_cb,
                stream_cb=stream_cb,
                dry_run=effective_dry_run,
            )
            if err:
                return None, err, None
            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="fast",
                gear_reason=f"Tier 1 local Ollama fast ({config.LOCAL_MODEL})",
                source="tier1_local_fast",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return reply, None, "tier1_local_fast"
        except Exception as exc:
            return None, f"Tier 1 local fast error: {exc}", None

    # ------------------------------------------------------------------
    # Tier 2 — Local Ollama heavy (reasoning model, deeper thinking)
    # ------------------------------------------------------------------
    def _tier2_local_heavy(
        self,
        clean: str,
        effective_dry_run: bool,
        approval_cb: ApprovalCallback | None,
        status_cb: StatusCallback | None,
        consent_cb: PrivacyConsentCallback | None,
        task_glow_cb: TaskGlowCallback | None,
        stream_cb: StreamCallback | None,
    ) -> tuple[str | None, str | None, str | None]:
        """Returns (reply, error, source) or (None, None, None) on fallback."""
        t0 = time.monotonic()
        try:
            messages = [{"role": "user", "content": clean}]
            reply, err = self._run_local_agent(
                messages=messages,
                gear="deep",
                approval_cb=approval_cb,
                status_cb=status_cb,
                consent_cb=consent_cb,
                task_glow_cb=task_glow_cb,
                stream_cb=stream_cb,
                dry_run=effective_dry_run,
            )
            if err:
                return None, err, None
            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="deep",
                gear_reason=f"Tier 2 local Ollama heavy ({config.LOCAL_MODEL})",
                source="tier2_local_heavy",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return reply, None, "tier2_local_heavy"
        except Exception as exc:
            return None, f"Tier 2 local heavy error: {exc}", None

    # ------------------------------------------------------------------
    # Tier 3 — Cloud via provider_router (bounded failover, max 3 providers)
    # ------------------------------------------------------------------
    def _tier3_cloud(
        self,
        clean: str,
        effective_dry_run: bool,
        approval_cb: ApprovalCallback | None,
        status_cb: StatusCallback | None,
        consent_cb: PrivacyConsentCallback | None,
        task_glow_cb: TaskGlowCallback | None,
        stream_cb: StreamCallback | None,
    ) -> tuple[str | None, str | None, str | None]:
        """Returns (reply, error, source) or (None, None, None) on fallback."""
        if not _has_cloud_key():
            return None, "No cloud provider API key configured.", None

        t0 = time.monotonic()
        try:
            vault = privacy.RedactionVault()
            redacted = vault.redact(clean)
            messages = [{"role": "user", "content": redacted}]
            reply, err, switched = self._run_cloud_agent(
                messages=messages,
                gear="tactical",
                vault=vault,
                approval_cb=approval_cb,
                status_cb=status_cb,
                consent_cb=consent_cb,
                task_glow_cb=task_glow_cb,
                stream_cb=stream_cb,
                dry_run=effective_dry_run,
            )
            if err:
                return None, err, None
            if reply is None:
                return None, "Cloud model returned an empty response.", None
            reply = vault.restore(reply)
            self.last_trace = TurnTrace(
                prompt_preview=clean[:80],
                gear="tactical",
                gear_reason="Tier 3 cloud via provider_router (bounded failover)",
                source="tier3_cloud",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            return reply, None, "tier3_cloud"
        except Exception as exc:
            return None, f"Tier 3 cloud error: {exc}", None

    def ask(
        self,
        text: str,
        mode: str = "auto",
        language: str | None = None,
        status_cb: StatusCallback | None = None,
        approval_cb: ApprovalCallback | None = None,
        consent_cb: PrivacyConsentCallback | None = None,
        task_glow_cb: TaskGlowCallback | None = None,
        stream_cb: StreamCallback | None = None,
        dry_run: bool = False,
        tier: int | None = None,
        channel: str = "text",
        callbacks: _bh_mod.BrainCallbacks | None = None,
    ) -> tuple[str, str]:
        t0 = time.monotonic()
        clean = text.strip()
        if not clean:
            return "Please enter a prompt.", "system"

        # Reset taint flag, step log, and tool results for this turn (T1 defense)
        self._turn_tainted = False
        self._turn_steps: list[dict[str, Any]] = []
        self._turn_tool_results: list[tuple[str, str]] = []

        # Phase 2: Explicit Local-First Compute Split (0=Reflex Only, 1=Local Fast, 2=Local Heavy, 3=Opt-In Cloud)
        effective_tier = tier if tier is not None else self._session_tier
        if effective_tier in {1, 2}:
            mode = "local"
        elif effective_tier == 3:
            mode = "cloud"

        # Movie Feature D: Dry-Run Simulation Mode ("simulate: ...", "dry run: ...", or --dry-run)
        effective_dry_run = bool(dry_run)
        dry_m = re.match(r"^(?:simulate|dry[\s-]?run)\s*[:\-]\s*(.+)$", clean, re.IGNORECASE)
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
                "• /tier [0|0.5|1|2|3|auto]  — show or lock compute tier (0=reflex, 0.5=smolLM, 1=local fast, 2=local heavy, 3=opt-in cloud)\n"
                "• /bg <goal> · /jobs    — dispatch non-blocking background Swarm worker & inspect job results\n"
                "• /struggle             — plain-English explanation of why the last browser/GUI/AI step struggled\n"
                "• /why                  — Glass-Box receipt for the last decision\n"
                "• /name <New> [Nick]    — rename assistant & nickname\n"
                "• /facts · /goals · /kg — view saved memory, goals & knowledge graph\n"
                "• /skills · /mcp · /cache · /clear\n"
                "• /tokens             — show compression token & context savings ledger\n"
                "• /expand <ccr_id>    — retrieve full original tool output from CCR cache\n"
                "• /cli <tool> <subcmd> [args] — typed CLI executor (git, docker, systemctl, gh)\n"
                "• Prefixes: 'quick: ...', 'deep: ...', 'simulate: ...', 'remember key = val'"
            ), "system"

        if low == "/persona" or low.startswith("/persona "):
            parts = clean.split(None, 1)
            if len(parts) == 1:
                cur_style = getattr(self, "_persona_style", "celina")
                cur_channel = getattr(self, "_persona_channel", "text")
                return (
                    f"Current persona: style={cur_style}, channel={cur_channel}\n"
                    "Styles: celina (warm, helpful), jarvis (formal, precise), minimal (concise)\n"
                    "Channels: text (default), voice (1-3 sentences, no markdown/emojis/bullets)\n"
                    "Usage: /persona <style> [text|voice]",
                    "system",
                )
            style_arg = parts[1].strip().lower()
            channel_arg = "text"
            if " " in parts[1]:
                style_arg, channel_arg = parts[1].strip().split(None, 1)
                style_arg = style_arg.lower()
                channel_arg = channel_arg.strip().lower()
            if style_arg not in {"celina", "jarvis", "minimal"}:
                return f"Unknown persona style '{style_arg}'. Choose: celina, jarvis, minimal.", "system"
            if channel_arg not in {"text", "voice"}:
                return f"Unknown channel '{channel_arg}'. Choose: text, voice.", "system"
            self._persona_style = style_arg
            self._persona_channel = channel_arg
            return f"Persona set to style={style_arg}, channel={channel_arg}.", "system"


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
            context_compressor.clear_compression_state()
            _sensory_mod.get_sensory_buffer().clear()
            return "Short-term conversation history cleared. Compression CCR cache wiped. Sensory buffer cleared.", "memory"
        if low == "/tokens":
            if low.startswith("/tokens reset"):
                context_compressor.get_token_tracker().reset()
                return "Compression token ledger reset.", "system"
            return context_compressor.get_token_tracker().format_report(), "system"
        if low == "/expand" or low.startswith("/expand "):
            parts = low.split(None, 1)
            ccr_id = parts[1].strip() if len(parts) > 1 else ""
            if not ccr_id:
                return "Usage: /expand <ccr_id> [optional filter query]\nExample: /expand abc123def4", "system"
            query = ""
            if len(parts) > 1 and " " in parts[1]:
                query = parts[1].split(None, 1)[1] if len(parts[1].split(None, 1)) > 1 else ""
            return context_compressor.retrieve_compressed_context(ccr_id, query=query), "system"
        if low == "/cli" or low.startswith("/cli "):
            from . import cli_bridge as _cli_bridge
            cli_parts = clean.split(None, 2)
            if len(cli_parts) < 3:
                return (
                    "Usage: /cli <tool> <subcmd> [args...]\n"
                    "Tools: git (status/diff/log/branch/show/rev-parse/remote | add/commit/checkout/switch/stash/pull/fetch),\n"
                    "       docker (ps/images/logs/inspect/stats/version | start/stop/restart),\n"
                    "       systemctl (status/is-active/is-enabled/list-units/list-timers | start/stop/restart/reload),\n"
                    "       gh (status/pr/issue/repo/run)\n"
                    "Mutating subcommands require interactive Tier-2 approval.",
                    "system",
                )
            tool_name = cli_parts[1]
            subcmd = cli_parts[2]
            extra = None
            if len(cli_parts) > 3:
                extra = cli_parts[3].split()
            return _cli_bridge.run_typed_cli(tool_name, subcmd, extra), "system"
        if low == "/facts":
            summary = self.db.format_facts_for_prompt()
            return summary or "No long-term facts saved yet.", "memory"
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
            if t_arg in {"0", "0.5", "1", "2", "3"}:
                if t_arg == "0.5":
                    self._session_tier = 0.5
                else:
                    self._session_tier = int(t_arg)
                descs = {
                    "0": "Tier 0 (Reflex Only): 0 LLM tokens, 0 MB AI RAM — instant OS/media/file commands.",
                    "0.5": f"Tier 0.5 (smolLM Quantized): Local smolLM via llama.cpp ({config.SMOLLM_MODEL}, ~100-200MB RAM).",
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
                f"  • Tier 0 (--tier 0): Gear 0 reflexes & Everyday Agents — 0 LLM tokens, 0 MB RAM\n"
                f"  • Tier 0.5 (--tier 0.5): Gear 0.5 smolLM quantized ({config.SMOLLM_MODEL}, ~100-200MB RAM)\n"
                f"  • Tier 1 (--tier 1): Local Fast Ollama (`{config.LOCAL_MODEL}`, think=False) — 100% offline\n"
                f"  • Tier 2 (--tier 2): Local Heavy Ollama (`{heavy_tag}`) — warns on 8 GB RAM\n"
                f"  • Tier 3 (--tier 3): Explicit Opt-In Cloud (OpenRouter, privacy-gated)"
            ), "system"

        # ---- Connectors (Update 23) ----
        # App connectors (Linear, GitHub, Gmail, Slack, Notion) — outside Gear 0, opt-in only.
        if low == "/connectors" or low.startswith("/connectors "):
            reg = _connectors_get_registry()
            parts = clean.split(None, 1)
            subcmd = parts[1].strip() if len(parts) > 1 else ""
            if subcmd == "":
                # List all connectors with status
                summary = reg.status_summary()
                lines = [
                    f"App Connectors ({summary['configured']}/{summary['total']} configured):",
                ]
                for row in summary["connectors"]:
                    mark = "✓" if row["configured"] else "○"
                    lines.append(
                        f"  {mark} {row['display_name']} ({row['name']}): {row['status']}",
                    )
                lines.append("")
                lines.append("Usage: /connectors <name> configure | deconfigure | status")
                lines.append("Example: /connectors linear configure")
                return "\\n".join(lines), "connectors"

            # Parse: /connectors <name> <subcommand>
            sub_parts = subcmd.split(None, 2)
            if len(sub_parts) < 2:
                return "Usage: /connectors <name> configure | deconfigure | status", "connectors"

            conn_name = sub_parts[0].strip()
            conn_subcmd = sub_parts[1].strip()

            conn = reg.get(conn_name)
            if conn is None:
                available = ", ".join(reg.list_connectors())
                return f"Unknown connector '{conn_name}'. Available: {available}", "connectors"

            if conn_subcmd == "status":
                row = conn.status_row()
                lines = [
                    f"Connector: {row['display_name']} ({row['name']})",
                    f"  Status: {row['status']}",
                    f"  Configured: {'yes' if row['configured'] else 'no'}",
                    f"  Actions: {', '.join(row['actions'])}",
                ]
                return "\\n".join(lines), "connectors"

            if conn_subcmd == "configure":
                return (
                    f"To configure '{conn_name}', set its API key in ~/.celina/storage/connectors/{conn_name}.json "
                    f"or use the connector's configure method programmatically. "
                    f"Read-only actions never require credentials; write actions need Allow/Deny approval.",
                    "connectors",
                )

            if conn_subcmd == "deconfigure":
                ok = conn.deconfigure()
                if ok:
                    return f"Connector '{conn_name}' deconfigured. Credentials removed.", "connectors"
                return f"Failed to deconfigure '{conn_name}': {conn.last_error}", "connectors"

            return f"Unknown subcommand '{conn_subcmd}'. Use: configure | deconfigure | status", "connectors"

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
        heal_m = re.match(r"^(?:heal|fix|repair)\s+skill\s+(.+)$", clean, re.IGNORECASE)
        if heal_m:
            return skills.heal_skill_from_last_trace(heal_m.group(1).strip(), db=self.db), "skills"

        # Shortcut: "save last actions as <name>"
        save_m = re.match(r"^save\s+last\s+actions?\s+as\s+(.+)$", clean, re.IGNORECASE)
        if save_m:
            return skills.save_last_actions_as_skill(save_m.group(1).strip()), "skills"

        if low.startswith("remember ") and "=" in clean[9:]:
            body = clean[9:].strip()
            k, v = body.split("=", 1)
            if effective_dry_run:
                return f"🧪 Dry-Run [Memory]: Would save {k.strip()} = {v.strip()} to long-term memory.", "dry-run · memory"
            self.db.set_fact(k, v)
            return f"Saved to long-term memory: {k.strip()} = {v.strip()}", "memory"

        # Deictic query detection: inject sensory buffer context for "what was that...?" queries
        deictic_patterns = (
            "what was that", "what did i just", "what did i see", "what did i look at",
            "summarize what", "that error", "that message", "that notification",
            "earlier", "just now", "a moment ago", "2 minutes ago", "few minutes ago",
        )
        is_deictic = any(p in low for p in deictic_patterns)
        sensory_ctx = ""
        if is_deictic:
            sensory_ctx = _sensory_mod.get_sensory_buffer().recent_context(limit=4)
            if sensory_ctx:
                user_prompt_clean = clean
                clean = sensory_ctx + "\n\nUser query (untrusted sensory data — verify before acting): " + user_prompt_clean
                low = clean.lower()

        # ---- Tier 0: zero-token, zero-AI-RAM, sub-20ms local reflexes ----
        tier0_reply, tier0_source = self._tier0_dispatch(
            clean,
            effective_dry_run,
            approval_cb,
            status_cb,
            consent_cb,
            task_glow_cb,
        )
        if tier0_reply is not None:
            return self._finalize_reply(tier0_reply, tier0_source, self.last_trace, callbacks)

        # ------------------------------------------------------------------
        # Tier-based LLM fallback (only reached when Tier 0 has no match)
        # Tier 0 = Gear 0 reflex only (0 tokens, 0 MB AI RAM)
        # Tier 0.5 = Gear 0.5 smolLM quantized (~100-200MB RAM, sub-100ms)
        # Tier 1 = Local Ollama fast (qwen3:1.7b, think=False)
        # Tier 2 = Local Ollama heavy (reasoning model)
        # Tier 3 = Cloud via provider_router (bounded failover, max 3 providers)
        # ------------------------------------------------------------------
        if effective_tier == 0:
            return (
                "Gear 0 reflex only — no larger local or cloud model available for this query. "
                "Try '/tier 0.5' for smolLM, '/tier 1' for local Ollama, or '/tier 3' for cloud.",
                "tier0_reflex_only",
            )

        # Determine which tiers to attempt based on effective_tier and mode
        if effective_tier in {None, 0.5}:
            # Step 1: Tier 0.5 micro-tier (intent classification → Gear 0 execution)
            # Always runs — this is the designed role (classifier, not answerer)
            micro_reply, micro_err, micro_src = self._tier0_5_micro_classify(
                clean, effective_dry_run, approval_cb, status_cb,
                consent_cb, task_glow_cb,
            )
            if micro_reply is not None:
                return self._finalize_reply(micro_reply, micro_src or "tier0_5_micro", self.last_trace, callbacks)

            # Step 2: Tier 0.5 smolLM free-text — only when Tier 1 is unavailable
            # In auto mode, never let smolLM preempt Tier 1.
            try_smollm = (
                effective_tier == 0.5
                or (effective_tier is None and not self._tier1_available())
            )
            if try_smollm:
                reply, err, src = self._tier0_5_smollm_generate(
                    clean, effective_dry_run,
                )
                if reply is not None:
                    return self._finalize_reply(reply, src or "tier0_5_smolLM", self.last_trace, callbacks)
                if err and effective_tier == 0.5:
                    return f"Tier 0.5 smolLM unavailable: {err}", "tier0_5_error"

        if effective_tier in {None, 1}:
            # Try Tier 1 (local Ollama fast)
            reply, err, src = self._tier1_local_fast(
                clean, effective_dry_run, approval_cb, status_cb,
                consent_cb, task_glow_cb, stream_cb,
            )
            if reply is not None:
                return self._finalize_reply(reply, src or "tier1_local_fast", self.last_trace, callbacks)
            if err and effective_tier == 1:
                return f"Tier 1 local fast unavailable: {err}", "tier1_error"

        if effective_tier in {None, 2}:
            # Try Tier 2 (local Ollama heavy)
            reply, err, src = self._tier2_local_heavy(
                clean, effective_dry_run, approval_cb, status_cb,
                consent_cb, task_glow_cb, stream_cb,
            )
            if reply is not None:
                return self._finalize_reply(reply, src or "tier2_local_heavy", self.last_trace, callbacks)
            if err and effective_tier == 2:
                return f"Tier 2 local heavy unavailable: {err}", "tier2_error"

        if effective_tier in {None, 3}:
            # Try Tier 3 (cloud via provider_router)
            reply, err, src = self._tier3_cloud(
                clean, effective_dry_run, approval_cb, status_cb,
                consent_cb, task_glow_cb, stream_cb,
            )
            if reply is not None:
                return self._finalize_reply(reply, src or "tier3_cloud", self.last_trace, callbacks)
            if err and effective_tier == 3:
                return f"Tier 3 cloud unavailable: {err}", "tier3_error"

        # All tiers exhausted
        return (
            "I couldn't process that request. No Gear 0 reflex matched, and no local "
            "or cloud model is available. Try '/tier 1' for local Ollama or '/tier 3' "
            "for cloud (set a provider API key in ~/.ai-env).",
            "no_match",
        )


def route_query_with_meta(
    text: str,
    mode: str = "auto",
    language: str | None = None,
    status_cb: StatusCallback | None = None,
    approval_cb: ApprovalCallback | None = None,
    consent_cb: PrivacyConsentCallback | None = None,
) -> tuple[str, str]:
    return CelinaRouter().ask(
        text,
        mode=mode,
        language=language,
        status_cb=status_cb,
        approval_cb=approval_cb,
        consent_cb=consent_cb,
    )


def process_query(text: str, mode: str = "auto", language: str | None = None) -> str:
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
    config.set_process_name("celina-daemon")

    # Fast-path operational subcommands: celina undo | migrate | export | import | checkpoints | evidence | capabilities | autonomy | config
    if len(sys.argv) >= 2 and sys.argv[1] in {
        "undo", "migrate", "export", "import", "checkpoints", "evidence", "capabilities", "autonomy", "config",
    }:
        from . import state_ops

        raise SystemExit(state_ops.run_cli(sys.argv[1:]))

    asst_label = config.get_assistant_display_label()
    parser = argparse.ArgumentParser(description=f"{asst_label} AI CLI v4.4 (EN / TE / HI)")
    parser.add_argument("prompt", nargs="*", help=f"One-shot prompt to ask {config.get_assistant_name()}")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--auto", action="store_true", help="Cloud + privacy firewall + offline local fallback")
    mode_group.add_argument("--local", action="store_true", help="Force 100%% offline local Ollama")
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
    tier_flag: int | None = args.tier
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
            lang_hint=lang, on_status=lambda s: print(f"[{s}]", flush=True),
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
        "(commands: 'exit', '/help', '/context', '/seed [--dir <path>] [--include-ref] [--dry-run]', '/tier [0-3|auto]', '/turbo', '/bg <goal>', '/jobs', '/train', '/model [tag]', '/ram', '/why', '/struggle', '/offload', '/lang auto|en|te|hi')",
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
