"""
Celina v5 Proactive Daemon (`proactive/daemon.py`)

Event-driven world model consuming eBPF sensor events and system signals.
Strict safety boundary:
  - ONLY actions in `SAFE_PROACTIVE_ALLOWLIST` (`set_focus_mode`, `set_dnd`,
    `set_brightness`) may emit direct execution actions.
  - ALL other matched rule actions are downgraded automatically into non-executing
    `{"suggestion": ...}` cards surfaced to Dynamic Island over D-Bus.
  - Per-rule cooldown prevents rapid duplicate triggers on continuous streams.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import signal
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ..ebpf_sensor import DEFAULT_EBPF_STREAM, EbpfEventClient

log = logging.getLogger("celina.proactive")

DEFAULT_RULES_PATH = Path(__file__).resolve().parent / "rules.json"
SAFE_PROACTIVE_ALLOWLIST: frozenset[str] = frozenset({"set_focus_mode", "set_dnd", "set_brightness"})


@dataclasses.dataclass
class WorldState:
    battery_pct: int = 100
    on_ac: bool = True
    active_app: str = ""
    calendar_event: Optional[str] = None
    focus_mode: str = "none"
    cpu_pressure: float = 0.0
    memory_pressure: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class ProactiveDaemon:
    def __init__(
        self,
        rules_path: Path = DEFAULT_RULES_PATH,
        state_publisher: Optional[Callable[[dict[str, Any]], None]] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        raw = json.loads(Path(rules_path).read_text(encoding="utf-8"))
        self.rules: list[dict[str, Any]] = list(raw.get("rules") or [])
        self.publish = state_publisher or (lambda payload: log.info("Proactive publish: %s", payload))
        self.state = WorldState()
        self._clock = clock
        self._last_fired: dict[str, float] = {}
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def ingest(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """Update world model from a kernel/system event and evaluate proactive rules."""
        kind = str(event.get("kind") or "")
        if kind == "power":
            if "battery_pct" in event:
                self.state.battery_pct = int(event["battery_pct"])
            if "on_ac" in event:
                self.state.on_ac = bool(event["on_ac"])
        elif kind == "focus":
            if "app_id" in event:
                self.state.active_app = str(event["app_id"])
            if "focus_mode" in event:
                self.state.focus_mode = str(event["focus_mode"])
        elif kind == "calendar":
            self.state.calendar_event = event.get("title")
        elif kind == "pressure":
            if "cpu" in event:
                self.state.cpu_pressure = float(event["cpu"])
            if "memory" in event:
                self.state.memory_pressure = float(event["memory"])
        elif kind == "ebpf_exec":
            comm = str(event.get("comm") or "").strip()
            if comm:
                self.state.active_app = comm
        return self._evaluate_rules()

    def _evaluate_rules(self) -> list[dict[str, Any]]:
        emitted: list[dict[str, Any]] = []
        now = self._clock()
        for idx, rule in enumerate(self.rules):
            cond = rule.get("when") or {}
            if not self._matches(cond):
                continue
            rule_id = str(rule.get("id") or f"rule-{idx}")
            cooldown = float(rule.get("cooldown_sec", 0.0))
            last_ts = self._last_fired.get(rule_id)
            if cooldown > 0.0 and last_ts is not None and (now - last_ts) < cooldown:
                continue
            self._last_fired[rule_id] = now
            out = self._act(rule.get("then") or {}, rule_id=rule_id)
            emitted.append(out)
        return emitted

    def _matches(self, cond: dict[str, Any]) -> bool:
        if not cond:
            return False
        for key, expected in cond.items():
            actual = getattr(self.state, key, None)
            if isinstance(expected, dict):
                if actual is None:
                    return False
                if "lt" in expected and not (actual < expected["lt"]):
                    return False
                if "lte" in expected and not (actual <= expected["lte"]):
                    return False
                if "gt" in expected and not (actual > expected["gt"]):
                    return False
                if "gte" in expected and not (actual >= expected["gte"]):
                    return False
            elif actual != expected:
                return False
        return True

    def _act(self, action: dict[str, Any], *, rule_id: str) -> dict[str, Any]:
        # Support both `{"tool": ...}` and `{"suggestion": {"tool": ...}}` rule formats
        if "suggestion" in action and "tool" not in action:
            payload = {"kind": "suggestion", "rule_id": rule_id, "suggestion": action["suggestion"]}
            self.publish(payload)
            return payload

        tool = str(action.get("tool") or "").strip()
        if tool in SAFE_PROACTIVE_ALLOWLIST:
            payload = {
                "kind": "auto_action",
                "rule_id": rule_id,
                "tool": tool,
                "arguments": dict(action.get("arguments") or {}),
            }
            if tool == "set_focus_mode" and "mode" in payload["arguments"]:
                self.state.focus_mode = str(payload["arguments"]["mode"])
        else:
            payload = {"kind": "suggestion", "rule_id": rule_id, "suggestion": dict(action)}

        self.publish(payload)
        return payload

    def poll_ebpf_once(self, stream_path: Path = DEFAULT_EBPF_STREAM) -> list[dict[str, Any]]:
        client = EbpfEventClient(stream_path)
        emitted: list[dict[str, Any]] = []
        for ev in client.read_recent_events(limit=25):
            if "comm" in ev:
                emitted.extend(self.ingest({"kind": "ebpf_exec", "comm": ev["comm"]}))
        return emitted


def main() -> int:
    parser = argparse.ArgumentParser(description="Celina v5 Proactive World-Model Daemon")
    parser.add_argument("--rules", default=str(DEFAULT_RULES_PATH), help="Path to proactive rules.json")
    parser.add_argument("--once", action="store_true", help="Evaluate one poll cycle and exit")
    parser.add_argument("--interval", type=float, default=5.0, help="Poll interval seconds")
    args = parser.parse_args()

    daemon = ProactiveDaemon(Path(args.rules))
    signal.signal(signal.SIGTERM, lambda *_a: daemon.request_stop())
    signal.signal(signal.SIGINT, lambda *_a: daemon.request_stop())

    if args.once:
        out = daemon.poll_ebpf_once()
        print(json.dumps({"state": daemon.state.to_dict(), "emitted": out}, indent=2))
        return 0

    try:
        while not daemon._stop:
            daemon.poll_ebpf_once()
            time.sleep(max(1.0, float(args.interval)))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
