"""Celina AI (Celly) - Connector Base Framework (Update 23)

Adapted from MausBot concepts (Apache-2.0, github.com/milind-soni/OpenMausBot)
to Python. No TypeScript code port.

Design:
- Connectors live OUTSIDE Gear 0, NOT in the Gear 0 dispatch chain
- Opt-in only — each connector requires explicit credential setup
- Allow/Deny permission broker for risky actions (MausBot permission cards)
- No webhook server — polling/schedule-based only
- No exponential backoff/jitter — bounded retry on 429/5xx only
- 102-tool contract preserved: connectors add tools only via explicit version delta
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from celina_pkg import config, evidence
from celina_pkg.approval import RiskLevel
from celina_pkg.config import atomic_write_private_text
from celina_pkg.net_policy import URLPolicyError, validate_web_comms_host


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that raises on any redirect — SSRF defense."""

    def redirect_request(  # type: ignore[no-untyped-def]  # overrides urllib.request.HTTPRedirectHandler.redirect_request; complex stdlib types
        self, req, fp, code, msg, headers, newurl,
    ):
        raise URLPolicyError(f"HTTP redirect to {newurl} blocked (redirects not allowed).")


# ------------------------------------------------------------------
# URL path-segment validation (path-traversal / injection defense)
# ------------------------------------------------------------------
# Characters that are never allowed in a URL path segment: path separators,
# URL-encoding marker, query/fragment delimiters, control chars, space, DEL.
_UNSAFE_PATH = re.compile(r"[/%\\?#\x00-\x20\x7f]")
_MAX_PATH_SEGMENT_LEN = 200


def _validate_path_segment(segment: Any, field_name: str = "segment") -> str:
    """Validate a single URL path segment against traversal/injection.

    Blocks / \\ % ? # control characters spaces and DEL — anything that
    could alter URL structure, escape the intended API path, or smuggle
    encoded traversal payloads. Rejects . and .. and caps length at 200
    chars. Otherwise permissive: base64url IDs (with = / + padding),
    UUIDs, and slug-style IDs all pass through.

    Raises ValueError if the segment is unsafe.
    """
    if not isinstance(segment, str):
        raise ValueError(f"{field_name} must be a string, got {type(segment).__name__}")
    if not segment:
        raise ValueError(f"{field_name} must not be empty")
    if len(segment) > _MAX_PATH_SEGMENT_LEN:
        raise ValueError(f"{field_name} exceeds {_MAX_PATH_SEGMENT_LEN} characters")
    if segment in {".", ".."}:
        raise ValueError(f"{field_name} '{segment}' is not a valid path segment")
    if _UNSAFE_PATH.search(segment):
        blocked = "/, %, backslash, ?, #, control chars, space, DEL"
        raise ValueError(f"{field_name} contains unsafe characters (blocked: {blocked}): {segment!r}")
    return segment


class ConnectorStatus(str, Enum):
    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"
    AUTHORIZING = "authorizing"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ConnectorConfig:
    """Immutable configuration for a single connector instance."""

    name: str                      # e.g. "linear", "github", "gmail"
    display_name: str              # human-readable name
    oauth_authorize_url: str | None = None
    oauth_token_url: str | None = None
    oauth_scopes: tuple[str, ...] = ()
    oauth_client_id_env: str | None = None
    oauth_client_secret_env: str | None = None
    api_base_url: str | None = None
    # Per-action risk levels: action_name -> RiskLevel
    action_risk_map: dict[str, RiskLevel] = field(default_factory=dict)
    # Actions that are read-only and never require Allow/Deny
    read_only_actions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ConnectorAction:
    """A single action a connector can perform."""

    name: str                      # e.g. "list_issues", "create_issue"
    description: str
    parameters: dict[str, Any]     # JSON Schema for parameters
    risk_level: RiskLevel = RiskLevel.READ_ONLY


@dataclass
class ActionResult:
    """Result of executing a connector action."""

    success: bool
    data: Any = None
    error: str | None = None
    action: str = ""
    connector: str = ""
    requires_approval: bool = False
    approved: bool = False


class AllowDenyGate:
    """MausBot-style permission broker — Allow/Deny cards for risky connector actions.

    Read-only actions pass through automatically. Actions with risk >= LOCAL_MUTATION
    require explicit approval via the approval_cb callback.
    """

    def __init__(self, approval_cb: Callable[[str, str], bool] | None = None):
        self._approval_cb = approval_cb
        self._pending: dict[str, dict[str, Any]] = {}

    def requires_approval(self, risk_level: RiskLevel) -> bool:
        """Return True if an action at this risk level needs Allow/Deny."""
        return risk_level not in {
            RiskLevel.READ_ONLY,
        }

    def request_approval(
        self,
        action_name: str,
        connector_name: str,
        risk_level: RiskLevel,
        params: dict[str, Any],
    ) -> bool:
        """Show an Allow/Deny card for a risky action.
        Returns True if approved, False if denied.
        """
        if not self.requires_approval(risk_level):
            return True

        if self._approval_cb is None:
            # No callback available — auto-deny for safety
            return False

        # Build a concise description for the Allow/Deny card
        param_summary = ", ".join(f"{k}={v}" for k, v in list(params.items())[:5])
        prompt = (
            f"Connector '{connector_name}' wants to {action_name} "
            f"({risk_level.value}). Parameters: {param_summary[:200]}. "
            f"Allow or deny?"
        )

        try:
            return bool(self._approval_cb(action_name, prompt))
        except Exception:
            return False

    def register_pending(self, action_id: str, action: dict[str, Any]) -> None:
        """Register a pending approval (for asynchronous flows)."""
        self._pending[action_id] = {
            "action": action,
            "created_at": time.monotonic(),
        }

    def resolve_pending(self, action_id: str, approved: bool) -> dict[str, Any] | None:
        """Resolve a pending approval. Returns the action if approved, None otherwise."""
        entry = self._pending.pop(action_id, None)
        if entry is None:
            return None
        return entry["action"] if approved else None


class BaseConnector:
    """Base class for all Celina connectors.

    Subclasses must implement:
    - is_configured() -> bool
    - list_actions() -> list[ConnectorAction]
    - execute_action(action_name, params) -> ActionResult
    - configure(credentials) -> bool
    """

    def __init__(self, config: ConnectorConfig, approval_cb: Callable[[str, str], bool] | None = None):
        self._config = config
        self._gate = AllowDenyGate(approval_cb)
        self._status = ConnectorStatus.NOT_CONFIGURED
        self._last_error: str | None = None
        self._credential_path: Path | None = None

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self._config.name

    @property
    def display_name(self) -> str:
        return self._config.display_name

    @property
    def status(self) -> ConnectorStatus:
        return self._status

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ------------------------------------------------------------------
    # Credential storage (local-first, ~/.celina/storage/connectors/)
    # ------------------------------------------------------------------
    def _credential_dir(self) -> Path:
        base = config.CONFIG_DIR / "connectors"
        base.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(base, 0o700)
        except OSError:
            pass
        return base

    def _credential_file(self) -> Path:
        if self._credential_path is None:
            self._credential_path = self._credential_dir() / f"{self.name}.json"
            try:
                os.chmod(self._credential_path, 0o600) if self._credential_path.exists() else None
            except OSError:
                pass
        return self._credential_path

    def _load_credentials(self) -> dict[str, str] | None:
        """Load stored credentials (never returns secret values in status output)."""
        try:
            cf = self._credential_file()
            if not cf.exists():
                return None
            data = json.loads(cf.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    def _save_credentials(self, credentials: dict[str, str]) -> None:
        """Persist credentials atomically with mode 0600 (no plaintext race window)."""
        cf = self._credential_file()
        atomic_write_private_text(cf, json.dumps(credentials, indent=2, ensure_ascii=False) + "\n")

    def _clear_credentials(self) -> None:
        """Remove stored credentials."""
        try:
            cf = self._credential_file()
            if cf.exists():
                cf.unlink()
        except Exception:  # noqa: S110  # Intentional: catch-and-swallow for optional dependency; failure is non-fatal
            pass

    # ------------------------------------------------------------------
    # Safe URL path building (path-traversal defense)
    # ------------------------------------------------------------------
    @classmethod
    def _build_path(cls, *segments: Any) -> str:
        """Join validated path segments into a leading-slash URL path.

        Each segment is run through _validate_path_segment before joining,
        so no caller can inject `../`, `%2e%2e`, or other traversal payloads
        through user-supplied IDs (message_id, page_id, owner, repo, …).
        """
        safe = "/".join(_validate_path_segment(s, f"path segment {i}") for i, s in enumerate(segments))
        return "/" + safe

    # ------------------------------------------------------------------
    # Abstract interface (subclasses implement)
    # ------------------------------------------------------------------
    def is_configured(self) -> bool:
        """Return True if this connector has valid credentials."""
        raise NotImplementedError

    def list_actions(self) -> list[ConnectorAction]:
        """Return all actions this connector can perform."""
        raise NotImplementedError

    def execute_action(self, action_name: str, params: dict[str, Any]) -> ActionResult:
        """Execute an action, applying Allow/Deny for risky operations."""
        raise NotImplementedError

    def configure(self, credentials: dict[str, str]) -> bool:
        """Store credentials and mark configured."""
        raise NotImplementedError

    def deconfigure(self) -> bool:
        """Remove credentials and mark not configured."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Status (safe for display — never exposes credential values)
    # ------------------------------------------------------------------
    def status_row(self) -> dict[str, Any]:
        """Return a safe status summary for /connectors or UI display."""
        is_cfg = self.is_configured()
        return {
            "name": self.name,
            "display_name": self.display_name,
            "configured": is_cfg,
            "status": "configured" if is_cfg else "not_configured",
            "last_error": self.last_error,
            "actions": [a.name for a in self.list_actions()],
            "read_only_actions": sorted(self._config.read_only_actions),
        }

    # ------------------------------------------------------------------
    # Protected helpers
    # ------------------------------------------------------------------
    def _set_error(self, message: str) -> None:
        self._last_error = message
        self._status = ConnectorStatus.ERROR

    # ------------------------------------------------------------------
    # Web comms gating (outbound HTTP)
    # ------------------------------------------------------------------
    def _check_web_comms(self, url: str) -> None:
        """Reject outbound calls when web_comms is disabled or host is not allowlisted."""
        if not config.is_web_comms_enabled():
            raise URLPolicyError(
                f"web_comms is disabled — outbound call to {url} blocked. "
                "Set CELINA_WEB_COMMS_ENABLED=1 and configure allowed_hosts in neural-schema.json.",
            )
        validate_web_comms_host(url)

    def _clear_error(self) -> None:
        self._last_error = None
        if self._status == ConnectorStatus.ERROR:
            self._status = ConnectorStatus.CONFIGURED if self.is_configured() else ConnectorStatus.NOT_CONFIGURED

    def _with_approval(self, action_name: str, params: dict[str, Any], fn: Callable[[], ActionResult]) -> ActionResult:
        """Run an action through the Allow/Deny gate if it requires approval."""
        action = next((a for a in self.list_actions() if a.name == action_name), None)
        risk_level = action.risk_level if action else RiskLevel.SECURITY_SENSITIVE

        if not self._gate.requires_approval(risk_level):
            result = fn()
            if result.success and risk_level != RiskLevel.READ_ONLY:
                self._record_execution(action_name, params, result)
            return result

        approved = self._gate.request_approval(action_name, self.name, risk_level, params)
        if not approved:
            return ActionResult(
                success=False,
                error="Denied by user (Allow/Deny gate)",
                action=action_name,
                connector=self.name,
                requires_approval=True,
                approved=False,
            )

        result = fn()
        result.approved = True
        result.requires_approval = True
        if result.success and risk_level != RiskLevel.READ_ONLY:
            self._record_execution(action_name, params, result)
        return result

    def _record_execution(self, action_name: str, params: dict[str, Any], result: ActionResult) -> None:
        """Log a mutating connector execution to the evidence ledger."""
        try:
            evidence.EvidenceLedger().record_execution(
                tool_name=f"connector.{self.name}.{action_name}",
                arguments=params,
                result_text=json.dumps(result.data) if result.data else str(result.error),
                tier="tier2",
                policy_decision="allow" if result.success else "deny",
                event_type="connector_execution",
                actor_identity=str(config.HOME),
            )
        except Exception:  # noqa: S110  # Intentional: catch-and-swallow for optional dependency; failure is non-fatal
            pass  # Evidence logging is best-effort; never break the connector flow

    # ------------------------------------------------------------------
    # Bounded HTTP helper (no exponential backoff/jitter)
    # ------------------------------------------------------------------
    class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        """Redirect handler that raises on any redirect — SSRF defense."""

        def redirect_request(  # type: ignore[no-untyped-def]  # overrides urllib.request.HTTPRedirectHandler.redirect_request; complex stdlib types
            self, req, fp, code, msg, headers, newurl,
        ):
            raise URLPolicyError(f"HTTP redirect to {newurl} blocked (redirects not allowed).")

    def _http_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
        timeout: int = 10,
        max_bytes: int = 2 * 1024 * 1024,
        allow_redirects: bool = False,
    ) -> tuple[int, dict[str, Any] | None, str | None]:
        """Execute a bounded HTTP request with SSRF, redirect, and size controls.

        Returns (status_code, parsed_json_or_None, error_detail_or_None).

        Security properties:
        - Redirects are rejected by default (SSRF via 30x hop).
        - Response body is capped at max_bytes (memory DoS defense).
        - Mutating methods (POST/PUT/PATCH/DELETE) are never retried.
        - Idempotent methods (GET/HEAD) retry at most 3x on 429/5xx, no sleep.
        - HTTP error bodies are captured and returned as error_detail.
        """
        self._check_web_comms(url)

        handlers: list[urllib.request.BaseHandler] = []
        if not allow_redirects:
            handlers.append(_NoRedirectHandler())
        opener = urllib.request.build_opener(*handlers)

        data: bytes | None = None
        req_headers = dict(headers)
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)  # noqa: S310  # URL validated by check_web_comms/validate_public_url before this call; only https:// allowed

        is_mutating = method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        max_attempts = 1 if is_mutating else 3

        last_status = 0
        last_error: str | None = None
        for attempt in range(max_attempts):
            try:
                with opener.open(req, timeout=timeout) as resp:
                    raw = resp.read(max_bytes + 1)
                    if len(raw) > max_bytes:
                        return resp.status, None, f"Response body exceeds {max_bytes} bytes"
                    body = raw.decode("utf-8", errors="replace")
                    try:
                        parsed = json.loads(body) if body else None
                    except json.JSONDecodeError:
                        parsed = None
                    return resp.status, parsed, None
            except urllib.error.HTTPError as exc:
                last_status = exc.code
                try:
                    err_body = exc.read().decode("utf-8", errors="replace")
                    last_error = err_body[:500] if err_body else None
                except Exception:
                    last_error = None
                if not is_mutating and exc.code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                    # Bounded retry: no sleep, no jitter, just retry immediately
                    continue
                return exc.code, None, last_error
            except URLPolicyError:
                raise
            except Exception:
                if attempt < max_attempts - 1 and not is_mutating:
                    continue
                return 0, None, last_error
        return last_status, None, last_error

    def _http_get(
        self,
        url: str,
        headers: dict[str, str],
        timeout: int = 10,
        max_bytes: int = 2 * 1024 * 1024,
        allow_redirects: bool = False,
    ) -> tuple[int, dict[str, Any] | None]:
        """Simple GET with bounded retry on 429/5xx (max 3 attempts, no sleep on 429).

        Redirects are blocked; response body is capped at max_bytes.
        Returns (status_code, parsed_json_or_None).
        """
        status, parsed, _error = self._http_request(
            "GET", url, headers, timeout=timeout, max_bytes=max_bytes, allow_redirects=allow_redirects,
        )
        return status, parsed

    def _http_post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int = 10,
        max_bytes: int = 2 * 1024 * 1024,
        allow_redirects: bool = False,
    ) -> tuple[int, dict[str, Any] | None]:
        """Simple POST — never retried (mutating). Redirects blocked; body capped.

        Returns (status_code, parsed_json_or_None).
        """
        status, parsed, _error = self._http_request(
            "POST", url, headers, payload=payload, timeout=timeout,
            max_bytes=max_bytes, allow_redirects=allow_redirects,
        )
        return status, parsed
