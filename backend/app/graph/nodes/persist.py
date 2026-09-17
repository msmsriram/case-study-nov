"""Knowledge Builder: writes a finished run to the three datastores.

persist      -> relational (full audit trail, incl. rejected claims) + vector (verified claims, passages)
build_graph  -> Graphiti / Neo4j (verified, relationship-bearing claims only; incremental per city)

They are two nodes on purpose: after `persist` the findings, sources, gaps and report are already
servable, while the slower graph build continues. The Q&A layer waits for graph_status == "ready".
"""
from __future__ import annotations

import asyncio
import logging

from langgraph.config import get_stream_writer
from sqlalchemy import select

from ...stores import graph as graph_store
from ...stores import relational, vector
from ..state import ResearchState

log = logging.getLogger(__name__)


def persist_node(state: ResearchState) -> dict:
    writer = get_stream_writer()
    writer({"stage": "storing", "message": "Writing audit trail to the relational store"})
    rel = relational.persist_run(state)
    writer({"stage": "storing", "message": f"{rel['verified']} verified / {rel['not_verified']} not verified / "
            f"{rel['rejected_grounding']} rejected claims, {rel['sources']} sources saved ({rel['backend']})"})
    vec = vector.index_run(state)
    writer({"stage": "storing", "message": f"Indexed {vec['indexed_claims']} claims and {vec['indexed_passages']} "
            f"evidence passages ({vec['backend']})", "findings_ready": True, "city_id": rel["city_id"]})
    return {"city_id": rel["city_id"], "stage": "building_graph", "stats": {"relational": rel, "vector": vec}}


def build_graph_node(state: ResearchState) -> dict:
    writer = get_stream_writer()
    run_id, city_id, plan = state["run_id"], state["city_id"], state["plan"]
    if not graph_store.is_configured():
        relational.set_graph_status(run_id, "skipped")
        writer({"stage": "building_graph", "message": "Neo4j not configured - knowledge graph skipped"})
        return {"graph_status": "skipped", "stage": "done"}

    verified = set(state.get("verified_claim_ids", []))
    claims = [c for c in state.get("claims", []) if c.id in verified]
    with relational.SessionLocal() as s:
        already = {cid for cid, ep in s.execute(
            select(relational.ClaimRow.id, relational.ClaimRow.graph_episode_uuid)
            .where(relational.ClaimRow.city_id == city_id)).all() if ep}
    bundles = graph_store.build_bundles(claims, already)
    n_claims = sum(len(b["claims"]) for b in bundles)
    if not bundles:
        relational.set_graph_status(run_id, "ready")
        writer({"stage": "building_graph", "message": "No new relationship claims - knowledge graph already up to date",
                "graph_ready": True})
        return {"graph_status": "ready", "stage": "done", "stats": {"graph": {"episodes": 0, "skipped_known": len(already)}}}

    relational.set_graph_status(run_id, "building")
    writer({"stage": "building_graph", "message": f"Building knowledge graph: {n_claims} verified claims in "
            f"{len(bundles)} episodes ({len(already)} already known)"})
    try:
        stats = asyncio.run(graph_store.ingest(
            city_id, f"{plan.city}, {plan.country}", bundles,
            on_progress=lambda m: writer({"stage": "building_graph", "message": m}),
            on_episode=lambda ep, ids: relational.mark_ingested(ids, ep)))
    except Exception as e:  # noqa: BLE001
        log.exception("graph build failed")
        relational.set_graph_status(run_id, "failed")
        return {"graph_status": "failed", "stage": "done", "errors": [f"graph: {type(e).__name__}: {e}"[:300]]}

    stats.pop("episode_claims", None)      # already recorded per episode
    status = "ready" if stats["episodes"] else "failed"
    relational.set_graph_status(run_id, status)
    relational.update_run_stats(run_id, {"graph": stats})   # the run record was written before the graph existed
    writer({"stage": "building_graph", "message": f"Knowledge graph {status}: {stats['nodes']} entities, "
            f"{stats['edges']} relationships in {stats['seconds']}s", "graph_ready": status == "ready"})
    return {"graph_status": status, "stage": "done", "stats": {"graph": stats}}
