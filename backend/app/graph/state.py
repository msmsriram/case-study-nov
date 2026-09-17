"""LangGraph state for one city-research run.

Design notes
- The state is the single source of truth while a run is in flight; the datastores
  (relational / vector / graph) are written by the `store` node from this state.
- Collections that several parallel nodes append to use `operator.add` reducers.
  Keyed collections (by URL) use `merge_dicts` so a fan-out never clobbers siblings.
- Every object carries provenance; nothing is stored without a source URL.
"""
from __future__ import annotations

import operator
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from ..research.models import CrawlDecision, FetchedDocument, SearchResult, SourceTier


def merge_dicts(left: dict, right: dict) -> dict:
    return {**left, **right}


# --------------------------------------------------------------------------- plan
class ResearchCategory(BaseModel):
    key: str = Field(description="snake_case id, e.g. cvd_burden")
    name: str = Field(description="short human label")
    why: str = Field(description="one sentence: what a City Lead needs from this category")
    queries: list[str] = Field(description="2-4 concrete web search queries, each naming the city")


class ResearchPlan(BaseModel):
    city: str
    country: str
    country_code: str | None = Field(default=None, description="ISO 3166-1 alpha-2 code of the country, lowercase, e.g. ke, in, br")
    admin_region: str | None = Field(default=None, description="state / province / county the city sits in, or null")
    aliases: list[str] = Field(default_factory=list, description="other names the city is known by")
    population_hint: str | None = Field(default=None, description="approximate population if known, else null")
    categories: list[ResearchCategory]
    assumptions: list[str] = Field(default_factory=list, description="anything the planner assumed about the city")


# ------------------------------------------------------------------------- claims
GeoLevel = Literal["city", "metro", "district", "state", "national", "global", "unknown"]
ClaimType = Literal["statistic", "programme", "policy", "stakeholder", "organization", "infrastructure", "risk_or_gap", "other"]


class ExtractedClaim(BaseModel):
    """What the extractor model returns. Provenance is attached in code, not by the model."""
    category: str = Field(description="one of the plan's category keys")
    claim_type: ClaimType
    statement: str = Field(description="single sentence, self-contained, names the place the evidence refers to")
    quote: str = Field(description="verbatim excerpt from the passage that supports the statement, 8-60 words, copied exactly")
    geo_level: GeoLevel = Field(description="geographic scope the EVIDENCE refers to, not the city being researched")
    entities: list[str] = Field(default_factory=list, description="people, organisations, programmes, policies named")
    year: int | None = Field(default=None, description="year the fact refers to, if stated; else null")
    confidence: float = Field(ge=0, le=1, description="how directly the quote supports the statement")


class ExtractionResult(BaseModel):
    claims: list[ExtractedClaim]
    source_relevance: Literal["high", "medium", "low", "none"] = Field(description="how relevant the source is to the city's CVD landscape")
    note: str | None = Field(default=None, description="anything odd about the source, e.g. marketing page, outdated")


class Claim(ExtractedClaim):
    """ExtractedClaim + provenance attached in code."""
    id: str
    source_url: str
    source_title: str = ""
    source_tier: SourceTier = "other"
    published_date: str | None = None
    evidence_window: str = Field(default="", description="~500 chars of source text around the quote")
    quote_verified: bool = Field(default=False, description="quote found verbatim (or near-verbatim) in the source text")


Verdict = Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED", "MISSING"]


class ClaimVerdict(BaseModel):
    claim_id: str
    verdict: Verdict
    rationale: str = Field(description="one or two sentences; say what in the evidence supports or fails the statement")
    geo_mismatch: bool = Field(description="true if the statement presents broader-level (state/national) data as if it were city data")
    corrected_geo_level: GeoLevel = Field(description="the geographic level the evidence actually supports")
    checker_confidence: float = Field(ge=0, le=1)


class VerificationBatch(BaseModel):
    verdicts: list[ClaimVerdict]


class Verification(ClaimVerdict):
    checker_model: str = ""


class Conflict(BaseModel):
    claim_ids: list[str] = Field(description="exactly two claim ids that disagree")
    description: str = Field(description="what differs, e.g. 'prevalence 24% vs 31% for the same population and year'")


class ConflictReport(BaseModel):
    conflicts: list[Conflict]


class Gap(BaseModel):
    category: str
    description: str
    severity: Literal["low", "medium", "high"]
    suggestion: str | None = None


# --------------------------------------------------------------- fan-out payloads
class ExtractInput(TypedDict):
    run_id: str
    doc_key: str
    plan: ResearchPlan
    document: FetchedDocument
    result: SearchResult
    category_hints: list[str]


class VerifyInput(TypedDict):
    run_id: str
    plan: ResearchPlan
    batch: list[Claim]


# -------------------------------------------------------------------------- state
Stage = Literal["planning", "searching", "checking_sources", "extracting", "verifying",
                "analysing_gaps", "storing", "building_graph", "reporting", "done", "failed"]


class ResearchState(TypedDict, total=False):
    run_id: str
    city_input: str
    city_id: str                                        # slug, set by the persist node; namespace in all three stores
    stage: Stage
    graph_status: str                                   # pending | building | ready | failed | skipped

    plan: ResearchPlan
    queries: list[str]
    query_category: dict[str, str]                       # query -> category key

    search_results: Annotated[dict[str, SearchResult], merge_dicts]      # normalized_url -> result
    url_queries: Annotated[dict[str, list[str]], merge_dicts]           # normalized_url -> queries that found it
    crawl_decisions: Annotated[dict[str, CrawlDecision], merge_dicts]   # normalized_url -> decision
    documents: Annotated[dict[str, FetchedDocument], merge_dicts]       # normalized_url -> document

    claims: Annotated[list[Claim], operator.add]                  # everything extracted (incl. later-rejected)
    rejected_claims: Annotated[list[Claim], operator.add]         # failed the quote-grounding check
    verifications: Annotated[list[Verification], operator.add]
    conflicts: list[Conflict]
    verified_claim_ids: list[str]                                 # SUPPORTED / PARTIALLY_SUPPORTED only
    gaps: list[Gap]

    report_markdown: str
    stats: Annotated[dict, merge_dicts]
    errors: Annotated[list[str], operator.add]
