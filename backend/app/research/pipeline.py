"""search -> crawlability check -> fetch/extract, with de-duplication and stats.

This is the function the LangGraph Search / Crawlability / Extraction nodes will call.
It is deliberately synchronous and thread-pooled: simple to reason about, easy to demo.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .crawlability import CrawlabilityChecker
from .fetcher import Fetcher
from .models import ResearchItem
from .search import search_many

log = logging.getLogger(__name__)


@dataclass
class ResearchStats:
    queries: int = 0
    raw_results: int = 0
    unique_urls: int = 0
    crawl_allowed: int = 0
    crawl_denied: int = 0
    fetched_ok: int = 0
    fetched_empty_or_blocked: int = 0
    fetch_errors: int = 0
    denied_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class ResearchBatch:
    items: list[ResearchItem]
    stats: ResearchStats


_checker: CrawlabilityChecker | None = None
_fetcher: Fetcher | None = None
_lock = threading.Lock()


def get_checker() -> CrawlabilityChecker:
    global _checker
    with _lock:
        if _checker is None:
            _checker = CrawlabilityChecker()
        return _checker


def get_fetcher() -> Fetcher:
    global _fetcher
    with _lock:
        if _fetcher is None:
            _fetcher = Fetcher()
        return _fetcher


def research_queries(queries: list[str], *, max_results_per_query: int = 8,
                     max_fetch: int | None = None, workers: int = 6) -> ResearchBatch:
    checker, fetcher = get_checker(), get_fetcher()
    stats = ResearchStats(queries=len(queries))

    # 1. search (deduplicate across queries, remember which queries surfaced each URL)
    by_url: dict[str, ResearchItem] = {}
    for q, results in search_many(queries, max_results=max_results_per_query).items():
        stats.raw_results += len(results)
        for r in results:
            item = by_url.get(r.normalized_url)
            if item is None:
                by_url[r.normalized_url] = ResearchItem(result=r, queries=[q])
            elif q not in item.queries:
                item.queries.append(q)
    items = list(by_url.values())
    stats.unique_urls = len(items)

    # 2. crawlability (before any content fetch)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        decisions = list(ex.map(lambda it: checker.check(it.result.url), items))
    for it, d in zip(items, decisions):
        it.decision = d
        if d.allowed:
            stats.crawl_allowed += 1
        else:
            stats.crawl_denied += 1
            stats.denied_reasons[d.reason] = stats.denied_reasons.get(d.reason, 0) + 1

    # 3. fetch + extract only what is permitted
    to_fetch = [it for it in items if it.decision and it.decision.allowed]
    if max_fetch is not None:
        to_fetch = to_fetch[:max_fetch]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        docs = list(ex.map(lambda it: fetcher.fetch(it.result.url, it.decision.crawl_delay_seconds), to_fetch))
    for it, doc in zip(to_fetch, docs):
        it.document = doc
        if doc.fetch_status == "ok":
            stats.fetched_ok += 1
        elif doc.fetch_status == "error":
            stats.fetch_errors += 1
        else:
            stats.fetched_empty_or_blocked += 1
    return ResearchBatch(items=items, stats=stats)
