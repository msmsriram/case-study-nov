"""Knowledge graph store: Graphiti on Neo4j (Aura).

What lives here (and why): *relationships over time* between organisations, programmes, policies,
places, people and health conditions - the institutional memory of a city. Only VERIFIED,
relationship-bearing claims are ingested (statistics stay in the relational / vector stores: a
prevalence number makes a poor graph entity). One namespace (group_id) per city.

Ingestion unit: an *episode* = a small bundle of verified claims from ONE source. The mapping
episode -> claim ids is recorded in the relational store, so every graph fact can be traced
fact -> episode -> claims -> quote -> source URL.

LLM: Graphiti needs structured output for extraction / de-duplication. Our free providers do not
enforce schemas for it, so the ontology is types-only (no attribute fields), which ingested
reliably in testing. All API keys of the pool are rotated per request inside one client.
"""
from __future__ import annotations

import itertools
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

from ..config import settings
from ..embeddings import embed, embed_one
from ..keys import numbered_env

os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")
log = logging.getLogger(__name__)
logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

GRAPH_CLAIM_TYPES = {"programme", "policy", "stakeholder", "organization", "infrastructure", "risk_or_gap"}


# --------------------------------------------------------------------------- ontology (types only)
class Organization(BaseModel):
    """A government body, ministry, department, NGO, company, university, hospital or international agency."""


class Programme(BaseModel):
    """A named health programme, project, campaign, screening drive or initiative."""


class Policy(BaseModel):
    """A named policy, strategy, plan, act, regulation or guideline."""


class Place(BaseModel):
    """A city, county, state, country, neighbourhood or informal settlement."""


class HealthCondition(BaseModel):
    """A disease, condition or risk factor such as hypertension, type 2 diabetes, air pollution or tobacco use."""


class Person(BaseModel):
    """A named individual with a role, such as a minister, governor, director or researcher."""


class Facility(BaseModel):
    """A named hospital, clinic, laboratory or other health facility."""


ENTITY_TYPES = {"Organization": Organization, "Programme": Programme, "Policy": Policy, "Place": Place,
                "HealthCondition": HealthCondition, "Person": Person, "Facility": Facility}

EXTRACTION_INSTRUCTIONS = (
    "These are fact-checked claims about a city's cardiovascular / non-communicable disease landscape. "
    "Extract only named organisations, programmes, policies, places, people, facilities and health conditions, "
    "and the relationships between them (who runs, funds, implements, partners with, governs, targets, is located in). "
    "Do NOT create entities for numbers, percentages, years, survey sample sizes or generic words like 'adults'. "
    "Do not add any fact that is not stated in the text.")


def is_configured() -> bool:
    return bool(settings.neo4j_uri and settings.neo4j_username and settings.neo4j_password)


# --------------------------------------------------------------------------- clients
def _llm_client():
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
    from openai import AsyncOpenAI

    keys = numbered_env("OLLAMA_API_KEY")
    if settings.graph_llm_provider == "ollama" and keys:
        counter = itertools.count()

        async def rotate(request: httpx.Request) -> None:       # spread Graphiti's many calls over the key pool
            request.headers["Authorization"] = f"Bearer {keys[next(counter) % len(keys)]}"

        http = httpx.AsyncClient(event_hooks={"request": [rotate]}, timeout=httpx.Timeout(120, connect=15))
        client = AsyncOpenAI(api_key=keys[0], base_url=f"{settings.ollama_base_url}/v1", http_client=http, max_retries=3)
        cfg = LLMConfig(api_key=keys[0], base_url=f"{settings.ollama_base_url}/v1", model=settings.ollama_model_checker,
                        small_model=settings.ollama_model_extractor, temperature=0, max_tokens=6000)
        os.environ.setdefault("SEMAPHORE_LIMIT", str(max(2, len(keys))))
        return OpenAIGenericClient(cfg, client=client, max_tokens=6000, structured_output_mode="json_object")

    cfg = LLMConfig(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1",
                    model=settings.groq_model_checker, small_model=settings.groq_model_extractor,
                    temperature=0, max_tokens=4000)
    os.environ.setdefault("SEMAPHORE_LIMIT", "1")
    return OpenAIGenericClient(cfg, max_tokens=4000, structured_output_mode="json_schema")


def _graphiti():
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.client import CrossEncoderClient
    from graphiti_core.driver.neo4j_driver import Neo4jDriver
    from graphiti_core.embedder.client import EmbedderClient

    class LocalEmbedder(EmbedderClient):
        async def create(self, input_data) -> list[float]:
            return embed_one(input_data if isinstance(input_data, str) else input_data[0])

        async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
            return embed(input_data_list)

    class LocalReranker(CrossEncoderClient):
        """Cosine similarity of local embeddings - a cheap stand-in for a hosted cross-encoder."""

        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            if not passages:
                return []
            vecs = embed([query] + passages)
            q, nq = vecs[0], sum(x * x for x in vecs[0]) ** 0.5
            out = [(p, sum(a * b for a, b in zip(q, v)) / (nq * (sum(x * x for x in v) ** 0.5) + 1e-9))
                   for p, v in zip(passages, vecs[1:])]
            return sorted(out, key=lambda x: -x[1])

    driver = Neo4jDriver(settings.neo4j_uri, settings.neo4j_username, settings.neo4j_password,
                         database=settings.neo4j_database)
    return Graphiti(graph_driver=driver, llm_client=_llm_client(), embedder=LocalEmbedder(),
                    cross_encoder=LocalReranker()), driver


# --------------------------------------------------------------------------- bundling
def build_bundles(claims: list, already_ingested: set[str]) -> list[dict]:
    """Group new, verified, relationship-bearing claims by source into episodes of N claims."""
    by_source: dict[str, list] = defaultdict(list)
    for c in claims:
        if c.id in already_ingested or c.claim_type not in GRAPH_CLAIM_TYPES:
            continue
        by_source[c.source_url].append(c)
    bundles: list[dict] = []
    n = settings.graph_claims_per_episode
    for url, cs in by_source.items():
        for i in range(0, len(cs), n):
            part = cs[i:i + n]
            bundles.append({"source_url": url, "source_title": part[0].source_title, "source_tier": part[0].source_tier,
                            "claims": part, "year": max((c.year for c in part if c.year), default=None)})
    # official / primary sources first, in case the episode cap binds
    tier_rank = {"government": 0, "intergovernmental": 0, "academic": 1, "ngo": 2}
    bundles.sort(key=lambda b: (tier_rank.get(b["source_tier"], 3), -len(b["claims"])))
    return bundles[: settings.graph_max_episodes_per_run]


# --------------------------------------------------------------------------- write path
async def ingest(city_id: str, city_label: str, bundles: list[dict], on_progress=None) -> dict:
    from graphiti_core.nodes import EpisodeType

    g, _ = _graphiti()
    stats = {"episodes": 0, "failed": 0, "nodes": 0, "edges": 0, "seconds": 0.0, "episode_claims": {}}
    t0 = time.time()
    try:
        await g.build_indices_and_constraints()
        for i, b in enumerate(bundles, 1):
            body = (f"City: {city_label}\nSource: {b['source_title']} ({b['source_tier']})\n"
                    "Verified claims:\n" + "\n".join(f"- {c.statement}" for c in b["claims"]))
            ref = datetime(b["year"], 1, 1, tzinfo=timezone.utc) if b["year"] and 1900 < b["year"] < 2100 else datetime.now(timezone.utc)
            t = time.time()
            try:
                res = await g.add_episode(
                    name=f"{city_id}:{b['claims'][0].id}", episode_body=body, source=EpisodeType.text,
                    source_description=f"{b['source_tier']} | {b['source_url']}", reference_time=ref,
                    group_id=city_id, entity_types=ENTITY_TYPES, custom_extraction_instructions=EXTRACTION_INSTRUCTIONS)
                stats["episodes"] += 1
                stats["nodes"] += len(res.nodes)
                stats["edges"] += len(res.edges)
                stats["episode_claims"][res.episode.uuid] = [c.id for c in b["claims"]]
                msg = f"episode {i}/{len(bundles)}: {len(res.nodes)} entities, {len(res.edges)} relationships ({time.time()-t:.0f}s)"
            except Exception as e:  # noqa: BLE001
                stats["failed"] += 1
                msg = f"episode {i}/{len(bundles)} failed: {type(e).__name__}: {str(e)[:160]}"
                log.warning(msg)
            if on_progress:
                on_progress(msg)
    finally:
        await g.close()
    stats["seconds"] = round(time.time() - t0, 1)
    return stats


# --------------------------------------------------------------------------- read path (query time)
async def search(city_id: str, query: str, k: int = 8) -> list[dict]:
    """Hybrid (BM25 + vector + graph) search over a city's facts. No LLM call."""
    g, driver = _graphiti()
    try:
        edges = await g.search(query, group_ids=[city_id], num_results=k)
        if not edges:
            return []
        node_ids = list({e.source_node_uuid for e in edges} | {e.target_node_uuid for e in edges})
        recs, _, _ = await driver.execute_query(
            "MATCH (n:Entity) WHERE n.uuid IN $ids RETURN n.uuid AS uuid, n.name AS name, labels(n) AS labels", ids=node_ids)
        nodes = {r["uuid"]: {"name": r["name"], "type": next((x for x in r["labels"] if x != "Entity"), "Entity")} for r in recs}
        ep_ids = list({ep for e in edges for ep in (e.episodes or [])})
        recs, _, _ = await driver.execute_query(
            "MATCH (ep:Episodic) WHERE ep.uuid IN $ids RETURN ep.uuid AS uuid, ep.source_description AS src", ids=ep_ids)
        ep_src = {r["uuid"]: r["src"] for r in recs}
        out = []
        for e in edges:
            s, t = nodes.get(e.source_node_uuid, {}), nodes.get(e.target_node_uuid, {})
            out.append({"fact": e.fact, "relation": e.name, "source_entity": s.get("name"), "source_type": s.get("type"),
                        "target_entity": t.get("name"), "target_type": t.get("type"),
                        "valid_at": e.valid_at.isoformat() if e.valid_at else None,
                        "invalid_at": e.invalid_at.isoformat() if e.invalid_at else None,
                        "episode_uuids": list(e.episodes or []),
                        "source_urls": sorted({ep_src[x].split(" | ")[-1] for x in (e.episodes or []) if x in ep_src})})
        return out
    finally:
        await g.close()


async def overview(city_id: str) -> dict:
    _, driver = _graphiti()
    try:
        recs, _, _ = await driver.execute_query(
            "MATCH (n:Entity) WHERE n.group_id = $g RETURN [x IN labels(n) WHERE x <> 'Entity'][0] AS type, count(*) AS c", g=city_id)
        rels, _, _ = await driver.execute_query(
            "MATCH (:Entity)-[r:RELATES_TO]->(:Entity) WHERE r.group_id = $g RETURN count(r) AS c", g=city_id)
        return {"entities": {r["type"] or "Entity": r["c"] for r in recs}, "relationships": rels[0]["c"] if rels else 0}
    finally:
        await driver.close()
