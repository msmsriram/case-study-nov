"""Data contracts for the research layer.

Everything downstream (extraction, fact-checking, storage) consumes these shapes,
so provenance fields (url, title, publisher, dates, retrieval time) live here.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "igshid",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication: lowercase host, no fragment,
    no tracking params, no trailing slash on paths."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() not in _TRACKING_PARAMS]
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", host, path, query, ""))


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


SourceTier = Literal["government", "intergovernmental", "academic", "ngo", "reference", "news", "commercial", "other"]

_ACADEMIC_HOSTS = (
    "ncbi.nlm.nih.gov", "pubmed", "doi.org", "thelancet.com", "bmj.com", "nature.com",
    "springer.com", "wiley.com", "sciencedirect.com", "plos.org", "jamanetwork.com",
    "ahajournals.org", "nejm.org", "frontiersin.org", "mdpi.com", "tandfonline.com",
    "oup.com", "cambridge.org", "elsevier.com", "biomedcentral.com", "medrxiv.org",
    "annalsofglobalhealth.org", "ijcm", "ijph", "scholar",
)
_INTERGOV_HOSTS = ("who.int", "un.org", "worldbank.org", "unicef.org", "undp.org", "oecd.org",
                   "europa.eu", "paho.org", "healthdata.org", "dcp-3.org", "ghdx")
_NEWS_HINTS = ("news", "times", "express", "herald", "tribune", "post", "hindu", "mint",
               "reuters", "bbc", "cnn", "guardian", "deccan", "chronicle", "telegraph", "daily")


def classify_source_tier(url: str) -> SourceTier:
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    # second-level conventions used by most country TLDs: gov.in, go.ke, gob.mx, gouv.fr, ac.uk, co.ke, or.ke ...
    sld = labels[-2] if len(labels) >= 3 else ""
    if any(h in host for h in _INTERGOV_HOSTS):
        return "intergovernmental"
    # literature databases first: pubmed / PMC live on a .gov domain but are academic sources
    if any(h in host for h in _ACADEMIC_HOSTS):
        return "academic"
    if labels[-1] in ("gov", "mil") or sld in ("gov", "go", "gob", "gouv", "govt", "nic", "gc"):
        return "government"
    if labels[-1] == "edu" or sld in ("edu", "ac"):
        return "academic"
    if "wikipedia.org" in host or "wikidata.org" in host:
        return "reference"
    if any(h in host for h in _NEWS_HINTS):
        return "news"
    if labels[-1] in ("org", "ngo", "int") or sld in ("org", "or", "ngo"):
        return "ngo"
    if labels[-1] in ("com", "co", "net", "io", "biz", "in") or sld in ("com", "co"):
        return "commercial"
    return "other"


class SearchResult(BaseModel):
    url: str
    normalized_url: str
    title: str = ""
    snippet: str = ""
    query: str
    provider: str
    rank: int
    published_date: str | None = None
    score: float | None = None
    source_tier: SourceTier = "other"

    @classmethod
    def build(cls, *, url: str, query: str, provider: str, rank: int, **kw) -> "SearchResult":
        return cls(url=url, normalized_url=normalize_url(url), query=query,
                   provider=provider, rank=rank, source_tier=classify_source_tier(url), **kw)


class CrawlDecision(BaseModel):
    """Output of the crawlability check. Produced BEFORE any content is fetched."""
    url: str
    allowed: bool
    reason: str
    robots_url: str | None = None
    robots_status: Literal["allowed", "disallowed", "no_robots", "unreachable", "not_checked"] = "not_checked"
    crawl_delay_seconds: float | None = None
    probe_status_code: int | None = None
    probe_content_type: str | None = None
    policy_flags: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utcnow)


class FetchedDocument(BaseModel):
    url: str
    final_url: str
    status_code: int
    content_type: str
    source_kind: Literal["html", "pdf", "text"]
    title: str = ""
    author: str | None = None
    published_date: str | None = None
    sitename: str | None = None
    hostname: str | None = None
    description: str | None = None
    language: str | None = None
    text: str = ""
    word_count: int = 0
    text_hash: str = ""
    truncated: bool = False
    # high = article body isolated with precision; medium = default extraction;
    # low = whole-page text fallback (likely navigation noise)
    extraction_quality: Literal["high", "medium", "low", "n/a"] = "n/a"
    fetch_status: Literal["ok", "empty", "blocked_suspected", "error"] = "ok"
    fetch_note: str | None = None
    fetched_at: datetime = Field(default_factory=utcnow)


class ResearchItem(BaseModel):
    """One discovered source with its crawl decision and (if allowed) its content."""
    result: SearchResult
    decision: CrawlDecision | None = None
    document: FetchedDocument | None = None
    queries: list[str] = Field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Content good enough to extract claims from. Low-quality (navigation-only)
        pages are kept as discovered sources but never mined for facts."""
        return (self.document is not None and self.document.fetch_status == "ok"
                and self.document.word_count >= 80
                and self.document.extraction_quality != "low")
