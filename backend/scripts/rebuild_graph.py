"""Rebuild one city's knowledge graph from the relational store (the system of record).

    python scripts/rebuild_graph.py nairobi-ke

Deletes the city's namespace in Neo4j, clears the episode mapping in SQL, and re-ingests every
verified relationship claim. Proof that the graph is a derived, reproducible asset.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select, update  # noqa: E402

from app.stores import graph as gs  # noqa: E402
from app.stores import relational as rel  # noqa: E402

city_id = sys.argv[1] if len(sys.argv) > 1 else "nairobi-ke"


async def wipe():
    _, d = gs._graphiti()
    try:
        recs, _, _ = await d.execute_query("MATCH (n) WHERE n.group_id = $g DETACH DELETE n RETURN count(n) AS c", g=city_id)
        return recs[0]["c"]
    finally:
        await d.close()


with rel.SessionLocal() as s:
    city = s.get(rel.City, city_id)
    if not city:
        sys.exit(f"unknown city {city_id}")
    run = s.execute(select(rel.Run).where(rel.Run.city_id == city_id).order_by(rel.Run.started_at.desc())).scalars().first()
    s.execute(update(rel.ClaimRow).where(rel.ClaimRow.city_id == city_id).values(graph_episode_uuid=None))
    s.commit()
    rows = s.execute(select(rel.ClaimRow, rel.Source).join(rel.Source, rel.ClaimRow.source_id == rel.Source.id, isouter=True)
                     .where(rel.ClaimRow.city_id == city_id, rel.ClaimRow.status == "verified")).all()

claims = [SimpleNamespace(id=c.id, claim_type=c.claim_type, source_url=c.source_url, statement=c.statement, year=c.year,
                          source_title=(src.title if src else ""), source_tier=(src.source_tier if src else "other"))
          for c, src in rows]
print(f"wiped {asyncio.run(wipe())} graph nodes for {city_id}")
bundles = gs.build_bundles(claims, set())
print(f"{sum(len(b['claims']) for b in bundles)} verified relationship claims -> {len(bundles)} episodes")
rel.set_graph_status(run.id, "building")
stats = asyncio.run(gs.ingest(city_id, f"{city.name}, {city.country}", bundles, on_progress=lambda m: print("  ", m, flush=True),
                              on_episode=lambda ep, ids: rel.mark_ingested(ids, ep)))
stats.pop("episode_claims", None)
rel.set_graph_status(run.id, "ready" if stats["episodes"] else "failed")
print("ingest:", stats)
print("overview:", asyncio.run(gs.overview(city_id)))
