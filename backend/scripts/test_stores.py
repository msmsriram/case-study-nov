"""Replay a saved run (samples/graph_<city>.json) through the three datastores, then query each.

    python scripts/test_stores.py nairobi [--no-graph]

No web search and no extraction happens here, so it costs no search credits.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select  # noqa: E402

from app.graph.state import Claim, Conflict, Gap, ResearchPlan, Verification  # noqa: E402
from app.research.models import CrawlDecision, FetchedDocument, SearchResult  # noqa: E402
from app.stores import graph as graph_store  # noqa: E402
from app.stores import relational as rel  # noqa: E402
from app.stores import vector  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("city", nargs="?", default="nairobi")
ap.add_argument("--no-graph", action="store_true")
ap.add_argument("--max-episodes", type=int, default=None)
args = ap.parse_args()

raw = json.loads((Path(__file__).resolve().parent.parent / "samples" / f"graph_{args.city}.json").read_text(encoding="utf-8"))
state = dict(raw)
state["plan"] = ResearchPlan.model_validate(raw["plan"])
state["search_results"] = {k: SearchResult.model_validate(v) for k, v in raw["search_results"].items()}
state["crawl_decisions"] = {k: CrawlDecision.model_validate(v) for k, v in raw["crawl_decisions"].items()}
state["documents"] = {k: FetchedDocument.model_validate(v) for k, v in raw["documents"].items()}
state["claims"] = [Claim.model_validate(c) for c in raw["claims"]]
state["rejected_claims"] = [Claim.model_validate(c) for c in raw.get("rejected_claims", [])]
state["verifications"] = [Verification.model_validate(v) for v in raw["verifications"]]
state["conflicts"] = [Conflict.model_validate(c) for c in raw.get("conflicts", [])]
state["gaps"] = [Gap.model_validate(g) for g in raw.get("gaps", [])]


def h(t):
    print("\n" + "=" * 96 + f"\n{t}\n" + "=" * 96)


h("1. RELATIONAL")
t = time.time()
r = rel.persist_run(state)
print(f"persisted in {time.time()-t:.1f}s -> {r}")
city_id = r["city_id"]
with rel.SessionLocal() as s:
    print("claims by status:", dict(s.execute(select(rel.ClaimRow.status, func.count()).where(rel.ClaimRow.city_id == city_id).group_by(rel.ClaimRow.status)).all()))
    print("verified by geo :", dict(s.execute(select(rel.ClaimRow.geo_level, func.count()).where(rel.ClaimRow.city_id == city_id, rel.ClaimRow.status == "verified").group_by(rel.ClaimRow.geo_level)).all()))
    print("sources by crawl:", dict(s.execute(select(rel.Source.crawl_allowed, func.count()).where(rel.Source.city_id == city_id).group_by(rel.Source.crawl_allowed)).all()))
    row = s.execute(select(rel.ClaimRow, rel.Source).join(rel.Source, rel.ClaimRow.source_id == rel.Source.id)
                    .where(rel.ClaimRow.city_id == city_id, rel.ClaimRow.status == "verified", rel.ClaimRow.geo_level == "city")).first()
    if row:
        c, src = row
        print(f"\n'where did this come from?' join:\n  claim   : {c.statement[:110]}\n  quote   : {c.quote[:110]}\n  verdict : {c.verdict} by {c.checker_model} - {(c.verdict_rationale or '')[:90]}"
              f"\n  source  : {src.title[:70]} [{src.source_tier}] {src.url[:70]}\n  crawl   : allowed={src.crawl_allowed} ({src.crawl_reason}), robots={src.robots_status}, fetched={src.fetched_at}")

h("2. VECTOR")
t = time.time()
v = vector.index_run(state)
print(f"indexed in {time.time()-t:.1f}s -> {v}")
for q in ("hypertension screening programmes in informal settlements", "air pollution and heart disease risk"):
    t = time.time()
    hits = vector.search(city_id, q, k=4)
    print(f"\nQ: {q}  ({time.time()-t:.2f}s)")
    for x in hits:
        print(f"   {x['score']:.3f} [{x['kind']:<7}] {x['text'][:105]}  <{x['source_tier']}>")

if not args.no_graph and graph_store.is_configured():
    h("3. GRAPH (Graphiti on Neo4j)")
    verified = set(state["verified_claim_ids"])
    with rel.SessionLocal() as s:
        already = {cid for cid, ep in s.execute(select(rel.ClaimRow.id, rel.ClaimRow.graph_episode_uuid).where(rel.ClaimRow.city_id == city_id)).all() if ep}
    bundles = graph_store.build_bundles([c for c in state["claims"] if c.id in verified], already)
    if args.max_episodes:
        bundles = bundles[: args.max_episodes]
    print(f"{sum(len(b['claims']) for b in bundles)} relationship claims -> {len(bundles)} episodes ({len(already)} claims already in graph)")
    if bundles:
        stats = asyncio.run(graph_store.ingest(city_id, f"{state['plan'].city}, {state['plan'].country}", bundles, on_progress=lambda m: print("   ", m)))
        for ep, ids in stats.pop("episode_claims").items():
            rel.mark_ingested(ids, ep)
        print("ingest:", stats)
    print("overview:", asyncio.run(graph_store.overview(city_id)))
    for q in ("Which organisations run hypertension or diabetes programmes?", "Who partners with the Ministry of Health?"):
        t = time.time()
        facts = asyncio.run(graph_store.search(city_id, q, k=5))
        print(f"\nQ: {q}  ({time.time()-t:.1f}s)")
        for f in facts:
            print(f"   ({f['source_type']}) {f['source_entity']} -[{f['relation']}]-> ({f['target_type']}) {f['target_entity']}")
            print(f"       {f['fact'][:110]}\n       provenance: {', '.join(f['source_urls'])[:100]}")
            with rel.SessionLocal() as s:
                n = s.execute(select(func.count()).select_from(rel.ClaimRow).where(rel.ClaimRow.graph_episode_uuid.in_(f["episode_uuids"]))).scalar()
            print(f"       -> {n} fact-checked claims behind this edge in the relational store")
