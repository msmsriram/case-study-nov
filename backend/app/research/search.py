"""Live web search providers, used as a fallback chain.

    serper (Google results, key pool with rotation)  ->  tavily  ->  ollama web search  ->  ddgs (keyless)

Every provider returns the same SearchResult shape. Search is *discovery only*: whatever a
provider returns, the URL still has to pass our crawlability gate before we read the page.
(Ollama's search returns whole-page text; we deliberately keep only a snippet of it.)
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import settings
from ..keys import KeyRing, NoKeyAvailable, numbered_env
from .cache import DiskCache
from .models import SearchResult

log = logging.getLogger(__name__)


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int = 8, gl: str | None = None) -> list[SearchResult]: ...


class _RateGate:
    """Serialise calls to a provider with a minimum spacing."""

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


# --------------------------------------------------------------------------- Serper
class SerperProvider:
    """google.serper.dev - 1 credit per query (num<=10). Free keys allow 5 req/s."""
    name = "serper"

    def __init__(self, keys: list[str]):
        self._ring = KeyRing("serper", keys, per_key_concurrency=2)
        self._gate = _RateGate(0.22)
        self._http = httpx.Client(timeout=20)

    def search(self, query: str, max_results: int = 8, gl: str | None = None) -> list[SearchResult]:
        body = {"q": query, "num": min(max_results, 10)}
        if gl:
            body["gl"] = gl
        for _ in range(len(self._ring) + 1):
            try:
                with self._ring.lease(wait_seconds=30) as key:
                    self._gate.wait()
                    r = self._http.post("https://google.serper.dev/search", json=body,
                                        headers={"X-API-KEY": key, "Content-Type": "application/json"})
                    if r.status_code == 200:
                        return [SearchResult.build(url=o["link"], query=query, provider=self.name,
                                                   rank=o.get("position", i + 1), title=o.get("title", ""),
                                                   snippet=o.get("snippet", ""), published_date=o.get("date"))
                                for i, o in enumerate(r.json().get("organic", [])) if o.get("link")]
                    if r.status_code == 429:
                        self._ring.retire(key, 5, "rate limited")
                    elif r.status_code in (400, 401, 402, 403):
                        self._ring.retire(key, 3600, f"HTTP {r.status_code} {r.text[:80]}")  # out of credits / bad key
                    else:
                        log.warning("serper HTTP %s for %r", r.status_code, query)
                        return []
            except NoKeyAvailable as e:
                log.warning("%s", e)
                return []
            except httpx.HTTPError as e:
                log.warning("serper network error for %r: %s", query, e)
                return []
        return []


# --------------------------------------------------------------------------- Tavily
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

    def search(self, query: str, max_results: int = 8, gl: str | None = None) -> list[SearchResult]:
        try:
            data = self._raw(query, max_results)
        except Exception as e:  # noqa: BLE001
            log.warning("tavily failed for %r: %s", query, e)
            return []
        return [SearchResult.build(url=r["url"], query=query, provider=self.name, rank=i + 1,
                                   title=r.get("title", ""), snippet=r.get("content", ""),
                                   published_date=r.get("published_date"), score=r.get("score"))
                for i, r in enumerate(data.get("results", []))]


# --------------------------------------------------------------------------- Ollama web search
class OllamaSearchProvider:
    name = "ollama"

    def __init__(self, keys: list[str]):
        self._ring = KeyRing("ollama-search", keys, per_key_concurrency=1)
        self._http = httpx.Client(timeout=30)

    def search(self, query: str, max_results: int = 8, gl: str | None = None) -> list[SearchResult]:
        for _ in range(len(self._ring) + 1):
            try:
                with self._ring.lease(wait_seconds=30) as key:
                    r = self._http.post(f"{settings.ollama_base_url}/api/web_search",
                                        json={"query": query, "max_results": min(max_results, 10)},
                                        headers={"Authorization": f"Bearer {key}"})
                    if r.status_code == 200:
                        # content is whole-page text; keep a snippet only - reading the page is the fetcher's job
                        return [SearchResult.build(url=o["url"], query=query, provider=self.name, rank=i + 1,
                                                   title=o.get("title", ""), snippet=(o.get("content") or "")[:400])
                                for i, o in enumerate(r.json().get("results", [])) if o.get("url")]
                    self._ring.retire(key, 600 if r.status_code in (401, 402, 429) else 30, f"HTTP {r.status_code}")
            except (NoKeyAvailable, httpx.HTTPError) as e:
                log.warning("ollama search failed for %r: %s", query, e)
                return []
        return []


# --------------------------------------------------------------------------- DuckDuckGo et al.
class DDGSProvider:
    """Keyless metasearch. One client per call (the shared client is not thread-safe under
    load) and a backend ladder: if one engine returns nothing, the next is tried."""
    name = "ddgs"
    _BACKENDS = ("auto", "duckduckgo", "yahoo", "startpage")

    def __init__(self):
        from ddgs import DDGS  # lazy import
        self._DDGS = DDGS
        self._gate = _RateGate(1.2)

    def search(self, query: str, max_results: int = 8, gl: str | None = None) -> list[SearchResult]:
        rows: list[dict] = []
        for backend in self._BACKENDS:
            self._gate.wait()
            try:
                rows = self._DDGS().text(query, max_results=max_results, safesearch="moderate", backend=backend) or []
                if rows:
                    break
            except Exception as e:  # noqa: BLE001
                log.debug("ddgs backend %s failed for %r: %s", backend, query, e)
        if not rows:
            log.warning("ddgs: no results for %r on any backend", query)
        return [SearchResult.build(url=r.get("href") or r.get("url"), query=query, provider=self.name, rank=i + 1,
                                   title=r.get("title", ""), snippet=r.get("body", ""))
                for i, r in enumerate(rows) if (r.get("href") or r.get("url"))]


# --------------------------------------------------------------------------- chain
class ChainProvider:
    """Try providers in order until one returns results."""

    def __init__(self, providers: list[SearchProvider]):
        self.providers = providers
        self.name = "+".join(p.name for p in providers)

    def search(self, query: str, max_results: int = 8, gl: str | None = None) -> list[SearchResult]:
        for p in self.providers:
            rows = p.search(query, max_results, gl)
            if rows:
                return rows
            log.info("search: %s returned nothing for %r, trying next provider", p.name, query)
        return []


_provider: SearchProvider | None = None
_provider_lock = threading.Lock()


def _build(name: str) -> SearchProvider | None:
    if name == "serper":
        keys = numbered_env("SERPER_API_KEY")
        return SerperProvider(keys) if keys else None
    if name == "tavily":
        return TavilyProvider(settings.tavily_api_key) if settings.tavily_api_key else None
    if name == "ollama":
        keys = numbered_env("OLLAMA_API_KEY")
        return OllamaSearchProvider(keys) if keys else None
    return DDGSProvider()


def get_search_provider() -> SearchProvider:
    global _provider
    with _provider_lock:
        if _provider is None:
            if settings.search_provider == "auto":
                chain = [p for p in (_build(n) for n in ("serper", "tavily", "ollama", "ddgs")) if p]
            else:
                first = _build(settings.search_provider)
                if first is None:
                    raise RuntimeError(f"SEARCH_PROVIDER={settings.search_provider} but no key configured")
                chain = [first] + ([DDGSProvider()] if settings.search_provider != "ddgs" else [])
            _provider = ChainProvider(chain)
            log.info("search providers: %s", _provider.name)
        return _provider


_cache = DiskCache("search")


def _cached_search(provider: SearchProvider, q: str, max_results: int, gl: str | None) -> list[SearchResult]:
    key = f"{provider.name}|{max_results}|{gl}|{q.lower().strip()}"
    hit = _cache.get(key)
    if hit is not None:
        return [SearchResult.model_validate(r) for r in hit]
    rows = provider.search(q, max_results, gl)
    if rows:
        _cache.set(key, [r.model_dump(mode="json") for r in rows])
    return rows


def search_many(queries: list[str], max_results: int = 8, workers: int = 4,
                gl: str | None = None) -> dict[str, list[SearchResult]]:
    """Run several queries concurrently; provider-level gates keep each API within its rate limit."""
    provider = get_search_provider()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda q: _cached_search(provider, q, max_results, gl), queries))
    return dict(zip(queries, results))
