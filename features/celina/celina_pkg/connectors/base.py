"""
Celina AI (Celly) - Connector Base Framework (Update 23)

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

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from celina_pkg import config
from celina_pkg.approval import RiskLevel


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
    oauth_authorize_url: Optional[str] = None
    oauth_token_url: Optional[str] = None
    oauth_scopes: tuple[str, ...] = ()
    oauth_client_id_env: Optional[str] = None
    oauth_client_secret_env: Optional[str] = None
    api_base_url: Optional[str] = None
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
    error: Optional[str] = None
    action: str = ""
    connector: str = ""
    requires_approval: bool = False
    approved: bool = False


class AllowDenyGate:
    """
    MausBot-style permission broker — Allow/Deny cards for risky connector actions.

    Read-only actions pass through automatically. Actions with risk >= LOCAL_MUTATION
    require explicit approval via the approval_cb callback.
    """

    def __init__(self, approval_cb: Optional[Callable[[str, str], bool]] = None):
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
        """
        Show an Allow/Deny card for a risky action.
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

    def resolve_pending(self, action_id: str, approved: bool) -> Optional[dict[str, Any]]:
        """Resolve a pending approval. Returns the action if approved, None otherwise."""
        entry = self._pending.pop(action_id, None)
        if entry is None:
            return None
        return entry["action"] if approved else None


class BaseConnector:
    """
    Base class for all Celina connectors.

    Subclasses must implement:
    - is_configured() -> bool
    - list_actions() -> list[ConnectorAction]
    - execute_action(action_name, params) -> ActionResult
    - configure(credentials) -> bool
    """

    def __init__(self, config: ConnectorConfig, approval_cb: Optional[Callable[[str, str], bool]] = None):
        self._config = config
        self._gate = AllowDenyGate(approval_cb)
        self._status = ConnectorStatus.NOT_CONFIGURED
        self._last_error: Optional[str] = None
        self._credential_path: Optional[Path] = None

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
    def last_error(self) -> Optional[str]:
        return self._last_error

    # ------------------------------------------------------------------
    # Credential storage (local-first, ~/.celina/storage/connectors/)
    # ------------------------------------------------------------------
    def _credential_dir(self) -> Path:
        base = Path.home() / ".celina" / "storage" / "connectors"
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

    def _load_credentials(self) -> Optional[dict[str, str]]:
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
        """Persist credentials with mode 0600."""
        cf = self._credential_file()
        cf.write_text(json.dumps(credentials, indent=2), encoding="utf-8")
        try:
            os.chmod(cf, 0o600)
        except OSError:
            pass

    def _clear_credentials(self) -> None:
        """Remove stored credentials."""
        try:
            cf = self._credential_file()
            if cf.exists():
                cf.unlink()
        except Exception:
            pass

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

    def _clear_error(self) -> None:
        self._last_error = None
        if self._status == ConnectorStatus.ERROR:
            self._status = ConnectorStatus.CONFIGURED if self.is_configured() else ConnectorStatus.NOT_CONFIGURED

    def _with_approval(self, action_name: str, params: dict[str, Any], fn: Callable[[], ActionResult]) -> ActionResult:
        """Run an action through the Allow/Deny gate if it requires approval."""
        action = next((a for a in self.list_actions() if a.name == action_name), None)
        risk_level = action.risk_level if action else RiskLevel.SECURITY_SENSITIVE

        if not self._gate.requires_approval(risk_level):
            return fn()

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
        return result

    # ------------------------------------------------------------------
    # Bounded HTTP helper (no exponential backoff/jitter)
    # ------------------------------------------------------------------
    def _http_get(self, url: str, headers: dict[str, str], timeout: int = 10) -> tuple[int, Optional[dict[str, Any]]]:
        """Simple GET with bounded retry on 429/5xx (max 3 attempts, no sleep on 429)."""
        import urllib.request
        import urllib.error

        req = urllib.request.Request(url, headers=headers)
        last_status = 0
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8")
                    return resp.status, json.loads(body) if body else None
            except urllib.error.HTTPError as exc:
                last_status = exc.code
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    # Bounded retry: no sleep, no jitter, just retry immediately
                    continue
                try:
                    body = exc.read().decode("utf-8")
                    return exc.code, json.loads(body) if body else None
                except Exception:
                    return exc.code, None
            except Exception:
                if attempt < 2:
                    continue
                return 0, None
        return last_status, None

    def _http_post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int = 10,
    ) -> tuple[int, Optional[dict[str, Any]]]:
        """Simple POST with bounded retry on 429/5xx (max 3 attempts, no sleep on 429)."""
        import urllib.request
        import urllib.error

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"})
        last_status = 0
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8")
                    return resp.status, json.loads(body) if body else None
            except urllib.error.HTTPError as exc:
                last_status = exc.code
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    continue
                try:
                    body = exc.read().decode("utf-8")
                    return exc.code, json.loads(body) if body else None
                except Exception:
                    return exc.code, None
            except Exception:
                if attempt < 2:
                    continue
                return 0, None
        return last_status, None
