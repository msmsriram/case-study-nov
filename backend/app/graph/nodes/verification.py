"""Independent fact checking.

Independence, by construction:
  - a different model (settings.groq_model_checker) from the extractor;
  - a different prompt whose only job is to falsify;
  - it sees the claim and the evidence window from the source, never the extractor's
    reasoning or confidence.

Consequences in the workflow:
  - UNSUPPORTED and MISSING claims are excluded from `verified_claim_ids`, so they never
    reach the datastores, the report or the Q&A layer (they remain visible in an audit view);
  - geo_mismatch corrects the geographic level so national data is never shown as city data;
  - a second pass compares statistics within a category and flags CONFLICTING pairs.
"""
from __future__ import annotations

import logging

from langgraph.config import get_stream_writer
from langgraph.types import Send

from ...config import settings
from ...llm import LLMParseError, model_for, structured_call
from ..state import Claim, Conflict, ConflictReport, ResearchState, Verification, VerificationBatch, VerifyInput

log = logging.getLogger(__name__)

CHECK_SYSTEM = """You are an independent fact checker. Another system extracted claims from web sources.
Your job is to try to FALSIFY each claim using only the evidence excerpt provided.

For each claim decide:
- SUPPORTED: the evidence states this, with the same numbers, population, place and time frame.
- PARTIALLY_SUPPORTED: the evidence supports the core of the statement but a detail is missing,
  approximated, or the statement adds something the evidence does not say (e.g. a year, a cause).
- UNSUPPORTED: the evidence does not say this, contradicts it, or the statement over-interprets it.
- MISSING: the evidence excerpt is empty or does not contain the quoted text at all.

Geography check: if the statement presents state/national/global data as if it were about the city
(or names the city where the evidence names a broader area), set geo_mismatch=true and give the level
the evidence actually supports in corrected_geo_level. Otherwise geo_mismatch=false and corrected_geo_level
= the level the evidence supports.

Be strict about numbers and dates. Be concise in the rationale. Never use outside knowledge to rescue a claim."""

CONFLICT_SYSTEM = """You compare statistical claims about the same topic collected from different sources.
Flag a CONFLICT only when two claims measure the same thing for the same population and geographic level
and give materially different values, and the difference is not explained by different years or different
definitions stated in the claims. Different years showing a trend are NOT a conflict. Different geographic
levels (city vs national) are NOT a conflict. If unsure, do not flag. Return an empty list if there are none."""


def fan_out_verification(state: ResearchState) -> list[Send]:
    claims = state.get("claims", [])
    size = settings.verify_batch_size
    return [Send("fact_check", VerifyInput(run_id=state["run_id"], plan=state["plan"], batch=claims[i:i + size]))
            for i in range(0, len(claims), size)]


def _fmt_claim(c: Claim) -> str:
    return (f"CLAIM id={c.id}\n  statement: {c.statement}\n  stated geo_level: {c.geo_level}; year: {c.year}\n"
            f"  source: {c.source_title} ({c.source_url})\n  quote: \"{c.quote}\"\n"
            f"  evidence excerpt: {c.evidence_window or '(none)'}\n")


def fact_check_node(inp: VerifyInput) -> dict:
    writer = get_stream_writer()
    plan, batch = inp["plan"], inp["batch"]
    user = (f"City being researched: {plan.city}, {plan.country}"
            + (f"; admin region: {plan.admin_region}" if plan.admin_region else "") + "\n\n"
            + "\n".join(_fmt_claim(c) for c in batch)
            + "\nReturn one verdict per claim id, in the same order.")
    try:
        result, usage = structured_call("checker", VerificationBatch, CHECK_SYSTEM, user,
                                        max_tokens=1800, reasoning_effort="medium")
    except LLMParseError as e:
        return {"errors": [f"verify: {e}"]}
    except Exception as e:  # noqa: BLE001
        log.exception("verification failed")
        return {"errors": [f"verify: {type(e).__name__}: {e}"[:300]]}

    ids = {c.id for c in batch}
    verdicts = [Verification(**v.model_dump(), checker_model=model_for("checker"))
                for v in result.verdicts if v.claim_id in ids]
    missing = ids - {v.claim_id for v in verdicts}
    for cid in missing:  # the checker skipped it: treat as not verified, never as verified
        verdicts.append(Verification(claim_id=cid, verdict="MISSING", rationale="checker returned no verdict",
                                     geo_mismatch=False, corrected_geo_level="unknown", checker_confidence=0.0,
                                     checker_model=model_for("checker")))
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    writer({"stage": "verifying", "message": f"batch of {len(batch)}: " + ", ".join(f"{k} {n}" for k, n in counts.items())})
    return {"verifications": verdicts, "stats": {"checker_tokens": usage.get("total_tokens", 0)}}


def detect_conflicts_node(state: ResearchState) -> dict:
    """Reduce step after verification, plus cross-claim conflict detection on statistics."""
    writer = get_stream_writer()
    verdict_by_id = {v.claim_id: v for v in state.get("verifications", [])}
    verified = [c for c in state.get("claims", [])
                if verdict_by_id.get(c.id) and verdict_by_id[c.id].verdict in ("SUPPORTED", "PARTIALLY_SUPPORTED")]

    # Geography corrections live on the Verification (corrected_geo_level); consumers apply them.
    geo_of = {c.id: (verdict_by_id[c.id].corrected_geo_level if verdict_by_id[c.id].geo_mismatch
                     and verdict_by_id[c.id].corrected_geo_level != "unknown" else c.geo_level) for c in verified}

    conflicts: list[Conflict] = []
    by_cat: dict[str, list[Claim]] = {}
    for c in verified:
        if c.claim_type == "statistic":
            by_cat.setdefault(c.category, []).append(c)
    total_tokens = 0
    for cat, claims in by_cat.items():
        if len(claims) < 2:
            continue
        user = f"Category: {cat}\n" + "\n".join(
            f"- id={c.id} | geo={geo_of[c.id]} | year={c.year} | source={c.source_title[:60]} | {c.statement}" for c in claims)
        try:
            rep, usage = structured_call("checker", ConflictReport, CONFLICT_SYSTEM, user, max_tokens=900, reasoning_effort="low")
            total_tokens += usage.get("total_tokens", 0)
            ids = {c.id for c in claims}
            conflicts += [k for k in rep.conflicts if len(k.claim_ids) == 2 and set(k.claim_ids) <= ids]
        except Exception as e:  # noqa: BLE001
            log.warning("conflict detection failed for %s: %s", cat, e)

    counts: dict[str, int] = {}
    for v in verdict_by_id.values():
        counts[v.verdict] = counts.get(v.verdict, 0) + 1
    writer({"stage": "analysing_gaps", "message": f"verified {len(verified)} of {len(state.get('claims', []))} claims; "
            f"{len(conflicts)} conflicts; verdicts: {counts}"})
    return {"verified_claim_ids": [c.id for c in verified], "conflicts": conflicts,
            "stage": "analysing_gaps",
            "stats": {"verdicts": counts, "verified": len(verified), "conflicts": len(conflicts),
                      "conflict_tokens": total_tokens, "checker_model": model_for("checker")}}
