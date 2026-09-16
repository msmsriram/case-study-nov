"""Run the LangGraph workflow for one city and stream progress.

    python scripts/test_graph.py "Nairobi, Kenya"
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console

from app.graph.builder import build_graph, mermaid
from app.llm import usage_snapshot

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("app.llm").setLevel(logging.INFO)
console = Console(width=150, legacy_windows=False)

city = sys.argv[1] if len(sys.argv) > 1 else "Nairobi, Kenya"
run_id = uuid.uuid4().hex[:8]
graph = build_graph()
config = {"configurable": {"thread_id": run_id}}

docs_dir = Path(__file__).resolve().parent.parent / "docs"
docs_dir.mkdir(exist_ok=True)
(docs_dir / "workflow.mmd").write_text(mermaid(), encoding="utf-8")

console.rule(f"[bold]Run {run_id}: {city}")
t0 = time.time()
for mode, chunk in graph.stream({"run_id": run_id, "city_input": city, "stage": "planning"},
                                config, stream_mode=["custom", "updates"]):
    if mode == "custom":
        console.print(f"[dim]{time.time()-t0:6.1f}s[/] [cyan]{chunk.get('stage','?'):<17}[/] {chunk.get('message','')}")
        if "denied_reasons" in chunk:
            for r, n in chunk["denied_reasons"].items():
                console.print(f"        [red]{n:>2}[/] {r}")
    else:
        node = next(iter(chunk))
        console.print(f"[dim]{time.time()-t0:6.1f}s[/] [green]node done:[/] {node}")

final = graph.get_state(config).values
plan = final["plan"]
console.rule("[bold]Plan")
console.print(f"{plan.city} | {plan.country} | region={plan.admin_region} | aliases={plan.aliases} | pop={plan.population_hint}")
for a in plan.assumptions:
    console.print(f"  [yellow]assumption:[/] {a}")
for cat in plan.categories:
    console.print(f"\n[bold]{cat.key}[/] - {cat.name}: [dim]{cat.why}[/]")
    for q in cat.queries:
        console.print(f"    - {q}")

console.rule("[bold]Stats")
console.print(final.get("stats"))
console.print("LLM usage:", usage_snapshot())

console.rule("[bold]Top usable documents by tier")
from app.research.models import ResearchItem
items = [ResearchItem(result=final["search_results"][k], decision=final["crawl_decisions"].get(k), document=d)
         for k, d in final["documents"].items()]
usable = [i for i in items if i.usable]
for it in sorted(usable, key=lambda i: i.result.source_tier)[:15]:
    d = it.document
    console.print(f"  [{it.result.source_tier:<17}] {d.word_count:>5}w q={d.extraction_quality:<6} {str(d.published_date or '')[:10]:<10} {(d.title or it.result.title)[:70]}")

out = Path(__file__).resolve().parent.parent / "samples" / f"graph_{city.split(',')[0].strip().lower().replace(' ', '_')}.json"
serialisable = {k: (v.model_dump(mode="json") if hasattr(v, "model_dump") else
                    ({kk: vv.model_dump(mode="json") for kk, vv in v.items()} if isinstance(v, dict) and v and hasattr(next(iter(v.values())), "model_dump") else v))
                for k, v in final.items()}
out.write_text(json.dumps(serialisable, indent=2, default=str), encoding="utf-8")
console.print(f"\n[dim]state written to {out}; workflow diagram at docs/workflow.mmd; total {time.time()-t0:.0f}s[/]")
