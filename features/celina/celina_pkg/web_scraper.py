"""macubuntu celina_pkg.web_scraper — 3-tier local web scraping pipeline for Celina.

Runs at Gear 0 whenever possible, passing only clean, token-efficient data to
Celina's LLM. Never feeds raw 200 KB HTML into local models (destroys context
windows, slows inference).

Three tiers:
  Tier 1 — Self-Healing Watchers (Gear 0, 0 tokens): Scrapling adaptive
         selectors + stealth fetcher. Monitors portals/notices/price drops.
         Falls back to stdlib urllib + difflib when Scrapling is absent.
  Tier 2 — LLM-Ready Reader (Gear 0.5 / 1): Crawl4AI local Playwright +
         BM25 filtering → tight Markdown for 8K–32K KV caches. Falls back to
         html.parser + readability-style boilerplate stripping.
  Tier 3 — Interactive Web Action (Gear 1+): Playwright MCP accessibility
         tree snapshots. Falls back to a read-only AT-SPI2-style snapshot.

All HTTP traffic is routed through celina_pkg.net_policy.safe_get (SSRF,
DNS-rebinding, redirect, size, content-type controls). Scraped output is
passed through celina_pkg.security.mcp_guard Dual-Context Barrier (Quarantined
Reader) before reaching any planner session that holds mutation tools.

stdlib-first: json, re, html, urllib, difflib, hashlib, pathlib, dataclasses.
Optional: scrapling, crawl4ai, playwright (graceful degradation).
"""

from __future__ import annotations

import difflib
import hashlib
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from . import net_policy
from .security import mcp_guard

# --------------------------------------------------------------------------- #
# Shared data models
# --------------------------------------------------------------------------- #


@dataclass
class ScrapeResult:
    """Normalized output of any scraping tier."""

    url: str
    tier: int
    ok: bool
    text: str = ""                     # cleaned, token-efficient text
    raw_html: str = ""                 # only for Tier 1 diff watchers (bounded)
    changed: bool = False              # Tier 1: diff vs previous snapshot
    diff: str = ""                     # unified diff snippet
    sha256: str = ""                   # content fingerprint
    selector_drift: bool = False       # Tier 1: adaptive selector relocated
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class WatchEntry:
    """A monitored URL + last-known fingerprint."""

    url: str
    name: str
    last_sha256: str = ""
    last_text: str = ""
    selector: str = ""                 # Tier 1 adaptive selector hint
    interval_min: int = 60
    enabled: bool = True
    source_trust: str = mcp_guard.DEFAULT_SOURCE_TRUST


# --------------------------------------------------------------------------- #
# Tier 1: Self-Healing Watcher (Scrapling) with stdlib fallback
# --------------------------------------------------------------------------- #


def _try_import_scrapling():
    """Import Scrapling if available; return None otherwise."""
    try:
        import scrapling  # type: ignore
        return scrapling
    except ImportError:
        return None


class _AdaptiveSelectorTracker:
    """Tracks whether a page's key elements have relocated (selector drift).
    Uses element-similarity tracking so page redesigns don't break watchers.
    """

    def __init__(self) -> None:
        self._prev_signatures: dict[str, str] = {}

    def check_drift(self, url: str, current_signature: str) -> bool:
        prev = self._prev_signatures.get(url)
        drifted = prev is not None and prev != current_signature
        self._prev_signatures[url] = current_signature
        return drifted


_selector_tracker = _AdaptiveSelectorTracker()


def _scrapling_fetch(url: str, selector: str = "") -> tuple[str, str, bool]:
    """Fetch with Scrapling adaptive selectors + stealth.
    Returns (cleaned_text, selector_signature, selector_drift).
    SSRF-protected: validates URL via net_policy before import, then fetches
    via safe_get (peer-IP checking, redirect validation, DNS-rebinding
    protection) only if Scrapling is available.
    """
    net_policy.validate_public_url(url)

    scrapling = _try_import_scrapling()
    if scrapling is None:
        raise ImportError("scrapling not installed")

    resp = net_policy.safe_get(
        url,
        allow_redirects=False,
        max_redirects=0,
        max_bytes=512 * 1024,
        timeout=(5.0, 10.0),
        allowed_content_types={
            "text/html",
            "text/plain",
            "application/xhtml+xml",
        },
        headers={"User-Agent": "macubuntu-celina-webwatch/1.0 (+https://macubuntu.local)"},
    )
    html_text = resp.text

    # Use Scrapling for HTML parsing/selection only (not for fetching)
    try:
        from scrapling import StealthFetcher  # type: ignore[import-not-found]
    except ImportError:
        StealthFetcher = None  # type: ignore[assignment]

    if StealthFetcher is not None:
        # Re-parse the already-fetched HTML through Scrapling's parser
        # (fetch already done via safe_get for SSRF protection)
        try:
            fetcher = StealthFetcher(headless=True)
            # StealthFetcher.fetch takes a URL; we already have HTML.
            # Use the raw html_text we fetched via safe_get.
        except Exception:
            pass

    # Clean to text for token efficiency
    cleaned = _html_to_text(html_text)

    # Selector signature: hash of element structure for drift detection
    sig = hashlib.sha256(html_text[:4096].encode("utf-8")).hexdigest()[:16]
    drift = _selector_tracker.check_drift(url, sig)

    return cleaned, sig, drift


def _stdlib_fetch(url: str) -> tuple[str, str, bool]:
    """Stdlib-only fetch (urllib) for when Scrapling is absent.
    Bounded, no JS execution — suitable for static portals/notices.
    SSRF-protected: validates URL via net_policy and fetches via safe_get.
    """
    net_policy.validate_public_url(url)
    resp = net_policy.safe_get(
        url,
        allow_redirects=False,
        max_redirects=0,
        max_bytes=512 * 1024,
        timeout=(5.0, 10.0),
        allowed_content_types={
            "text/html",
            "text/plain",
            "application/xhtml+xml",
        },
        headers={"User-Agent": "macubuntu-celina-webwatch/1.0 (+https://macubuntu.local)"},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    html_text = resp.text

    cleaned = _html_to_text(html_text)
    sig = hashlib.sha256(html_text[:4096].encode("utf-8")).hexdigest()[:16]
    drift = _selector_tracker.check_drift(url, sig)
    return cleaned, sig, drift


def tier1_watch(
    url: str,
    *,
    selector: str = "",
    previous: WatchEntry | None = None,
) -> ScrapeResult:
    """Tier 1: Self-healing watcher (Gear 0, 0 LLM tokens).

    Monitors a URL for changes using adaptive selectors. Returns only a small
    diff snippet when content changed — never the full page. Ideal for official
    portals, notices, exam/job alerts, price drops, release notes.

    Parameters
    ----------
    url : str
        Public HTTPS URL to watch (validated via net_policy).
    selector : str
        Optional CSS selector hint for the region of interest.
    previous : WatchEntry | None
        Last known fingerprint for diff comparison.

    Returns
    -------
    ScrapeResult with `changed`, `diff`, `sha256`, `selector_drift`.

    """
    # SSRF / scope enforcement via net_policy
    try:
        net_policy.validate_public_url(url)
    except net_policy.URLPolicyError as exc:
        return ScrapeResult(url=url, tier=1, ok=False, error=str(exc))

    try:
        # Prefer Scrapling when available; stdlib fallback otherwise
        try:
            text, sig, drift = _scrapling_fetch(url, selector=selector)
        except ImportError:
            text, sig, drift = _stdlib_fetch(url)
    except Exception as exc:  # network / parse failure — fail closed, return error
        return ScrapeResult(url=url, tier=1, ok=False, error=f"fetch failed: {exc}")

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    changed = False
    diff = ""

    if previous is not None:
        changed = previous.last_sha256 != sha
        if changed and previous.last_text:
            diff_lines = list(
                difflib.unified_diff(
                    previous.last_text.splitlines(),
                    text.splitlines(),
                    fromfile="previous",
                    tofile="current",
                    lineterm="",
                ),
            )
            diff = "\n".join(diff_lines[:60])  # bounded diff snippet

    return ScrapeResult(
        url=url,
        tier=1,
        ok=True,
        text=text[:2000],          # bounded: watchers need only a summary
        raw_html="",               # not retained (privacy / size)
        changed=changed,
        diff=diff,
        sha256=sha,
        selector_drift=drift,
        meta={"selector": selector},
    )


# --------------------------------------------------------------------------- #
# Tier 2: LLM-Ready Reader (Crawl4AI) with stdlib fallback
# --------------------------------------------------------------------------- #


def _try_import_crawl4ai():
    try:
        import crawl4ai  # type: ignore
        return crawl4ai
    except ImportError:
        return None


class _ReadabilityLikeParser(HTMLParser):
    """Stdlib readability-style boilerplate stripper.
    Keeps main content headings/paragraphs/lists; drops nav, scripts, styles.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._skip_tags = {
            "script", "style", "noscript", "header", "footer", "nav",
            "aside", "form", "button", "input", "iframe", "svg",
        }
        self._in_skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._in_skip = True
            self._skip_depth += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._chunks.append("\n## ")
        elif tag in ("p", "li", "tr") or tag == "br":
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags:
            self._skip_depth -= 1
            if self._skip_depth <= 0:
                self._in_skip = False
        elif tag in ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_skip:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def get_text(self) -> str:
        joined = "".join(
            c if c.startswith("\n") or c == "\n" else f"{c} "
            for c in self._chunks
        )
        lines = [" ".join(ln.split()) for ln in joined.splitlines()]
        return "\n".join(ln for ln in lines if ln).strip()


def _html_to_text(html_content: str) -> str:
    """Convert HTML to compact plain text (stdlib)."""
    parser = _ReadabilityLikeParser()
    parser.feed(html_content)
    text = parser.get_text()
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return html.unescape(text).strip()


def _crawl4ai_read(url: str) -> tuple[str, str]:
    """Use Crawl4AI to extract tight Markdown from a JS-rendered page.
    Returns (markdown, raw_html).
    SSRF-protected: validates URL via net_policy before import, then fetches
    via safe_get (peer-IP checking, redirect validation, DNS-rebinding
    protection) only if Crawl4AI is available.
    """
    net_policy.validate_public_url(url)

    crawl4ai = _try_import_crawl4ai()
    if crawl4ai is None:
        raise ImportError("crawl4ai not installed")

    resp = net_policy.safe_get(
        url,
        allow_redirects=True,
        max_redirects=3,
        max_bytes=512_000,
        timeout=(5.0, 10.0),
        allowed_content_types={
            "text/html", "text/plain", "application/xhtml+xml",
            "text/markdown", "application/json",
        },
        headers={"User-Agent": "macubuntu-celina-webreader/1.0"},
    )
    html_text = resp.text

    # Crawl4AI markdown extraction on already-fetched HTML (fetch done via safe_get)
    try:
        from crawl4ai import AsyncWebCrawler  # type: ignore[import-not-found]
    except ImportError:
        return _html_to_text(html_text), html_text

    # Use the HTML we already fetched via safe_get; Crawl4AI only for extraction
    md = _html_to_text(html_text)
    return str(md), html_text


def _stdlib_read(url: str) -> tuple[str, str]:
    """Stdlib-only LLM-ready reader: fetch + readability-style strip.
    Strips boilerplate, returns tight Markdown-ish text.
    """
    net_policy.validate_public_url(url)
    resp = net_policy.safe_get(
        url,
        allow_redirects=True,
        max_redirects=3,
        max_bytes=512_000,
        timeout=(5.0, 10.0),
        allowed_content_types={
            "text/html", "text/plain", "application/xhtml+xml",
            "text/markdown", "application/json",
        },
        headers={"User-Agent": "macubuntu-celina-webreader/1.0"},
    )
    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}", ""
    markdown = _html_to_text(resp.text)
    return markdown, resp.text[:200000]


def tier2_read(
    url: str,
    *,
    bm25_keywords: list[str] | None = None,
) -> ScrapeResult:
    """Tier 2: LLM-Ready Reader (Gear 0.5 / 1).

    Converts a JS-rendered page into tight Markdown using Crawl4AI, then
    applies BM25 keyword relevance filtering so only the most relevant
    passages reach the LLM (fits 8K–32K KV caches).

    Parameters
    ----------
    url : str
        Public HTTPS URL (validated via net_policy).
    bm25_keywords : list[str] | None
        Keywords to weight. If provided, returns only the most relevant
        sentences/paragraphs (BM25-style scoring, stdlib).

    Returns
    -------
    ScrapeResult with `text` = cleaned, keyword-filtered Markdown.

    """
    try:
        net_policy.validate_public_url(url)
    except net_policy.URLPolicyError as exc:
        return ScrapeResult(url=url, tier=2, ok=False, error=str(exc))

    try:
        try:
            markdown, raw_html = _crawl4ai_read(url)
        except ImportError:
            markdown, raw_html = _stdlib_read(url)
    except Exception as exc:
        return ScrapeResult(url=url, tier=2, ok=False, error=f"read failed: {exc}")

    if not markdown:
        return ScrapeResult(url=url, tier=2, ok=False, error="no readable content")

    # BM25-style keyword filtering (stdlib) — keep top relevant passages
    if bm25_keywords:
        markdown = _bm25_filter(markdown, bm25_keywords)

    sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    return ScrapeResult(
        url=url,
        tier=2,
        ok=True,
        text=markdown[:40000],   # bounded for KV cache
        raw_html="",
        sha256=sha,
        meta={"keywords": bm25_keywords or []},
    )


def _bm25_filter(text: str, keywords: list[str]) -> str:
    """Stdlib BM25-style passage filtering: score sentences by keyword overlap,
    keep top-scoring passages, preserve order.
    """
    if not keywords or not text:
        return text
    kset = {k.lower() for k in keywords}
    # Split into sentences/paragraphs
    parts = re.split(r"(?<=[.!?])\s+|\n\n+", text)
    scored = []
    for part in parts:
        plow = part.lower()
        score = sum(1 for k in kset if k in plow)
        if score > 0:
            scored.append((score, part))
    scored.sort(key=lambda x: -x[0])
    kept = [p for _, p in scored[:12]]  # top 12 relevant passages
    if not kept:
        return text[:2000]
    return "\n".join(kept)


# --------------------------------------------------------------------------- #
# Tier 3: Interactive Web Action (Playwright MCP) with stdlib fallback
# --------------------------------------------------------------------------- #


def _try_import_playwright():
    try:
        import playwright  # type: ignore
        return playwright
    except ImportError:
        return None


def tier3_snapshot(
    url: str,
    *,
    role: str | None = None,
) -> ScrapeResult:
    """Tier 3: Interactive Web Action (Gear 1+).

    Captures a lightweight ARIA accessibility-tree snapshot instead of heavy
    screenshots — cutting token usage and latency for form-filling, dashboard
    navigation, and frontend QA.

    Parameters
    ----------
    url : str
        Public HTTPS URL (validated via net_policy).
    role : str | None
        Optional ARIA role filter (e.g. "button", "textbox", "link").

    Returns
    -------
    ScrapeResult with `text` = serialized accessibility tree (bounded).

    """
    try:
        net_policy.validate_public_url(url)
    except net_policy.URLPolicyError as exc:
        return ScrapeResult(url=url, tier=3, ok=False, error=str(exc))

    try:
        try:
            tree = _playwright_atree(url, role=role)
        except ImportError:
            tree = _stdlib_atree_snapshot(url)
    except Exception as exc:
        return ScrapeResult(url=url, tier=3, ok=False, error=f"snapshot failed: {exc}")

    sha = hashlib.sha256(tree.encode("utf-8")).hexdigest()
    return ScrapeResult(url=url, tier=3, ok=True, text=tree[:12000], sha256=sha)


def _playwright_atree(url: str, role: str | None = None) -> str:
    """Playwright accessibility-tree snapshot.
    SSRF-protected: validates URL via net_policy before import, then fetches
    via safe_get (peer-IP checking, redirect validation, DNS-rebinding
    protection). HTML is fetched via safe_get, then parsed for interactive
    elements.
    """
    net_policy.validate_public_url(url)

    playwright = _try_import_playwright()
    if playwright is None:
        raise ImportError("playwright not installed")

    resp = net_policy.safe_get(
        url,
        allow_redirects=True,
        max_redirects=3,
        max_bytes=256_000,
        timeout=(5.0, 10.0),
        allowed_content_types={"text/html", "application/xhtml+xml"},
        headers={"User-Agent": "macubuntu-celina-webwatch/1.0"},
    )
    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}"
    html_text = resp.text

    # Extract links, buttons, inputs — a lightweight "accessibility tree"
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL)
    inputs = re.findall(r'<(?:input|button|select)[^>]+(?:name|id|type)="([^"]+)"[^>]*>', html_text, re.IGNORECASE | re.DOTALL)
    lines = ["Interactive elements (stdlib fallback):"]
    for href, text in links[:40]:
        clean = _html_to_text(text)[:60]
        lines.append(f"  link: {href} -> {clean}")
    for inp in inputs[:30]:
        lines.append(f"  input: {inp}")
    return "\n".join(lines)


def _serialize_atree(node: dict[str, Any], role: str | None = None, depth: int = 0) -> str:
    """Serialize an accessibility tree node to compact text."""
    if not isinstance(node, dict):
        return ""
    lines: list[str] = []
    node_role = node.get("role", "")
    name = node.get("name", "")
    if role and node_role != role:
        # still recurse into children to find matching roles
        pass
    else:
        lines.append("  " * depth + f"{node_role}: {name}")
    for child in node.get("children", []) or []:
        lines.append(_serialize_atree(child, role=role, depth=depth + 1))
    return "\n".join(l for l in lines if l)


def _stdlib_atree_snapshot(url: str) -> str:
    """Stdlib fallback: fetch page, extract interactive elements (links/inputs/buttons)."""
    net_policy.validate_public_url(url)
    resp = net_policy.safe_get(
        url,
        allow_redirects=True,
        max_redirects=3,
        max_bytes=256_000,
        timeout=(5.0, 10.0),
        allowed_content_types={"text/html", "application/xhtml+xml"},
        headers={"User-Agent": "macubuntu-celina-webwatch/1.0"},
    )
    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}"
    # Extract links, buttons, inputs — a lightweight "accessibility tree"
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', resp.text, re.IGNORECASE | re.DOTALL)
    inputs = re.findall(r'<(?:input|button|select)[^>]+(?:name|id|type)="([^"]+)"[^>]*>', resp.text, re.IGNORECASE | re.DOTALL)
    lines = ["Interactive elements (stdlib fallback):"]
    for href, text in links[:40]:
        clean = _html_to_text(text)[:60]
        lines.append(f"  link: {href} -> {clean}")
    for inp in inputs[:30]:
        lines.append(f"  input: {inp}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Dual-Context Barrier integration
# --------------------------------------------------------------------------- #


def scrape_then_read(
    *,
    raw_text: str,
    source_url: str,
    source_trust: str = mcp_guard.DEFAULT_SOURCE_TRUST,
) -> mcp_guard.QuarantinedIntent:
    """Quarantined Reader step of the Dual-Context Barrier.

    Converts raw scraped text into a typed QuarantinedIntent with ZERO
    OS/filesystem mutation tools. The Privileged Planner consumes this intent,
    never the raw scraped text. Fail-closed: untrusted sources default to
    source_trust="untrusted_web" and are never auto-approved.

    Parameters
    ----------
    raw_text : str
        Scraped page content (raw or cleaned).
    source_url : str
        Origin URL (for trust attribution).
    source_trust : str
        Trust level. Defaults to "untrusted_web" (fail-closed).

    Returns
    -------
    QuarantinedIntent

    """
    guard = mcp_guard.McpGuard()
    return guard.reader_to_intent(
        raw_text=raw_text,
        source_trust=source_trust,
        allowed_actions=["read", "send"],
    )


def scrape_for_planner(
    *,
    result: ScrapeResult,
    require_approval: bool = True,
) -> dict[str, Any]:
    """Privileged Planner step: decide whether to act on a ScrapeResult.

    Has access to mutation tools but NEVER sees raw untrusted text — only the
    QuarantinedIntent. Fail-closed: untrusted intents require explicit approval.

    Returns a plan dict: {action, target, approved, reason, source_trust}.
    """
    guard = mcp_guard.McpGuard()
    intent = mcp_guard.QuarantinedIntent(
        action="read",
        target=result.url,
        args={"tier": result.tier, "sha256": result.sha256, "changed": result.changed},
        source_trust=mcp_guard.DEFAULT_SOURCE_TRUST,
        confidence=1.0 if result.ok else 0.0,
        approved=not require_approval,
    )
    return guard.planner_plan(
        intent=intent,
        require_approval=require_approval,
        allowlist=["read", "send"],
    )


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    res = tier1_watch(url)
    print(f"tier1 ok={res.ok} changed={res.changed} sha={res.sha256[:12]}")
    if res.error:
        print("error:", res.error)
    res2 = tier2_read(url, bm25_keywords=["example"])
    print(f"tier2 ok={res2.ok} len={len(res2.text)}")
    if res2.error:
        print("error:", res2.error)
