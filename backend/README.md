# CARDIO4Cities City Intelligence – Backend

## Setup

```bash
# from the repo root, with case_venv activated
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env    # then fill in keys
```

Run the research-layer smoke test for any city:

```bash
cd backend
python scripts/test_research.py "Nairobi, Kenya"
```

Output: a table of every discovered source with its crawl decision, source tier, extraction
quality, word count and date, plus the full structured result in `samples/research_<city>.json`.

## Layer 1: Research (`app/research/`)

The foundation every agent builds on. Three separate responsibilities, in a fixed order:

| Module | Responsibility |
|---|---|
| `search.py` | Live web search at request time. Tavily when `TAVILY_API_KEY` is set, DuckDuckGo (keyless) otherwise. Same `SearchResult` shape from both. |
| `crawlability.py` | Decides **before any fetch** whether a source permits automated extraction. Produces a `CrawlDecision` with the reason. |
| `fetcher.py` | Polite fetch + extraction for approved URLs only. HTML via trafilatura, PDF via pypdf. Produces a `FetchedDocument` with provenance metadata. |
| `pipeline.py` | search → de-duplicate → crawlability → fetch. Returns items + stats. This is what the LangGraph nodes call. |
| `models.py` | Pydantic contracts shared with every downstream layer. |

### Crawlability decision, in order

1. **URL sanity** – http/https only.
2. **Platform policy** – domains whose terms prohibit scraping or that are login-walled
   (social networks, ResearchGate, Scribd, ...) are never fetched.
3. **robots.txt** – parsed with Protego, cached per host, checked for both our bot token
   and `*`. Crawl-delay is honoured by the fetcher.
   Unreachable robots.txt (5xx / timeout) ⇒ **deny**, per RFC 9309 §2.3.1.4. Configurable
   via `ROBOTS_UNREACHABLE_POLICY`.
4. **HEAD probe** – status code and content type without downloading the body.
   401/403/404/406/410/451 or a non-text content type ⇒ deny.

### Decisions worth defending

- **We identify honestly** as `CARDIO4CitiesResearchBot`. Several publishers (Nature,
  Springer, some Indian government portals behind WAFs) refuse that identity while serving
  browsers normally. We treat a refusal as "this source does not permit our bot" and do not
  spoof a browser. Cost: roughly a third of discovered URLs are not fetched.
- **Denied sources are not discarded.** They stay in the knowledge base as *discovered,
  not extracted*, with the search snippet retained as low-confidence evidence. The City
  Lead can still see that the Ministry page exists and open it manually.
- **Source tiers** (`government`, `intergovernmental`, `academic`, `ngo`, `reference`,
  `news`, `commercial`) are assigned from the domain at discovery time and feed source
  weighting downstream.
- **Extraction quality is graded**: `high` = article body isolated with precision mode,
  `medium` = default extraction or PDF text, `low` = navigation-heavy page (detected by a
  short-line heuristic). Only `high`/`medium` documents are mined for claims.
- **Dates are taken from meta tags / structured data only**, not a full-page scan; fewer
  dates, but fewer wrong ones. A missing date is surfaced as unknown, never guessed.
- **Rate limiting is per domain** (1 s floor, or robots crawl-delay if larger), with
  retry/backoff on 429 and 5xx, and hard caps on bytes and extracted characters.

### Known limitations of this layer

- No JavaScript rendering. SPA-only pages come back as `empty` or `low` quality.
- DuckDuckGo occasionally returns no results for a query; Tavily is far more reliable and
  is the recommended provider.
- Landing pages that link to the real document (e.g. WHO country-profile pages that link
  to a PDF) are graded `low`; following the PDF link is a planned improvement.
