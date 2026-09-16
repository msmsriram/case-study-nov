"""Gap analysis: deterministic, no model call.

Turns what the run did NOT find into first-class output. A City Lead needs to know that
"no city-level hypertension prevalence exists" as much as any number we did find.
"""
from __future__ import annotations

from datetime import date

from langgraph.config import get_stream_writer

from ..state import Gap, ResearchState

_CITY_LEVELS = {"city", "metro", "district"}


def gap_analysis_node(state: ResearchState) -> dict:
    writer = get_stream_writer()
    plan = state["plan"]
    verified_ids = set(state.get("verified_claim_ids", []))
    claims = [c for c in state.get("claims", []) if c.id in verified_ids]
    vmap = {v.claim_id: v for v in state.get("verifications", [])}

    def geo(c):  # checker's corrected level wins over the extractor's
        v = vmap.get(c.id)
        return v.corrected_geo_level if v and v.geo_mismatch and v.corrected_geo_level != "unknown" else c.geo_level

    gaps: list[Gap] = []
    this_year = date.today().year

    for cat in plan.categories:
        cc = [c for c in claims if c.category == cat.key]
        city = [c for c in cc if geo(c) in _CITY_LEVELS]
        sources = {c.source_url for c in cc}
        if not cc:
            gaps.append(Gap(category=cat.key, severity="high",
                            description=f"No verified evidence found for '{cat.name}'.",
                            suggestion="Ask the municipal health department directly; check state health department reports."))
            continue
        if not city:
            gaps.append(Gap(category=cat.key, severity="medium",
                            description=f"Only state/national-level evidence for '{cat.name}'; nothing specific to {plan.city}.",
                            suggestion="Request city-level data from the municipal health department or a local academic partner."))
        if len(sources) < 2:
            gaps.append(Gap(category=cat.key, severity="low",
                            description=f"'{cat.name}' rests on a single source ({next(iter(sources))}).",
                            suggestion="Corroborate with a second independent source."))
        years = [c.year for c in cc if c.year]
        if years and max(years) < this_year - 5:
            gaps.append(Gap(category=cat.key, severity="medium",
                            description=f"Most recent dated evidence for '{cat.name}' is from {max(years)}.",
                            suggestion="Check for newer surveys or reports before the meeting."))

    # Sources we found but were not permitted to read: still worth a human look.
    denied_official = [k for k, d in state.get("crawl_decisions", {}).items()
                       if not d.allowed and state["search_results"][k].source_tier in ("government", "intergovernmental")]
    if denied_official:
        listing = "; ".join(state["search_results"][k].url for k in denied_official[:6])
        gaps.append(Gap(category="sources", severity="medium",
                        description=f"{len(denied_official)} official sources were discovered but could not be extracted "
                                    f"(robots.txt / access refused / unreachable).",
                        suggestion=f"Open manually: {listing}"))

    writer({"stage": "analysing_gaps", "message": f"{len(gaps)} gaps identified"})
    return {"gaps": gaps, "stage": "done", "stats": {"gaps": len(gaps)}}
