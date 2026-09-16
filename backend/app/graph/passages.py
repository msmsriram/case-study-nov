"""Deterministic passage selection: choose the parts of a document worth sending to the LLM.

With an 8K tokens/minute budget we cannot send whole documents. Paragraphs are scored on
city names, category vocabulary, health vocabulary and the presence of numbers, then the
top ones are kept in original order up to a token budget. No model call involved.
"""
from __future__ import annotations

import re

from ..config import settings
from ..llm import estimate_tokens

CATEGORY_TERMS: dict[str, list[str]] = {
    "cvd_burden": ["prevalence", "mortality", "deaths", "incidence", "burden", "hypertension", "blood pressure",
                   "diabetes", "cholesterol", "dyslipid", "stroke", "heart", "cardiovascular", "ncd", "awareness",
                   "control", "treated", "survey", "steps", "nfhs", "dhs"],
    "health_system": ["hospital", "clinic", "primary care", "primary health", "facility", "facilities", "cardiolog",
                      "beds", "doctors", "nurses", "workforce", "insurance", "referral", "tertiary", "capacity"],
    "programmes": ["programme", "program", "screening", "initiative", "campaign", "pilot", "launched", "rollout",
                   "community health", "outreach", "intervention", "project", "partnership"],
    "policies": ["policy", "strategy", "strategic plan", "act", "regulation", "law", "tax", "tobacco", "salt",
                 "sugar", "guideline", "mission", "framework", "budget", "action plan"],
    "stakeholders": ["ministry", "department", "county", "municipal", "corporation", "commissioner", "mayor",
                     "governor", "director", "secretary", "minister", "ngo", "foundation", "university", "college",
                     "who", "partner", "association", "society"],
    "risks_gaps": ["gap", "lack", "shortage", "barrier", "inequit", "access", "afford", "out-of-pocket",
                   "pollution", "air quality", "funding", "underreport", "data", "unavailable", "challenge"],
}
_HEALTH_TERMS = ["hypertens", "diabet", "cardiovascular", "cardiac", "heart", "stroke", "ncd",
                 "non-communicable", "noncommunicable", "blood pressure", "cholesterol", "obesity"]
_NUM = re.compile(r"\d")


def _paragraphs(text: str, max_words: int = 130) -> list[str]:
    out: list[str] = []
    for block in re.split(r"\n\s*\n|\n(?=\S)", text):
        words = block.split()
        if len(words) < 12:
            continue
        for i in range(0, len(words), max_words):
            out.append(" ".join(words[i:i + max_words]))
    return out


def select_passages(text: str, *, city_names: list[str], categories: list[str],
                    budget_tokens: int | None = None) -> tuple[str, int]:
    """Return (joined passages, number of paragraphs considered)."""
    budget = budget_tokens or settings.max_passage_tokens_per_document
    paras = _paragraphs(text)
    if not paras:
        return "", 0
    names = [n.lower() for n in city_names if n]
    cat_terms = [t for c in categories for t in CATEGORY_TERMS.get(c, [])]

    scored: list[tuple[float, int, str]] = []
    for idx, p in enumerate(paras):
        low = p.lower()
        s = 0.0
        s += 3.0 * sum(low.count(n) for n in names)
        s += 1.5 * sum(1 for t in cat_terms if t in low)
        s += 1.0 * sum(1 for t in _HEALTH_TERMS if t in low)
        s += 1.0 if _NUM.search(p) else 0.0
        s += 0.5 if idx < 3 else 0.0            # leads / abstracts are usually informative
        scored.append((s, idx, p))
    scored.sort(key=lambda x: (-x[0], x[1]))

    chosen: list[tuple[int, str]] = []
    used = 0
    for s, idx, p in scored:
        if s <= 0.5:
            break
        t = estimate_tokens(p)
        if used + t > budget:
            continue
        chosen.append((idx, p))
        used += t
        if used >= budget * 0.95:
            break
    chosen.sort()
    return "\n\n".join(p for _, p in chosen), len(paras)
