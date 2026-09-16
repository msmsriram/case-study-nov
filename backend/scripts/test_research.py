"""Exercise the research layer the way the Research Planner agent will:
several category questions for one city, then inspect what comes back.

    python scripts/test_research.py "Hyderabad, India"
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.table import Table

from app.research import research_queries

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
console = Console(width=170, legacy_windows=False)

city = sys.argv[1] if len(sys.argv) > 1 else "Hyderabad, India"
QUERIES = [
    f"{city} hypertension prevalence statistics",
    f"{city} cardiovascular disease mortality data",
    f"{city} municipal health department non-communicable disease programme",
    f"{city} diabetes screening programme government",
    f"{city} public health policy cardiovascular",
    f"{city} cardiology hospitals tertiary care",
]

t0 = time.time()
batch = research_queries(QUERIES, max_results_per_query=6)
elapsed = time.time() - t0

console.rule(f"[bold]Research batch for {city}  ({elapsed:.1f}s)")
console.print(batch.stats)

tbl = Table(title="Discovered sources", show_lines=False)
tbl.add_column("#", width=3)
tbl.add_column("host", width=28)
tbl.add_column("crawl", width=8)
tbl.add_column("reason / fetch", width=42)
tbl.add_column("tier", width=11)
tbl.add_column("q", width=4)
tbl.add_column("words", width=6, justify="right")
tbl.add_column("date", width=11)
tbl.add_column("title", width=45)
for i, it in enumerate(batch.items, 1):
    d, doc = it.decision, it.document
    crawl = "[green]allow" if d and d.allowed else "[red]deny"
    if doc:
        reason = f"{doc.fetch_status}" + (f": {doc.fetch_note}" if doc.fetch_note else "")
        kind, words, date = doc.extraction_quality[:4], str(doc.word_count), (doc.published_date or "")[:10]
        title = doc.title or it.result.title
    else:
        reason = d.reason if d else ""
        kind, words, date, title = "", "", "", it.result.title
    host = it.result.normalized_url.split("/")[2][:28]
    tbl.add_row(str(i), host, crawl, reason[:42], it.result.source_tier, kind, words, date, title[:45])
console.print(tbl)

out = Path(__file__).resolve().parent.parent / "samples" / f"research_{city.split(',')[0].strip().lower().replace(' ', '_')}.json"
out.write_text(json.dumps([it.model_dump(mode="json") for it in batch.items], indent=2), encoding="utf-8")

usable = [it for it in batch.items if it.usable]
console.rule(f"[bold]{len(usable)} usable documents - sample text")
for it in usable[:3]:
    doc = it.document
    console.print(f"\n[bold cyan]{doc.title}[/]  ({doc.sitename or doc.hostname}, {doc.published_date}, {doc.word_count} words)")
    console.print(f"[dim]{doc.final_url}[/]")
    console.print(doc.text[:900].replace("\n", " ") + " ...")

console.print(f"\n[dim]full output written to {out}[/]")
