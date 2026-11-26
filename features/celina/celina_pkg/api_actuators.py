"""
Celina Phase 3: API-First Actuators (`api_actuators.py`)

Replaces brittle GUI clicking with clean direct REST API actuation wherever APIs exist,
STRICTLY preserving Celina's security architecture:
  - Every mutating API call passes through Tier-2 `approval.request_bound_approval` + `verify_and_consume`
  - Every outbound URL passes through `net_policy.validate_url` (SSRF protection)
  - Every executed call appends a tamper-evident entry to `EvidenceLedger`
  - Secrets (`SPOTIFY_ACCESS_TOKEN`, webhook auth) are read strictly from GNOME Keyring or
    `~/.config/celina/env` (0600) — NEVER placed in LLM prompts or logs!
  - Opt-in n8n / webhook calls remain strictly gated behind `config.is_web_comms_enabled()`!
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from typing import Any, Callable, Optional
from urllib.parse import quote

import requests

from . import approval, command_runner, config, evidence, net_policy

log = logging.getLogger("celina.api_actuators")

ApprovalCallback = Callable[[str, str], bool]


def _lookup_api_token(service_key: str, env_var: str) -> str:
    """
    Reads an API token strictly from OS environment / ~/.config/celina/env or GNOME Keyring
    (`secret-tool lookup app celina service <service_key>`). Never exposes token to LLM.
    """
    direct = (os.environ.get(env_var) or config.ENV.get(env_var) or "").strip()
    if direct:
        return direct
    if shutil.which("secret-tool"):
        res = command_runner.run_command(
            ["secret-tool", "lookup", "app", "celina", "service", service_key],
            timeout=2.5,
        )
        if res.ok and res.stdout.strip():
            return res.stdout.strip()
    return ""


def _record_api_evidence(tool_name: str, summary_args: dict[str, Any], status: str, summary: str) -> None:
    try:
        args_hash = hashlib.sha256(
            json.dumps(summary_args, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        evidence.EvidenceLedger().append(
            tool_name=tool_name,
            arguments_hash=args_hash,
            status=status,
            summary=summary[:240],
        )
    except Exception as exc:
        log.debug("Evidence ledger append skipped for %s: %s", tool_name, exc)


def spotify_api_play(
    query: str,
    *,
    approval_cb: Optional[ApprovalCallback] = None,
    dry_run: bool = False,
    token_override: Optional[str] = None,
) -> tuple[bool, str]:
    """
    API-First Spotify Playback via Spotify Web API (`/v1/search` + `/v1/me/player/play`).
    Returns `(handled_via_api, message)`.
    If no Spotify token is configured, returns `(False, hint)` so callers cleanly fall back
    to Celina's browser + MPRIS D-Bus playback pipeline!
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return False, "No Spotify track query provided."

    token = token_override if token_override is not None else _lookup_api_token("spotify-api", "SPOTIFY_ACCESS_TOKEN")
    if not token:
        return False, "No SPOTIFY_ACCESS_TOKEN configured (using browser/MPRIS playback fallback)."

    if dry_run:
        return True, f"🧪 Dry-Run [API Actuator]: Would ask Tier-2 approval to play '{clean_q}' via Spotify Web API."

    if approval_cb is None:
        return True, "Denied: Spotify API playback requires interactive Tier-2 approval."

    safe_args = {"service": "spotify_api", "query": clean_q}
    tok, deny_err = approval.request_bound_approval(
        "control_media",
        safe_args,
        approval_cb,
        title=f"Approve Spotify Web API Playback: '{clean_q}'?",
        target="https://api.spotify.com/v1/me/player/play",
    )
    if deny_err:
        _record_api_evidence("spotify_api_play", safe_args, "denied", deny_err)
        return True, deny_err
    ok, err = approval.verify_and_consume(tok, "control_media", safe_args)
    if not ok:
        return True, err

    search_url = f"https://api.spotify.com/v1/search?q={quote(clean_q)}&type=track&limit=1"
    play_url = "https://api.spotify.com/v1/me/player/play"
    for u in (search_url, play_url):
        valid, verr = net_policy.validate_url(u)
        if not valid:
            return True, f"Blocked by network policy: {verr}"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        sr = requests.get(search_url, headers=headers, timeout=8.0)
        if sr.status_code == 401:
            _record_api_evidence("spotify_api_play", safe_args, "auth_expired", "Spotify token expired (401)")
            return False, "Spotify API token expired — falling back to browser/MPRIS playback."
        sr.raise_for_status()
        items = sr.json().get("tracks", {}).get("items", [])
        if not items:
            return True, f"No Spotify tracks matched '{clean_q}'."
        track = items[0]
        uri = str(track.get("uri") or "")
        title = str(track.get("name") or clean_q)
        artist = ", ".join(a.get("name", "") for a in track.get("artists", []) if a.get("name"))
        if not uri:
            return True, f"Spotify returned track '{title}' without playable URI."

        pr = requests.put(play_url, headers=headers, json={"uris": [uri]}, timeout=8.0)
        if pr.status_code == 404:
            _record_api_evidence("spotify_api_play", safe_args, "no_device", "No active Spotify Connect device")
            return False, "No active Spotify Connect device open — falling back to browser."
        pr.raise_for_status()
        msg = f"🎵 Playing '{title}'" + (f" by {artist}" if artist else "") + " via Spotify Web API (Tier-2 approved)."
        _record_api_evidence("spotify_api_play", safe_args, "ok", msg)
        return True, msg
    except Exception as exc:
        _record_api_evidence("spotify_api_play", safe_args, "error", str(exc))
        return False, f"Spotify API error ({exc}) — falling back to browser."


def call_approved_webhook(
    url: str,
    payload: dict[str, Any],
    *,
    approval_cb: Optional[ApprovalCallback] = None,
    dry_run: bool = False,
) -> str:
    """
    Opt-In Tier-2 Webhook / n8n Actuator Wrapper:
      - Strictly gated behind `config.is_web_comms_enabled()`
      - Strictly validated & sent via `net_policy.safe_request` (blocks private/metadata SSRF & redirects)
      - Strictly requires Tier-2 approval + logs tamper-evident EvidenceLedger entry
    """
    if not config.is_web_comms_enabled():
        return "Blocked: External webhooks/n8n are disabled by default (enable `web_comms.enabled` in neural-schema.json)."

    valid, verr = net_policy.validate_url(url)
    if not valid:
        return f"Blocked by network policy: {verr}"

    if dry_run:
        return f"🧪 Dry-Run [API Actuator]: Would ask Tier-2 approval to POST JSON ({len(payload)} keys) to {url}."

    if approval_cb is None:
        return "Denied: Webhook execution requires interactive Tier-2 approval."

    safe_args = {"url": url, "payload_keys": sorted(str(k) for k in payload.keys())}
    tok, deny_err = approval.request_bound_approval(
        "http_fetch",
        safe_args,
        approval_cb,
        title=f"Approve Opt-In Webhook POST to {url}?",
        target=url,
    )
    if deny_err:
        _record_api_evidence("call_approved_webhook", safe_args, "denied", deny_err)
        return deny_err
    ok, err = approval.verify_and_consume(tok, "http_fetch", safe_args)
    if not ok:
        return err

    try:
        resp = net_policy.safe_request(
            "POST",
            url,
            policy="public",
            allow_redirects=False,
            max_bytes=256_000,
            timeout=(5.0, 10.0),
            headers={"User-Agent": "Celina-Webhook-Actuator/4.4"},
            json_body=payload,
        )
        if resp.status_code >= 400:
            msg = f"Error: Webhook POST returned HTTP {resp.status_code}."
            _record_api_evidence("call_approved_webhook", safe_args, "error", msg)
            return msg
        msg = f"✅ Webhook POST succeeded ({resp.status_code}) -> {url}"
        _record_api_evidence("call_approved_webhook", safe_args, "ok", msg)
        return msg
    except net_policy.URLPolicyError as exc:
        msg = f"Blocked by network policy: {exc}"
        _record_api_evidence("call_approved_webhook", safe_args, "blocked", msg)
        return msg
    except Exception as exc:
        _record_api_evidence("call_approved_webhook", safe_args, "error", str(exc))
        return f"Error: Webhook POST failed ({exc})."
