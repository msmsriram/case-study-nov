"""Polite fetcher + content extractor.

Only called for URLs the CrawlabilityChecker approved. Responsibilities:
  - per-domain rate limiting (honours robots crawl-delay when larger than our floor)
  - retries with backoff on 429 / 5xx / transient network errors
  - size caps (bytes and extracted characters)
  - HTML -> clean text + metadata via trafilatura; PDF -> text via pypdf
  - detection of blocked pages (captcha / JS-wall / near-empty bodies)
"""
from __future__ import annotations

import io
import logging
import re
import threading
import time
from urllib.parse import urlsplit

import httpx
import trafilatura
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings
from .cache import DiskCache
from .models import FetchedDocument, text_hash

log = logging.getLogger(__name__)

_BLOCK_PATTERNS = re.compile(
    r"(enable javascript|access denied|captcha|are you a robot|unusual traffic|"
    r"cloudflare|attention required|verify you are human|please log in|sign in to continue)",
    re.IGNORECASE,
)


class _Retryable(Exception):
    pass


def _looks_like_boilerplate(text: str) -> bool:
    """Navigation / menu dumps look like many very short lines. Real prose does not."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 8:
        return False
    short = sum(1 for ln in lines if len(ln.split()) <= 3)
    words = [len(ln.split()) for ln in lines]
    words.sort()
    median = words[len(words) // 2]
    return short / len(lines) > 0.6 and median <= 4


class DomainRateLimiter:
    def __init__(self, floor_seconds: float):
        self.floor = floor_seconds
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._meta = threading.Lock()

    def wait(self, host: str, crawl_delay: float | None = None) -> None:
        interval = max(self.floor, crawl_delay or 0.0)
        with self._meta:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            delta = time.monotonic() - self._last.get(host, 0.0)
            if delta < interval:
                time.sleep(interval - delta)
            self._last[host] = time.monotonic()


class Fetcher:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,text/plain;q=0.8,*/*;q=0.5",
                "Accept-Language": "en;q=0.9",
            },
            follow_redirects=True, timeout=settings.request_timeout_seconds,
        )
        self._limiter = DomainRateLimiter(settings.min_seconds_between_requests_per_domain)
        self._cache = DiskCache("fetch")

    # ------------------------------------------------------------------ public
    def fetch(self, url: str, crawl_delay: float | None = None) -> FetchedDocument:
        cached = self._cache.get(url)
        if cached is not None:
            return FetchedDocument.model_validate(cached)
        doc = self._fetch_live(url, crawl_delay)
        if doc.fetch_status == "ok":
            self._cache.set(url, doc.model_dump(mode="json"))
        return doc

    def _fetch_live(self, url: str, crawl_delay: float | None = None) -> FetchedDocument:
        host = urlsplit(url).netloc.lower()
        self._limiter.wait(host, crawl_delay)
        try:
            resp, body = self._get(url)
        except Exception as e:  # noqa: BLE001
            return FetchedDocument(url=url, final_url=url, status_code=0, content_type="",
                                   source_kind="text", fetch_status="error",
                                   fetch_note=f"{type(e).__name__}: {e}"[:300])
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if resp.status_code >= 400:
            return FetchedDocument(url=url, final_url=str(resp.url), status_code=resp.status_code,
                                   content_type=ctype, source_kind="text", fetch_status="error",
                                   fetch_note=f"HTTP {resp.status_code}")
        if ctype == "application/pdf" or url.lower().endswith(".pdf"):
            return self._from_pdf(url, resp, body)
        if ctype.startswith("text/plain"):
            return self._from_text(url, resp, body)
        return self._from_html(url, resp, body)

    # ----------------------------------------------------------------- network
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10),
           retry=retry_if_exception_type((_Retryable, httpx.TransportError)), reraise=True)
    def _get(self, url: str) -> tuple[httpx.Response, bytes]:
        with self._client.stream("GET", url) as r:
            if r.status_code == 429 or r.status_code >= 500:
                raise _Retryable(f"HTTP {r.status_code}")
            buf = io.BytesIO()
            for chunk in r.iter_bytes():
                buf.write(chunk)
                if buf.tell() > settings.max_document_bytes:
                    log.info("size cap hit for %s", url)
                    break
            return r, buf.getvalue()

    # -------------------------------------------------------------- extractors
    def _from_html(self, url: str, resp: httpx.Response, body: bytes) -> FetchedDocument:
        html = body.decode(resp.encoding or "utf-8", errors="replace")
        # Graded extraction: isolate the article body first; only widen if that finds nothing.
        # Landing pages / nav-heavy pages then end up as "low" quality instead of polluting evidence.
        meta: dict = {}
        text, quality = "", "low"
        for q, kw in (("high", dict(favor_precision=True)), ("medium", {})):
            doc = trafilatura.bare_extraction(
                html, url=str(resp.url), with_metadata=True, include_comments=False,
                include_tables=True,
                # meta tags / structured data only: fewer wrong dates than a full-page scan
                date_extraction_params={"extensive_search": False, "original_date": True},
                **kw)
            m = doc.as_dict() if hasattr(doc, "as_dict") else (doc or {})
            t = (m.get("text") or "").strip()
            if len(t.split()) >= 60 and not _looks_like_boilerplate(t):
                meta, text, quality = m, t, q
                break
            meta = meta or m
            text = text or t
        if not text:
            text = (trafilatura.html2txt(html) or "").strip()
        d = self._finish(url, resp, "html", text, title=meta.get("title") or "",
                         author=meta.get("author"), date=meta.get("date"),
                         sitename=meta.get("sitename"), hostname=meta.get("hostname"),
                         description=meta.get("description"), language=meta.get("language"))
        d.extraction_quality = quality
        return d

    def _from_pdf(self, url: str, resp: httpx.Response, body: bytes) -> FetchedDocument:
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(body))
            pages = reader.pages[: settings.max_pdf_pages]
            text = "\n\n".join((p.extract_text() or "") for p in pages).strip()
            info = reader.metadata or {}
            title = (info.get("/Title") or "").strip()
            author = (info.get("/Author") or None)
            truncated_pages = len(reader.pages) > settings.max_pdf_pages
        except Exception as e:  # noqa: BLE001
            return FetchedDocument(url=url, final_url=str(resp.url), status_code=resp.status_code,
                                   content_type="application/pdf", source_kind="pdf",
                                   fetch_status="error", fetch_note=f"pdf parse failed: {e}"[:300])
        d = self._finish(url, resp, "pdf", text, title=title, author=author,
                         hostname=urlsplit(str(resp.url)).netloc)
        d.truncated = d.truncated or truncated_pages
        d.extraction_quality = "medium"  # pypdf text is complete but loses layout/tables
        return d

    def _from_text(self, url: str, resp: httpx.Response, body: bytes) -> FetchedDocument:
        text = body.decode(resp.encoding or "utf-8", errors="replace").strip()
        return self._finish(url, resp, "text", text, hostname=urlsplit(str(resp.url)).netloc)

    def _finish(self, url: str, resp: httpx.Response, kind: str, text: str, **meta) -> FetchedDocument:
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        truncated = len(text) > settings.max_text_chars
        if truncated:
            text = text[: settings.max_text_chars]
        words = len(text.split())
        status, note = "ok", None
        if words < 40:
            head = text[:1500]
            if _BLOCK_PATTERNS.search(head):
                status, note = "blocked_suspected", "bot-wall / login-wall language in a near-empty page"
            else:
                status, note = "empty", "no substantive text extracted"
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        return FetchedDocument(
            url=url, final_url=str(resp.url), status_code=resp.status_code, content_type=ctype,
            source_kind=kind, title=meta.get("title") or "", author=meta.get("author"),
            published_date=meta.get("date"), sitename=meta.get("sitename"),
            hostname=meta.get("hostname"), description=meta.get("description"),
            language=meta.get("language"), text=text, word_count=words,
            text_hash=text_hash(text), truncated=truncated, fetch_status=status, fetch_note=note,
        )
