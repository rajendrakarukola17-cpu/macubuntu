"""Celina AI (Celly) - Gear 0 Network (Update 19)

Local network status queries — DNS, connectivity, interfaces.

Examples:
  - "Is my internet up?"
  - "What's my DNS?"

"""

from __future__ import annotations

import socket

_CHECK_INTERNET_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:is\s+my\s+)?internet\s+(?:up|down|working|connected)\??$",
    __import__("re").IGNORECASE,
)
_DNS_RE = __import__("re").compile(
    r"^(?:please\s+)?(?:what'?s|what is)\s+(?:my\s+)?dns\??$",
    __import__("re").IGNORECASE,
)


def network_internet_up() -> str:
    """Check if internet connectivity is available."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3).close()
        return "✓ Internet is UP."
    except OSError:
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=3).close()
            return "✓ Internet is UP."
        except OSError:
            return "✗ Internet is DOWN."
    except Exception as exc:
        return f"Failed to check internet: {exc}"


def network_dns() -> str:
    """Read DNS servers from /etc/resolv.conf."""
    try:
        import pathlib
        resolv = pathlib.Path("/etc/resolv.conf")
        if resolv.exists():
            content = resolv.read_text()
            servers = []
            for line in content.splitlines():
                if line.strip().startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        servers.append(parts[1])
            if servers:
                return f"📡 DNS servers: {', '.join(servers)}"
            return "No DNS servers found in /etc/resolv.conf."
        return "Cannot read /etc/resolv.conf."
    except Exception as exc:
        return f"Failed to read DNS: {exc}"


# ---------------------------------------------------------------------------
# Master dispatch
# ---------------------------------------------------------------------------

def gear0_network_classify(query: str) -> str | None:
    """Classify query into a network handler name, or None."""
    low = query.strip().lower()
    if _CHECK_INTERNET_RE.match(low):
        return "internet"
    if _DNS_RE.match(low):
        return "dns"
    return None


def gear0_network_execute(handler: str, query: str, dry_run: bool = False, **kwargs) -> str | None:
    """Execute a Gear 0-Network handler."""
    try:
        if handler == "internet":
            return network_internet_up()
        if handler == "dns":
            return network_dns()
    except Exception:
        return None
    return None


__all__ = [
    "gear0_network_classify",
    "gear0_network_execute",
    "network_dns",
    "network_internet_up",
]
