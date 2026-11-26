# Copyright 2026 Celina contributors.
"""Explicit-ledger self-improvement audit bridge; importing performs no I/O."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Protocol

from .contracts import BudgetSnapshot, VerifierResult

if TYPE_CHECKING:
    from .flags import SelfImproveFlags

_EVENT = re.compile(r"selfimprove(?:\.[a-z0-9_]+)+\Z")
_SECRET_KEY = re.compile(r"(?i)(password|passwd|secret|token|api.?key|private.?key)")
_SECRET_VALUE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?token)\s*[:=]\s*[^\s,;]+",
)
_TOKEN = re.compile(
    r"(?i)\b(?:bearer\s+[A-Za-z0-9._~+/-]{8,}|sk-[A-Za-z0-9_-]{12,}"
    r"|ghp_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}"
    r"|AIza[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})\b",
)
_MARKER = "[TRUNCATED]"
_MAX_PREVIEW = 220


class LedgerSink(Protocol):
    """Structural subset of the explicit EvidenceLedger API used by the bridge."""

    def verify(self) -> tuple[bool, str, int]: ...  # noqa: D102  # Protocol describes the interface.
    def record_execution(
        self, *, tool_name: str, arguments: dict[str, Any], result_text: str, event_type: str,
    ) -> dict[str, Any]:
        """Append an event and return its evidence record."""


def _sanitize(value: object) -> object:
    if isinstance(value, (VerifierResult, BudgetSnapshot)):
        return _sanitize(asdict(value))
    if isinstance(value, str):
        return _TOKEN.sub("[REDACTED]", _SECRET_VALUE.sub(r"\1=[REDACTED]", value))
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    message = "unsupported audit payload type"
    raise ValueError(message)


def _preview(payload: object) -> str:
    text = json.dumps(_sanitize(payload), sort_keys=True, ensure_ascii=True, allow_nan=False)
    if len(text) > _MAX_PREVIEW:
        return text[: _MAX_PREVIEW - len(_MARKER)] + _MARKER
    return text


class LedgerBridge:
    """Write audit events using an explicitly supplied EvidenceLedger instance.

    This module never constructs a ledger or imports evidence on import. Disabled
    master flags suppress recording; omitted flags mean audit-only recording and
    do not enable or execute any self-improvement action.
    """

    def __init__(self, ledger: LedgerSink) -> None:
        """Bind the caller-owned ledger without creating files or directories."""
        self._ledger = ledger

    def record(
        self, event_type: str, payload: object, *, flags: SelfImproveFlags | None = None,
    ) -> dict[str, Any] | None:
        """Record a sanitized event after verifying the ledger; propagate failures."""
        if not _EVENT.fullmatch(event_type):
            message = "event must be a selfimprove.* identifier"
            raise ValueError(message)
        if flags is not None and not flags.enabled:
            return None
        preview = _preview(payload)
        self._ledger.verify()
        return self._ledger.record_execution(
            tool_name="selfimprove", arguments={"event_type": event_type},
            result_text=preview, event_type=event_type,
        )
