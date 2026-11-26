"""Celina AI (Celly) - Linear Connector (Update 23)

Adapted from MausBot concepts (Apache-2.0, github.com/milind-soni/OpenMausBot)
to Python. Connects to Linear via its GraphQL API using a user-provided API key.

Linear API: https://api.linear.app/graphql
Docs: https://developers.linear.app/docs/graphql

Hard refusals:
- Not in Gear 0 dispatch chain
- No webhook server
- No exponential backoff/jitter (bounded retry only)
- Read-only actions never require Allow/Deny; write actions do
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

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

# Read-only actions that never require Allow/Deny
LINEAR_READ_ONLY_ACTIONS: frozenset[str] = frozenset({
    "list_issues",
    "get_issue",
    "list_projects",
    "get_project",
    "list_teams",
    "get_team",
    "list_users",
    "get_user",
    "list_cycles",
})

# All known Linear actions with their risk levels
LINEAR_ACTIONS: dict[str, ConnectorAction] = {
    "list_issues": ConnectorAction(
        name="list_issues",
        description="List Linear issues with optional filters",
        parameters={
            "type": "object",
            "properties": {
                "team_key": {"type": "string", "description": "Team key (e.g. 'ENG')"},
                "query": {"type": "string", "description": "Title substring search"},
                "state_name": {"type": "string", "description": "Workflow state name"},
                "limit": {"type": "integer", "description": "Max results (1-250)", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_issue": ConnectorAction(
        name="get_issue",
        description="Get a single Linear issue by id or identifier",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Issue UUID or identifier (e.g. 'ENG-142')"},
            },
            "required": ["id"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "create_issue": ConnectorAction(
        name="create_issue",
        description="Create a new Linear issue",
        parameters={
            "type": "object",
            "properties": {
                "team_key": {"type": "string", "description": "Team key (e.g. 'ENG')"},
                "title": {"type": "string", "description": "Issue title"},
                "description": {"type": "string", "description": "Issue description (Markdown)"},
                "priority": {"type": "integer", "description": "Priority 0-4 (0=None, 1=Urgent, 2=High, 3=Medium, 4=Low)"},
                "assignee_id": {"type": "string", "description": "User UUID to assign"},
                "state_id": {"type": "string", "description": "Workflow state UUID"},
                "label_ids": {"type": "array", "items": {"type": "string"}, "description": "Label UUIDs"},
                "project_id": {"type": "string", "description": "Project UUID"},
                "cycle_id": {"type": "string", "description": "Cycle UUID"},
                "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
            },
            "required": ["team_key", "title"],
        },
        risk_level=RiskLevel.FILE_WRITE,
    ),
    "update_issue": ConnectorAction(
        name="update_issue",
        description="Update an existing Linear issue",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Issue UUID or identifier"},
                "title": {"type": "string", "description": "New title"},
                "description": {"type": "string", "description": "New description"},
                "priority": {"type": "integer", "description": "Priority 0-4"},
                "state_id": {"type": "string", "description": "Workflow state UUID"},
                "assignee_id": {"type": "string", "description": "User UUID to assign"},
                "label_ids": {"type": "array", "items": {"type": "string"}, "description": "Replace all labels"},
                "added_label_ids": {"type": "array", "items": {"type": "string"}, "description": "Add labels"},
                "removed_label_ids": {"type": "array", "items": {"type": "string"}, "description": "Remove labels"},
                "project_id": {"type": "string", "description": "Project UUID"},
                "cycle_id": {"type": "string", "description": "Cycle UUID"},
                "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
            },
            "required": ["id"],
        },
        risk_level=RiskLevel.FILE_WRITE,
    ),
    "delete_issue": ConnectorAction(
        name="delete_issue",
        description="Delete (soft-delete) a Linear issue",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Issue UUID or identifier"},
                "permanently_delete": {"type": "boolean", "description": "Skip grace period (admins only)"},
            },
            "required": ["id"],
        },
        risk_level=RiskLevel.SECURITY_SENSITIVE,
    ),
    "list_projects": ConnectorAction(
        name="list_projects",
        description="List Linear projects",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name substring filter"},
                "state": {"type": "string", "description": "Project state"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_project": ConnectorAction(
        name="get_project",
        description="Get a single Linear project by id",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Project UUID"},
            },
            "required": ["id"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "list_teams": ConnectorAction(
        name="list_teams",
        description="List Linear teams",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name substring filter"},
                "key": {"type": "string", "description": "Exact team key"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_team": ConnectorAction(
        name="get_team",
        description="Get a single Linear team by id",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Team UUID"},
            },
            "required": ["id"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "list_users": ConnectorAction(
        name="list_users",
        description="List Linear users",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name substring filter"},
                "email": {"type": "string", "description": "Exact email match"},
                "active": {"type": "boolean", "description": "Active users only"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_user": ConnectorAction(
        name="get_user",
        description="Get a single Linear user by id",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "User UUID"},
            },
            "required": ["id"],
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
    "list_cycles": ConnectorAction(
        name="list_cycles",
        description="List Linear cycles (sprints)",
        parameters={
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Team UUID"},
                "is_active": {"type": "boolean", "description": "Active cycles only"},
                "limit": {"type": "integer", "description": "Max results", "default": 50},
            },
        },
        risk_level=RiskLevel.READ_ONLY,
    ),
}


class LinearConnector(BaseConnector):
    """Linear connector — GraphQL API client with Allow/Deny gate.

    Credentials: Linear API key stored locally at
    ~/.celina/storage/connectors/linear.json (mode 0600).
    """

    def __init__(self, approval_cb=None):
        cfg = ConnectorConfig(
            name="linear",
            display_name="Linear",
            oauth_authorize_url=None,  # Linear uses API keys, not OAuth
            oauth_token_url=None,
            oauth_scopes=(),
            oauth_client_id_env=None,
            oauth_client_secret_env=None,
            api_base_url=LINEAR_GRAPHQL_URL,
            action_risk_map={name: a.risk_level for name, a in LINEAR_ACTIONS.items()},
            read_only_actions=LINEAR_READ_ONLY_ACTIONS,
        )
        super().__init__(cfg, approval_cb)
        self._api_key: str | None = None

    # ------------------------------------------------------------------
    # Credential management
    # ------------------------------------------------------------------
    def is_configured(self) -> bool:
        """Return True if a Linear API key is stored."""
        creds = self._load_credentials()
        if creds is None:
            return False
        self._api_key = creds.get("api_key", "")
        return bool(self._api_key and self._api_key != "PASTE_YOUR_KEY_HERE")

    def configure(self, credentials: dict[str, str]) -> bool:
        """Store Linear API key."""
        api_key = credentials.get("api_key", "").strip()
        if not api_key:
            self._set_error("Empty API key")
            return False
        self._api_key = api_key
        try:
            self._save_credentials({"api_key": api_key})
            self._clear_error()
            self._status = ConnectorStatus.CONFIGURED
            return True
        except Exception as exc:
            self._set_error(f"Failed to save credentials: {exc}")
            return False

    def deconfigure(self) -> bool:
        """Remove stored Linear API key."""
        try:
            self._clear_credentials()
            self._api_key = None
            self._status = ConnectorStatus.NOT_CONFIGURED
            self._clear_error()
            return True
        except Exception as exc:
            self._set_error(f"Failed to clear credentials: {exc}")
            return False

    # ------------------------------------------------------------------
    # Action interface
    # ------------------------------------------------------------------
    def list_actions(self) -> list[ConnectorAction]:
        return list(LINEAR_ACTIONS.values())

    def execute_action(self, action_name: str, params: dict[str, Any]) -> ActionResult:
        """Execute a Linear action through the Allow/Deny gate."""
        if not self.is_configured():
            return ActionResult(
                success=False,
                error="Linear connector not configured. Set a Linear API key first.",
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
                if action_name == "list_issues":
                    return self._list_issues(params)
                if action_name == "get_issue":
                    return self._get_issue(params)
                if action_name == "create_issue":
                    return self._create_issue(params)
                if action_name == "update_issue":
                    return self._update_issue(params)
                if action_name == "delete_issue":
                    return self._delete_issue(params)
                if action_name == "list_projects":
                    return self._list_projects(params)
                if action_name == "get_project":
                    return self._get_project(params)
                if action_name == "list_teams":
                    return self._list_teams(params)
                if action_name == "get_team":
                    return self._get_team(params)
                if action_name == "list_users":
                    return self._list_users(params)
                if action_name == "get_user":
                    return self._get_user(params)
                if action_name == "list_cycles":
                    return self._list_cycles(params)
                return ActionResult(
                    success=False,
                    error=f"Action not implemented: {action_name}",
                    action=action_name,
                    connector=self.name,
                )
            except Exception as exc:
                return ActionResult(
                    success=False,
                    error=f"Linear API error: {exc}",
                    action=action_name,
                    connector=self.name,
                )

        return self._with_approval(action_name, params, _run)

    # ------------------------------------------------------------------
    # GraphQL helpers
    # ------------------------------------------------------------------
    def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a GraphQL request to Linear. Returns parsed JSON or raises."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        status, body = self._http_post(LINEAR_GRAPHQL_URL, headers, payload, timeout=15)
        if status != 200 or body is None:
            raise RuntimeError(f"Linear GraphQL returned HTTP {status}")

        if "errors" in body:
            err_msgs = [e.get("message", "unknown error") for e in body["errors"]]
            raise RuntimeError(f"Linear GraphQL errors: {'; '.join(err_msgs)}")

        return body.get("data", {})

    # ------------------------------------------------------------------
    # Action implementations
    # ------------------------------------------------------------------
    def _list_issues(self, params: dict[str, Any]) -> ActionResult:
        variables: dict[str, Any] = {"first": min(int(params.get("limit", 50)), 250)}
        filter_parts: list[str] = []
        if params.get("team_key"):
            variables["teamKey"] = params["team_key"]
            filter_parts.append("team: {key: {eq: $teamKey}}")
        if params.get("query"):
            variables["titleQuery"] = params["query"]
            filter_parts.append("title: {contains: $titleQuery}")
        if params.get("state_name"):
            variables["stateName"] = params["state_name"]
            filter_parts.append("state: {name: {eq: $stateName}}")
        filter_str = ", ".join(filter_parts) if filter_parts else ""

        gql = (
            "query listIssues($teamKey: String, $titleQuery: String, $stateName: String, $first: Int!) {\n"
            "issues(filter: {" + filter_str + "}, first: $first) {\n"
            "nodes {\n"
            "id identifier title description priority createdAt updatedAt\n"
            "state { id name color }\n"
            "assignee { id name email }\n"
            "team { id key name }\n"
            "url\n"
            "}\n"
            "}\n"
            "}\n"
        )

        try:
            data = self._graphql(gql, variables or None)
            issues = data.get("issues", {}).get("nodes", [])
            return ActionResult(
                success=True,
                data={"issues": issues, "count": len(issues)},
                action="list_issues",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="list_issues",
                connector=self.name,
            )
    def _get_issue(self, params: dict[str, Any]) -> ActionResult:
        issue_id = params["id"]
        variables = {"issueId": issue_id}
        gql = (
            "query getIssue($issueId: String!) {\n"
            "issue(id: $issueId) {\n"
            "id identifier title description priority createdAt updatedAt\n"
            "state { id name color }\n"
            "assignee { id name email }\n"
            "team { id key name }\n"
            "url\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables)
            issue = data.get("issue")
            if issue is None:
                return ActionResult(
                    success=False,
                    error=f"Issue not found: {issue_id}",
                    action="get_issue",
                    connector=self.name,
                )
            return ActionResult(
                success=True,
                data={"issue": issue},
                action="get_issue",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="get_issue",
                connector=self.name,
            )
    def _create_issue(self, params: dict[str, Any]) -> ActionResult:
        team_key = params["team_key"]
        team_id = self._resolve_team_id(team_key)
        if not team_id:
            return ActionResult(
                success=False,
                error=f"Team not found: {team_key}",
                action="create_issue",
                connector=self.name,
            )

        variables: dict[str, Any] = {
            "teamId": team_id,
            "title": params["title"],
        }
        if params.get("description"):
            variables["description"] = params["description"]
        if params.get("priority") is not None:
            variables["priority"] = int(params["priority"])
        if params.get("assignee_id"):
            variables["assigneeId"] = params["assignee_id"]
        if params.get("state_id"):
            variables["stateId"] = params["state_id"]
        if params.get("project_id"):
            variables["projectId"] = params["project_id"]
        if params.get("cycle_id"):
            variables["cycleId"] = params["cycle_id"]
        if params.get("due_date"):
            variables["dueDate"] = params["due_date"]
        if params.get("label_ids"):
            variables["labelIds"] = params["label_ids"]

        gql = (
            "mutation createIssue($teamId: ID!, $title: String!, $description: String, $priority: Int, "
            "$assigneeId: ID, $stateId: ID, $projectId: ID, $cycleId: ID, $dueDate: String, $labelIds: [ID!]) {\n"
            "issueCreate(input: {teamId: $teamId, title: $title, description: $description, priority: $priority, "
            "assigneeId: $assigneeId, stateId: $stateId, projectId: $projectId, cycleId: $cycleId, "
            "dueDate: $dueDate, labelIds: $labelIds}) {\n"
            "issue { id identifier title url }\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables)
            issue = data.get("issueCreate", {}).get("issue", {})
            return ActionResult(
                success=True,
                data={"issue": issue},
                action="create_issue",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="create_issue",
                connector=self.name,
            )
    def _update_issue(self, params: dict[str, Any]) -> ActionResult:
        issue_id = params["id"]
        variables: dict[str, Any] = {"id": issue_id}
        set_parts: list[str] = []
        if params.get("title"):
            variables["title"] = params["title"]
            set_parts.append("title: $title")
        if params.get("description") is not None:
            variables["description"] = params["description"]
            set_parts.append("description: $description")
        if params.get("priority") is not None:
            variables["priority"] = int(params["priority"])
            set_parts.append("priority: $priority")
        if params.get("assignee_id"):
            variables["assigneeId"] = params["assignee_id"]
            set_parts.append("assigneeId: $assigneeId")
        if params.get("state_id"):
            variables["stateId"] = params["state_id"]
            set_parts.append("stateId: $stateId")
        if params.get("project_id"):
            variables["projectId"] = params["project_id"]
            set_parts.append("projectId: $projectId")
        if params.get("cycle_id"):
            variables["cycleId"] = params["cycle_id"]
            set_parts.append("cycleId: $cycleId")
        if params.get("due_date") is not None:
            variables["dueDate"] = params["due_date"]
            set_parts.append("dueDate: $dueDate")
        if params.get("added_label_ids"):
            variables["addedLabelIds"] = params["added_label_ids"]
            set_parts.append("addedLabelIds: $addedLabelIds")

        if not set_parts:
            return ActionResult(
                success=False,
                error="No fields to update",
                action="update_issue",
                connector=self.name,
            )

        gql = (
            "mutation updateIssue($id: ID!, $title: String, $description: String, $priority: Int, "
            "$assigneeId: ID, $stateId: ID, $projectId: ID, $cycleId: ID, $dueDate: String, "
            "$addedLabelIds: [ID!]) {\n"
            "issueUpdate(id: $id, input: {" + ", ".join(set_parts) + "}) {\n"
            "issue { id identifier title url }\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables)
            issue = data.get("issueUpdate", {}).get("issue", {})
            return ActionResult(
                success=True,
                data={"issue": issue},
                action="update_issue",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="update_issue",
                connector=self.name,
            )
    def _delete_issue(self, params: dict[str, Any]) -> ActionResult:
        issue_id = params["id"]
        variables = {"issueId": issue_id, "permanentDelete": bool(params.get("permanently_delete", False))}
        gql = (
            "mutation deleteIssue($issueId: ID!, $permanentDelete: Boolean!) {\n"
            "issueDelete(id: $issueId, permanentlyDelete: $permanentDelete) {\n"
            "success deletedIssueId\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables)
            result = data.get("issueDelete", {})
            return ActionResult(
                success=bool(result.get("success")),
                data={"deleted_issue_id": result.get("deletedIssueId"), "permanent": variables["permanentDelete"]},
                action="delete_issue",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="delete_issue",
                connector=self.name,
            )
    def _list_projects(self, params: dict[str, Any]) -> ActionResult:
        variables: dict[str, Any] = {"first": min(int(params.get("limit", 50)), 250)}
        filter_parts: list[str] = []
        if params.get("name"):
            variables["projectName"] = params["name"]
            filter_parts.append("name: {contains: $projectName}")
        if params.get("state"):
            variables["projectState"] = params["state"]
            filter_parts.append("state: {eq: $projectState}")
        filter_str = ", ".join(filter_parts) if filter_parts else ""

        gql = (
            "query listProjects($projectName: String, $projectState: String, $first: Int!) {\n"
            "projects(filter: {" + filter_str + "}, first: $first) {\n"
            "nodes {\n"
            "id name description priority createdAt updatedAt\n"
            "state { id name }\n"
            "url\n"
            "}\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables or None)
            projects = data.get("projects", {}).get("nodes", [])
            return ActionResult(
                success=True,
                data={"projects": projects, "count": len(projects)},
                action="list_projects",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="list_projects",
                connector=self.name,
            )
    def _get_project(self, params: dict[str, Any]) -> ActionResult:
        project_id = params["id"]
        variables = {"projectId": project_id}
        gql = (
            "query getProject($projectId: String!) {\n"
            "project(id: $projectId) {\n"
            "id name description priority createdAt updatedAt\n"
            "state { id name }\n"
            "url\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables)
            project = data.get("project")
            if project is None:
                return ActionResult(
                    success=False,
                    error=f"Project not found: {project_id}",
                    action="get_project",
                    connector=self.name,
                )
            return ActionResult(
                success=True,
                data={"project": project},
                action="get_project",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="get_project",
                connector=self.name,
            )
    def _list_teams(self, params: dict[str, Any]) -> ActionResult:
        variables: dict[str, Any] = {"first": min(int(params.get("limit", 50)), 250)}
        filter_parts: list[str] = []
        if params.get("name"):
            variables["teamName"] = params["name"]
            filter_parts.append("name: {contains: $teamName}")
        if params.get("key"):
            variables["teamKey"] = params["key"]
            filter_parts.append("key: {eq: $teamKey}")
        filter_str = ", ".join(filter_parts) if filter_parts else ""

        gql = (
            "query listTeams($teamName: String, $teamKey: String, $first: Int!) {\n"
            "teams(filter: {" + filter_str + "}, first: $first) {\n"
            "nodes {\n"
            "id key name description\n"
            "}\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables or None)
            teams = data.get("teams", {}).get("nodes", [])
            return ActionResult(
                success=True,
                data={"teams": teams, "count": len(teams)},
                action="list_teams",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="list_teams",
                connector=self.name,
            )
    def _get_team(self, params: dict[str, Any]) -> ActionResult:
        team_id = params["id"]
        variables = {"teamId": team_id}
        gql = (
            "query getTeam($teamId: String!) {\n"
            "team(id: $teamId) {\n"
            "id key name description createdAt updatedAt\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables)
            team = data.get("team")
            if team is None:
                return ActionResult(
                    success=False,
                    error=f"Team not found: {team_id}",
                    action="get_team",
                    connector=self.name,
                )
            return ActionResult(
                success=True,
                data={"team": team},
                action="get_team",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="get_team",
                connector=self.name,
            )
    def _list_users(self, params: dict[str, Any]) -> ActionResult:
        variables: dict[str, Any] = {"first": min(int(params.get("limit", 50)), 250)}
        filter_parts: list[str] = []
        if params.get("name"):
            variables["userName"] = params["name"]
            filter_parts.append("name: {contains: $userName}")
        if params.get("email"):
            variables["userEmail"] = params["email"]
            filter_parts.append("email: {eq: $userEmail}")
        if params.get("active") is not None:
            variables["userActive"] = params["active"]
            filter_parts.append("active: {eq: $userActive}")
        filter_str = ", ".join(filter_parts) if filter_parts else ""

        gql = (
            "query listUsers($userName: String, $userEmail: String, $userActive: Boolean, $first: Int!) {\n"
            "users(filter: {" + filter_str + "}, first: $first) {\n"
            "nodes {\n"
            "id name email active createdAt updatedAt\n"
            "}\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables or None)
            users = data.get("users", {}).get("nodes", [])
            return ActionResult(
                success=True,
                data={"users": users, "count": len(users)},
                action="list_users",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="list_users",
                connector=self.name,
            )
    def _get_user(self, params: dict[str, Any]) -> ActionResult:
        user_id = params["id"]
        variables = {"userId": user_id}
        gql = (
            "query getUser($userId: String!) {\n"
            "user(id: $userId) {\n"
            "id name email active createdAt updatedAt\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables)
            user = data.get("user")
            if user is None:
                return ActionResult(
                    success=False,
                    error=f"User not found: {user_id}",
                    action="get_user",
                    connector=self.name,
                )
            return ActionResult(
                success=True,
                data={"user": user},
                action="get_user",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="get_user",
                connector=self.name,
            )
    def _list_cycles(self, params: dict[str, Any]) -> ActionResult:
        variables: dict[str, Any] = {"first": min(int(params.get("limit", 50)), 250)}
        filter_parts: list[str] = []
        if params.get("team_id"):
            variables["cycleTeamId"] = params["team_id"]
            filter_parts.append("team: {id: {eq: $cycleTeamId}}")
        if params.get("is_active") is not None:
            variables["cycleActive"] = params["is_active"]
            filter_parts.append("active: {eq: $cycleActive}")
        filter_str = ", ".join(filter_parts) if filter_parts else ""

        gql = (
            "query listCycles($cycleTeamId: String, $cycleActive: Boolean, $first: Int!) {\n"
            "cycles(filter: {" + filter_str + "}, first: $first) {\n"
            "nodes {\n"
            "id name description startsAt endsAt completedAt active\n"
            "}\n"
            "}\n"
            "}\n"
        )
        try:
            data = self._graphql(gql, variables or None)
            cycles = data.get("cycles", {}).get("nodes", [])
            return ActionResult(
                success=True,
                data={"cycles": cycles, "count": len(cycles)},
                action="list_cycles",
                connector=self.name,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                action="list_cycles",
                connector=self.name,
            )
    def _resolve_team_id(self, team_key: str) -> str | None:
        """Resolve a team key (e.g. 'ENG') to a team UUID."""
        result = self._list_teams({"key": team_key, "limit": 1})
        if result.success and result.data.get("teams"):
            return result.data["teams"][0]["id"]
        return None

    @staticmethod
    def _graphql_escape(s: str) -> str:
        """Escape a string for safe inclusion in a GraphQL query."""
        return (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
