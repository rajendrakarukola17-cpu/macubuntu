"""Celina AI (Celly) - Centralized Network, URL & SSRF Security Policy (Phase 3)
Single authoritative egress policy used by:
  - fetch_webpage
  - web_search
  - community skill download (skills.py)
  - MCP HTTP transport (mcp_client.py)
  - IoT smart-home webhooks (tools.py)

Invariants:
  1. Public web requests allow ONLY https:// and reject embedded URL credentials.
  2. DNS is resolved BEFORE connecting; every resolved IP is validated against
     loopback, link-local, multicast, private, reserved, unspecified, cloud
     metadata, and IPv4-mapped IPv6 ranges.
  3. Redirects are disabled by default; when explicitly permitted, every hop is
     revalidated through DNS + IP policy and capped at max_redirects.
  4. Response size, timeout, and Content-Type are strictly bounded.
  5. IoT webhooks require an explicit host/port/method allowlist file (0600).
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

DEFAULT_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/html",
    "text/plain",
    "text/markdown",
    "text/xml",
    "application/json",
    "application/xhtml+xml",
    "application/xml",
    "application/rss+xml",
    "application/atom+xml",
})

# Cloud metadata & sensitive link-local / CGNAT addresses explicitly denied
_METADATA_HOSTS: frozenset[str] = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "metadata",
    "instance-data",
    "169.254.169.254",
    "100.100.100.200",
    "fd00:ec2::254",
})

_METADATA_IPS: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address] = frozenset({
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
})

_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")

ResolverFn = Callable[[str, int], list[str]]


class URLPolicyError(ValueError):
    """Raised when a URL, resolved IP, redirect, or response violates network policy."""


class OfflineError(RuntimeError):
    """Raised when a web_comms tool is invoked while offline or when web_comms.enabled is false."""


WEB_COMMS_DEFAULT_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "fetch_webpage",
    "fetch_url_text",
    "get_weather",
    "get_news_headlines",
    "get_current_time_zone",
    "compose_message",
})


def is_online(host: str = "1.1.1.1", port: int = 443, timeout: float = 2.0) -> bool:
    """Fast TCP connectivity probe returning False immediately when offline."""
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout)):
            return True
    except OSError:
        return False


def is_web_comms_tool(tool_name: str) -> bool:
    """Returns True if tool_name belongs to the web_comms domain."""
    from . import config

    dom = (config.TOOL_DOMAINS or {}).get("web_comms") or {}
    dom_tools = set(dom.get("tools") or ()) | set(WEB_COMMS_DEFAULT_TOOLS)
    return (tool_name or "").strip() in dom_tools


def validate_web_comms_host(url: str, allowed_hosts: Iterable[str] | None = None) -> None:
    """Enforces optional web_comms.allowed_hosts when configured (non-empty list)."""
    from . import config

    allow = (
        [str(h).strip().lower().rstrip(".") for h in allowed_hosts if str(h).strip()]
        if allowed_hosts is not None
        else config.get_web_comms_allowed_hosts()
    )
    if not allow:
        return
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise URLPolicyError("URL missing hostname for web_comms.allowed_hosts check.")
    if not any(host == a or host.endswith(f".{a}") for a in allow):
        raise URLPolicyError(
            f"Host '{host}' is not in web_comms.allowed_hosts ({allow}).",
        )


def check_web_comms_allowed(
    tool_name: str,
    url: str | None = None,
    *,
    online_probe: Callable[[], bool] | None = None,
) -> None:
    """Fail-closed offline & opt-in guard for web_comms tools:
    1. Requires web_comms.enabled == True (default off).
    2. Requires active internet connectivity (is_online()).
    3. Enforces web_comms.allowed_hosts when non-empty and a URL is supplied.
    """
    from . import config

    if not is_web_comms_tool(tool_name):
        return
    if not config.is_web_comms_enabled():
        raise OfflineError(
            f"Live web_comms tool '{tool_name}' is disabled (web_comms.enabled=false). "
            "Enable opt-in web_comms to fetch live data.",
        )
    probe = online_probe if online_probe is not None else is_online
    if not probe():
        raise OfflineError(
            f"Device is offline; cannot run web_comms tool '{tool_name}'. "
            "Local offline tools and local LLM remain available.",
        )
    if url:
        validate_web_comms_host(url)


def guarded_call(
    tool_name: str,
    *args: Any,
    online_probe: Callable[[], bool] | None = None,
    **kwargs: Any,
) -> Any:
    """Executes a tool call under the offline & opt-in web_comms guard.
    Raises OfflineError for web_comms tools when web_comms.enabled is False or offline.
    Accepts either:
      - guarded_call("get_weather", callable_fn, *fn_args, **fn_kwargs)
      - guarded_call("get_weather", *fn_args, **fn_kwargs)  (resolves tool from celina_pkg.tools)
    """
    target_fn: Callable[..., Any] | None = None
    call_args = list(args)
    if call_args and callable(call_args[0]):
        target_fn = call_args.pop(0)
    elif callable(kwargs.get("fn")):
        target_fn = kwargs.pop("fn")

    url_hint = kwargs.get("url") if isinstance(kwargs.get("url"), str) else None
    if not url_hint and call_args and isinstance(call_args[0], str) and call_args[0].strip().startswith(("http://", "https://")):
        url_hint = call_args[0].strip()

    check_web_comms_allowed(tool_name, url=url_hint, online_probe=online_probe)

    if target_fn is None:
        from . import tools

        target_fn = tools._AUTO_TOOL_MAP.get(tool_name) or tools._APPROVAL_TOOL_MAP.get(tool_name)
        if target_fn is None:
            raise KeyError(f"Unknown tool '{tool_name}'")

    return target_fn(*call_args, **kwargs)


@dataclass(frozen=True)
class ValidatedURL:
    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: tuple[str, ...]


@dataclass(frozen=True)
class SafeHTTPResponse:
    url: str
    status_code: int
    content_type: str
    body: bytes
    headers: dict[str, str]

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


def is_forbidden_public_ip(ip_str: str) -> tuple[bool, str]:
    """Returns (True, reason) if ip_str is loopback, private, link-local, multicast,
    reserved, unspecified, CGNAT, cloud metadata, or an IPv4-mapped IPv6 wrapper
    around any forbidden address.
    """
    try:
        ip_obj = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return True, f"invalid IP address '{ip_str}'"

    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1, ::ffff:169.254.169.254, ::ffff:10.0.0.1)
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        inner_bad, inner_reason = is_forbidden_public_ip(str(ip_obj.ipv4_mapped))
        if inner_bad:
            return True, f"IPv4-mapped IPv6 wrapping forbidden IP ({inner_reason})"

    if ip_obj in _METADATA_IPS:
        return True, "cloud metadata IP"
    if ip_obj.is_loopback:
        return True, "loopback IP"
    if ip_obj.is_link_local:
        return True, "link-local IP"
    if ip_obj.is_multicast:
        return True, "multicast IP"
    if ip_obj.is_private:
        return True, "private IP"
    if ip_obj.is_reserved:
        return True, "reserved IP"
    if ip_obj.is_unspecified:
        return True, "unspecified IP"
    if isinstance(ip_obj, ipaddress.IPv4Address) and ip_obj in _CGNAT_NET:
        return True, "shared CGNAT IP"
    if not ip_obj.is_global:
        return True, "non-global IP"

    return False, ""


def default_dns_resolver(host: str, port: int) -> list[str]:
    """Resolves all A/AAAA IP strings for host via system getaddrinfo."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise URLPolicyError(f"DNS resolution failed for '{host}': {exc}") from exc
    ips: list[str] = []
    seen: set[str] = set()
    for fam, _stype, _proto, _canon, sockaddr in infos:
        if fam in (socket.AF_INET, socket.AF_INET6):
            ip_s = str(sockaddr[0])
            if ip_s not in seen:
                seen.add(ip_s)
                ips.append(ip_s)
    if not ips:
        raise URLPolicyError(f"No IP addresses resolved for '{host}'.")
    return ips


def validate_public_url(
    raw_url: str,
    *,
    resolver: ResolverFn | None = None,
) -> ValidatedURL:
    """Validates a public web URL:
    - https:// ONLY
    - NO embedded credentials (user:pass@host)
    - NO localhost / .local / .internal / metadata hostnames
    - DNS resolved upfront and EVERY IP verified public/global
    """
    url = (raw_url or "").strip()
    if not url:
        raise URLPolicyError("Empty URL.")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise URLPolicyError("Only https:// URLs are permitted for public web requests.")
    if parsed.username is not None or parsed.password is not None:
        raise URLPolicyError("URLs containing embedded credentials are forbidden.")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise URLPolicyError("URL is missing a valid hostname.")
    if host in {"localhost", "localhost.localdomain"} or host in _METADATA_HOSTS:
        raise URLPolicyError(f"Host '{host}' is blocked by network security policy.")
    if host.endswith((".local", ".internal", ".localhost", ".lan", ".home.arpa")):
        raise URLPolicyError(f"Local/internal host '{host}' is blocked for public requests.")

    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise URLPolicyError(f"Invalid URL port: {exc}") from exc
    if port != 443:
        raise URLPolicyError(f"Non-standard port {port} is blocked for public HTTPS requests (443 only).")

    # Direct IP literal check before DNS
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        resolved = [host]
    except ValueError:
        resolve_fn = resolver or default_dns_resolver
        resolved = resolve_fn(host, port)

    if not resolved:
        raise URLPolicyError(f"DNS returned no addresses for '{host}'.")

    for ip_s in resolved:
        forbidden, reason = is_forbidden_public_ip(ip_s)
        if forbidden:
            raise URLPolicyError(f"Blocked resolved IP {ip_s} for '{host}': {reason}.")

    return ValidatedURL(
        url=url,
        scheme="https",
        host=host,
        port=port,
        resolved_ips=tuple(resolved),
    )


def load_iot_allowlist(path: Path) -> dict[str, Any]:
    """Loads explicit IoT webhook allowlist JSON (defaults to deny-all hosts)."""
    default_cfg = {
        "allowed_hosts": [],
        "allowed_ports": [8123, 1880],
        "allowed_methods": ["POST"],
    }
    if not path.exists():
        return default_cfg
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return default_cfg
        return {
            "allowed_hosts": [str(h).strip().lower() for h in (loaded.get("allowed_hosts") or []) if str(h).strip()],
            "allowed_ports": [int(p) for p in (loaded.get("allowed_ports") or [8123, 1880])],
            "allowed_methods": [str(m).strip().upper() for m in (loaded.get("allowed_methods") or ["POST"])],
        }
    except Exception:
        return default_cfg


def validate_iot_url(
    raw_url: str,
    method: str,
    allowlist: dict[str, Any],
    *,
    resolver: ResolverFn | None = None,
) -> ValidatedURL:
    """Validates an IoT / smart-home webhook strictly against the explicit allowlist:
    - host must match allowed_hosts
    - port must match allowed_ports
    - method must match allowed_methods
    - no URL credentials, no metadata IPs, no Ollama port 11434
    """
    url = (raw_url or "").strip()
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise URLPolicyError("IoT webhook URL must use http:// or https://.")
    if parsed.username is not None or parsed.password is not None:
        raise URLPolicyError("URLs with embedded credentials are forbidden.")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise URLPolicyError("IoT webhook URL is missing a hostname.")
    if host in _METADATA_HOSTS:
        raise URLPolicyError("Cloud metadata endpoints are strictly forbidden.")

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise URLPolicyError(f"Invalid port: {exc}") from exc
    if port in {11434, 11435}:
        raise URLPolicyError("Direct calls to local inference ports 11434/11435 are blocked.")

    http_method = (method or "POST").strip().upper()
    allowed_methods = {str(m).upper() for m in (allowlist.get("allowed_methods") or ["POST"])}
    if http_method not in allowed_methods:
        raise URLPolicyError(
            f"HTTP method '{http_method}' not in IoT allowlist ({sorted(allowed_methods)}).",
        )

    allowed_hosts = {str(h).strip().lower().rstrip(".") for h in (allowlist.get("allowed_hosts") or [])}
    if not allowed_hosts or host not in allowed_hosts:
        raise URLPolicyError(
            f"Host '{host}' is not in ~/.config/celina/iot-allowlist.json allowed_hosts.",
        )

    allowed_ports = {int(p) for p in (allowlist.get("allowed_ports") or [])}
    if port not in allowed_ports:
        raise URLPolicyError(
            f"Port {port} is not in IoT allowlist ({sorted(allowed_ports)}).",
        )

    # Resolve & verify no metadata/multicast/unspecified/IPv4-mapped metadata address
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        resolved = [host]
    except ValueError:
        resolve_fn = resolver or default_dns_resolver
        resolved = resolve_fn(host, port)

    for ip_s in resolved:
        try:
            ip_obj = ipaddress.ip_address(ip_s.split("%", 1)[0])
        except ValueError as err:
            raise URLPolicyError(f"Invalid resolved IP '{ip_s}'.") from err
        inner = ip_obj.ipv4_mapped if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped else ip_obj
        if inner in _METADATA_IPS or inner.is_multicast or inner.is_unspecified:
            raise URLPolicyError(f"Resolved IoT IP '{ip_s}' is forbidden.")
        if inner.is_link_local and str(inner).startswith("169.254."):
            raise URLPolicyError(f"Link-local metadata range '{ip_s}' is forbidden.")

    return ValidatedURL(
        url=url,
        scheme=scheme,
        host=host,
        port=port,
        resolved_ips=tuple(resolved),
    )


def enforce_sensitive_egress(
    url: str,
    *,
    payload_text: str = "",
    classification: str = "PUBLIC",
    allowed_destinations: Iterable[str] | None = None,
) -> None:
    """Capability #15: Fail-closed destination-scoped & sensitive-data egress control.
    Blocks CONFIDENTIAL/SECRET payloads or detected secret tokens from leaving the host
    to any destination not explicitly permitted.
    """
    cls_upper = (classification or "PUBLIC").strip().upper()
    if cls_upper in {"CONFIDENTIAL", "SECRET"}:
        raise URLPolicyError(
            f"Fail-closed egress block: classification={cls_upper} payload cannot be sent to '{url}'.",
        )
    if payload_text:
        from . import privacy

        _redacted, report = privacy.RedactionVault().redact(payload_text)
        if report.has_secrets or report.has_pii:
            raise URLPolicyError(
                f"Fail-closed sensitive-data egress block: detected sensitive data ({report.summary}) in outbound payload.",
            )
    if allowed_destinations is not None:
        validate_web_comms_host(url, allowed_hosts=allowed_destinations)


def _check_peer_ip(
    resp: requests.Response,
    allowed_ips: tuple[str, ...],
    is_public: bool,
    *,
    fail_closed: bool = False,
) -> None:
    """Defense against DNS rebinding: verifies the actual connected socket peer IP
    matches the validated pre-resolution IP set and public-IP policy.
    When `fail_closed=True`, inability to inspect peer socket raises URLPolicyError.
    """
    raw = getattr(resp, "raw", None)
    conn = getattr(raw, "_connection", None)
    sock = getattr(conn, "sock", None)
    if sock is None or not hasattr(sock, "getpeername"):
        if fail_closed:
            raise URLPolicyError("Fail-closed peer verification: connected socket peer IP unavailable.")
        return
    try:
        peer = sock.getpeername()
        peer_ip = str(peer[0])
    except Exception as exc:
        raise URLPolicyError(f"Fail-closed peer verification failed: {exc}") from exc
    if is_public:
        bad, reason = is_forbidden_public_ip(peer_ip)
        if bad:
            raise URLPolicyError(f"DNS rebinding detected: connected to forbidden IP {peer_ip} ({reason}).")
    if allowed_ips and peer_ip not in allowed_ips:
        # Allow canonicalized IPv6 equivalent comparison
        try:
            p_obj = ipaddress.ip_address(peer_ip.split("%", 1)[0])
            if all(ipaddress.ip_address(a.split("%", 1)[0]) != p_obj for a in allowed_ips):
                raise URLPolicyError(
                    f"DNS rebinding detected: connected IP {peer_ip} not in pre-validated set {allowed_ips}.",
                )
        except ValueError as err:
            raise URLPolicyError(f"Invalid peer IP '{peer_ip}'.") from err


def safe_request(
    method: str,
    url: str,
    *,
    policy: str = "public",
    iot_allowlist: dict[str, Any] | None = None,
    allow_redirects: bool = False,
    max_redirects: int = 0,
    max_bytes: int = 2 * 1024 * 1024,
    timeout: tuple[float, float] | float = (5.0, 10.0),
    allowed_content_types: Iterable[str] | None = DEFAULT_ALLOWED_CONTENT_TYPES,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    resolver: ResolverFn | None = None,
    session: requests.Session | None = None,
    fail_closed_peer_ip: bool | None = None,
    classification: str = "PUBLIC",
    allowed_destinations: Iterable[str] | None = None,
) -> SafeHTTPResponse:
    """Executes an HTTP request under strict SSRF, DNS-rebinding, redirect, size,
    timeout, sensitive-data egress, and Content-Type controls.
    """
    payload_preview = json.dumps(json_body, sort_keys=True, default=str) if json_body is not None else ""
    enforce_sensitive_egress(
        url,
        payload_text=payload_preview,
        classification=classification,
        allowed_destinations=allowed_destinations,
    )
    http_method = (method or "GET").strip().upper()
    redirects_cap = max(0, min(5, int(max_redirects))) if allow_redirects else 0
    if policy == "iot":
        redirects_cap = 0  # Redirects are NEVER followed for IoT webhooks

    allowed_cts = {ct.strip().lower() for ct in allowed_content_types} if allowed_content_types is not None else None
    client = session if session is not None else requests
    strict_peer = (session is None) if fail_closed_peer_ip is None else bool(fail_closed_peer_ip)

    current_url = url
    current_params = params
    hops = 0

    while True:
        if policy == "iot":
            validated = validate_iot_url(
                current_url,
                http_method,
                iot_allowlist or {},
                resolver=resolver,
            )
        else:
            validated = validate_public_url(current_url, resolver=resolver)

        resp = client.request(
            http_method,
            validated.url,
            headers=headers,
            params=current_params,
            json=json_body,
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        try:
            _check_peer_ip(resp, validated.resolved_ips, is_public=(policy != "iot"), fail_closed=strict_peer)

            if resp.status_code in {301, 302, 303, 307, 308}:
                location = (resp.headers.get("Location") or "").strip()
                if not location:
                    raise URLPolicyError(f"Redirect response ({resp.status_code}) missing Location header.")
                if hops >= redirects_cap:
                    raise URLPolicyError(
                        f"Redirects are blocked (status {resp.status_code} -> {location[:120]}).",
                    )
                next_url = urljoin(validated.url, location)
                hops += 1
                current_url = next_url
                current_params = None
                if resp.status_code == 303:
                    http_method = "GET"
                    json_body = None
                continue

            # Validate Content-Type when allowlist is active
            raw_ct = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if allowed_cts is not None and raw_ct and raw_ct not in allowed_cts:
                raise URLPolicyError(f"Blocked disallowed Content-Type '{raw_ct}'.")

            # Check declared Content-Length early
            cl_hdr = resp.headers.get("Content-Length")
            if cl_hdr and cl_hdr.isdigit() and int(cl_hdr) > max_bytes:
                raise URLPolicyError(
                    f"Response too large ({int(cl_hdr)} bytes exceeds limit {max_bytes}).",
                )

            buf = bytearray()
            for chunk in resp.iter_content(chunk_size=16384):
                if not chunk:
                    continue
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise URLPolicyError(f"Response exceeded maximum allowed size ({max_bytes} bytes).")

            return SafeHTTPResponse(
                url=validated.url,
                status_code=int(resp.status_code),
                content_type=raw_ct,
                body=bytes(buf),
                headers=dict(resp.headers),
            )
        finally:
            resp.close()


def safe_get(
    url: str,
    *,
    allow_redirects: bool = False,
    max_redirects: int = 0,
    max_bytes: int = 2 * 1024 * 1024,
    timeout: tuple[float, float] | float = (5.0, 10.0),
    allowed_content_types: Iterable[str] | None = DEFAULT_ALLOWED_CONTENT_TYPES,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    resolver: ResolverFn | None = None,
    session: requests.Session | None = None,
) -> SafeHTTPResponse:
    return safe_request(
        "GET",
        url,
        policy="public",
        allow_redirects=allow_redirects,
        max_redirects=max_redirects,
        max_bytes=max_bytes,
        timeout=timeout,
        allowed_content_types=allowed_content_types,
        headers=headers,
        params=params,
        resolver=resolver,
        session=session,
    )
