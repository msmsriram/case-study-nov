"""Run the LangGraph workflow for one city and stream progress.

    python scripts/test_graph.py "Nairobi, Kenya" [--max-docs 8]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console

from app.config import settings

ap = argparse.ArgumentParser()
ap.add_argument("city", nargs="?", default="Nairobi, Kenya")
ap.add_argument("--max-docs", type=int, default=None)
args = ap.parse_args()
if args.max_docs:
    settings.max_documents_per_run = args.max_docs

from app.graph.builder import build_graph, mermaid  # noqa: E402
from app.llm import usage_snapshot  # noqa: E402
from app.research.models import ResearchItem  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("app.llm").setLevel(logging.INFO)
console = Console(width=150, legacy_windows=False)

city = args.city
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
        if node in ("plan", "search", "crawl_check", "fetch", "collect_claims", "detect_conflicts", "gap_analysis"):
            console.print(f"[dim]{time.time()-t0:6.1f}s[/] [green]node done:[/] {node}")

final = graph.get_state(config).values
plan = final["plan"]
console.rule("[bold]Plan")
console.print(f"{plan.city} | {plan.country} | region={plan.admin_region} | aliases={plan.aliases} | pop={plan.population_hint}")
for a in plan.assumptions:
    console.print(f"  [yellow]assumption:[/] {a}")
for cat in plan.categories:
    console.print(f"[bold]{cat.key}[/]: " + " | ".join(cat.queries))

console.rule("[bold]Stats")
console.print(final.get("stats"))
console.print("LLM usage:", usage_snapshot())
if final.get("errors"):
    console.print("[red]errors:[/]", final["errors"])

console.rule("[bold]Claims by category (verdict, geo, source)")
verdicts = {v.claim_id: v for v in final.get("verifications", [])}
verified = set(final.get("verified_claim_ids", []))
colour = {"SUPPORTED": "green", "PARTIALLY_SUPPORTED": "yellow", "UNSUPPORTED": "red", "MISSING": "magenta"}
for cat in plan.categories:
    cc = [c for c in final.get("claims", []) if c.category == cat.key]
    if not cc:
        continue
    console.print(f"\n[bold underline]{cat.name}[/] ({len(cc)} claims)")
    for c in cc:
        v = verdicts.get(c.id)
        tag = f"[{colour.get(v.verdict,'white')}]{v.verdict}[/]" if v else "[dim]unverified[/]"
        geo = f"[red]geo→{v.corrected_geo_level}[/]" if v and v.geo_mismatch else c.geo_level
        console.print(f"  {tag:<28} [{geo}] {c.statement}")
        console.print(f"      [dim]“{c.quote[:110]}” — {c.source_title[:50] or c.source_url[:50]} ({c.source_tier}, {c.year or c.published_date or 'n.d.'})[/]")
        if v and v.verdict not in ("SUPPORTED",):
            console.print(f"      [dim italic]checker: {v.rationale[:160]}[/]")

if final.get("rejected_claims"):
    console.rule(f"[bold]{len(final['rejected_claims'])} claims rejected by quote-grounding check")
    for c in final["rejected_claims"][:6]:
        console.print(f"  [red]x[/] {c.statement[:100]}  [dim]quote: “{c.quote[:80]}”[/]")

if final.get("conflicts"):
    console.rule("[bold]Conflicts")
    for k in final["conflicts"]:
        console.print(f"  [red]![/] {k.claim_ids}: {k.description}")

console.rule("[bold]Gaps")
for g in final.get("gaps", []):
    console.print(f"  [{ {'high':'red','medium':'yellow','low':'dim'}[g.severity] }]{g.severity:<6}[/] {g.category:<14} {g.description}")
    if g.suggestion:
        console.print(f"         [dim]→ {g.suggestion[:150]}[/]")

out = Path(__file__).resolve().parent.parent / "samples" / f"graph_{city.split(',')[0].strip().lower().replace(' ', '_')}.json"


def _ser(v):
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    if isinstance(v, dict):
        return {k: _ser(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_ser(x) for x in v]
    return v


out.write_text(json.dumps({k: _ser(v) for k, v in final.items()}, indent=2, default=str), encoding="utf-8")
console.print(f"\n[dim]state written to {out}; total {time.time()-t0:.0f}s[/]")
