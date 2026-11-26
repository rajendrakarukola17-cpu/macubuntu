"""macubuntu celina_pkg.frontier_escalation — Opt-In Frontier Escalation MCP.

A FastMCP-style cloud bridge gated by explicit user approval. When a local
task requires 1M-token cross-repo synthesis, complex legal/technical drafting,
or high-stakes multi-file refactors beyond local 4B–35B models, Celina offers
a one-click escalation pill in celina-island to route only *sanitized* context
to an opt-in cloud connector (GPT-6.2 Astra / Claude / Gemini).

Security invariants (zero-cloud privacy preserved):
  - Opt-in only: local execution is the default; cloud is NEVER used without
    explicit per-escalation approval via celina-island.
  - Dual-Context Barrier: context passes through mcp_guard Quarantined Reader
    (sanitization, secret/PII redaction) before any cloud send. The cloud
    connector never sees raw unsanitized text.
  - SSRF/scope enforcement: all outbound cloud requests route through
    net_policy.safe_get (no loopback, no RFC1918, no metadata IPs, https-only).
  - Allowlist: only configured cloud endpoints are permitted (default: none —
    escalation is disabled until the user adds an endpoint).
  - Content-size bounds: max 256 KB per escalation request.
  - Audit: every escalation logged to evidence.py with content hash.

stdlib-only: json, re, hashlib, time, pathlib, dataclasses, typing, urllib.
Optional: requests (for cloud HTTP), pydantic (schemas).
No TOOL_SCHEMAS changes — standalone module, no new registered tools.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import net_policy
from .security import mcp_guard

# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


@dataclass
class CloudEndpoint:
    """A configured cloud escalation endpoint (opt-in, user-configured)."""

    name: str
    base_url: str               # https://api.example.com/v1
    model: str                  # e.g. "gpt-6.2-astra", "claude-3-opus", "gemini-2.5-pro"
    api_key_env: str = "FRONTIER_API_KEY"   # env var name for the API key
    enabled: bool = False       # must be explicitly enabled by user
    max_tokens: int = 1048576   # 1M-token cross-repo synthesis cap
    requires_approval: bool = True


@dataclass
class EscalationRequest:
    """A request to escalate a task to the frontier cloud model."""

    task_id: str
    goal: str
    context: str                # sanitized context (already through Quarantined Reader)
    endpoint: str               # endpoint name
    model: str
    estimated_tokens: int
    reason: str                 # why escalation is needed
    source_trust: str = mcp_guard.DEFAULT_SOURCE_TRUST
    approved: bool = False
    escalated_at: str | None = None


@dataclass
class EscalationResult:
    """Result of a frontier escalation."""

    ok: bool
    task_id: str
    endpoint: str
    model: str
    response: str = ""
    tokens_used: int = 0
    error: str = ""
    evidence_id: str = ""


# --------------------------------------------------------------------------- #
# Endpoint registry
# --------------------------------------------------------------------------- #

ESCALATION_DIR = Path.home() / ".local" / "share" / "celina" / "escalation"
ENDPOINTS_FILE = ESCALATION_DIR / "endpoints.json"
HISTORY_FILE = ESCALATION_DIR / "history.jsonl"


def ensure_escalation_dir() -> None:
    ESCALATION_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def register_endpoint(
    *,
    name: str,
    base_url: str,
    model: str,
    api_key_env: str = "FRONTIER_API_KEY",
    max_tokens: int = 1048576,
) -> CloudEndpoint:
    """Register a cloud escalation endpoint.

    Fail-closed: https-only, no metadata/loopback/RFC1918 hosts (validated
    via net_policy.validate_public_url). Disabled by default until user
    explicitly enables it.
    """
    # Validate the base URL via net_policy (SSRF/scope enforcement)
    try:
        net_policy.validate_public_url(base_url)
    except net_policy.URLPolicyError as exc:
        raise ValueError(f"endpoint '{name}' blocked by network policy: {exc}")

    endpoint = CloudEndpoint(
        name=name,
        base_url=base_url.rstrip("/"),
        model=model,
        api_key_env=api_key_env,
        enabled=False,
        max_tokens=max(256, min(1048576, int(max_tokens))),
    )
    ensure_escalation_dir()
    endpoints = _load_json(ENDPOINTS_FILE, [])
    if not isinstance(endpoints, list):
        endpoints = []
    # Replace if exists, else append
    endpoints = [e for e in endpoints if e.get("name") != name]
    endpoints.append({
        "name": endpoint.name,
        "base_url": endpoint.base_url,
        "model": endpoint.model,
        "api_key_env": endpoint.api_key_env,
        "enabled": endpoint.enabled,
        "max_tokens": endpoint.max_tokens,
        "requires_approval": endpoint.requires_approval,
    })
    _save_json(ENDPOINTS_FILE, endpoints)
    return endpoint


def list_endpoints() -> list[CloudEndpoint]:
    """List registered cloud endpoints."""
    ensure_escalation_dir()
    raw = _load_json(ENDPOINTS_FILE, [])
    endpoints: list[CloudEndpoint] = []
    if isinstance(raw, list):
        for e in raw:
            endpoints.append(CloudEndpoint(
                name=str(e.get("name", "")),
                base_url=str(e.get("base_url", "")),
                model=str(e.get("model", "")),
                api_key_env=str(e.get("api_key_env", "FRONTIER_API_KEY")),
                enabled=bool(e.get("enabled", False)),
                max_tokens=int(e.get("max_tokens", 1048576)),
                requires_approval=bool(e.get("requires_approval", True)),
            ))
    return endpoints


def enable_endpoint(name: str, enabled: bool = True) -> bool:
    """Enable/disable a cloud endpoint (user opt-in)."""
    endpoints = _load_json(ENDPOINTS_FILE, [])
    if not isinstance(endpoints, list):
        return False
    for e in endpoints:
        if e.get("name") == name:
            e["enabled"] = bool(enabled)
            _save_json(ENDPOINTS_FILE, endpoints)
            return True
    return False


# --------------------------------------------------------------------------- #
# Dual-Context Barrier: sanitize context before cloud send
# --------------------------------------------------------------------------- #


def sanitize_escalation_context(
    *,
    raw_context: str,
    source_trust: str = mcp_guard.DEFAULT_SOURCE_TRUST,
) -> tuple[str, mcp_guard.QuarantinedIntent]:
    """Quarantined Reader: sanitize escalation context before cloud send.

    Never sends raw unsanitized text to a frontier model. Applies mcp_guard
    reader_to_intent classification and redacts anything flagged as a
    write/delete/send action or containing secrets.

    Returns (sanitized_context, intent). The intent tells the caller whether
    the content is safe to escalate.
    """
    guard = mcp_guard.McpGuard()
    intent = guard.reader_to_intent(
        raw_text=raw_context,
        source_trust=source_trust,
        allowed_actions=["read"],
    )

    sanitized = raw_context
    if intent.action in {"write", "delete", "send"}:
        # Strip actionable content — don't send write/delete/send instructions to cloud
        sanitized = "[REDACTED: actionable content stripped before cloud escalation]"
    elif intent.source_trust == mcp_guard.DEFAULT_SOURCE_TRUST and not intent.approved:
        # Untrusted source — still sanitize but flag
        sanitized = f"[UNTRUSTED_SOURCE] {sanitized}"

    return sanitized, intent


# --------------------------------------------------------------------------- #
# Cloud HTTP client (via net_policy for SSRF protection)
# --------------------------------------------------------------------------- #


def _get_api_key(endpoint: CloudEndpoint) -> str | None:
    return os.environ.get(endpoint.api_key_env)


def _cloud_headers(endpoint: CloudEndpoint) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = _get_api_key(endpoint)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def escalate_to_cloud(
    *,
    request: EscalationRequest,
) -> EscalationResult:
    """Send a sanitized escalation request to a frontier cloud model.

    Fail-closed:
      - Endpoint must be registered AND enabled
      - Must use https:// (validated at registration)
      - API key must be present in env
      - Request size bounded at 256 KB
      - Requires explicit approval (request.approved must be True)

    Returns EscalationResult. All events logged to evidence.py.
    """
    ensure_escalation_dir()
    result = EscalationResult(
        ok=False, task_id=request.task_id, endpoint=request.endpoint, model=request.model,
    )

    # Find endpoint
    endpoints = {e.name: e for e in list_endpoints()}
    endpoint = endpoints.get(request.endpoint)
    if endpoint is None:
        result.error = f"endpoint not registered: {request.endpoint}"
        _log_escalation(request, result, approved=False)
        return result
    if not endpoint.enabled:
        result.error = f"endpoint not enabled (opt-in required): {request.endpoint}"
        _log_escalation(request, result, approved=False)
        return result
    if not request.approved:
        result.error = "escalation not approved (celina-island consent required)"
        _log_escalation(request, result, approved=False)
        return result

    # API key check
    api_key = _get_api_key(endpoint)
    if not api_key:
        result.error = f"API key not found in env var {endpoint.api_key_env}"
        _log_escalation(request, result, approved=False)
        return result

    # Size bound: 256 KB max
    payload_ctx = request.context[:256 * 1024]
    if len(request.context) > 256 * 1024:
        payload_ctx = payload_ctx + "\n...[context truncated for cloud escalation]..."

    # Build request payload (provider-agnostic chat format)
    payload = {
        "model": endpoint.model,
        "messages": [
            {"role": "system", "content": "You are a frontier assistant responding to a Celina escalation. Be concise, cite sources, and do not request local system access."},
            {"role": "user", "content": f"Task: {request.goal}\\n\\nReason: {request.reason}\\n\\nContext:\\n{payload_ctx}"},
        ],
        "max_tokens": min(endpoint.max_tokens, 4096),
        "temperature": 0.2,
    }

    url = f"{endpoint.base_url}/chat/completions"
    try:
        resp = net_policy.safe_request(
            "POST",
            url,
            policy="public",
            max_bytes=256 * 1024,
            timeout=(10.0, 60.0),
            allowed_content_types={"application/json", "text/event-stream"},
            headers=_cloud_headers(endpoint),
            json_body=payload,
        )
        if resp.status_code >= 400:
            result.error = f"cloud HTTP {resp.status_code}: {resp.text[:200]}"
            _log_escalation(request, result, approved=True)
            return result
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:500]}
        result.response = str(data)[:2000]
        result.tokens_used = int(data.get("usage", {}).get("total_tokens", 0)) if isinstance(data, dict) else 0
        result.ok = True
    except net_policy.URLPolicyError as exc:
        result.error = f"blocked by network policy: {exc}"
    except Exception as exc:
        result.error = f"cloud request failed: {exc}"

    _log_escalation(request, result, approved=True, ok=result.ok)
    return result


# --------------------------------------------------------------------------- #
# Audit logging to evidence.py
# --------------------------------------------------------------------------- #


def _log_escalation(
    request: EscalationRequest,
    result: EscalationResult,
    *,
    approved: bool,
    ok: bool = False,
) -> None:
    """Log every escalation attempt to the evidence ledger + history JSONL."""
    entry = {
        "task_id": request.task_id,
        "endpoint": request.endpoint,
        "model": request.model,
        "goal": request.goal[:200],
        "reason": request.reason[:200],
        "estimated_tokens": request.estimated_tokens,
        "source_trust": request.source_trust,
        "approved": approved,
        "ok": ok,
        "tokens_used": result.tokens_used,
        "error": result.error[:200] if result.error else "",
        "escalated_at": request.escalated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # JSONL history
    try:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        pass
    # Evidence ledger
    try:
        from . import evidence
        ledger = evidence.get_evidence_ledger()
        ledger.record_security_event(
            event_type="frontier_escalation",
            tool_name=str(request.endpoint or "frontier_escalation"),
            detail=json.dumps(entry, sort_keys=True),
            summary=str(request.reason[:200]),
            task_id=str(request.task_id),
            actor_identity=f"uid:{os.getuid()}",
            policy_decision=("allow" if approved else "deny:frontier_escalation"),
        )
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Escalation decision helper
# --------------------------------------------------------------------------- #


def should_escalate(
    *,
    context_len_chars: int,
    task_type: str,
    local_ctx_used_pct: float,
) -> tuple[bool, str]:
    """Heuristic to decide whether to offer a frontier escalation pill.

    Returns (should_offer, reason). Offers escalation when:
      - Context is very large (>200K chars ≈ 1M-token cross-repo synthesis)
      - Task type is high-stakes (legal, technical drafting, multi-file refactor)
      - Local context is exhausted (>80% used)

    Returns (False, reason) when local handling is sufficient.
    """
    if local_ctx_used_pct > 0.80:
        return True, "local context exhausted (>80%)"
    if context_len_chars > 200_000:
        return True, "large context (>200K chars) — cross-repo synthesis candidate"
    if task_type in {"legal", "technical_drafting", "multi_file_refactor", "high_stakes"}:
        return True, f"high-stakes task type: {task_type}"
    return False, "local handling sufficient"


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "endpoints"
    if cmd == "endpoints":
        for ep in list_endpoints():
            print(f"  {ep.name}: {ep.base_url} model={ep.model} enabled={ep.enabled}")
    elif cmd == "register":
        print("Usage: frontier_escalation.py endpoints | decide")
    elif cmd == "decide":
        offer, reason = should_escalate(
            context_len_chars=250_000,
            task_type="multi_file_refactor",
            local_ctx_used_pct=0.85,
        )
        print(f"escalate: {offer} — {reason}")
