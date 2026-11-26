"""macubuntu celina_pkg.shortcuts.triggers — Event-driven Shortcut trigger daemon.

Fires .cshortcut.yaml Shortcuts deterministically on OS events with zero LLM:
  - Power/AC (UPower): unplugged, AC connected, battery < 20%
  - Network (NetworkManager): joined Home/Office/Public Wi-Fi
  - Audio/Bluetooth (BlueZ): headphones connected
  - Filesystem (inotify): PDF dropped in ~/Downloads -> auto-OCR + index
  - Time/Calendar (systemd timers + calendar_bridge): 5 min before meeting

Graceful degradation: if dbus/upower/inotify are unavailable, each watcher logs
and skips rather than crashing. Zero generative LLM, no PyTorch.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("celina.shortcuts.triggers")

try:
    import dbus  # type: ignore
    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False

try:
    from inotify.adapters import Inotify  # type: ignore
    INOTIFY_AVAILABLE = True
except ImportError:
    INOTIFY_AVAILABLE = False


@dataclass
class TriggerBinding:
    event_type: str
    condition: str
    shortcut_id: str
    bound_inputs: dict[str, Any] = field(default_factory=dict)
    cooldown_sec: float = 300.0
    last_fired: float = 0.0


@dataclass
class TriggerEvent:
    event_type: str
    detail: dict[str, Any]
    ts: float = field(default_factory=time.time)


class TriggerDaemon:
    def __init__(self, engine: Any, *, bindings: list[TriggerBinding] | None = None) -> None:
        self.engine = engine
        self.bindings: list[TriggerBinding] = list(bindings or [])
        self._watchers: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    def add_binding(self, binding: TriggerBinding) -> None:
        self.bindings.append(binding)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        self._watchers = [
            asyncio.create_task(self._watch_power()),
            asyncio.create_task(self._watch_network()),
            asyncio.create_task(self._watch_bluetooth()),
            asyncio.create_task(self._watch_filesystem()),
            asyncio.create_task(self._watch_calendar()),
        ]
        log.info("Trigger daemon started (%d bindings, %d watchers)",
                 len(self.bindings), len(self._watchers))

    async def stop(self) -> None:
        self._stop.set()
        for w in self._watchers:
            w.cancel()
        await asyncio.gather(*self._watchers, return_exceptions=True)
        log.info("Trigger daemon stopped")

    async def run_forever(self) -> None:
        await self.start()
        await self._stop.wait()
        await self.stop()

    async def _maybe_dispatch(self, event: TriggerEvent) -> None:
        for b in self.bindings:
            if b.event_type != event.event_type:
                continue
            if time.time() - b.last_fired < b.cooldown_sec:
                continue
            if not self._matches(b.condition, event.detail):
                continue
            inputs = dict(b.bound_inputs)
            inputs.update(event.detail.get("inputs", {}))
            spec = self.engine.get_spec(b.shortcut_id)
            if spec is None:
                log.warning("Trigger %s references unknown shortcut %s",
                            event.event_type, b.shortcut_id)
                continue
            b.last_fired = time.time()
            asyncio.create_task(self.engine.run(spec, inputs))
            log.info("Dispatched shortcut %s on %s event", b.shortcut_id, event.event_type)

    @staticmethod
    def _coerce_value(v: str) -> Any:
        low = v.lower()
        if low in ("true", "false"):
            return low == "true"
        if low in ("none", "null", ""):
            return None
        try:
            if "." in v:
                return float(v)
            return int(v)
        except ValueError:
            return v

    @staticmethod
    def _matches(condition: str, detail: dict[str, Any]) -> bool:
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if op in condition:
                k, v = [p.strip() for p in condition.split(op, 1)]
                actual = detail.get(k)
                try:
                    target = TriggerDaemon._coerce_value(v)
                    # Coerce actual to match target type for bool comparisons
                    if isinstance(target, bool) and isinstance(actual, str):
                        actual = actual.lower() == "true"
                    if isinstance(actual, bool) and isinstance(target, str):
                        target = target.lower() == "true"
                    if op == "==":
                        return actual == target
                    if op == "!=":
                        return actual != target
                    if op == ">":
                        return float(actual) > float(target)
                    if op == "<":
                        return float(actual) < float(target)
                    if op == ">=":
                        return float(actual) >= float(target)
                    if op == "<=":
                        return float(actual) <= float(target)
                except (TypeError, ValueError):
                    return False
        return False

    async def _watch_power(self) -> None:
        while not self._stop.is_set():
            try:
                bat = self._read_battery()
                if bat is not None:
                    await self._maybe_dispatch(TriggerEvent(
                        "power",
                        {"battery_pct": bat["pct"], "on_ac": bat["on_ac"]},
                    ))
            except Exception as exc:
                log.debug("power watcher: %s", exc)
            await asyncio.sleep(5)

    def _read_battery(self) -> dict[str, Any] | None:
        try:
            for ps in os.listdir("/sys/class/power_supply"):
                cap_path = "/sys/class/power_supply/" + ps + "/capacity"
                online_path = "/sys/class/power_supply/" + ps + "/online"
                if os.path.exists(cap_path):
                    with open(cap_path) as fh:
                        pct = int(fh.read().strip())
                    on_ac = False
                    if os.path.exists(online_path):
                        with open(online_path) as fh:
                            on_ac = fh.read().strip() == "1"
                    return {"pct": pct, "on_ac": on_ac}
        except (OSError, ValueError):
            pass
        return None

    async def _watch_network(self) -> None:
        last_ssid: str | None = None
        while not self._stop.is_set():
            try:
                ssid = self._read_wifi_ssid()
                if ssid is not None and ssid != last_ssid:
                    last_ssid = ssid
                    await self._maybe_dispatch(TriggerEvent(
                        "network",
                        {"ssid": ssid},
                    ))
            except Exception as exc:
                log.debug("network watcher: %s", exc)
            await asyncio.sleep(10)

    def _read_wifi_ssid(self) -> str | None:
        try:
            out = subprocess.run(
                ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
                capture_output=True, text=True, timeout=5,
            )
            for line in out.stdout.splitlines():
                if line.startswith("yes:"):
                    return line.split(":", 1)[1] or None
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
        return None

    async def _watch_bluetooth(self) -> None:
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["bluetoothctl", "info"],
                    capture_output=True, text=True, timeout=5,
                )
                if "Connected: yes" in out.stdout:
                    await self._maybe_dispatch(TriggerEvent(
                        "bluetooth",
                        {"device_connected": True},
                    ))
            except (FileNotFoundError, subprocess.SubprocessError):
                pass
            await asyncio.sleep(10)

    async def _watch_filesystem(self) -> None:
        watch_dir = Path.home() / "Downloads"
        if not watch_dir.exists():
            return
        if INOTIFY_AVAILABLE:
            try:
                from inotify.adapters import Inotify
                inotify = Inotify()
                inotify.add_watch(str(watch_dir))
                while not self._stop.is_set():
                    await asyncio.sleep(1)
                    for event in inotify.event_gen(yield_nones=False):
                        if event is None:
                            continue
                        _, type_names, path, filename = event
                        if "IN_CLOSE_WRITE" in type_names and filename and filename.endswith(".pdf"):
                            await self._maybe_dispatch(TriggerEvent(
                                "filesystem",
                                {"path": str(Path(path) / filename),
                                 "inputs": {"file": str(Path(path) / filename)}},
                            ))
            except Exception as exc:
                log.debug("filesystem watcher: %s", exc)
        else:
            seen: dict[str, float] = {}
            while not self._stop.is_set():
                try:
                    for f in watch_dir.glob("*.pdf"):
                        mtime = f.stat().st_mtime
                        if f.name not in seen:
                            seen[f.name] = mtime
                        elif seen[f.name] != mtime and time.time() - mtime < 10:
                            await self._maybe_dispatch(TriggerEvent(
                                "filesystem",
                                {"path": str(f), "inputs": {"file": str(f)}},
                            ))
                            seen[f.name] = mtime
                except OSError:
                    pass
                await asyncio.sleep(3)

    async def _watch_calendar(self) -> None:
        while not self._stop.is_set():
            try:
                import json
                repo_root = Path(__file__).resolve().parents[2]
                out = subprocess.run(
                    ["python3", "-c",
                     "from celina_pkg.calendar_bridge import upcoming_events; "
                     "print(json.dumps(upcoming_events(minutes_ahead=10)))"],
                    capture_output=True, text=True, timeout=10,
                    cwd=str(repo_root),
                )
                if out.returncode == 0 and out.stdout.strip():
                    events = json.loads(out.stdout)
                    for ev in events:
                        await self._maybe_dispatch(TriggerEvent(
                            "calendar",
                            {"title": ev.get("title", ""),
                             "when": ev.get("when", ""), "inputs": {}},
                        ))
            except Exception as exc:
                log.debug("calendar watcher: %s", exc)
            await asyncio.sleep(60)
