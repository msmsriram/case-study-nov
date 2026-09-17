"""Claim extraction: one fan-out task per usable document (LangGraph Send).

Guard rails against fabrication, applied in code after the model call:
  1. every claim must carry a verbatim quote;
  2. the quote must be found in the source text (exact after whitespace/case
     normalisation, or a near-verbatim match). Claims that fail are moved to
     `rejected_claims` and never reach the fact checker or the datastores;
  3. the evidence window (text around the quote) is attached so the fact checker and
     the UI can show *where* a claim came from.
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re

from langgraph.config import get_stream_writer
from langgraph.types import Send

from ...config import settings
from ...llm import LLMParseError, model_for, structured_call
from ...research.models import ResearchItem
from ..passages import select_passages
from ..state import Claim, ExtractInput, ExtractionResult, ResearchState

log = logging.getLogger(__name__)

SYSTEM = """You extract atomic, checkable claims from a web source for a city cardiovascular-health briefing.
Audience: a CARDIO4Cities City Lead preparing to meet government and healthcare stakeholders.

Extract only claims that are useful for understanding the city's cardiovascular / NCD landscape:
statistics (prevalence, mortality, awareness/treatment/control rates, facility counts), programmes,
policies, stakeholders and organisations, infrastructure, risks and gaps.

Hard rules:
- The `quote` must be copied EXACTLY from the passage text, 8-60 words. Never paraphrase inside the quote.
- Never invent numbers, names, years or attitudes. If the passage does not state it, do not claim it.
- `geo_level` describes what the EVIDENCE refers to. If a number is for the whole country, geo_level is
  "national" even though we are researching a city. If it is for the state/province/county, use "state" or
  "district". Only use "city" or "metro" when the passage clearly refers to the city itself.
- `statement` must be self-contained and name the place the evidence refers to
  (e.g. "In Kenya, 24% of adults aged 18-69 have hypertension (STEPS 2015)", not "prevalence is 24%").
- Prefer fewer, precise claims over many vague ones. Maximum {max_claims} claims.
- If the source is irrelevant (e.g. a clinic advert with no facts), return no claims and source_relevance "none".
- Relevance comes before extraction. The source must actually be about health, healthcare, health policy or
  cardiovascular risk factors. Acronyms collide: "NCD" can be an organisation's name (for example a conservation
  NGO), "HTA" or "CVD" can mean unrelated things. If the passage is about another subject, a true and well-quoted
  claim is still useless: return no claims and source_relevance "none"."""


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[‘’“”\"'`]", "", s)
    s = re.sub(r"[^a-z0-9%.,;:()/-]+", " ", s)
    return " ".join(s.split())


def _locate_quote(quote: str, text: str) -> tuple[bool, int]:
    """Return (verified, char offset in original text or -1)."""
    q, t = _norm(quote), _norm(text)
    if not q:
        return False, -1
    pos = t.find(q)
    if pos >= 0:
        return True, _approx_offset(text, pos, len(t))
    # near-verbatim: longest common block must cover >= 75% of the quote
    sm = difflib.SequenceMatcher(None, t, q, autojunk=False)
    m = sm.find_longest_match(0, len(t), 0, len(q))
    if m.size >= 0.75 * len(q) and m.size >= 30:
        return True, _approx_offset(text, m.a, len(t))
    return False, -1


def _approx_offset(text: str, norm_pos: int, norm_len: int) -> int:
    return int(len(text) * (norm_pos / max(norm_len, 1)))


def _window(text: str, offset: int, radius: int = 260) -> str:
    if offset < 0:
        return ""
    a, b = max(0, offset - radius), min(len(text), offset + radius)
    return ("…" if a > 0 else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


def fan_out_extraction(state: ResearchState) -> list[Send]:
    plan = state["plan"]
    sends: list[Send] = []
    for key, doc in state["documents"].items():
        result = state["search_results"][key]
        if not ResearchItem(result=result, document=doc).usable:
            continue
        hints = sorted({state["query_category"][q] for q in state["url_queries"].get(key, []) if q in state["query_category"]})
        sends.append(Send("extract_claims", ExtractInput(run_id=state["run_id"], doc_key=key, plan=plan,
                                                         document=doc, result=result, category_hints=hints)))
    return sends


def extract_claims_node(inp: ExtractInput) -> dict:
    writer = get_stream_writer()
    doc, res, plan = inp["document"], inp["result"], inp["plan"]
    names = [plan.city] + plan.aliases + ([plan.admin_region] if plan.admin_region else [])
    cats = inp["category_hints"] or [c.key for c in plan.categories]
    passages, n_paras = select_passages(doc.text, city_names=names, categories=cats)
    if not passages:
        return {"stats": {f"skipped_no_passages:{inp['doc_key'][:40]}": 1}}

    user = (
        f"City being researched: {plan.city}, {plan.country}"
        + (f" (admin region: {plan.admin_region})" if plan.admin_region else "") + "\n"
        f"Category keys available: {', '.join(c.key for c in plan.categories)}\n"
        f"Categories this source was found for: {', '.join(cats)}\n\n"
        f"SOURCE\ntitle: {doc.title or res.title}\npublisher: {doc.sitename or doc.hostname}\n"
        f"published: {doc.published_date or 'unknown'}\nsource tier: {res.source_tier}\nurl: {doc.final_url}\n\n"
        f"PASSAGES (selected from {n_paras} paragraphs)\n{passages}"
    )
    try:
        result, usage = structured_call("extractor", ExtractionResult,
                                        SYSTEM.format(max_claims=settings.max_claims_per_document), user,
                                        max_tokens=1800, reasoning_effort="low")
    except LLMParseError as e:
        return {"errors": [f"extract:{inp['doc_key']}: {e}"]}
    except Exception as e:  # noqa: BLE001
        log.exception("extraction failed for %s", doc.final_url)
        return {"errors": [f"extract:{inp['doc_key']}: {type(e).__name__}: {e}"[:300]]}

    accepted: list[Claim] = []
    rejected: list[Claim] = []
    for ec in result.claims[: settings.max_claims_per_document]:
        ok, off = _locate_quote(ec.quote, doc.text)
        cid = hashlib.sha1(f"{doc.final_url}|{ec.statement}".encode()).hexdigest()[:10]
        claim = Claim(**ec.model_dump(), id=cid, source_url=doc.final_url, source_title=doc.title or res.title,
                      source_tier=res.source_tier, published_date=doc.published_date,
                      evidence_window=_window(doc.text, off) if ok else "", quote_verified=ok)
        if ec.category not in {c.key for c in plan.categories}:
            claim.category = cats[0]
        (accepted if ok else rejected).append(claim)

    writer({"stage": "extracting", "message": f"{len(accepted)} claims from {doc.sitename or doc.hostname}"
            + (f" ({len(rejected)} rejected: quote not in source)" if rejected else ""),
            "doc_key": inp["doc_key"], "relevance": result.source_relevance})
    return {"claims": accepted, "rejected_claims": rejected,
            "stats": {"extractor_tokens": usage.get("total_tokens", 0)}}


def collect_claims_node(state: ResearchState) -> dict:
    """Reduce step after the extraction fan-out."""
    writer = get_stream_writer()
    n, r = len(state.get("claims", [])), len(state.get("rejected_claims", []))
    writer({"stage": "verifying", "message": f"{n} grounded claims collected, {r} rejected; starting independent verification"})
    return {"stage": "verifying", "stats": {"claims_extracted": n, "claims_rejected_grounding": r,
                                            "extractor_model": model_for("extractor")}}
