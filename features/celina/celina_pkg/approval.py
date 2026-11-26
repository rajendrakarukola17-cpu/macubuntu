"""
Celina AI (Celly) - Risk-Aware, Argument-Bound Approval System (Phase 4)
Replaces coarse boolean approval with cryptographic argument binding:
  - 8 explicit RiskLevel tiers
  - ApprovalToken binding (tool, target, canonical SHA-256 arguments_hash, risk, expires_at)
  - Exact final-argument verification immediately before execution
  - Mandatory fresh per-step re-confirmation for high-risk actions (NEVER bypassed by
    upfront skill approval!)
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

ApprovalCallback = Callable[[str, str], bool]


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    LOCAL_MUTATION = "local_mutation"
    DESKTOP_INPUT = "desktop_input"
    FILE_WRITE = "file_write"
    NETWORK_REQUEST = "network_request"
    MESSAGE_SEND = "message_send"
    SHELL_HOST = "shell_host"
    SECURITY_SENSITIVE = "security_sensitive"


# Tools mapped to their exact default risk tier
TOOL_RISK_MAP: dict[str, RiskLevel] = {
    # Read-only
    "get_system_stats": RiskLevel.READ_ONLY,
    "list_running_apps": RiskLevel.READ_ONLY,
    "search_files": RiskLevel.READ_ONLY,
    "search_in_files": RiskLevel.READ_ONLY,
    "read_file": RiskLevel.READ_ONLY,
    "read_document": RiskLevel.READ_ONLY,
    "get_clipboard": RiskLevel.READ_ONLY,
    "get_highlighted_text": RiskLevel.READ_ONLY,
    "read_screen": RiskLevel.READ_ONLY,
    "find_text_on_screen": RiskLevel.READ_ONLY,
    "inspect_ui_elements": RiskLevel.READ_ONLY,
    "search_memory": RiskLevel.READ_ONLY,
    "cache_status": RiskLevel.READ_ONLY,
    "list_skills": RiskLevel.READ_ONLY,
    "mcp_list_servers": RiskLevel.READ_ONLY,
    "mcp_list_tools": RiskLevel.READ_ONLY,
    "list_quarantined_skills": RiskLevel.READ_ONLY,
    "get_hardware_capabilities": RiskLevel.READ_ONLY,
    "spotlight_query": RiskLevel.READ_ONLY,
    "get_continuity_status": RiskLevel.READ_ONLY,
    "audit_app_permissions": RiskLevel.READ_ONLY,
    "search_active_app_menus": RiskLevel.READ_ONLY,
    "get_upcoming_events": RiskLevel.READ_ONLY,
    "search_contacts": RiskLevel.READ_ONLY,
    "search_browser_bookmarks": RiskLevel.READ_ONLY,
    "prepare_meeting_context": RiskLevel.READ_ONLY,
    "diagnose_systemd_service": RiskLevel.READ_ONLY,
    "get_secret_vault_status": RiskLevel.READ_ONLY,
    # Local state mutations
    "writing_tool_transform": RiskLevel.LOCAL_MUTATION,
    "live_text_ocr": RiskLevel.LOCAL_MUTATION,
    "create_calendar_event": RiskLevel.FILE_WRITE,
    "set_focus_mode": RiskLevel.LOCAL_MUTATION,
    "route_app_audio": RiskLevel.LOCAL_MUTATION,
    "launch_android_mirror": RiskLevel.LOCAL_MUTATION,
    "control_airplay_mirror": RiskLevel.LOCAL_MUTATION,
    "manage_default_apps": RiskLevel.LOCAL_MUTATION,
    "mount_ios_device": RiskLevel.LOCAL_MUTATION,
    "start_ios_continuity_camera": RiskLevel.LOCAL_MUTATION,
    "start_ipad_sidecar_weylus": RiskLevel.LOCAL_MUTATION,
    "respond_ios_notification": RiskLevel.LOCAL_MUTATION,
    "set_volume": RiskLevel.LOCAL_MUTATION,
    "set_brightness": RiskLevel.LOCAL_MUTATION,
    "control_media": RiskLevel.LOCAL_MUTATION,
    "control_connectivity": RiskLevel.LOCAL_MUTATION,
    "open_application": RiskLevel.LOCAL_MUTATION,
    "open_document": RiskLevel.LOCAL_MUTATION,
    "close_application": RiskLevel.LOCAL_MUTATION,
    "toggle_app_nap": RiskLevel.LOCAL_MUTATION,
    "set_clipboard": RiskLevel.LOCAL_MUTATION,
    "set_timer": RiskLevel.LOCAL_MUTATION,
    "remember_fact": RiskLevel.LOCAL_MUTATION,
    "link_knowledge": RiskLevel.LOCAL_MUTATION,
    "manage_goal": RiskLevel.LOCAL_MUTATION,
    "clear_cache": RiskLevel.LOCAL_MUTATION,
    "learn_skill": RiskLevel.LOCAL_MUTATION,
    "save_last_actions_as_skill": RiskLevel.LOCAL_MUTATION,
    "start_recording": RiskLevel.LOCAL_MUTATION,
    "stop_recording": RiskLevel.LOCAL_MUTATION,
    "update_skill": RiskLevel.LOCAL_MUTATION,
    "forget_skill": RiskLevel.LOCAL_MUTATION,
    "save_workspace_snapshot": RiskLevel.LOCAL_MUTATION,
    "set_assistant_identity": RiskLevel.LOCAL_MUTATION,
    # Desktop input
    "click_on_text": RiskLevel.DESKTOP_INPUT,
    "click_position": RiskLevel.DESKTOP_INPUT,
    "scroll_screen": RiskLevel.DESKTOP_INPUT,
    "drag_and_drop": RiskLevel.DESKTOP_INPUT,
    "type_text": RiskLevel.DESKTOP_INPUT,
    "press_hotkey": RiskLevel.DESKTOP_INPUT,
    "autonomous_gui_task": RiskLevel.DESKTOP_INPUT,
    "run_skill": RiskLevel.DESKTOP_INPUT,
    # File writes / moves / deletes
    "write_file": RiskLevel.FILE_WRITE,
    "edit_file": RiskLevel.FILE_WRITE,
    "create_document": RiskLevel.FILE_WRITE,
    "edit_document": RiskLevel.FILE_WRITE,
    "convert_document": RiskLevel.FILE_WRITE,
    "libreoffice_edit": RiskLevel.FILE_WRITE,
    "move_file": RiskLevel.FILE_WRITE,
    "trash_file": RiskLevel.FILE_WRITE,
    # Network requests / external side effects
    "web_search": RiskLevel.NETWORK_REQUEST,
    "fetch_webpage": RiskLevel.NETWORK_REQUEST,
    "fetch_url_text": RiskLevel.NETWORK_REQUEST,
    "get_weather": RiskLevel.NETWORK_REQUEST,
    "get_news_headlines": RiskLevel.NETWORK_REQUEST,
    "get_current_time_zone": RiskLevel.NETWORK_REQUEST,
    "trigger_iot_webhook": RiskLevel.NETWORK_REQUEST,
    "install_community_skill": RiskLevel.NETWORK_REQUEST,
    "mcp_call_tool": RiskLevel.NETWORK_REQUEST,
    # Outbound messaging
    "compose_message": RiskLevel.MESSAGE_SEND,
    # Shell / sandboxed code execution
    "run_shell_command": RiskLevel.SHELL_HOST,
    "run_python_sandbox": RiskLevel.SHELL_HOST,
    "code_sandbox": RiskLevel.SHELL_HOST,
    # Security-sensitive promotions / policy actions / live audio capture
    "transcribe_system_audio": RiskLevel.SECURITY_SENSITIVE,
    "approve_quarantined_skill": RiskLevel.SECURITY_SENSITIVE,
    "reject_quarantined_skill": RiskLevel.LOCAL_MUTATION,
    "submit_goal": RiskLevel.LOCAL_MUTATION,
    # MCP Perception & Actuation (Tier-2 by definition: external MCP processes)
    "computer_click": RiskLevel.DESKTOP_INPUT,
    "computer_type": RiskLevel.DESKTOP_INPUT,
    "vision_ocr": RiskLevel.SECURITY_SENSITIVE,
    "vision_analyze": RiskLevel.SECURITY_SENSITIVE,
    "ghostdesk_click": RiskLevel.DESKTOP_INPUT,
    # Skill management & security scanning
    "ponytail_evaluate": RiskLevel.LOCAL_MUTATION,
    "skilspector_scan": RiskLevel.SECURITY_SENSITIVE,
}

# Sensitive window keywords where typing / synthetic input must ALWAYS re-confirm (or be refused)
_SENSITIVE_WINDOW_HINTS = (
    "terminal", "konsole", "alacritty", "kitty", "xterm", "tmux",
    "password", "keyring", "keepass", "bitwarden", "1password", "polkit",
    "authenticate", "seahorse", "checkout", "payment",
)
# Short sensitive tokens matched on word boundaries to avoid false positives like 'databank'/'flashcards'
_SENSITIVE_WINDOW_WORD_RE = re.compile(r"\b(ssh|gpg|bank|sudo)\b", re.IGNORECASE)


def _is_sensitive_window(active_window: str) -> bool:
    win_low = (active_window or "").lower()
    if not win_low:
        return False
    return any(h in win_low for h in _SENSITIVE_WINDOW_HINTS) or bool(_SENSITIVE_WINDOW_WORD_RE.search(win_low))

# Tools that ALWAYS require immediate per-action re-confirmation (cannot be bypassed by skill approval)
ALWAYS_RECONFIRM_TOOLS: frozenset[str] = frozenset({
    "run_shell_command",
    "code_sandbox",
    "run_python_sandbox",
    "transcribe_system_audio",
    "compose_message",
    "trigger_iot_webhook",
    "trash_file",
    "move_file",
    "install_community_skill",
    "approve_quarantined_skill",
    "mcp_call_tool",
    "computer_click",
    "computer_type",
    "ghostdesk_click",
    "click_position",
    "drag_and_drop",
    "ponytail_evaluate",
    "skilspector_scan",
})


def compute_arguments_hash(tool: str, arguments: Optional[dict[str, Any]]) -> str:
    """Computes a canonical SHA-256 hash bound to the exact tool name and final arguments."""
    payload = {
        "tool": str(tool),
        "arguments": arguments if isinstance(arguments, dict) else {},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_risk(
    tool: str,
    arguments: Optional[dict[str, Any]] = None,
    active_window: str = "",
) -> RiskLevel:
    """Determines exact effective RiskLevel, escalating for high-risk targets/arguments."""
    base = TOOL_RISK_MAP.get(tool, RiskLevel.SECURITY_SENSITIVE)
    args = arguments or {}
    win_low = (active_window or "").lower()

    if tool in {"type_text", "press_hotkey"} and _is_sensitive_window(active_window):
        return RiskLevel.SECURITY_SENSITIVE
    if tool in {"trash_file", "move_file"}:
        return RiskLevel.SECURITY_SENSITIVE
    if tool == "run_shell_command":
        return RiskLevel.SHELL_HOST
    if tool == "mcp_call_tool":
        return RiskLevel.SECURITY_SENSITIVE
    if tool == "trigger_iot_webhook":
        return RiskLevel.NETWORK_REQUEST
    # Escalate any command-like argument trying to install software or touch security settings
    joined = json.dumps(args, ensure_ascii=False).lower()
    if any(k in joined for k in ("apt install", "dpkg -i", "pip install", "passwd", "sudoers", "visudo", "payment")):
        return RiskLevel.SECURITY_SENSITIVE
    return base


def requires_fresh_confirmation(
    tool: str,
    arguments: Optional[dict[str, Any]] = None,
    active_window: str = "",
) -> bool:
    """
    Returns True if this action MUST be freshly confirmed immediately prior to execution
    and may NEVER inherit an upfront multi-step workflow/skill approval.
    """
    if tool in ALWAYS_RECONFIRM_TOOLS:
        return True
    risk = classify_risk(tool, arguments, active_window=active_window)
    if risk in {
        RiskLevel.SHELL_HOST,
        RiskLevel.MESSAGE_SEND,
        RiskLevel.SECURITY_SENSITIVE,
    }:
        return True
    if tool in {"type_text", "press_hotkey"} and _is_sensitive_window(active_window):
        return True
    return False


@dataclass
class ApprovalToken:
    tool: str
    target: str
    arguments_hash: str
    risk: RiskLevel
    expires_at: float
    single_use: bool = True
    consumed: bool = False

    def to_record(self) -> dict[str, Any]:
        exp_iso = datetime.datetime.fromtimestamp(
            self.expires_at, tz=datetime.timezone.utc
        ).isoformat()
        return {
            "tool": self.tool,
            "target": self.target,
            "arguments_hash": self.arguments_hash,
            "risk": self.risk.value,
            "expires_at": exp_iso,
        }

    def verify_for(self, tool: str, arguments: Optional[dict[str, Any]], now: Optional[float] = None) -> tuple[bool, str]:
        ts = time.time() if now is None else now
        if self.single_use and self.consumed:
            return False, "Approval token already consumed; fresh confirmation required."
        if ts > self.expires_at:
            return False, "Approval token expired; fresh confirmation required."
        if tool != self.tool:
            return False, f"Approval tool mismatch (approved '{self.tool}', attempted '{tool}')."
        actual_hash = compute_arguments_hash(tool, arguments)
        if actual_hash != self.arguments_hash:
            return False, (
                "Approval arguments changed after confirmation "
                f"(approved {self.arguments_hash[:12]} != actual {actual_hash[:12]})."
            )
        return True, ""

    def consume(self) -> None:
        self.consumed = True


def infer_target(tool: str, arguments: Optional[dict[str, Any]]) -> str:
    args = arguments or {}
    for key in (
        "path", "src", "dst", "dest", "mount_dir", "video_device", "webhook_url", "url",
        "server_name", "app_name", "unit_name", "unit", "mime_type", "recipient",
        "slug", "serial", "title", "goal", "target_text", "command", "script",
        "selector", "query", "name",
    ):
        if args.get(key):
            return str(args[key])[:120]
    static_targets = {
        "run_shell_command": "sandbox-shell",
        "run_python_sandbox": "sandbox-shell",
        "transcribe_system_audio": "system-audio",
        "start_ipad_sidecar_weylus": "ipad-sidecar",
        "control_airplay_mirror": "airplay-mirror",
        "respond_ios_notification": "ios-ancs",
    }
    if tool in static_targets:
        return static_targets[tool]
    return "desktop"


def request_bound_approval(
    tool: str,
    arguments: Optional[dict[str, Any]],
    approval_cb: Optional[ApprovalCallback],
    *,
    title: Optional[str] = None,
    target: Optional[str] = None,
    active_window: str = "",
    extra_warning: str = "",
    ttl_seconds: float = 60.0,
) -> tuple[Optional[ApprovalToken], Optional[str]]:
    """
    Requests user approval and returns an ApprovalToken cryptographically bound
    to the exact tool name and final arguments.
    """
    if approval_cb is None:
        return None, f"Denied: '{tool}' requires interactive user approval."

    risk = classify_risk(tool, arguments, active_window=active_window)
    tgt = target or infer_target(tool, arguments)
    arg_hash = compute_arguments_hash(tool, arguments)
    expires_at = time.time() + max(5.0, min(300.0, float(ttl_seconds)))

    token = ApprovalToken(
        tool=tool,
        target=tgt,
        arguments_hash=arg_hash,
        risk=risk,
        expires_at=expires_at,
        single_use=True,
    )

    record = token.to_record()
    args_preview = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)
    if len(args_preview) > 360:
        args_preview = args_preview[:360] + "..."

    detail_lines = [
        f"Risk:     {record['risk']}",
        f"Tool:     {record['tool']}",
        f"Target:   {record['target']}",
        f"ArgsHash: {record['arguments_hash'][:16]}",
        f"Expires:  {record['expires_at']}",
        f"Args:     {args_preview}",
    ]
    if extra_warning:
        detail_lines.insert(0, f"⚠️  {extra_warning}")

    prompt_title = title or f"Approve {risk.value} action: {tool}?"
    approved = bool(approval_cb(prompt_title, "\n".join(detail_lines)))
    if not approved:
        return None, "Cancelled by user."
    return token, None


def verify_and_consume(
    token: Optional[ApprovalToken],
    tool: str,
    arguments: Optional[dict[str, Any]],
    *,
    now: Optional[float] = None,
) -> tuple[bool, str]:
    if token is None:
        return False, f"Denied: Missing bound approval token for '{tool}'."
    ok, err = token.verify_for(tool, arguments, now=now)
    if not ok:
        return False, f"Denied: {err}"
    token.consume()
    return True, ""


class ApprovalEngine:
    """
    Synchronous Tier-2 approval bridge for the Celina v5 Autonomy Engine.
    Strictly fails CLOSED (returns False) if no interactive approval channel
    or Dynamic Island confirmation grants the exact (tool, arguments_hash) pair.
    """

    def __init__(self, approval_cb: Optional[Callable[[str, str], bool]] = None):
        self._approval_cb = approval_cb

    def request_sync(
        self,
        *,
        tool: str,
        arguments_hash: str,
        context: str,
        timeout: int = 300,
    ) -> bool:
        if not tool or not arguments_hash:
            return False
        if self._approval_cb is None:
            # Fail-closed in headless / background mode when no interactive UI approver is attached
            return False
        risk = TOOL_RISK_MAP.get(tool, RiskLevel.SECURITY_SENSITIVE)
        title = f"Autonomy approval ({risk.value}): {tool}"
        details = (
            f"Tool:     {tool}\n"
            f"Risk:     {risk.value}\n"
            f"ArgsHash: {arguments_hash[:16]}\n"
            f"Timeout:  {int(timeout)}s\n"
            f"Goal:     {context[:280]}"
        )
        try:
            return bool(self._approval_cb(title, details))
        except Exception:
            return False

