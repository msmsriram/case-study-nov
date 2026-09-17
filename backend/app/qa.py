"""Ask-the-city: conversational retrieval over the three stores, answers with evidence.

    question
       |- Graphiti  : relationship facts between entities (who runs / funds / partners with what)
       |- Qdrant    : semantically similar VERIFIED claims + verbatim source passages
       '- SQL       : verdict, rationale, geography level, quote, source and crawl record for every hit,
                      plus the claims behind each graph fact and the run's recorded gaps
       -> numbered evidence list [E1..En] -> answer model -> answer where every sentence cites [En]

Rules enforced here rather than hoped for:
  - the answer model sees ONLY the evidence list; citations that do not exist are stripped and reported;
  - evidence that is not city-level is labelled with its geography so national data is never passed off as local;
  - if nothing relevant is found the answer says so and returns the recorded knowledge gaps instead.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
import re
import time
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from .llm import structured_call
from .stores import graph as graph_store
from .stores import relational as rel
from .stores import vector

log = logging.getLogger(__name__)


class ResolvedQuestion(BaseModel):
    standalone_question: str = Field(description="the latest question rewritten so it can be understood with no conversation context")
    is_follow_up: bool = Field(description="true if the latest question depended on the earlier turns")


RESOLVE_SYSTEM = """You rewrite the user's latest question into a standalone question for a search system.
Use the conversation only to resolve references (it, they, that programme, the second one, what about funding?).
Keep names exactly as they appeared. Do not answer the question. Do not add facts, assumptions or constraints
that the user did not express. If the latest question is already self-contained, return it unchanged."""


class AnswerResult(BaseModel):
    answer: str = Field(description="Markdown. Every factual sentence ends with one or more citations like [E3] or [E1][E4].")
    confidence: Literal["high", "medium", "low"] = Field(description="high only if several independent, city-level, SUPPORTED items agree")
    caveats: list[str] = Field(default_factory=list, description="limits of the evidence: national-only data, old data, single source, conflicts")
    insufficient_evidence: bool = Field(description="true if the evidence does not answer the question")


SYSTEM = """You answer questions for a CARDIO4Cities City Lead preparing to meet government and healthcare stakeholders.
You may use ONLY the numbered evidence items provided. You have no other knowledge.

Rules:
- Every factual sentence must end with the citation(s) of the evidence it rests on, e.g. [E2] or [E1][E5].
- Never state a number, name, date or relationship that is not in the evidence.
- Each evidence item shows its geographic level. If it is not city-level, say so in the sentence
  ("nationally", "at county level"), never present it as a fact about the city itself.
- Items marked PARTIALLY_SUPPORTED are weaker: hedge them. Items marked 'passage' are verbatim source text
  that has not been fact-checked: use them for context only and say "according to <source>".
- If items disagree, report both values and say they conflict.
- If the evidence does not answer the question, say plainly what is not known and set insufficient_evidence=true.
  Do not pad the answer with loosely related facts.
- Be concise: a short paragraph or a few bullets. Plain language for a non-technical reader."""


def _rows_for(claim_ids: list[str]) -> dict[str, tuple]:
    if not claim_ids:
        return {}
    with rel.SessionLocal() as s:
        rows = s.execute(select(rel.ClaimRow, rel.Source).join(rel.Source, rel.ClaimRow.source_id == rel.Source.id, isouter=True)
                         .where(rel.ClaimRow.id.in_(claim_ids), rel.ClaimRow.status == "verified")).all()
    return {c.id: (c, src) for c, src in rows}


def _claims_by_episode(episode_uuids: list[str]) -> dict[str, list[str]]:
    """episode uuid -> verified claim ids, in ONE query (the hosted database is a network hop away)."""
    if not episode_uuids:
        return {}
    out: dict[str, list[str]] = {}
    with rel.SessionLocal() as s:
        for cid, ep in s.execute(select(rel.ClaimRow.id, rel.ClaimRow.graph_episode_uuid).where(
                rel.ClaimRow.graph_episode_uuid.in_(episode_uuids), rel.ClaimRow.status == "verified")).all():
            out.setdefault(ep, []).append(cid)
    return out


def city_status(city_id: str) -> dict | None:
    with rel.SessionLocal() as s:
        city = s.get(rel.City, city_id)
        if not city:
            return None
        run = s.execute(select(rel.Run).where(rel.Run.city_id == city_id).order_by(rel.Run.started_at.desc())).scalars().first()
        return {"city_id": city.id, "name": city.name, "country": city.country,
                "graph_status": run.graph_status if run else "pending", "run_id": run.id if run else None}


def retrieve(city_id: str, question: str, k_graph: int = 8, k_claims: int = 8, k_passages: int = 4) -> dict:
    timings: dict[str, float] = {}

    def graph_part():
        t = time.time()
        try:
            return (asyncio.run(graph_store.search(city_id, question, k=k_graph)) if graph_store.is_configured() else []), time.time() - t
        except Exception as e:  # noqa: BLE001
            log.warning("graph search failed: %s", e)
            return [], time.time() - t

    def vector_part():
        t = time.time()
        return (vector.search(city_id, question, k=k_claims, kind="claim"),
                vector.search(city_id, question, k=k_passages, kind="passage")), time.time() - t

    # the three stores are independent network hops: query graph and vector concurrently
    with ThreadPoolExecutor(max_workers=2) as ex:
        fg, fv = ex.submit(graph_part), ex.submit(vector_part)
        (facts, tg), ((claim_hits, passage_hits), tv) = fg.result(), fv.result()
    timings["graph_s"], timings["vector_s"] = round(tg, 2), round(tv, 2)

    t = time.time()
    episode_ids = sorted({ep for f in facts for ep in f["episode_uuids"]})
    claim_ids = [h["claim_id"] for h in claim_hits]
    with rel.SessionLocal() as s:                              # ONE connection checkout for all SQL work
        rows = {c.id: (c, src) for c, src in s.execute(
            select(rel.ClaimRow, rel.Source).join(rel.Source, rel.ClaimRow.source_id == rel.Source.id, isouter=True)
            .where(rel.ClaimRow.id.in_(claim_ids), rel.ClaimRow.status == "verified")).all()} if claim_ids else {}
        by_episode: dict[str, list[str]] = {}
        if episode_ids:
            for cid, ep in s.execute(select(rel.ClaimRow.id, rel.ClaimRow.graph_episode_uuid).where(
                    rel.ClaimRow.graph_episode_uuid.in_(episode_ids), rel.ClaimRow.status == "verified")).all():
                by_episode.setdefault(ep, []).append(cid)
        gaps = [{"category": g.category, "severity": g.severity, "description": g.description, "suggestion": g.suggestion}
                for g in s.execute(select(rel.GapRow).where(rel.GapRow.city_id == city_id).order_by(rel.GapRow.id.desc()).limit(12)).scalars()]
    evidence: list[dict] = []
    seen_claims: set[str] = set()

    for f in facts:                                           # 1. graph facts, backed by their claims in SQL
        backing = sorted({cid for ep in f["episode_uuids"] for cid in by_episode.get(ep, [])})
        evidence.append({"kind": "graph_fact", "text": f["fact"],
                         "relation": f"({f['source_type']}) {f['source_entity']} -[{f['relation']}]-> ({f['target_type']}) {f['target_entity']}",
                         "geo_level": None, "verdict": "VERIFIED_CLAIMS", "year": (f["valid_at"] or "")[:4] or None,
                         "source_urls": f["source_urls"], "source_title": None, "source_tier": None,
                         "backing_claim_ids": backing, "quote": None})
    for h in claim_hits:                                      # 2. verified claims (vector recall, SQL truth)
        if h["claim_id"] in seen_claims or h["claim_id"] not in rows:
            continue
        seen_claims.add(h["claim_id"])
        c, src = rows[h["claim_id"]]
        evidence.append({"kind": "claim", "claim_id": c.id, "text": c.statement, "quote": c.quote,
                         "geo_level": c.geo_level, "verdict": c.verdict, "verdict_rationale": c.verdict_rationale,
                         "year": c.year, "category": c.category, "in_conflict": c.in_conflict,
                         "source_urls": [c.source_url], "source_title": src.title if src else h.get("source_title"),
                         "source_tier": src.source_tier if src else h.get("source_tier"),
                         "published_date": src.published_date if src else None, "score": h["score"]})
    for h in passage_hits:                                    # 3. verbatim context
        evidence.append({"kind": "passage", "text": h["text"], "quote": None, "geo_level": None, "verdict": "UNVERIFIED_CONTEXT",
                         "year": None, "source_urls": [h["source_url"]], "source_title": h.get("source_title"),
                         "source_tier": h.get("source_tier"), "published_date": h.get("published_date"), "score": h["score"]})
    for i, e in enumerate(evidence, 1):
        e["n"] = i

    timings["sql_s"] = round(time.time() - t, 2)
    return {"evidence": evidence, "gaps": gaps, "timings": timings,
            "counts": {"graph_facts": len(facts), "claims": len(seen_claims), "passages": len(passage_hits)}}


def _format(evidence: list[dict]) -> str:
    lines = []
    for e in evidence:
        head = f"[E{e['n']}] kind={e['kind']}"
        if e["kind"] == "claim":
            head += f" | verdict={e['verdict']} | geo_level={e['geo_level']} | year={e['year']} | source={e['source_title']} ({e['source_tier']})"
            if e.get("in_conflict"):
                head += " | IN CONFLICT with another claim"
        elif e["kind"] == "graph_fact":
            head += f" | relationship={e['relation']} | backed by {len(e['backing_claim_ids'])} fact-checked claims"
        else:
            head += f" | verbatim, not fact-checked | source={e['source_title']} ({e['source_tier']})"
        lines.append(head + "\n    " + e["text"][:700])
    return "\n".join(lines)


def normalize_citations(text: str, prefix: str = "E") -> str:
    """Models vary how they write citations: 【E3】, (E3), [E3, E7], [E3-E5]. Canonicalise to [E3][E7] so that
    validation and the UI's clickable markers see every one of them."""
    text = text.replace("【", "[").replace("】", "]")
    p = re.escape(prefix)

    def expand(m: re.Match) -> str:
        body = m.group(1)
        nums: list[int] = []
        for part in re.split(r"[,;\s]+(?:and\s+)?", body):
            rng = re.fullmatch(rf"{p}?(\d+)\s*[-–]\s*{p}?(\d+)", part)
            one = re.fullmatch(rf"{p}?(\d+)", part)
            if rng and int(rng.group(2)) - int(rng.group(1)) < 12:
                nums += list(range(int(rng.group(1)), int(rng.group(2)) + 1))
            elif one:
                nums.append(int(one.group(1)))
            elif part:
                return m.group(0)                      # not a citation list: leave untouched
        return "".join(f"[{prefix}{n}]" for n in nums) if nums else m.group(0)

    text = re.sub(rf"\[({p}\d+(?:\s*[,;\-–]\s*(?:and\s+)?{p}?\d+)+)\]", expand, text)
    return re.sub(rf"\(({p}\d+)\)", r"[\1]", text)


def resolve_question(question: str, history: list[dict], city_name: str) -> tuple[str, bool]:
    """Conversational retrieval: turn a follow-up into a standalone query. Memory shapes the QUESTION only;
    evidence is always retrieved fresh, so nothing from an earlier answer can be reused as a fact."""
    turns = [m for m in history if m.get("content")][-6:]
    if not turns:
        return question, False
    convo = "\n".join(f"{m['role'].upper()}: {m['content'][:500]}" for m in turns)
    try:
        res, _ = structured_call("extractor", ResolvedQuestion, RESOLVE_SYSTEM,
                                 f"City: {city_name}\n\nCONVERSATION\n{convo}\n\nLATEST QUESTION: {question}",
                                 max_tokens=300, reasoning_effort="low")
        sq = res.standalone_question.strip()
        return (sq, res.is_follow_up and sq.lower() != question.strip().lower()) if sq else (question, False)
    except Exception as e:  # noqa: BLE001 - never block an answer on the rewrite
        log.warning("question rewrite failed: %s", e)
        return question, False


def answer(city_id: str, question: str, history: list[dict] | None = None) -> dict:
    status = city_status(city_id)
    if status is None:
        return {"error": "unknown_city", "message": f"No research found for '{city_id}'. Research the city first."}
    t0 = time.time()
    asked = question
    question, rewritten = resolve_question(asked, history or [], status["name"])
    t_resolve = round(time.time() - t0, 2)
    got = retrieve(city_id, question)
    got["timings"]["resolve_s"] = t_resolve
    ev = got["evidence"]
    if not ev:
        return {"city": status, "question": asked, "resolved_question": question, "rewritten": rewritten, "answer": "I found no stored evidence relevant to this question.", "cited": [], "stores_used_in_answer": [],
                "insufficient_evidence": True, "confidence": "low", "caveats": [], "evidence": [], "gaps": got["gaps"],
                "retrieval": got["counts"], "timings": got["timings"]}

    user = (f"City: {status['name']}, {status['country']}\nQuestion: {question}\n\nEVIDENCE\n{_format(ev)}\n\n"
            "KNOWN GAPS recorded for this city (mention only if relevant to the question):\n"
            + "\n".join(f"- ({g['severity']}) {g['description']}" for g in got["gaps"][:8]))
    t = time.time()
    res, usage = structured_call("answer", AnswerResult, SYSTEM, user, max_tokens=1200, reasoning_effort="low")
    got["timings"]["llm_s"] = round(time.time() - t, 2)

    res.answer = normalize_citations(res.answer)
    valid = {e["n"] for e in ev}
    cited = {int(x) for x in re.findall(r"\[E(\d+)\]", res.answer)}
    invalid = sorted(cited - valid)
    text = res.answer
    for n in invalid:                                         # never show a citation that points nowhere
        text = text.replace(f"[E{n}]", "")
    used = sorted(cited & valid)
    kinds = {e["kind"] for e in ev if e["n"] in used}
    got["timings"]["total_s"] = round(time.time() - t0, 2)
    return {"city": status, "question": asked, "resolved_question": question, "rewritten": rewritten, "answer": text, "confidence": res.confidence, "caveats": res.caveats,
            "insufficient_evidence": res.insufficient_evidence, "cited": used, "invalid_citations_removed": invalid,
            "stores_used_in_answer": sorted(kinds), "evidence": ev, "gaps": got["gaps"] if res.insufficient_evidence else [],
            "retrieval": got["counts"], "timings": got["timings"], "tokens": usage.get("total_tokens")}
