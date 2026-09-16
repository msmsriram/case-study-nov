"""Assembles the city-research workflow.

    START -> plan -> search -> crawl_check -> fetch -> (extract_claims -> fact_check -> gaps -> store -> report) -> END

Nodes are synchronous (thread-pooled I/O with HTTP-level timeouts); LangGraph per-node
timeouts are async-only, so retry policies guard the idempotent steps instead. A flaky
search API or a slow host cannot hang a run. Progress is streamed via custom events.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from .nodes.planner import plan_node
from .nodes.research import crawl_check_node, fetch_node, search_node
from .state import ResearchState

# Pydantic objects we keep in state must be whitelisted for checkpoint (de)serialisation.
ALLOWED_STATE_TYPES = [
    ("app.graph.state", "ResearchPlan"), ("app.graph.state", "ResearchCategory"),
    ("app.graph.state", "Claim"), ("app.graph.state", "Verification"), ("app.graph.state", "Gap"),
    ("app.research.models", "SearchResult"), ("app.research.models", "CrawlDecision"),
    ("app.research.models", "FetchedDocument"),
]


def state_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_STATE_TYPES)


def _route_after_search(state: ResearchState) -> str:
    return "crawl_check" if state.get("search_results") else END


def build_graph(checkpointer=None):
    g = StateGraph(ResearchState)
    g.add_node("plan", plan_node, retry_policy=RetryPolicy(max_attempts=3, initial_interval=2))
    g.add_node("search", search_node, retry_policy=RetryPolicy(max_attempts=2, initial_interval=3))
    g.add_node("crawl_check", crawl_check_node)
    g.add_node("fetch", fetch_node)

    g.add_edge(START, "plan")
    g.add_edge("plan", "search")
    g.add_conditional_edges("search", _route_after_search, ["crawl_check", END])
    g.add_edge("crawl_check", "fetch")
    g.add_edge("fetch", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver(serde=state_serde()))


def mermaid() -> str:
    return build_graph().get_graph().draw_mermaid()
