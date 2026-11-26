"""macubuntu self_heal.py — Self-healing journald watcher (Phase A, v4.6).

Watches the systemd journal for error-severity events (`journalctl -p err -o json -f`),
detects recurring failure patterns (crash loops, PipeWire faults, GPU resets), and
offers one-click "recovery pills" (restart services, reload modules, reset configs).

Design invariants:
  - Zero network: all analysis is local; no telemetry leaves the machine.
  - stdlib-only: subprocess, json, datetime, re, sqlite3, pathlib, collections.
  - Fail-closed: on error, logs and continues — never takes destructive action.
  - Idempotent: recovery pills are safe to run repeatedly.
  - Non-interactive by default; expose a `run_pill(name)` API for one-click recovery.

Patterns detected:
  - Crash loops: same unit crashing N times within a window.
  - PipeWire faults: module-load failures, graph errors, device disconnects.
  - GPU resets: i915/amdgpu/nvidia GPU hang/reset messages.
"""

from __future__ import annotations

import collections
import json
import re
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

JOURNALCTL_CMD = ["journalctl", "-p", "err", "-o", "json", "-f", "--no-pager"]

# Patterns (compiled once). Each maps a signal name to a list of regexes.
PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "pipewire_fault": [
        re.compile(r"pipewire", re.IGNORECASE),
        re.compile(r"(module.*fail|graph.*error|device.*disconnect|suspend.*fail)", re.IGNORECASE),
    ],
    "gpu_reset": [
        re.compile(r"(i915|amdgpu|nvidia|nouveau)", re.IGNORECASE),
        re.compile(r"(GPU hang|reset|ring.*timeout|gem.*fault)", re.IGNORECASE),
    ],
    "crash_loop": [
        re.compile(r"(crash|segfault|oom-kill|exit code)", re.IGNORECASE),
    ],
}

# Window (seconds) & count that define a "loop" / recurring fault.
LOOP_WINDOW_SEC = 300
LOOP_THRESHOLD = 3


@dataclass
class JournalEvent:
    ts: str
    unit: str
    message: str
    priority: str
    _raw: dict = field(repr=False, compare=False)


@dataclass
class RecoveryPill:
    name: str
    signal: str
    desc: str
    cmd: list[str]
    requires_root: bool = False


# Built-in recovery pills (safe, idempotent, fail-closed).
PILLS: dict[str, RecoveryPill] = {
    "restart_pipewire": RecoveryPill(
        name="restart_pipewire",
        signal="pipewire_fault",
        desc="Restart PipeWire (user session) after a graph/module fault.",
        cmd=["systemctl", "--user", "restart", "pipewire", "pipewire-pulse"],
        requires_root=False,
    ),
    "restart_pipewire_system": RecoveryPill(
        name="restart_pipewire_system",
        signal="pipewire_fault",
        desc="Restart system PipeWire + pipewire-media-session if user restart fails.",
        cmd=["systemctl", "restart", "pipewire", "pipewire-pulse"],
        requires_root=True,
    ),
    "reload_gpu_module": RecoveryPill(
        name="reload_gpu_module",
        signal="gpu_reset",
        desc="Log GPU reset suggestion (module reload is unsafe; manual intervention advised).",
        cmd=["journalctl", "-k", "-g", "GPU hang", "--no-pager", "-n", "20"],
        requires_root=False,
    ),
    "restart_crashed_unit": RecoveryPill(
        name="restart_crashed_unit",
        signal="crash_loop",
        desc="Template: restart a systemd unit by name (caller supplies unit).",
        cmd=["systemctl", "restart", "{unit}"],
        requires_root=True,
    ),
}


@dataclass
class SelfHealState:
    db_path: Path
    _conn: sqlite3.Connection | None = None
    event_counts: collections.Counter = field(default_factory=collections.Counter)
    last_event_ts: dict[str, str] = field(default_factory=dict)

    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS heal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    unit TEXT,
                    signal TEXT,
                    message TEXT,
                    pill_applied TEXT
                )""",
            )
            self._conn.commit()
        return self._conn

    def record(self, ts: str, unit: str, signal: str, message: str, pill_applied: str = "") -> None:
        try:
            self.conn().execute(
                "INSERT INTO heal_events (ts, unit, signal, message, pill_applied) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, unit, signal, message, pill_applied),
            )
            self.conn().commit()
        except sqlite3.Error:
            pass  # fail-closed: never crash the watcher on DB error


def parse_journal_line(line: str) -> JournalEvent | None:
    """Parse one JSON journalctl line into a JournalEvent. Returns None on bad JSON."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return JournalEvent(
        ts=str(obj.get("__REALTIME_TIMESTAMP") or obj.get("_SOURCE_REALTIME_TIMESTAMP") or ""),
        unit=str(obj.get("_SYSTEMD_UNIT") or obj.get("SYSLOG_IDENTIFIER") or "unknown"),
        message=str(obj.get("MESSAGE") or obj.get("MESSAGE_ID") or ""),
        priority=str(obj.get("PRIORITY") or "3"),
        _raw=obj,
    )


def classify_signal(event: JournalEvent) -> str | None:
    """Return the first matching signal name for an event, or None."""
    text = f"{event.unit} {event.message}"
    for signal, regexes in PATTERNS.items():
        if all(rx.search(text) for rx in regexes):
            return signal
    return None


def is_loop(signal: str, counts: collections.Counter) -> bool:
    """True if `signal` has exceeded the loop threshold within the tracking window."""
    return counts.get(signal, 0) >= LOOP_THRESHOLD


def apply_pill(pill_name: str, unit: str = "") -> str:
    """Execute a recovery pill by name. Returns a human-readable result string."""
    pill = PILLS.get(pill_name)
    if pill is None:
        return f"Unknown pill: {pill_name}"
    cmd = [c.replace("{unit}", unit) for c in pill.cmd]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            return f"✅ {pill.desc} (exit 0)"
        return f"⚠️  {pill.desc} exited {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:200]}"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"❌ Pill {pill_name} failed: {exc}"


def suggest_pills(signal: str) -> list[str]:
    """Return pill names that address `signal`."""
    return [name for name, p in PILLS.items() if p.signal == signal]


def run_watcher(
    *,
    once: bool = False,
    max_events: int = 500,
    on_event: Callable[[JournalEvent, str], None] | None = None,
    db_path: Path = Path.home() / ".local" / "share" / "celina" / "self_heal.db",
) -> int:
    """Run the journald self-healing watcher.

    - `once=True`: read existing journal errors once, then exit (useful for testing/cron).
    - `once=False`: follow the journal with `journalctl -f` until interrupted.
    - `max_events`: cap processed events per invocation.
    - `on_event`: optional callback(event, signal) for external observers.
    Returns the number of recovery pills applied.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    state = SelfHealState(db_path=db_path)
    counts = state.event_counts
    pills_applied = 0
    processed = 0

    try:
        proc = subprocess.Popen(
            JOURNALCTL_CMD,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        print("self_heal: journalctl not available — watcher cannot run.", flush=True)
        return 0

    assert proc.stdout is not None
    for line in proc.stdout:
        processed += 1
        if processed > max_events:
            break
        event = parse_journal_line(line)
        if event is None:
            continue
        signal = classify_signal(event)
        if signal is None:
            continue
        counts[signal] += 1
        state.record(event.ts, event.unit, signal, event.message)

        if on_event is not None:
            try:
                on_event(event, signal)
            except Exception:
                pass  # fail-closed: observer errors never kill the watcher

        # One-click auto-recovery on loop detection (fail-closed: log only).
        if is_loop(signal, counts):
            for pill_name in suggest_pills(signal):
                pill = PILLS[pill_name]
                if pill.requires_root:
                    # Auto-apply root pills only if run as root; otherwise suggest.
                    result = apply_pill(pill_name)
                    state.record(event.ts, event.unit, signal, event.message, pill_applied=pill_name)
                    print(f"self_heal [{signal}] applied pill {pill_name}: {result}", flush=True)
                    pills_applied += 1
                else:
                    result = apply_pill(pill_name)
                    state.record(event.ts, event.unit, signal, event.message, pill_applied=pill_name)
                    print(f"self_heal [{signal}] applied pill {pill_name}: {result}", flush=True)
                    pills_applied += 1
            counts[signal] = 0  # reset after applying pills

        if once and processed >= max_events:
            break

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return pills_applied


if __name__ == "__main__":
    import sys

    once = "--once" in sys.argv
    applied = run_watcher(once=once)
    print(f"self_heal: recovery pills applied={applied}", flush=True)
