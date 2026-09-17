"""End-to-end Graphiti check against Neo4j Aura, using verified claims from a previous run.

    python scripts/test_graphiti.py [ollama|groq] [n_claims]

Proves: connection, index build, episode ingestion with a custom ontology, entity/edge
extraction by our LLM, hybrid search at query time, and provenance back to the source URL.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("SEMAPHORE_LIMIT", "2")          # free-tier LLMs: keep Graphiti's concurrency low
os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import logging  # noqa: E402

logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logging.getLogger("neo4j").setLevel(logging.ERROR)

from graphiti_core import Graphiti  # noqa: E402
from graphiti_core.cross_encoder.client import CrossEncoderClient  # noqa: E402
from graphiti_core.driver.neo4j_driver import Neo4jDriver  # noqa: E402
from graphiti_core.embedder.client import EmbedderClient  # noqa: E402
from graphiti_core.llm_client.config import LLMConfig  # noqa: E402
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient  # noqa: E402
from graphiti_core.nodes import EpisodeType  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.keys import numbered_env  # noqa: E402

provider = sys.argv[1] if len(sys.argv) > 1 else "ollama"
n_claims = int(sys.argv[2]) if len(sys.argv) > 2 else 6


# ------------------------------------------------------------------ local embedder + reranker (no API key)
class FastEmbedEmbedder(EmbedderClient):
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding
        self._m = TextEmbedding(model_name=model)

    async def create(self, input_data) -> list[float]:
        text = input_data if isinstance(input_data, str) else input_data[0]
        return [float(x) for x in next(iter(self._m.embed([text])))]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self._m.embed(input_data_list)]


class EmbeddingReranker(CrossEncoderClient):
    """Cheap stand-in for a cross-encoder: cosine similarity of local embeddings."""

    def __init__(self, embedder: FastEmbedEmbedder):
        self._e = embedder

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        vecs = await self._e.create_batch([query] + passages)
        q = vecs[0]
        nq = sum(x * x for x in q) ** 0.5
        scored = []
        for p, v in zip(passages, vecs[1:]):
            nv = sum(x * x for x in v) ** 0.5
            scored.append((p, sum(a * b for a, b in zip(q, v)) / (nq * nv + 1e-9)))
        return sorted(scored, key=lambda x: -x[1])


# ------------------------------------------------------------------ ontology
class Organization(BaseModel):
    """A government body, ministry, department, NGO, company, university, hospital group or international agency."""


class Programme(BaseModel):
    """A named health programme, project, campaign, screening drive or initiative."""


class Policy(BaseModel):
    """A named policy, strategy, plan, act, regulation or guideline."""


class Place(BaseModel):
    """A city, county, state, country, neighbourhood or informal settlement."""


class HealthCondition(BaseModel):
    """A disease, condition or risk factor such as hypertension, type 2 diabetes, air pollution, tobacco use."""


class Person(BaseModel):
    """A named individual with a role, such as a minister, governor, director or researcher."""


ENTITY_TYPES = {"Organization": Organization, "Programme": Programme, "Policy": Policy,
                "Place": Place, "HealthCondition": HealthCondition, "Person": Person}


def llm_client():
    if provider == "groq":
        cfg = LLMConfig(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1",
                        model="openai/gpt-oss-120b", small_model="openai/gpt-oss-20b", temperature=0, max_tokens=4000)
        return OpenAIGenericClient(cfg, max_tokens=4000, structured_output_mode="json_schema")
    cfg = LLMConfig(api_key=numbered_env("OLLAMA_API_KEY")[0], base_url="https://ollama.com/v1",
                    model="gpt-oss:120b", small_model="gpt-oss:20b", temperature=0, max_tokens=6000)
    return OpenAIGenericClient(cfg, max_tokens=6000, structured_output_mode="json_object")


async def main():
    state = json.loads((Path(__file__).resolve().parent.parent / "samples" / "graph_nairobi.json").read_text(encoding="utf-8"))
    verified = set(state["verified_claim_ids"])
    claims = [c for c in state["claims"] if c["id"] in verified and c["category"] in ("programmes", "stakeholders", "policies")]
    claims = claims[:n_claims]
    group = "nairobi-test"

    emb = FastEmbedEmbedder()
    driver = Neo4jDriver(os.environ["NEO4J_URI"], os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"],
                         database=os.environ.get("NEO4J_DATABASE", "neo4j"))
    g = Graphiti(graph_driver=driver, llm_client=llm_client(), embedder=emb, cross_encoder=EmbeddingReranker(emb))
    try:
        t = time.time()
        await g.build_indices_and_constraints()
        print(f"indices ready in {time.time()-t:.1f}s | LLM provider: {provider}")

        for i, c in enumerate(claims, 1):
            t = time.time()
            ref = datetime(c["year"], 1, 1, tzinfo=timezone.utc) if c.get("year") else datetime.now(timezone.utc)
            try:
                res = await g.add_episode(
                    name=f"claim-{c['id']}", episode_body=c["statement"],
                    source=EpisodeType.text, source_description=f"verified claim | {c['source_tier']} | {c['source_url']}",
                    reference_time=ref, group_id=group, entity_types=ENTITY_TYPES)
                print(f"  [{i}/{len(claims)}] {time.time()-t:5.1f}s nodes={len(res.nodes)} edges={len(res.edges)} | {c['statement'][:90]}")
                for e in res.edges[:3]:
                    print(f"        edge: {e.name}: {e.fact[:110]}")
            except Exception as ex:  # noqa: BLE001
                print(f"  [{i}/{len(claims)}] FAILED after {time.time()-t:.1f}s: {type(ex).__name__}: {str(ex)[:300]}")

        for q in ("Which organisations run hypertension or diabetes programmes in Nairobi?",
                  "What policies or strategies address non-communicable diseases?"):
            t = time.time()
            edges = await g.search(q, group_ids=[group], num_results=5)
            print(f"\nQ: {q}  ({time.time()-t:.1f}s, {len(edges)} facts)")
            for e in edges:
                eps = await driver.execute_query(
                    "MATCH (ep:Episodic) WHERE ep.uuid IN $ids RETURN ep.source_description AS src", ids=e.episodes)
                src = eps[0][0]["src"] if eps and eps[0] else "?"
                print(f"   - {e.fact[:120]}\n       valid_at={e.valid_at} | provenance: {src[-90:]}")

        recs, _, _ = await driver.execute_query(
            "MATCH (n) WHERE n.group_id = $g RETURN labels(n) AS l, count(*) AS c ORDER BY c DESC", g=group)
        print("\ngraph contents:", [(r["l"], r["c"]) for r in recs])
    finally:
        await g.close()


asyncio.run(main())
