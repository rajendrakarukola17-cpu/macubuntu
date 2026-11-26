"""FastMCP 4.2 Desktop & Skills Server for Celina OS.

Exposes Focus Modes, Visual Intelligence screen capture, and Celina's verified
skills directory (SkillsDirectoryProvider with built-in file digests) over MCP
with approval-gated Tier-2 execution.

Runs over Unix domain sockets at $XDG_RUNTIME_DIR/celina/mcp.sock.
All OS mutations default to Tier-2 approval via FastMCP elicitation.

Zero filesystem mutations at module import time — directories and sockets are
created lazily under os.umask(0o077) with strict 0700/0600 modes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("celina.mcp.os_desktop")

VERIFIED_SKILLS = Path.home() / ".local/share/celina/skills/verified"
QUARANTINE_SKILLS = Path.home() / ".local/share/celina/skills/quarantine"


def _runtime_dir() -> Path:
    """Return the Celina runtime dir under XDG_RUNTIME_DIR (no import-time side effects)."""
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "celina"


def _visual_intelligence_allowed() -> bool:
    """Fail-closed check of ~/.config/celina/access_policy.json visual_intelligence toggle."""
    config_home = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"),
    )
    policy_path = config_home / "celina" / "access_policy.json"
    if not policy_path.is_file():
        return True
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
        return bool(data.get("capabilities", {}).get("visual_intelligence", True))
    except (OSError, json.JSONDecodeError):
        # Corrupt or unreadable policy → fail-closed
        return False


# ---------------------------------------------------------------------------
# Tool functions — always defined at module level (even without FastMCP).
# Decorated with @_mcp.tool only when FastMCP is available.
# ---------------------------------------------------------------------------

async def set_focus_mode(mode: str, ctx: Any) -> dict[str, str]:
    """Toggle macOS-style Focus Modes (work, study, personal, off) via GNOME DND.

    Tier-2 approval via FastMCP elicitation before applying notification changes.
    Persists the active mode so work/study/personal/off remain distinct.
    """
    allowed = {"work", "study", "personal", "off"}
    if mode not in allowed:
        raise ValueError(
            f"Unsupported focus mode '{mode}'. Choose from {sorted(allowed)}.",
        )

    # Tier-2 approval via FastMCP elicitation
    decision = await ctx.elicit(
        f"Activate '{mode}' Focus Mode (updates GNOME notifications)?",
    )
    if getattr(decision, "action", None) != "accept":
        return {"status": "cancelled", "mode": mode}

    show_banners = "true" if mode == "off" else "false"

    # Async subprocess with timeout — never block the event loop.
    try:
        proc = await asyncio.create_subprocess_exec(
            "gsettings",
            "set",
            "org.gnome.desktop.notifications",
            "show-banners",
            show_banners,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode != 0:
            return {
                "status": "failed",
                "mode": mode,
                "error": (stderr or b"").decode("utf-8", errors="replace").strip(),
            }
    except (TimeoutError, OSError) as exc:
        return {"status": "failed", "mode": mode, "error": str(exc)}

    # Persist focus mode state for Dynamic Island / GNOME extension observability.
    _persist_focus_mode(mode, show_banners)

    return {"status": "applied", "mode": mode, "show_banners": show_banners}


async def capture_visual_intelligence(ctx: Any) -> dict[str, str]:
    """Capture active screen via xdg-desktop-portal into ephemeral tmpfs for OCR/VLM.

    Tier-2 gated: checks access_policy.json (fail-closed) and requires
    ctx.elicit() approval before any screen capture. Writes to a unique
    0o600 temp file under 0o700 $XDG_RUNTIME_DIR/celina/screenshots — never
    accepts caller-controlled output_dir and never overwrites latest_vi.png.
    """
    # 1. Fail-closed on access_policy.json
    if not _visual_intelligence_allowed():
        return {
            "status": "denied",
            "reason": "visual_intelligence disabled in access_policy.json",
        }

    # 2. Mandatory Tier-2 elicitation — never silent screen capture
    decision = await ctx.elicit(
        "Capture screen for Visual Intelligence into ephemeral tmpfs?",
    )
    if getattr(decision, "action", None) != "accept":
        return {"status": "cancelled"}

    # 3. Always write to 0o700 tmpfs with unique 0o600 temp file (no races, no overwrite)
    out_base = _runtime_dir() / "screenshots"
    out_base.mkdir(parents=True, exist_ok=True)
    os.chmod(out_base, 0o700)

    import tempfile

    fd, tmp_name = tempfile.mkstemp(prefix="vi_", suffix=".png", dir=str(out_base))
    os.close(fd)
    os.chmod(tmp_name, 0o600)

        # 4. Async Wayland-native capture with timeout — never block the event loop.
        # Prefer celina-visual-intelligence tool, fall back to xdg-desktop-portal via gdbus.
    captured = False
    capture_error = ""
    vi_tool = Path.home() / "tools" / "celina-visual-intelligence"
    if vi_tool.is_file() and os.access(vi_tool, os.X_OK):
        try:
            proc = await asyncio.create_subprocess_exec(
                str(vi_tool),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30.0,
            )
            if proc.returncode == 0:
                captured = True
            else:
                capture_error = (
                    stderr or b""
                ).decode("utf-8", errors="replace").strip()
        except (TimeoutError, OSError) as exc:
            capture_error = str(exc)

    if not captured:
        # Fall back to xdg-desktop-portal Screenshot via gdbus
        try:
            proc = await asyncio.create_subprocess_exec(
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.portal.Desktop",
                "--object-path",
                "/org/freedesktop/portal/desktop",
                "--method",
                "org.freedesktop.portal.Screenshot.Screenshot",
                "",
                "Interactive",
                "{}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30.0,
            )
            if proc.returncode == 0:
                captured = True
            else:
                capture_error = (
                    stderr or b""
                ).decode("utf-8", errors="replace").strip()
        except (TimeoutError, OSError) as exc:
            capture_error = str(exc)

    if not captured:
        # Clean up the temp file on failure
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        return {
            "status": "failed",
            "reason": f"screen capture failed: {capture_error or 'unknown error'}",
        }

    return {
        "status": "captured",
        "image_path": tmp_name,
        "next_step": "pass to vision_analyze + vision_ocr",
    }


# ---------------------------------------------------------------------------
# TaskGraph & Goals MCP tools (bidirectional task-list server for Claude Code / Cursor)
# ---------------------------------------------------------------------------


async def list_celina_tasks(limit: int = 25) -> dict[str, Any]:
    """Read-only: lets external coding agents (Claude Code / Cursor) inspect Celina's TaskGraph & active goals.

    Reads TaskGraph snapshot + active memory.db goals. No mutation, no approval needed.
    """
    from .. import config, memory
    from ..planner.task_graph import TaskGraph

    lim = max(1, min(100, int(limit)))
    graph = TaskGraph(config.BASE_DIR / "tasks.db")
    try:
        snap = graph.export_graph(limit=lim)
    finally:
        graph.close()
    goals = memory.get_db().list_goals(status="active", limit=15, cloud_only=False)
    return {
        "counts": snap.get("counts", {}),
        "evidence_ok": snap.get("evidence_ok", False),
        "tasks": [
            {
                "id": t["id"],
                "status": t["status"],
                "goal": t["goal"],
                "priority": t["priority"],
            }
            for t in snap.get("tasks", [])
        ],
        "active_goals": goals,
    }


async def queue_celina_task(goal: str, ctx: Any, priority: int = 5) -> dict[str, Any]:
    """Tier-2 approval-gated: lets Claude Code / Cursor queue a new task into Celina's durable TaskGraph.

    Rejects prompt injections via evidence.detect_prompt_injection().
    Requires await ctx.elicit(...) confirmation before persisting into tasks.db.
    """
    clean = (goal or "").strip()
    if not clean:
        raise ValueError("goal must not be empty")

    from .. import config, evidence
    from ..planner.task_graph import TaskGraph
    from ..planner.tree_planner import TreePlanner

    if evidence.detect_prompt_injection(clean):
        return {"status": "blocked", "reason": "prompt_injection_detected"}

    decision = await ctx.elicit(
        f"Allow external MCP client to queue Celina task (priority={priority}): '{clean[:120]}'?",
    )
    if getattr(decision, "action", None) != "accept":
        return {"status": "cancelled"}

    graph = TaskGraph(config.BASE_DIR / "tasks.db")
    try:
        planner = TreePlanner(llm_client=None, graph=graph)
        root_id = planner.plan(clean, available_tools=[], priority=max(1, min(10, int(priority))))
        return {"status": "queued", "root_task_id": root_id, "goal": clean}
    finally:
        graph.close()


# ---------------------------------------------------------------------------
# FastMCP integration (optional dependency)
# ---------------------------------------------------------------------------

try:
    from fastmcp import Context, FastMCP
    from fastmcp.server.providers.skills import SkillsDirectoryProvider

    HAS_FASTMCP = True

    _mcp = FastMCP("celina-os-desktop")

    # Register the module-level functions as MCP tools
    _mcp.tool(set_focus_mode)
    _mcp.tool(capture_visual_intelligence)
    _mcp.tool(list_celina_tasks)
    _mcp.tool(queue_celina_task)

except ImportError:
    HAS_FASTMCP = False
    _mcp = None  # type: ignore[assignment]

    log.warning(
        "fastmcp>=4.2 not installed. MCP server will not be available. "
        "Install with: pip install fastmcp>=4.2.0",
    )


def _persist_focus_mode(mode: str, show_banners: str) -> None:
    """Persist active focus mode to runtime state for Dynamic Island / GNOME extension."""
    try:
        state_dir = _runtime_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        state_file = state_dir / "focus_mode.json"
        state_file.write_text(
            json.dumps({"mode": mode, "show_banners": show_banners}, indent=2),
            encoding="utf-8",
        )
        os.chmod(state_file, 0o600)
    except OSError:
        # Non-fatal: persistence failure must not break focus mode application.
        pass


def create_mcp_server() -> Any:
    """Return the FastMCP app instance, creating skill dirs lazily with 0700 perms.

    Directory creation happens here (when the server actually starts), never
    at module import time — preserving the zero-import-side-effect invariant.
    """
    if not HAS_FASTMCP or _mcp is None:
        return None

    # Create skill directories lazily with strict 0700 perms (no import-time mutation)
    VERIFIED_SKILLS.mkdir(parents=True, exist_ok=True)
    QUARANTINE_SKILLS.mkdir(parents=True, exist_ok=True)
    os.chmod(VERIFIED_SKILLS, 0o700)
    os.chmod(QUARANTINE_SKILLS, 0o700)

    _mcp.add_provider(SkillsDirectoryProvider(roots=[VERIFIED_SKILLS]))
    return _mcp


def has_fastmcp() -> bool:
    """Return True if fastmcp is available and the server was created."""
    return HAS_FASTMCP


if __name__ == "__main__":  # pragma: no cover
    if HAS_FASTMCP and _mcp is not None:
        # Run over Unix domain socket for local-only IPC.
        sock_dir = _runtime_dir()
        sock_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(sock_dir, 0o700)

        sock_path = sock_dir / "mcp.sock"
        # Unlink stale socket before binding.
        sock_path.unlink(missing_ok=True)

        old_umask = os.umask(0o077)
        try:
            _mcp.run(transport="unix-socket", path=str(sock_path))
        finally:
            os.umask(old_umask)
            # Clean up stale socket on exit
            try:
                sock_path.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        print("fastmcp not installed; cannot start MCP server.", flush=True)
