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

## Layer 2: Workflow (`app/graph/`) and LLM access (`app/llm.py`)

```bash
cd backend
python scripts/test_graph.py "Nairobi, Kenya"     # streams progress, prints plan + stats
```

**Graph** (`app/graph/builder.py`, LangGraph 1.2): `START → plan → search → crawl_check → fetch → …`
The diagram is regenerated to `docs/workflow.mmd` on every test run. Search, crawlability
and fetch are separate nodes on purpose: the crawlability decision is an explicit gate in
the workflow, not a helper hidden inside a fetch function. Nodes stream progress with
`get_stream_writer`, so the API can push stage updates to the UI.

**State** (`app/graph/state.py`): one `ResearchState` per run. Keyed collections
(`search_results`, `crawl_decisions`, `documents`, keyed by normalised URL) use a dict-merge
reducer; lists (`claims`, `verifications`, `errors`) use `operator.add`, so parallel fan-out
nodes never overwrite each other. Pydantic models in state are whitelisted for checkpoint
serialisation (`ALLOWED_STATE_TYPES`).

**Planner** (`nodes/planner.py`): one structured call to the large model. The six
intelligence categories are fixed by the programme's needs; the model adapts *queries* to the
city (admin region, aliases, national surveys, local bodies) and records its assumptions.
Query hygiene strips typographic punctuation, de-duplicates and guarantees the city name.

**Fetch prioritisation** (`nodes/research.py`): when the per-run document cap binds,
government and intergovernmental sources go first, then academic, NGO, reference/news,
commercial; ties broken by how many queries surfaced the URL.

**LLM layer** (`app/llm.py`): Groq, routed by role.

| Role | Model | Why |
|---|---|---|
| planner | `openai/gpt-oss-120b` | judgement, once per run |
| extractor | `openai/gpt-oss-20b` | volume work: one call per document |
| checker | `openai/gpt-oss-120b` | independent of the extractor by construction |
| answer | `openai/gpt-oss-120b` | synthesis with citations |

The free tier allows ~8K tokens/min **per model**. `TokenBudget` is a per-model sliding
window that blocks a call until it fits; usage is accumulated per role. All calls use strict
JSON-schema structured output. Set `GROQ_MODEL_*` in `.env` to re-route.

### Known limitations of this layer

- No JavaScript rendering. SPA-only pages come back as `empty` or `low` quality.
- DuckDuckGo occasionally returns no results for a query; Tavily is far more reliable and
  is the recommended provider.
- Landing pages that link to the real document (e.g. WHO country-profile pages that link
  to a PDF) are graded `low`; following the PDF link is a planned improvement.
