"""Vector store (Qdrant): semantic recall over evidence.

What lives here (and why): text that has to be found by *meaning* -
  kind="claim"   : every VERIFIED claim (statement + quote). Rejected claims are never indexed.
  kind="passage" : the most relevant passages of each usable source document, so a question can
                   reach context the extractor did not turn into a claim. Passages are verbatim
                   source text and are labelled as unverified context when used.
Payloads carry ids back to the relational store (claim_id / source_id); no facts are stored only here.

Qdrant Cloud when QDRANT_URL is set, embedded on-disk Qdrant otherwise - same code path.
"""
from __future__ import annotations

import atexit
import logging
import threading
import uuid

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from ..config import settings
from ..embeddings import EMBEDDING_DIM, embed, embed_one
from ..graph.passages import select_passages
from ..graph.state import effective_statement
from ..research.models import ResearchItem
from .relational import city_id_for, source_id_for

log = logging.getLogger(__name__)
_client: QdrantClient | None = None
_lock = threading.Lock()
_NS = uuid.UUID("6f1d2c3a-0b7e-4c55-9a53-1c0de0c4d1e5")


def client() -> QdrantClient:
    global _client
    with _lock:
        if _client is None:
            if settings.qdrant_url:
                _client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=60)
            else:
                _client = QdrantClient(path=str(settings.data_dir / "qdrant"))
            _ensure(_client)
            atexit.register(_client.close)      # embedded mode holds a file lock; release it cleanly
        return _client


def backend_name() -> str:
    return "qdrant-cloud" if settings.qdrant_url else "qdrant-embedded"


def _ensure(c: QdrantClient) -> None:
    name = settings.qdrant_collection
    if not c.collection_exists(name):
        c.create_collection(name, vectors_config=qm.VectorParams(size=EMBEDDING_DIM, distance=qm.Distance.COSINE))
    for field in ("city_id", "kind", "category", "geo_level"):
        try:  # needed for filtered search on Qdrant Cloud; a no-op warning in embedded mode
            c.create_payload_index(name, field_name=field, field_schema=qm.PayloadSchemaType.KEYWORD)
        except Exception:  # noqa: BLE001
            pass


def _pid(key: str) -> str:
    return str(uuid.uuid5(_NS, key))


def index_run(state: dict, max_passages_per_doc: int = 10) -> dict:
    plan = state["plan"]
    city_id = city_id_for(plan.city, plan.country_code, plan.country)
    verdicts = {v.claim_id: v for v in state.get("verifications", [])}
    verified = set(state.get("verified_claim_ids", []))

    texts: list[str] = []
    points: list[tuple[str, dict]] = []

    final_to_key = {d.final_url: k for k, d in state.get("documents", {}).items()}
    for c in state.get("claims", []):
        if c.id not in verified:
            continue
        v = verdicts.get(c.id)
        geo = v.corrected_geo_level if v and v.geo_mismatch and v.corrected_geo_level != "unknown" else c.geo_level
        key = final_to_key.get(c.source_url)
        shown, _ = effective_statement(c, v)      # never index the unsupported half of a partially supported claim
        texts.append(f"{shown}\n{c.quote}")
        points.append((_pid(f"claim|{c.id}"), {
            "kind": "claim", "city_id": city_id, "claim_id": c.id, "category": c.category, "claim_type": c.claim_type,
            "geo_level": geo, "verdict": v.verdict if v else None, "year": c.year, "text": shown,
            "quote": c.quote, "source_url": c.source_url, "source_title": c.source_title, "source_tier": c.source_tier,
            "source_id": source_id_for(city_id, key) if key else None}))

    names = [plan.city] + plan.aliases + ([plan.admin_region] if plan.admin_region else [])
    cats = [cat.key for cat in plan.categories]
    n_pass = 0
    for key, doc in state.get("documents", {}).items():
        res = state["search_results"][key]
        if not ResearchItem(result=res, document=doc).usable:
            continue
        joined, _ = select_passages(doc.text, city_names=names, categories=cats, budget_tokens=2400)
        for i, para in enumerate([p for p in joined.split("\n\n") if p.strip()][:max_passages_per_doc]):
            texts.append(para)
            points.append((_pid(f"passage|{city_id}|{key}|{i}"), {
                "kind": "passage", "city_id": city_id, "source_id": source_id_for(city_id, key), "chunk": i,
                "text": para, "source_url": doc.final_url, "source_title": doc.title or res.title,
                "source_tier": res.source_tier, "published_date": doc.published_date}))
            n_pass += 1

    if not points:
        return {"indexed_claims": 0, "indexed_passages": 0, "backend": backend_name()}
    vectors = embed(texts)
    c = client()
    batch = 64
    for i in range(0, len(points), batch):
        c.upsert(settings.qdrant_collection, points=[
            qm.PointStruct(id=pid, vector=vec, payload=payload)
            for (pid, payload), vec in zip(points[i:i + batch], vectors[i:i + batch])])
    return {"indexed_claims": len(points) - n_pass, "indexed_passages": n_pass, "backend": backend_name()}


def search(city_id: str, query: str, k: int = 8, kind: str | None = None) -> list[dict]:
    must = [qm.FieldCondition(key="city_id", match=qm.MatchValue(value=city_id))]
    if kind:
        must.append(qm.FieldCondition(key="kind", match=qm.MatchValue(value=kind)))
    res = client().query_points(settings.qdrant_collection, query=embed_one(query), limit=k,
                                query_filter=qm.Filter(must=must), with_payload=True)
    return [{"score": round(p.score, 4), **(p.payload or {})} for p in res.points]


def delete_city(city_id: str) -> int:
    c = client()
    flt = qm.Filter(must=[qm.FieldCondition(key="city_id", match=qm.MatchValue(value=city_id))])
    n = c.count(settings.qdrant_collection, count_filter=flt, exact=True).count
    if n:
        c.delete(settings.qdrant_collection, points_selector=qm.FilterSelector(filter=flt))
    return n
