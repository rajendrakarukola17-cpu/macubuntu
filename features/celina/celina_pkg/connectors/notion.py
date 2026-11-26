"""Celina AI (Celly) - Notion Connector (Update 23)

Adapted from MausBot concepts (Apache-2.0, github.com/milind-soni/OpenMausBot)
to Python. Connects to Notion via the Notion API using an integration token.

Notion API: https://api.notion.com
Docs: https://developers.notion.com

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

NOTION_API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

NOTION_READ_ONLY_ACTIONS: frozenset[str] = frozenset({
    "search",
    "get_page",
    "get_database",
    "query_database",
    "list_users",
})

NOTION_ACTIONS: dict[str, ConnectorAction] = {
    "search": ConnectorAction(
        name="search",
        description="Search Notion pages and databases",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_page": ConnectorAction(
        name="get_page",
        description="Get a Notion page by ID",
        parameters={
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Notion page ID"},
            },
            "required": ["page_id"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_database": ConnectorAction(
        name="get_database",
        description="Get a Notion database by ID",
        parameters={
            "type": "object",
            "properties": {
                "database_id": {"type": "string", "description": "Notion database ID"},
            },
            "required": ["database_id"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "query_database": ConnectorAction(
        name="query_database",
        description="Query a Notion database",
        parameters={
            "type": "object",
            "properties": {
                "database_id": {"type": "string", "description": "Notion database ID"},
                "filter": {"type": "object", "description": "Notion filter object"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
            "required": ["database_id"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "create_page": ConnectorAction(
        name="create_page",
        description="Create a new Notion page",
        parameters={
            "type": "object",
            "properties": {
                "parent_id": {"type": "string", "description": "Parent page or database ID"},
                "parent_type": {"type": "string", "description": "page_id or database_id", "default": "page_id"},
                "properties": {"type": "object", "description": "Notion properties object"},
                "children": {"type": "array", "items": {"type": "object"}, "description": "Page children blocks"},
            },
            "required": ["parent_id", "properties"],
        },
        risk_level=RiskLevel.FILE_WRITE,
    ),
    "list_users": ConnectorAction(
        name="list_users",
        description="List Notion users in the workspace",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
}


class NotionConnector(BaseConnector):
    """Notion connector — Notion API client with Allow/Deny gate."""

    def __init__(self, approval_cb=None):
        cfg = ConnectorConfig(
            name="notion",
            display_name="Notion",
            oauth_authorize_url="https://api.notion.com/v1/oauth/authorize",
            oauth_token_url="https://api.notion.com/v1/oauth/token",
            oauth_scopes=(),
            oauth_client_id_env="CELINA_NOTION_CLIENT_ID",
            oauth_client_secret_env="CELINA_NOTION_CLIENT_SECRET",
            api_base_url=NOTION_API_URL,
            action_risk_map={name: a.risk_level for name, a in NOTION_ACTIONS.items()},
            read_only_actions=NOTION_READ_ONLY_ACTIONS,
        )
        super().__init__(cfg, approval_cb)
        self._integration_token: str | None = None

    def is_configured(self) -> bool:
        creds = self._load_credentials()
        if creds is None:
            return False
        self._integration_token = creds.get("integration_token", "")
        return bool(self._integration_token and self._integration_token != "PASTE_YOUR_TOKEN_HERE")

    def configure(self, credentials: dict[str, str]) -> bool:
        token = credentials.get("integration_token", "").strip()
        if not token:
            self._set_error("Empty integration token")
            return False
        self._integration_token = token
        try:
            self._save_credentials({"integration_token": token})
            self._clear_error()
            self._status = ConnectorStatus.CONFIGURED
            return True
        except Exception as exc:
            self._set_error(f"Failed to save credentials: {exc}")
            return False

    def deconfigure(self) -> bool:
        try:
            self._clear_credentials()
            self._integration_token = None
            self._status = ConnectorStatus.NOT_CONFIGURED
            self._clear_error()
            return True
        except Exception as exc:
            self._set_error(f"Failed to clear credentials: {exc}")
            return False

    def list_actions(self) -> list[ConnectorAction]:
        return list(NOTION_ACTIONS.values())

    def execute_action(self, action_name: str, params: dict[str, Any]) -> ActionResult:
        if not self.is_configured():
            return ActionResult(
                success=False,
                error="Notion connector not configured. Set a Notion integration token first.",
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
                if action_name == "search":
                    return self._search(params)
                if action_name == "get_page":
                    return self._get_page(params)
                if action_name == "get_database":
                    return self._get_database(params)
                if action_name == "query_database":
                    return self._query_database(params)
                if action_name == "create_page":
                    return self._create_page(params)
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
                    error=f"Notion API error: {exc}",
                    action=action_name,
                    connector=self.name,
                )

        return self._with_approval(action_name, params, _run)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._integration_token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{NOTION_API_URL}{path}"
        status, body = self._http_post(url, self._headers(), payload, timeout=15)
        if status != 200 or body is None:
            raise RuntimeError(f"Notion API returned HTTP {status} for POST {path}")
        return body

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{NOTION_API_URL}{path}"
        status, body = self._http_get(url, self._headers(), timeout=15)
        if status != 200 or body is None:
            raise RuntimeError(f"Notion API returned HTTP {status} for GET {path}")
        return body

    def _search(self, params: dict[str, Any]) -> ActionResult:
        query = params.get("query", "")
        limit = min(int(params.get("limit", 50)), 100)
        payload: dict[str, Any] = {"page_size": limit}
        if query:
            payload["query"] = query
        body = self._post("/search", payload)
        results = body.get("results", [])
        return ActionResult(
            success=True,
            data={"results": results, "count": len(results)},
            action="search",
            connector=self.name,
        )

    def _get_page(self, params: dict[str, Any]) -> ActionResult:
        page_id = params["page_id"]
        body = self._get(self._build_path("pages", page_id))
        return ActionResult(success=True, data={"page": body}, action="get_page", connector=self.name)

    def _get_database(self, params: dict[str, Any]) -> ActionResult:
        database_id = params["database_id"]
        body = self._get(self._build_path("databases", database_id))
        return ActionResult(success=True, data={"database": body}, action="get_database", connector=self.name)

    def _query_database(self, params: dict[str, Any]) -> ActionResult:
        database_id = params["database_id"]
        limit = min(int(params.get("limit", 50)), 100)
        payload: dict[str, Any] = {"page_size": limit}
        if params.get("filter"):
            payload["filter"] = params["filter"]
        body = self._post(self._build_path("databases", database_id, "query"), payload)
        results = body.get("results", [])
        return ActionResult(
            success=True,
            data={"results": results, "count": len(results)},
            action="query_database",
            connector=self.name,
        )

    def _create_page(self, params: dict[str, Any]) -> ActionResult:
        parent_id = params["parent_id"]
        parent_type = params.get("parent_type", "page_id")
        properties = params["properties"]
        children = params.get("children", [])

        parent_key = "page_id" if parent_type == "page_id" else "database_id"
        payload: dict[str, Any] = {
            "parent": {parent_key: parent_id},
            "properties": properties,
        }
        if children:
            payload["children"] = children

        body = self._post("/pages", payload)
        return ActionResult(success=True, data={"page": body}, action="create_page", connector=self.name)

    def _list_users(self, params: dict[str, Any]) -> ActionResult:
        limit = min(int(params.get("limit", 50)), 100)
        import urllib.parse
        url = f"{NOTION_API_URL}/users?{urllib.parse.urlencode({'page_size': str(limit)})}"
        status, body = self._http_get(url, self._headers(), timeout=15)
        if status != 200 or body is None:
            raise RuntimeError(f"Notion API returned HTTP {status} for GET /users")
        users = body.get("results", [])
        return ActionResult(
            success=True,
            data={"users": users, "count": len(users)},
            action="list_users",
            connector=self.name,
        )
