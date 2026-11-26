"""
Celina AI (Celly) - Gmail Connector (Update 23)

Adapted from MausBot concepts (Apache-2.0, github.com/milind-soni/OpenMausBot)
to Python. Connects to Gmail via the Gmail API using OAuth 2.0 credentials.

Gmail API: https://gmail.googleapis.com
Docs: https://developers.google.com/gmail/api

Hard refusals:
- Not in Gear 0 dispatch chain
- No webhook server
- No exponential backoff/jitter (bounded retry only)
"""

from __future__ import annotations

from typing import Any, Optional

from celina_pkg.approval import RiskLevel
from .base import (
    BaseConnector,
    ConnectorConfig,
    ConnectorStatus,
    ConnectorAction,
    ActionResult,
)

GMAIL_API_URL = "https://gmail.googleapis.com/gmail/v1"

GMAIL_READ_ONLY_ACTIONS: frozenset[str] = frozenset({
    "list_messages",
    "get_message",
    "list_labels",
    "get_profile",
})

GMAIL_ACTIONS: dict[str, ConnectorAction] = {
    "list_messages": ConnectorAction(
        name="list_messages",
        description="List Gmail messages with optional query filter",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query (e.g. 'is:unread from:boss')"},
                "max_results": {"type": "integer", "description": "Max results (1-500)", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_message": ConnectorAction(
        name="get_message",
        description="Get a single Gmail message by ID",
        parameters={
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID"},
            },
            "required": ["message_id"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "list_labels": ConnectorAction(
        name="list_labels",
        description="List Gmail labels",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_profile": ConnectorAction(
        name="get_profile",
        description="Get the authenticated user's Gmail profile",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.READ_ONLY,
    ),
    "send_message": ConnectorAction(
        name="send_message",
        description="Send an email via Gmail",
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body (plain text)"},
            },
            "required": ["to", "subject", "body"],
        },
        risk_level=RiskLevel.MESSAGE_SEND,
    ),
}


class GmailConnector(BaseConnector):
    """Gmail connector — Gmail API client with Allow/Deny gate."""

    def __init__(self, approval_cb=None):
        cfg = ConnectorConfig(
            name="gmail",
            display_name="Gmail",
            oauth_authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            oauth_token_url="https://oauth2.googleapis.com/token",
            oauth_scopes=(
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
            ),
            oauth_client_id_env="CELINA_GMAIL_CLIENT_ID",
            oauth_client_secret_env="CELINA_GMAIL_CLIENT_SECRET",
            api_base_url=GMAIL_API_URL,
            action_risk_map={name: a.risk_level for name, a in GMAIL_ACTIONS.items()},
            read_only_actions=GMAIL_READ_ONLY_ACTIONS,
        )
        super().__init__(cfg, approval_cb)
        self._access_token: Optional[str] = None

    def is_configured(self) -> bool:
        creds = self._load_credentials()
        if creds is None:
            return False
        self._access_token = creds.get("access_token", "")
        return bool(self._access_token and self._access_token != "PASTE_YOUR_TOKEN_HERE")

    def configure(self, credentials: dict[str, str]) -> bool:
        token = credentials.get("access_token", "").strip()
        if not token:
            self._set_error("Empty access token")
            return False
        self._access_token = token
        try:
            self._save_credentials({"access_token": token})
            self._clear_error()
            self._status = ConnectorStatus.CONFIGURED
            return True
        except Exception as exc:
            self._set_error(f"Failed to save credentials: {exc}")
            return False

    def deconfigure(self) -> bool:
        try:
            self._clear_credentials()
            self._access_token = None
            self._status = ConnectorStatus.NOT_CONFIGURED
            self._clear_error()
            return True
        except Exception as exc:
            self._set_error(f"Failed to clear credentials: {exc}")
            return False

    def list_actions(self) -> list[ConnectorAction]:
        return list(GMAIL_ACTIONS.values())

    def execute_action(self, action_name: str, params: dict[str, Any]) -> ActionResult:
        if not self.is_configured():
            return ActionResult(
                success=False,
                error="Gmail connector not configured. Set a Gmail access token first.",
                action=action_name,
                connector=self.name,
            )

        action = next((a for a in self.list_actions() if a.name == action_name), None)
        if action is None:
            return ActionResult(
                success=False,
                error=f"Unknown action: {action_name}",
                action=action_name,
                connector=self.name,
            )

        def _run() -> ActionResult:
            try:
                if action_name == "list_messages":
                    return self._list_messages(params)
                elif action_name == "get_message":
                    return self._get_message(params)
                elif action_name == "list_labels":
                    return self._list_labels(params)
                elif action_name == "get_profile":
                    return self._get_profile(params)
                elif action_name == "send_message":
                    return self._send_message(params)
                else:
                    return ActionResult(
                        success=False,
                        error=f"Action not implemented: {action_name}",
                        action=action_name,
                        connector=self.name,
                    )
            except Exception as exc:
                return ActionResult(
                    success=False,
                    error=f"Gmail API error: {exc}",
                    action=action_name,
                    connector=self.name,
                )

        return self._with_approval(action_name, params, _run)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[dict[str, str]] = None) -> dict[str, Any]:
        import urllib.parse
        url = f"{GMAIL_API_URL}{path}"
        if params:
            qs = urllib.parse.urlencode(params)
            url = f"{url}?{qs}"
        status, body = self._http_get(url, self._headers(), timeout=15)
        if status != 200 or body is None:
            raise RuntimeError(f"Gmail API returned HTTP {status} for {path}")
        return body

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{GMAIL_API_URL}{path}"
        status, body = self._http_post(url, self._headers(), payload, timeout=15)
        if status not in (200, 201, 202) or body is None:
            raise RuntimeError(f"Gmail API returned HTTP {status} for POST {path}")
        return body

    def _list_messages(self, params: dict[str, Any]) -> ActionResult:
        query = params.get("query", "")
        max_results = min(int(params.get("max_results", 50)), 500)
        body = self._get("/users/me/messages", {"q": query, "maxResults": str(max_results)})
        messages = body.get("messages", [])
        return ActionResult(
            success=True,
            data={"messages": messages, "count": len(messages)},
            action="list_messages",
            connector=self.name,
        )

    def _get_message(self, params: dict[str, Any]) -> ActionResult:
        message_id = params["message_id"]
        body = self._get(f"/users/me/messages/{message_id}")
        return ActionResult(success=True, data={"message": body}, action="get_message", connector=self.name)

    def _list_labels(self, params: dict[str, Any]) -> ActionResult:
        body = self._get("/users/me/labels")
        labels = body.get("labels", [])
        return ActionResult(
            success=True,
            data={"labels": labels, "count": len(labels)},
            action="list_labels",
            connector=self.name,
        )

    def _get_profile(self, params: dict[str, Any]) -> ActionResult:
        body = self._get("/users/me/profile")
        return ActionResult(success=True, data={"profile": body}, action="get_profile", connector=self.name)

    def _send_message(self, params: dict[str, Any]) -> ActionResult:
        import base64

        to = params["to"]
        subject = params["subject"]
        body_text = params["body"]

        # Build RFC 2822 message and base64url encode it
        raw_msg = (
            f"To: {to}\r\n"
            f"Subject: {subject}\r\n"
            f"\r\n"
            f"{body_text}"
        )
        raw_bytes = raw_msg.encode("utf-8")
        raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("utf-8").rstrip("=")

        payload = {"raw": raw_b64}
        result = self._post("/users/me/messages/send", payload)
        return ActionResult(
            success=True,
            data={"sent": result},
            action="send_message",
            connector=self.name,
        )
