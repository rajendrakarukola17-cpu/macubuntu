"""Celina Typed CLI Bridge (`cli_bridge.py`) — Safe `cli_<tool>` Execution Gateway

CLI execution is disabled while no trusted broker is wired. The legacy
subcommand-level read-only labels do not establish safety for arbitrary args.

Zero import-time side effects: no $HOME writes, no subprocess spawning at import.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import approval, command_runner

ApprovalCallback = Callable[[str, str], bool]

# (read_only_subcommands, mutating_subcommands_requiring_tier2_approval)
CLI_ALLOWLIST: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "git": (
        frozenset({"status", "diff", "log", "branch", "show", "rev-parse", "remote"}),
        frozenset({"add", "commit", "checkout", "switch", "stash", "pull", "fetch"}),
    ),
    "docker": (
        frozenset({"ps", "images", "logs", "inspect", "stats", "version"}),
        frozenset({"start", "stop", "restart"}),
    ),
    "systemctl": (
        frozenset({"status", "is-active", "is-enabled", "list-units", "list-timers"}),
        frozenset({"start", "stop", "restart", "reload"}),
    ),
    "gh": (
        frozenset({"status", "pr", "issue", "repo", "run"}),
        frozenset(),  # keep gh strictly read-only unless explicitly extended
    ),
}

_FORBIDDEN_FLAGS = frozenset(
    {"--exec", "--upload-pack", "--receive-pack", "-c", "--config", "--privileged", "--host"},
)


def _normalize_tool_name(tool: str) -> str:
    """Strip optional `cli_` prefix and lowercase."""
    return (tool or "").strip().lower().removeprefix("cli_")


def _strip_leading_dash_dash(args: list[str]) -> list[str]:
    """If the first arg is `--`, drop it (caller already provided separator)."""
    if args and args[0] == "--":
        return args[1:]
    return args


def _trusted_cli_dispatch_available() -> bool:
    """No broker-owned typed CLI path exists. Do not enable legacy dispatch."""
    return False


def run_typed_cli(
    tool: str,
    subcommand: str,
    extra_args: list[str] | None = None,
    *,
    cwd: str | None = None,
    approval_cb: ApprovalCallback | None = None,
    timeout: float = 15.0,
) -> str:
    """Execute an allowlisted CLI tool without a shell, enforcing read vs. mutate tiers.

    Args:
        tool: CLI tool name (e.g. "git", "docker", "systemctl", "gh"); optional "cli_" prefix.
        subcommand: Subcommand (e.g. "status", "ps", "is-active").
        extra_args: Additional argv after the subcommand.
        cwd: Working directory (resolved under $HOME for safety).
        approval_cb: Interactive approval callback for mutating subcommands.
        timeout: Per-command timeout in seconds.

    Returns:
        Formatted command output or a block/deny message.

    """
    bin_name = _normalize_tool_name(tool)
    sub = (subcommand or "").strip().lower()
    if not bin_name:
        return "Blocked: empty CLI tool name."
    if bin_name not in CLI_ALLOWLIST:
        return (
            f"Blocked: CLI tool '{bin_name}' is not in the typed allowlist "
            f"({sorted(CLI_ALLOWLIST)})."
        )

    ro_subs, rw_subs = CLI_ALLOWLIST[bin_name]
    if sub not in ro_subs and sub not in rw_subs:
        return f"Blocked: Subcommand '{bin_name} {sub}' is not permitted."
    if extra_args is not None and (type(extra_args) is not list or any(type(arg) is not str for arg in extra_args)):
        return "Denied: CLI arguments must be a list of strings."
    clean_extra = _strip_leading_dash_dash([arg for arg in (extra_args or []) if arg.strip()])
    for arg in clean_extra:
        flag_head = arg.split("=", 1)[0]
        if flag_head in _FORBIDDEN_FLAGS:
            return f"Blocked: Flag '{flag_head}' is forbidden in typed CLI execution."
    # Even nominally read-only groups such as `gh pr` and `git branch` can
    # mutate via their remaining arguments. Do not invoke the legacy path.
    if not _trusted_cli_dispatch_available():
        return "Denied: typed CLI execution requires a trusted approval broker, which is not available."
    if not shutil.which(bin_name):
        return f"Error: '{bin_name}' is not installed on PATH."

    argv = [bin_name]
    if bin_name == "systemctl":
        argv.append("--user")  # Never allow unapproved system-wide unit mutations here
    argv.append(sub)
    if clean_extra:
        argv.extend(clean_extra)

    is_mutating = sub in rw_subs
    if is_mutating:
        bound_args: dict[str, Any] = {"argv": argv, "cwd": cwd or ""}
        token, deny_err = approval.request_bound_approval(
            "run_shell_command",
            bound_args,
            approval_cb,
            title=f"Approve CLI Command: {bin_name} {sub}?",
            target=" ".join(argv)[:140],
        )
        if deny_err:
            return deny_err
        ok, err = approval.verify_and_consume(token, "run_shell_command", bound_args)
        if not ok:
            return err

    work_dir = str(Path(cwd).expanduser().resolve()) if cwd else None
    res = command_runner.run_command(argv, timeout=timeout, cwd=work_dir)
    out = ((res.stdout or "") + (f"\nSTDERR:\n{res.stderr}" if res.stderr else "")).strip() or "(No output)"
    if len(out) > 4000:
        out = out[:4000] + "\n...[truncated]"
    return f"[cli_{bin_name} exit={res.returncode}]\n{out}"
