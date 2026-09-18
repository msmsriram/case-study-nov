"""HTTP API for the City Intelligence app.

    POST /api/research                 start a run for any city (live research)        -> {run_id}
    GET  /api/runs/{run_id}/events     Server-Sent Events: stage-by-stage progress
    GET  /api/runs/{run_id}            run status + stats
    GET  /api/cities                   researched cities (the reusable intelligence assets)
    GET  /api/cities/{id}              overview: counts by category / geography / verdict, gaps, graph status
    GET  /api/cities/{id}/claims       findings with verdict, quote, evidence window and source   (filters)
    GET  /api/cities/{id}/sources      every discovered source with its crawlability decision
    GET  /api/cities/{id}/graph        entities + relationships for visualisation (from Neo4j)
    POST /api/cities/{id}/ask          question answering over graph + vector + SQL, with citations
    GET  /api/cities/{id}/report       downloadable briefing (?format=md|html)
    GET  /api/workflow                 the LangGraph workflow as Mermaid
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import Counter

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import qa, report
from .graph.builder import build_graph, mermaid
from .llm import usage_snapshot
from .stores import graph as graph_store
from .stores import relational as rel
from .config import BACKEND_DIR
from .stores import vector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="CARDIO4Cities City Intelligence", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------- run manager
class RunHandle:
    def __init__(self, run_id: str, city_input: str):
        self.run_id, self.city_input = run_id, city_input
        self.events: list[dict] = []
        self.status = "running"            # running | complete | failed
        self.city_id: str | None = None
        self.cv = threading.Condition()
        self.started = time.time()

    def emit(self, event: dict) -> None:
        event = {"t": round(time.time() - self.started, 1), **event}
        with self.cv:
            self.events.append(event)
            self.cv.notify_all()


RUNS: dict[str, RunHandle] = {}
_workflow = None
_workflow_lock = threading.Lock()


def workflow():
    global _workflow
    with _workflow_lock:
        if _workflow is None:
            _workflow = build_graph()
        return _workflow


def _execute(h: RunHandle) -> None:
    try:
        cfg = {"configurable": {"thread_id": h.run_id}}
        for mode, chunk in workflow().stream({"run_id": h.run_id, "city_input": h.city_input, "stage": "planning"},
                                             cfg, stream_mode=["custom", "updates"]):
            if mode == "custom":
                if chunk.get("city_id"):
                    h.city_id = chunk["city_id"]
                h.emit({"type": "progress", **{k: v for k, v in chunk.items() if k != "plan"}})
            else:
                node = next(iter(chunk))
                if node not in ("extract_claims", "fact_check"):
                    h.emit({"type": "node_done", "node": node})
        final = workflow().get_state(cfg).values
        h.city_id = final.get("city_id") or h.city_id
        h.status = "complete"
        h.emit({"type": "complete", "city_id": h.city_id, "stats": final.get("stats", {}),
                "graph_status": final.get("graph_status"), "errors": final.get("errors", [])[:5]})
    except Exception as e:  # noqa: BLE001
        log.exception("run %s failed", h.run_id)
        h.status = "failed"
        h.emit({"type": "failed", "message": f"{type(e).__name__}: {e}"[:400]})


class ResearchRequest(BaseModel):
    city: str = Field(min_length=2, max_length=120, examples=["Nairobi, Kenya"])


@app.post("/api/research")
def start_research(req: ResearchRequest):
    running = [r for r in RUNS.values() if r.status == "running"]
    if len(running) >= 2:
        raise HTTPException(429, "Two research runs are already in progress; please wait for one to finish.")
    h = RunHandle(uuid.uuid4().hex[:10], req.city.strip())
    RUNS[h.run_id] = h
    threading.Thread(target=_execute, args=(h,), daemon=True, name=f"run-{h.run_id}").start()
    return {"run_id": h.run_id, "city": h.city_input}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str):
    h = RUNS.get(run_id)
    if not h:
        raise HTTPException(404, "unknown run")

    def gen():
        i = 0
        while True:
            with h.cv:
                while i >= len(h.events) and h.status == "running":
                    if not h.cv.wait(timeout=15):
                        break
                batch = h.events[i:]
                i += len(batch)
                done = h.status != "running" and i >= len(h.events)
            for e in batch:
                yield f"data: {json.dumps(e, default=str)}\n\n"
            if not batch:
                yield ": keep-alive\n\n"
            if done:
                return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}")
def run_status(run_id: str):
    h = RUNS.get(run_id)
    if h:
        return {"run_id": run_id, "status": h.status, "city_id": h.city_id, "events": len(h.events),
                "last": h.events[-1] if h.events else None}
    with rel.SessionLocal() as s:
        r = s.get(rel.Run, run_id)
        if not r:
            raise HTTPException(404, "unknown run")
        return {"run_id": run_id, "status": r.status, "city_id": r.city_id, "graph_status": r.graph_status, "stats": r.stats}


# --------------------------------------------------------------------------- cities
def _city_or_404(s, city_id: str) -> rel.City:
    c = s.get(rel.City, city_id)
    if not c:
        raise HTTPException(404, f"city '{city_id}' has not been researched")
    return c


@app.get("/api/cities")
def cities():
    with rel.SessionLocal() as s:
        out = []
        for c in s.execute(select(rel.City).order_by(rel.City.last_researched_at.desc())).scalars():
            n = s.execute(select(func.count()).select_from(rel.ClaimRow).where(rel.ClaimRow.city_id == c.id, rel.ClaimRow.status == "verified")).scalar()
            run = s.execute(select(rel.Run).where(rel.Run.city_id == c.id).order_by(rel.Run.started_at.desc())).scalars().first()
            out.append({"city_id": c.id, "name": c.name, "country": c.country, "admin_region": c.admin_region,
                        "last_researched_at": c.last_researched_at, "verified_claims": n,
                        "graph_status": run.graph_status if run else None})
        return out


@app.get("/api/cities/{city_id}")
def city_overview(city_id: str):
    with rel.SessionLocal() as s:
        c = _city_or_404(s, city_id)
        run = s.execute(select(rel.Run).where(rel.Run.city_id == city_id).order_by(rel.Run.started_at.desc())).scalars().first()
        claims = s.execute(select(rel.ClaimRow.status, rel.ClaimRow.category, rel.ClaimRow.geo_level, rel.ClaimRow.verdict,
                                  rel.ClaimRow.in_conflict).where(rel.ClaimRow.city_id == city_id)).all()
        sources = s.execute(select(rel.Source.crawl_allowed, rel.Source.fetch_status, rel.Source.source_tier, rel.Source.crawl_reason)
                            .where(rel.Source.city_id == city_id)).all()
        gaps = s.execute(select(rel.GapRow).where(rel.GapRow.run_id == (run.id if run else ""))).scalars().all()
        conflicts = s.execute(select(rel.ConflictRow).where(rel.ConflictRow.run_id == (run.id if run else ""))).scalars().all()
        ver = [x for x in claims if x.status == "verified"]
        plan = (run.plan if run else {}) or {}
        return {
            "city": {"city_id": c.id, "name": c.name, "country": c.country, "admin_region": c.admin_region,
                     "aliases": c.aliases, "population_hint": c.population_hint, "last_researched_at": c.last_researched_at},
            "run": {"run_id": run.id, "graph_status": run.graph_status, "finished_at": run.finished_at, "stats": run.stats,
                    "assumptions": plan.get("assumptions", [])} if run else None,
            "categories": [{"key": k["key"], "name": k["name"], "why": k.get("why"),
                            "verified": sum(1 for x in ver if x.category == k["key"]),
                            "city_level": sum(1 for x in ver if x.category == k["key"] and x.geo_level in ("city", "metro", "district"))}
                           for k in plan.get("categories", [])],
            "counts": {"verified": len(ver), "partially_supported": sum(1 for x in ver if x.verdict == "PARTIALLY_SUPPORTED"),
                       "not_accepted": dict(Counter(x.status for x in claims if x.status != "verified")),
                       "by_geo": dict(Counter(x.geo_level for x in ver)), "in_conflict": sum(1 for x in ver if x.in_conflict),
                       "sources": len(sources), "sources_read": sum(1 for x in sources if x.fetch_status == "ok"),
                       "sources_denied": sum(1 for x in sources if not x.crawl_allowed),
                       "denied_reasons": dict(Counter(x.crawl_reason for x in sources if not x.crawl_allowed)),
                       "sources_by_tier": dict(Counter(x.source_tier for x in sources)),
                       "gaps": len(gaps), "conflicts": len(conflicts)},
            "gaps": [{"category": g.category, "severity": g.severity, "description": g.description, "suggestion": g.suggestion} for g in gaps],
            "conflicts": [{"claim_a": k.claim_a, "claim_b": k.claim_b, "description": k.description} for k in conflicts],
            "stores": {"relational": rel.backend_name(), "vector": vector.backend_name(),
                       "graph": "graphiti+neo4j" if graph_store.is_configured() else "not configured"},
        }


@app.delete("/api/cities/{city_id}")
def delete_city(city_id: str):
    """Remove a city from ALL three stores: audit trail, evidence index and its knowledge-graph namespace."""
    with rel.SessionLocal() as s:
        _city_or_404(s, city_id)
    if any(r.status == "running" and r.city_id == city_id for r in RUNS.values()):
        raise HTTPException(409, "A research run for this city is still in progress. Wait for it to finish, then delete.")
    removed: dict = {}
    errors: list[str] = []
    # derived stores first, the system of record last: if a derived delete fails the city is still listed and can be retried
    try:
        removed["vector_points"] = vector.delete_city(city_id)
    except Exception as e:  # noqa: BLE001
        errors.append(f"vector: {type(e).__name__}: {e}"[:200])
    if graph_store.is_configured():
        try:
            removed["graph_nodes"] = asyncio.run(graph_store.delete_city(city_id))
        except Exception as e:  # noqa: BLE001
            errors.append(f"graph: {type(e).__name__}: {e}"[:200])
    if errors:
        raise HTTPException(502, {"message": "Could not clear every store; nothing was removed from the record.", "errors": errors})
    removed["relational"] = rel.delete_city(city_id)
    log.info("deleted city %s: %s", city_id, removed)
    return {"deleted": city_id, "removed": removed}


@app.get("/api/cities/{city_id}/claims")
def city_claims(city_id: str, category: str | None = None, status: str = "verified", geo_level: str | None = None,
                limit: int = Query(300, le=1000)):
    with rel.SessionLocal() as s:
        _city_or_404(s, city_id)
        q = select(rel.ClaimRow, rel.Source).join(rel.Source, rel.ClaimRow.source_id == rel.Source.id, isouter=True) \
            .where(rel.ClaimRow.city_id == city_id)
        if status != "all":
            q = q.where(rel.ClaimRow.status == status) if status != "not_accepted" else q.where(rel.ClaimRow.status != "verified")
        if category:
            q = q.where(rel.ClaimRow.category == category)
        if geo_level:
            q = q.where(rel.ClaimRow.geo_level == geo_level)
        out = []
        for c, src in s.execute(q.limit(limit)).all():
            out.append({"id": c.id, "category": c.category, "claim_type": c.claim_type, "statement": c.statement,
                        "original_statement": c.original_statement,
                        "quote": c.quote, "evidence_window": c.evidence_window, "quote_verified": c.quote_verified,
                        "geo_level": c.geo_level, "extractor_geo_level": c.extractor_geo_level, "geo_mismatch": c.geo_mismatch,
                        "year": c.year, "entities": c.entities, "status": c.status, "verdict": c.verdict,
                        "verdict_rationale": c.verdict_rationale, "checker_confidence": c.checker_confidence,
                        "extractor_model": c.extractor_model, "checker_model": c.checker_model, "in_conflict": c.in_conflict,
                        "in_graph": bool(c.graph_episode_uuid),
                        "source": {"id": src.id, "url": src.final_url or src.url, "title": src.title, "publisher": src.publisher,
                                   "tier": src.source_tier, "published_date": src.published_date, "retrieved_at": src.fetched_at,
                                   "robots_status": src.robots_status} if src else {"url": c.source_url}})
        order = {"city": 0, "metro": 1, "district": 2, "state": 3, "national": 4, "global": 5, "unknown": 6}
        out.sort(key=lambda x: (order.get(x["geo_level"], 9), x["verdict"] != "SUPPORTED"))
        return out


@app.get("/api/cities/{city_id}/sources")
def city_sources(city_id: str):
    with rel.SessionLocal() as s:
        _city_or_404(s, city_id)
        n_claims = dict(s.execute(select(rel.ClaimRow.source_id, func.count()).where(
            rel.ClaimRow.city_id == city_id, rel.ClaimRow.status == "verified").group_by(rel.ClaimRow.source_id)).all())
        rows = s.execute(select(rel.Source).where(rel.Source.city_id == city_id)).scalars().all()
        return [{"id": x.id, "url": x.final_url or x.url, "title": x.title, "publisher": x.publisher, "tier": x.source_tier,
                 "provider": x.provider, "published_date": x.published_date, "snippet": x.snippet, "queries": x.queries,
                 "crawl_allowed": x.crawl_allowed, "crawl_reason": x.crawl_reason, "robots_status": x.robots_status,
                 "fetch_status": x.fetch_status, "extraction_quality": x.extraction_quality, "word_count": x.word_count,
                 "retrieved_at": x.fetched_at, "verified_claims": n_claims.get(x.id, 0)}
                for x in sorted(rows, key=lambda x: (-n_claims.get(x.id, 0), not x.crawl_allowed))]


@app.get("/api/cities/{city_id}/graph")
def city_graph(city_id: str, limit: int = Query(250, le=600)):
    if not graph_store.is_configured():
        raise HTTPException(503, "graph store not configured")

    async def q():
        _, driver = graph_store._graphiti()
        try:
            recs, _, _ = await driver.execute_query(
                "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) WHERE r.group_id = $g "
                "RETURN a.uuid AS s, a.name AS sn, [x IN labels(a) WHERE x <> 'Entity'][0] AS st, "
                "b.uuid AS t, b.name AS tn, [x IN labels(b) WHERE x <> 'Entity'][0] AS tt, "
                "r.name AS rel, r.fact AS fact, r.invalid_at AS invalid_at LIMIT $limit", g=city_id, limit=limit)
            return recs
        finally:
            await driver.close()

    recs = asyncio.run(q())
    nodes: dict[str, dict] = {}
    edges = []
    for r in recs:
        nodes.setdefault(r["s"], {"id": r["s"], "name": r["sn"], "type": r["st"] or "Entity"})
        nodes.setdefault(r["t"], {"id": r["t"], "name": r["tn"], "type": r["tt"] or "Entity"})
        edges.append({"source": r["s"], "target": r["t"], "relation": r["rel"], "fact": r["fact"], "invalidated": bool(r["invalid_at"])})
    return {"nodes": list(nodes.values()), "edges": edges}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    conversation_id: str | None = Field(default=None, description="omit to start a new conversation")


@app.post("/api/cities/{city_id}/ask")
def ask(city_id: str, req: AskRequest):
    """One conversational turn. History is loaded from the relational store, the follow-up is resolved into a
    standalone question, evidence is retrieved fresh from all three stores, and both messages are saved."""
    status = qa.city_status(city_id)
    if not status:
        raise HTTPException(404, f"city '{city_id}' has not been researched")
    if graph_store.is_configured() and status["graph_status"] in ("pending", "building"):
        raise HTTPException(409, "The knowledge graph for this city is still being built. Findings and the report are "
                                 "available now; questions open as soon as the graph is ready.")
    history: list[dict] = []
    cid = req.conversation_id
    with rel.SessionLocal() as s:
        conv = s.get(rel.Conversation, cid) if cid else None
        if conv is None or conv.city_id != city_id:
            cid = uuid.uuid4().hex[:16]
            s.add(rel.Conversation(id=cid, city_id=city_id, title=req.question[:120]))
            s.commit()
        else:
            history = [{"role": m.role, "content": m.content} for m in s.execute(
                select(rel.Message).where(rel.Message.conversation_id == cid).order_by(rel.Message.id)).scalars()]

    result = qa.answer(city_id, req.question, history=history)
    result["conversation_id"] = cid
    result["turn"] = len(history) // 2 + 1

    with rel.SessionLocal() as s:
        s.add(rel.Message(conversation_id=cid, role="user", content=req.question, payload={}))
        s.add(rel.Message(conversation_id=cid, role="assistant", content=result.get("answer", ""),
                          payload=json.loads(json.dumps(result, default=str))))
        conv = s.get(rel.Conversation, cid)
        if conv:
            conv.updated_at = rel.utcnow()
        s.commit()
    return result


@app.get("/api/cities/{city_id}/conversations")
def conversations(city_id: str, limit: int = Query(20, le=100)):
    with rel.SessionLocal() as s:
        _city_or_404(s, city_id)
        rows = s.execute(select(rel.Conversation).where(rel.Conversation.city_id == city_id)
                         .order_by(rel.Conversation.updated_at.desc()).limit(limit)).scalars().all()
        counts = dict(s.execute(select(rel.Message.conversation_id, func.count()).where(
            rel.Message.conversation_id.in_([c.id for c in rows])).group_by(rel.Message.conversation_id)).all()) if rows else {}
        return [{"conversation_id": c.id, "title": c.title, "updated_at": c.updated_at, "turns": counts.get(c.id, 0) // 2}
                for c in rows]


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str):
    with rel.SessionLocal() as s:
        conv = s.get(rel.Conversation, conversation_id)
        if not conv:
            raise HTTPException(404, "unknown conversation")
        msgs = s.execute(select(rel.Message).where(rel.Message.conversation_id == conversation_id)
                         .order_by(rel.Message.id)).scalars().all()
        return {"conversation_id": conv.id, "city_id": conv.city_id, "title": conv.title, "created_at": conv.created_at,
                "messages": [{"role": m.role, "content": m.content, "created_at": m.created_at,
                              **({"answer": m.payload} if m.role == "assistant" else {})} for m in msgs]}


@app.get("/api/cities/{city_id}/report")
def city_report(city_id: str, format: str = "md"):
    try:
        r = report.build_report(city_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if format == "html":
        html = report.to_html(r["markdown"], r["filename"])
        return Response(html, media_type="text/html",
                        headers={"Content-Disposition": f'attachment; filename="{r["filename"].replace(".md", ".html")}"'})
    return PlainTextResponse(r["markdown"], media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{r["filename"]}"'})


@app.get("/api/workflow")
def workflow_diagram():
    return {"mermaid": mermaid()}


@app.get("/api/health")
def health():
    return {"ok": True, "stores": {"relational": rel.backend_name(), "vector": vector.backend_name(),
                                   "graph": graph_store.is_configured()}, "llm_usage": usage_snapshot()}


# --------------------------------------------------------------------------- built frontend (container deployments)
_DIST = next((d for d in (BACKEND_DIR / "frontend_dist", BACKEND_DIR.parent / "frontend_dist") if d.exists()), None)
if _DIST is not None:
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = _DIST / path
        return FileResponse(f if path and f.is_file() else _DIST / "index.html")
