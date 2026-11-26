"""
Celina AI (Celly) - GitHub Connector (Update 23)

Adapted from MausBot concepts (Apache-2.0, github.com/milind-soni/OpenMausBot)
to Python. Connects to GitHub via its REST API using a personal access token.

GitHub API: https://api.github.com
Docs: https://docs.github.com/en/rest

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

GITHUB_API_URL = "https://api.github.com"

GITHUB_READ_ONLY_ACTIONS: frozenset[str] = frozenset({
    "list_repos",
    "get_repo",
    "list_issues",
    "get_issue",
    "list_pull_requests",
    "get_user",
})

GITHUB_ACTIONS: dict[str, ConnectorAction] = {
    "list_repos": ConnectorAction(
        name="list_repos",
        description="List GitHub repositories for the authenticated user",
        parameters={
            "type": "object",
            "properties": {
                "visibility": {"type": "string", "description": "all, public, or private", "default": "all"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_repo": ConnectorAction(
        name="get_repo",
        description="Get a single GitHub repository",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "list_issues": ConnectorAction(
        name="list_issues",
        description="List issues in a GitHub repository",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "description": "open, closed, or all", "default": "open"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
            "required": ["owner", "repo"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_issue": ConnectorAction(
        name="get_issue",
        description="Get a single GitHub issue",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number"},
            },
            "required": ["owner", "repo", "issue_number"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "create_issue": ConnectorAction(
        name="create_issue",
        description="Create a new GitHub issue",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body (Markdown)"},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "Label names"},
            },
            "required": ["owner", "repo", "title"],
        },
        risk_level=RiskLevel.FILE_WRITE,
    ),
    "list_pull_requests": ConnectorAction(
        name="list_pull_requests",
        description="List pull requests in a GitHub repository",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "description": "open, closed, or all", "default": "open"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
            "required": ["owner", "repo"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_user": ConnectorAction(
        name="get_user",
        description="Get a GitHub user profile",
        parameters={
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "GitHub username"},
            },
            "required": ["username"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
}


class GitHubConnector(BaseConnector):
    """GitHub connector — REST API client with Allow/Deny gate."""

    def __init__(self, approval_cb=None):
        cfg = ConnectorConfig(
            name="github",
            display_name="GitHub",
            oauth_authorize_url=None,
            oauth_token_url=None,
            oauth_scopes=(),
            oauth_client_id_env=None,
            oauth_client_secret_env=None,
            api_base_url=GITHUB_API_URL,
            action_risk_map={name: a.risk_level for name, a in GITHUB_ACTIONS.items()},
            read_only_actions=GITHUB_READ_ONLY_ACTIONS,
        )
        super().__init__(cfg, approval_cb)
        self._token: Optional[str] = None

    def is_configured(self) -> bool:
        creds = self._load_credentials()
        if creds is None:
            return False
        self._token = creds.get("token", "")
        return bool(self._token and self._token != "PASTE_YOUR_TOKEN_HERE")

    def configure(self, credentials: dict[str, str]) -> bool:
        token = credentials.get("token", "").strip()
        if not token:
            self._set_error("Empty token")
            return False
        self._token = token
        try:
            self._save_credentials({"token": token})
            self._clear_error()
            self._status = ConnectorStatus.CONFIGURED
            return True
        except Exception as exc:
            self._set_error(f"Failed to save credentials: {exc}")
            return False

    def deconfigure(self) -> bool:
        try:
            self._clear_credentials()
            self._token = None
            self._status = ConnectorStatus.NOT_CONFIGURED
            self._clear_error()
            return True
        except Exception as exc:
            self._set_error(f"Failed to clear credentials: {exc}")
            return False

    def list_actions(self) -> list[ConnectorAction]:
        return list(GITHUB_ACTIONS.values())

    def execute_action(self, action_name: str, params: dict[str, Any]) -> ActionResult:
        if not self.is_configured():
            return ActionResult(
                success=False,
                error="GitHub connector not configured. Set a GitHub personal access token first.",
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
                if action_name == "list_repos":
                    return self._list_repos(params)
                elif action_name == "get_repo":
                    return self._get_repo(params)
                elif action_name == "list_issues":
                    return self._list_issues(params)
                elif action_name == "get_issue":
                    return self._get_issue(params)
                elif action_name == "create_issue":
                    return self._create_issue(params)
                elif action_name == "list_pull_requests":
                    return self._list_pull_requests(params)
                elif action_name == "get_user":
                    return self._get_user(params)
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
                    error=f"GitHub API error: {exc}",
                    action=action_name,
                    connector=self.name,
                )

        return self._with_approval(action_name, params, _run)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        }

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{GITHUB_API_URL}{path}"
        status, body = self._http_get(url, self._headers(), timeout=15)
        if status != 200 or body is None:
            raise RuntimeError(f"GitHub API returned HTTP {status} for {path}")
        return body

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{GITHUB_API_URL}{path}"
        status, body = self._http_post(url, self._headers(), payload, timeout=15)
        if status not in (200, 201) or body is None:
            raise RuntimeError(f"GitHub API returned HTTP {status} for POST {path}")
        return body

    def _list_repos(self, params: dict[str, Any]) -> ActionResult:
        visibility = params.get("visibility", "all")
        limit = min(int(params.get("limit", 50)), 100)
        body = self._get(f"/user/repos?visibility={visibility}&per_page={limit}&sort=updated")
        repos = [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "full_name": r.get("full_name"),
                "private": r.get("private"),
                "html_url": r.get("html_url"),
                "description": r.get("description"),
                "updated_at": r.get("updated_at"),
            }
            for r in body
        ]
        return ActionResult(success=True, data={"repos": repos, "count": len(repos)}, action="list_repos", connector=self.name)

    def _get_repo(self, params: dict[str, Any]) -> ActionResult:
        owner = params["owner"]
        repo = params["repo"]
        body = self._get(f"/repos/{owner}/{repo}")
        return ActionResult(success=True, data={"repo": body}, action="get_repo", connector=self.name)

    def _list_issues(self, params: dict[str, Any]) -> ActionResult:
        owner = params["owner"]
        repo = params["repo"]
        state = params.get("state", "open")
        limit = min(int(params.get("limit", 50)), 100)
        body = self._get(f"/repos/{owner}/{repo}/issues?state={state}&per_page={limit}")
        issues = [
            {
                "number": i.get("number"),
                "title": i.get("title"),
                "state": i.get("state"),
                "html_url": i.get("html_url"),
                "created_at": i.get("created_at"),
                "updated_at": i.get("updated_at"),
            }
            for i in body
        ]
        return ActionResult(success=True, data={"issues": issues, "count": len(issues)}, action="list_issues", connector=self.name)

    def _get_issue(self, params: dict[str, Any]) -> ActionResult:
        owner = params["owner"]
        repo = params["repo"]
        issue_number = params["issue_number"]
        body = self._get(f"/repos/{owner}/{repo}/issues/{issue_number}")
        return ActionResult(success=True, data={"issue": body}, action="get_issue", connector=self.name)

    def _create_issue(self, params: dict[str, Any]) -> ActionResult:
        owner = params["owner"]
        repo = params["repo"]
        payload = {
            "title": params["title"],
        }
        if params.get("body"):
            payload["body"] = params["body"]
        if params.get("labels"):
            payload["labels"] = params["labels"]
        body = self._post(f"/repos/{owner}/{repo}/issues", payload)
        return ActionResult(success=True, data={"issue": body}, action="create_issue", connector=self.name)

    def _list_pull_requests(self, params: dict[str, Any]) -> ActionResult:
        owner = params["owner"]
        repo = params["repo"]
        state = params.get("state", "open")
        limit = min(int(params.get("limit", 50)), 100)
        body = self._get(f"/repos/{owner}/{repo}/pulls?state={state}&per_page={limit}")
        prs = [
            {
                "number": p.get("number"),
                "title": p.get("title"),
                "state": p.get("state"),
                "html_url": p.get("html_url"),
                "created_at": p.get("created_at"),
            }
            for p in body
        ]
        return ActionResult(success=True, data={"pull_requests": prs, "count": len(prs)}, action="list_pull_requests", connector=self.name)

    def _get_user(self, params: dict[str, Any]) -> ActionResult:
        username = params["username"]
        body = self._get(f"/users/{username}")
        return ActionResult(success=True, data={"user": body}, action="get_user", connector=self.name)
