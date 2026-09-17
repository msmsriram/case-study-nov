"""Ask questions against a researched city.   python scripts/test_qa.py nairobi-ke ["question" ...]"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.qa import answer  # noqa: E402

city_id = sys.argv[1] if len(sys.argv) > 1 else "nairobi-ke"
questions = sys.argv[2:] or [
    "Which organisations are involved in hypertension prevention or NCD programmes, and how are they connected?",
    "What is the prevalence of hypertension in Nairobi?",
    "What is the mayor's personal opinion of CARDIO4Cities?",
]
for q in questions:
    r = answer(city_id, q)
    print("\n" + "=" * 100 + f"\nQ: {q}\n" + "=" * 100)
    if "error" in r:
        print(r)
        continue
    print(r["answer"])
    print(f"\nconfidence={r['confidence']} insufficient={r['insufficient_evidence']} | retrieved {r['retrieval']} | "
          f"stores used in answer: {r['stores_used_in_answer']} | timings {r['timings']}")
    for c in r["caveats"]:
        print(f"  caveat: {c}")
    if r.get("invalid_citations_removed"):
        print(f"  removed invalid citations: {r['invalid_citations_removed']}")
    print("  cited evidence:")
    for e in r["evidence"]:
        if e["n"] in r.get("cited", []):
            print(f"    [E{e['n']}] {e['kind']:<10} geo={e['geo_level']} verdict={e['verdict']} | {e['text'][:90]}")
            print(f"          {', '.join(e['source_urls'])[:110]}")
    if r["gaps"]:
        print("  recorded gaps:", [g["description"][:70] for g in r["gaps"][:3]])
