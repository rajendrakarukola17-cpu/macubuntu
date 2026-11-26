"""Task-aware OpenAI-compatible cloud provider selection for Celina.

Keeps provider routing deterministic and dependency-light. Secrets are read from
config at call time; status output never returns their values.

Update 18 additions:
- Automatic model discovery: query each provider's /models endpoint using just
  the API key; Celina picks the best free model per task category automatically.
- Automatic ensemble decision: Celina analyses task complexity and decides
  whether to run parallel multi-provider ensemble or single-provider failover.
"""
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import os
import threading
import time
from typing import Any

try:
    import requests
except Exception:  # pragma: no cover - requests is a hard dependency in practice
    requests = None  # type: ignore


@dataclass(frozen=True)
class Provider:
    name: str
    endpoint: str
    key_attr: str
    model_env: str
    default_model: str


PROVIDERS: tuple[Provider, ...] = (
    Provider("groq", "https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "CELINA_GROQ_MODEL", "llama-3.3-70b-versatile"),
    Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "GEMINI_API_KEY", "CELINA_GEMINI_MODEL", "gemini-2.5-flash"),
    Provider("nvidia", "https://integrate.api.nvidia.com/v1/chat/completions", "NVIDIA_API_KEY", "CELINA_NVIDIA_MODEL", "meta/llama-3.3-70b-instruct"),
    Provider("mistral", "https://api.mistral.ai/v1/chat/completions", "MISTRAL_API_KEY", "CELINA_MISTRAL_MODEL", "mistral-small-latest"),
    Provider("cerebras", "https://api.cerebras.ai/v1/chat/completions", "CEREBRAS_API_KEY", "CELINA_CEREBRAS_MODEL", "llama-3.3-70b"),
    Provider("openrouter", "https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY", "CELINA_OPENROUTER_MODEL", "openrouter/free"),
    Provider("xai", "https://api.x.ai/v1/chat/completions", "XAI_API_KEY", "CELINA_XAI_MODEL", "grok-3-mini"),
    Provider("glm", "https://open.bigmodel.cn/api/paas/v4/chat/completions", "ZHIPUAI_API_KEY", "CELINA_GLM_MODEL", "glm-4"),
    Provider("deepseek", "https://api.deepseek.com/v1/chat/completions", "DEEPSEEK_API_KEY", "CELINA_DEEPSEEK_MODEL", "deepseek-chat"),
    Provider("together", "https://api.together.xyz/v1/chat/completions", "TOGETHER_API_KEY", "CELINA_TOGETHER_MODEL", "meta-llama/Llama-3.1-8b-Instruct-Turbo"),
    Provider("perplexity", "https://api.perplexity.ai/chat/completions", "PERPLEXITY_API_KEY", "CELINA_PERPLEXITY_MODEL", "sonar-small"),
)

# ---------------------------------------------------------------------------
# Automatic model discovery (Update 18)
# ---------------------------------------------------------------------------

# Provider-specific /models discovery endpoints (queried with the API key only).
MODEL_ENDPOINTS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "nvidia": "https://integrate.api.nvidia.com/v1/models",
    "mistral": "https://api.mistral.ai/v1/models",
    "cerebras": "https://api.cerebras.ai/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "xai": "https://api.x.ai/v1/models",
    "glm": "https://open.bigmodel.cn/api/paas/v4/models",
    "deepseek": "https://api.deepseek.com/v1/models",
    "together": "https://api.together.xyz/v1/models",
    "perplexity": "https://api.perplexity.ai/models",
}

# Discovery cache (provider name -> list of model IDs), TTL 1 hour.
_DISCOVERED_MODELS: dict[str, list[str]] = {}
_DISCOVERY_TIMESTAMP: dict[str, float] = {}
DISCOVERY_CACHE_TTL = 3600

# Task-category -> preferred model-ID keywords (used to pick the best free model).
TASK_MODEL_PREFERENCES: dict[str, list[str]] = {
    "code": ["coder", "code", "deepseek", "codellama", "wizardcoder", "phind"],
    "math": ["grok", "reasoning", "r1", "o1", "deepseek", "nemotron"],
    "creative": ["gemini", "claude", "creative", "instruct"],
    "search": ["sonar", "search", "browsing"],
    "multilingual": ["glm", "gemini", "multilingual"],
    "vision": ["vision", "vl", "glm-4v", "gemini"],
    "long_context": ["gemini", "long", "1m", "128k"],
    "general": ["llama-3.3", "llama-3.1", "mixtral", "grok", "nemotron"],
}

# ---------------------------------------------------------------------------
# Task decomposition + ensemble routing (existing Update 18 code)
# ---------------------------------------------------------------------------

# Preferred order can be overridden for deployments/tests using comma-separated
# provider names. Unknown and unconfigured entries are ignored.
_LOCK = threading.Lock()
_COOLDOWN_UNTIL: dict[str, float] = {}
_LAST_FAILURE: dict[str, str] = {}


def _key(provider: Provider) -> str:
    from . import config
    return str(getattr(config, provider.key_attr, "") or "").strip()


def configured(provider: Provider) -> bool:
    key = _key(provider)
    return bool(key and key != "PASTE_YOUR_KEY_HERE")


def model_for(provider: Provider) -> str:
    return os.environ.get(provider.model_env, "").strip() or provider.default_model


def _rank(prompt: str, gear: str) -> list[str]:
    override = os.environ.get("CELINA_PROVIDER_ORDER", "").strip()
    if override:
        raw = [p.strip().lower() for p in override.split(",") if p.strip()]
    else:
        low = prompt.lower()
        if any(x in low for x in ("pdf", "document", "image", "screenshot", "vision", "long file", "multi-file")):
            raw = ["gemini", "glm", "nvidia", "groq", "mistral", "cerebras", "openrouter", "xai", "deepseek", "together", "perplexity"]
        elif gear == "deep" or any(x in low for x in ("architecture", "reason", "analyze", "debug", "security audit", "compare")):
            raw = ["xai", "nvidia", "gemini", "groq", "mistral", "cerebras", "deepseek", "openrouter", "glm", "together", "perplexity"]
        elif any(x in low for x in ("code", "python", "script", "json", "function")):
            raw = ["deepseek", "mistral", "nvidia", "gemini", "groq", "cerebras", "together", "openrouter", "xai", "glm", "perplexity"]
        elif any(x in low for x in ("search", "current", "latest", "news", "weather")):
            raw = ["perplexity", "openrouter", "gemini", "groq", "cerebras", "mistral", "nvidia", "xai", "deepseek", "together", "glm"]
        elif any(x in low for x in ("translate", "chinese", "hindi", "spanish", "french", "german", "japanese", "korean", "arabic", "telugu")):
            raw = ["glm", "gemini", "groq", "nvidia", "mistral", "cerebras", "openrouter", "xai", "deepseek", "together", "perplexity"]
        else:
            raw = ["groq", "cerebras", "gemini", "mistral", "nvidia", "openrouter", "xai", "glm", "deepseek", "together", "perplexity"]
    # With no explicit order, only configured cloud credentials are attempted.
    seen: set[str] = set()
    ordered = [name for name in raw if name in {p.name for p in PROVIDERS} and not (name in seen or seen.add(name))]
    return ordered


def candidates(prompt: str, gear: str) -> list[tuple[Provider, str]]:
    now = time.monotonic()
    rank = _rank(prompt, gear)
    by_name = {p.name: p for p in PROVIDERS}
    result: list[tuple[Provider, str]] = []
    with _LOCK:
        cooldowns = dict(_COOLDOWN_UNTIL)
    for name in rank:
        p = by_name[name]
        if configured(p) and cooldowns.get(name, 0.0) <= now:
            result.append((p, _key(p)))
    # If every configured provider is cooling down, allow the soonest one again
    # rather than forcing an immediate local downgrade or permanent lockout.
    if not result:
        for name in rank:
            p = by_name[name]
            if configured(p):
                result.append((p, _key(p)))
    return result


def mark_failure(name: str, reason: str, retry_after: float = 0.0) -> None:
    now = time.monotonic()
    # A modest bounded cooldown prevents hot-looping a rate-limited service.
    cooldown = min(max(retry_after, 15.0), 300.0)
    with _LOCK:
        _COOLDOWN_UNTIL[name] = now + cooldown
        _LAST_FAILURE[name] = reason[:120]


def mark_success(name: str) -> None:
    with _LOCK:
        _COOLDOWN_UNTIL.pop(name, None)
        _LAST_FAILURE.pop(name, None)


def status() -> list[dict[str, Any]]:
    now = time.monotonic()
    with _LOCK:
        cooldowns, failures = dict(_COOLDOWN_UNTIL), dict(_LAST_FAILURE)
    rows = []
    for p in PROVIDERS:
        key_ok = configured(p)
        until = cooldowns.get(p.name, 0.0)
        rows.append({"name": p.name, "configured": key_ok, "model": model_for(p),
                     "cooldown_seconds": max(0, int(until - now)),
                     "last_error": failures.get(p.name, "")})
    return rows


def reset_state_for_tests() -> None:
    with _LOCK:
        _COOLDOWN_UNTIL.clear()
        _LAST_FAILURE.clear()


# ---------------------------------------------------------------------------
# Automatic model discovery (Update 18)
# ---------------------------------------------------------------------------

def _discovery_endpoint(provider: Provider) -> str | None:
    """Return the /models discovery endpoint for a provider, or None."""
    return MODEL_ENDPOINTS.get(provider.name)


def _extract_model_ids(payload: Any, provider_name: str) -> list[str]:
    """Extract model IDs from a provider's /models response (format varies)."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", [])
    if not isinstance(data, list):
        return []
    ids: list[str] = []
    for item in data:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("name", "")
            if isinstance(mid, str) and mid:
                # Strip provider prefix (e.g. "models/gemini-2.5-flash" -> "gemini-2.5-flash")
                if "/" in mid:
                    mid = mid.split("/")[-1]
                ids.append(mid)
    return ids


def discover_available_models(provider: Provider, force_refresh: bool = False) -> list[str]:
    """
    Query a provider's /models endpoint using just the API key.
    Returns a list of available model IDs, or falls back to the default model.
    Results are cached for DISCOVERY_CACHE_TTL seconds.
    """
    now = time.monotonic()
    if (
        not force_refresh
        and provider.name in _DISCOVERED_MODELS
        and now - _DISCOVERY_TIMESTAMP.get(provider.name, 0.0) < DISCOVERY_CACHE_TTL
    ):
        return _DISCOVERED_MODELS[provider.name]

    key = _key(provider)
    if not key or key == "PASTE_YOUR_KEY_HERE":
        return [provider.default_model]

    if requests is None:
        return [provider.default_model]

    endpoint = _discovery_endpoint(provider)
    if not endpoint:
        return [provider.default_model]

    try:
        headers = {"Authorization": f"Bearer {key}"}
        # Gemini uses query-param auth instead of Bearer header
        if provider.name == "gemini":
            resp = requests.get(endpoint, params={"key": key}, timeout=8)
        else:
            resp = requests.get(endpoint, headers=headers, timeout=8)
        if resp.status_code == 200:
            ids = _extract_model_ids(resp.json(), provider.name)
            if ids:
                _DISCOVERED_MODELS[provider.name] = ids
                _DISCOVERY_TIMESTAMP[provider.name] = now
                return ids
    except Exception:
        pass  # Fall through to cached/default

    return _DISCOVERED_MODELS.get(provider.name, [provider.default_model])


def select_best_model(provider: Provider, task_category: str = "general") -> str:
    """
    Pick the best available model for a task category from discovered models.
    Falls back to the provider's default_model if discovery yields nothing
    or no keyword matches.
    """
    available = discover_available_models(provider)
    prefs = TASK_MODEL_PREFERENCES.get(task_category, TASK_MODEL_PREFERENCES["general"])

    low_available = [m.lower() for m in available]
    for pref in prefs:
        for mid in low_available:
            if pref in mid:
                return available[low_available.index(mid)]

    # No keyword match — return first available model
    return available[0] if available else provider.default_model


def refresh_all_discovered_models() -> None:
    """Force-refresh discovered models for all configured providers (background-safe)."""
    for p in PROVIDERS:
        try:
            discover_available_models(p, force_refresh=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Automatic ensemble vs single-provider decision (Update 18)
# ---------------------------------------------------------------------------

def should_use_ensemble(goal: str) -> bool:
    """
    Automatically decide whether a task warrants ensemble (parallel multi-provider)
    routing vs single-provider failover.

    Heuristics:
    - Multiple distinct task domains detected (e.g. code + search) -> ensemble
    - Complex single-domain task (60+ words) -> ensemble
    - Multiple conjunctions indicating multi-part task -> ensemble
    - Otherwise -> single provider
    """
    low = goal.lower()
    word_count = len(goal.split())

    domain_keywords = {
        "code": ("code", "script", "python", "json", "function", "program", "debug", "spreadsheet", "excel", "pandas"),
        "search": ("search", "find", "current", "latest", "news", "weather", "price", "stock", "who is", "what is", "when", "where"),
        "creative": ("write", "story", "poem", "essay", "article", "blog", "narrative", "explain", "summarize"),
        "math": ("math", "calculate", "solve", "equation", "reason", "logic", "proof", "theorem"),
        "multilingual": ("translate", "chinese", "hindi", "spanish", "french", "german", "japanese", "korean", "arabic", "telugu"),
        "vision": ("image", "picture", "photo", "screenshot", "pdf", "document", "scan", "ocr", "view", "see"),
    }

    domains_detected = 0
    for _domain, keywords in domain_keywords.items():
        if any(k in low for k in keywords):
            domains_detected += 1

    if domains_detected >= 2:
        return True

    if word_count >= 60 and domains_detected >= 1:
        return True

    conjunction_count = sum(
        1
        for c in (" and ", " then ", " also ", " plus ", " as well as ", " while ", " at the same time ")
        if c in low
    )
    if conjunction_count >= 2:
        return True

    return False


# ---------------------------------------------------------------------------
# Task Decomposition + Ensemble Routing (Update 18)
# ---------------------------------------------------------------------------

# Task type keywords -> provider groups for parallel ensemble routing
TASK_ENSEMBLE_MAP: dict[str, list[str]] = {
    "code": ["deepseek", "cerebras", "groq", "mistral"],
    "math": ["xai", "nvidia", "groq", "gemini"],
    "creative": ["gemini", "groq", "mistral"],
    "search": ["perplexity", "openrouter", "gemini"],
    "multilingual": ["glm", "gemini", "groq"],
    "vision": ["gemini", "glm"],
    "long_context": ["gemini", "openrouter"],
    "general": ["groq", "cerebras", "nvidia", "mistral", "gemini", "openrouter"],
}


def _provider_by_name(name: str) -> Provider | None:
    for p in PROVIDERS:
        if p.name == name:
            return p
    return None


def decompose_task(goal: str, gear: str = "standard") -> list[dict[str, Any]]:
    """
    Split one user task into subtasks with provider assignments.

    Returns a list of subtask dicts with keys:
      - subtask: the sub-goal text
      - provider: preferred provider name
      - model: model to use (auto-discovered from provider's /models endpoint)
      - priority: execution order (1 = first)

    If no decomposition keywords match, returns a single subtask with the
    general provider order (single-provider mode).
    """
    low = goal.lower()
    subtasks: list[dict[str, Any]] = []
    priority = 1

    def _subtask(provider_name: str, category: str, text: str) -> None:
        p = _provider_by_name(provider_name)
        model = select_best_model(p, category) if p else provider_name
        subtasks.append({
            "subtask": text,
            "provider": provider_name,
            "model": model,
            "priority": priority,
        })

    # Code / scripting tasks -> DeepSeek (auto-discovered model)
    if any(x in low for x in ("code", "script", "python", "json", "function", "program", "debug", "spreadsheet", "excel", "pandas")):
        _subtask("deepseek", "code", f"Code task: {goal}")
        priority += 1

    # Math / reasoning tasks -> xAI (auto-discovered model)
    if any(x in low for x in ("math", "calculate", "solve", "equation", "reason", "logic", "proof", "theorem")):
        _subtask("xai", "math", f"Math/reasoning: {goal}")
        priority += 1

    # Creative / narrative / writing tasks -> Gemini (auto-discovered model)
    if any(x in low for x in ("write", "story", "poem", "essay", "article", "blog", "narrative", "explain", "summarize")):
        _subtask("gemini", "creative", f"Creative writing: {goal}")
        priority += 1

    # Search / current info / web tasks -> Perplexity (auto-discovered model)
    if any(x in low for x in ("search", "find", "current", "latest", "news", "weather", "stock", "price", "who is", "what is", "when", "where")):
        _subtask("perplexity", "search", f"Web research: {goal}")
        priority += 1

    # Vision / image / document tasks -> Gemini (auto-discovered model)
    if any(x in low for x in ("image", "picture", "photo", "screenshot", "pdf", "document", "scan", "ocr", "view", "see")):
        _subtask("gemini", "vision", f"Vision/document: {goal}")
        priority += 1

    # Multilingual / translation tasks -> GLM (auto-discovered model)
    if any(x in low for x in ("translate", "chinese", "hindi", "spanish", "french", "german", "japanese", "korean", "arabic", "telugu", "hindi")):
        _subtask("glm", "multilingual", f"Multilingual: {goal}")
        priority += 1

    # Long context / large document tasks -> Gemini (auto-discovered model)
    if any(x in low for x in ("long", "large", "big document", "whole file", "entire", "all pages", "full text")):
        _subtask("gemini", "long_context", f"Long context: {goal}")
        priority += 1

    # If no specific keywords matched, use general routing (single subtask)
    if not subtasks:
        subtasks.append({
            "subtask": goal,
            "provider": "groq",
            "model": select_best_model(_provider_by_name("groq"), "general"),
            "priority": 1,
        })

    return subtasks


def model_for_by_name(name: str) -> str:
    """Get the default model for a provider by name."""
    for p in PROVIDERS:
        if p.name == name:
            return model_for(p)
    return ""


def ensemble_candidates(prompt: str, gear: str = "standard") -> list[tuple[Provider, str, str, str]]:
    """
    Return provider candidates for ensemble (parallel) execution.

    Returns list of (Provider, api_key, model, subtask_description) tuples,
    one per subtask. Each subtask goes to its best-matched provider with its
    auto-discovered model. Only configured, non-cooling-down providers are included.
    """
    now = time.monotonic()
    subtasks = decompose_task(prompt, gear)
    by_name = {p.name: p for p in PROVIDERS}
    with _LOCK:
        cooldowns = dict(_COOLDOWN_UNTIL)

    result: list[tuple[Provider, str, str, str]] = []
    seen_subtasks: set[str] = set()

    for st in subtasks:
        pname = st["provider"]
        if pname not in by_name:
            continue
        p = by_name[pname]
        if not configured(p):
            continue
        if cooldowns.get(pname, 0.0) > now:
            continue
        subtask_key = f"{pname}:{st['subtask'][:50]}"
        if subtask_key in seen_subtasks:
            continue
        seen_subtasks.add(subtask_key)
        result.append((p, _key(p), st.get("model", model_for(p)), st["subtask"]))

    # Fallback: if no ensemble providers available, return single general candidate
    if not result:
        for name in _rank(prompt, gear):
            p = by_name[name]
            if configured(p) and cooldowns.get(name, 0.0) <= now:
                result.append((p, _key(p), model_for(p), prompt))
                break

    return result


def merge_ensemble_results(results: list[dict[str, Any]]) -> str:
    """
    Merge multiple provider responses into one coherent answer.

    results: list of dicts with keys: provider, model, subtask, response, error
    Returns a merged string with deduplicated content.
    """
    if not results:
        return ""

    successful = [r for r in results if r.get("response") and not r.get("error")]
    if not successful:
        # Return the first error message if all failed
        for r in results:
            if r.get("error"):
                return f"[All providers failed] {r['error']}"
        return ""

    if len(successful) == 1:
        return successful[0]["response"]

    # Build merged response
    parts: list[str] = []
    seen_content: set[str] = set()

    for r in successful:
        provider = r.get("provider", "unknown")
        subtask = r.get("subtask", "")
        response = r.get("response", "").strip()

        # Skip near-duplicate content (simple heuristic)
        content_key = response[:100].lower()
        if content_key in seen_content:
            continue
        seen_content.add(content_key)

        header = f"## {provider}" + (f" ({subtask})" if subtask and subtask != response[:50] else "")
        parts.append(f"{header}\n{response}")

    merged = "\n\n---\n\n".join(parts)
    return merged


def is_ensemble_enabled() -> bool:
    """Check if ensemble mode is enabled via environment variable."""
    return os.environ.get("CELINA_ENSEMBLE", "0").strip().lower() in ("1", "true", "yes", "on")


def set_ensemble(enabled: bool) -> None:
    """Enable or disable ensemble mode."""
    os.environ["CELINA_ENSEMBLE"] = "1" if enabled else "0"
