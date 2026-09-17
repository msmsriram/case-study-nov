"""Assembles the city-research workflow.

    START -> plan -> search -> crawl_check -> fetch
          -> [Send per document] extract_claims -> collect_claims
          -> [Send per batch]    fact_check     -> detect_conflicts -> gap_analysis
          -> persist (relational + vector) -> build_graph (Graphiti / Neo4j) -> END

Nodes are synchronous (thread-pooled I/O with HTTP-level timeouts); LangGraph per-node
timeouts are async-only, so retry policies guard the idempotent steps instead. Fan-out
nodes catch their own exceptions and report them in `errors`, so one bad document cannot
fail a run. Progress is streamed via custom events.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from .nodes.extraction import collect_claims_node, extract_claims_node, fan_out_extraction
from .nodes.gaps import gap_analysis_node
from .nodes.persist import build_graph_node, persist_node
from .nodes.planner import plan_node
from .nodes.research import crawl_check_node, fetch_node, search_node
from .nodes.verification import detect_conflicts_node, fact_check_node, fan_out_verification
from .state import ResearchState

# Pydantic objects we keep in state must be whitelisted for checkpoint (de)serialisation.
ALLOWED_STATE_TYPES = [
    ("app.graph.state", "ResearchPlan"), ("app.graph.state", "ResearchCategory"),
    ("app.graph.state", "Claim"), ("app.graph.state", "Verification"), ("app.graph.state", "Gap"),
    ("app.graph.state", "Conflict"),
    ("app.research.models", "SearchResult"), ("app.research.models", "CrawlDecision"),
    ("app.research.models", "FetchedDocument"),
]


def state_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_STATE_TYPES)


def _route_after_search(state: ResearchState) -> str:
    return "crawl_check" if state.get("search_results") else END


def _route_after_fetch(state: ResearchState):
    sends = fan_out_extraction(state)
    return sends if sends else "gap_analysis"


def _route_after_collect(state: ResearchState):
    sends = fan_out_verification(state)
    return sends if sends else "gap_analysis"


def build_graph(checkpointer=None):
    g = StateGraph(ResearchState)
    g.add_node("plan", plan_node, retry_policy=RetryPolicy(max_attempts=3, initial_interval=2))
    g.add_node("search", search_node, retry_policy=RetryPolicy(max_attempts=2, initial_interval=3))
    g.add_node("crawl_check", crawl_check_node)
    g.add_node("fetch", fetch_node)
    g.add_node("extract_claims", extract_claims_node)
    g.add_node("collect_claims", collect_claims_node)
    g.add_node("fact_check", fact_check_node)
    g.add_node("detect_conflicts", detect_conflicts_node)
    g.add_node("gap_analysis", gap_analysis_node)
    g.add_node("persist", persist_node)
    g.add_node("build_graph", build_graph_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "search")
    g.add_conditional_edges("search", _route_after_search, ["crawl_check", END])
    g.add_edge("crawl_check", "fetch")
    g.add_conditional_edges("fetch", _route_after_fetch, ["extract_claims", "gap_analysis"])
    g.add_edge("extract_claims", "collect_claims")
    g.add_conditional_edges("collect_claims", _route_after_collect, ["fact_check", "gap_analysis"])
    g.add_edge("fact_check", "detect_conflicts")
    g.add_edge("detect_conflicts", "gap_analysis")
    g.add_edge("gap_analysis", "persist")
    g.add_edge("persist", "build_graph")
    g.add_edge("build_graph", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver(serde=state_serde()))


def mermaid() -> str:
    return build_graph().get_graph().draw_mermaid()
