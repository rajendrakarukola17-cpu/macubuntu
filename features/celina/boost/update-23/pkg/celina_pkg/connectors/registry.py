"""
Celina AI (Celly) - Connector Registry (Update 23)

Central registry for all connectors. Connectors are registered here and can be
looked up by name. The registry is NOT part of the Gear 0 dispatch chain.
"""

from __future__ import annotations

from typing import Optional

from .base import BaseConnector, ConnectorConfig, ConnectorStatus


class ConnectorRegistry:
    """Thread-safe registry of connector instances."""

    def __init__(self):
        self._connectors: dict[str, BaseConnector] = {}
        self._configs: dict[str, ConnectorConfig] = {}

    def register(self, connector: BaseConnector, config: ConnectorConfig) -> None:
        """Register a connector instance with its config."""
        self._connectors[connector.name] = connector
        self._configs[connector.name] = config

    def get(self, name: str) -> Optional[BaseConnector]:
        """Return a connector by name, or None if not registered."""
        return self._connectors.get(name)

    def get_config(self, name: str) -> Optional[ConnectorConfig]:
        """Return a connector config by name."""
        return self._configs.get(name)

    def list_connectors(self) -> list[str]:
        """Return all registered connector names."""
        return list(self._connectors.keys())

    def list_status_rows(self) -> list[dict[str, object]]:
        """Return safe status summaries for all connectors (no credentials exposed)."""
        rows: list[dict[str, object]] = []
        for name in sorted(self._connectors.keys()):
            conn = self._connectors[name]
            rows.append(conn.status_row())
        return rows

    def configured_connectors(self) -> list[str]:
        """Return names of connectors that have valid credentials."""
        return [name for name, conn in self._connectors.items() if conn.is_configured()]

    def unconfigured_connectors(self) -> list[str]:
        """Return names of connectors that are not configured."""
        return [name for name, conn in self._connectors.items() if not conn.is_configured()]

    def status_summary(self) -> dict[str, object]:
        """Return a high-level summary of connector state."""
        total = len(self._connectors)
        configured = len(self.configured_connectors())
        return {
            "total": total,
            "configured": configured,
            "unconfigured": total - configured,
            "connectors": self.list_status_rows(),
        }


# ---------------------------------------------------------------------------
# Module-level registry singleton
# ---------------------------------------------------------------------------

_REGISTRY: Optional[ConnectorRegistry] = None


def get_registry() -> ConnectorRegistry:
    """Return the module-level registry singleton (lazily created)."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ConnectorRegistry()
    return _REGISTRY
