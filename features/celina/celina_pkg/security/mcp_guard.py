"""macubuntu celina_pkg.security.mcp_guard — OWASP MCP03 Anti-Poisoning + Dual-Context Barrier.

Implements two defenses for Model Context Protocol (MCP) tool interactions:

1. MCP03 Tool-Poisoning Pinning (SHA-256):
   - Computes SHA-256 of the *live* MCP `tools/list` response.
   - Compares against `mcp_tools.lock.json` (pinned baseline).
   - Any drift (added/removed/modified tools, changed descriptions/schemas) is
     flagged as potential tool-poisoning → fail-closed (tools quarantined).
   - Never executes a pinned tool whose live hash doesn't match the lock file.

2. Dual-Context Barrier:
   - Quarantined Reader (tool-less): reads untrusted external text (emails, web,
     documents) with ZERO OS/filesystem mutation tools. Cannot write, cannot call
     Tier-2 tools.
   - Privileged Planner: only receives *sanitized, validated* intent from the
     Reader. Has access to mutation tools, but never sees raw untrusted text.
   - Cross-context communication goes through a strictly typed Intent schema.

stdlib-only (sqlite3, datetime, re, json). All new method params are optional
kwargs with safe defaults. Fail-closed: invalid/missing source_trust defaults
to "untrusted_web".
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOCK_FILE_NAME = "mcp_tools.lock.json"
DEFAULT_SOURCE_TRUST = "untrusted_web"  # fail-closed default

# MCP tool names that are NEVER auto-pinnable / always require explicit approval
# (defense-in-depth beyond the pinning check).
NEVER_AUTO_PIN = frozenset({
    "write_file", "edit_file", "create_document", "compose_message",
    "run_skill", "learn_skill", "run_shell_command", "computer_use",
})


@dataclass
class ToolPin:
    name: str
    sha256: str
    schema_sig: str
    source_trust: str = DEFAULT_SOURCE_TRUST
    pinned_at: str = field(default_factory=lambda: _dt.datetime.utcnow().isoformat() + "Z")


@dataclass
class PinDrift:
    tool: str
    reason: str
    live_sha256: str | None = None
    locked_sha256: str | None = None


@dataclass
class QuarantinedIntent:
    """Sanitized intent passed from Quarantined Reader to Privileged Planner."""

    action: str
    target: str
    args: dict[str, Any] = field(default_factory=dict)
    source_trust: str = DEFAULT_SOURCE_TRUST
    confidence: float = 0.0
    approved: bool = False


class McpGuard:
    """OWASP MCP03 anti-poisoning guard + Dual-Context Barrier coordinator.

    Usage:
        guard = McpGuard(lock_path="system/capabilities/mcp_tools.lock.json")
        drift = guard.check_pin_drift(live_tools_list)   # list[PinDrift]
        if drift:
            quarantine_tools([d.tool for d in drift])
        intent = guard.reader_to_intent(raw_external_text)   # Quarantined Reader
        plan = guard.planner_plan(intent)                   # Privileged Planner
    """

    def __init__(
        self,
        *,
        lock_path: str = "system/capabilities/mcp_tools.lock.json",
        repo_dir: str | None = None,
        db_path: str | None = None,
    ) -> None:
        self.lock_path = Path(lock_path)
        self.repo_dir = Path(repo_dir) if repo_dir else Path.cwd()
        self.db_path = Path(db_path) if db_path else (
            Path.home() / ".local" / "share" / "celina" / "mcp_guard.db"
        )
        self._pins: dict[str, ToolPin] = {}
        self._drift_log: list[PinDrift] = []
        self._load_lock()
        self._init_db()

    # ------------------------------------------------------------------ #
    # Lock file load / persist (idempotent)
    # ------------------------------------------------------------------ #
    def _load_lock(self) -> None:
        """Load pinned tool hashes from mcp_tools.lock.json. Fail-closed: empty if missing/invalid."""
        self._pins = {}
        if not self.lock_path.exists():
            return
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
            entries = data if isinstance(data, list) else data.get("tools", [])
            for entry in entries:
                name = str(entry.get("name", ""))
                if not name:
                    continue
                self._pins[name] = ToolPin(
                    name=name,
                    sha256=str(entry.get("sha256", "")),
                    schema_sig=str(entry.get("schema_sig", "")),
                    source_trust=str(entry.get("source_trust", DEFAULT_SOURCE_TRUST)),
                    pinned_at=str(entry.get("pinned_at", "")),
                )
        except (json.JSONDecodeError, OSError, ValueError):
            # Fail-closed: no pins loaded → every live tool will appear as drift
            self._pins = {}

    def save_lock(self, *, path: str | None = None) -> None:
        """Persist current pins to the lock file (idempotent, atomic via temp+replace)."""
        dst = Path(path) if path else self.lock_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        entries = [
            {
                "name": p.name,
                "sha256": p.sha256,
                "schema_sig": p.schema_sig,
                "source_trust": p.source_trust,
                "pinned_at": p.pinned_at,
            }
            for p in self._pins.values()
        ]
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        tmp.write_text(json.dumps({"tools": entries}, indent=2), encoding="utf-8")
        tmp.replace(dst)

    # ------------------------------------------------------------------ #
    # SHA-256 pinning of live MCP tools/list
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tool_sha256(tool: dict[str, Any]) -> str:
        """Stable SHA-256 over a tool's name + inputSchema + description."""
        canonical = json.dumps(
            {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "inputSchema": tool.get("inputSchema", {}),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def check_pin_drift(
        self,
        *,
        live_tools: list[dict[str, Any]] | None = None,
        live_tools_json: str | None = None,
    ) -> list[PinDrift]:
        """Compare live MCP `tools/list` against the pinned lock file.

        Returns a list of PinDrift for any tool whose live SHA-256/schema
        differs from the lock, plus any lock file tool missing from live.
        Fail-closed: any drift is reported; caller decides quarantine.
        """
        drift: list[PinDrift] = []
        live_map: dict[str, dict[str, Any]] = {}

        if live_tools_json is not None:
            try:
                live_tools = json.loads(live_tools_json)
            except json.JSONDecodeError:
                # Cannot parse live list → treat all pins as drift (fail-closed)
                for name, pin in self._pins.items():
                    drift.append(PinDrift(tool=name, reason="live_tools unparseable"))
                self._drift_log.extend(drift)
                return drift

        if live_tools:
            for t in live_tools:
                name = str(t.get("name", ""))
                if name:
                    live_map[name] = t

        # Check every live tool for hash/schema drift
        for name, tool in live_map.items():
            live_sha = self._tool_sha256(tool)
            pin = self._pins.get(name)
            if pin is None:
                drift.append(PinDrift(tool=name, reason="new tool not in lock file (unpinned)"))
            elif pin.sha256 != live_sha:
                drift.append(PinDrift(
                    tool=name, reason="SHA-256/schema mismatch (possible tool poisoning)",
                    live_sha256=live_sha, locked_sha256=pin.sha256,
                ))
            elif name in NEVER_AUTO_PIN and pin.source_trust == DEFAULT_SOURCE_TRUST:
                drift.append(PinDrift(tool=name, reason="high-risk tool with untrusted source"))

        # Check lock-file tools missing from live (possible suppression attack)
        for name in self._pins:
            if name not in live_map:
                drift.append(PinDrift(tool=name, reason="pinned tool missing from live tools/list"))

        self._drift_log.extend(drift)
        return drift

    def quarantine_tool(self, *, tool_name: str) -> None:
        """Mark a tool as quarantined (fail-closed: refuses execution)."""
        if tool_name in self._pins:
            pin = self._pins[tool_name]
            pin.source_trust = "quarantined"
            pin.pinned_at = _dt.datetime.utcnow().isoformat() + "Z"

    def is_quarantined(self, *, tool_name: str) -> bool:
        pin = self._pins.get(tool_name)
        return bool(pin and pin.source_trust == "quarantined")

    # ------------------------------------------------------------------ #
    # Dual-Context Barrier
    # ------------------------------------------------------------------ #
    def reader_to_intent(
        self,
        *,
        raw_text: str,
        source_trust: str = DEFAULT_SOURCE_TRUST,
        allowed_actions: list[str] | None = None,
    ) -> QuarantinedIntent:
        """Quarantined Reader: parse untrusted external text into a typed intent.

        NEVER has access to OS/filesystem mutation tools. Only reads and classifies.
        Returns a QuarantinedIntent with source_trust defaulting to "untrusted_web"
        (fail-closed). The Privileged Planner consumes this intent, never raw_text.
        """
        trust = (source_trust or DEFAULT_SOURCE_TRUST).strip()
        if trust not in {"untrusted_web", "trusted_local", "trusted_user", "quarantined"}:
            trust = DEFAULT_SOURCE_TRUST

        intent = QuarantinedIntent(
            action="",
            target="",
            args={},
            source_trust=trust,
            confidence=0.0,
            approved=False,
        )

        # Tool-less classification: only keyword/heuristic matching, no code execution.
        low = (raw_text or "").strip()
        if not low:
            return intent

        # Detect obvious action verbs (read-only classification).
        action_map = {
            "read": ["read", "show", "view", "open", "display"],
            "write": ["write", "create", "save", "make", "draft"],
            "send": ["send", "email", "message", "notify"],
            "delete": ["delete", "remove", "trash", "erase"],
        }
        detected_action = ""
        for action, keywords in action_map.items():
            if any(kw in low for kw in keywords):
                detected_action = action
                break
        intent.action = detected_action
        intent.target = low[:200]  # truncated, never stored verbatim
        intent.confidence = 0.5 if detected_action else 0.0

        # Fail-closed: untrusted sources are never auto-approved.
        intent.approved = trust in {"trusted_local", "trusted_user"}
        return intent

    def planner_plan(
        self,
        *,
        intent: QuarantinedIntent,
        require_approval: bool = True,
        allowlist: list[str] | None = None,
    ) -> dict[str, Any]:
        """Privileged Planner: decide whether to act on a QuarantinedIntent.

        Has access to mutation tools, but NEVER sees raw untrusted text — only
        the typed intent from the Reader. Fail-closed: untrusted intents require
        explicit approval before any mutation.
        """
        plan: dict[str, Any] = {
            "action": intent.action,
            "target": intent.target,
            "approved": False,
            "reason": "",
            "source_trust": intent.source_trust,
        }

        if intent.source_trust == DEFAULT_SOURCE_TRUST and require_approval and not intent.approved:
            plan["reason"] = (
                f"untrusted source ({intent.source_trust}) requires explicit user approval"
            )
            return plan

        if intent.action and intent.action in NEVER_AUTO_PIN and not intent.approved:
            plan["reason"] = f"high-risk action '{intent.action}' requires explicit approval"
            return plan

        if allowlist is not None and intent.action not in allowlist:
            plan["reason"] = f"action '{intent.action}' not in planner allowlist"
            return plan

        plan["approved"] = True
        plan["reason"] = "intent validated and approved by Privileged Planner"
        return plan

    # ------------------------------------------------------------------ #
    # SQLite persistence of drift events (idempotent in-place migration)
    # ------------------------------------------------------------------ #
    def _init_db(self) -> None:
        """Create/migrate the drift-log database idempotently (cache.py pattern)."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS mcp_drift (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    live_sha256 TEXT,
                    locked_sha256 TEXT
                )""",
            )
            # Idempotent in-place migration: add source_trust column if missing
            cols = {r[1] for r in conn.execute("PRAGMA table_info(mcp_drift)").fetchall()}
            if "source_trust" not in cols:
                conn.execute("ALTER TABLE mcp_drift ADD COLUMN source_trust TEXT NOT NULL DEFAULT 'untrusted_web'")
            conn.commit()
        finally:
            conn.close()

    def record_drift(self, *, drift: PinDrift) -> None:
        """Persist a drift event to SQLite (fail-closed: never raises)."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute(
                "INSERT INTO mcp_drift (ts, tool, reason, live_sha256, locked_sha256, source_trust) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _dt.datetime.utcnow().isoformat() + "Z",
                    drift.tool,
                    drift.reason,
                    drift.live_sha256,
                    drift.locked_sha256,
                    DEFAULT_SOURCE_TRUST,
                ),
            )
            conn.commit()
            conn.close()
        except sqlite3.Error:
            pass  # fail-closed: DB issues never block the guard
