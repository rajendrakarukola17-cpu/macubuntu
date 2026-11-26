"""
Celina AI (Celly) - Connectors (Update 23)

App connectors adapted from MausBot concepts (Apache-2.0, github.com/milind-soni/OpenMausBot)
to Python. Connectors live OUTSIDE Gear 0 and are NOT in the Gear 0 dispatch chain.

Hard refusals (must respect):
- No gear0_webhook.py — no inbound webhook server in Gear 0
- No cloud connector in Gear 0 dispatch chain
- No TypeScript code port — Python implementations only
- No exponential backoff/jitter
- 102-tool contract must not silently change
"""

from __future__ import annotations

from .base import BaseConnector, ConnectorConfig, ConnectorStatus, ConnectorAction, ActionResult, AllowDenyGate
from .registry import ConnectorRegistry, get_registry

__all__ = [
    "BaseConnector",
    "ConnectorConfig",
    "ConnectorStatus",
    "ConnectorAction",
    "ActionResult",
    "AllowDenyGate",
    "ConnectorRegistry",
    "get_registry",
]
