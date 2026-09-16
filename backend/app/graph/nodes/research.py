"""Search -> Crawlability -> Fetch nodes.

Kept as three separate graph nodes (rather than one pipeline call) so the workflow
diagram shows the crawlability decision as an explicit gate before any fetch, and so
each stage streams its own progress and stats.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from langgraph.config import get_stream_writer

from ...config import settings
from ...research.models import CrawlDecision, FetchedDocument, ResearchItem, SearchResult
from ...research.pipeline import get_checker, get_fetcher
from ...research.search import search_many
from ..state import ResearchState

log = logging.getLogger(__name__)

# Which sources we fetch first when the per-run document cap binds.
_TIER_PRIORITY = {"government": 0, "intergovernmental": 0, "academic": 1, "ngo": 2,
                  "reference": 3, "news": 3, "commercial": 4, "other": 5}


def search_node(state: ResearchState) -> dict:
    writer = get_stream_writer()
    queries = state["queries"]
    writer({"stage": "searching", "message": f"Searching the web with {len(queries)} queries"})

    results: dict[str, SearchResult] = {}
    url_queries: dict[str, list[str]] = {}
    raw = 0
    for q, rows in search_many(queries, max_results=settings.max_results_per_query).items():
        raw += len(rows)
        for r in rows:
            results.setdefault(r.normalized_url, r)
            url_queries.setdefault(r.normalized_url, []).append(q)
    writer({"stage": "searching", "message": f"{raw} results, {len(results)} unique sources"})
    return {"search_results": results, "url_queries": url_queries, "stage": "checking_sources",
            "stats": {"raw_results": raw, "unique_urls": len(results)}}


def crawl_check_node(state: ResearchState) -> dict:
    writer = get_stream_writer()
    results = state["search_results"]
    writer({"stage": "checking_sources", "message": f"Checking crawl permission for {len(results)} sources"})
    checker = get_checker()
    keys = list(results)
    with ThreadPoolExecutor(max_workers=6) as ex:
        decisions = list(ex.map(lambda k: checker.check(results[k].url), keys))
    out: dict[str, CrawlDecision] = dict(zip(keys, decisions))
    allowed = sum(1 for d in out.values() if d.allowed)
    reasons: dict[str, int] = {}
    for d in out.values():
        if not d.allowed:
            reasons[d.reason] = reasons.get(d.reason, 0) + 1
    writer({"stage": "checking_sources", "message": f"{allowed} permitted, {len(out) - allowed} not permitted",
            "denied_reasons": reasons})
    return {"crawl_decisions": out, "stage": "extracting",
            "stats": {"crawl_allowed": allowed, "crawl_denied": len(out) - allowed, "denied_reasons": reasons}}


def _priority(state: ResearchState, key: str) -> tuple:
    r = state["search_results"][key]
    hits = len(state["url_queries"].get(key, []))
    return (_TIER_PRIORITY.get(r.source_tier, 5), -hits, r.rank)


def fetch_node(state: ResearchState) -> dict:
    writer = get_stream_writer()
    allowed = [k for k, d in state["crawl_decisions"].items() if d.allowed]
    allowed.sort(key=lambda k: _priority(state, k))
    chosen = allowed[: settings.max_documents_per_run]
    writer({"stage": "extracting", "message": f"Fetching {len(chosen)} of {len(allowed)} permitted sources"})
    fetcher = get_fetcher()
    with ThreadPoolExecutor(max_workers=6) as ex:
        docs = list(ex.map(
            lambda k: fetcher.fetch(state["search_results"][k].url, state["crawl_decisions"][k].crawl_delay_seconds),
            chosen))
    documents: dict[str, FetchedDocument] = dict(zip(chosen, docs))
    usable = sum(1 for k, d in documents.items()
                 if ResearchItem(result=state["search_results"][k], document=d).usable)
    writer({"stage": "extracting", "message": f"{usable} usable documents extracted"})
    return {"documents": documents,
            "stats": {"fetched": len(documents), "usable_documents": usable, "skipped_by_cap": len(allowed) - len(chosen)}}
