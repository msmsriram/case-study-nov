"""Research Planner node.

Turns a raw city name into a ResearchPlan: resolved city / country / admin region,
a fixed set of intelligence categories a City Lead needs, and concrete search
queries tailored to that city (local programme names, administrative bodies,
national surveys). This is the "what does understanding a city consist of" answer.
"""
from __future__ import annotations

import logging

from langgraph.config import get_stream_writer

from ...config import settings
from ...llm import structured_call
from ..state import ResearchPlan, ResearchState

log = logging.getLogger(__name__)

# The categories are fixed by the programme's needs; the planner adapts the *queries*.
CORE_CATEGORIES = """
1. cvd_burden      - prevalence / mortality of hypertension, type 2 diabetes, dyslipidaemia, CVD; awareness, treatment, control rates
2. health_system   - public/private hospitals, primary care, cardiology capacity, screening infrastructure, health insurance coverage
3. programmes      - existing NCD / hypertension / diabetes screening & management programmes run by city, state, national or NGO actors
4. policies        - policies affecting CVD risk: NCD strategy, tobacco, salt/sugar, urban health missions, city health plans
5. stakeholders    - municipal health department, mayor/commissioner, state health ministry, medical colleges, NGOs, WHO / partner organisations
6. risks_gaps      - known data gaps, access inequities, funding constraints, environmental risk factors (air pollution), workforce shortages
""".strip()

SYSTEM = f"""You are the Research Planner for CARDIO4Cities, a global initiative that helps city governments
reduce cardiovascular disease. A City Lead is about to meet government and healthcare stakeholders in a
city they have never researched. You design the web research plan.

Produce a plan with EXACTLY these six categories (use these keys):
{CORE_CATEGORIES}

Rules for queries:
- 2 to 3 queries per category, {settings.max_queries_per_run} queries in total at most.
- Every query must name the city (or an official alias) AND, where useful, the state/province or country,
  because most health statistics are published at state or national level.
- Prefer queries that surface official / primary sources: municipal corporation, state health department,
  national health ministry, national health surveys (e.g. NFHS, STEPS, DHS), WHO country profiles,
  peer-reviewed studies, named local programmes.
- Use local terminology when you know it (e.g. the actual name of the municipal body, the national
  NCD programme, the state health mission). Do NOT invent programme names you are not sure exist;
  use generic phrasing instead.
- Queries are plain search-engine strings, no quotes, no boolean operators.
- Spell health terms out instead of using bare acronyms that collide with organisation names: write
  "non-communicable diseases" (or the local-language term, e.g. "maladies non transmissibles") rather than "NCD".
- Where the country's working language is not English, write about half of the queries in that language:
  official and municipal sources are usually published in it.

Resolve the city carefully: give the country, the admin region (state / province / county) and any
aliases (former names, local-language names). If the city name is ambiguous, pick the most likely
large city and record that as an assumption. Record every assumption you make."""


# apostrophes become a space, not nothing: French elisions ("d'acces", "l'air") must not be glued into one word
_PUNCT = str.maketrans({"‑": "-", "‐": "-", "–": "-", "—": "-", "’": " ", "‘": " ",
                        "“": "", "”": "", '"': "", "'": " "})


def _clean_query(q: str) -> str:
    """Search engines dislike typographic dashes/quotes that LLMs love to emit."""
    return " ".join(q.translate(_PUNCT).split())


def _dedupe(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        s = _clean_query(s)
        k = s.lower()
        if k and k not in seen:
            seen.add(k)
            out.append(s)
    return out


def plan_node(state: ResearchState) -> dict:
    writer = get_stream_writer()
    city = state["city_input"].strip()
    writer({"stage": "planning", "message": f"Planning research for {city}"})

    plan, usage = structured_call(
        "planner", ResearchPlan, SYSTEM,
        f"City to research: {city}\nToday's context: the City Lead needs a briefing within days.",
        max_tokens=2500, reasoning_effort="medium",
    )

    # Query hygiene: dedupe, guarantee the city name is present, cap the total.
    queries: list[str] = []
    query_category: dict[str, str] = {}
    names = [plan.city] + plan.aliases
    for cat in plan.categories:
        cleaned = []
        for q in _dedupe(cat.queries):
            if not any(n.lower() in q.lower() for n in names if n):
                q = f"{plan.city} {q}"
            cleaned.append(q)
        cat.queries = cleaned
        for q in cleaned:
            if q not in query_category and len(queries) < settings.max_queries_per_run:
                queries.append(q)
                query_category[q] = cat.key

    writer({"stage": "planning", "message": f"{len(plan.categories)} categories, {len(queries)} queries",
            "plan": plan.model_dump()})
    return {
        "plan": plan, "queries": queries, "query_category": query_category, "stage": "searching",
        "stats": {"planner_tokens": usage.get("total_tokens", 0), "queries": len(queries)},
    }
