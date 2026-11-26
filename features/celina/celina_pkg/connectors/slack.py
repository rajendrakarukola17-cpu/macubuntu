"""Celina AI (Celly) - Slack Connector (Update 23)

Adapted from MausBot concepts (Apache-2.0, github.com/milind-soni/OpenMausBot)
to Python. Connects to Slack via the Slack Web API using a bot token.

Slack API: https://slack.com/api
Docs: https://api.slack.com/methods

Hard refusals:
- Not in Gear 0 dispatch chain
- No webhook server
- No exponential backoff/jitter (bounded retry only)
"""

from __future__ import annotations

from typing import Any

from celina_pkg.approval import RiskLevel

from .base import (
    ActionResult,
    BaseConnector,
    ConnectorAction,
    ConnectorConfig,
    ConnectorStatus,
)

SLACK_API_URL = "https://slack.com/api"

SLACK_READ_ONLY_ACTIONS: frozenset[str] = frozenset({
    "list_channels",
    "get_channel",
    "list_messages",
    "get_user",
    "list_users",
})

SLACK_ACTIONS: dict[str, ConnectorAction] = {
    "list_channels": ConnectorAction(
        name="list_channels",
        description="List Slack channels",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_channel": ConnectorAction(
        name="get_channel",
        description="Get information about a Slack channel",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID or name"},
            },
            "required": ["channel"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "list_messages": ConnectorAction(
        name="list_messages",
        description="List messages from a Slack channel",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
                "oldest": {"type": "string", "description": "Start of time range (Unix timestamp)"},
                "latest": {"type": "string", "description": "End of time range (Unix timestamp)"},
            },
            "required": ["channel"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "send_message": ConnectorAction(
        name="send_message",
        description="Send a message to a Slack channel",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID or name"},
                "text": {"type": "string", "description": "Message text"},
            },
            "required": ["channel", "text"],
        },
        risk_level=RiskLevel.MESSAGE_SEND,
    ),
    "get_user": ConnectorAction(
        name="get_user",
        description="Get information about a Slack user",
        parameters={
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "User ID"},
            },
            "required": ["user"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "list_users": ConnectorAction(
        name="list_users",
        description="List Slack users in the workspace",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
}


class SlackConnector(BaseConnector):
    """Slack connector — Slack Web API client with Allow/Deny gate."""

    def __init__(self, approval_cb=None):
        cfg = ConnectorConfig(
            name="slack",
            display_name="Slack",
            oauth_authorize_url="https://slack.com/oauth/v2/authorize",
            oauth_token_url="https://slack.com/api/oauth.v2.access",
            oauth_scopes=(
                "channels:read",
                "chat:write",
                "groups:read",
                "im:read",
                "users:read",
            ),
            oauth_client_id_env="CELINA_SLACK_CLIENT_ID",
            oauth_client_secret_env="CELINA_SLACK_CLIENT_SECRET",
            api_base_url=SLACK_API_URL,
            action_risk_map={name: a.risk_level for name, a in SLACK_ACTIONS.items()},
            read_only_actions=SLACK_READ_ONLY_ACTIONS,
        )
        super().__init__(cfg, approval_cb)
        self._bot_token: str | None = None

    def is_configured(self) -> bool:
        creds = self._load_credentials()
        if creds is None:
            return False
        self._bot_token = creds.get("bot_token", "")
        return bool(self._bot_token and self._bot_token != "PASTE_YOUR_TOKEN_HERE")

    def configure(self, credentials: dict[str, str]) -> bool:
        token = credentials.get("bot_token", "").strip()
        if not token:
            self._set_error("Empty bot token")
            return False
        self._bot_token = token
        try:
            self._save_credentials({"bot_token": token})
            self._clear_error()
            self._status = ConnectorStatus.CONFIGURED
            return True
        except Exception as exc:
            self._set_error(f"Failed to save credentials: {exc}")
            return False

    def deconfigure(self) -> bool:
        try:
            self._clear_credentials()
            self._bot_token = None
            self._status = ConnectorStatus.NOT_CONFIGURED
            self._clear_error()
            return True
        except Exception as exc:
            self._set_error(f"Failed to clear credentials: {exc}")
            return False

    def list_actions(self) -> list[ConnectorAction]:
        return list(SLACK_ACTIONS.values())

    def execute_action(self, action_name: str, params: dict[str, Any]) -> ActionResult:
        if not self.is_configured():
            return ActionResult(
                success=False,
                error="Slack connector not configured. Set a Slack bot token first.",
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
                if action_name == "list_channels":
                    return self._list_channels(params)
                if action_name == "get_channel":
                    return self._get_channel(params)
                if action_name == "list_messages":
                    return self._list_messages(params)
                if action_name == "send_message":
                    return self._send_message(params)
                if action_name == "get_user":
                    return self._get_user(params)
                if action_name == "list_users":
                    return self._list_users(params)
                return ActionResult(
                    success=False,
                    error=f"Action not implemented: {action_name}",
                    action=action_name,
                    connector=self.name,
                )
            except Exception as exc:
                return ActionResult(
                    success=False,
                    error=f"Slack API error: {exc}",
                    action=action_name,
                    connector=self.name,
                )

        return self._with_approval(action_name, params, _run)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _get(self, path: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        import urllib.parse
        url = f"{SLACK_API_URL}{path}"
        if query:
            qs = urllib.parse.urlencode(query)
            url = f"{url}?{qs}"
        status, body = self._http_get(url, self._headers(), timeout=15)
        if status != 200 or body is None:
            raise RuntimeError(f"Slack API returned HTTP {status} for {path}")
        if not body.get("ok", False):
            raise RuntimeError(f"Slack API error: {body.get('error', 'unknown')}")
        return body

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{SLACK_API_URL}{path}"
        status, body = self._http_post(url, self._headers(), payload, timeout=15)
        if status != 200 or body is None:
            raise RuntimeError(f"Slack API returned HTTP {status} for POST {path}")
        if not body.get("ok", False):
            raise RuntimeError(f"Slack API error: {body.get('error', 'unknown')}")
        return body

    def _list_channels(self, params: dict[str, Any]) -> ActionResult:
        limit = min(int(params.get("limit", 50)), 1000)
        body = self._get("/conversations.list", {"limit": str(limit)})
        channels = body.get("channels", [])
        return ActionResult(
            success=True,
            data={"channels": channels, "count": len(channels)},
            action="list_channels",
            connector=self.name,
        )

    def _get_channel(self, params: dict[str, Any]) -> ActionResult:
        channel = params["channel"]
        body = self._get("/conversations.info", {"channel": channel})
        return ActionResult(success=True, data={"channel": body.get("channel", {})}, action="get_channel", connector=self.name)

    def _list_messages(self, params: dict[str, Any]) -> ActionResult:
        channel = params["channel"]
        limit = min(int(params.get("limit", 50)), 1000)
        query: dict[str, str] = {"channel": channel, "limit": str(limit)}
        if params.get("oldest"):
            query["oldest"] = params["oldest"]
        if params.get("latest"):
            query["latest"] = params["latest"]
        body = self._get("/conversations.history", query)
        messages = body.get("messages", [])
        return ActionResult(
            success=True,
            data={"messages": messages, "count": len(messages)},
            action="list_messages",
            connector=self.name,
        )

    def _send_message(self, params: dict[str, Any]) -> ActionResult:
        channel = params["channel"]
        text = params["text"]
        payload = {"channel": channel, "text": text}
        result = self._post("/chat.postMessage", payload)
        return ActionResult(
            success=True,
            data={"sent": result},
            action="send_message",
            connector=self.name,
        )

    def _get_user(self, params: dict[str, Any]) -> ActionResult:
        user = params["user"]
        body = self._get("/users.info", {"user": user})
        return ActionResult(success=True, data={"user": body.get("user", {})}, action="get_user", connector=self.name)

    def _list_users(self, params: dict[str, Any]) -> ActionResult:
        limit = min(int(params.get("limit", 50)), 1000)
        body = self._get("/users.list", {"limit": str(limit)})
        users = body.get("members", [])
        return ActionResult(
            success=True,
            data={"users": users, "count": len(users)},
            action="list_users",
            connector=self.name,
        )
