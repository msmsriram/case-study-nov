"""City research report (Markdown, optionally HTML) generated from the relational store.

Deterministic by design: the body is assembled from verified claims, never free-written by a model,
so the report cannot contain a statement that lacks a source. The only model-written part is the
short executive summary, which may cite only reference numbers that exist and is dropped if it
cites anything else.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select

from .stores import relational as rel

GEO_ORDER = ["city", "metro", "district", "state", "national", "global", "unknown"]
GEO_LABEL = {"city": "City-level", "metro": "Metro-level", "district": "County / district-level", "state": "Regional-level (state / province / region, not city-specific)",
             "national": "National-level (not city-specific)", "global": "Global / regional (not city-specific)",
             "unknown": "Geography unclear"}
TIER_LABEL = {"government": "Government", "intergovernmental": "Intergovernmental", "academic": "Academic", "ngo": "NGO",
              "reference": "Reference", "news": "News", "commercial": "Commercial", "other": "Other"}


_JUNK_TITLE = re.compile(r"^(link_icon\b.*|links?|home|index|untitled|pdf|document|page|download|welcome)$", re.I)


def clean_title(title: str | None, url: str) -> str:
    """Pages often expose a useless <title> ("link_icon Links", "Home"); fall back to something readable."""
    from urllib.parse import unquote, urlsplit
    t = (title or "").replace(" ", " ").replace("Â ", " ").replace("Â", "")
    t = re.sub(r"\s+", " ", t).strip(" -|·")
    if len(t) >= 4 and not _JUNK_TITLE.match(t):
        return t
    parts = urlsplit(url)
    tail = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    tail = re.sub(r"\.(pdf|html?|aspx|php)$", "", tail, flags=re.I)
    tail = re.sub(r"[-_+]+", " ", tail).strip()
    host = parts.netloc.removeprefix("www.")
    return f"{tail[:80]} ({host})" if len(tail) >= 6 else host


def fmt_date(value: str | None) -> str | None:
    """Dates arrive as ISO strings from page metadata or as free text from the search engine ("19 Apr 2023").
    Never slice them: a truncated year is worse than no date."""
    if not value:
        return None
    v = value.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%d %b %Y")
        except ValueError:
            return None
    if re.search(r"\b(19|20)\d{2}\b", v) and len(v) <= 40:      # free text that at least carries a full year
        return v
    return None


class ExecSummary(BaseModel):
    bullets: list[str] = Field(description="4-6 bullets. Each ends with reference numbers like [3] or [2][7]. Flag national-only data as national.")


SUMMARY_SYSTEM = """Write the executive summary of a city cardiovascular-health briefing for a City Lead.
Use ONLY the numbered findings provided. Each bullet must end with the reference number(s) it rests on, e.g. [4] or [2][9].
Never present national or global data as city data: say "nationally" when the finding is national-level.
Lead with what is known about the city itself, then the national context, then the biggest gaps. No invented numbers."""


def build_report(city_id: str, with_summary: bool = True) -> dict:
    with rel.SessionLocal() as s:
        city = s.get(rel.City, city_id)
        if not city:
            raise ValueError(f"unknown city {city_id}")
        run = s.execute(select(rel.Run).where(rel.Run.city_id == city_id).order_by(rel.Run.started_at.desc())).scalars().first()
        claims = s.execute(select(rel.ClaimRow).where(rel.ClaimRow.city_id == city_id)).scalars().all()
        sources = {x.id: x for x in s.execute(select(rel.Source).where(rel.Source.city_id == city_id)).scalars()}
        gaps = s.execute(select(rel.GapRow).where(rel.GapRow.run_id == run.id)).scalars().all() if run else []
        conflicts = s.execute(select(rel.ConflictRow).where(rel.ConflictRow.run_id == run.id)).scalars().all() if run else []

    verified = [c for c in claims if c.status == "verified"]
    not_verified = [c for c in claims if c.status != "verified"]
    plan = (run.plan if run else {}) or {}
    categories = [(c["key"], c["name"], c.get("why", "")) for c in plan.get("categories", [])] or \
                 sorted({(c.category, c.category.replace("_", " ").title(), "") for c in verified})

    # reference numbers: one per source URL, in order of first use
    ref_no: dict[str, int] = {}

    def ref(c) -> int:
        if c.source_url not in ref_no:
            ref_no[c.source_url] = len(ref_no) + 1
        return ref_no[c.source_url]

    by_cat: dict[str, list] = defaultdict(list)
    for c in verified:
        by_cat[c.category].append(c)
    for cs in by_cat.values():
        cs.sort(key=lambda c: (GEO_ORDER.index(c.geo_level) if c.geo_level in GEO_ORDER else 9, -(c.year or 0)))

    body: list[str] = []
    for key, name, why in categories:
        cs = by_cat.get(key, [])
        body.append(f"\n## {name}\n")
        if why:
            body.append(f"*{why}*\n")
        if not cs:
            body.append("> **No verified evidence found.** See *Knowledge gaps* below.\n")
            continue
        current = None
        for c in cs:
            if c.geo_level != current:
                current = c.geo_level
                body.append(f"\n**{GEO_LABEL.get(current, current)}**\n")
            flags = []
            if c.verdict == "PARTIALLY_SUPPORTED":
                flags.append("partially supported")
            if c.geo_mismatch:
                flags.append("geography corrected by fact checker")
            if c.in_conflict:
                flags.append("conflicts with another source")
            if c.year:
                flags.append(str(c.year))
            tail = f" *({'; '.join(flags)})*" if flags else ""
            body.append(f"- {c.statement} [{ref(c)}]{tail}")

    # executive summary (model-written, citation-checked)
    summary_md = ""
    if with_summary and verified:
        try:
            from .llm import structured_call
            top = sorted(verified, key=lambda c: (GEO_ORDER.index(c.geo_level) if c.geo_level in GEO_ORDER else 9,
                                                  c.verdict != "SUPPORTED"))[:40]
            listing = "\n".join(f"[{ref(c)}] ({c.geo_level}; {c.category}; {c.verdict}) {c.statement}" for c in top)
            gap_txt = "\n".join(f"- {g.description}" for g in gaps[:8])
            res, _ = structured_call("answer", ExecSummary, SUMMARY_SYSTEM,
                                     f"City: {city.name}, {city.country}\n\nFINDINGS\n{listing}\n\nRECORDED GAPS\n{gap_txt}",
                                     max_tokens=900, reasoning_effort="low")
            valid = set(ref_no.values())
            bullets = [re.sub(r"\[(\d+(?:\s*,\s*\d+)+)\]", lambda m: "".join(f"[{n.strip()}]" for n in m.group(1).split(",")),
                              b.replace("【", "[").replace("】", "]")) for b in res.bullets]
            ok = [b for b in bullets if (nums := {int(n) for n in re.findall(r"\[(\d+)\]", b)}) and nums <= valid]
            if ok:
                summary_md = "\n".join(f"- {b}" for b in ok)
        except Exception:  # noqa: BLE001 - the report must still be produced
            summary_md = ""

    now = datetime.now(timezone.utc)
    n_src = len(sources)
    n_read = sum(1 for x in sources.values() if x.fetch_status == "ok")
    denied = [x for x in sources.values() if not x.crawl_allowed]
    out: list[str] = [
        f"# {city.name}, {city.country}: Cardiovascular Health Intelligence Briefing",
        f"*Prepared for CARDIO4Cities City Leads. Generated {now:%d %b %Y %H:%M} UTC from live web research"
        + (f" (run `{run.id}`, {run.finished_at:%d %b %Y})" if run and run.finished_at else "") + ".*\n",
        "| | |\n|---|---|",
        f"| Administrative region | {city.admin_region or 'not established'} |",
        f"| Sources discovered / read | {n_src} / {n_read} |",
        f"| Claims extracted / verified | {len(claims)} / {len(verified)} |",
        f"| Claims rejected by the fact checker or grounding check | {len(not_verified)} |",
        f"| Conflicts / knowledge gaps | {len(conflicts)} / {len(gaps)} |\n",
        "> **How to read this briefing.** Every statement carries a reference number that resolves to its source at the end. "
        "Statements are grouped by the geography the evidence actually refers to: national figures are never presented as city figures. "
        "Only statements an independent fact-checking model confirmed against the source text are included.\n",
    ]
    if summary_md:
        out += ["## Executive summary\n", summary_md, ""]
    out += body

    out.append("\n## Conflicting evidence\n")
    if conflicts:
        idx = {c.id: c for c in claims}
        for k in conflicts:
            a, b = idx.get(k.claim_a), idx.get(k.claim_b)
            out.append(f"- {k.description}"
                       + (f"\n  - A: {a.statement} [{ref(a)}]" if a else "") + (f"\n  - B: {b.statement} [{ref(b)}]" if b else ""))
    else:
        out.append("No conflicting statistics were detected between sources in this run.")

    out.append("\n## Knowledge gaps\n")
    sev = {"high": 0, "medium": 1, "low": 2}
    for g in sorted(gaps, key=lambda g: sev.get(g.severity, 3)):
        out.append(f"- **{g.severity.upper()}** ({g.category}): {g.description}" + (f" *Suggested next step: {g.suggestion}*" if g.suggestion else ""))
    if not gaps:
        out.append("No gaps recorded.")

    out.append("\n## Sources found but not read\n")
    out.append("These sources were discovered but our crawler was not permitted to extract them (robots.txt, access refused, "
               "unreachable, or platform terms). They may be worth opening manually.\n")
    off = [x for x in denied if x.source_tier in ("government", "intergovernmental", "academic")]
    for x in off[:15]:
        out.append(f"- [{clean_title(x.title, x.url)[:90]}]({x.url}) - {TIER_LABEL.get(x.source_tier, x.source_tier)}; {x.crawl_reason}")
    if len(denied) > len(off[:15]):
        out.append(f"- ...and {len(denied) - len(off[:15])} more (see the Sources view).")

    out.append("\n## Claims that were not accepted\n")
    out.append(f"{len(not_verified)} extracted statements were excluded from this briefing: "
               + ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(_count(not_verified).items())) + ". "
               "They remain in the audit trail with the fact checker's reasoning.")

    out.append("\n## References\n")
    url_to_source = {(x.final_url or x.url): x for x in sources.values()}
    for url, n in sorted(ref_no.items(), key=lambda kv: kv[1]):
        x = url_to_source.get(url)
        meta = []
        if x:
            meta = [TIER_LABEL.get(x.source_tier, x.source_tier), x.publisher or "", f"published {fmt_date(x.published_date)}" if fmt_date(x.published_date) else "publication date unknown",
                    f"retrieved {x.fetched_at:%d %b %Y}" if x.fetched_at else ""]
        title = clean_title(x.title if x else "", url)[:120]
        out.append(f"{n}. [{title}]({url}) - " + "; ".join(m for m in meta if m))

    md = "\n".join(out) + "\n"
    return {"city_id": city_id, "markdown": md, "references": len(ref_no), "verified_claims": len(verified),
            "has_summary": bool(summary_md), "filename": f"{city_id}-cvd-briefing-{now:%Y%m%d}.md"}


def _count(rows) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        out[r.status] += 1
    return dict(out)


def to_html(markdown_text: str, title: str) -> str:
    import markdown as md
    body = md.markdown(markdown_text, extensions=["tables", "sane_lists"])
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>"
            "body{font:16px/1.55 Georgia,serif;max-width:860px;margin:40px auto;padding:0 20px;color:#1b1f23}"
            "h1{font-size:28px}h2{margin-top:36px;border-bottom:1px solid #d0d7de;padding-bottom:6px}"
            "table{border-collapse:collapse}td,th{border:1px solid #d0d7de;padding:6px 10px}"
            "blockquote{border-left:4px solid #0969da;margin:16px 0;padding:6px 16px;background:#f6f8fa}"
            "a{color:#0969da}li{margin:4px 0}@media print{a{color:#000}}</style></head><body>" + body + "</body></html>")
