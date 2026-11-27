"""UNV-1: Live local staging-server SSRF & egress-policy integration tests."""

from __future__ import annotations

import http.server
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CELINA_ROOT = REPO_ROOT / "features" / "celina"
if str(CELINA_ROOT) not in sys.path:
    sys.path.insert(0, str(CELINA_ROOT))

from celina_pkg import config, net_policy  # noqa: E402
from celina_pkg.connectors.base import BaseConnector, ConnectorConfig  # noqa: E402


class _CountingHandler(http.server.BaseHTTPRequestHandler):
    hits: int = 0

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"secret":"must-never-reach"}')

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


class _DummyConnector(BaseConnector):
    name = "dummy"
    domain = "dummy"

    def healthcheck(self) -> tuple[bool, str]:
        return True, "ok"


@pytest.fixture()
def staging_server():
    _CountingHandler.hits = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def test_unv1_net_policy_blocks_live_loopback_metadata_and_dns_rebinding(
    staging_server: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real local staging server receives 0 requests for loopback, metadata, private IPs, and rebinding."""
    port = staging_server
    monkeypatch.setitem(config.WEB_COMMS_CFG, "enabled", True)

    blocked_urls = [
        f"http://127.0.0.1:{port}/latest",
        f"https://127.0.0.1:{port}/latest",
        f"https://localhost:{port}/latest",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.1/internal",
        "https://192.168.1.1/admin",
        f"https://[::ffff:127.0.0.1]:{port}/mapped",
    ]
    for url in blocked_urls:
        with pytest.raises(net_policy.URLPolicyError):
            net_policy.safe_get(url)

    # DNS rebinding simulation: hostname resolves to real loopback IP 127.0.0.1
    with pytest.raises(net_policy.URLPolicyError):
        net_policy.safe_get(
            "https://rebind.attacker.example/secret",
            resolver=lambda _host, _port: ["127.0.0.1"],
        )

    assert _CountingHandler.hits == 0


def test_unv1_connector_http_request_blocks_live_loopback_and_non_https(
    staging_server: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BaseConnector._http_request fails closed before touching a real loopback staging server."""
    port = staging_server
    monkeypatch.setitem(config.WEB_COMMS_CFG, "enabled", True)
    monkeypatch.setitem(config.WEB_COMMS_CFG, "allowed_hosts", [])
    monkeypatch.setattr(net_policy, "is_online", lambda *_a, **_kw: True)

    conn = _DummyConnector(ConnectorConfig(name="dummy", display_name="Dummy"))
    for url in (
        f"http://127.0.0.1:{port}/leak",
        f"https://127.0.0.1:{port}/leak",
        "https://169.254.169.254/latest/meta-data/",
    ):
        with pytest.raises(net_policy.URLPolicyError):
            conn._http_request("GET", url, headers={})

    assert _CountingHandler.hits == 0
