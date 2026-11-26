"""macubuntu celina_pkg.llm.lifecycle_router — LLM Lifecycle, Cloud Routing & PII Gate.

Consolidates Patches 1, 2, 3, 5, and 7 from the routing/lifecycle audit:

  Patch 1 — Outbound Reversible PII & Secret Redaction Gate (RedactionSession)
      Scrubs emails, tokens, local paths, phone numbers from outbound cloud-bound
      text with reversible placeholders ([EMAIL_01], [SECRET_01], [PATH_01]).
      Rehydrates on response; tokens stay redacted in logs and evidence.py.

  Patch 2 — Circuit Breaker & Silent Offline Failover (HybridCloudRouter)
      Tracks rolling timeouts/5xx/rate-limits. Trips a 300s circuit breaker when
      the cloud connector is unreachable or exceeds a 12s first-token budget,
      then transparently falls back to the local tactical/deep model.

  Patch 3 — Egress Audit Receipts
      Logs every cloud escalation (model, redacted byte count, token estimate,
      latency, approval ID) into evidence.py via record_execution().

  Patch 5 — Active Offloading (POST /models/unload + verification)
      LocalLlamaLifecycleClient.unload_model() actively frees RAM/VRAM and polls
      GET /models until status confirms unloaded. evict_all_models() for hw_governor.

  Patch 7 — UDS Transport & Server Hardening
      All internal clients use httpx.AsyncClient over the 0600 UDS socket
      (/run/user/$UID/celina/llama.sock) with --api-key-file (never --api-key in
      CLI args). --connect (P2P tunnel) and server-side --mcp/--tools stay
      disabled; Celina's fail-closed manifest remains the sole tool gate.

stdlib + httpx only. No PyTorch, no network for import.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Patch 1 — Outbound Reversible PII & Secret Redaction Gate
# ---------------------------------------------------------------------------

# Lightweight zero-PyTorch outbound secret & PII patterns.
# Extend with Microsoft Presidio/local NER on 16GB/32GB profiles.
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "SECRET",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})\b",
        ),
    ),
    (
        "EMAIL",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "PHONE",
        re.compile(
            r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})\b",
        ),
    ),
    (
        "LOCALPATH",
        re.compile(r"/home/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._/@-]+)*"),
    ),
]


@dataclass
class RedactionSession:
    """Reversible PII/secret redaction session.

    redact() replaces sensitive spans with numbered placeholders
    ([SECRET_01], [EMAIL_01], [PHONE_01], [PATH_01]) and records the mapping.
    rehydrate() restores originals on the cloud response before returning to
    the user. Placeholders (not originals) are written to logs and evidence.py.
    """

    token_to_raw: dict[str, str] = field(default_factory=dict)
    raw_to_token: dict[str, str] = field(default_factory=dict)

    def redact(self, text: str) -> str:
        out = text
        for label, rx in PII_PATTERNS:
            def _sub(match: re.Match[str]) -> str:
                raw = match.group(0)
                if raw in self.raw_to_token:
                    return self.raw_to_token[raw]
                tok = f"[{label}_{len(self.token_to_raw) + 1:02d}]"
                self.token_to_raw[tok] = raw
                self.raw_to_token[raw] = tok
                return tok
            out = rx.sub(_sub, out)
        return out

    def rehydrate(self, text: str) -> str:
        out = text
        for tok, raw in self.token_to_raw.items():
            out = out.replace(tok, raw)
        return out

    @property
    def redacted_count(self) -> int:
        return len(self.token_to_raw)


# ---------------------------------------------------------------------------
# Patch 5 — LocalLlamaLifecycleClient (UDS transport, active unload)
# ---------------------------------------------------------------------------


class LocalLlamaLifecycleClient:
    """Talks to llama-server strictly over a 0600 Unix domain socket.

    Patch 7: No hardcoded TCP URLs (e.g. http://127.0.0.1:8080). All internal
    clients use httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=...)).
    API key is loaded from --api-key-file (never --api-key in CLI args).
    --connect (P2P tunnel) and server-side --mcp/--tools remain disabled;
    Celina's fail-closed manifest is the sole tool gate.
    """

    def __init__(
        self,
        socket_path: Path,
        api_key_file: Path | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        headers: dict[str, str] = {}
        if api_key_file is not None and api_key_file.exists():
            headers["Authorization"] = f"Bearer {api_key_file.read_text().strip()}"
        transport = httpx.AsyncHTTPTransport(uds=str(self.socket_path))
        self._client = httpx.AsyncClient(
            transport=transport,
            base_url="http://llama.local",
            headers=headers,
            timeout=httpx.Timeout(
                connect=5.0, read=90.0, write=15.0, pool=5.0,
            ),
        )

    async def loaded_models(self) -> list[dict[str, Any]]:
        r = await self._client.get("/models")
        r.raise_for_status()
        return r.json().get("data", [])

    async def unload_model(self, alias: str, verify_timeout_s: float = 10.0) -> bool:
        """Patch 5: Actively frees RAM/VRAM in llama-server Router Mode via
        POST /models/unload and verifies completion by polling GET /models.

        Returns True when the model is confirmed unloaded/sleeping.
        In single-model mode (/models/unload returns 404), falls back to
        checking is_sleeping via GET /props.
        """
        try:
            resp = await self._client.post(
                "/models/unload", json={"model": alias},
            )
        except httpx.HTTPError:
            return False

        if resp.status_code == 404:
            # Single-model mode: no /models/unload; check sleep state via /props
            try:
                props = (await self._client.get("/props")).json()
                return bool(props.get("is_sleeping", False))
            except httpx.HTTPError:
                return False

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            return False

        deadline = time.monotonic() + verify_timeout_s
        while time.monotonic() < deadline:
            try:
                models = await self.loaded_models()
            except httpx.HTTPError:
                await asyncio.sleep(0.25)
                continue
            entry = next((m for m in models if m.get("id") == alias), None)
            if entry is None:
                return True
            status_value = entry.get("status", {}).get("value", "")
            if status_value in {"unloaded", "sleeping"}:
                return True
            await asyncio.sleep(0.25)
        return False

    async def evict_all_models(self) -> list[str]:
        """Patch 5: Called by hw_governor.py on PSI > threshold or fullscreen GPU
        launch. Actively unloads every loaded model and returns the list of
        aliases successfully evicted.
        """
        evicted: list[str] = []
        try:
            models = await self.loaded_models()
        except httpx.HTTPError:
            return evicted
        for m in models:
            alias = m.get("id")
            status = m.get("status", {}).get("value", "")
            if alias and status in {"loaded", "loading"}:
                if await self.unload_model(alias):
                    evicted.append(alias)
        return evicted

    async def tts_wav(self, text: str, voice: str = "female") -> bytes:
        """Patch 7: UDS replacement for hardcoded 127.0.0.1:8080 TTS calls.
        Routes over /run/user/$UID/celina/llama.sock.
        """
        r = await self._client.post(
            "/v1/audio/speech",
            json={
                "input": text,
                "voice": voice,
                "lang": "en",
                "response_format": "wav",
            },
        )
        r.raise_for_status()
        return r.content

    async def props(self) -> dict[str, Any]:
        r = await self._client.get("/props")
        r.raise_for_status()
        return r.json()

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Patch 2 — Circuit Breaker & Silent Offline Failover
# Patch 3 — Egress Audit Receipts
# ---------------------------------------------------------------------------


@dataclass
class CloudRouteResult:
    route: str  # "cloud_sanitized" or "local"
    redacted_items: int
    text: str
    latency_ms: float = 0.0
    circuit_open: bool = False


class HybridCloudRouter:
    """Routes local-first with reversible PII redaction + circuit-breaker failover
    to the local model.

    Cloud escalation only fires when:
      - allow_cloud is True (explicit user opt-in / @cloud / island pill), AND
      - estimated_tokens exceeds the local tier window (e.g. >85% of ctx),
      - AND the circuit breaker is closed (cloud recently healthy).

    On cloud failure/timeout: trip circuit breaker for cooldown_s, silently
    fall back to local model with a subtle celina-island notice (route="local",
    circuit_open=True so the UI can show a notice).
    """

    def __init__(
        self,
        local_call: Callable[[str], Any],
        cloud_call: Callable[[str], Any],
        *,
        cooldown_s: float = 300.0,
        first_token_budget_s: float = 12.0,
        local_ctx_limit: int = 16384,
    ) -> None:
        self._local_call = local_call
        self._cloud_call = cloud_call
        self._cooldown_s = cooldown_s
        self._first_token_budget_s = first_token_budget_s
        self._local_ctx_limit = local_ctx_limit
        self._cloud_open_until: float = 0.0

    @property
    def circuit_open(self) -> bool:
        return time.monotonic() < self._cloud_open_until

    async def complete(
        self,
        prompt: str,
        *,
        allow_cloud: bool = False,
        estimated_tokens: int = 0,
        local_ctx_limit: int | None = None,
        approval_id: str = "",
    ) -> CloudRouteResult:
        """Route a completion. Returns CloudRouteResult with route, redacted count,
        text, latency, and circuit_open flag (for UI notice).
        """
        ctx_limit = local_ctx_limit if local_ctx_limit is not None else self._local_ctx_limit
        needs_cloud = (
            allow_cloud
            and not self.circuit_open
            and estimated_tokens > int(ctx_limit * 0.85)
        )

        now = time.monotonic()

        if needs_cloud:
            # Patch 1: Redact outbound PII/secrets before any cloud send.
            session = RedactionSession()
            sanitized = session.redact(prompt)
            redacted_count = session.redacted_count
            start = time.monotonic()
            try:
                raw_reply = await asyncio.wait_for(
                    self._cloud_call(sanitized),
                    timeout=self._first_token_budget_s,
                )
                latency_ms = (time.monotonic() - start) * 1000.0
                # Patch 3: Egress audit receipt to evidence.py.
                _log_cloud_egress(
                    model="cloud-frontier",
                    redacted_bytes=len(sanitized.encode("utf-8")),
                    token_estimate=estimated_tokens,
                    latency_ms=latency_ms,
                    approval_id=approval_id,
                    redacted_count=redacted_count,
                    ok=True,
                )
                return CloudRouteResult(
                    route="cloud_sanitized",
                    redacted_items=redacted_count,
                    text=session.rehydrate(str(raw_reply)),
                    latency_ms=latency_ms,
                )
            except Exception:
                # Patch 2: Trip circuit breaker, fail over silently to local.
                self._cloud_open_until = now + self._cooldown_s
                _log_cloud_egress(
                    model="cloud-frontier",
                    redacted_bytes=len(sanitized.encode("utf-8")),
                    token_estimate=estimated_tokens,
                    latency_ms=(time.monotonic() - start) * 1000.0,
                    approval_id=approval_id,
                    redacted_count=redacted_count,
                    ok=False,
                    error="cloud unreachable or timeout — circuit tripped, local failover",
                )

        # Local path (or cloud unavailable after circuit trip)
        local_reply = await self._local_call(prompt)
        return CloudRouteResult(
            route="local",
            redacted_items=0,
            text=str(local_reply),
            circuit_open=self.circuit_open,
        )


# ---------------------------------------------------------------------------
# Patch 3 — Egress Audit Receipts (evidence.py integration)
# ---------------------------------------------------------------------------


def _log_cloud_egress(
    *,
    model: str,
    redacted_bytes: int,
    token_estimate: int,
    latency_ms: float,
    approval_id: str,
    redacted_count: int,
    ok: bool,
    error: str = "",
) -> None:
    """Log every cloud escalation into evidence.py so the user can audit exactly
    what left the machine. Fail-closed: never raises.
    """
    try:
        from .evidence import get_evidence_ledger

        ledger = get_evidence_ledger()
        ledger.record_execution(
            tool_name="cloud_escalation",
            arguments={
                "model": model,
                "redacted_bytes": redacted_bytes,
                "token_estimate": token_estimate,
                "latency_ms": round(latency_ms, 2),
                "approval_id": approval_id,
                "redacted_count": redacted_count,
                "ok": ok,
                "error": error[:200] if error else "",
            },
            result_text=(
                f"cloud_escalation model={model} ok={ok} "
                f"redacted={redacted_count} bytes={redacted_bytes} "
                f"latency_ms={latency_ms:.1f}"
            ),
            why_reasoning="Frontier escalation — outbound PII redacted, audit receipt logged",
            tier="tier2",
        )
    except Exception:
        # Evidence logging must never break the routing path.
        pass
