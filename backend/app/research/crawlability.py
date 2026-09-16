"""Crawlability detection: decide whether a URL may be fetched, BEFORE fetching it.

Checks, in order (cheapest first, first failure wins):
  1. URL sanity (http/https only)
  2. Platform policy: sites whose terms forbid automated extraction or that are login-walled
  3. robots.txt for our user agent (Protego parser, cached per host)
  4. Lightweight HEAD probe: status code and content type, without downloading the body

The decision object records every check so the UI can show *why* a source was skipped.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from protego import Protego

from ..config import settings
from .models import CrawlDecision

log = logging.getLogger(__name__)

# Domains whose terms of service prohibit scraping or which are login-walled / JS-only.
# We still keep these URLs as *discovered* sources; we just never extract from them.
_DENY_SUFFIXES: dict[str, str] = {
    "facebook.com": "platform terms prohibit automated extraction",
    "instagram.com": "platform terms prohibit automated extraction",
    "twitter.com": "platform terms prohibit automated extraction",
    "x.com": "platform terms prohibit automated extraction",
    "linkedin.com": "platform terms prohibit automated extraction (login wall)",
    "tiktok.com": "platform terms prohibit automated extraction",
    "pinterest.com": "platform terms prohibit automated extraction",
    "quora.com": "login-walled, low-provenance content",
    "youtube.com": "video platform; no extractable text",
    "scribd.com": "login-walled document host",
    "researchgate.net": "blocks automated access; use publisher/DOI instead",
    "academia.edu": "login-walled",
}

_ALLOWED_CONTENT_PREFIXES = ("text/html", "application/xhtml", "application/pdf", "text/plain")


@dataclass
class _RobotsEntry:
    status: str            # fetched | no_robots | unreachable
    parser: Protego | None
    robots_url: str


class RobotsCache:
    def __init__(self, client: httpx.Client):
        self._client = client
        self._cache: dict[str, _RobotsEntry] = {}
        self._lock = threading.Lock()

    def get(self, url: str) -> _RobotsEntry:
        parts = urlsplit(url)
        host_key = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if host_key in self._cache:
                return self._cache[host_key]
        entry = self._fetch(host_key)
        with self._lock:
            self._cache[host_key] = entry
        return entry

    def _fetch(self, host_key: str) -> _RobotsEntry:
        robots_url = f"{host_key}/robots.txt"
        for attempt in range(2):
            try:
                r = self._client.get(robots_url, timeout=8.0)
                if r.status_code == 200 and "text" in r.headers.get("content-type", "text/plain"):
                    return _RobotsEntry("fetched", Protego.parse(r.text), robots_url)
                if r.status_code >= 500:
                    continue  # transient? retry once, then RFC 9309: unreachable => disallow
                # RFC 9309 s2.3.1.3: 4xx (incl. 401/403) => no restrictions
                return _RobotsEntry("no_robots", None, robots_url)
            except httpx.HTTPError as e:
                log.debug("robots fetch error %s (attempt %d): %s", robots_url, attempt, e)
                break  # network-level failure: do not burn another timeout
        return _RobotsEntry("unreachable", None, robots_url)


class CrawlabilityChecker:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            headers={"User-Agent": settings.user_agent, "Accept": "*/*"},
            follow_redirects=True, timeout=settings.request_timeout_seconds,
        )
        self._robots = RobotsCache(self._client)
        self._ua_token = self._bot_token(settings.user_agent)

    @staticmethod
    def _bot_token(ua: str) -> str:
        # "Mozilla/5.0 (compatible; CARDIO4CitiesResearchBot/0.1; +url)" -> "CARDIO4CitiesResearchBot"
        if "compatible;" in ua:
            tail = ua.split("compatible;", 1)[1].strip()
            return tail.split("/")[0].split(";")[0].strip()
        return ua.split("/")[0]

    # ------------------------------------------------------------------ public
    def check(self, url: str, probe: bool = True) -> CrawlDecision:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return CrawlDecision(url=url, allowed=False, reason="unsupported URL scheme",
                                 policy_flags=["bad_scheme"])

        host = parts.netloc.lower().split(":")[0]
        for suffix, why in _DENY_SUFFIXES.items():
            if host == suffix or host.endswith("." + suffix):
                return CrawlDecision(url=url, allowed=False, reason=why,
                                     robots_status="not_checked", policy_flags=["platform_policy"])

        entry = self._robots.get(url)
        delay: float | None = None
        if entry.status == "fetched" and entry.parser is not None:
            rp = entry.parser
            allowed_bot = rp.can_fetch(url, self._ua_token)
            allowed_any = rp.can_fetch(url, "*")
            delay = rp.crawl_delay(self._ua_token) or rp.crawl_delay("*")
            if not (allowed_bot and allowed_any):
                return CrawlDecision(url=url, allowed=False, reason="robots.txt disallows this path",
                                     robots_url=entry.robots_url, robots_status="disallowed",
                                     crawl_delay_seconds=delay)
            robots_status = "allowed"
        elif entry.status == "no_robots":
            robots_status = "no_robots"
        else:
            if settings.robots_unreachable_policy == "deny":
                return CrawlDecision(url=url, allowed=False,
                                     reason="robots.txt unreachable; conservative policy denies crawl",
                                     robots_url=entry.robots_url, robots_status="unreachable")
            robots_status = "unreachable"

        status_code: int | None = None
        ctype: str | None = None
        flags: list[str] = []
        if probe:
            status_code, ctype, flags = self._probe(url)
            if status_code is not None and status_code in (401, 402, 403, 404, 406, 410, 451):
                return CrawlDecision(url=url, allowed=False, reason=f"HTTP {status_code} on probe",
                                     robots_url=entry.robots_url, robots_status=robots_status,
                                     crawl_delay_seconds=delay, probe_status_code=status_code,
                                     probe_content_type=ctype, policy_flags=flags)
            if ctype and not ctype.startswith(_ALLOWED_CONTENT_PREFIXES):
                return CrawlDecision(url=url, allowed=False, reason=f"unsupported content type {ctype}",
                                     robots_url=entry.robots_url, robots_status=robots_status,
                                     crawl_delay_seconds=delay, probe_status_code=status_code,
                                     probe_content_type=ctype, policy_flags=flags)

        return CrawlDecision(url=url, allowed=True, reason="permitted",
                             robots_url=entry.robots_url, robots_status=robots_status,
                             crawl_delay_seconds=delay, probe_status_code=status_code,
                             probe_content_type=ctype, policy_flags=flags)

    # ----------------------------------------------------------------- helpers
    def _probe(self, url: str) -> tuple[int | None, str | None, list[str]]:
        """HEAD request; fall back to a streamed GET that reads no body if HEAD is refused."""
        flags: list[str] = []
        try:
            r = self._client.head(url, timeout=10.0)
            if r.status_code in (405, 501, 403):
                flags.append("head_refused")
                with self._client.stream("GET", url, timeout=10.0) as g:
                    return g.status_code, g.headers.get("content-type", "").split(";")[0].strip() or None, flags
            return r.status_code, r.headers.get("content-type", "").split(";")[0].strip() or None, flags
        except httpx.HTTPError as e:
            flags.append(f"probe_error:{type(e).__name__}")
            return None, None, flags
