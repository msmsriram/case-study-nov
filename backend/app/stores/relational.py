"""Relational store: the system of record and audit trail.

What lives here (and why): everything that is tabular, filterable and must be exact -
cities, research runs, every discovered source with its crawl decision, every claim with
its verdict / rationale / geography level (including the ones we REJECTED, for audit),
conflicts and gaps. "Where did this come from?" is ultimately answered by a join here.

SQLAlchemy 2.0 so the same code runs on local SQLite (dev) and Postgres / Neon (prod).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from ..config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def city_id_for(city: str, country_code: str | None, country: str = "") -> str:
    return slugify(f"{city}-{country_code or country}")


def source_id_for(city_id: str, normalized_url: str) -> str:
    return hashlib.sha1(f"{city_id}|{normalized_url}".encode()).hexdigest()[:16]


class Base(DeclarativeBase):
    pass


class City(Base):
    __tablename__ = "cities"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(120))
    country_code: Mapped[str | None] = mapped_column(String(4))
    admin_region: Mapped[str | None] = mapped_column(String(160))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    population_hint: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_researched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    city_id: Mapped[str] = mapped_column(ForeignKey("cities.id"), index=True)
    city_input: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="running")       # running | complete | failed
    graph_status: Mapped[str] = mapped_column(String(30), default="pending")  # pending | building | ready | failed | skipped
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    errors: Mapped[list] = mapped_column(JSON, default=list)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    city_id: Mapped[str] = mapped_column(ForeignKey("cities.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)               # run that last saw it
    url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30))
    source_tier: Mapped[str] = mapped_column(String(30), index=True)
    published_date: Mapped[str | None] = mapped_column(String(40))
    queries: Mapped[list] = mapped_column(JSON, default=list)
    crawl_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    crawl_reason: Mapped[str] = mapped_column(Text, default="")
    robots_status: Mapped[str | None] = mapped_column(String(30))
    fetch_status: Mapped[str | None] = mapped_column(String(30))              # None = never fetched
    extraction_quality: Mapped[str | None] = mapped_column(String(10))
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    text_hash: Mapped[str | None] = mapped_column(String(20))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClaimRow(Base):
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    city_id: Mapped[str] = mapped_column(ForeignKey("cities.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), index=True)
    claim_type: Mapped[str] = mapped_column(String(30))
    statement: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    evidence_window: Mapped[str] = mapped_column(Text, default="")
    quote_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    entities: Mapped[list] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    extractor_geo_level: Mapped[str] = mapped_column(String(12))
    geo_level: Mapped[str] = mapped_column(String(12), index=True)            # final: checker's correction wins
    geo_mismatch: Mapped[bool] = mapped_column(Boolean, default=False)
    extractor_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extractor_model: Mapped[str | None] = mapped_column(String(60))
    # status: verified | unsupported | missing | rejected_grounding | unverified
    status: Mapped[str] = mapped_column(String(24), index=True)
    verdict: Mapped[str | None] = mapped_column(String(24))
    verdict_rationale: Mapped[str | None] = mapped_column(Text)
    checker_confidence: Mapped[float | None] = mapped_column(Float)
    checker_model: Mapped[str | None] = mapped_column(String(60))
    in_conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    graph_episode_uuid: Mapped[str | None] = mapped_column(String(40), index=True)  # set once ingested into Graphiti
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConflictRow(Base):
    __tablename__ = "conflicts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[str] = mapped_column(String(80), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    claim_a: Mapped[str] = mapped_column(String(16))
    claim_b: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text)


class GapRow(Base):
    __tablename__ = "gaps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city_id: Mapped[str] = mapped_column(String(80), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)


class Conversation(Base):
    """A chat thread about one city. Gives the Q&A layer its conversational memory."""
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    city_id: Mapped[str] = mapped_column(ForeignKey("cities.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))                 # user | assistant
    content: Mapped[str] = mapped_column(Text)                    # question, or the answer text
    payload: Mapped[dict] = mapped_column(JSON, default=dict)     # assistant: full answer (evidence, citations, timings)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------- engine
def _url() -> str:
    url = settings.database_url or f"sqlite:///{(settings.data_dir / 'city_intel.db').as_posix()}"
    if url.startswith("postgres://"):                   # Neon / Heroku style
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


# pool_recycle: serverless Postgres (Neon) drops idle connections; recycle before it does.
engine = create_engine(_url(), pool_pre_ping=True, pool_recycle=240, pool_size=5, max_overflow=5, future=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
Base.metadata.create_all(engine)


def backend_name() -> str:
    return engine.url.get_backend_name()


# --------------------------------------------------------------------------- write path
def persist_run(state: dict) -> dict:
    """Write one run's full audit trail. Idempotent: primary keys are deterministic."""
    plan = state["plan"]
    city_id = city_id_for(plan.city, plan.country_code, plan.country)
    run_id = state["run_id"]
    verdicts = {v.claim_id: v for v in state.get("verifications", [])}
    verified = set(state.get("verified_claim_ids", []))
    conflicts = state.get("conflicts", []) or []
    in_conflict = {cid for k in conflicts for cid in k.claim_ids}
    from ..llm import model_for

    with SessionLocal() as s:
        s.merge(City(id=city_id, name=plan.city, country=plan.country, country_code=plan.country_code,
                     admin_region=plan.admin_region, aliases=plan.aliases, population_hint=plan.population_hint,
                     last_researched_at=utcnow()))
        s.flush()

        final_to_source: dict[str, str] = {}
        for key, res in state.get("search_results", {}).items():
            d = state.get("crawl_decisions", {}).get(key)
            doc = state.get("documents", {}).get(key)
            sid = source_id_for(city_id, key)
            if doc is not None:
                final_to_source[doc.final_url] = sid
                final_to_source[doc.url] = sid
            s.merge(Source(
                id=sid, city_id=city_id, run_id=run_id, url=res.url, final_url=doc.final_url if doc else None,
                title=(doc.title if doc and doc.title else res.title), snippet=res.snippet,
                publisher=(doc.sitename or doc.hostname) if doc else None, provider=res.provider,
                source_tier=res.source_tier, published_date=(doc.published_date if doc else None) or res.published_date,
                queries=state.get("url_queries", {}).get(key, []),
                crawl_allowed=bool(d and d.allowed), crawl_reason=d.reason if d else "not checked",
                robots_status=d.robots_status if d else None,
                fetch_status=doc.fetch_status if doc else None,
                extraction_quality=doc.extraction_quality if doc else None,
                word_count=doc.word_count if doc else 0, text_hash=doc.text_hash if doc else None,
                fetched_at=doc.fetched_at if doc else None))
        s.flush()

        def row(c, status: str) -> ClaimRow:
            v = verdicts.get(c.id)
            final_geo = v.corrected_geo_level if v and v.geo_mismatch and v.corrected_geo_level != "unknown" else c.geo_level
            return ClaimRow(
                id=c.id, city_id=city_id, run_id=run_id, source_id=final_to_source.get(c.source_url),
                source_url=c.source_url, category=c.category, claim_type=c.claim_type, statement=c.statement,
                quote=c.quote, evidence_window=c.evidence_window, quote_verified=c.quote_verified,
                entities=c.entities, year=c.year, extractor_geo_level=c.geo_level, geo_level=final_geo,
                geo_mismatch=bool(v and v.geo_mismatch), extractor_confidence=c.confidence,
                extractor_model=model_for("extractor"), status=status,
                verdict=v.verdict if v else None, verdict_rationale=v.rationale if v else None,
                checker_confidence=v.checker_confidence if v else None, checker_model=v.checker_model if v else None,
                in_conflict=c.id in in_conflict)

        existing_episode = dict(s.execute(select(ClaimRow.id, ClaimRow.graph_episode_uuid)
                                          .where(ClaimRow.city_id == city_id)).all())
        n = {"verified": 0, "not_verified": 0, "rejected_grounding": 0}
        for c in state.get("claims", []):
            v = verdicts.get(c.id)
            status = "verified" if c.id in verified else (v.verdict.lower() if v else "unverified")
            r = row(c, status)
            r.graph_episode_uuid = existing_episode.get(c.id)     # keep institutional memory across re-runs
            s.merge(r)
            n["verified" if status == "verified" else "not_verified"] += 1
        for c in state.get("rejected_claims", []):
            s.merge(row(c, "rejected_grounding"))
            n["rejected_grounding"] += 1

        s.query(ConflictRow).filter_by(run_id=run_id).delete()
        s.query(GapRow).filter_by(run_id=run_id).delete()
        for k in conflicts:
            s.add(ConflictRow(city_id=city_id, run_id=run_id, claim_a=k.claim_ids[0], claim_b=k.claim_ids[1],
                              description=k.description))
        for g in state.get("gaps", []) or []:
            s.add(GapRow(city_id=city_id, run_id=run_id, category=g.category, severity=g.severity,
                         description=g.description, suggestion=g.suggestion))

        s.merge(Run(id=run_id, city_id=city_id, city_input=state.get("city_input", plan.city), status="complete",
                    graph_status="pending", finished_at=utcnow(), plan=plan.model_dump(mode="json"),
                    stats=state.get("stats", {}), errors=state.get("errors", [])))
        s.commit()
    return {"city_id": city_id, "sources": len(state.get("search_results", {})), **n,
            "conflicts": len(conflicts), "gaps": len(state.get("gaps", []) or []), "backend": backend_name()}


def set_graph_status(run_id: str, status: str) -> None:
    with SessionLocal() as s:
        r = s.get(Run, run_id)
        if r:
            r.graph_status = status
            s.commit()


def mark_ingested(claim_ids: list[str], episode_uuid: str) -> None:
    with SessionLocal() as s:
        for cid in claim_ids:
            r = s.get(ClaimRow, cid)
            if r:
                r.graph_episode_uuid = episode_uuid
        s.commit()


def update_run_stats(run_id: str, extra: dict) -> None:
    """Merge late-arriving stats (e.g. the graph build, which finishes after the run record is written)."""
    with SessionLocal() as s:
        r = s.get(Run, run_id)
        if r:
            r.stats = {**(r.stats or {}), **extra}
            s.commit()


def delete_city(city_id: str) -> dict:
    """Remove a city and everything derived from it. Children first: Postgres enforces the foreign keys."""
    from sqlalchemy import delete
    with SessionLocal() as s:
        if not s.get(City, city_id):
            return {}
        conv_ids = [r[0] for r in s.execute(select(Conversation.id).where(Conversation.city_id == city_id)).all()]
        n: dict[str, int] = {}
        n["messages"] = s.execute(delete(Message).where(Message.conversation_id.in_(conv_ids))).rowcount if conv_ids else 0
        n["conversations"] = s.execute(delete(Conversation).where(Conversation.city_id == city_id)).rowcount
        n["claims"] = s.execute(delete(ClaimRow).where(ClaimRow.city_id == city_id)).rowcount
        n["conflicts"] = s.execute(delete(ConflictRow).where(ConflictRow.city_id == city_id)).rowcount
        n["gaps"] = s.execute(delete(GapRow).where(GapRow.city_id == city_id)).rowcount
        n["sources"] = s.execute(delete(Source).where(Source.city_id == city_id)).rowcount
        n["runs"] = s.execute(delete(Run).where(Run.city_id == city_id)).rowcount
        n["cities"] = s.execute(delete(City).where(City.id == city_id)).rowcount
        s.commit()
        return n
