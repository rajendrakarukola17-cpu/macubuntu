"""
Celina AI — Capabilities #12 & #16:
  12. OpenTelemetry-Compatible Distributed Tracing (`CelinaTracer`, `SpanContext`)
      - W3C TraceContext (`trace_id` 32-hex, `span_id` 16-hex, `parent_span_id`)
      - Every autonomous run emits a trace; every tool call is a span with
        redacted inputs/outputs, duration_ms, tokens, cost_usd, and policy status
      - Optional OpenTelemetry SDK bridge when `opentelemetry` is installed
  16. Durable Traces & `/why` Receipts Across Restart (`traces.db` SQLite WAL)
      - Persists traces, spans, `/why` receipts, and durable evidence mirror in
        `~/.local/share/celina/traces.db` (WAL + busy_timeout=5000 + 0600) so full
        execution history survives daemon crashes and restarts cleanly.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config, privacy

try:
    from opentelemetry import trace as _otel_trace  # type: ignore[import-not-found]
except Exception:
    _otel_trace = None


def new_trace_id() -> str:
    return secrets.token_hex(16)  # 32-hex W3C trace_id


def new_span_id() -> str:
    return secrets.token_hex(8)  # 16-hex W3C span_id


@dataclasses.dataclass
class SpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    name: str
    kind: str  # internal | tool | planner | router | saga | bus
    start_ns: int
    end_ns: int = 0
    duration_ms: float = 0.0
    status: str = "ok"
    inputs: dict[str, Any] = dataclasses.field(default_factory=dict)
    outputs: dict[str, Any] = dataclasses.field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[str(key)] = value

    def set_io(self, *, inputs: Optional[dict[str, Any]] = None, outputs: Optional[dict[str, Any]] = None) -> None:
        if inputs is not None:
            self.inputs = dict(inputs)
        if outputs is not None:
            self.outputs = dict(outputs)

    def set_usage(self, *, tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0) -> None:
        self.tokens_in = int(tokens_in)
        self.tokens_out = int(tokens_out)
        self.cost_usd = float(cost_usd)


class DurableTraceStore:
    """Thread-safe SQLite WAL backend storing traces, spans, `/why` receipts, and durable evidence."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else (config.BASE_DIR / "traces.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    root_name TEXT NOT NULL,
                    root_goal_id TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'running',
                    total_cost_usd REAL NOT NULL DEFAULT 0.0,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_traces_started ON traces (started_at DESC);

                CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    start_ns INTEGER NOT NULL,
                    end_ns INTEGER NOT NULL,
                    duration_ms REAL NOT NULL,
                    status TEXT NOT NULL,
                    inputs_json TEXT NOT NULL DEFAULT '{}',
                    outputs_json TEXT NOT NULL DEFAULT '{}',
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    attrs_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans (trace_id, start_ns ASC);

                CREATE TABLE IF NOT EXISTS why_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    prompt_preview TEXT NOT NULL,
                    gear TEXT NOT NULL,
                    source TEXT NOT NULL,
                    receipt_text TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_why_created ON why_receipts (created_at DESC);

                CREATE TABLE IF NOT EXISTS evidence_durable (
                    seq INTEGER PRIMARY KEY,
                    entry_hash TEXT NOT NULL UNIQUE,
                    prev_hash TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    event_type TEXT NOT NULL DEFAULT 'tool_execution',
                    tool_name TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    root_goal_id TEXT NOT NULL DEFAULT '',
                    task_id TEXT NOT NULL DEFAULT '',
                    trace_id TEXT NOT NULL DEFAULT '',
                    record_json TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def _redact_json(self, obj: dict[str, Any]) -> str:
        raw = json.dumps(obj or {}, sort_keys=True, default=str)[:2000]
        redacted, _ = privacy.RedactionVault().redact(raw)
        return redacted

    def start_trace(
        self,
        root_name: str,
        *,
        trace_id: Optional[str] = None,
        root_goal_id: str = "",
        meta: Optional[dict[str, Any]] = None,
    ) -> str:
        tid = trace_id or new_trace_id()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO traces (trace_id, root_name, root_goal_id, started_at, status, meta_json)
                VALUES (?, ?, ?, ?, 'running', ?)
                """,
                (tid, root_name, root_goal_id, now, self._redact_json(meta or {})),
            )
            self._conn.commit()
        return tid

    def finish_trace(self, trace_id: str, *, status: str = "ok") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) AS total_cost FROM spans WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            total_cost = float(row["total_cost"]) if row else 0.0
            self._conn.execute(
                "UPDATE traces SET ended_at = ?, status = ?, total_cost_usd = ? WHERE trace_id = ?",
                (now, status, total_cost, trace_id),
            )
            self._conn.commit()

    def record_span(self, span: SpanRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO spans (
                    span_id, trace_id, parent_span_id, name, kind, start_ns, end_ns,
                    duration_ms, status, inputs_json, outputs_json,
                    tokens_in, tokens_out, cost_usd, attrs_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span.span_id,
                    span.trace_id,
                    span.parent_span_id,
                    span.name,
                    span.kind,
                    int(span.start_ns),
                    int(span.end_ns),
                    round(float(span.duration_ms), 3),
                    span.status,
                    self._redact_json(span.inputs),
                    self._redact_json(span.outputs),
                    int(span.tokens_in),
                    int(span.tokens_out),
                    float(span.cost_usd),
                    self._redact_json(span.attributes),
                ),
            )
            self._conn.commit()

    def save_why_receipt(
        self,
        *,
        trace_id: str,
        prompt_preview: str,
        gear: str,
        source: str,
        receipt_text: str,
        receipt_hash: str,
    ) -> str:
        rid = f"why_{secrets.token_hex(8)}"
        now = datetime.now(timezone.utc).isoformat()
        redacted_txt, _ = privacy.RedactionVault().redact(receipt_text or "")
        redacted_prev, _ = privacy.RedactionVault().redact(prompt_preview or "")
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO why_receipts (
                    receipt_id, trace_id, created_at, prompt_preview, gear, source, receipt_text, receipt_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rid, trace_id, now, redacted_prev[:160], gear, source, redacted_txt[:4000], receipt_hash),
            )
            self._conn.commit()
        return rid

    def mirror_evidence_record(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO evidence_durable (
                    seq, entry_hash, prev_hash, timestamp_utc, event_type,
                    tool_name, outcome, root_goal_id, task_id, trace_id, record_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(record.get("seq", 0)),
                    str(record.get("entry_hash", "")),
                    str(record.get("prev_hash", "")),
                    str(record.get("timestamp_utc", "")),
                    str(record.get("event_type", "tool_execution")),
                    str(record.get("tool", "")),
                    str(record.get("outcome", "")),
                    str(record.get("root_goal_id", "")),
                    str(record.get("task_id", "")),
                    str(record.get("trace_id", "")),
                    json.dumps(record, sort_keys=True),
                ),
            )
            self._conn.commit()

    def get_trace(self, trace_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            t_row = self._conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
            if not t_row:
                return None
            spans = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_ns ASC", (trace_id,)
                ).fetchall()
            ]
            receipts = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT * FROM why_receipts WHERE trace_id = ? ORDER BY created_at ASC", (trace_id,)
                ).fetchall()
            ]
        return {"trace": dict(t_row), "spans": spans, "why_receipts": receipts}

    def list_traces(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM traces ORDER BY started_at DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
            return [dict(r) for r in rows]

    def list_why_receipts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM why_receipts ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
            return [dict(r) for r in rows]

    def count_durable_evidence(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM evidence_durable").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class CelinaTracer:
    """OpenTelemetry-compatible distributed tracer backed by DurableTraceStore (`traces.db`)."""

    def __init__(self, store: Optional[DurableTraceStore] = None):
        self.store = store or DurableTraceStore()
        self._otel = _otel_trace.get_tracer("celina.os") if _otel_trace is not None else None
        self._tls = threading.local()

    def current_trace_id(self) -> str:
        stack = getattr(self._tls, "span_stack", None)
        return stack[-1].trace_id if stack else ""

    def current_span_id(self) -> Optional[str]:
        stack = getattr(self._tls, "span_stack", None)
        return stack[-1].span_id if stack else None

    @contextlib.contextmanager
    def start_span(
        self,
        name: str,
        *,
        kind: str = "internal",
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        root_goal_id: str = "",
        inputs: Optional[dict[str, Any]] = None,
        attributes: Optional[dict[str, Any]] = None,
    ) -> Iterator[SpanRecord]:
        stack: list[SpanRecord] = getattr(self._tls, "span_stack", [])
        if not hasattr(self._tls, "span_stack"):
            self._tls.span_stack = stack

        is_root = not stack and trace_id is None
        eff_trace_id = trace_id or (stack[-1].trace_id if stack else new_trace_id())
        eff_parent = parent_span_id if parent_span_id is not None else (stack[-1].span_id if stack else None)

        if is_root or not stack:
            self.store.start_trace(name, trace_id=eff_trace_id, root_goal_id=root_goal_id, meta=attributes)

        span = SpanRecord(
            trace_id=eff_trace_id,
            span_id=new_span_id(),
            parent_span_id=eff_parent,
            name=name,
            kind=kind,
            start_ns=time.time_ns(),
            inputs=dict(inputs or {}),
            attributes=dict(attributes or {}),
        )
        stack.append(span)

        otel_cm = self._otel.start_as_current_span(name) if self._otel is not None else contextlib.nullcontext()
        try:
            with otel_cm:
                yield span
        except Exception as exc:
            span.status = f"error:{type(exc).__name__}"
            span.set_attribute("error.message", str(exc)[:240])
            raise
        finally:
            span.end_ns = time.time_ns()
            span.duration_ms = max(0.0, (span.end_ns - span.start_ns) / 1_000_000.0)
            stack.pop()
            self.store.record_span(span)
            if is_root:
                self.store.finish_trace(eff_trace_id, status=span.status)


_DEFAULT_TRACER: Optional[CelinaTracer] = None


def get_tracer() -> CelinaTracer:
    global _DEFAULT_TRACER
    if _DEFAULT_TRACER is None:
        _DEFAULT_TRACER = CelinaTracer()
    return _DEFAULT_TRACER


class OTLPJsonExporter:
    """
    agentlog-ai / Traccia compatible local OTLP HTTP/JSON span exporter.
    Strict security invariant: only permits loopback collector endpoints
    (`127.0.0.1` / `localhost` / `::1`) so traces & prompts never exfiltrate.
    Fails soft if no collector is running (SQLite WAL `traces.db` remains authoritative).
    """

    def __init__(self, endpoint: str = "http://127.0.0.1:4318/v1/traces", *, timeout: float = 1.5):
        import urllib.parse

        parsed = urllib.parse.urlparse(str(endpoint or "").strip())
        host = (parsed.hostname or "").strip().lower()
        if parsed.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(f"OTLP endpoint '{endpoint}' must be loopback-only (127.0.0.1/localhost).")
        self.endpoint = str(endpoint)
        self.timeout = float(timeout)

    def format_otlp_payload(self, span: SpanRecord, *, service_name: str = "celina-autonomy") -> dict[str, Any]:
        attrs = [{"key": str(k), "value": {"stringValue": str(v)}} for k, v in (span.attributes or {}).items()]
        attrs.extend(
            [
                {"key": "gen_ai.usage.input_tokens", "value": {"intValue": int(span.tokens_in)}},
                {"key": "gen_ai.usage.output_tokens", "value": {"intValue": int(span.tokens_out)}},
                {"key": "traccia.cost_usd", "value": {"doubleValue": float(span.cost_usd)}},
            ]
        )
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "celina.telemetry", "version": "4.4.0"},
                            "spans": [
                                {
                                    "traceId": span.trace_id,
                                    "spanId": span.span_id,
                                    "parentSpanId": span.parent_span_id or "",
                                    "name": span.name,
                                    "startTimeUnixNano": str(span.start_ns),
                                    "endTimeUnixNano": str(span.end_ns or span.start_ns),
                                    "attributes": attrs,
                                    "status": {"code": 1 if span.status == "ok" else 2, "message": span.status},
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    def export_span(self, span: SpanRecord, *, session: Any = None) -> bool:
        import requests as _req

        payload = self.format_otlp_payload(span)
        client = session if session is not None else _req
        try:
            resp = client.post(self.endpoint, json=payload, timeout=self.timeout)
            return 200 <= int(getattr(resp, "status_code", 500)) < 300
        except Exception:
            return False


def agent_span(
    name: str,
    *,
    tracer_getter: Optional[Callable[[], Optional[CelinaTracer]]] = None,
    self_attr: Optional[str] = None,
    governance_tier: str = "internal",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    agentlog-ai / Traccia style decorator (`@agent_span(name="router")`).
    Wraps any agent/router/engine callable with automatic W3C span recording,
    governance attributes, and error capture on the durable SQLite WAL backend.
    If `self_attr` is provided (e.g. `"tracer"`), resolves the tracer from the
    bound instance (`getattr(self, self_attr, None)`) and skips tracing cleanly
    when the instance explicitly disables telemetry (`None`).
    """
    import functools

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            tr: Optional[CelinaTracer] = None
            if tracer_getter is not None:
                tr = tracer_getter()
            elif self_attr is not None and args:
                tr = getattr(args[0], self_attr, None)
            else:
                tr = get_tracer()
            if tr is None:
                return fn(*args, **kwargs)
            with tr.start_span(
                f"agent.{name}",
                kind="agent",
                attributes={
                    "agentlog.agent_name": name,
                    "traccia.governance_tier": governance_tier,
                },
            ) as sp:
                res = fn(*args, **kwargs)
                sp.set_attribute("agentlog.completed", True)
                return res

        return _wrapper

    return _decorator
