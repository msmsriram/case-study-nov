"""Live web search providers.

Tavily is preferred (agent-oriented, returns clean snippets, no IP blocking).
DuckDuckGo (ddgs) is the keyless fallback so the pipeline works without any API key.
Both return the same SearchResult shape.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..config import settings
from .models import SearchResult

log = logging.getLogger(__name__)


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int = 8) -> list[SearchResult]: ...


class _RateGate:
    """Serialise calls to a provider with a minimum spacing (keyless APIs throttle hard)."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


class DDGSProvider:
    name = "ddgs"

    def __init__(self):
        from ddgs import DDGS  # lazy import
        self._ddgs = DDGS()
        self._gate = _RateGate(1.5)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=12),
           retry=retry_if_exception_type(Exception), reraise=True)
    def _raw(self, query: str, max_results: int) -> list[dict]:
        self._gate.wait()
        return self._ddgs.text(query, max_results=max_results, safesearch="moderate") or []

    def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        try:
            rows = self._raw(query, max_results)
        except Exception as e:  # noqa: BLE001
            log.warning("ddgs failed for %r: %s", query, e)
            return []
        out: list[SearchResult] = []
        for i, r in enumerate(rows):
            url = r.get("href") or r.get("url")
            if not url:
                continue
            out.append(SearchResult.build(url=url, query=query, provider=self.name, rank=i + 1,
                                          title=r.get("title", ""), snippet=r.get("body", "")))
        return out


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str):
        from tavily import TavilyClient
        self._client = TavilyClient(api_key=api_key)
        self._gate = _RateGate(0.25)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
    def _raw(self, query: str, max_results: int) -> dict:
        self._gate.wait()
        return self._client.search(query=query, max_results=max_results, search_depth="basic",
                                   include_answer=False, include_raw_content=False)

    def search(self, query: str, max_results: int = 8) -> list[SearchResult]:
        try:
            data = self._raw(query, max_results)
        except Exception as e:  # noqa: BLE001
            log.warning("tavily failed for %r: %s", query, e)
            return []
        out: list[SearchResult] = []
        for i, r in enumerate(data.get("results", [])):
            out.append(SearchResult.build(url=r["url"], query=query, provider=self.name, rank=i + 1,
                                          title=r.get("title", ""), snippet=r.get("content", ""),
                                          published_date=r.get("published_date"), score=r.get("score")))
        return out


_provider: SearchProvider | None = None
_provider_lock = threading.Lock()


def get_search_provider() -> SearchProvider:
    global _provider
    with _provider_lock:
        if _provider is None:
            choice = settings.search_provider
            if choice == "auto":
                choice = "tavily" if settings.tavily_api_key else "ddgs"
            if choice == "tavily":
                if not settings.tavily_api_key:
                    raise RuntimeError("SEARCH_PROVIDER=tavily but TAVILY_API_KEY is empty")
                _provider = TavilyProvider(settings.tavily_api_key)
            else:
                _provider = DDGSProvider()
            log.info("search provider: %s", _provider.name)
        return _provider


def search_many(queries: list[str], max_results: int = 8, workers: int = 3) -> dict[str, list[SearchResult]]:
    """Run several queries; provider-level rate gate keeps us polite."""
    provider = get_search_provider()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda q: provider.search(q, max_results), queries))
    return dict(zip(queries, results))
