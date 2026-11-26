"""Celina AI (Celly) - Zero-Idle-RAM Ephemeral MCP (Model Context Protocol) Client (v4.2)
- Pure Python stdlib + requests (zero heavy SDK bloat!)
- Supports BOTH local 'stdio' MCP servers (uvx / npx / python) AND remote Streamable HTTP MCP servers
- Timely Offload: Local stdio servers NEVER launch at boot; they spin up on-demand and auto-terminate
  after idle_offload_seconds (default 180s) to preserve 8GB RAM!
- Security & Privacy: Expands ${ENV_VAR} from ~/.config/celina/env safely, gates mutating calls with
  1-click approval, and marks outputs as local_sensitive_tools for the Privacy Firewall.
"""

import datetime
import json
import os
import re
import selectors
import shlex
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from . import approval, config, net_policy

ApprovalCallback = Callable[[str, str], bool]

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHELL_METACHARS_RE = re.compile(r"(?:&&|\|\||[;|`<>]|\$\()")


def _expand_env(value: str) -> str:
    def repl(match: re.Match) -> str:
        var = match.group(1)
        if var == "HOME":
            return str(config.HOME)
        return os.environ.get(var) or config.ENV.get(var, "")

    return _ENV_PATTERN.sub(repl, value)


def load_mcp_config(cfg_path: Path = config.MCP_CONFIG_FILE) -> dict[str, Any]:
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"idle_offload_seconds": 180, "servers": {}}


def save_mcp_config(cfg: dict[str, Any], cfg_path: Path = config.MCP_CONFIG_FILE) -> None:
    """Atomically persists mcp-servers.json with owner-only 0600 permissions."""
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_path.with_name(f".{cfg_path.name}.tmp")
    payload = json.dumps(cfg, indent=2, sort_keys=True) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, cfg_path)


def validate_mcp_name(name: str) -> str:
    clean = (name or "").strip()
    if not _MCP_NAME_RE.fullmatch(clean):
        raise ValueError(
            f"Invalid MCP server name '{name}'. Use 1-64 chars matching [A-Za-z0-9._-].",
        )
    return clean


def parse_mcp_command(command: str | list[str]) -> list[str]:
    if isinstance(command, str):
        raw = command.strip()
        if not raw:
            raise ValueError("MCP command must not be empty.")
        if _SHELL_METACHARS_RE.search(raw):
            raise ValueError(
                "Shell metacharacters (;, |, &&, ||, `, $(, <, >) are forbidden in MCP commands.",
            )
        argv = shlex.split(raw)
    elif isinstance(command, list):
        argv = [str(part).strip() for part in command if str(part).strip()]
        if any(_SHELL_METACHARS_RE.search(p) for p in argv):
            raise ValueError("Shell metacharacters are forbidden in MCP command arguments.")
    else:
        raise ValueError("MCP command must be a string or list of arguments.")
    if not argv:
        raise ValueError("MCP command must not be empty.")
    return argv


def add_mcp_server(
    name: str,
    command: str | list[str],
    *,
    cfg_path: Path = config.MCP_CONFIG_FILE,
    description: str = "",
    approve: bool = False,
    approval_cb: ApprovalCallback | None = None,
    allowed_tools: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Opt-in MCP server loader (`celina config add-mcp <name> <command>`).
    Each MCP server gets:
      - one opt-in entry (`opt_in=True`)
      - one explicit allowlist entry (`command` argv, zero ambient secrets inherited)
      - one kill switch (`kill_switch=False`, flippable instantaneously via `revoke_mcp_server`)
    Fail-closed: requires `approve=True` or `approval_cb(...)` returning True.
    """
    clean_name = validate_mcp_name(name)
    argv = parse_mcp_command(command)
    from .autonomy import mcp_loader

    mcp_loader.check_mcp_blocklist(clean_name, {"command": argv})

    if not approve:
        if approval_cb is None:
            raise PermissionError(
                f"Adding MCP server '{clean_name}' requires explicit approval (pass approve=True or approval_cb).",
            )
        summary = f"Command: {' '.join(argv)}\nIsolated env: yes (ambient API keys stripped)\nKill switch: enabled"
        if not bool(approval_cb(f"Approve Opt-In MCP Server '{clean_name}'?", summary)):
            raise PermissionError(f"Cancelled by user: MCP server '{clean_name}' was not approved.")

    cfg = load_mcp_config(cfg_path)
    servers = dict(cfg.get("servers") or {})
    now = datetime.datetime.now(datetime.UTC).isoformat()
    entry = {
        "transport": "stdio",
        "command": argv,
        "description": (description or f"Opt-in MCP server ({argv[0]})").strip(),
        "enabled": True,
        "opt_in": True,
        "isolated": True,
        "kill_switch": False,
        "revoked": False,
        "allowlist_entry": {
            "command": argv,
            "approved": True,
            "added_at": now,
        },
        "explicit_auto_approve_tools": [str(t).strip() for t in (allowed_tools or []) if str(t).strip()],
        "env": {str(k): str(v) for k, v in (env or {}).items()},
    }
    servers[clean_name] = entry
    cfg["servers"] = servers
    cfg.setdefault("idle_offload_seconds", 180)
    save_mcp_config(cfg, cfg_path)
    return {"ok": True, "action": "added", "name": clean_name, "entry": entry, "config_path": str(cfg_path)}


def revoke_mcp_server(
    name: str,
    *,
    cfg_path: Path = config.MCP_CONFIG_FILE,
    manager: Optional["MCPManager"] = None,
) -> dict[str, Any]:
    """Triggers the per-server kill switch: disables, revokes, clears auto-approvals, and stops any live process."""
    clean_name = validate_mcp_name(name)
    cfg = load_mcp_config(cfg_path)
    servers = dict(cfg.get("servers") or {})
    if clean_name not in servers:
        return {"ok": False, "error": f"MCP server '{clean_name}' not found."}

    scfg = dict(servers[clean_name])
    scfg["enabled"] = False
    scfg["kill_switch"] = True
    scfg["revoked"] = True
    scfg["revoked_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    scfg["explicit_auto_approve_tools"] = []
    servers[clean_name] = scfg
    cfg["servers"] = servers
    save_mcp_config(cfg, cfg_path)

    mgr = manager if manager is not None else _DEFAULT_MCP
    stopped = False
    if mgr is not None:
        stopped = mgr.stop_server(clean_name)
    return {"ok": True, "action": "revoked", "name": clean_name, "stopped_process": stopped}


def remove_mcp_server(
    name: str,
    *,
    cfg_path: Path = config.MCP_CONFIG_FILE,
    manager: Optional["MCPManager"] = None,
) -> dict[str, Any]:
    """Stops any live subprocess and removes the MCP server entry completely."""
    clean_name = validate_mcp_name(name)
    cfg = load_mcp_config(cfg_path)
    servers = dict(cfg.get("servers") or {})
    if clean_name not in servers:
        return {"ok": False, "error": f"MCP server '{clean_name}' not found."}

    servers.pop(clean_name, None)
    cfg["servers"] = servers
    save_mcp_config(cfg, cfg_path)

    mgr = manager if manager is not None else _DEFAULT_MCP
    stopped = False
    if mgr is not None:
        stopped = mgr.stop_server(clean_name)
    return {"ok": True, "action": "removed", "name": clean_name, "stopped_process": stopped}


_SAFE_STDIO_ENV_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TMPDIR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "VIRTUAL_ENV", "PYTHONPATH", "NODE_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR",
)


class _StdioMCPProcess:
    """Manages one ephemeral local MCP server subprocess over newline-delimited JSON-RPC 2.0."""

    def __init__(self, name: str, command: list[str], extra_env: dict[str, str] | None = None):
        self.name = name
        self.command = [_expand_env(c) for c in command]
        self.extra_env = {k: _expand_env(v) for k, v in (extra_env or {}).items()}
        self._proc: subprocess.Popen | None = None
        self._read_buf = bytearray()
        self._lock = threading.Lock()
        self._req_id = 0
        self.last_used = time.time()

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure_started(self) -> str | None:
        if self.is_alive():
            return None
        if not self.command or not shutil.which(self.command[0]):
            return f"Error: MCP binary '{self.command[0] if self.command else ''}' is not installed."
        # Privacy-safe env: never inherit ambient API keys (e.g. OPENROUTER_API_KEY) into third-party MCP servers
        env = {k: os.environ[k] for k in _SAFE_STDIO_ENV_KEYS if k in os.environ}
        env.update(self.extra_env)
        self._read_buf.clear()
        try:
            # Unbuffered binary pipes so selectors + os.read() never deadlock or starve buffered lines
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                env=env,
            )
        except Exception as exc:
            return f"Error starting MCP server '{self.name}': {exc}"

        # Perform MCP initialize handshake
        init_res, err = self._rpc_locked(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "celina-desktop", "version": "4.2.0"},
            },
        )
        if err:
            self._stop_locked()
            return f"MCP handshake failed for '{self.name}': {err}"
        self._notify_locked("notifications/initialized", {})
        return None

    def _notify_locked(self, method: str, params: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            return
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        try:
            self._proc.stdin.write((msg + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except OSError:
            pass

    def _rpc_locked(
        self, method: str, params: dict[str, Any], timeout: float = 15.0,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self._proc or not self._proc.stdin or not self._proc.stdout:
            return None, "MCP process not running."
        self._req_id += 1
        req_id = self._req_id
        payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        try:
            self._proc.stdin.write((payload + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except OSError as exc:
            return None, str(exc)

        deadline = time.time() + timeout
        stdout_fd = self._proc.stdout.fileno()
        sel = selectors.DefaultSelector()
        sel.register(stdout_fd, selectors.EVENT_READ)
        try:
            while True:
                # Drain any complete newline-delimited frames already buffered
                nl_idx = self._read_buf.find(b"\n")
                while nl_idx != -1:
                    raw_line = bytes(self._read_buf[:nl_idx])
                    del self._read_buf[: nl_idx + 1]
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if line:
                        try:
                            data = json.loads(line)
                            if isinstance(data, dict) and data.get("id") == req_id:
                                if "error" in data:
                                    return None, str(data["error"])
                                return data.get("result") or {}, None
                        except json.JSONDecodeError:
                            pass
                    nl_idx = self._read_buf.find(b"\n")

                remaining = deadline - time.time()
                if remaining <= 0:
                    return None, f"MCP request '{method}' timed out."
                events = sel.select(timeout=remaining)
                if not events:
                    return None, f"MCP request '{method}' timed out."
                try:
                    chunk = os.read(stdout_fd, 4096)
                except OSError as exc:
                    return None, f"MCP read error: {exc}"
                if not chunk:
                    return None, "MCP server closed stream unexpectedly."
                self._read_buf.extend(chunk)
        finally:
            sel.close()

    def call(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        with self._lock:
            self.last_used = time.time()
            err = self._ensure_started()
            if err:
                return None, err
            res, rpc_err = self._rpc_locked(method, params)
            if rpc_err and "timed out" in rpc_err:
                self._stop_locked()
            return res, rpc_err

    def _stop_locked(self) -> None:
        if self._proc is not None:
            proc = self._proc
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
            finally:
                for stream in (proc.stdin, proc.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
                self._proc = None
                self._read_buf.clear()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()


class MCPManager:
    """Lazy-starting, auto-offloading MCP connector registry."""

    def __init__(self, cfg_path: Path = config.MCP_CONFIG_FILE):
        self.cfg_path = cfg_path
        self._lock = threading.Lock()
        self._procs: dict[str, _StdioMCPProcess] = {}
        self._http_req_id = 0

    def _get_servers(self) -> tuple[dict[str, Any], float]:
        cfg = load_mcp_config(self.cfg_path)
        return dict(cfg.get("servers") or {}), float(cfg.get("idle_offload_seconds", 180))

    def list_servers(self) -> str:
        servers, offload_sec = self._get_servers()
        if not servers:
            return f"No MCP servers configured. Add servers to {self.cfg_path}"
        lines = [f"MCP Connectors (auto-offload idle stdio after {int(offload_sec)}s):"]
        for name, scfg in servers.items():
            enabled = bool(scfg.get("enabled", False))
            revoked = bool(scfg.get("kill_switch", False) or scfg.get("revoked", False))
            running = name in self._procs and self._procs[name].is_alive()
            state = "REVOKED (kill-switch)" if revoked else ("ACTIVE" if running else ("ENABLED (standby 0 MB)" if enabled else "disabled"))
            lines.append(
                f"• {name} [{scfg.get('transport', 'stdio')} | {state}] — {scfg.get('description', '')}",
            )
        return "\n".join(lines)

    def _http_rpc(
        self, scfg: dict[str, Any], method: str, params: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        url = _expand_env(str(scfg.get("url", ""))).strip()
        if not url:
            return None, "MCP server is missing 'url'."
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        for hk, hv in (scfg.get("headers") or {}).items():
            expanded = _expand_env(str(hv)).strip()
            if expanded and not expanded.endswith("Bearer"):
                headers[hk] = expanded
        self._http_req_id += 1
        try:
            resp = net_policy.safe_request(
                "POST",
                url,
                policy="public",
                allow_redirects=False,
                max_bytes=2 * 1024 * 1024,
                timeout=(5.0, 20.0),
                allowed_content_types={"application/json", "text/plain"},
                headers=headers,
                json_body={"jsonrpc": "2.0", "id": self._http_req_id, "method": method, "params": params},
            )
            if resp.status_code >= 400:
                return None, f"HTTP MCP error: status {resp.status_code}"
            data = resp.json()
            if "error" in data:
                return None, str(data["error"])
            return data.get("result") or {}, None
        except net_policy.URLPolicyError as exc:
            return None, f"HTTP MCP blocked by network policy: {exc}"
        except Exception as exc:
            return None, f"HTTP MCP error: {exc}"

    def _dispatch(
        self, server_name: str, method: str, params: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        servers, _ = self._get_servers()
        scfg = servers.get(server_name)
        if not scfg:
            return None, f"Unknown MCP server '{server_name}'. Run mcp_list_servers to inspect."
        if scfg.get("kill_switch", False) or scfg.get("revoked", False):
            return None, f"MCP server '{server_name}' has been revoked via kill switch."
        if not scfg.get("enabled", False):
            return None, (
                f"MCP server '{server_name}' is currently disabled in {self.cfg_path}. "
                "Set '\"enabled\": true' to activate it."
            )

        transport = str(scfg.get("transport", "stdio")).lower()
        if transport == "http":
            return self._http_rpc(scfg, method, params)

        with self._lock:
            proc = self._procs.get(server_name)
            if proc is None:
                proc = _StdioMCPProcess(
                    server_name,
                    list(scfg.get("command") or []),
                    scfg.get("env") if isinstance(scfg.get("env"), dict) else None,
                )
                self._procs[server_name] = proc
        return proc.call(method, params)

    def list_tools(self, server_name: str) -> str:
        res, err = self._dispatch(server_name.strip(), "tools/list", {})
        if err:
            return err
        tool_list = (res or {}).get("tools") or []
        if not tool_list:
            return f"MCP server '{server_name}' exposes no tools."
        lines = [f"Tools on MCP server '{server_name}':"]
        for t in tool_list[:40]:
            lines.append(f"- {t.get('name')}: {str(t.get('description', ''))[:120]}")
        return "\n".join(lines)

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        approval_cb: ApprovalCallback | None = None,
        server_annotation_hint: dict[str, Any] | None = None,
    ) -> str:
        """Executes an MCP tool call.
        Security Invariant (Phase 4 Item 10):
          - NEVER classifies tools as read-only by name prefix (get_/list_/read_/search_).
          - ALL unknown MCP tools default to explicit argument-bound user approval.
          - Server-declared annotations (e.g. readOnlyHint) are displayed ONLY as an
            untrusted hint, NEVER used as sole authorization to bypass approval.
        """
        servers, _ = self._get_servers()
        sname = server_name.strip()
        tname = tool_name.strip()
        scfg = servers.get(sname)
        if not scfg:
            return f"Error: Unknown MCP server '{server_name}'."
        if scfg.get("kill_switch", False) or scfg.get("revoked", False):
            return f"Error: MCP server '{server_name}' has been revoked via kill switch."
        if not scfg.get("enabled", False):
            return f"Error: MCP server '{server_name}' is disabled in {self.cfg_path}."

        # Only explicitly user-allowlisted tool names in mcp-servers.json may skip approval
        explicit_allow = {str(x).strip() for x in (scfg.get("explicit_auto_approve_tools") or [])}
        final_args = {"server_name": sname, "tool_name": tname, "arguments": arguments or {}}

        token: approval.ApprovalToken | None = None
        if tname not in explicit_allow:
            hint_note = ""
            if isinstance(server_annotation_hint, dict) and "readOnlyHint" in server_annotation_hint:
                hint_note = (
                    f"Server annotation hint (unverified): readOnlyHint={bool(server_annotation_hint['readOnlyHint'])}"
                )
            token, deny_err = approval.request_bound_approval(
                "mcp_call_tool",
                final_args,
                approval_cb,
                title=f"Approve External MCP Tool Call ({sname}.{tname})?",
                target=f"{sname}.{tname}",
                extra_warning=hint_note or "Unknown MCP tools always require explicit argument-bound approval.",
            )
            if deny_err:
                return deny_err

            ok, err = approval.verify_and_consume(token, "mcp_call_tool", final_args)
            if not ok:
                return err

        res, err = self._dispatch(
            sname,
            "tools/call",
            {"name": tname, "arguments": arguments or {}},
        )
        if err:
            return f"MCP error ({server_name}.{tool_name}): {err}"

        content = (res or {}).get("content") or []
        if isinstance(content, list):
            texts = [
                str(block.get("text", json.dumps(block, ensure_ascii=False)))
                for block in content
                if isinstance(block, dict)
            ]
            out = "\n".join(texts).strip()
        else:
            out = json.dumps(res, ensure_ascii=False)
        return (out[:4000] + "\n...[truncated]") if len(out) > 4000 else (out or "(Empty MCP response)")

    def stop_server(self, server_name: str) -> bool:
        """Immediately terminates a running stdio MCP server process (kill switch)."""
        with self._lock:
            proc = self._procs.pop(server_name, None)
        if proc is not None:
            proc.stop()
            return True
        return False

    def offload_idle_servers(self, force: bool = False) -> int:
        """Stops idle local stdio MCP servers to reclaim 100% of their RAM."""
        _, offload_sec = self._get_servers()
        now = time.time()
        stopped = 0
        with self._lock:
            for name in list(self._procs.keys()):
                proc = self._procs[name]
                if force or (proc.is_alive() and (now - proc.last_used) >= offload_sec):
                    proc.stop()
                    stopped += 1
        return stopped


_DEFAULT_MCP: MCPManager | None = None


def get_mcp() -> MCPManager:
    global _DEFAULT_MCP
    if _DEFAULT_MCP is None:
        _DEFAULT_MCP = MCPManager()
    return _DEFAULT_MCP
